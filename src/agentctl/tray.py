# -*- coding: utf-8 -*-
"""tray.py —— 托盘图标（Windows 通知区）。

为什么不用第三方库：本机没有 pystray，也不想为一个图标引依赖。
做法：**独立线程 + 一个隐藏窗口**（不动 Tk 自己的 WndProc，避免把面板搞崩），
      窗口只做两件事：收托盘消息、弹右键菜单；动作通过队列交回 Tk 主线程。

对外：
  install(tip="AgentCtl 面板") -> Tray   （失败返回 None，绝不抛）
  tray.poll() -> 队列里的事件名（"show" / "stop" / "quit"）或 None
  tray.close()
"""
import ctypes
import ctypes.wintypes as wt
import os
import queue
import threading

WM_APP = 0x8000
WM_TRAY = WM_APP + 21
WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_LBUTTONUP = 0x0203, 0x0205, 0x0202
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
NIM_ADD, NIM_DELETE = 0x0, 0x2
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
TPM_RETURNCMD, TPM_NONOTIFY = 0x0100, 0x0080

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ICO_PATH = os.path.join(_REPO_ROOT, "assets", "agentctl.ico")


def _load_tray_icon(user32):
    """优先 LoadImageW 从 assets/agentctl.ico 加载；失败回退 IDI_APPLICATION。

    图标缺失/损坏绝不许崩托盘 —— 所以每一步都吞异常并落到系统默认图标。
    """
    try:
        if os.path.isfile(_ICO_PATH):
            h = user32.LoadImageW(None, _ICO_PATH, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE)
            if h:
                return h
    except Exception:
        pass
    return user32.LoadIconW(None, IDI_APPLICATION)


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
                ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64),
                ("dwInfoFlags", wt.DWORD), ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wt.HICON)]


class Tray:
    def __init__(self, tip="AgentCtl 面板"):
        self.q = queue.Queue()
        self._hwnd = None
        self._thread = None
        self._alive = False
        self._tip = tip

    # —— 对外 ——
    def poll(self):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        self._alive = False
        if self._hwnd:
            try:
                ctypes.windll.user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass

    # —— 线程 ——
    def start(self):
        self._alive = True
        self._thread = threading.Thread(target=self._run, name="agentctl-tray", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        try:
            self._run_inner()
        except Exception:
            pass                      # 托盘挂了也不影响面板

    def _run_inner(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
        user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        hinst = kernel32.GetModuleHandleW(None)

        def wndproc(hwnd, msg, wp, lp):
            if msg == WM_TRAY:
                if lp == WM_LBUTTONDBLCLK:
                    self.q.put("show")
                elif lp == WM_RBUTTONUP:
                    menu = user32.CreatePopupMenu()
                    user32.AppendMenuW(menu, 0x0000, 1, "显示面板")
                    user32.AppendMenuW(menu, 0x0000, 2, "停止 MCP 服务")
                    user32.AppendMenuW(menu, 0x0800, 0, "")
                    user32.AppendMenuW(menu, 0x0000, 3, "退出")
                    pt = wt.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    user32.SetForegroundWindow(hwnd)
                    cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_NONOTIFY,
                                                pt.x, pt.y, 0, hwnd, None)
                    user32.PostMessageW(hwnd, 0, 0, 0)
                    if cmd == 1:
                        self.q.put("show")
                    elif cmd == 2:
                        self.q.put("stop")
                    elif cmd == 3:
                        self.q.put("quit")
                    return 0
                return 0
            if msg == 0x0010:         # WM_CLOSE
                self._remove_icon()
                user32.DestroyWindow(hwnd)
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        self._wndproc = WNDPROC(wndproc)          # 保引用，别被 GC

        class WNDCLASSEX(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]

        cls = WNDCLASSEX()
        cls.cbSize = ctypes.sizeof(WNDCLASSEX)
        cls.lpfnWndProc = self._wndproc
        cls.hInstance = hinst
        cls.lpszClassName = "AgentCtlTrayWnd"
        if not user32.RegisterClassExW(ctypes.byref(cls)):
            return
        hwnd = user32.CreateWindowExW(0, "AgentCtlTrayWnd", "AgentCtl", 0, 0, 0, 0, 0,
                                      None, None, hinst, None)
        if not hwnd:
            return
        self._hwnd = hwnd

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = _load_tray_icon(user32)
        nid.szTip = self._tip[:120]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            return
        self._nid = nid

        msg = wt.MSG()
        while self._alive and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _remove_icon(self):
        try:
            shell32 = ctypes.windll.shell32
            nid = getattr(self, "_nid", None)
            if nid is not None:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                self._nid = None
        except Exception:
            pass


def install(tip="AgentCtl 面板"):
    """尽力装托盘；失败返回 None（调用方据此只降级提示，不报错）。"""
    try:
        return Tray(tip).start()
    except Exception:
        return None
