# -*- coding: utf-8 -*-
"""ctrl_border —— 电脑控制期间的屏幕边缘状态提示（派单 W4 原型）。

档位 → 颜色 / 厚度（★ 2026-10-15 窗F 定板，用户 20:41 拍板，**别再随手刷色**）：
    A 只读感知   = #8FD8FF 浅蓝   3 px
    B 点击滚动   = #3BA6F0 中浅蓝 4 px
    C 打字快捷键 = #1565C0 中深蓝 5 px
    D 拖拽       = #0B3D91 深蓝   6 px（封顶）
    通行证过期 / 急停(brake) = #F44336 红 6 px
        ★人必须立刻能看出「现在不该再动」
        —— **红不在渐变里**：红只表示"状态"，不占档位（A–D 谁都不是红）。

为什么这么定（理由钉在这，改回去之前先读这段）：
    ① 按权限**逐档加深**（A 最浅 → D 最深）。改色前的色序是「蓝→黄→橙→紫」，
       深浅乱跳 —— B 的黄比 A 的蓝还亮，看着像"随便挑的"，读不出轻重。
    ② **逐档加厚** 3→6：D 档封顶 6 px（原默认 8 px 反而太扎眼，
       用户原话「最厚别太明显」）。红也用 6。
    ③ 每档 **1px 反色描边**：亮档（A/B/红）配深描边 #0A0E14，
       暗档（C/D）配浅描边 #F5F8FF，落在**主色最内一圈**、**总厚不变**。
       为什么非加不可：实测 A 在白底只有 1.56:1、D 在深底只有 1.93:1
       （WCAG 判「看得见的非文本」要 3:1）—— 纯色一维，必然有一端在
       某种底色上直接消失。描边是那一端唯一的救命稻草。
冻结钉子：tests/_2h_wF_配色冻结判据.py —— 五个 RGB + 五档厚度**写死成快照**，
       谁改了配色/厚度它就红（连契约里的颜色名一起钉，schema_version 钉死在 5）。

形态：每块显示器四条边的细边框（不是遮罩、不挡视野），每屏 8 个窗口
    —— 每边 1 个主色窗口 + 1 个 1px 描边窗口，两者**不重叠**、加起来仍是总厚。

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

    cd <仓库根>\\src
    python -X utf8 -m agentctl.ctrl_border --tier C            # 中深蓝
    python -X utf8 -m agentctl.ctrl_border --tier A --duration 5
    python -X utf8 -m agentctl.ctrl_border --brake             # 红
    python -X utf8 -m agentctl.ctrl_border --list-monitors     # 只列显示器

★ T1/W3-B（2026-10-14）新增 **--follow**：自己盯通行证档位＋急停 flag，颜色自动跟着变，
由 server.py / mcp.py 在启动时 spawn 一次、退出时收掉。判定优先级（--once 会原样打印）：
    红 ← 急停 flag 存在 / 通行证读失败或 JSON 坏 / consumed=true / 已过期
    四档蓝 ← scopes 里取**最高**档（A<B<C<D），A+B+C ⇒ C（中深蓝）
    隐藏 ← 通行证文件不存在（还没授权）。**隐藏不是故障**：服务常驻，没证是常态，
          开机就常红等于狼来了 —— 真出事（急停/过期/用完/读坏）才该红。
          ★ 隐藏时**必往日志写一行**，免得「看不见边框」被误判成「急停了」。
    想翻成常红：--idle red 或 AGENTCTL_BORDER_IDLE=red（不改代码）。
    `python -X utf8 -m agentctl.ctrl_border --follow`
    `python -X utf8 -m agentctl.ctrl_border --once`     # 打印一次判定就退出，不画窗口

纯标准库 + ctypes，无第三方依赖；不改任何现有文件（W4 边界）。
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

# ★ W3-B：只在**作为主程序跑**时重排 stdout。
#   原先在 import 时就无条件 reconfigure —— W3-B 要让 server.py / mcp.py import 本模块，
#   那会把它们的 stdout 一并改掉（mcp.py 的 stdout 是 JSON-RPC 协议专用，不能乱碰）。
if __name__ == "__main__" and sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# ── 常量 ─────────────────────────────────────────────
WNDCLASS_NAME = "AgentCtlCtrlBorder"      # 测试脚本靠它 EnumWindows 找边框窗
# ★ 窗F：1px 反色描边**必须**另开一个窗口类 ——
#   窗口类的背景刷（hbrBackground）是**整个类一份**，一个类刷不了两种颜色。
#   所以描边那 4 个窗口用这个类（名字带 -edge），刷子是描边色。
#   ⚠ 枚举边框窗的脚本要认**两个**类名（tests\_T1_窗3_自证.py 已同步）。
WNDCLASS_EDGE_NAME = "AgentCtlCtrlBorderEdge"
WM_NCHITTEST = 0x0084
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
HTTRANSPARENT = -1
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
SW_HIDE = 0
SW_SHOW = 5
WS_EX_TRANSPARENT = 0x00000020            # ★ 坑 1：点击穿透
WS_EX_NOACTIVATE = 0x08000000             # ★ 坑 2：不抢焦点
WS_EX_TOOLWINDOW = 0x00000080             # 不进任务栏/Alt-Tab
WS_EX_TOPMOST = 0x00000008                # 一直在最前
WDA_EXCLUDEFROMCAPTURE = 0x11             # ★ 坑 3：不进截图（Win10 2004+）
GWL_EXSTYLE = -20
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001                       # winuser.h：CS_VREDRAW=0x0001（写成 0x0004 是未定义位，RegisterClassW 报 87）
COLORREF = ctypes.c_uint32

# ★ W3-B 新增：--follow 状态机 / 单例 / 父进程监护 / start_follow() 用的常量
PM_REMOVE = 0x0001
# 单例互斥量名。★ 允许用 AGENTCTL_BORDER_MUTEX 覆盖 —— **仅测试/多实例用**；
#   生产留空就是下面这个默认值，单例行为一字不变。
#   为什么要这个口子：本机可能**已经有 agentctl 服务在跑**并占着默认互斥量，
#   那时判据起的每个 --follow 都会正确地秒退「已有一个在跑」——
#   判据不是错，是**在跟生产实例抢**。让判据用自己的互斥量，才测得到自己那段逻辑。
#   （与 --pass-file / --brake-flag / AGENTCTL_BORDER_LOG 是同一类"测试专用覆盖"。）
FOLLOW_MUTEX = os.environ.get("AGENTCTL_BORDER_MUTEX") or "Local\\AgentCtlCtrlBorderFollow"
CREATE_NO_WINDOW = 0x08000000             # 别让边框子进程闪一个黑框
SYNCHRONIZE = 0x00100000                   # OpenProcess 权限：只等不等
WAIT_OBJECT_0 = 0
ERROR_ALREADY_EXISTS = 183

# ★ 窗Y（2026-10-17 孤儿边框）新增：Job Object + 进程创建时间。
#   为什么要有这套：--follow 原来只拿 OpenProcess(SYNCHRONIZE) 盯**父的 pid**，
#   而 pid 是**会被系统回收再利用**的。父死之后、pid 被分给别的活进程时，
#   子进程就永远盯着一台**跟自己无关**的进程，父进程监护等于没有 ⇒
#   屏幕上留下 8 个 AgentCtlCtrlBorder* 孤儿窗口（2026-10-17 11:48 pid 16400 现场）。
#   取证与复现见 work\_孤儿取证_改前.py / work\_孤儿取证_改前_输出.txt。
#   现在**两条腿一起上**：
#     ① 内核腿（主）：start_follow 建一个带 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 的
#        Job，把子进程 Assign 进去 ⇒ **父进程一死，OS 自己收掉子进程**，
#        不靠任何轮询/猜测。
#     ② 身份腿（辅）：开口那一刻记下父的**创建时间**（GetProcessTimes）；
#        对不上（pid 已被复用 = 换了人）⇒ 认亲失败 ⇒ 收摊并留日志，不静默。
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9        # winbase.h 的 JobObjectInformationClass
JOB_OBJECT_QUERY = 0x0004
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_INVALID_PARAMETER = 87                 # OpenProcess：pid 在进程表里不存在
QS_ALLINPUT = 0x04FF                         # MsgWaitForMultipleObjects 的等待条件
WAIT_FAILED = 0xFFFFFFFF
# 开口（OpenProcess 父句柄）失败时的**宽限期**（秒）：给「父正在退出、句柄还没落地」
# 留余量。过了还开不到就认「管不了」⇒ 自己收摊。
#   ⛔ 原来的行为是**永远开、永远返回 False**（fail-open）—— 那就是孤儿边框的老路。
PARENT_OPEN_GRACE = 3.0
# 「监护没生效」告警的**重复播报间隔**（秒）：文案一字不改，只是不再每个轮询刷一遍
#   把日志淹了（原实现 0.5 s 一条，实测 6 s 刷 12 条）。
PARENT_OPEN_WARN_EVERY = 5.0

# ══════════ 配色 / 厚度 / 反色描边（窗F 2026-10-15 定板）══════════════════
# ★ 这三个表是**契约级**的：用户 20:41 拍板、DSH 定死五个值。
#   改任何一个都必须走单独立项 + 用户重新拍板，并同步
#   contract/tools.json 的 conventions.edge_border_hint 与冻结判据
#   tests\_2h_wF_配色冻结判据.py（那份判据会当场红，这是它存在的意义）。
#
# 档位色（RGB）。红 = 通行证过期/急停，**不在 A–D 渐变里**（红只表状态）。
TIERS = {
    "A": ((0x8F, 0xD8, 0xFF), "A 级·只读感知（只截图/读树，不动手）"),
    "B": ((0x3B, 0xA6, 0xF0), "B 级·点击滚动"),
    "C": ((0x15, 0x65, 0xC0), "C 级·打字快捷键"),
    "D": ((0x0B, 0x3D, 0x91), "D 级·拖拽"),
}
BRAKE_COLOR = ((0xF4, 0x43, 0x36), "通行证过期 / 已刹车 —— 不该再动")

# 每档厚度（物理 px）：权限越大线越粗。D 档封顶 6（用户要「最厚别太明显」）。
# ★ 改色前是**全局单值 8**（--thickness 的默认值），厚薄不表达轻重，
#   只有颜色在变 —— 档位是"逐级加重权限"，视觉上也该逐级加重。
TIER_THICKNESS = {"A": 3, "B": 4, "C": 5, "D": 6}
BRAKE_THICKNESS = 6                      # 红用 6（与 D 同厚：红是"最要紧的一档"）
# 兜底厚度：颜色不在表里时用（理论上走不到 —— decide() 已 fail-closed 成红，
# 但真走到了也不该静默画出 0 px）。
FALLBACK_THICKNESS = 6

# 1px 反色描边：亮档配深描边、暗档配浅描边。分界取 CIE L* > 55
# （实测：A 83.0 / B 65.4 / 红 55.6 ⇒ 深；C 43.2 / D 28.0 ⇒ 浅）。
EDGE_DARK = (0x0A, 0x0E, 0x14)
EDGE_LIGHT = (0xF5, 0xF8, 0xFF)
EDGE_LSTAR_THRESHOLD = 55.0
TIER_EDGE = {
    "A": EDGE_DARK,          # 浅蓝 ⇒ 深描边（白底上才勾得出来）
    "B": EDGE_DARK,
    "C": EDGE_LIGHT,         # 中深蓝 ⇒ 浅描边（深底上才勾得出来）
    "D": EDGE_LIGHT,
}
BRAKE_EDGE = EDGE_DARK      # 红 L*=55.6 > 55

# 判定状态（decide() 的返回值）。「隐藏」是第四态，不是红也不是故障。
ST_TIER = "tier"          # 有证 ⇒ 按最高档上色
ST_RED = "red"            # 该红：急停/过期/用完/读坏
ST_HIDDEN = "hidden"      # 没证 ⇒ 隐藏（--idle red 可翻成红）


def _rgb_bgr(rgb):
    """(r,g,b) -> COLORREF（Windows 是 BGR 序）。"""
    r, g, b = rgb
    return COLORREF((b << 16) | (g << 8) | r)


def _srgb_lin(c8):
    """8 位 sRGB 分量 → 线性光（IEC 61966-2-1）。"""
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb):
    """WCAG 相对亮度 Y（0~1）。"""
    r, g, b = (_srgb_lin(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def lstar(rgb):
    """CIE 1976 L*（0=黑、100=白）。反色描边就按它分档。"""
    y = relative_luminance(rgb)
    if y <= 216.0 / 24389.0:
        return y * (24389.0 / 27.0)
    return 116.0 * (y ** (1.0 / 3.0)) - 16.0


def pick_edge(rgb):
    """这个主色该配哪一档 1px 描边：偏亮配深，偏暗配浅。

    ★ TIER_EDGE 是这条规则的**抄本**，本函数是**规则本身**。
    两者必须一致 —— 冻结判据 tests/_2h_wF_配色冻结判据.py 把两边都钉住，
      只改一边会当场红（不然就静默分家：表说深、运行期画浅）。
    """
    return EDGE_DARK if lstar(rgb) > EDGE_LSTAR_THRESHOLD else EDGE_LIGHT


_STYLE_BY_COLOR = None


def _style_by_color():
    """主色 RGB → (厚度, 描边色)。现搭：四档 + 红一起进表（红也查得到）。"""
    global _STYLE_BY_COLOR
    if _STYLE_BY_COLOR is None:
        m = {}
        for tier, (rgb, _desc) in TIERS.items():
            m[tuple(rgb)] = (TIER_THICKNESS[tier], TIER_EDGE[tier])
        m[tuple(BRAKE_COLOR[0])] = (BRAKE_THICKNESS, BRAKE_EDGE)
        _STYLE_BY_COLOR = m
    return _STYLE_BY_COLOR


def resolve_style(color, thickness=None):
    """(主色, 厚度覆盖) ⇒ (总厚度, 描边色)。

    ★ 显式给了 thickness 就用它（老用法不许被配色改死：--thickness 12 还是 12）；
      没给 ⇒ 按档取（A 3 / B 4 / C 5 / D 6，红 6）。
    ★ 描边永远随主色亮暗走，**且不计入总厚** —— 它占的是主色最内那一像素。
    """
    auto_t, edge = _style_by_color().get(
        tuple(color), (FALLBACK_THICKNESS, pick_edge(color)))
    t = auto_t if thickness is None else int(thickness)
    return max(1, int(t)), edge


def _edge_geometry(x1, y1, x2, y2, t):
    """一块屏幕的四条边 ⇒ 8 个矩形：(标签, x, y, 宽, 高, "body"/"edge")。

    ★ **总厚 = t，描边不加厚**：主色 (t-1) px 占外侧、1px 描边占**最内一圈**，
      两块**不重叠**，合起来从屏幕边缘往里正好 t px。
    ★ 描边必须在最内一圈（不是最外）：从上往下看是「外圈主色 → 内圈描边」，
      这样描边永远紧贴内容区，在任何底色上都是那条"最后一道界"。
    ★ 上下两条全长压角、左右两条让出角，避免 z 序缝（老做法照旧）。
    ★ 纯算术、不碰 Win32 ⇒ 冻结判据能直接调它验几何，跑判据时屏幕不闪边框。
    """
    t = max(1, int(t))
    w, h = x2 - x1, y2 - y1
    b = t - 1
    if b <= 0:
        # t=1：没有留给描边的像素。宁可只画主色，也不画一个 0 px 的窗口。
        return [("top", x1, y1, w, t, "body"),
                ("bottom", x1, y2 - t, w, t, "body"),
                ("left", x1, y1 + t, t, h - 2 * t, "body"),
                ("right", x2 - t, y1 + t, t, h - 2 * t, "body")]
    return [
        ("top", x1, y1, w, b, "body"),
        ("top", x1, y1 + t - 1, w, 1, "edge"),
        ("bottom", x1, y2 - t + 1, w, b, "body"),
        ("bottom", x1, y2 - t, w, 1, "edge"),
        ("left", x1, y1 + t, b, h - 2 * t, "body"),
        ("left", x1 + t - 1, y1 + t, 1, h - 2 * t, "edge"),
        ("right", x2 - t + 1, y1 + t, b, h - 2 * t, "body"),
        ("right", x2 - t, y1 + t, 1, h - 2 * t, "edge"),
    ]
# ★ ctypes 在 64 位下 restype 默认 c_int，会把 64 位句柄截断成 32 位
#   （RegisterClassW 就是这样报 87 参数无效的）。凡返回句柄/LRESULT 的都要显式声明。
user32.CreateWindowExW.restype = wt.HWND
user32.LoadCursorW.restype = ctypes.c_void_p
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.SetWindowDisplayAffinity.restype = ctypes.c_int
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.UnregisterClassW.argtypes = [wt.LPCWSTR, ctypes.c_void_p]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND,
                                wt.UINT, wt.UINT, wt.UINT]
gdi32.CreateSolidBrush.restype = ctypes.c_void_p
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
# 不设 argtypes 时 LPARAM/WPARAM 会被当 32 位 int 转 → 64 位值 OverflowError
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.ReleaseMutex.argtypes = [wt.HANDLE]
# ★ 窗Y 新增（Job Object / 进程创建时间 / 带等待的取消息）。
#   同样受本文件开头那条 64 位句柄规矩约束：不声明 restype 就把句柄截成 32 位。
kernel32.CreateJobObjectW.restype = wt.HANDLE
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
kernel32.SetInformationJobObject.restype = wt.BOOL
kernel32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int,
                                             ctypes.c_void_p, wt.DWORD]
kernel32.AssignProcessToJobObject.restype = wt.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
kernel32.IsProcessInJob.restype = wt.BOOL
kernel32.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE,
                                    ctypes.POINTER(wt.BOOL)]
kernel32.OpenJobObjectW.restype = wt.HANDLE
kernel32.OpenJobObjectW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
kernel32.QueryInformationJobObject.restype = wt.BOOL
kernel32.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wt.DWORD,
                                               ctypes.POINTER(wt.DWORD)]
kernel32.GetProcessTimes.restype = wt.BOOL
kernel32.GetProcessTimes.argtypes = [wt.HANDLE,
                                     ctypes.POINTER(wt.FILETIME),
                                     ctypes.POINTER(wt.FILETIME),
                                     ctypes.POINTER(wt.FILETIME),
                                     ctypes.POINTER(wt.FILETIME)]
kernel32.GetCurrentProcess.restype = wt.HANDLE
user32.MsgWaitForMultipleObjects.restype = wt.DWORD
user32.MsgWaitForMultipleObjects.argtypes = [wt.DWORD,
                                             ctypes.POINTER(wt.HANDLE),
                                             wt.BOOL, wt.DWORD, wt.DWORD]

# WndProc 是模块级函数，靠这个列表找到要销毁的窗口（--duration 到点退出用）
_ALL_HWNDS = []

# ★ W3-B：**只在真退出时**才允许 PostQuitMessage。
#   --follow 改色走「销毁 + 重建」这条路，销毁时 _ALL_HWNDS 会被清空 ⇒
#   WM_DESTROY 里那句「列表空了就 PostQuitMessage」当场让消息循环自杀，
#   表现是边框闪一下就没。这个标志是本单**动 _wnd_proc 的唯一理由**。
_SHUTTING_DOWN = False


def log_path():
    """边框日志落点。★ 只**追加**写诊断，绝不写任何闸门/急停文件。"""
    return os.environ.get("AGENTCTL_BORDER_LOG") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl",
        "ctrl_border.log")


def _stderr_is_log(path):
    """stderr 是不是已经指向 path 这个文件（被 spawn 重定向的那种情况）。"""
    try:
        a = os.fstat(sys.stderr.fileno())
        b = os.stat(path)
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    except Exception:
        return False


def _emit(text):
    r"""一行诊断 → **stderr + 日志文件**。

    ★ 为什么不去 stdout：mcp.py 的 stdout 是 JSON-RPC 协议专用，边框往里写一个字节
      就当场破坏协议。手动手跑时 stderr 就是终端，照样看得见；被 spawn 时 stderr
      已被重定向到日志文件，于是「无通行证 ⇒ 隐藏」「WDA 失败」这些**必须有据可查**
      的消息自动落到 %LOCALAPPDATA%\AgentCtl\ctrl_border.log。
    """
    line = "[border] " + str(text)
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    # ★ 被 spawn 时 stderr **已经被重定向进这个日志文件**了（start_follow 就是这么干的），
    #   再单独 append 一次就是同一句话写两遍 —— 日志翻倍、读的人以为出了两次事。
    #   所以先认一下"stderr 是不是就是这个文件"，是就只写一遍。
    if _stderr_is_log(p := log_path()):
        return
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "a", encoding="utf-8", errors="replace") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass                      # 日志写不了也不能拖垮边框本体


# ── 状态判定（W3-B）────────────────────────────────────
# 纯读：**只** os.path.exists + 读那 1 KB 的通行证 JSON。
#   不调 gate.status() —— 它每次都重新体检 legacy gate.py，0.5 s 一轮太浪费。
#   ⛔ 本模块**永远不写**通行证、不写急停 flag，只读。
def resolve_paths(pass_file=None, brake_flag=None):
    """定位通行证与急停 flag。**优先用 gate 模块的常量**（保证与闸门同一份真相源），
    gate 导不进来才回落环境变量口径并**如实打警告**，不静默。"""
    why = None
    if pass_file:
        pf = pass_file
    else:
        pf = None
        try:
            from agentctl import gate
            pf = gate.PASSFILE
        except Exception as e:
            pf = os.path.join(os.path.expanduser("~"), "_auth", "auth_pass.json")
            why = ("gate 模块导入失败（%s）⇒ 回落 HOME/_auth/auth_pass.json 口径"
                   "（可能与真闸门不是同一个文件，如实报告）" % e)
    bf = brake_flag or os.environ.get("AGENTCTL_BRAKE_FLAG")
    if not bf:
        try:
            from agentctl import gate
            bf = gate.BRAKE_FLAG
        except Exception:
            bf = os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "AgentCtl", "_急停.flag")
    return pf, bf, why


# ══════════ 档位口径单一源（2026-10-15 窗A P3）══════════════════════════════
# 事故原委：这里原来有个 _short_scope()，只取 scopes 项的**首字母**当档位。
#     'Debug' → 'D'  ⇒ 边框显示「D 级·拖拽」，而闸门一个档都不放行
#                    ⇒ **指示器骗人：说能拖拽，其实不能**。
#     'All' / 'ABC' / 'A1' / 'CD' 同理 —— 边框亮出一个闸门从没给过的档位色。
# 同一张证、同一份 scopes，两个模块各认各的 —— 这种分叉的方向永远是
# 「边框说没事、闸门说不行」，而**人只看边框**。所以它比它看上去严重。
#
# 设计原则（写死在这，不许再分叉）：**显示层只许比闸门更严，不许更松。**
#     边框算出的档位必须 ⊆ 闸门真放行的档位；算不出 / 对不上 ⇒ 红（fail-closed），绝不猜。
#
# 怎么做到不作弊：判档**直接用闸门自己那一行** ——
#     gate._check_locked:740 / gate.status:962 的
#     allowed = [short(x) for x in p["scopes"]]
# —— 同一函数（gate.short）、同一份档位表（gate.SCOPES）。边框自己不再有归一逻辑。
# 机械判据见 work/_2h_wA_档位口径判据.py（能跑红）。
def _gate_tier_calendar():
    """取闸门自己的档位表 ⇒ (short 函数, 档位元组 按低到高)。取不到 ⇒ (None, ())。"""
    # ⚠️ import 写在函数里，**不许提到模块顶层**：gate.py 在 import 时会无条件
    #   reconfigure sys.stdout/stderr 的编码（gate.py:38-42）。本模块会被
    #   server.py / mcp.py 进程内 import，而 mcp.py 的 stdout 是 JSON-RPC 协议专用，
    #   本文件顶部 :50-56 明确写了不许动它 ⇒ 顶层 import gate 会连坐。
    #   顺带把编码还原回去：万一宿主进程是第一次 import gate，别让它顺手改了宿主。
    saved = []
    for s in (sys.stdout, sys.stderr):
        try:
            saved.append((s, s.encoding, s.errors))
        except Exception:
            pass
    try:
        from agentctl import gate
        return gate.short, tuple(gate.SCOPES)
    except Exception:
        return None, ()
    finally:
        for s, enc, err in saved:
            try:
                if s.encoding != enc:
                    s.reconfigure(encoding=enc, errors=err)
            except Exception:
                pass


def _tiers_from_scopes(raw):
    """scopes ⇒ 边框该显示的档位。返回 (档位元组, 闸门认不出的项, 出错说明)。

    ⚠️ 为什么用 gate.short 而**不是** gate.parse_scopes：
        parse_scopes 是**用户手输**时的宽松解析（会把 'CD' 展开成 C、D）。
        但通行证里的 scopes 是**闸门自己的放行依据** —— _check_locked 对每项只做 short()，
        'CD' 归一不出来就是归一不出来 ⇒ 闸门全拒。
        边框要是也用 parse_scopes，就会对一张闸门全拒的证亮出 D 级 —— 正是本条要禁的方向。

    认不出的项**照闸门的样子当没给权限**，但要如实报进理由 ——
    一眼看出是谁写了张坏证，而不是让边框默默显示成别的意思。
    """
    short, tiers = _gate_tier_calendar()
    if short is None:
        return (), (), "取不到闸门的档位口径（gate 模块导入失败）⇒ 不另立一套，fail-closed 显示红"
    ok, junk = [], []
    for x in raw:
        try:
            s = short(x) if isinstance(x, str) else short(str(x))
        except Exception:
            junk.append(repr(x))
            continue
        if s in tiers:
            if s not in ok:
                ok.append(s)
        else:
            junk.append(str(s))
    # 档位高低序 = 闸门 SCOPES 的键序（唯一真源），不在这里另写一份 A/B/C/D
    return tuple(t for t in tiers if t in ok), tuple(junk), ""


def decide(pass_file, brake_flag, idle="hide", now=None):
    """当前该显示什么 ⇒ (状态, RGB 元组 或 None, 理由)。**纯只读，好判据。**

    优先级（从上到下，先命中先返回）：
        红   ← 急停 flag 存在
        红   ← 通行证文件在，但读失败 / JSON 解析失败（fail-closed）
        红   ← consumed = true（一次性证已用完）
        红   ← expires_at <= now（过期；缺 expires_at 按过期算，与 gate 同口径）
        四档蓝 ← scopes 里取**最高**档（A<B<C<D，逐档加深：A浅→D深）
        隐藏 ← 通行证文件不存在（还没授权）；--idle red 可翻成常红

    ★ 档位不是这里自己认的：**复用闸门那一套**（gate.short + gate.SCOPES，见 _tiers_from_scopes）。
      算不出 / 与闸门对不上 ⇒ 红（fail-closed）。
      规矩：显示层只许比闸门更严，不许更松 —— 边框亮出的档，闸门必须真放行。    """
    now = time.time() if now is None else float(now)
    if brake_flag and os.path.exists(brake_flag):
        return ST_RED, BRAKE_COLOR[0], "急停 flag 存在：%s" % brake_flag
    if not pass_file or not os.path.exists(pass_file):
        where = pass_file or "（路径未定位）"
        if idle == "red":
            return ST_RED, BRAKE_COLOR[0], "无通行证（%s）且 --idle=red" % where
        return ST_HIDDEN, None, "无通行证 ⇒ 隐藏（%s）" % where
    try:
        with open(pass_file, "r", encoding="utf-8") as f:
            p = json.load(f)
        if not isinstance(p, dict):
            raise ValueError("顶层不是对象，是 %s" % type(p).__name__)
    except Exception as e:
        return (ST_RED, BRAKE_COLOR[0],
                "通行证读失败/JSON 解析失败：%s（%s）⇒ fail-closed 显示红"
                % (e, pass_file))
    if p.get("consumed"):
        return ST_RED, BRAKE_COLOR[0], "一次性通行证已用完（consumed=true）"
    try:
        exp = float(p.get("expires_at", 0))
    except Exception:
        exp = 0.0
    # ★ 缺 expires_at 一律按过期算 —— 与 gate.py:737「time.time() > expires_at」同口径，
    #   不在这里另立一套更宽松的规则（两处口径分叉 = 边框说没事、闸门说不行）。
    if now > exp:
        return (ST_RED, BRAKE_COLOR[0],
                "通行证已过期（expires_at=%s ≤ now=%.0f，标注 %s）"
                % (p.get("expires_at"), now, p.get("expires")))
    raw = p.get("scopes") or ([p["scope"]] if p.get("scope") else [])
    have, junk, err = _tiers_from_scopes(raw)
    junk_note = ("；闸门认不出的项（按**没给权限**算，与闸门口径一致）：%s"
                 % "、".join(junk)) if junk else ""
    if err or not have:
        return (ST_RED, BRAKE_COLOR[0],
                "%s（scopes=%r）⇒ fail-closed 显示红%s"
                % (err or "通行证里没有闸门认得的档位", raw, junk_note))
    top = have[-1]
    if top not in TIERS:
        # 闸门认得、边框没配色 —— 同样当红，绝不瞎猜一个颜色顶上
        return (ST_RED, BRAKE_COLOR[0],
                "档位 %s 在闸门里有、边框 TIERS 没配色 ⇒ fail-closed 显示红" % top)
    return (ST_TIER, TIERS[top][0],
            "档位 %s（%s）—— 闸门放行 %s ⇒ 取最高档%s"
            % (top, TIERS[top][1], "、".join(have), junk_note))



def _acquire_singleton():
    """单例互斥量。返回 (句柄, 是我的)。防 server 与 mcp 同时起两组边框叠在一起。"""
    h = kernel32.CreateMutexW(None, True, FOLLOW_MUTEX)
    if not h:
        # 建不出互斥量就不拦（拦不住比误拦强），并如实说明，不静默。
        _emit("[X] CreateMutexW 失败（GetLastError=%d）⇒ 单例拦不住，"
              "server 与 mcp 可能各起一组边框叠在一起"
              % kernel32.GetLastError())
        return None, True
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(h)
        return None, False
    return h, True


def _release_singleton(h):
    if h:
        kernel32.ReleaseMutex(h)
        kernel32.CloseHandle(h)


class _JobBasicLimit(ctypes.Structure):
    """JOBOBJECT_BASIC_LIMIT_INFORMATION（winbase.h）。

    ★ 为什么自己定义：ctypes.wintypes 里没有；字段顺序与大小必须与 Windows
      结构体逐字节一致，否则 SetInformationJobObject 会写坏栈。
    """
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wt.DWORD),
                ("SchedulingClass", wt.DWORD)]


class _IOCounters(ctypes.Structure):
    """IO_COUNTERS（winbase.h）—— Job 扩展限额结构里的坑位，本单不用它，但要占位。"""
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class _JobExtendedLimit(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION（winbase.h）。"""
    _fields_ = [("BasicLimitInformation", _JobBasicLimit),
                ("IoInfo", _IOCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _filetime_to_unix(ct):
    """Win32 FILETIME（100 ns 自 1601-01-01 起）⇒ Unix 秒。"""
    return (float(ct) / 1e7) - 11644473600.0


def _ct_text(ct):
    """创建时间（FILETIME）⇒ 人能读的本地时间串，只为日志用。"""
    if not ct:
        return "(取不到)"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(_filetime_to_unix(ct)))
    except Exception:
        return "FILETIME=%d" % ct


def _process_create_time(pid):
    """pid 上那个进程的创建时间（FILETIME 整数）；取不到返回 0。

    ★ 这就是**身份**：pid 会回收再利用，创建时间不会。
      「同一个 pid、在同一时刻，只能是同一个创建时间」—— 对不上就是换了人。
    ★ 用 PROCESS_QUERY_LIMITED_INFORMATION：一样能问创建时间，权限要求低得多。
    """
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return 0
    # ★ 四个出参**都给真缓冲**，不许给 None。MSDN 说后三个可以传 NULL，
    #   但本机实测（Win11 26100）传 NULL 直接 access violation writing 0x0
    #   —— 判据 D1 的 traceback 就是这么炸的。别拿"文档说可以"去赌系统实现。
    ct, ex, kn, us = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
    try:
        ok = kernel32.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(ex),
                                      ctypes.byref(kn), ctypes.byref(us))
    finally:
        kernel32.CloseHandle(h)
    if not ok:
        return 0
    return (int(ct.dwHighDateTime) << 32) | int(ct.dwLowDateTime)


def _make_kill_on_close_job():
    """建一个「最后一个句柄一关，就把里面所有进程杀掉」的 Job。返回 (句柄, 错误码)。

    ★ 为什么这才是真保证：KILL_ON_JOB_CLOSE 是**内核**语义 —— 谁攥着 Job 句柄，
      谁死或谁关句柄，内核就把 Job 里剩下的进程全杀掉。父进程被 TerminateProcess
      崩掉那一刻 OS 直接收摊：不需要子进程还活着、不需要它在轮询、
      不需要它的代码走到哪一行。（对比：OpenProcess+WaitForSingleObject 是
      **用户态**轮询 —— 子进程只要卡住、认错人、或压根没走到那行，就留下孤儿。）
    ★ 句柄**不可继承**（CreateJobObject 第一参数传 None）⇒ 子进程拿不到它，
      Job 的寿命只跟父进程绑死。否则子进程自己攥着句柄，Job 永远不空，父死了也杀不掉。
    """
    h = kernel32.CreateJobObjectW(None, None)
    if not h:
        return None, kernel32.GetLastError()
    info = _JobExtendedLimit()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(h, JobObjectExtendedLimitInformation,
                                          ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        err = kernel32.GetLastError()
        kernel32.CloseHandle(h)
        return None, err
    return h, 0


def _assign_to_job(job, process_handle):
    """把一个进程塞进 Job。返回 (成功?, 错误码)。"""
    if not job or not process_handle:
        return False, 0
    if kernel32.AssignProcessToJobObject(job, process_handle):
        return True, 0
    return False, kernel32.GetLastError()


def _job_kill_on_close_state():
    """本进程是不是正被一个「关句柄即杀」的 Job 罩着。
    返回 (在 Job 里?, 带 KILL_ON_JOB_CLOSE?, 说明文字)。

    ★ 这只是**如实汇报**，不是判据：查不到 Job 也能跑（下面还有 pid 腿）。
      它的用处是让日志能回答「这一次到底是谁在兜底」—— 否则只能靠猜。
    """
    in_job = wt.BOOL()
    if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None,
                                   ctypes.byref(in_job)):
        return (False, False, "IsProcessInJob 失败（GetLastError=%d）"
                % kernel32.GetLastError())
    if not in_job.value:
        return False, False, "不在任何 Job 里（命令行直起/单跑，本就是正常情形）"
    j = kernel32.OpenJobObjectW(JOB_OBJECT_QUERY, False, None)
    if not j:
        return (True, False,
                "在 Job 里，但 OpenJobObject 查不到明细（GetLastError=%d）"
                % kernel32.GetLastError())
    try:
        info = _JobExtendedLimit()
        n = wt.DWORD()
        ok = kernel32.QueryInformationJobObject(
            j, JobObjectExtendedLimitInformation, ctypes.byref(info),
            ctypes.sizeof(info), ctypes.byref(n))
    finally:
        kernel32.CloseHandle(j)
    if not ok:
        return (True, False,
                "在 Job 里，但 QueryInformationJobObject 失败（GetLastError=%d）"
                % kernel32.GetLastError())
    flags = int(info.BasicLimitInformation.LimitFlags)
    return (True, bool(flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE),
            "LimitFlags=0x%X" % flags)


def _border_parent_pid():
    """拉起 --follow 时写进 --parent-pid 的那个 pid。

    ★ AGENTCTL_BORDER_PARENT_PID 是**仅测试**的口子（与 AGENTCTL_BORDER_MUTEX
      同一类）：判据要复现「父 pid 已被复用给别的活进程」这个终局，就得能指定一个
      **不是**我爹的 pid 进去。生产留空 ⇒ 就是 os.getpid()，行为一字不变。
    """
    v = (os.environ.get("AGENTCTL_BORDER_PARENT_PID") or "").strip()
    if v.isdigit() and int(v) > 0:
        return int(v)
    return os.getpid()


def _log_excepthook(etype, value, tb):
    """未捕获异常 => 落进日志文件。

    * 为什么需要它：--follow 被 spawn 时 stderr 是 DEVNULL（见 start_follow 里的
      注释：Windows 上继承的标准句柄不保留 append 语义，会把父进程写的日志行覆盖掉）。
      没有这道钩子，子进程崩了会**一个字都不留** —— 那就是不静默地失败。
    """
    import traceback
    try:
        _emit("[X] --follow 未捕获异常，进程要死了：\n%s"
              % "".join(traceback.format_exception(etype, value, tb)))
    except Exception:
        pass
    sys.__excepthook__(etype, value, tb)


# ── 接线（W3-B）：给 server.py / mcp.py 各调一行 ──────────────
# 逻辑本体全在本模块（怎么 spawn、怎么收、日志落点、单例判定、环境开关），
# 两个传输层各只调 start_follow()/stop_follow()，**不复制逻辑**。
_FOLLOW_PROC = None


# ★ 窗Y：Job 句柄由**父进程**攥着。它活着 ⇒ Job 不空 ⇒ 没人被杀；
#   它一死（进程没了 ⇒ 内核自动关掉它的全部句柄）⇒ Job 空 ⇒ 成员全被杀。
_FOLLOW_JOB = None


def _proc_handle(p):
    """Popen 的进程句柄（子进程刚起来、要往 Job 里塞的那一下用）。"""
    try:
        return int(p._handle)
    except Exception:
        return 0


def _close_job_holder():
    """放掉本进程攥着的 Job 句柄 ⇒ 内核立刻杀掉 Job 里的所有进程。

    ★ 这是 stop_follow 的**保险**：terminate/wait 都没收干净时，闭句柄是最后一道。
    ★ 这个句柄本来就该跟着本进程活到最后一刻，所以只在「要收摊 / 要换一个新的」
      时才闭 —— 闭早了会把自己的边框先杀掉。
    """
    global _FOLLOW_JOB
    j = _FOLLOW_JOB
    _FOLLOW_JOB = None
    if j:
        kernel32.CloseHandle(j)


def start_follow(reason=""):
    """拉起 --follow 子进程。返回 pid；没拉起来返回 0。**从不抛异常** ——
    边框是可见性提示，不该把服务本体带崩。"""
    # ★ 窗Y：_FOLLOW_JOB 也得 global —— 不然这里赋的是个函数局部变量，
    #   模块级那份永远不更新 ⇒ stop_follow 的 _close_job_holder() 闭不到它。
    global _FOLLOW_PROC, _FOLLOW_JOB
    try:
        if os.environ.get("AGENTCTL_BORDER") == "0":
            _emit("[跳过] AGENTCTL_BORDER=0 ⇒ 本次不拉边框（%s）" % (reason or ""))
            return 0
        if getattr(sys, "frozen", False):
            # ★ 冻结态（PyInstaller）：`sys.executable -m agentctl.ctrl_border` 不成立
            #   —— -m 要求真的能 import 到 agentctl 包，冻结产物里没有这个包路径。
            #   本单**不做** border 子命令（那是打包脚本的地盘，见放行 §4.2）
            #   ⇒ 如实报告 + 跳过接线，**不静默**。要支持得挂下一单。
            _emit("[X] 冻结态（PyInstaller）：`-m agentctl.ctrl_border` 不成立，"
                  "本单不做 border 子命令 ⇒ **跳过边框接线**。"
                  "如果你在装版上看不到边框，是接线未生效，不是你看漏。")
            return 0
        if _FOLLOW_PROC is not None and _FOLLOW_PROC.poll() is None:
            _emit("[跳过] 本进程已有 --follow 子进程 pid=%s ⇒ 不拉第二组"
                  % _FOLLOW_PROC.pid)
            return _FOLLOW_PROC.pid
        log = log_path()
        d = os.path.dirname(log)
        if d:
            os.makedirs(d, exist_ok=True)
        # 让子进程无论从哪儿被拉起都能 import 到 agentctl（免得靠 cwd 碰运气）
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env["PYTHONPATH"] = pkg_parent + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        # ★ 窗Y：pid 之外再报一份**创建时间**。pid 会被回收再利用，创建时间不会 ——
        #   子进程靠「pid + 创建时间」认亲，光看 pid 会认错人（2026-10-17 孤儿边框）。
        ppid = _border_parent_pid()
        cmd = [sys.executable, "-X", "utf8", "-m", "agentctl.ctrl_border",
               "--follow", "--parent-pid", str(ppid)]
        pct = _process_create_time(ppid)
        if pct:
            cmd += ["--parent-create-time", str(pct)]
        elif ppid:
            _emit("[X] 取不到父 pid=%s 的创建时间 ⇒ 子进程只能靠「自己的创建时间」"
                  "兜底认亲。如实报告，不静默。" % ppid)
        # ★ stdout/stderr **必须**重定向到文件：
        #   ① 没人读的 PIPE 写满会把子进程卡死；
        #   ② mcp.py 的 stdout 是 JSON-RPC 协议专用，边框写进去当场破坏协议。
        # ★★ stdout/stderr 给 DEVNULL，**不**重定向进日志文件。
        #   踩过的坑（实测复现）：Windows 上通过 STARTUPINFO.lpStdHandles 继承给
        #   子进程的标准句柄**不保留 append 语义** —— 子进程里的 write 会落在
        #   「继承时的文件偏移」(0)，把父进程先前 append 进去的那几行**直接覆盖掉**。
        #   open(log,"a") 给文件对象、os.open(O_APPEND) 传 fd，两种都试过，一样丢。
        #   ⇒ 父与子**不能共用一个日志句柄**。
        #   改法：子进程的 stdout/stderr 一律 DEVNULL，它自己写的每一行都由
        #   _emit() 用**本进程新开的 open(log,"a")** 追加 —— 一个时刻只有一个写者，
        #   append 语义就是对的。DEVNULL 同时保证 mcp.py 的 stdout 协议一个字节都不碰。
        #   （子进程万一崩了，traceback 由下面的 excepthook 补进日志，不会静默。）
        # ★ 窗Y 内核腿：先建一个「最后一个句柄一关就杀掉全部成员」的 Job，
        #   拉起后立刻把子进程 Assign 进去 ⇒ 父进程被 TerminateProcess 崩掉的
        #   那一刻，**OS 自己收摊**，不靠子进程轮询、不靠子进程代码走到哪一行。
        #   ★ close_fds=True 已保证子进程**继承不到**这个 Job 句柄 ⇒ Job 不会因为
        #     子进程还活着而永远不空（那等于白建：内核永远等不到「最后一个句柄」）。
        _close_job_holder()          # 先放掉上一个（正常路径上这里本来就是空的）
        job, jerr = _make_kill_on_close_job()
        if not job:
            _emit("[X] 建 KILL_ON_JOB_CLOSE 的 Job 失败（GetLastError=%d）⇒ "
                  "内核腿没了，只剩 pid+创建时间校验那条腿。如实报告，不静默。"
                  % jerr)
        else:
            # ★ 先攥进模块级那份再拉子进程：这样下面 Popen 一抛异常，
            #   except 里的 _close_job_holder() 才收得到它（否则每次起失败漏一个句柄）。
            _FOLLOW_JOB = job
        _FOLLOW_PROC = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, env=env, close_fds=True,
            cwd=pkg_parent, creationflags=CREATE_NO_WINDOW)
        if job:
            ok, aerr = _assign_to_job(job, _proc_handle(_FOLLOW_PROC))
            if ok:
                _emit("[内核腿] 边框 pid=%s 已入 KILL_ON_JOB_CLOSE 的 Job ⇒ "
                      "父进程一死，OS 直接收掉它（不靠轮询）" % _FOLLOW_PROC.pid)
            else:
                # 关掉这个**空** Job（子进程没进去）不影响它，只是不留句柄。
                _close_job_holder()
                _emit("[X] AssignProcessToJobObject 失败（GetLastError=%d）⇒ "
                      "内核腿没接上，只剩 pid+创建时间校验那条腿。"
                      "如实报告，不静默。" % aerr)
        _emit("边框已启动 pid=%s（%s，父 pid=%s 创建时间=%s）→ 日志 %s"
              % (_FOLLOW_PROC.pid, reason or "启动", ppid, _ct_text(pct), log))
        return _FOLLOW_PROC.pid
    except Exception as e:
        _close_job_holder()          # ★ 别把刚建的 Job 句柄漏在服务里
        _emit("[X] 拉起 --follow 失败：%r ⇒ 这次没有边框。如实报告，不静默。" % (e,))
        return 0


def stop_follow(timeout=5.0):
    """收掉本进程拉起的边框子进程。belt：子进程自己也有 --parent-pid 监护，
    父进程暴毙时它会自杀；这里是正常路径上的干净收摊。"""
    global _FOLLOW_PROC
    p = _FOLLOW_PROC
    _FOLLOW_PROC = None
    if p is None:
        _close_job_holder()          # 没有子进程可等，但 Job 句柄也别攥着
        return 0
    try:
        if p.poll() is not None:
            _close_job_holder()
            return 0
        p.terminate()
        p.wait(timeout=timeout)
        _emit("边框已收掉 pid=%s" % p.pid)
    except Exception as e:
        try:
            p.kill()
        except Exception:
            pass
        _emit("[X] 收边框失败：%r（已尝试 kill）" % (e,))
    finally:
        # ★ 窗Y：无论上面收没收干净，最后都放掉 Job 句柄 ——
        #   闭句柄 = 内核把 Job 里所有成员杀掉 = 最后一道保险（孤儿边框的兜底）。
        _close_job_holder()
    return 0


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
        _mark_shutdown(True)
        try:
            for h in pend:
                user32.DestroyWindow(h)
        finally:
            _mark_shutdown(False)
        user32.PostQuitMessage(0)
        return 0
    if msg == WM_DESTROY:
        # ★ W3-B：_SHUTTING_DOWN 为真时**不许** PostQuitMessage ——
        #   --follow 改色是「销毁 + 重建」，不是退出。
        if not _ALL_HWNDS and not _SHUTTING_DOWN:
            user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _mark_shutdown(on):
    """给 _wnd_proc 用的置位小跑腿（放在 def _wnd_proc 之后是因为它只在运行期用）。"""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = bool(on)


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


def unregister_border_classes():
    """注销两个边框窗口类（主色 + 1px 描边）。

    ★ 为什么要**两个**类：hbrBackground 是**类级**一份，一个类刷不了两种颜色。
      窗F 加了 1px 反色描边（两种颜色），于是描边那 4 个窗口另开一个类。
    ★ 为什么必须清：hInstance=NULL 注册的类**不随进程退出注销**，
      留着就是 lpfnWndProc 指向一个已经没了的进程的野指针
      （下一个进程拿到 1410 若沿用它 ⇒ 实测卡住、窗口一个都建不出来）。
    注销失败（说明有活着的属主还在用）是**正常**的，不报错、不静默假装成功。
    """
    for name in (WNDCLASS_NAME, WNDCLASS_EDGE_NAME):
        try:
            user32.UnregisterClassW(name, None)
        except Exception:
            pass


class _BorderApp:
    def __init__(self, color, thickness, no_wda, duration,
                 idle="hide", pass_file=None, brake_flag=None,
                 poll=0.5, parent_pid=0, parent_create_time=0):
        # ★ 窗F：thickness 允许是 None（= 按档取厚度）。显式给了就一直是它，
        #   改色（--follow 跟证换档）时也不许被按档厚度顶掉 —— 显式压得住一切。
        self.thickness_override = None if thickness is None else int(thickness)
        self.color = color
        self.thickness, self.edge_color = resolve_style(color,
                                                         self.thickness_override)
        self.no_wda = no_wda
        self.duration = duration
        # ── W3-B 新增（默认值全是「老用法不变」）──
        self.idle = idle
        self.pass_file = pass_file
        self.brake_flag = brake_flag
        self.poll = max(0.05, float(poll or 0.5))
        self.parent_pid = int(parent_pid or 0)
        # ★ 窗Y：拉起我的人报上来的自己的创建时间（FILETIME）。开口认亲时对不上
        #   ⇒ 这个 pid 上已经换成别人了（pid 被回收再利用）⇒ 收摊。
        self.parent_create_time = int(parent_create_time or 0)
        self._parent_dead = False          # 判定了「父没了/换了人/管不了」⇒ 收摊
        self._parent_open_deadline = 0.0   # 开不到父句柄时的宽限截止（monotonic）
        self._parent_open_warn_at = 0.0    # 上次播报「监护没生效」的时刻
        self.visible = None            # 当前边框显示与否（None=还没建）
        # 老用法（run）走 print，输出照旧打在终端；--follow 走 _emit（stderr+日志）。
        self.sink = print
        self._brush = None
        self._brush_edge = None
        self.n_body = 0              # 本次建了几个主色窗口（--follow 的日志要如实报）
        self.n_edge = 0              # 本次建了几个 1px 描边窗口
        self._parent_h = None
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
        self._brush = gdi32.CreateSolidBrush(_rgb_bgr(self.color))
        self._brush_edge = gdi32.CreateSolidBrush(_rgb_bgr(self.edge_color))
        # ★★ 先把可能存在的**上一个进程留下的僵尸窗口类**清掉再注册。
        #   坑（本机实测踩到）：hInstance=NULL 注册的窗口类**不随进程退出而注销**，
        #   而类里存着 lpfnWndProc —— 那是**上个进程**的地址。进程一死它就成了野指针。
        #   下一个进程 RegisterClassW 会拿到 1410（类已存在）⇒ 若就"沿用"，
        #   CreateWindowExW 会拿着野指针当窗口过程 ⇒ 实测**卡住、四个窗口一个都建不出来**
        #   （进程还活着、占着单例互斥量、屏幕上却一个边框都没有）。
        #   所以这里先 UnregisterClassW：能清掉就清掉（后面 RegisterClassW 必成功、
        #   lpfnWndProc 指向自己）；清不掉说明**有活着的属主**用它 ⇒ 1410 时沿用是**对的**。
        #   ★ 窗F：现在有**两个**类（主色 + 1px 描边），两个都要走这条路。
        unregister_border_classes()
        self._register_class(WNDCLASS_NAME, self._brush)
        self._register_class(WNDCLASS_EDGE_NAME, self._brush_edge)

    def _register_class(self, name, brush):
        """注册一个窗口类。hbrBackground 是**类级**一份 —— 一个类刷不了两种颜色。"""
        wc = WNDCLASSW()
        wc.lpszClassName = name
        wc.lpfnWndProc = self._wpc
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.hbrBackground = brush
        wc.hCursor = user32.LoadCursorW(None, 32512)     # IDC_ARROW
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = kernel32.GetLastError()
            if err != 1410:                                # ERROR_CLASS_ALREADY_EXISTS
                raise OSError("RegisterClassW(%s) 失败：%d" % (name, err))

    def _create(self, x, y, w, h, tag, edge=False):
        cls = WNDCLASS_EDGE_NAME if edge else WNDCLASS_NAME
        title = "%s-%s%s" % (WNDCLASS_NAME, tag, "-edge" if edge else "")
        exstyle = (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE |
                   WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        hwnd = user32.CreateWindowExW(
            exstyle, cls, title,
            WS_POPUP | WS_VISIBLE,
            x, y, w, h, None, None, None, None)
        if not hwnd:
            raise OSError("CreateWindowExW 失败（%s%s）"
                          % (tag, "-edge" if edge else ""))
        # 坑 3：从屏幕捕获里排除（失败=如实报，不静默）
        if not self.no_wda:
            ok = user32.SetWindowDisplayAffinity(wt.HWND(hwnd),
                                                 WDA_EXCLUDEFROMCAPTURE)
            if not ok:
                self.sink("[X] SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) 失败"
                      "（hwnd=%s，GetLastError=%d）——本机/此窗口不支持，"
                      "边框**会**进入截图！平台 perceive/ctl_shot 的画面判定会被污染。"
                      "替代方案：只在无截图动作的间隙显示，或改用任务栏/标题栏指示。"
                      % (hwnd, kernel32.GetLastError()), flush=True)
        return hwnd

    # -- 窗口批量建/拆（W3-B：改色走这里）---------------------
    def _create_all(self):
        """按当前颜色把每块屏幕的四条边全建出来。返回建了几个窗口。

        ★ 窗F：每边现在建**两个**窗口 —— 主色 (t-1) px + 1px 反色描边，
          矩形由 _edge_geometry() 算（纯函数，冻结判据直接调它验几何）。
          **总厚仍是 t**：两块不重叠，各占一段。
        """
        user32.SetProcessDPIAware()            # 原始像素，防 DPI 缩放错位
        self._register()
        n = 0
        self.n_body = self.n_edge = 0
        for (x1, y1, x2, y2) in self.monitors():
            for tag, gx, gy, gw, gh, kind in _edge_geometry(x1, y1, x2, y2,
                                                           self.thickness):
                if gw <= 0 or gh <= 0:
                    # 屏太小/厚度太大时可能算出空矩形：跳过，别建 0 px 的窗口。
                    continue
                hw = self._create(gx, gy, gw, gh, tag, edge=(kind == "edge"))
                self.hwnds.append(hw)
                _ALL_HWNDS.append(hw)
                n += 1
                if kind == "edge":
                    self.n_edge += 1
                else:
                    self.n_body += 1
        return n

    def _destroy_all(self):
        """拆掉全部边框窗。★ 必须先置 _SHUTTING_DOWN —— 否则 WM_DESTROY 里那句
        「_ALL_HWNDS 空了就 PostQuitMessage」会让消息循环当场自杀。"""
        pend = list(_ALL_HWNDS)
        _ALL_HWNDS.clear()
        self.hwnds = []
        _mark_shutdown(True)
        try:
            for h in pend:
                user32.DestroyWindow(h)
        finally:
            _mark_shutdown(False)

    def _show(self, on):
        """显示/隐藏。★ 空闲用 SW_HIDE **不销毁** —— 定时器与单例需要一个宿主窗口在。"""
        for h in self.hwnds:
            user32.ShowWindow(wt.HWND(h), SW_SHOW if on else SW_HIDE)
        self.visible = bool(on)

    def recolor(self, color):
        """改色 = **销毁 + 重新注册 + 重建**，不去改窗口类的背景刷。

        理由：改类刷要碰 SetClassLongPtrW 的 64 位 restype 陷阱（这个文件开头已经
        因为同类问题踩过一次：restype 没声明导致 RegisterClassW 报 87）。销毁重建走
        的是**已经验证过**的 CreateWindowExW 路径。且颜色只在「档位变了/过期了/急停了」
        时才变，不是每 0.5 s ⇒ 8×N 个窗口重建的代价可以忽略。

        ★ 窗F：**厚度也跟档位走**了，所以判据不只是"颜色变了没" ——
          颜色相同但档位厚度不同（理论上不会发生：颜色↔档位一一对应）也要重建，
          否则屏幕上留着的厚度与颜色对不上。显式 --thickness 仍然全程压住。
        """
        want_t, want_edge = resolve_style(color, self.thickness_override)
        if tuple(color) == tuple(self.color) \
                and want_t == self.thickness \
                and tuple(want_edge) == tuple(self.edge_color):
            return False
        was = bool(self.visible)
        self._destroy_all()
        old_brushes = (self._brush, self._brush_edge)
        self.color = color
        self.thickness, self.edge_color = want_t, want_edge
        unregister_border_classes()
        # ★ 2026-10-15 修（GLM(ZCode) 复检 刺1 · 真泄漏）：
        #   这里**不能**再 _register() —— _create_all() 内部（:808）本来就会 _register()，
        #   而 _register() 既建刷**又**注册窗口类。原来的顺序是
        #     :869 建一对新刷 A → 删旧刷 → :808 再建一对新刷 B 覆盖 self._brush/_brush_edge
        #   ⇒ **刷 A 无人 DeleteObject**，每换一次档就泄漏 2 把 GDI 刷（句柄进程上限 10000，
        #   常驻 --follow 频繁换档才可见，属真 bug 但轻微）。
        #   现在只在这里清类，新刷交给 _create_all→_register() 建（带新颜色），旧刷由下面循环删净。
        for ob in old_brushes:
            if ob:
                gdi32.DeleteObject(ctypes.c_void_p(ob))
        self._create_all()
        self.visible = False
        if was:
            self._show(True)
        return True

    def run(self):
        n = self._create_all()
        print("[OK] 已画边框：显示器 %d 块 × 每屏 4 条 = %d 个窗口"
              "（主色 %d + 1px 反色描边 %d），厚度 %d px%s"
              % (len(self.monitors()), n, self.n_body, self.n_edge,
                 self.thickness,
                 ("（描边占最内一圈，总厚不变；描边色 RGB=%s）" % (self.edge_color,))
                 if self.n_edge else
                 # 厚度只有 1px 时主色占满，描边无处安放 —— 如实说，别假装有描边
                 "（厚度只有 1px，无处安放 1px 描边 ⇒ 本次只画主色）"),
              flush=True)
        if self.duration and self.duration > 0:
            # 只在一个窗口上设定时器即可（同线程消息队列共享；到点销毁全部）
            user32.SetTimer(self.hwnds[0], 1, int(self.duration * 1000), None)
            print("    --duration=%gs 到点自动退出" % self.duration, flush=True)

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        # ★ 2026-10-15 修（GLM(ZCode) 复检 刺3 · 卫生）：退出前**注销窗口类**。
        #   不注销会留"僵尸类"（类里的 lpfnWndProc 指向本进程地址，进程一死就是野指针；
        #   下个进程 RegisterClassW 会拿到 1410）。有自愈兜底（_register() 开头先 UnregisterClassW），
        #   所以非紧急；但既然已抽成函数，这里补一行就闭环。
        unregister_border_classes()
        return 0

    # -- --follow：盯档位（W3-B）------------------------------
    def _parent_gone(self):
        """父进程没了（或已经不是父了）？⇒ 自己收摊。返回 True = 该收摊。

        为什么非做不可：父进程被 kill/崩溃后子进程会被 reparent 成孤儿，
        **一条橙/红边框就永久留在用户屏幕上挡视野** —— 比不做还坏。

        ★ 窗Y（2026-10-17 孤儿边框事故）之后这里有四道：
          ① 内核 Job（start_follow 建的 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE）：
             父一死 OS 直接杀本进程，**根本轮不到这里** —— 不靠轮询猜测。
          ② 本函数盯的是**句柄**。句柄只认人、不认 pid ⇒ pid 被回收再利用对它无效。
             （原来盯的其实也已经是句柄；问题是句柄是「开口那一刻」才抓的，
               抓晚了就认错了人 —— 那才是 2026-10-17 那条孤儿的真死法。）
          ③ 开口那一刻核对**创建时间**：pid 上的人换了 ⇒ 收摊并留日志，不静默。
          ④ 开不到句柄（父已彻底消失 / 权限不足）⇒ **不许无限开下去**：
             宽限期一过就认「管不了」⇒ 收摊。
             ⛔ 原实现永远返回 False（fail-open）—— 那正是孤儿边框的老路。实测
               （work\\_孤儿取证_改前_输出.txt）：父彻底消失后 6 s，子进程仍活着、
               屏幕上 8 个边框窗、日志刷了 12 条告警。
        """
        if not self.parent_pid:
            return False
        if self._parent_dead:
            return True
        if self._parent_h is None:
            h = kernel32.OpenProcess(SYNCHRONIZE, False, int(self.parent_pid))
            if not h:
                return self._parent_open_failed(kernel32.GetLastError())
            self._parent_h = h
            self._parent_open_deadline = 0.0
            self._parent_open_warn_at = 0.0
            if self._verify_parent_identity():
                self._parent_dead = True
                return True
        return kernel32.WaitForSingleObject(self._parent_h, 0) == WAIT_OBJECT_0

    def _parent_open_failed(self, err):
        """开口（OpenProcess 父句柄）失败怎么判。返回「该不该收摊」。

        ★ 两种错、两种判法：
          · 87（ERROR_INVALID_PARAMETER）＝ 这个 pid 在进程表里**根本不存在**
            ⇒ 父已经彻底没了（典型场景：父在我们开口之前就死了）⇒ 立刻收摊，不等。
          · 其它错（拒绝访问等）＝ 现在还说不准 ⇒ 给 PARENT_OPEN_GRACE 秒宽限，
            期间照常播报告警；宽限一过还开不到 ⇒ 判「管不了」⇒ 收摊。
            ⛔ 原来的行为是**永远等下去**（每 0.5 s 重试、永不放弃）⇒ 孤儿边框。
        """
        now = time.monotonic()
        if not self._parent_open_deadline:
            self._parent_open_deadline = now + PARENT_OPEN_GRACE
        if err == ERROR_INVALID_PARAMETER:
            self._parent_dead = True
            _emit("[--follow] 父 pid=%s 在进程表里不存在（OpenProcess 报 %d）"
                  " ⇒ 父已彻底退出，自己收摊（防孤儿边框永久挡屏）"
                  % (self.parent_pid, err))
            return True
        if now >= self._parent_open_deadline:
            self._parent_dead = True
            _emit("[--follow] 连续 %.1fs 开不到父 pid=%s 的句柄（GetLastError=%d）"
                  " ⇒ 判定「父已经管不了」，自己收摊：没人监护的边框留在屏幕上"
                  "就是孤儿边框。如实报告，不静默。"
                  % (PARENT_OPEN_GRACE, self.parent_pid, err))
            return True
        # 宽限期内：照播那条老告警（**文案一字未改**），只是不再每个轮询刷一遍 ——
        # 原实现 0.5 s 一条，实测 6 s 就刷了 12 条，把真出事的那行埋掉了。
        if not self._parent_open_warn_at or \
                now - self._parent_open_warn_at >= PARENT_OPEN_WARN_EVERY:
            self._parent_open_warn_at = now
            _emit("[X] OpenProcess(父 pid=%s) 失败 ⇒ **父进程监护没生效**，"
                  "父进程若被杀会留下孤儿边框。如实报告，不静默。"
                  % self.parent_pid)
        return False

    def _verify_parent_identity(self):
        """句柄抓到了 ⇒ 再认一次亲：**这个 pid 上的进程到底是不是我爹**。
        返回 True = 换了人（该收摊）。

        两条独立证据（都来自内核，不是猜的）：
          ① 拉起我的人报上来的 --parent-create-time。对不上 ⇒ pid 已被复用。
          ② 对比**我自己**的创建时间：我爹生我之前就在了。若这个 pid 上的进程
             比我还晚 ⇒ 它不可能是我爹 ⇒ 一定是复用了别人的 pid。
        ★ 为什么只在开口那一刻判一次、后续轮询不再判：
          句柄只认人 —— pid 回收对**已经握住**的句柄完全无效。判一次之后这个
          句柄就锁死是我爹，再判一万次也不会多出任何保证，只白花 GetProcessTimes。
        """
        ct = _process_create_time(self.parent_pid)
        mine = _process_create_time(os.getpid())
        if self.parent_create_time and ct and ct != self.parent_create_time:
            _emit("[--follow] ★ 父进程已换人：pid=%s 上的进程创建于 %s，"
                  "而拉起我的人报上来的是 %s ⇒ 这个 pid 已被系统复用给别人了"
                  " ⇒ 自己收摊。如实报告，不静默。"
                  % (self.parent_pid, _ct_text(ct),
                     _ct_text(self.parent_create_time)))
            return True
        if mine and ct and ct > mine:
            _emit("[--follow] ★ 父进程已换人：pid=%s 上的进程创建于 %s，"
                  "**比我（%s）还晚** ⇒ 它不可能是我爹（我爹生我之前就在了）"
                  " ⇒ 这个 pid 已被复用 ⇒ 自己收摊。如实报告，不静默。"
                  % (self.parent_pid, _ct_text(ct), _ct_text(mine)))
            return True
        return False

    def _pump(self, seconds):
        """边转消息边等。★ 不用 SetTimer：GetMessage 会阻塞，而 --follow 要的是
        「定期看一眼文件」。PeekMessage 非阻塞最省事，也彻底避开 WM_TIMER 与
        WM_DESTROY 的纠缠（--follow 全程不设定时器）。"""
        end = time.monotonic() + seconds
        msg = wt.MSG()
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            # ★ 窗Y：**手里有父句柄就把等待交给内核**（MsgWaitForMultipleObjects）：
            #   「有窗口消息」或「父进程没了」任一发生就醒。原实现只 PeekMessage +
            #   sleep(0.05)，父死了最多要等一整个 poll（默认 0.5 s）才被发现 ——
            #   那半秒里子进程还在建/拆窗口。内核等待把这块盲区砍成 0。
            #   万一这条 API 在别的机器上抽风（WAIT_FAILED）⇒ 落回原来那条老路，
            #   绝不因为一条优化把整个 --follow 搞死。
            handles = (wt.HANDLE * 1)(self._parent_h) if self._parent_h else None
            if handles is not None:
                r = user32.MsgWaitForMultipleObjects(1, handles, False,
                                                     int(left * 1000), QS_ALLINPUT)
                if r == WAIT_OBJECT_0:      # 父进程没了 ⇒ 立刻回去让调用者收摊
                    return
                if r == WAIT_FAILED:
                    handles = None          # 落回老路（见上面的说明）
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_QUIT:
                    raise SystemExit(0)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            if handles is None:
                time.sleep(min(0.05, max(0.0, left)))

    def run_follow(self):
        # ★ --follow 的每一条诊断都必须落进日志（stderr 在被 spawn 时是 DEVNULL），
        #   所以这里把 sink 换成 _emit，并挂上 excepthook —— 崩了也要留证据。
        self.sink = _emit
        sys.excepthook = _log_excepthook
        pf, bf, warn = resolve_paths(self.pass_file, self.brake_flag)
        if warn:
            _emit("[X] " + warn)
        mutex, mine = _acquire_singleton()
        if not mine:
            _emit("[OK] 已有一个 --follow 在跑（单例互斥量 %s）⇒ 本次不画边框，退出"
                  % FOLLOW_MUTEX)
            return 0
        _emit("[--follow] 通行证=%s" % pf)
        _emit("[--follow] 急停flag=%s" % bf)
        _emit("[--follow] 轮询 %.2fs，父进程监护 pid=%s，idle=%s，厚度 %s px"
              "（不给 --thickness 时按档：A3/B4/C5/D6，红6）"
              % (self.poll, self.parent_pid or "无", self.idle,
                 ("显式 %d" % self.thickness) if self.thickness_override is not None
                 else "按档 %d" % self.thickness))
        # ★ 窗Y：把「这一次到底是谁在兜底」写进日志。以前这条只写 pid，事后没人
        #   说得清孤儿是怎么活下来的 —— 而孤儿边框的事故复盘全靠日志。
        if self.parent_pid:
            in_job, armed, why = _job_kill_on_close_state()
            _emit("[--follow] 父进程监护：内核腿=%s；pid 腿=pid=%s + 创建时间=%s"
                  "（句柄只认人不认 pid；pid 被复用由创建时间戳穿）"
                  % ("已入 KILL_ON_JOB_CLOSE Job（父一死 OS 直接收摊本进程）"
                     if armed else ("在 Job 里但没带 KILL_ON_JOB_CLOSE（%s）" % why
                                    if in_job else why),
                     self.parent_pid, _ct_text(self.parent_create_time)))
        last = None
        try:
            # ★ 必须**在 try 里面**：原来放在 try 外面，建窗一失败异常就直接飞出去，
            #   底下的 finally（销毁窗口 + 释放单例互斥量）压根跑不到
            #   ⇒ 互斥量被一个**没有边框的进程**长期占着，之后每个 --follow 都秒退
            #   「已有一个在跑」，现场就是"服务在跑但屏幕上什么边框都没有"。
            # ★ 窗Y：**先认亲，再画窗**。原来第一次 _parent_gone() 在建完 8 个
            #   窗口、进了轮询之后才发生 —— 子进程冷启动那一小段（满载时几百毫秒）
            #   里父死了、pid 又被系统复用，就认错了人，然后永远盯着别人活。
            #   现在认错了 ⇒ **一张窗都不画**。
            if self.parent_pid and self._parent_gone():
                _emit("[--follow] 父进程（或它占用的 pid）已经没了 "
                      "⇒ 还没画窗口就收摊")
                return 0
            n = self._create_all()
            _emit("[--follow] 已建 %d 个边框窗口（主色 %d + 1px 反色描边 %d；"
                  "初始隐藏，等判定）" % (n, self.n_body, self.n_edge))
            while True:
                if self._parent_gone():
                    _emit("[--follow] 父进程已退出 ⇒ 自己收摊（防孤儿边框永久挡屏）")
                    break
                st, color, why = decide(pf, bf, self.idle)
                sig = (st, tuple(color) if color else None)
                changed = sig != last
                if st == ST_HIDDEN:
                    # ★ DSH 追加的硬要求：隐藏态**必须**往日志写一行 ——
                    #   否则「看不见边框」会被误判成「急停了」。
                    if changed:
                        _emit("[--follow] 无通行证 ⇒ 隐藏（%s）" % why)
                    if self.visible:
                        self._show(False)
                else:
                    if changed and last is not None and last[0] == ST_HIDDEN:
                        _emit("[--follow] 通行证出现 ⇒ 显示边框")
                    if self.recolor(color):
                        _emit("[--follow] 改色 → RGB=%s 厚度=%dpx 描边=%s（销毁重建）"
                              % (color, self.thickness, self.edge_color))
                    if not self.visible:
                        self._show(True)
                    if changed:
                        _emit("[--follow] 状态=%s 颜色 RGB=%s  理由：%s"
                              % (st, color, why))
                last = sig
                self._pump(self.poll)
        finally:
            self._destroy_all()
            # ★ 主动注销窗口类：hInstance=NULL 的类**不随进程退出注销**，
            #   留着就是个 lpfnWndProc 指向本进程（马上就没了）的僵尸类。
            #   不注销的话，**下一个** --follow 会拿到 1410 并沿用它 ⇒ 建不出窗口。
            unregister_border_classes()
            _release_singleton(mutex)
            _emit("[--follow] 已退出")
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
    # ★ 窗F：不给 = **按档取厚度**（A3/B4/C5/D6，红 6）；显式给了就全程用它
    #   （老用法 --thickness 12 一字未变，仍然压得住）。
    ap.add_argument("--thickness", type=int, default=None,
                    help="边框总厚 px（默认不给 = 按档：A3/B4/C5/D6，红 6；"
                        "显式给了就所有档都用这个值）")
    ap.add_argument("--no-wda", action="store_true",
                    help="关闭「不进截图」（仅调试/截图留证用）")
    ap.add_argument("--list-monitors", action="store_true", help="只列出显示器")
    # ── W3-B 新增（老参数行为一字未动，下面这些默认值全是「不改变旧行为」）──
    _idle = (os.environ.get("AGENTCTL_BORDER_IDLE") or "hide").strip().lower()
    if _idle not in ("hide", "red"):
        _emit("[X] AGENTCTL_BORDER_IDLE=%r 认不得 ⇒ 回落到 hide" % _idle)
        _idle = "hide"
    ap.add_argument("--follow", action="store_true",
                    help="盯通行证档位＋急停 flag，颜色自动跟着变（T1/W3-B）")
    ap.add_argument("--once", action="store_true",
                    help="打印一次当前判定就退出，不画窗口（自证/排查用）")
    ap.add_argument("--poll", type=float,
                    default=float(os.environ.get("AGENTCTL_BORDER_POLL") or 0.5),
                    help="--follow 轮询间隔秒（默认 0.5；环境变量 AGENTCTL_BORDER_POLL）")
    ap.add_argument("--pass-file", default=None,
                    help="覆盖通行证路径（**仅测试**；生产留空＝用 gate.PASSFILE）")
    ap.add_argument("--brake-flag", default=None,
                    help="覆盖急停 flag 路径（**仅测试**；生产留空＝gate.BRAKE_FLAG）")
    ap.add_argument("--idle", choices=("hide", "red"), default=_idle,
                    help="没有通行证时：hide 隐藏（默认）/ red 常红")
    ap.add_argument("--parent-pid", type=int, default=0,
                    help="父进程 pid，没了就自杀（防孤儿边框永久挡屏）")
    ap.add_argument("--parent-create-time", type=int, default=0,
                    help="父进程创建时间（Win32 FILETIME 整数）。start_follow 会自动"
                         "带上；手工拉起不给也能跑。与 --parent-pid 对不上 ⇒ 这个 "
                         "pid 已被系统复用给别人 ⇒ 自行收摊"
                         "（★ 2026-10-17 孤儿边框修复）")
    a = ap.parse_args(argv)

    if a.list_monitors:
        list_monitors()
        return 0
    if a.once:
        pf, bf, warn = resolve_paths(a.pass_file, a.brake_flag)
        if warn:
            _emit("[X] " + warn)
        st, color, why = decide(pf, bf, a.idle)
        _emit("通行证=%s  急停flag=%s" % (pf, bf))
        line = "判定：状态=%s 颜色=%s 理由：%s" % (
            st, ("RGB=%s" % (color,)) if color else "隐藏（不画窗口）", why)
        print(line)
        _emit(line)
        return 0
    if a.follow:
        app = _BorderApp(TIERS["A"][0], a.thickness, a.no_wda, 0,
                         idle=a.idle, pass_file=a.pass_file,
                         brake_flag=a.brake_flag, poll=a.poll,
                         parent_pid=a.parent_pid,
                         parent_create_time=a.parent_create_time)
        return app.run_follow()
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
