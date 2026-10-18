# -*- coding: utf-8 -*-
r'''坐标工具 —— 统一 DPI 口径，杜绝"手算坐标"这类错

【为什么需要它（2026-10-01 实测事故）】
  本机屏幕 **物理 2560×1600**，但 **DPI 未感知的进程**问系统只能得到 **1463×914**（虚拟化），
  缩放比 **168/96 = 1.75**。
  我先前用"逻辑坐标"算输入框位置、再交给"已设 DPI 感知"的 act.py 执行
  → **两套坐标系打架** → 点到 (615,901) 而目标在 (1610,800) → 怎么点都不中。

【铁律（本文件强制）】
  1. **凡是要传给 act.py / perceive.py 的坐标，一律是物理口径**
  2. **任何脚本在取窗口矩形之前，必须先 import 本模块**（它在导入时就把 DPI 感知设好）
  3. 不许再手算缩放 —— 用 `win_rect()` / `client_origin()` / `to_physical()`

用法：
  import sys; sys.path.insert(0, r"<老线 tools 目录>")   # 示例：换成你自己的路径
  import coord                                  # 导入即设好 DPI 感知
  print(coord.screen())                         # (2560, 1600) 物理
  print(coord.scale())                          # 1.75
  print(coord.win_rect(hwnd))                   # 物理口径窗口矩形
  print(coord.client_origin(hwnd))              # 客户区屏幕原点（物理）
'''
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

# ── ① 导入时就设 DPI 感知（必须最早做，晚了就不生效）──
_DPI_MODE = "未设置"
try:
    _hr = ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
    _DPI_MODE = "shcore PER_MONITOR_AWARE (hr=%s)" % _hr
except Exception as _e1:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        _DPI_MODE = "user32 SetProcessDPIAware (fallback)"
    except Exception as _e2:
        _DPI_MODE = "★ 设置失败: %s / %s" % (_e1, _e2)

user32 = ctypes.WinDLL("user32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


def dpi_mode() -> str:
    return _DPI_MODE


def awareness() -> int:
    """0=UNAWARE 1=SYSTEM 2=PER_MONITOR -1=查询失败"""
    v = ctypes.c_int(-2)
    try:
        shcore.GetProcessDpiAwareness(None, ctypes.byref(v))
    except Exception:
        return -1
    return v.value


def screen() -> tuple[int, int]:
    """物理屏幕分辨率（DPI 感知设好后即为真值）"""
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def screen_dpi() -> int:
    hdc = user32.GetDC(0)
    try:
        return int(gdi32.GetDeviceCaps(hdc, 88))       # LOGPIXELSX
    finally:
        user32.ReleaseDC(0, hdc)


def scale() -> float:
    return screen_dpi() / 96.0


def is_sane() -> tuple[bool, str]:
    """自检：DPI 感知必须是 PER_MONITOR(2)，且分辨率应等于抓屏尺寸"""
    a = awareness()
    w, h = screen()
    try:
        from PIL import ImageGrab
        gw, gh = ImageGrab.grab().size
    except Exception:
        gw, gh = (-1, -1)
    ok = (a == 2) and (gw < 0 or (w, h) == (gw, gh))
    msg = ("DPI=%s 系统口径=%dx%d 抓屏=%sx%s 缩放=%.3f %s"
           % (a, w, h, gw, gh, scale(), "✅" if ok else "❌ 口径不一致"))
    return ok, msg


def win_rect(hwnd: int) -> tuple[int, int, int, int]:
    """窗口矩形（物理口径）"""
    r = wt.RECT()
    if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
        raise OSError("GetWindowRect 失败 hwnd=%s" % hwnd)
    return r.left, r.top, r.right, r.bottom


def client_origin(hwnd: int) -> tuple[int, int]:
    """客户区左上角在屏幕上的坐标（物理口径）"""
    p = wt.POINT(0, 0)
    if not user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(p)):
        raise OSError("ClientToScreen 失败 hwnd=%s" % hwnd)
    return p.x, p.y


def client_size(hwnd: int) -> tuple[int, int]:
    c = wt.RECT()
    if not user32.GetClientRect(wt.HWND(hwnd), ctypes.byref(c)):
        raise OSError("GetClientRect 失败 hwnd=%s" % hwnd)
    return c.right - c.left, c.bottom - c.top


def client_to_screen(hwnd: int, cx: int, cy: int) -> tuple[int, int]:
    """客户区坐标 → 屏幕物理坐标"""
    ox, oy = client_origin(hwnd)
    return ox + cx, oy + cy


def image_to_client(img_w: int, img_h: int, hwnd: int) -> float:
    """截图尺寸 → 客户区尺寸 的换算比。

    PrintWindow 截出来的图**不一定是客户区尺寸**（可能被 DWM 放大），
    用这个比值把"截图里的像素坐标"换回"客户区坐标"。
    """
    cw, ch = client_size(hwnd)
    if img_w <= 0 or img_h <= 0:
        return 1.0
    return cw / float(img_w)


def sanity_report() -> str:
    ok, msg = is_sane()
    return "[coord] %s | 模式=%s" % (msg, _DPI_MODE)


if __name__ == "__main__":
    print("=== 坐标工具自检 ===")
    print("  DPI 模式      : %s" % _DPI_MODE)
    ok, msg = is_sane()
    print("  %s" % msg)
    print("  屏幕(物理)    : %d x %d" % screen())
    print("  屏幕 DPI      : %d  → 缩放 %.3f 倍" % (screen_dpi(), scale()))
    print("  感知级别      : %d  (2=PER_MONITOR 才是对的)" % awareness())
    print("")
    print("  用法示例：")
    print("    import coord")
    print("    x0,y0,x1,y1 = coord.win_rect(hwnd)          # 物理口径")
    print("    sx, sy = coord.client_to_screen(hwnd, cx, cy) # 客户区→屏幕")
