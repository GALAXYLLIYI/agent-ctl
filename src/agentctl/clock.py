# -*- coding: utf-8 -*-
r"""clock.py —— 三层时间源：单调钟 / 墙钟锚点 / 任务栏时钟见证

设计依据（详细设计 §3）：
  · **T1 单调钟** `time.perf_counter()`：本机实测 = QueryPerformanceCounter，10 MHz、
    `monotonic=True`、**`adjustable=False`**、百万次采样零逆序 → **排序一律用它**。
  · **T2 墙钟** `time.time()`：本机实测 **`adjustable=True`**（系统时钟可被改）→ 只当"给人读"的锚。
    锚点对 (mono, wall) 用线性模型 wall ≈ a + b·mono 拟合；b 明显偏离 1 → 时钟被人改过。
  · **T3 见证**：抓任务栏时钟 → **LANCZOS 放大 4 倍**（实测 1x 会漏时间行）→ Windows OCR
    → **归一化全角冒号** → 与墙钟比对。认不出就写"未识别"，**绝不猜**（不猜不编）。

★ 两条 OCR 路的取舍（2026-10-02 实测定案）：
   ①`winrt-Windows.Media.Ocr` 进程内：单次 **6–40 ms** ← 首选
   ② shell 调 PowerShell：单次 **396–412 ms** ← **兜底**（因为①在冻结 exe 里能否加载未验证）
   降级必须留痕：backends 里记 `winrt` / `powershell`。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys
import time

# ── 全角/半角归一化：本机实测 Windows OCR 返回的是全角冒号 `21：18` ──
_FULLWIDTH = str.maketrans("０１２３４５６７８９：／", "0123456789:/")


def normalize_ocr(text: str) -> str:
    return (text or "").translate(_FULLWIDTH)


class Clock:
    """单调钟 + 墙钟锚点。**可注入时钟函数**，便于在不改系统时间的前提下测时钟跳变检测。"""

    def __init__(self, *, mono_fn=time.perf_counter, wall_fn=time.time, max_anchors: int = 240):
        self._mono = mono_fn
        self._wall = wall_fn
        self._max = max_anchors
        self.anchors: list[tuple[float, float]] = []

    # ── 基本读 ──
    def mono(self) -> float:
        return float(self._mono())

    def wall(self) -> float:
        return float(self._wall())

    def wall_iso(self) -> str:
        return _dt.datetime.fromtimestamp(self.wall()).astimezone().isoformat(timespec="milliseconds")

    def anchor(self) -> tuple[float, float]:
        a = (self.mono(), self.wall())
        self.anchors.append(a)
        if len(self.anchors) > self._max:
            del self.anchors[: len(self.anchors) - self._max]
        return a

    # ── 时钟跳变检测 ──
    def drift_report(self) -> dict:
        """用锚点对拟合 wall ≈ a + b·mono。

        b 是"墙钟相对于单调钟的速率"：
          · b ≈ 1        → 正常
          · |b-1| 超阈   → **系统时钟被人为改过**（不是自然漂移；自然漂移远小于此）
        残差最大跳变则用于发现"中途被改一次"。
        """
        n = len(self.anchors)
        if n < 2:
            return {"n": n, "verdict": "样本不足", "slope": None, "max_residual_ms": None}
        ms = [m for m, _ in self.anchors]
        ws = [w for _, w in self.anchors]
        mbar = sum(ms) / n
        wbar = sum(ws) / n
        den = sum((m - mbar) ** 2 for m in ms)
        slope = (sum((m - mbar) * (w - wbar) for m, w in zip(ms, ws)) / den) if den else 1.0
        # 残差：实测墙钟 − 拟合墙钟
        res = [(w - (wbar + slope * (m - mbar))) for m, w in zip(ms, ws)]
        max_res = max(abs(r) for r in res) * 1000.0
        verdict = "正常"
        if abs(slope - 1.0) > 0.02:
            verdict = "时钟被改（斜率异常）"
        elif max_res > 1500.0:
            verdict = "时钟被改（残差跳变）"
        return {"n": n, "slope": slope, "max_residual_ms": max_res, "verdict": verdict,
                "anomaly": verdict != "正常" and n >= 3}

    # ── 会话起手／收尾锚点（§3.2）──
    def session_open(self) -> dict:
        m, w = self.anchor()
        return {"t_mono0": m, "t_wall0": _dt.datetime.fromtimestamp(w).astimezone().isoformat(timespec="milliseconds")}

    def session_close(self) -> dict:
        m, w = self.anchor()
        rep = self.drift_report()
        return {"t_mono1": m, "t_wall1": _dt.datetime.fromtimestamp(w).astimezone().isoformat(timespec="milliseconds"),
                "drift": rep}


# ────────────────────────── T3 见证 ──────────────────────────
class OcrBackend:
    """Windows OCR 双路：①winrt 进程内优先，②PowerShell 兜底。**降级留痕**。"""

    # ★ B 项（2026-10-05，本地 agent 交付审查发现的硬编码）★
    # 原来这里写死本机路径 F:\..._ctl_recon\WinOcrProbe.ps1 ——
    #   ① 换机器就废（A 项消灭的 6 处硬编码之一）
    #   ② ps1 根本没进包（spec 的 datas 里没有），所以装出来的 exe 用不了这条兜底路
    # 现在改成**逐级解析 + 找不到就明确报错**，绝不静默用默认值。
    #
    # 解析顺序：显式参数 → 环境变量 AGENTCTL_OCR_PS1 → 包内 ocr\WinOcrProbe.ps1
    #   （源码态是项目根 ocr 目录，冻结态是 _MEIPASSocr）
    _PS_NAME = "WinOcrProbe.ps1"

    def __init__(self, vendor_dir: str | None = None, ps_script: str | None = None):
        self.vendor = vendor_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vendor")
        self.ps_script = ps_script or self._resolve_ps()
        self._engine = None
        self.backend = None          # 'winrt' | 'powershell' | None
        self.last_error = ""

    @classmethod
    def _resolve_ps(cls) -> str:
        """找 PowerShell 兜底脚本。**找不到返回一个一定不存在的路径**

        为什么返回假路径而不是 None / raise：
          调用方 `_try_powershell` 靠 `os.path.exists` 判断，
          拿到不存在的路径 → 返回 None + 把 last_error 写清楚 → 降级到 winrt。
          这就是「**明确报错但不打断主路**」——
          兜底路挂掉不该让整个 OCR 停摆，但也不能假装它还在。
        """
        env = os.environ.get("AGENTCTL_OCR_PS1")
        if env:
            return env
        here = os.path.dirname(os.path.abspath(__file__))
        roots = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.dirname(here)))   # 项目根（源码态）
        for r in roots:
            cand = os.path.join(r, "ocr", cls._PS_NAME)
            if os.path.isfile(cand):
                return cand
        # 明确告诉你「找过哪、期望在哪」，而不是静默用默认值
        return os.path.join(roots[0] if roots else ".", "ocr",
                            cls._PS_NAME + ".（没找到：请设 AGENTCTL_OCR_PS1）")

    # ① winrt 进程内
    def _try_winrt(self, png_path: str) -> str | None:
        try:
            if self.vendor not in sys.path:
                sys.path.insert(0, self.vendor)
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.graphics.imaging import BitmapDecoder
            from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
        except Exception as e:
            self.last_error = f"winrt import 失败: {type(e).__name__}: {e}"
            return None

        def blk(op):
            if hasattr(op, "get"):
                return op.get()
            import asyncio

            async def _r():
                return await op

            return asyncio.run(_r())

        try:
            if self._engine is None:
                self._engine = OcrEngine.try_create_from_user_profile_languages()
            if self._engine is None:
                self.last_error = "winrt 建不出 OCR 引擎"
                return None
            data = open(png_path, "rb").read()
            ms = InMemoryRandomAccessStream()
            dw = DataWriter(ms)
            dw.write_bytes(data)
            blk(dw.store_async()); blk(dw.flush_async()); dw.detach_stream()
            ms.seek(0)
            dec = blk(BitmapDecoder.create_async(ms))
            bmp = blk(dec.get_software_bitmap_async())
            res = blk(self._engine.recognize_async(bmp))
            self.backend = "winrt"
            return res.text
        except Exception as e:
            self.last_error = f"winrt 调用失败: {type(e).__name__}: {e}"
            return None

    # ② PowerShell 兜底
    def _try_powershell(self, png_path: str) -> str | None:
        if not os.path.exists(self.ps_script):
            # 明确说清「找过哪、怎么指定」，别只丢个路径让人自己猜
            self.last_error = (
                "PowerShell 兜底不可用：找不到 %s"
                "（已找：环境变量 AGENTCTL_OCR_PS1 → 包内 ocr\\）。"
                "主路 winrt 不受影响，但这条兜底此刻是死的。"
                % self.ps_script)
            return None
        try:
            p = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", self.ps_script, "-Path", png_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except Exception as e:
            self.last_error = f"PS 调用失败: {type(e).__name__}: {e}"
            return None
        for line in (p.stdout or "").splitlines():
            if line.strip().startswith("OCR text"):
                self.backend = "powershell"
                return line.split(":", 1)[1].strip().strip("[]")
        self.last_error = f"PS 无 OCR 输出（rc={p.returncode}）"
        return None

    def read(self, png_path: str) -> tuple[str, str]:
        """返回 (文本, 后端)。后端为 'winrt' / 'powershell' / ''（都没成）。"""
        t = self._try_winrt(png_path)
        if t is not None:
            return t, "winrt"
        t = self._try_powershell(png_path)
        if t is not None:
            return t, "powershell"
        return "", ""


_TIME_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})(?:\s*[:：]\s*(\d{2}))?")
# ⚠️ 2026-10-02 验收②暴露的局限：原来只比"时分"，不比日期 ——
#    拿 9/27 的图跟 10/2 的现在比，delta 只算成 9.5 小时（按一天内比），
#    而实际差了 4 天多。屏幕明明读出了日期，就该一起比。此处修正。
_DATE_RE = re.compile(r"(20\d{2})\s*[/\-年\.]\s*(\d{1,2})\s*[/\-月\.]\s*(\d{1,2})")


class Witness:
    """任务栏时钟见证：裁区 → **放大 4 倍** → OCR → 归一化 → 与墙钟比对。"""

    def __init__(self, ocr: OcrBackend | None = None, upscale: int = 4,
                 tol_sec: float = 3.0, tol_sec_minute_only: float = 90.0):
        """tol_sec               = 屏幕上**带秒**时的容差
        tol_sec_minute_only   = 屏幕上**只有时:分**时的容差

        ⚠️ 2026-10-02 验收②抓到的规格错误：本机任务栏时钟**只显示 HH:MM，不显示秒**，
           于是"屏幕 21:18"与"真实 21:18:21"天生差 21 秒 —— 用 2 秒容差会**必然误报 drift**。
           修法：按屏幕精度给容差。只有时:分时，最大合理偏差是一个整分钟（59 s），
           故取 90 s 留余量；带秒时才可以收紧。
        """
        self.ocr = ocr or OcrBackend()
        self.upscale = max(1, int(upscale))       # 实测：1x 会漏时间行，4x 才稳
        self.tol_sec = tol_sec
        self.tol_sec_minute_only = tol_sec_minute_only

    @staticmethod
    def taskbar_clock_box(screen_w: int, screen_h: int, *, bar_px: int = 72, box_w: int = 260) -> tuple[int, int, int, int]:
        """任务栏时钟区（本机实测：任务栏约 72 px 高、时钟在右下角 260×64 内）。"""
        return (max(0, screen_w - box_w), max(0, screen_h - bar_px + 8), screen_w, screen_h)

    def crop_and_upscale(self, src_png: str, out_png: str) -> tuple[int, int, int, int]:
        from PIL import Image
        im = Image.open(src_png)
        box = self.taskbar_clock_box(im.width, im.height)
        c = im.crop(box)
        c.resize((c.width * self.upscale, c.height * self.upscale), Image.LANCZOS).save(out_png)
        return box

    def check(self, src_png: str, work_png: str, *, now_ts: float | None = None) -> dict:
        """对一张（已授权的）全屏图做一次见证。返回结构化结果，**认不出就如实写**。"""
        import datetime as dt
        box = self.crop_and_upscale(src_png, work_png)
        text, backend = self.ocr.read(work_png)
        norm = normalize_ocr(text)
        now = now_ts if now_ts is not None else time.time()
        out = {"t_wall_sys": dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
               "box": list(box), "upscale": self.upscale,
               "ocr_text": text, "ocr_text_norm": norm, "ocr_backend": backend,
               "delta_sec": None, "result": "", "note": ""}
        if not norm.strip():
            out["result"] = "未识别"
            out["note"] = self.ocr.last_error or "OCR 无输出"
            return out
        m = _TIME_RE.search(norm)
        if not m:
            out["result"] = "未识别"
            out["note"] = "识别到文字但解析不出 HH:MM"
            return out
        hh, mm = int(m.group(1)), int(m.group(2))
        ss = int(m.group(3)) if m.group(3) else 0
        if not (0 <= hh < 24 and 0 <= mm < 60):
            out["result"] = "未识别"
            out["note"] = f"时间越界: {hh}:{mm}"
            return out
        sysnow = dt.datetime.fromtimestamp(now)

        # ★ 有日期就比完整日期时间；没有才退回"只比时分"
        dm = _DATE_RE.search(norm)
        if dm:
            try:
                # 与 sysnow 保持一致：都用**朴素本地时间**，否则会报
                # "can't subtract offset-naive and offset-aware datetimes"（验收②抓到过）
                scr = dt.datetime(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)), hh, mm, ss)
                out["screen_full"] = scr.astimezone().isoformat(timespec="seconds")
                delta = abs((scr - sysnow.replace(microsecond=0)).total_seconds())
                out["compared"] = "完整日期时间"
            except ValueError as e:
                out["result"] = "未识别"
                out["note"] = f"日期越界: {dm.groups()} ({e})"
                return out
        else:
            scr = sysnow.replace(hour=hh, minute=mm, second=ss, microsecond=0)
            out["compared"] = "仅时分（屏幕无日期）"
            if m.group(3):
                delta = abs((scr - sysnow.replace(microsecond=0)).total_seconds())
            else:
                delta = abs(((hh * 60 + mm) - (sysnow.hour * 60 + sysnow.minute)) * 60)

        out["delta_sec"] = delta
        out["screen_time"] = f"{hh:02d}:{mm:02d}" + (f":{ss:02d}" if m.group(3) else "")
        has_sec = bool(m.group(3))
        tol = self.tol_sec if has_sec else self.tol_sec_minute_only
        out["tolerance_sec"] = tol
        out["precision"] = "秒" if has_sec else "分（任务栏时钟通常不带秒）"
        out["result"] = "ok" if delta <= tol else "drift"
        return out
