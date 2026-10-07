# -*- coding: utf-8 -*-
r"""_bundled_deps.py —— **只为打包而存在**：把老脚本要用的依赖显式 import 一遍。

为什么需要这个文件（2026-10-02 冻结验收抓到的真问题）：
  服务是"包壳调用老脚本"，`AgentCtl.exe legacy perceive.py` 要靠 **exe 自带的解释器**跑老脚本，
  这样安装包才不必再装一个 Python。但 PyInstaller **只做静态分析** ——
  没有任何模块 import `win32gui`，它就不会被收进包，于是冻结后报：
      ModuleNotFoundError: No module named 'win32gui'

★ 写法上有一条必须遵守（我第一版就写错了）：
  **不能用 `__import__(变量名)`** —— PyInstaller 静态分析看不出循环变量里的字符串，
  照样收不进去。**必须写成字面量的 `import xxx` 语句**，它才看得见。
  所以下面是一长串 try/except + 字面 import，看着笨，但这是**唯一可靠**的写法。

★ 这份清单怎么来的（别靠记忆）：
  直接扫 `tools\*.py` 的全部 import 语句得出 —— 因为我第一版就是靠回忆写的，
  结果漏了 `PIL.ImageStat`，冻结后 `perceive.py` 一跑就崩。
  扫出来的第三方模块里有一批是**论文类一次性脚本**用的：
      docx(63 处) / fitz / matplotlib(7 处)
  它们**不属于控制链路**，打进包会让体积翻倍（matplotlib 一家就 ~50 MB）→ **故意不收**。
  ⛔ 后果要说清：**用 `legacy` 跑那些论文脚本会 ImportError** —— 这是有意为之，不是 bug。

★ 将来老脚本引入**控制链路**的新依赖，必须同步加到这里，否则打包版会在那一步断掉。
"""
from __future__ import annotations


def load() -> dict:
    """逐个 import，返回 {模块名: 成功?}。**缺哪个就少哪个能力，如实报出来**，
    不让服务整体起不来。只在需要时调用（别在每次 CLI 调用都跑）。"""
    r: dict[str, bool] = {}

    # ── pywin32（perceive.py / act.py 必需）──
    try:
        import win32gui; r["win32gui"] = True
    except Exception:
        r["win32gui"] = False
    try:
        import win32api; r["win32api"] = True
    except Exception:
        r["win32api"] = False
    try:
        import win32con; r["win32con"] = True
    except Exception:
        r["win32con"] = False
    try:
        import win32process; r["win32process"] = True
    except Exception:
        r["win32process"] = False
    try:
        import win32ui; r["win32ui"] = True
    except Exception:
        r["win32ui"] = False
    try:
        import win32clipboard; r["win32clipboard"] = True
    except Exception:
        r["win32clipboard"] = False

    # ── UIA / COM ──
    try:
        import uiautomation; r["uiautomation"] = True
    except Exception:
        r["uiautomation"] = False
    try:
        import comtypes; r["comtypes"] = True
    except Exception:
        r["comtypes"] = False
    try:
        import comtypes.client; r["comtypes.client"] = True
    except Exception:
        r["comtypes.client"] = False

    # ── 图像 / 采集 ──
    try:
        from PIL import Image; r["PIL.Image"] = True
    except Exception:
        r["PIL.Image"] = False
    try:
        from PIL import ImageGrab; r["PIL.ImageGrab"] = True
    except Exception:
        r["PIL.ImageGrab"] = False
    try:
        from PIL import ImageDraw; r["PIL.ImageDraw"] = True
    except Exception:
        r["PIL.ImageDraw"] = False
    try:
        from PIL import ImageFont; r["PIL.ImageFont"] = True
    except Exception:
        r["PIL.ImageFont"] = False
    try:
        from PIL import ImageStat; r["PIL.ImageStat"] = True      # ★ 漏过它，冻结后 perceive.py 直接崩
    except Exception:
        r["PIL.ImageStat"] = False
    try:
        import mss; r["mss"] = True
    except Exception:
        r["mss"] = False

    # ── 其它控制链路依赖 ──
    try:
        import win32com.client; r["win32com.client"] = True
    except Exception:
        r["win32com.client"] = False
    try:
        import websocket; r["websocket"] = True                   # cdp.py 走 CDP 要用
    except Exception:
        r["websocket"] = False
    try:
        import zstandard; r["zstandard"] = True
    except Exception:
        r["zstandard"] = False

    # ── 其它 ──
    try:
        import psutil; r["psutil"] = True
    except Exception:
        r["psutil"] = False
    try:
        import numpy; r["numpy"] = True
    except Exception:
        r["numpy"] = False
    try:
        import tkinter; r["tkinter"] = True
    except Exception:
        r["tkinter"] = False

    # ── 新加的：Windows OCR（路线①）与 Windows.Graphics.Capture ──
    try:
        import winrt.windows.media.ocr; r["winrt.windows.media.ocr"] = True
    except Exception:
        r["winrt.windows.media.ocr"] = False
    try:
        import winrt.windows.graphics.imaging; r["winrt.windows.graphics.imaging"] = True
    except Exception:
        r["winrt.windows.graphics.imaging"] = False
    try:
        import winrt.windows.storage.streams; r["winrt.windows.storage.streams"] = True
    except Exception:
        r["winrt.windows.storage.streams"] = False
    try:
        import winrt.windows.globalization; r["winrt.windows.globalization"] = True
    except Exception:
        r["winrt.windows.globalization"] = False
    try:
        import winrt.windows.foundation; r["winrt.windows.foundation"] = True
    except Exception:
        r["winrt.windows.foundation"] = False
    try:
        import windows_capture; r["windows_capture"] = True
    except Exception:
        r["windows_capture"] = False

    return r


if __name__ == "__main__":
    res = load()
    bad = [k for k, v in res.items() if not v]
    print(f"依赖 {len(res)} 项，缺 {len(bad)} 项：" + (", ".join(bad) if bad else "无"))
    raise SystemExit(1 if bad else 0)
