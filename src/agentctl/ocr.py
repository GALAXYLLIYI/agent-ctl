# -*- coding: utf-8 -*-
r"""窗G2 · OCR 通道（R3）：**Windows.Media.Ocr**，纯读，不碰输入、不留文件。

为什么是它：本机 **tesseract 不在**，`winrt` 与 PIL 可用 ⇒ 走系统自带引擎
（框架 §㉓ 下一步排序第 2 条）。前人已实测（`docs\证据\WindowsOCR实测_20261002.txt`）：
普通进程（无 MSIX package identity）就能调 `Windows.Media.Ocr`，本机可用语言只有 `zh-Hans-CN`，
`MaxImageDimension = 10000`，且**原尺寸会漏行、4 倍放大才认全** —— 所以下面默认放大 4 倍。

四条硬约束（派单 §一.1）：
  ① 只截**客户区**，截图只在**内存里**走一圈（PIL → PNG bytes → WinRT 内存流），**不落任何文件**；
  ② 坐标**换算回物理像素**：`GetDpiForWindow` 取 DPI，`dpi_scale = dpi/96` 写进返回，**不写死 1.0**；
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
           "dpi_scale": 1.0}
    out.update(extra)
    return out


# ── 窗口几何与 DPI ───────────────────────────────────────────────────────
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
    """客户区在**屏幕物理像素**里的位置与大小。取不到就老实说取不到。"""
    out = {"ok": False, "left": 0, "top": 0, "width": 0, "height": 0,
           "logical_width": 0, "logical_height": 0, "原因": ""}
    try:
        import win32gui                                    # noqa: PLC0415（只在真跑时导入）
        l, t, r, b = win32gui.GetClientRect(int(hwnd))
        sl, st = win32gui.ClientToScreen(int(hwnd), (l, t))
        out["logical_width"], out["logical_height"] = int(r - l), int(b - t)
        out["left"], out["top"] = int(sl), int(st)
        k = dpi_scale(hwnd)
        out["width"] = int(round(out["logical_width"] * k))
        out["height"] = int(round(out["logical_height"] * k))
        out["ok"] = (out["logical_width"] > 0 and out["logical_height"] > 0)
        if not out["ok"]:
            out["原因"] = "客户区是空的（最小化？宽或高为 0）"
    except Exception as e:
        out["原因"] = "%s: %s" % (type(e).__name__, e)
    return out


# ── 截图：只取客户区，**内存里**过，不落文件 ──────────────────────────────
def grab_client(hwnd: int, box: dict):
    """截客户区，返回 PIL Image（逻辑尺寸）。失败抛异常，由上层归成 ok=false。

    ① 先试 `PrintWindow(PW_CLIENTONLY|PW_RENDERFULLCONTENT)` —— 窗口被别的窗口压着也能画出来；
    ② 它对某些窗口（老式控件/硬件加速）会返回全黑或空白，这时退回 `ImageGrab` 按屏幕矩形抓。
    ★ 两条路都只在内存里，**不写任何临时文件**。
    """
    from PIL import Image, ImageGrab                      # noqa: PLC0415
    w, h = int(box.get("logical_width") or 0), int(box.get("logical_height") or 0)
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
def ocr_window(hwnd: int, *, rect=None, lang: str = OCR_DEFAULT_LANG,
               max_ms: int = OCR_DEFAULT_MAX_MS) -> dict:
    """对一个窗口的**客户区**做一次 OCR。

    `rect` 是**客户区内**的裁剪框 `(x, y, w, h)`（客户区逻辑坐标）；不给就整块客户区。
    ★ **不许挂**：`max_ms` 到点就返回 `{"ok": false, "原因": "超时…"}` —— 干活过程在守护线程里，
    调用方只 `join(max_ms)`。线程不杀（杀不掉正在跑的 WinRT 调用），但它**不再挡着调用方**。
    ★ **不留文件**：截图 → PNG bytes → WinRT 内存流，全程内存。
    ★ `words[].x/y/w/h` = **客户区相对、客户区物理像素**；`dpi_scale` 就是换算用的因子。
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
            if rect:
                try:
                    rx, ry, rw, rh = [int(v) for v in rect]
                    img = img.crop((rx, ry, rx + max(1, rw), ry + max(1, rh)))
                except Exception:
                    pass                              # 裁剪框不合法就照整块跑，不因此整次失败
            f = int(OCR_UPSCALE)
            cap = int(eng.get("上限") or OCR_MAX_IMAGE_DIM)
            if max(img.size) * f > cap:
                f = max(1, int(cap // max(1, max(img.size))))
            if f != 1:
                from PIL import Image                   # noqa: PLC0415
                img = img.resize((img.size[0] * f, img.size[1] * f), Image.LANCZOS)
            res = _recognize(_encode_png(img), lang)
            k = float(dpi_scale(hwnd))
            words = []
            for wd in (res.get("words") or []):
                # OCR 给的是**放大后**位图里的坐标：÷放大倍数 → 客户区逻辑像素；×dpi_scale → 物理像素
                words.append({"t": wd["t"],
                              "x": int(round(wd["x"] / float(f) * k)),
                              "y": int(round(wd["y"] / float(f) * k)),
                              "w": int(round(wd["w"] / float(f) * k)),
                              "h": int(round(wd["h"] / float(f) * k)),
                              "conf": OCR_CONF["text"]})
            total = len(words)
            keep = words[:OCR_MAX_WORDS]
            box["result"] = {
                "ok": True, "engine": OCR_ENGINE_NAME, "lang": str(lang),
                "text": str(res.get("text") or ""),
                "ms": int((time.time() - t0) * 1000),
                "words": keep,
                "omitted": {"total": total, "returned": len(keep), "cap": OCR_MAX_WORDS},
                "dpi_scale": k,
                "放大倍数": int(f),
                "源字数": total,
                "客户区": {"left": geom["left"], "top": geom["top"],
                         "width": geom["width"], "height": geom["height"],
                         "逻辑宽": geom["logical_width"], "逻辑高": geom["logical_height"]},
                "口径": "坐标 = 客户区相对、**物理像素**（OCR 位图坐标 ÷ 放大倍数 × dpi_scale）；"
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
