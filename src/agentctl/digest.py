# -*- coding: utf-8 -*-
r"""digest.py —— 窗口画面摘要的**唯一来源**（签发与校验共用同一份口径）

★ 为什么要有这个模块（2026-10-21，单 A「统一哈希来源」）
  改前，这一对摘要是**两个来源**：
    · 签发（core.py::_issue_capture_from_shot，core.py:888）：对 legacy 帧文件
      （PrintWindow 落盘 PNG）的**文件字节**做 sha256；
    · 校验（core.py::_bind_capture，core.py:2190）：用 **mss 实时抓屏**拿 RGB 缓冲做 sha256。
  文件字节 vs 实时抓屏 ⇒ 即使画面**一模一样**也必然不等 ⇒ 闸门 gate.py:1179 的比对
  必然判「画面哈希变了」（code=12）⇒ **假拦**。
  而且 verify_image 从来没进过契约（contract/tools.json 里 grep 不到），
  core.py:2183 那个分支**一次都没走到过** ⇒ 「凭据内容寻址」这个能力事实上**不存在**。

★ 口径（四条，少一条就会在别的机器上飘）
  ① **只取客户区**：PrintWindow + PW_CLIENTONLY|PW_RENDERFULLCONTENT(=3)。
     为什么不是整窗 —— 实测（2026-10-21，同一窗口连抓 3 次）：
        整窗(flag=2)：三次 sha256 **各不相同**；逐像素比对：2634 个像素不同
          （占 3.43%），**最大通道差只有 ±1**，差异区域 bbox = x[197..319] y[0..30]
          ⇒ 那是**标题栏**（DWM 文字渲染的亚像素噪声），不是画面变了。
        客户区(flag=3)：三次 sha256 **完全相同**，逐像素 **0 差异**。
     ⇒ 整窗的哈希不可用作「同一张图」的判据；客户区可以。
  ② 对**像素**取 sha256，不对 PNG 文件字节取。
     PNG 是无损但**带编码器版本差异**的格式：同一画面换个编码器就换哈希。
  ③ **拿不到就返回空串**，调用方据此**跳过**比对（闸门 gate.py:1179 的既有语义：
     签发时没拿到摘要就只比 rect/前台/TTL）—— **不许假装有摘要**。
  ④ **绝不退回屏幕抓取**（ImageGrab / mss）：那是**另一个来源**；而且窗口被盖住时
     抓到的是**压在上面那个窗口**的像素。宁可没有摘要，也不要一个来路不明的摘要。

★ 与本项目既有口径的关系
  · ocr.grab_client 用的是同一个 flag=3 客户区口径，但它**带 ImageGrab 退路**
    （ocr.py:115-119）⇒ 摘要这条线**不**复用它，退路在这里是毒药。
  · legacy perceive.py 的 _print_window 是**整窗**(flag=2) + 落 PNG 文件 ⇒ 那是
    「给人看的证据图」，不是摘要来源。两者别混。
"""
from __future__ import annotations

import ctypes
import hashlib

#: 摘要算法代号。口径一改就换号（摘要不跨口径比较）。
ALGO = "client-rgb-sha256-v1"

#: 纯色/近纯色 ⇒ 判为「没画面」（perceive.py::_looks_blank 的同一条规矩：
#: 窗口最小化时 GetWindowRect 返回哨兵矩形，抓到一块既不黑也不空的碎片）。
_BLANK_STDDEV = 3.0

_dpi_ready = False


def _ensure_dpi_aware() -> None:
    """尽力把进程设成 PER_MONITOR_AWARE（拿到的客户区尺寸才是物理像素）。

    ★ 口径与老 coord.py **完全一致**（shcore.SetProcessDpiAwareness(2) → 退回
      user32.SetProcessDPIAware()）：两处各设各的会让坐标系打架。
    ★ 只在**进程启动早期**设一次有效；已经建过窗口后再设会失败 —— 那就沿用现状，
      不报错、不假装成功。
      ★ 调用方（core._issue_capture_from_shot）**先调 _win_rect 再取摘要**：
        _win_rect 会 import 老 coord.py（导入即设 DPI 感知）⇒ 摘要拿到的是物理坐标。
    """
    global _dpi_ready
    if _dpi_ready:
        return
    _dpi_ready = True
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)     # PER_MONITOR_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _capture_client_rgb(hwnd: int):
    """抓窗口**客户区** → PIL RGB 图。拿不到返回 None（调用方据此没有摘要）。

    ★ 这里**没有** ImageGrab 退路 —— 见本模块开头口径④。
    """
    try:
        from PIL import Image
        import win32gui
        import win32ui
    except Exception:
        return None
    try:
        hwnd = int(hwnd)
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        w, h = int(right - left), int(bottom - top)
        if w <= 0 or h <= 0:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        try:
            bmp.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bmp)
            ok = bool(ctypes.windll.user32.PrintWindow(ctypes.c_void_p(hwnd),
                                                       save_dc.GetSafeHdc(), 3))
            if not ok:
                return None
            return Image.frombuffer("RGB", (w, h), bmp.GetBitmapBits(True),
                                    "raw", "BGRX", 0, 1)
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)
    except Exception:
        return None


def _looks_blank(img) -> bool:
    """整块画面「没有内容」：灰度标准差 < 3.0（与 perceive.py 同一条规矩）。"""
    try:
        from PIL import ImageStat
        return ImageStat.Stat(img.convert("L")).stddev[0] < _BLANK_STDDEV
    except Exception:
        return True


def window_digest(hwnd: int) -> str:
    """**规范摘要**：这个窗口此刻画面的 sha256（十六进制）。拿不到返回空串。

    ★ 签发侧与校验侧**必须都调这一个函数** —— 这就是「统一哈希来源」的全部含义。
      调两个函数（哪怕算法一样、参数一样）等于没统一。
    """
    _ensure_dpi_aware()
    img = _capture_client_rgb(hwnd)
    if img is None:
        return ""
    if _looks_blank(img):
        return ""                 # 没有画面 ⇒ 没有摘要（不许假装有）
    try:
        return hashlib.sha256(img.tobytes()).hexdigest()
    except Exception:
        return ""


def describe() -> dict:
    """口径说明（给回执/文档/排查用；判据会核这几行不许被悄悄改掉）。"""
    return {
        "算法": ALGO,
        "范围": "客户区（PW_CLIENTONLY|PW_RENDERFULLCONTENT=3）",
        "粒度": "像素字节的 sha256（不是 PNG 文件字节）",
        "拿不到时": "返回空串 ⇒ 跳过图像比对（仍比 rect/前台/TTL）",
        "退路": "无（绝不退回屏幕抓取）",
    }
