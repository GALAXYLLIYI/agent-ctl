# -*- coding: utf-8 -*-
"""ctrl_border —— 电脑控制期间的屏幕边缘状态提示（派单 W4 原型）。

档位 → 颜色（沿用平台 A/B/C/D 分级与急停概念）：
    A 只读感知 = 蓝      B 点击滚动 = 黄
    C 打字快捷键 = 橙    D 拖拽 = 紫
    通行证过期 / 急停(brake) = 红  ★人必须立刻能看出「现在不该再动」

形态：每块显示器四条边的细边框（不是遮罩、不挡视野），每屏四窗。

三个必须绕过的坑（少一个这个功能就有害，见工单 W4）：
    1. 点击穿透：WS_EX_TRANSPARENT + WM_NCHITTEST 返回 HTTRANSPARENT
       —— 否则边框会把用户鼠标挡在外面，比不做还坏。
    2. 不抢焦点：WS_EX_NOACTIVATE —— 否则边框一出现就夺走用户正在输入的窗口。
    3. 不进截图：SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
       —— 本平台 perceive / ctl_shot 靠 GDI 截图工作；边框若进截图：
         ① 画面边缘永远在变，「画面变没变」的判定被污染；
         ② agent 会把自己画的边框当成屏幕上的内容（自己骗自己）。
       若该 API 失败（如 Win10 2004 之前），启动时**如实打印警告**并继续显示
       ——**不静默降级**；调试/留证时用 --no-wda 显式关掉它。

独立运行（本模块是新增原型，尚未接入 core/gate；接入点清单见
`agent-ctl\\work\\_派单W4_边框原型与接入点.md`）：

    cd <工作区>\\Documents\\agent-ctl\\src
    python -X utf8 -m agentctl.ctrl_border --tier C            # 橙
    python -X utf8 -m agentctl.ctrl_border --tier A --duration 5
    python -X utf8 -m agentctl.ctrl_border --brake             # 红
    python -X utf8 -m agentctl.ctrl_border --list-monitors     # 只列显示器

纯标准库 + ctypes，无第三方依赖；不改任何现有文件（W4 边界）。
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import sys
import time

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# ── 常量 ─────────────────────────────────────────────
WNDCLASS_NAME = "AgentCtlCtrlBorder"      # 测试脚本靠它 EnumWindows 找边框窗
WM_NCHITTEST = 0x0084
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
HTTRANSPARENT = -1
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TRANSPARENT = 0x00000020            # ★ 坑 1：点击穿透
WS_EX_NOACTIVATE = 0x08000000             # ★ 坑 2：不抢焦点
WS_EX_TOOLWINDOW = 0x00000080             # 不进任务栏/Alt-Tab
WS_EX_TOPMOST = 0x00000008                # 一直在最前
WDA_EXCLUDEFROMCAPTURE = 0x11             # ★ 坑 3：不进截图（Win10 2004+）
GWL_EXSTYLE = -20
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001                       # winuser.h：CS_VREDRAW=0x0001（写成 0x0004 是未定义位，RegisterClassW 报 87）
COLORREF = ctypes.c_uint32

# 档位色（RGB）。红 = 通行证过期/急停，最要紧的一档。
TIERS = {
    "A": ((0x21, 0x96, 0xF3), "A 级·只读感知（只截图/读树，不动手）"),
    "B": ((0xFF, 0xC1, 0x07), "B 级·点击滚动"),
    "C": ((0xFF, 0x98, 0x00), "C 级·打字快捷键"),
    "D": ((0x9C, 0x27, 0xB0), "D 级·拖拽"),
}
BRAKE_COLOR = ((0xF4, 0x43, 0x36), "通行证过期 / 已刹车 —— 不该再动")


def _rgb_bgr(rgb):
    """(r,g,b) -> COLORREF（Windows 是 BGR 序）。"""
    r, g, b = rgb
    return COLORREF((b << 16) | (g << 8) | r)


# ★ ctypes 在 64 位下 restype 默认 c_int，会把 64 位句柄截断成 32 位
#   （RegisterClassW 就是这样报 87 参数无效的）。凡返回句柄/LRESULT 的都要显式声明。
user32.CreateWindowExW.restype = wt.HWND
user32.LoadCursorW.restype = ctypes.c_void_p
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.SetWindowDisplayAffinity.restype = ctypes.c_int
gdi32.CreateSolidBrush.restype = ctypes.c_void_p
# 不设 argtypes 时 LPARAM/WPARAM 会被当 32 位 int 转 → 64 位值 OverflowError
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]

# WndProc 是模块级函数，靠这个列表找到要销毁的窗口（--duration 到点退出用）
_ALL_HWNDS = []


# ── 窗口过程 ─────────────────────────────────────────
def _wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_NCHITTEST:
        # 双保险：exstyle 已穿透；这里再声明一次（对某些只问 hit-test 的代码路径）
        return HTTRANSPARENT
    if msg == WM_TIMER:
        # --duration 到点。★ DestroyWindow 会**同步**派发 WM_DESTROY，
        #   所以必须先把待销毁列表取走再销毁，否则 WM_DESTROY 里看到的列表非空、
        #   PostQuitMessage 不执行、消息循环永不退出（实测踩过）。
        pend = list(_ALL_HWNDS)
        _ALL_HWNDS.clear()
        for h in pend:
            user32.DestroyWindow(h)
        user32.PostQuitMessage(0)
        return 0
    if msg == WM_DESTROY:
        if not _ALL_HWNDS:
            user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                             ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSW(ctypes.Structure):        # wintypes 没带它，自己定义
    _fields_ = [("style", wt.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class _BorderApp:
    def __init__(self, color, thickness, no_wda, duration):
        self.color = color
        self.thickness = thickness
        self.no_wda = no_wda
        self.duration = duration
        self.hwnds = []
        self._wpc = WNDPROC(_wnd_proc)          # 防回调被 GC
        self._monitors = []

    # -- 显示器枚举 ------------------------------------------------
    def _enum_monitors(self):
        MonitorEnumProc = ctypes.WINFUNCTYPE(
            ctypes.c_int, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), ctypes.c_void_p)
        buf = []

        def cb(hmon, hdc, lprect, lparam):
            buf.append((lprect.contents.left, lprect.contents.top,
                        lprect.contents.right, lprect.contents.bottom))
            return 1

        self._mec = MonitorEnumProc(cb)          # 防回调被 GC
        user32.EnumDisplayMonitors(None, None, self._mec, 0)
        return buf

    def monitors(self):
        if not self._monitors:
            self._monitors = self._enum_monitors()
        return self._monitors

    # -- 窗口类与创建 ----------------------------------------------
    def _register(self):
        wc = WNDCLASSW()
        wc.lpszClassName = WNDCLASS_NAME
        wc.lpfnWndProc = self._wpc
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.hbrBackground = gdi32.CreateSolidBrush(_rgb_bgr(self.color))
        wc.hCursor = user32.LoadCursorW(None, 32512)     # IDC_ARROW
        if not user32.RegisterClassW(ctypes.byref(wc)):
            # 已注册（同进程二次实例化）则沿用
            err = kernel32.GetLastError()
            if err != 1410:                                # ERROR_CLASS_ALREADY_EXISTS
                raise OSError("RegisterClassW 失败：%d" % err)

    def _create(self, x, y, w, h, tag):
        title = "AgentCtlCtrlBorder-%s" % tag
        exstyle = (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE |
                   WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        hwnd = user32.CreateWindowExW(
            exstyle, WNDCLASS_NAME, title,
            WS_POPUP | WS_VISIBLE,
            x, y, w, h, None, None, None, None)
        if not hwnd:
            raise OSError("CreateWindowExW 失败（%s）" % tag)
        # 坑 3：从屏幕捕获里排除（失败=如实报，不静默）
        if not self.no_wda:
            ok = user32.SetWindowDisplayAffinity(wt.HWND(hwnd),
                                                 WDA_EXCLUDEFROMCAPTURE)
            if not ok:
                print("[X] SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) 失败"
                      "（hwnd=%s，GetLastError=%d）——本机/此窗口不支持，"
                      "边框**会**进入截图！平台 perceive/ctl_shot 的画面判定会被污染。"
                      "替代方案：只在无截图动作的间隙显示，或改用任务栏/标题栏指示。"
                      % (hwnd, kernel32.GetLastError()), flush=True)
        return hwnd

    def run(self):
        user32.SetProcessDPIAware()            # 原始像素，防 DPI 缩放错位
        self._register()
        t = self.thickness
        n = 0
        for (x1, y1, x2, y2) in self.monitors():
            w, h = x2 - x1, y2 - y1
            # 四条边；上下两条全长压角，左右两条让出角，避免 z 序缝
            geo = [
                ("top",    x1, y1,         w, t),
                ("bottom", x1, y2 - t,     w, t),
                ("left",   x1, y1 + t,     t, h - 2 * t),
                ("right",  x2 - t, y1 + t, t, h - 2 * t),
            ]
            for tag, gx, gy, gw, gh in geo:
                h = self._create(gx, gy, gw, gh, tag)
                self.hwnds.append(h)
                _ALL_HWNDS.append(h)
                n += 1
        print("[OK] 已画边框：显示器 %d 块 × 每屏 4 条 = %d 个窗口，厚度 %d px"
              % (len(self.monitors()), n, t), flush=True)
        if self.duration and self.duration > 0:
            # 只在一个窗口上设定时器即可（同线程消息队列共享；到点销毁全部）
            user32.SetTimer(self.hwnds[0], 1, int(self.duration * 1000), None)
            print("    --duration=%gs 到点自动退出" % self.duration, flush=True)

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        return 0


def list_monitors():
    app = _BorderApp((0, 0, 0), 0, True, 0)
    user32.SetProcessDPIAware()
    for i, (x1, y1, x2, y2) in enumerate(app.monitors()):
        print("  显示器 %d：%d,%d → %d,%d（%d × %d）"
              % (i, x1, y1, x2, y2, x2 - x1, y2 - y1))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="屏幕边缘状态提示（电脑控制档位可视化，W4 原型）")
    ap.add_argument("--tier", choices=sorted(TIERS), help="档位 A/B/C/D")
    ap.add_argument("--brake", action="store_true",
                    help="急停/通行证过期：红（优先于 --tier）")
    ap.add_argument("--duration", type=float, default=0,
                    help="显示秒数，到点自动退出（默认 0 = 一直显示）")
    ap.add_argument("--thickness", type=int, default=8, help="边框厚度 px（默认 8）")
    ap.add_argument("--no-wda", action="store_true",
                    help="关闭「不进截图」（仅调试/截图留证用）")
    ap.add_argument("--list-monitors", action="store_true", help="只列出显示器")
    a = ap.parse_args(argv)

    if a.list_monitors:
        list_monitors()
        return 0
    if a.brake:
        color, desc = BRAKE_COLOR
    elif a.tier:
        color, desc = TIERS[a.tier]
    else:
        ap.error("要 --tier A/B/C/D 或 --brake（至少给一个）")
    print("档位：%s  颜色 RGB=%s" % (desc, color), flush=True)
    app = _BorderApp(color, a.thickness, a.no_wda, a.duration)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
