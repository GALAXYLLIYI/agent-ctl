# -*- coding: utf-8 -*-
r"""窗G2 · OCR 通道（R3）：**Windows.Media.Ocr**，纯读，不碰输入、不留文件。

为什么是它：本机 **tesseract 不在**，`winrt` 与 PIL 可用 ⇒ 走系统自带引擎
（框架 §㉓ 下一步排序第 2 条）。前人已实测（`docs\证据\WindowsOCR实测_20261002.txt`）：
普通进程（无 MSIX package identity）就能调 `Windows.Media.Ocr`，本机可用语言只有 `zh-Hans-CN`，
`MaxImageDimension = 10000`，且**原尺寸会漏行、4 倍放大才认全** —— 所以下面默认放大 4 倍。

四条硬约束（派单 §一.1）：
  ① 只截**客户区**，截图只在**内存里**走一圈（PIL → PNG bytes → WinRT 内存流），**不落任何文件**；
  ② 坐标**一律客户区相对、客户区物理像素**（M15 A 批口径统一）：
     `coord_space = "client_physical"`；`dpi_scale = dpi/96` 仍写进返回（**不写死 1.0**），
     但 words 的坐标**不再**乘它（截图本身已是物理像素）⇒ 数值与改前**等价**；
  ③ 超时即返回 `{"ok": false, "原因": "超时"}` —— 干活过程丢进**守护线程**，调用方最多等 `max_ms`，
     **不许挂着**；
  ④ `words` 上限 80，`omitted.total` 报**真实总数**（裁掉多少说多少）。
"""

from __future__ import annotations

import ctypes
import io
import threading
import time

OCR_ENGINE_NAME = "Windows.Media.Ocr"
OCR_DEFAULT_LANG = "zh-Hans-CN"
OCR_DEFAULT_MAX_MS = 1500
OCR_MAX_WORDS = 80            # 派单：words <= 80
OCR_UPSCALE = 4               # 实测：原尺寸会漏行，4 倍才认全
OCR_MIN_BOX_PX = 4            # bbox 宽高下限（派单：>4px 才算可当锚）
OCR_MAX_IMAGE_DIM = 10000     # OcrEngine.MaxImageDimension（本机实测值）

# §⑫ 的 ocr 档：existence 单独压低是**刻意的** —— 像素只证明「那儿有块墨」，
# 证明不了那是个能点的控件；bbox 也不高（OCR 框会偏），但与 text **分开记**。
OCR_CONF = {"bbox": 0.55, "text": 0.55, "existence": 0.40}

# 引擎探测结果缓存：起一次引擎不便宜，而且**探测本身不该有副作用**。
_ENGINE = {"查过": False, "ok": False, "lang": "", "langs": [],
           "原因": "", "上限": OCR_MAX_IMAGE_DIM}


def _fail(reason: str, ms: int = 0, **extra) -> dict:
    out = {"ok": False, "engine": OCR_ENGINE_NAME, "原因": str(reason),
           "ms": int(ms), "text": "", "words": [],
           "omitted": {"total": 0, "returned": 0, "cap": OCR_MAX_WORDS},
           "dpi_scale": 1.0,
           # ★ 口径声明：**恒定**（成功/失败都带）⇒ 调用方不用猜。
           "coord_space": "client_physical"}
    out.update(extra)
    return out


# ── 窗口几何与 DPI ───────────────────────────────────────────────────────
#: 进程 DPI 感知是否已经问过（避免反复调 Win32）。
_dpi_ready = False


def _dpi_awareness_level() -> int:
    """进程 DPI 感知级别：2=PER_MONITOR_AWARE / 1=SYSTEM_DPI_AWARE / 0=UNAWARE。

    ★ **这是 A 批口径统一的前提**（2026-10-22 实测，同一个记事本窗口、同一时刻）：
        · PER_MONITOR_AWARE(2) ⇒ `GetClientRect`/`ClientToScreen` 给**物理像素**（2543×1520）
        · DPI_UNAWARE(0)      ⇒ 给**逻辑（虚拟化）像素**（1695×1013）
      「拿到的到底是不是物理像素」**必须当场问进程**，不能靠猜 ——
      改前 `client_rect_screen` 无条件乘 `dpi_scale`，在**感知进程**里就双乘了 1.5。
    """
    try:
        v = ctypes.c_int(0)
        if int(ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(v))) == 0:
            return int(v.value)
    except Exception:
        pass
    try:
        user32 = ctypes.windll.user32
        ctx = user32.GetThreadDpiAwarenessContext()
        return int(user32.GetAwarenessFromDpiAwarenessContext(ctx))
    except Exception:
        return 0


def _system_dpi_scale() -> float:
    """系统 DPI / 96。取不到就回 1.0，**不假装知道**。"""
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        if dpi > 0:
            return round(dpi / 96.0, 4)
    except Exception:
        pass
    try:
        dpi = int(ctypes.windll.gdi32.GetDeviceCaps(0, 88))    # LOGPIXELSX
        if dpi > 0:
            return round(dpi / 96.0, 4)
    except Exception:
        pass
    return 1.0


def _ensure_dpi_aware() -> int:
    """把进程设成 PER_MONITOR_AWARE（**与 digest/framecache 同一套调用**）⇒ 返回感知级别。

    ★ 为什么 `ocr.py` 自己也要设：`ocr.py` 可能被**单独**用（判据脚本就是独立进程），
      而 `GetClientRect` 在 unaware 进程里给的是逻辑像素 ⇒ 口径整个错一档。
    ★ 复用 `digest._ensure_dpi_aware()` 作**唯一真源** —— 它与 `framecache._ensure_dpi_aware`
      是同一套调用（shcore.SetProcessDpiAwareness(2) → 退回 user32.SetProcessDPIAware），
      两处各设各的会让坐标系打架。
    ★ 进程已经建过窗口再设会失败（Windows 不许改）⇒ **不假装成功**：
      返回**当下真实的**感知级别，由调用方决定这批坐标可不可信。
    """
    global _dpi_ready
    if not _dpi_ready:
        _dpi_ready = True
        try:
            from agentctl import digest                    # noqa: PLC0415
            digest._ensure_dpi_aware()
        except Exception:
            try:
                ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
    return _dpi_awareness_level()


def dpi_scale(hwnd: int) -> float:
    """`GetDpiForWindow` → dpi/96。取不到就回 1.0，**不假装知道**。"""
    try:
        dpi = int(ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(int(hwnd))))
        if dpi > 0:
            return round(dpi / 96.0, 4)
    except Exception:
        pass
    return 1.0


def client_rect_screen(hwnd: int) -> dict:
    """客户区在**屏幕物理像素**里的位置与大小。取不到就老实说取不到。

    ★★ **M15 A 批口径统一（2026-10-22）+ A1 修正**：
      `GetClientRect`/`ClientToScreen` 给的是**物理**还是**逻辑**像素，取决于**进程**的 DPI 感知：
        · PER_MONITOR_AWARE(2) ⇒ **物理**像素（本机实测 2543×1520）
        · DPI_UNAWARE(0)      ⇒ **逻辑（虚拟化）**像素（本机实测 1695×1013）
      同一个记事本窗口、同一时刻，两种进程读出的客户区差 1.5 倍。
      ★ 改前这里**无条件**乘 `dpi_scale` ⇒ 在**感知进程**里**双乘 1.5**
        （服务进程就是感知进程：`legacy_tools/coord.py` **导入即设** PER_MONITOR_AWARE），
        客户区被报成 3814×2280 ⇒ OCR 跑在一张放大 1.5 倍的位图上，坐标整个错一档。
      ⇒ 现在按**当场问到的感知级别**算，并把 `dpi_aware` / `口径可靠` 一起报出来 ——
        不可靠时**明说不可靠**，由调用方决定这批坐标能不能拿去对齐（B 批会据此拒）。
    """
    out = {"ok": False, "left": 0, "top": 0, "width": 0, "height": 0,
           "logical_width": 0, "logical_height": 0, "原因": "",
           "dpi_aware": 0, "口径可靠": False, "物理": True}
    try:
        import win32gui                                    # noqa: PLC0415（只在真跑时导入）
        aware = _ensure_dpi_aware()
        out["dpi_aware"] = int(aware)
        l, t, r, b = win32gui.GetClientRect(int(hwnd))
        sl, st = win32gui.ClientToScreen(int(hwnd), (l, t))
        k = dpi_scale(hwnd)
        raw_w, raw_h = int(r - l), int(b - t)
        if aware >= 2:
            # PER_MONITOR_AWARE：raw **已经是物理像素**，原点也是物理屏幕坐标 ⇒ 不许再乘。
            out["width"], out["height"] = raw_w, raw_h
            out["left"], out["top"] = int(sl), int(st)
            out["口径可靠"] = True
        elif aware == 1:
            # SYSTEM_DPI_AWARE：raw 是**系统 DPI 下的物理**。本机系统 DPI == 窗口 DPI ⇒ 同一套；
            # 跨不同 DPI 的显示器时这半档不严格 ⇒ 口径标「不可靠」，B 批不许拿它对齐。
            out["width"], out["height"] = raw_w, raw_h
            out["left"], out["top"] = int(sl), int(st)
            out["口径可靠"] = bool(abs(k - _system_dpi_scale()) < 0.01)
            if not out["口径可靠"]:
                out["原因"] = ("进程是 SYSTEM_DPI_AWARE 且系统 DPI(%.2f) ≠ 窗口 DPI(%.2f) "
                               "⇒ 客户区尺寸/原点不是本窗口的真实物理像素"
                               % (_system_dpi_scale(), k))
        else:
            # DPI_UNAWARE：raw 是**逻辑像素** ⇒ 尺寸乘 k 才是物理；
            # ★ 但 `ClientToScreen` 给的是**虚拟化屏幕坐标** ⇒ 原点口径**不可靠**（标出来）。
            out["width"], out["height"] = int(round(raw_w * k)), int(round(raw_h * k))
            out["left"], out["top"] = int(sl), int(st)
            out["口径可靠"] = False
            out["原因"] = ("进程不是 DPI 感知（DPI_UNAWARE）⇒ 客户区原点是虚拟化坐标，"
                           "不是屏幕物理像素；只够做**客户区内**的相对判断")
        out["logical_width"] = int(round(out["width"] / k)) if k else raw_w
        out["logical_height"] = int(round(out["height"] / k)) if k else raw_h
        out["ok"] = (out["width"] > 0 and out["height"] > 0)
        if not out["ok"]:
            out["原因"] = "客户区是空的（最小化？宽或高为 0）"
    except Exception as e:
        out["原因"] = "%s: %s" % (type(e).__name__, e)
    return out


# ── 截图：只取客户区，**内存里**过，不落文件 ──────────────────────────────
def grab_client(hwnd: int, box: dict):
    """截客户区，返回 PIL Image（**客户区物理像素**）。失败抛异常，由上层归成 ok=false。

    ① 先试 `PrintWindow(PW_CLIENTONLY|PW_RENDERFULLCONTENT)` —— 窗口被别的窗口压着也能画出来；
    ② 它对某些窗口（老式控件/硬件加速）会返回全黑或空白，这时退回 `ImageGrab` 按屏幕矩形抓。
    ★ 两条路都只在内存里，**不写任何临时文件**。

    ★★ **M15 A 批口径统一（2026-10-22）**：两条路径现在返回**同尺寸口径**的图
       —— 都是**客户区物理像素**（改前：PrintWindow 返**逻辑尺寸**、ImageGrab 返**物理尺寸**
       ⇒ 同一窗口的 words 坐标差 **1.5 倍**（本机 dpi_scale=1.5），实测差 ~5px）。
       ⇒ 位图按**物理尺寸**建 ⇒ 客户区物理 = `box["width"] × box["height"]`。
    """
    from PIL import Image, ImageGrab                      # noqa: PLC0415
    w, h = int(box.get("width") or 0), int(box.get("height") or 0)
    if w <= 0 or h <= 0:
        raise RuntimeError("客户区尺寸为 0，不截")
    img = None
    try:
        import win32gui, win32ui                           # noqa: PLC0415
        hwnd_dc = win32gui.GetWindowDC(int(hwnd))
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(ctypes.c_void_p(int(hwnd)),
                                              save_dc.GetSafeHdc(), 3)
        if ok:
            img = Image.frombuffer("RGB", (w, h), bmp.GetBitmapBits(True),
                                   "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(int(hwnd), hwnd_dc)
    except Exception:
        img = None
    if img is None:
        img = ImageGrab.grab(bbox=(int(box["left"]), int(box["top"]),
                                     int(box["left"]) + int(box["width"]),
                                     int(box["top"]) + int(box["height"])),
                             all_screens=True)
    if img.size[0] < 2 or img.size[1] < 2:
        raise RuntimeError("截下来的客户区是空的")
    return img


def merge_word_boxes(words):
    """把 Windows OCR 的**逐字**框合并成**词**框（M15 A 批；规则取自 ZCode 意见 §Q1，主控已采纳）。

    ★ 为什么要合：Windows.Media.Ocr 把中文**逐字**切开（「文件」= 文 x=31,w=32 / 件 x=63,w=32，
      首尾相接 gap=0）⇒ 不合就没有「词」这个粒度，后续对齐/可点化全都无从谈起。

    规则（**尺度基准用 h 不用 w** —— 标点框 w 只有半字，用 w 会把阈值拉小误断）：
      · 同行：垂直中心差 ≤ **0.5 × min(h)**
      · 紧邻：水平 gap ≤ **0.5 × min(h)**（gap = b.x − (p.x + p.w)）
      · 同号：|Δh| ≤ **25%**（同一行不同字号（富文本）本就该拆词）
      · 按 x 排序**贪心**；**标点并入左词**；空格（gap > 0.5×h）断词
    输出：词框 = 成员框**并集**；`conf` = min(成员 conf)；`t` = 成员文本依次拼接。

    ★ **纯几何，零依赖**（不引 numpy）。
    """
    out = []
    for b in sorted([dict(x) for x in (words or [])], key=lambda k: k.get("x", 0)):
        p = out[-1] if out else None
        if p is not None:
            try:
                same_row = (abs((p["y"] + p["h"] / 2.0) - (b["y"] + b["h"] / 2.0))
                            <= 0.5 * min(p["h"], b["h"]))
                gap = b["x"] - (p["x"] + p["w"])
                close = gap <= 0.5 * min(p["h"], b["h"])
                same_size = abs(p["h"] - b["h"]) <= 0.25 * max(p["h"], b["h"])
            except Exception:
                same_row = close = same_size = False
            if same_row and close and same_size:
                p["x"] = min(p["x"], b["x"])
                p["y"] = min(p["y"], b["y"])
                p["w"] = max(p["x"] + p["w"], b["x"] + b["w"]) - p["x"]
                p["h"] = max(p["y"] + p["h"], b["y"] + b["h"]) - p["y"]
                p["conf"] = min(p.get("conf", 1.0), b.get("conf", 1.0))
                p["t"] = str(p.get("t") or "") + str(b.get("t") or "")
                continue
        out.append(dict(b))
    return out


def _encode_png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── 引擎 ────────────────────────────────────────────────────────────────
def engine_ok(lang: str = OCR_DEFAULT_LANG, refresh: bool = False) -> dict:
    """引擎能不能起来（**只探测，不截图**）。结果缓存 —— 探测一次就够。

    返回 `{ok, lang, langs, 原因, 上限}`。`ok=false` 时**原因**要说清是「没装 / 没语言 / 导入失败」。
    """
    if _ENGINE["查过"] and not refresh:
        return dict(_ENGINE)
    _ENGINE["查过"] = True
    _ENGINE.update({"ok": False, "lang": "", "langs": [], "原因": "",
                    "上限": OCR_MAX_IMAGE_DIM})
    try:
        from winrt.windows.media.ocr import OcrEngine         # noqa: PLC0415
        try:
            _ENGINE["langs"] = [str(x.language_tag)
                                 for x in OcrEngine.available_recognizer_languages]
        except Exception:
            _ENGINE["langs"] = []
        try:
            _ENGINE["上限"] = int(OcrEngine.max_image_dimension)
        except Exception:
            pass
        eng = None
        try:
            from winrt.windows.globalization import Language   # noqa: PLC0415
            eng = OcrEngine.try_create_from_language(Language(str(lang)))
        except Exception:
            eng = None
        if eng is None:
            eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            _ENGINE["原因"] = ("Windows.Media.Ocr 起不来（要的语言 %s 不在 %s，用户语言也没有）"
                               % (lang, _ENGINE["langs"] or "可用语言列表"))
        else:
            _ENGINE["ok"] = True
            _ENGINE["lang"] = str(lang)
    except Exception as e:
        _ENGINE["原因"] = "导入 Windows.Media.Ocr 失败：%s: %s" % (type(e).__name__, e)
    return dict(_ENGINE)


def _recognize(png_bytes: bytes, lang: str) -> dict:
    """PNG 字节 → WinRT OCR → `{text, words[]}`。**文件 API 一次都不用**（实测那个会炸）。"""
    import asyncio                                          # noqa: PLC0415
    from winrt.windows.graphics.imaging import BitmapDecoder  # noqa: PLC0415
    from winrt.windows.media.ocr import OcrEngine            # noqa: PLC0415
    from winrt.windows.storage.streams import (              # noqa: PLC0415
        DataWriter, InMemoryRandomAccessStream)

    async def _run():
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            from winrt.windows.globalization import Language
            engine = OcrEngine.try_create_from_language(Language(str(lang)))
        if engine is None:
            raise RuntimeError("OcrEngine 起不来")
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png_bytes)
        await writer.store_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        words = []
        for line in result.lines:
            for wd in line.words:
                rect = wd.bounding_rect
                words.append({"t": str(wd.text), "x": int(rect.x), "y": int(rect.y),
                              "w": int(rect.width), "h": int(rect.height)})
        return {"text": str(result.text), "words": words}

    return asyncio.run(_run())


# ── 主入口 ──────────────────────────────────────────────────────────────
def recognize_image(img, lang: str = OCR_DEFAULT_LANG, *, upscale: int = OCR_UPSCALE,
                    cap: int = OCR_MAX_IMAGE_DIM) -> dict:
    """PIL 图（**客户区物理像素**）→ `{"text", "words"（客户区物理像素）, "放大倍数"}`。

    ★ 抽出来是为了让「两条抓图路径是不是同一口径」能被**真判**：
      两条路径各喂一张图进来，比**同一个词的框坐标**，而不是只比图的尺寸。
      （只比尺寸的话，PrintWindow 返 1695×1013、ImageGrab 返 2542×1520 那种
      「同区域差 1.5 倍」的洞会被放过 —— A 批的老判据正是这么漏的。）

    ★ 换算只有一步：**÷放大倍数**。图本身已是客户区物理尺寸 ⇒ **不再乘 dpi_scale**
      （乘一次就双乘；改前那个 ×k 就是双乘的来源之一）。
    """
    f = int(upscale or 1)
    cap = int(cap or OCR_MAX_IMAGE_DIM)
    if max(img.size) * f > cap:
        f = max(1, int(cap // max(1, max(img.size))))
    if f != 1:
        from PIL import Image                       # noqa: PLC0415
        img = img.resize((img.size[0] * f, img.size[1] * f), Image.LANCZOS)
    res = _recognize(_encode_png(img), lang)
    words = []
    for wd in (res.get("words") or []):
        words.append({"t": wd["t"],
                      "x": int(round(wd["x"] / float(f))),
                      "y": int(round(wd["y"] / float(f))),
                      "w": int(round(wd["w"] / float(f))),
                      "h": int(round(wd["h"] / float(f))),
                      "conf": OCR_CONF["text"]})
    return {"text": str(res.get("text") or ""), "words": words, "放大倍数": int(f)}


def ocr_window(hwnd: int, *, rect=None, lang: str = OCR_DEFAULT_LANG,
               max_ms: int = OCR_DEFAULT_MAX_MS) -> dict:
    """对一个窗口的**客户区**做一次 OCR。

    `rect` 是**客户区内**的裁剪框 `(x, y, w, h)` —— 口径为**客户区物理像素**
    （★ M15 A 批统一；改前 docstring 写「客户区逻辑坐标」是**错话**，实测不是）。
    ★ **不许挂**：`max_ms` 到点就返回 `{"ok": false, "原因": "超时…"}` —— 干活过程在守护线程里，
    调用方只 `join(max_ms)`。线程不杀（杀不掉正在跑的 WinRT 调用），但它**不再挡着调用方**。
    ★ **不留文件**：截图 → PNG bytes → WinRT 内存流，全程内存。
    ★ `words[].x/y/w/h` = **客户区相对、客户区物理像素**（口径声明见返回值 `coord_space`）。
    ★ 另有「词框」＝把 Windows OCR 的**逐字**框合并成**词**框（见 `merge_word_boxes`）。
    """
    t0 = time.time()
    budget = max(0.05, float(max_ms or OCR_DEFAULT_MAX_MS) / 1000.0)
    box: dict = {}

    def _work():
        try:
            eng = engine_ok(lang)
            if not eng.get("ok"):
                box["result"] = _fail(eng.get("原因") or "OCR 引擎不可用",
                                      ms=int((time.time() - t0) * 1000))
                return
            geom = client_rect_screen(hwnd)
            box["geom"] = geom
            if not geom.get("ok"):
                box["result"] = _fail(geom.get("原因") or "拿不到客户区",
                                      ms=int((time.time() - t0) * 1000), 客户区=geom)
                return
            img = grab_client(hwnd, geom)
            # ★★ **帧绑定原点**（M15 B 批前提）：抓完**立刻**再取一次几何。
            #   原点/尺寸没动 ⇒ 这一帧的 origin 可信（可以拿去做屏幕物理对齐）；
            #   动了 ⇒ 窗口在抓图期间移动过 ⇒ 这一帧的 origin **不可信** ——
            #   如实标 `画面稳定=False`，B 批据此拒绝对齐（**不许"现取 origin"蒙混过关**）。
            geom2 = client_rect_screen(hwnd)
            _k1 = (geom.get("left"), geom.get("top"), geom.get("width"), geom.get("height"))
            _k2 = (geom2.get("left"), geom2.get("top"), geom2.get("width"), geom2.get("height"))
            if _k1 == _k2:
                geom = {**geom, "画面稳定": True}
            else:
                # 动了 ⇒ 用**抓完那一刻**的几何当帧的 origin，并标不可信。
                geom = {**geom2, "画面稳定": False,
                        "原因": ("抓图期间窗口移动了（几何 %s → %s）⇒ 这一帧的 origin 不可信"
                                 % (_k1, _k2))}
            box["geom"] = geom
            if rect:
                try:
                    rx, ry, rw, rh = [int(v) for v in rect]
                    img = img.crop((rx, ry, rx + max(1, rw), ry + max(1, rh)))
                except Exception:
                    pass                              # 裁剪框不合法就照整块跑，不因此整次失败
            rec = recognize_image(img, lang,
                                  cap=int(eng.get("上限") or OCR_MAX_IMAGE_DIM))
            words = rec["words"]
            total = len(words)
            keep = words[:OCR_MAX_WORDS]
            box["result"] = {
                "ok": True, "engine": OCR_ENGINE_NAME, "lang": str(lang),
                "text": str(rec.get("text") or ""),
                "ms": int((time.time() - t0) * 1000),
                "words": keep,
                "omitted": {"total": total, "returned": len(keep), "cap": OCR_MAX_WORDS},
                "dpi_scale": float(dpi_scale(hwnd)),
                "放大倍数": int(rec.get("放大倍数") or 1),
                # ★ 口径声明（一个就够；**不引入第二套坐标**）
                "coord_space": "client_physical",
                # ★ A 批：把**逐字**框合并成**词**框（Windows OCR 把中文逐字切开）
                "词框": merge_word_boxes(keep),
                "源字数": total,
                "客户区": {"left": geom["left"], "top": geom["top"],
                         "width": geom["width"], "height": geom["height"],
                         "逻辑宽": geom["logical_width"], "逻辑高": geom["logical_height"],
                         # ★ 口径自证三件套：进程感知级别 / 口径可不可靠 / 抓图期间窗口有没有动。
                         #   任何一个不达标，B 批都**不许**拿这批词框去做屏幕物理对齐。
                         "dpi_aware": geom.get("dpi_aware"),
                         "口径可靠": bool(geom.get("口径可靠")),
                         "画面稳定": bool(geom.get("画面稳定")),
                         "origin_口径": ("屏幕物理像素（与 UIA BoundingRectangle 同一套）"
                                      if geom.get("口径可靠") else
                                      "★ 不可靠：%s" % (geom.get("原因") or "未知"))},
                "口径": "坐标 = 客户区相对、**物理像素**（OCR 位图坐标 ÷ 放大倍数；"
                    "截图本身已是客户区物理尺寸 ⇒ 不再乘 dpi_scale）；"
                    "「词框」= 逐字框按 同行+紧邻+同号 合并后的词框（见 merge_word_boxes）。"
                    "conf 用 §⑫ 的 ocr 档，不自造模型。截图只在内存里过，不留文件。",
            }
        except Exception as e:
            box["result"] = _fail("%s: %s" % (type(e).__name__, e),
                                  ms=int((time.time() - t0) * 1000))

    th = threading.Thread(target=_work, name="agentctl-ocr", daemon=True)
    th.start()
    th.join(budget)
    if th.is_alive():
        out = _fail("超时（>%d ms）" % int(max_ms), ms=int(max_ms))
        out["口径"] = "到点就返回，**不挂着**；后台线程可能还在跑，但它不再挡调用方。"
        if box.get("geom"):
            out["客户区"] = box["geom"]
        return out
    return box.get("result") or _fail("内部线程没回结果（不该发生）",
                                      ms=int((time.time() - t0) * 1000))
