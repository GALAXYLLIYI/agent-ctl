# -*- coding: utf-8 -*-
r"""P0 自指保护判据（窗K · 2026-10-16；窗O 收尾；窗O2 真机残留检测；窗O3 去飘）

================================ 它在守什么 ================================
legacy_tools/act.py 的 check_target 是这套系统**唯一的防自授权闸门**。
改动前它只认一个标题字符串（BLOCKED_TITLES），改一个字、换个语言、
或者干脆躲进终端里手打命令，这道闸门就漏。本判据钉死升级后的四件事：

    ① 终端类窗口（真起一个 cmd.exe，标题故意写成无害的）⇒ 必须被拦
       —— 而且要说明是**按身份**拦的（属主进程名 / 窗口类名），不是碰巧标题对上了；
    ② 授权弹窗标题（**只用标题**，属主进程与类名都与授权弹窗无关）⇒ 必须被拦
       —— 这是兜底那层单独成立；
    ③ 普通应用（记事本）⇒ 必须放行 —— 别为了防自授权把正常活也挡了；
    ④ 终端的**临时放行**：用户确认过 ⇒ 同一窗口放行，且**事件里有记录**；
       30 分钟过期后 ⇒ 重新被拦（不许变成永久白名单）。

★ 断言 4 的「过期」不真等 30 分钟（判据不该跑半小时）：把记录里的到期时间
  改到 31 分钟前，等价于时间过去了 30 分钟，然后对**同一个还开着的终端窗口**
  再问一次 check_target。其余每一步都是真的：真进程、真 HWND、真 check_target。

================================ 窗O2：残留检测曾经是瞎的 ====================
DSH 亲跑出 rc=1（绿32/红2）：
    [红] 0s-a 埋进去的残留**被检出**（扫到 0 个窗）
    [红] 0s-c 埋的进程真的退出了（pid 34284）  ← 只是把窗藏了，进程还活着

根因不是「类名对不上」这么简单。自己实测（证据见 work\_2h_wO2\根因实测_20261016.md）：

    ① **判据一直验的是一块假桌面。**
       agent 进程（我、DSH）启动在私有沙箱桌面 CodexSandboxDesktop-… 上，
       而子进程继承的是**进程**的桌面 —— 夹具全落在那块私有桌面上，
       用户真实的 Default 桌面上一个夹具窗都没有。
       SetThreadDesktop 只改线程，改不动子进程的落点。
    ② 控制台窗由 Windows Terminal 托管（CASCADIA_HOSTING_WINDOW_CLASS）
       或 ConPTY（PseudoConsoleWindow，**标题恒为空**）——
       按「类名 + 标题键」认窗，两条路都认不出来。

⇒ 本版定死四条：
    · **桌面锚定**：切到 Default，并且夹具一律用 STARTUPINFO.lpDesktop 指名起在
      Default 上，作用域（桌面名）开跑就打在抬头上，不许含糊；
    · **认窗不靠类名**：三种宿主类全认，再叠「标题键 + 属主 pid/进程族 +
      本轮命令行标记」；
    · **三态**：**扫到残留 / 确认干净 / 根本没法扫**。第三种一律记「验不了」，
      绝不当绿 —— 认不出来不是桌面干净；
    · **收尾杀整棵进程树 + 复查 pid 真没了**，且**不许杀跑之前就存在的进程**：
      WindowsTerminal.exe 是全局共享宿主，杀了会连用户自己的终端一起端掉。

================================ 窗O3：把「飘」从判据里拿掉 ====================
DSH 亲跑 O2 两次：第 1 次 rc=1（绿 34 / 红 3），第 2 次 rc=0（绿 37 / 红 0）——
O2 自报「5 连跑全 0」，而 DSH 两次没复现。三条红的病灶，**都不是产品代码的锅**，
是判据自己的采样方式有毛病（DSH 的判断方向对，实测细节补在下面）：

    ① **2b「标题改了要被拦」被放行** —— 采样竞态。
       根因不是「SetWindowText 慢」：实测改完 **0.0s** 就读得到，且记事本
       3 秒内 30 个采样一次都没改回去（证据：work/_2h_wO3/probe_title_set.py）。
       真正脆的是「**我断言的那个句柄上，标题当时到底在不在**」—— 句柄可能
       已经被换掉/失效，那一刻标题根本不在，闸门当然放行；判据却把它记成
       「兜底层没生效」。⇒ **「没拦住」与「我没测到」必须分开**：轮询等标题
       真的落到本跑夹具的窗口上（≤TITLE_WAIT）；等不到就记 **验不了**，
       绝不记「放行了」。

    ② **6c「夹具窗 HWND 失效」还活着** —— 瞬时判定。
       杀进程 / 关窗是异步的，查一次就下结论必然飘。⇒ 轮询等失效（≤HWND_WAIT），
       并且**句柄被别的窗复用**不算我们的残留（红只准是「我们的东西没收干净」）。

    ③ **6d「终端宿主窗 + 记事本没变多」** —— ★ **断言范围错了**：它量的是
       **整个桌面**。别的窗（窗N2 那一刻正在弹窗）、用户自己随时会开终端/记事本，
       这条天生不可控。⇒ 保留全局对比，但**必须分清多出来的是谁的窗**：
       跑后新增窗若**不是本跑夹具**（属主不在本跑进程族 / 标题不带本轮标记或标题键）
       ⇒ 记 **验不了（环境被外部占用）**，**不许**记红。
       （6a —— 作用域正确的那条 —— 本来就是绿的，这也印证了问题在范围不在检测。）

另加一条**前置**断言（治「跟别的窗抢桌面」，也是窗K / 窗O2 两次飘的共同形态）：
开跑前先看桌面上有没有**别的判据 / 本项目自证弹窗 / 别的跑的夹具**在动；
有就 **验不了（环境被外部占用）并 rc=2**，绝不带着「别人在动」的桌面往下断言。

★ **退出码三态**（窗O3 起）：
    rc=0 绿（每条都测到了且都成立）｜rc=1 红（有测到的断言不成立）｜rc=2 验不了
    —— 环境/采样条件不具备，**没测到 ≠ 没成立**，绝不当绿也不当红。

================================ 窗O4：夹具别再用记事本 ====================
DSH 亲跑 O3 成果两次：
    第 1 次 rc=2（绿 34 / 红 0 / 验不了 3）—— 三态语义**工作正常**
    第 2 次 rc=1（绿 28 / 红 2 / 验不了 2），两条红**都是夹具没起来**：
    [红] 2a 普通应用夹具出现  ← 「没等到 notepad 窗口」
    [红] 4a 临时放行        ← 「夹具没起来」

★ 根因（DSH 判断，本判据实测复核，见 work/_2h_wO4/probe_fixture.py）：
  **Win11 的记事本是单实例 + 标签页**：连开两次 notepad.exe 会拿到**两个 pid
  但同一扇窗**（实测两次都是 hwnd 658730，第二次的 notepad.exe 把请求交给了
  已经在跑的那个实例，然后自己退了）。
  ⇒ 用户此刻开着记事本时，判据起的 notepad.exe 会被**吸进用户那扇窗**，
    桌面上一扇新窗都没有；而判据认窗时排除了「跑之前就存在的窗」，
    于是认不到窗，记成「没等到 notepad 窗口」＝ **假红**。
  ⇒ 拿 notepad.exe 当「普通应用夹具」**天生不可靠**：它起不起得来，
    取决于用户此刻有没有开记事本。

★ 本版改法：**自造窗**。判据自己用纯 Win32（RegisterClassExW + CreateWindowExW；
  **不依赖 tkinter** —— 本机 Tcl 的 init.tcl 就是坏的，tkinter 根本起不来）
  起一扇**可见**顶层窗：
    · **类名 = AgentCtlWoFixNorm_<本跑标记>** ⇒ 一跑一个，天然唯一，
      别的跑/别的程序不可能撞上；
    · 标题也带本跑标记 ⇒ **类名 + 属主 pid 族 + 轮次标记**三条都能认出自己；
    · 属主是 pythonw.exe（不是 python.exe —— 后者在 act.py 里是**硬拒**的，
      TkTopLevel 这个类名同样硬拒，所以「自己起个 tkinter 顶层窗」这条路也堵死）；
    · 窗**可见**（WS_VISIBLE + ShowWindow(SW_SHOWN)）—— 可见性是要件，
      绝不许做成隐形/隐藏窗来换绿（断言 2a-0 专门验这一条）。
  ⇒ 起窗是**确定**的：与用户开没开记事本、记事本的标签页机制，全都无关。

★ 残留判定**只认自己的东西**（窗O4）：
    认窗/认残留 = 本跑自造窗类名 ＋ 属主落在本跑夹具进程族 ＋ 标题带本跑标记，
    三条任一命中才算「我们的」。**桌面上别人的窗（用户的记事本/终端/浏览器）
    一律不算残留**；它们只在 6d 里被**分开计数**，并在真的影响断言时记「验不了」。
  ⇒ ⛔ 不再拿「桌面上记事本数量」当指标 —— 那是全桌面量，跟我们没关系。

================================ 怎么跑 ========================================
    python -X utf8 tests\_P0_自指保护判据.py                      # 绿（现行实现）
    python -X utf8 tests\_P0_自指保护判据.py --list-bad          # 打印红验证的跑法

退出码三态（窗O3 起）：**0 = 绿 / 1 = 红 / 2 = 验不了**（环境不具备，没测到）。
  rc=2 的三种来源：① 开跑前桌面上有别的判据 / 自证弹窗 / 别的跑的夹具在动；
  ② 跑的过程中桌面被外部占用（6d 发现外来新窗）；③ 某条断言的采样条件不具备
  （例如 2b 的标题在等待期内一直没落到夹具窗上）。

红验证（拿改动前那份「只按标题」的实现跑）：
    python -X utf8 tests\_P0_自指保护判据.py ^
        --impl "work\_2h_wK\bak_legacy_tools__act.py"
    ⇒ **断言 1 必红**：无害标题的终端窗口被放行了。

★ 这条判据会**真的起两个窗口**（一个 cmd.exe 终端夹具、一个**判据自造的**
  Win32 顶层窗）落在用户的 Default 桌面上，**跑完一定自己收掉**（断言 6 会当场验
  这件事）；放行记录写到临时目录（AGENTCTL_TERMINAL_PASS_FILE 覆盖口），
  **不碰真机记录**。
"""
from __future__ import annotations

import argparse
import atexit
import ctypes
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LEGACY = os.path.join(REPO, "legacy_tools")
ACT_DEFAULT = os.path.join(LEGACY, "act.py")
BAD_IMPL = os.path.join(REPO, "work", "_2h_wK", "bak_legacy_tools__act.py")

CREATE_NEW_CONSOLE = 0x00000010
WM_CLOSE = 0x0010
AUTH_TITLE = "DSH电脑控制授权"          # act.py BLOCKED_TITLES 里登记的那个标题
INNOCENT_TITLE = "随手记要用的窗口"       # 终端夹具故意用的无害标题
PASSDB = os.path.join(tempfile.gettempdir(), "wK_terminal_pass.json")

# ── 窗O3：异步动作的等待上限 ─────────────────────────────────────────
#   改标题、关窗、杀进程都是**异步**的：查一次就下结论 = 判据在飘。
#   给的是「耐心」不是「阈值」：到点还没成，就如实记「没测到」，不许硬判。
TITLE_WAIT = 5.0      # 等标题真的落到夹具窗上
HWND_WAIT = 5.0       # 等夹具窗 HWND 失效 / 夹具进程退出

# ── 窗O3：前置检查认的「桌面被别人占着」信号 ────────────────────────────
BRAKE_POPUP_CLASS = "AgentCtlBrakePopup"      # 本项目急停自证弹窗（act.py 注册）
FOREIGN_MARKER_PREFIX = "AGENTCTL_WOFIX_"     # 任何一跑判据的本轮标记前缀
OTHER_CRITERION_HINTS = ("_P0_自指保护判据.py", "_急停弹窗判据.py", "brake_popup.py")

# ── 窗O2：三种控制台宿主，一个都不能漏认 ─────────────────────────────
#   ① ConsoleWindowClass             —— conhost 自己的控制台窗
#   ② PseudoConsoleWindow            —— ConPTY；★ 标题恒为空，按标题键认不出来
#   ③ CASCADIA_HOSTING_WINDOW_CLASS  —— Windows Terminal 托管；标题是命令行
CONSOLE_CLASSES = ("ConsoleWindowClass", "PseudoConsoleWindow",
                   "CASCADIA_HOSTING_WINDOW_CLASS")
TERMINAL_FIXTURE_CLASS = "ConsoleWindowClass"   # 只用于措辞
INTERACTIVE_DESKTOP = "Default"

# ── 窗O：夹具的三条硬参数 ─────────────────────────────────────────────
FIXTURE_TTL = 25        # 夹具寿命上限（秒）。≤30s 是硬要求：判据本体 ~2s。
CLOSE_GRACE = 5.0       # 关窗 / 杀树之后，等「窗没了且进程也没了」的耐心
RESIDUE_POLICY = "clean"  # 本单选定：先清后跑（清不掉才报红）
RESIDUE_POLICY_TEXT = ("先清后跑：检出上一轮残留就收干净再往下跑；"
                       "收不掉（还剩着）直接报红停手 —— 带着残留往下跑等于让判据变随机数")

# 本轮唯一标记：写进每个终端夹具的命令行。认窗认不出来时，靠它把夹具连人带窗
# 揪出来；父进程被杀、子进程被 re-parent 的孤儿，也照样跑不掉。
MARKER = "AGENTCTL_WOFIX_%d" % os.getpid()

# ── 窗O4：自造窗夹具的三个「认自己」的凭据 ───────────────────────────────
#   ⛔ 窗O3 用的是 notepad.exe。实测 Win11 记事本是**单实例 + 标签页**：
#     连开两次拿到两个 pid 但**同一扇窗** —— 用户自己开着记事本时，
#     判据起的 notepad.exe 会被吸进用户那扇窗，桌面上一扇新窗都没有。
#   ⇒ 记事本当普通应用夹具天生不可靠（起不起得来取决于用户开没开记事本）。
#   ⇒ 改为自己造窗：**类名带本跑标记**，标题也带本跑标记，属主是本跑 pythonw.exe。
#     三条（类名 / 属主 pid 族 / 轮次标记）任一命中才算「我们的」。
SELF_CLASS_PREFIX = "AgentCtlWoFixNorm_"        # 自造窗类名前缀（永不与他人共用）
SELF_FIXTURE_CLASS = SELF_CLASS_PREFIX + MARKER  # ★ 一跑一个，天然唯一
SELF_FIXTURE_TITLE = MARKER + " 自造窗夹具"       # 标题也带标记 ⇒ 第二条凭据
SELF_FIXTURE_W, SELF_FIXTURE_H = 420, 260

# ── 窗O4：认「别人的窗」时排掉的系统噪声 ─────────────────────────────────
#   6d 要数「别人的应用窗」有没有新冒出来（外来 ⇒ 验不了，O3 立下的语义）。
#   但桌面上有些窗是系统外壳自己挂的，会自己冒自己灭（IME、缩略图助手、
#   显卡悬浮层…）。实测把它们列进来，否则判据会一直「验不了」。
#   实测记录：work/_2h_wO4/probe_noise_default.py（Default 桌面 18s 采样）
NOISE_CLASSES = (
    "Shell_TrayWnd", "Progman", "WorkerW", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "ShellHandwritingCanvas", "Button",
    "IME", "MSCTFIME UI", "tooltips_class32", "NotifyIconOverflowWindow",
    "ThumbnailDeviceHelperWnd", "EdgeUiInputTopWndClass",
    "XamlExplorerHostIslandWindow_WASDK", "DummyDWMListenerWindow",
    "CEF-OSC-WIDGET", "TaskListThumbnailWnd", "ForegroundStaging",
    "MultitaskingViewFrame", "Xaml_WindowedPopupClass", "SysShadow",
    "Tao Thread Event Target", "ApplicationFrameWindow",
)

# 「算一个应用窗」的门槛：可见 + 有标题 + 不太小 + 类名不在噪声名单里。
APP_WIN_MIN_SIDE = 64

# 三态：第三种「根本没法扫」绝不当绿。
FOUND, CLEAN, CANNOT_SCAN = "扫到残留", "确认干净", "没法扫"

OK, BAD, UNV = [], [], []      # UNV = 验不了：**没测到**，既不当绿也不当红

BASELINE_PIDS = set()   # 跑之前就活着的进程 —— 收尾时一个都不许动
DESKTOP = None         # 判据认定的残留扫描作用域


class ScanUnavailable(RuntimeError):
    """桌面状态不可知（枚举不到）—— 按「没法扫」算，绝不当成干净。"""


def section(t):
    print("\n" + "=" * 74 + "\n" + t + "\n" + "=" * 74, flush=True)


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("绿" if cond else "红", name,
                           ("  ← %s" % detail) if (detail and not cond) else ""),
          flush=True)
    return bool(cond)


def unverifiable(name, why):
    """记一条**验不了**：这一条**没测到**，不是「没成立」。

    ★ 窗O3 的核心纪律：**「没拦住」与「我没测到」必须分得开**。
      采样条件不具备时记红，等于用一条假警报把真警报淹掉 —— 那比漏报更坏。
    """
    UNV.append(name)
    print("  [验不了] %s  ← %s" % (name, why), flush=True)
    return False


# ── 被测实现：按路径加载，所以同一份判据既能验现行实现、也能验「改动前那份」──
def load_act(path):
    if LEGACY not in sys.path:
        sys.path.insert(0, LEGACY)          # act.py 要 import gate / gate_lock
    os.environ["AGENTCTL_TERMINAL_PASS_FILE"] = PASSDB
    os.environ["AGENTCTL_TERMINAL_PASS_LOG"] = PASSDB + ".jsonl"
    for p in (PASSDB, PASSDB + ".jsonl"):
        if os.path.exists(p):
            os.remove(p)
    spec = importlib.util.spec_from_file_location("act_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════ 窗O2 · 一、桌面锚定：别再验一块假桌面 ══════════
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32.OpenDesktopW.restype = wintypes.HANDLE
_u32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL,
                             wintypes.DWORD]
_u32.GetProcessWindowStation.restype = wintypes.HANDLE
_u32.GetThreadDesktop.restype = wintypes.HANDLE
_u32.GetThreadDesktop.argtypes = [wintypes.DWORD]
_u32.SetThreadDesktop.restype = wintypes.BOOL
_u32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
_k32.CreateProcessW.restype = wintypes.BOOL

DESKTOP_READOBJECTS = 0x0001
DESKTOP_CREATEWINDOWS = 0x0004
DESKTOP_SWITCHDESKTOP = 0x0100


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def _obj_name(handle):
    buf = ctypes.create_unicode_buffer(256)
    n = wintypes.DWORD()
    if handle and _u32.GetUserObjectInformationW(handle, 2, buf, 512, ctypes.byref(n)):
        return buf.value
    return "<拿不到>"


def current_desktop_name():
    tid = _k32.GetCurrentThreadId()
    return _obj_name(_u32.GetThreadDesktop(tid))


def anchor_desktop():
    """把判据的残留扫描作用域钉死，并回报到底钉在哪儿。

    ★ 关键区别（踩过的坑）：SetThreadDesktop 只改**本线程**，
      子进程继承的是**进程**的桌面 —— 所以除了切线程，还得用
      STARTUPINFO.lpDesktop 指名把夹具起在同一块桌面上，光切线程没有用。
    """
    global DESKTOP
    here = current_desktop_name()
    h = _u32.OpenDesktopW(INTERACTIVE_DESKTOP, 0, False,
                        DESKTOP_READOBJECTS | DESKTOP_CREATEWINDOWS | DESKTOP_SWITCHDESKTOP)
    if h:
        ok = bool(_u32.SetThreadDesktop(h))
        _u32.CloseDesktop(h)
        if ok:
            DESKTOP = INTERACTIVE_DESKTOP
            return DESKTOP, "已切到用户的 %s 桌面（残留扫描作用域＝用户的真桌面）" % DESKTOP
    DESKTOP = here
    return DESKTOP, ("★ 切不到 %s，沿用当前桌面 %r —— 残留结论**只覆盖这块桌面**，"
                     "不代表用户桌面" % (INTERACTIVE_DESKTOP, here))


class Spawned(object):
    """指名起在指定桌面上的进程。

    ★ 为什么不用 subprocess.Popen：子进程继承**进程**的桌面，
      从沙箱桌面起的夹具永远落不到用户桌面上 —— 判据就永远在验假桌面。
      STARTUPINFO.lpDesktop 才是指名的办法。
    """

    def __init__(self, pi, cmdline, desktop):
        self.pid = pi.dwProcessId
        self._h = pi.hProcess
        self.cmdline = cmdline
        self.desktop = desktop

    def wait(self, timeout=None):
        ms = 0xFFFFFFFF if timeout is None else int(timeout * 1000)
        return _k32.WaitForSingleObject(self._h, ms) == 0

    def kill(self):
        try:
            _k32.TerminateProcess(self._h, 1)
        except Exception:
            pass

    def __del__(self):
        try:
            _k32.CloseHandle(self._h)
        except Exception:
            pass


def spawn_on_desktop(cmdline, desktop=None, flags=CREATE_NEW_CONSOLE):
    """把命令起在**指名**的桌面上。失败就抛，别假装起来了。"""
    desktop = desktop or DESKTOP or current_desktop_name()
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = ctypes.c_wchar_p(desktop)
    pi = PROCESS_INFORMATION()
    ok = _k32.CreateProcessW(None, ctypes.create_unicode_buffer(cmdline),
                             None, None, False, flags, None, None,
                             ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return Spawned(pi, cmdline, desktop)


# ── 窗：自己上桌面上找，不问实现「你觉得弹了吗」────────────────────────
import win32gui                                    # noqa: E402
import win32process                                # noqa: E402
import psutil                                      # noqa: E402


def win_attr(h, getter, default=""):
    """读窗口属性，**容忍窗口中途没了**。

    ★ EnumWindows 和 GetClassName 之间隔着一次 IPC：窗随时可能自己关掉，
      这时 GetClassName 抛 1400（无效的窗口句柄）。判据里这种异常要是冒到
      收尾路径上，就把「红」变成「崩」，还顺手制造新的飘 —— 一律按默认值处理。
    """
    try:
        return getter(h) or default
    except Exception:
        return default


def owner_of(h):
    try:
        return psutil.Process(win32process.GetWindowThreadProcessId(h)[1]).name()
    except Exception:
        return "?"


def owner_pid(h):
    try:
        return int(win32process.GetWindowThreadProcessId(h)[1])
    except Exception:
        return -1


def describe(h):
    return {"hwnd": int(h), "title": win_attr(h, win32gui.GetWindowText),
            "class": win_attr(h, win32gui.GetClassName), "process": owner_of(h)}


def enum_hwnds():
    """枚举本线程所在桌面的全部顶层窗（含不可见 —— 藏起来的残留也算残留）。

    ★ fail-closed：连试几次都枚举不出来就**抛 ScanUnavailable**，
      绝不返回空列表 —— 返回空列表会被后面的「残留 = 0」当成「桌面干净」，那是绿障。
    ★ 回调里只做 append：把 psutil / GetWindowText 之类塞进 EnumWindows 回调，
      会让 pywin32 抛 122（数据区域太小）。
    """
    last = None
    for _ in range(8):
        out = []

        def cb(h, _l):
            out.append(int(h))
            return True

        try:
            win32gui.EnumWindows(cb, 0)
            return out
        except Exception as e:
            last = e
            time.sleep(0.4)
    raise ScanUnavailable("EnumWindows 连试 8 次都失败（%s）—— 桌面状态不可知，按没法扫算"
                         % (last,))


def visible():
    """只要可见窗口。枚举不出来照样抛（不许拿空列表当干净）。"""
    return [h for h in enum_hwnds() if win32gui.IsWindowVisible(h)]


def wait_window(pred, timeout=20.0, exclude=()):
    """等到第一个满足 pred(h) 的可见窗口。找不到就返回 None（如实说找不到）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        for h in visible():
            if h in exclude:
                continue
            if pred(h):
                return h
        time.sleep(0.4)
    return None


# ══════════ 窗O3 · 三条飘的病根在采样方式，不在产品代码 ══════════


def wait_title_on_fixture(pids, want, timeout=TITLE_WAIT, hwnds=()):
    """轮询：等目标标题真的落到**本跑夹具的某个活窗**上。返回 (hwnd, 等了多久)。

    ★ 为什么必须轮询（2b 那条红的病根）：
      改标题是异步的，而且**句柄本身都可能换掉** —— 判据改完立刻断言，
      那一刻标题压根不在窗上，闸门当然放行，判据却记成「兜底层没生效」。
      那是**假警报**，比漏报更坏。
    ★ pids 非空时只认属主落在本跑夹具进程里的窗；句柄失效就现场重新枚举，
      不拿一个可能已经换了用户的句柄去断言。
    ★ 返回 hwnd=None = **没等到** ⇒ 调用方必须记「验不了」，绝不许记「放行了」。
    """
    want = want or ""
    pids = set(int(p) for p in (pids or ()))
    pool = [int(h) for h in (hwnds or ())]
    t0 = time.time()
    while True:
        cands = list(pool)
        if pids:
            for h in visible():
                if h not in pool:
                    cands.append(h)
        for h in cands:
            if not hwnd_alive(h):
                continue
            if pids and owner_pid(h) not in pids:
                continue
            if want and want in (win_attr(h, win32gui.GetWindowText) or ""):
                return h, round(time.time() - t0, 3)
        if time.time() - t0 >= timeout:
            return None, round(time.time() - t0, 3)
        time.sleep(0.05)


def wait_hwnd_gone(h, timeout=HWND_WAIT):
    """轮询等夹具窗 HWND 失效。返回 (失效了吗, 等了多久)。"""
    t0 = time.time()
    while hwnd_alive(h):
        if time.time() - t0 >= timeout:
            return False, round(time.time() - t0, 2)
        time.sleep(0.2)
    return True, round(time.time() - t0, 2)


def wait_pid_gone(pid, timeout=HWND_WAIT):
    """轮询等夹具进程退出。返回 (退了吗, 等了多久)。"""
    t0 = time.time()
    while pid_alive(pid):
        if time.time() - t0 >= timeout:
            return False, round(time.time() - t0, 2)
        time.sleep(0.2)
    return True, round(time.time() - t0, 2)


def is_noise_class(cls):
    """外壳/输入法的自挂窗：会自己冒自己灭，不算「有人在用桌面」。"""
    cls = cls or ""
    return cls in NOISE_CLASSES or any(cls.startswith(n) for n in NOISE_CLASSES)


def is_ours_window(h, own_pids=()):
    """这扇窗**是不是本跑夹具的**。

    ★ 窗O4 定的认「自己」三条凭据（任一命中即是）：
      ① 类名 == 本跑自造窗类（一跑一个，别的程序不可能撞上）；
      ② 属主 pid 落在本跑夹具进程族里；
      ③ 标题带本跑标记 / 夹具标题键（WindowsTerminal 托管时属主不是我们，
         只有这一条认得出来 —— 这正是窗O2 实测到的形态）。
    ★ 认不出 = **不算我们的**（宁可认不到，也不许把用户的窗算成我们的残留）。
    """
    cls = win_attr(h, win32gui.GetClassName)
    title = win_attr(h, win32gui.GetWindowText)
    pid = owner_pid(h)
    if cls == SELF_FIXTURE_CLASS:
        return True
    if own_pids and pid in set(int(p) for p in own_pids):
        return True
    return bool(MARKER in (title or "") or INNOCENT_TITLE in (title or ""))


def app_windows(own_pids=()):
    """桌面上**别人的应用窗**快照：hwnd → (属主 pid, 类名, 标题)。

    ★ 窗O4：这张表**不再是残留指标**。O3 那个「桌面上终端宿主窗 + 记事本数量」
      的写法已废 —— 那是**全桌面量**，用户开不开记事本都会让它变。
      现在它只回答一个问题：**跑的过程中，别人有没有新开窗**（外来 ⇒ 验不了）。
    ★ 门槛：可见 + 有标题 + 不小于 64x64 + 类名不在系统噪声名单里
      （否则 IME/缩略图助手/显卡悬浮层会让人看着像「有人在用桌面」）。
    ★ 自己的窗**先排掉**：属主在夹具进程族、或类名/标题带本跑标记。
    """
    own = set(int(p) for p in (own_pids or ()))
    out = {}
    for h in enum_hwnds():
        if not win32gui.IsWindowVisible(h):
            continue
        cls = win_attr(h, win32gui.GetClassName)
        title = win_attr(h, win32gui.GetWindowText)
        pid = owner_pid(h)
        if pid in own or cls == SELF_FIXTURE_CLASS:
            continue
        if MARKER in (title or "") or INNOCENT_TITLE in (title or ""):
            continue
        if is_noise_class(cls) or not title:
            continue
        try:
            l, t, r, b = win32gui.GetWindowRect(h)
        except Exception:
            continue
        if (r - l) < APP_WIN_MIN_SIDE or (b - t) < APP_WIN_MIN_SIDE:
            continue
        out[int(h)] = (pid, cls, title)
    return out


def host_is_ours(info, own_pids):
    """这扇「跑后新增的窗」是不是**本跑夹具**的。"""
    pid, cls, title = info
    if own_pids and pid in own_pids:
        return True
    if cls == SELF_FIXTURE_CLASS:
        return True
    return bool(MARKER in (title or "") or INNOCENT_TITLE in (title or ""))


def _ancestor_pids():
    """我自己和我的父链。

    ★ 前置检查必须排除它们：父 pwsh 的命令行里就带着本脚本的路径，
      不排除就等于「自己把自己判成别的判据在跑」，永远 rc=2。
    """
    out, pid = set(), os.getpid()
    for _ in range(16):
        if pid <= 0 or pid in out:
            break
        out.add(pid)
        try:
            pid = psutil.Process(pid).ppid()
        except Exception:
            break
    return out


def precondition():
    """开跑前：**桌面上是不是有人在用**。返回 (安静?, 命中说明列表)。

    ★ 窗O3 新增。窗K / 窗O2 两次飘的共同形态就是「别的窗正在弹窗时我往下断言」。
      带着「别人在动」的桌面跑出来的红，绿的都未必是绿的，红的更可能是假的。
    ★ 只**看**，不动手：发现外来夹具绝不代劳去杀 —— 那是别人的东西。
      判据只负责如实说「验不了」并给出手工清理的线索。
    """
    hits = []
    mine = _ancestor_pids()
    # ★ 窗O4：自造窗夹具必须有 pythonw.exe 才起得起来。
    #   没有它 ⇒ 造不出一个「不在任何名单里」的普通应用窗口（python.exe 被硬拒）
    #   ⇒ 断言 2/3 无从测起，属于「环境不具备」：前置就记验不了，别带着往下跑。
    if not pythonw_path():
        hits.append("找不到 pythonw.exe —— 造不出一个不在任何名单里的"
                    "「普通应用」窗口 ⇒ 断言 2/3 无从测起")
    try:
        for pr in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                pid = int(pr.info["pid"])
                if pid in mine:
                    continue
                cl = " ".join(pr.info.get("cmdline") or [])
            except Exception:
                continue
            if not cl:
                continue
            if FOREIGN_MARKER_PREFIX in cl:
                hits.append("别的跑的夹具还活着：pid=%s（命令行带 %s）%r"
                            % (pid, FOREIGN_MARKER_PREFIX, cl[:90]))
            elif any(x in cl for x in OTHER_CRITERION_HINTS):
                hits.append("别的判据正在跑：pid=%s %r" % (pid, cl[:90]))
    except Exception as e:
        hits.append("★ 进程表读不到（%s）—— 前置检查**没法做**" % e)
    try:
        for h in enum_hwnds():
            if not win32gui.IsWindowVisible(h):
                continue
            cls = win_attr(h, win32gui.GetClassName)
            title = win_attr(h, win32gui.GetWindowText)
            if cls == BRAKE_POPUP_CLASS:
                hits.append("本项目的急停自证弹窗在桌上：hwnd=%s（%r）" % (h, title))
            elif INNOCENT_TITLE in title:
                hits.append("桌面上有带夹具标题键的窗（别的跑的残留）："
                            "hwnd=%s 属主 %s（%r）" % (h, owner_of(h), title))
    except ScanUnavailable as e:
        hits.append("★ 枚举不到桌面窗口（%s）—— 前置检查**没法做**" % e)
    return (not hits), hits


# ══════════ 窗O2 · 二、三态残留扫描（认窗 + 认进程）══════════

def pid_alive(pid):
    try:
        return psutil.Process(int(pid)).is_running()
    except Exception:
        return False


def hwnd_alive(h):
    if not h:
        return False
    try:
        return bool(win32gui.IsWindow(int(h)))
    except Exception:
        return False


def _wait(cond, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.25)
    return bool(cond())


def family_of(pids):
    """给一组 pid 补齐子孙。认残留不能只看本人：杀父之后子进程会被 re-parent。"""
    out = set()
    for p in pids:
        try:
            pr = psutil.Process(int(p))
        except Exception:
            continue
        out.add(pr.pid)
        try:
            for c in pr.children(recursive=True):
                out.add(c.pid)
        except Exception:
            pass
    return out


def marker_processes():
    """命令行里带本轮标记的活进程 —— 专抓被 re-parent 的孤儿。"""
    hits = []
    try:
        for pr in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cl = pr.info.get("cmdline") or []
                if any(MARKER in str(x) for x in cl):
                    hits.append(pr)
            except Exception:
                continue
    except Exception as e:
        raise ScanUnavailable("进程表读不到（%s）—— 没法扫" % e)
    return hits


def fixture_windows(fam=()):
    """现场枚举夹具窗。**不靠类名单认** —— 类名随后端变。

    认出来的口径（四条任一命中即是）：
        · **类名 == 本跑自造窗类**（窗O4 新增：一跑一个，唯一）；
        · 标题含本轮标记 MARKER；
        · 标题含夹具标题键 INNOCENT_TITLE（按**包含**匹配：cmd 被杀后标题会变成
          「随手记要用的窗口  - pause」这种带后缀的样子）；
        · 属主 pid 落在夹具进程族里（这一条认得出来 ConPTY 与 WT 托管两族）；
        · 类名是三种终端宿主之一，且属主在夹具进程族里。

    ★ 窗O4：**判不出是「没扫到」，不是「桌面干净」**。扫不到一律走三态。
    """
    fam = set(int(p) for p in (fam or ()))
    out = []
    for h in enum_hwnds():
        cls = win_attr(h, win32gui.GetClassName)
        title = win_attr(h, win32gui.GetWindowText)
        pid = owner_pid(h)
        why = None
        if cls == SELF_FIXTURE_CLASS:
            why = "类名=本跑自造窗类"
        elif MARKER in title:
            why = "标题含本轮标记"
        elif INNOCENT_TITLE in title:
            why = "标题含夹具标题键"
        elif pid in fam:
            why = "属主在夹具进程族里"
        elif cls in CONSOLE_CLASSES and pid in fam:
            why = "终端宿主窗（%s）" % cls
        if why:
            out.append({"hwnd": h, "pid": pid, "class": cls, "title": title,
                        "why": why, "visible": bool(win32gui.IsWindowVisible(h))})
    return out


def scan_residue(fam=()):
    """扫残留。返回 (状态, 行)：三态里的哪一种 + 证据行。

    ★ 判据的核心口径：**扫不到 ≠ 干净**。枚举不到就是 CANNOT_SCAN（验不了），
      绝不当绿 —— 这是窗O2 的第一条铁律。
    """
    fam = set(int(p) for p in (fam or ()))
    try:
        wins = fixture_windows(fam)
        procs = marker_processes()
    except ScanUnavailable as e:
        return CANNOT_SCAN, [{"why": str(e)}]
    rows = list(wins)
    for p in procs:
        rows.append({"hwnd": None, "pid": p.pid, "class": "<进程>",
                     "title": " ".join(p.info.get("cmdline") or [])[:120],
                     "why": "命令行带本轮标记", "visible": None})
    for pid in sorted(fam):
        if pid_alive(pid):
            rows.append({"hwnd": None, "pid": pid, "class": "<进程>", "title": "",
                         "why": "夹具进程还活着", "visible": None})
    return (FOUND if rows else CLEAN), rows


def count_host_windows():
    """桌面上**别人的应用窗**总数 —— 只作对照，**不是残留指标**。

    ★ 窗O4：O3 这里是「终端宿主窗 + 记事本」的数量。已废 ——
      那是全桌面量，用户开不开记事本都会让它变，拿它当指标天生会飘。
    """
    return len(app_windows())


def kill_tree(pid):
    """杀一棵进程树：**子先父后**，且**跑之前就存在的进程一个都不许动**。

    ★ 为什么不能只杀父：控制台窗是 cmd.exe 起的，cmd 一死，子进程变孤儿，
      而那扇窗反而由孤儿抱着继续赖在桌面上 —— 只 p.kill() 收不干净。
    ★ 为什么加 BASELINE 保护：WindowsTerminal.exe 是**全局共享**宿主，
      用户自己的终端全在它下面。杀它等于把用户的窗口一起端掉 —— 那比留个
      残留还糟。只收自己起的、跑之前不存在的那些。
    """
    try:
        pr = psutil.Process(int(pid))
    except Exception:
        return
    if pr.pid in BASELINE_PIDS:
        return
    kids = []
    try:
        kids = [c for c in pr.children(recursive=True) if c.pid not in BASELINE_PIDS]
    except Exception:
        kids = []
    for c in reversed(kids):
        try:
            c.kill()
        except Exception:
            pass
    try:
        pr.kill()
    except Exception:
        pass


def reap(hwnd, pids=(), grace=CLOSE_GRACE):
    """把一扇夹具窗连同它的进程收掉，并**确认**真走了。

    先礼（PostMessage WM_CLOSE）→ 等 → 不行再强（杀整棵进程树）→ 再等。
    返回 True = 窗没了 **且** 相关进程都没了。
    ★ 绝不用 ShowWindow 藏窗充数：藏起来的窗用户看不见，但残留还在，
      而 0s-c 这类断言要的正是「进程真的退了」。
    """
    pids = set(int(p) for p in pids if p)
    if not hwnd_alive(hwnd) and not pids:
        return True

    def quiet():
        return (not hwnd_alive(hwnd)) and all(not pid_alive(p) for p in pids)

    for _ in range(2):
        try:
            win32gui.PostMessage(int(hwnd), WM_CLOSE, 0, 0)
        except Exception:
            pass
        if _wait(quiet, grace):
            return True
        for p in sorted(pids):
            kill_tree(p)
    return _wait(quiet, 1.0)


# 夹具登记册：谁起的窗都得在这儿挂号，atexit 与 finally 都靠它收尾。
LIVE = {}


def register(key, label, popen, hwnd, pid):
    LIVE[key] = {"label": label, "popen": popen, "hwnd": hwnd, "pid": pid}


def live_family():
    return family_of([r["pid"] for r in LIVE.values() if r.get("pid")])


def reap_all(verbose=False):
    """收尾总入口。幂等 ⇒ finally 与 atexit 各调一次也不怕。"""
    for _pass in range(3):
        if not LIVE:
            try:
                extra = fixture_windows()
            except ScanUnavailable:
                extra = []
            if not extra:
                break
        for key in list(LIVE):
            rec = LIVE[key]
            hwnd, pid = rec["hwnd"], rec["pid"]
            ok = reap(hwnd, [pid]) if hwnd else False
            kill_tree(pid)
            if rec["popen"] is not None:
                try:
                    rec["popen"].wait(timeout=1.0)
                except Exception:
                    pass
            if ok and not pid_alive(pid):
                del LIVE[key]
            if verbose:
                print("  收 %s：hwnd=%s pid=%s → %s"
                      % (rec["label"], hwnd, pid, "已收干净" if ok else "已强杀"))
        for row in fixture_windows(live_family()):      # 现场兜底：还剩的都收
            if row["hwnd"]:
                reap(row["hwnd"], [row["pid"]])
        try:                                            # 孤儿：命令行还带本轮标记
            for pr in marker_processes():
                if pr.pid not in BASELINE_PIDS:
                    try:
                        pr.kill()
                    except Exception:
                        pass
        except ScanUnavailable:
            pass
    return True


def sweep_residue(fam=(), rounds=4):
    """扫 + 清。返回 (检出过的, 清完还赖着的, 首次扫描状态, 收尾扫描状态)。

    ★ 首次状态和收尾状态必须分开：0s-a 要证明的是「**检出时**认得出来」，
      用收尾那个 CLEAN 去判就成了自己骗自己。
    """
    seen = []
    state0 = CANNOT_SCAN
    for _ in range(rounds):
        state, rows = scan_residue(fam)
        if state == CANNOT_SCAN:
            return seen, rows, CANNOT_SCAN, CANNOT_SCAN
        if not seen:
            state0 = state
        if not rows:
            break
        for r in rows:
            seen.append(r)
            if r["hwnd"]:
                reap(r["hwnd"], [r["pid"]])
            else:
                kill_tree(r["pid"])
    state, rows = scan_residue(fam)
    return seen, rows, (state0 if state0 != CANNOT_SCAN else CANNOT_SCAN), state


def terminal_cmdline():
    """终端夹具的命令行。

    ★ 用 cmd 内建 **pause**，不 spawn 工作性子进程（原实现是 timeout.exe）：
      cmd 一死，子进程立刻变孤儿，反过来由它抱着那扇控制台窗赖在桌面上。
    ★ 夹一条 MARKER 进去（rem 不回显，也不污染窗口标题）：
      认窗认不出来时，靠它把夹具连人带窗揪出来，re-parent 的孤儿也跑不掉。
    """
    return "cmd.exe /k title %s & rem %s & pause" % (INNOCENT_TITLE, MARKER)


def spawn_terminal_fixture(job=None):
    """起那个无害标题的终端夹具，落在判据认定的桌面上。"""
    p = spawn_on_desktop(terminal_cmdline(), DESKTOP, CREATE_NEW_CONSOLE)
    attach_to_job(job, p.pid)
    return p


# ══════════ 窗O4：自造窗夹具（替掉 notepad.exe）══════════
#   ⛔ 永不使用 notepad.exe 当「普通应用夹具」——
#     Win11 记事本是单实例 + 标签页，起不起得来取决于用户此刻有没有开记事本
#     （见文件头「窗O4」那段）。这里自己造一扇**可见**窗，起不来得确定。
#   ★ 为什么不用 tkinter：本机 Tcl 的 init.tcl 就是坏的，import tkinter 过得去，
#     Tk() 直接 TclError 起不来；而且 TkTopLevel 这个类名在 act.py 里是**硬拒**的，
#     用它当「普通应用」等于造一扇天生就该被拦的窗。纯 Win32 两条路都绕开了。
SELF_WIN_SRC = """# -*- coding: utf-8 -*-
# 判据的「普通应用」夹具：纯 Win32 建一扇可见顶层窗。
# 用法：pythonw.exe <本文件> <类名> <标题> [状态文件]
# 类名/标题都由判据传进来，都带本跑标记 —— 认窗不靠「桌面上有几个记事本」。
import ctypes
import sys
import traceback
from ctypes import wintypes

u = ctypes.WinDLL("user32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    # ★ 必须带 hIconSm：RegisterClassExW 要的是 WNDCLASSEXW(80 字节)，
    #   少一个字段就 ERROR_INVALID_PARAMETER(87)（本判据踩过，见回执）。
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


u.DefWindowProcW.restype = LRESULT
u.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
u.RegisterClassExW.restype = ctypes.c_ushort
u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
u.CreateWindowExW.restype = wintypes.HWND
u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                              wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.HWND,
                              wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
u.UpdateWindow.argtypes = [wintypes.HWND]
u.GetMessageW.restype = ctypes.c_int
u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                          ctypes.c_uint, ctypes.c_uint]
u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
u.DispatchMessageW.restype = LRESULT
u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
u.PostQuitMessage.argtypes = [ctypes.c_int]
u.DestroyWindow.restype = wintypes.BOOL
u.DestroyWindow.argtypes = [wintypes.HWND]
u.LoadCursorW.restype = wintypes.HANDLE
u.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
k.GetModuleHandleW.restype = wintypes.HMODULE
k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

cls_name, title = sys.argv[1], sys.argv[2]
status = sys.argv[3] if len(sys.argv) > 3 else None


def say(line):
    # 状态文件是给判据看的**诊断通道**：夹具起不来时，判据要把子进程的
    # traceback 原样打出来 —— 绝不许「起不来」只留一句「没等到窗」。
    if status:
        try:
            with open(status, "a", encoding="utf-8") as f:
                f.write(line + "\\n")
                f.flush()
        except Exception:
            pass


def _proc(h, m, w, l):
    if m == 0x0002:            # WM_DESTROY -> 退消息循环
        u.PostQuitMessage(0)
        return 0
    if m == 0x0010:            # WM_CLOSE -> 销毁，进程自己退（收尾要的就是这个）
        u.DestroyWindow(h)
        return 0
    return u.DefWindowProcW(h, m, w, l)


KEEP = WNDPROC(_proc)
try:
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = KEEP
    wc.hInstance = k.GetModuleHandleW(None)
    wc.lpszClassName = cls_name
    wc.hCursor = u.LoadCursorW(None, ctypes.c_void_p(32512))
    wc.hbrBackground = ctypes.c_void_p(6)
    if not u.RegisterClassExW(ctypes.byref(wc)):
        raise ctypes.WinError(ctypes.get_last_error())
    hwnd = u.CreateWindowExW(0, cls_name, title,
                             0x00CF0000 | 0x10000000,   # WS_OVERLAPPEDWINDOW|WS_VISIBLE
                             90, 90, 420, 260, None, None, wc.hInstance, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    u.ShowWindow(hwnd, 5)      # SW_SHOWN：可见性是要件，不许做隐形窗换绿
    u.UpdateWindow(hwnd)
    say("hwnd=%s visible=%s" % (hwnd, u.IsWindowVisible(hwnd)))
    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))
    say("loop-exit")
except Exception:
    say("EXC " + traceback.format_exc())
    raise
"""

SELF_TMP = [None]          # 本跑落地的临时文件（脚本 + 状态文件），收尾删掉


def pythonw_path():
    """属主必须是 pythonw.exe。

    ★ 为什么不能退回 sys.executable（= python.exe）：act.py 的
      BLOCKED_PROCESSES_HARD 里硬拒 python.exe —— 那扇窗**天生就该被拦**，
      拿它当「普通应用」验「普通应用必须放行」，等于自己把判据的前提拆了。
    """
    cand = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                        "pythonw.exe")
    return cand if os.path.isfile(cand) else None


def write_self_script():
    """把自造窗脚本落到 %TEMP%，文件名带本跑标记。返回 (脚本路径, 状态文件路径)。"""
    script = os.path.join(tempfile.gettempdir(), "wo4_win_%s.py" % MARKER)
    status = os.path.join(tempfile.gettempdir(), "wo4_win_%s.status" % MARKER)
    with open(script, "w", encoding="utf-8") as f:
        f.write(SELF_WIN_SRC)
    try:
        if os.path.exists(status):
            os.remove(status)
    except OSError:
        pass
    SELF_TMP[0] = (script, status)
    return script, status


def cleanup_self_script():
    for p in (SELF_TMP[0] or ()):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    SELF_TMP[0] = None


def self_status_text():
    """子进程写下的状态（建窗结果 / 失败时的 traceback）。读不到就如实说读不到。"""
    rec = SELF_TMP[0]
    if not rec:
        return ""
    try:
        with open(rec[1], encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def spawn_self_fixture(job=None):
    """起那扇**自造窗**：类名/标题都带本跑标记，落在判据认定的桌面上。"""
    pyw = pythonw_path()
    if not pyw:
        raise RuntimeError("找不到 pythonw.exe —— 造不出一个不在任何名单里的"
                           "「普通应用」窗口（python.exe 被 act.py 硬拒）")
    script, status = write_self_script()
    cmd = '"%s" "%s" "%s" "%s" "%s"' % (pyw, script, SELF_FIXTURE_CLASS,
                                        SELF_FIXTURE_TITLE, status)
    p = spawn_on_desktop(cmd, DESKTOP, 0)      # flags=0：不要控制台窗
    attach_to_job(job, p.pid)
    return p


def start_watchdog():
    """看门狗：判据本体万一卡住（某一步 hang 住），FIXTURE_TTL 秒后照样收。"""
    t = threading.Timer(FIXTURE_TTL, lambda: reap_all(verbose=True))
    t.daemon = True
    t.start()
    return t


class Fixtures(object):
    """两个真窗口：一个 cmd.exe 终端（无害标题），一个**判据自造的**普通应用窗。

    ★ 窗O4：第二个夹具**不再用 notepad.exe**（单实例 + 标签页 ⇒ 要么起不来，
      要么被用户已经开着的记事本吸走 —— 实测两次 notepad.exe 拿到同一扇窗）。
      改成自造窗：类名 AgentCtlWoFixNorm_<本跑标记>、标题带本跑标记、
      属主是本跑 pythonw.exe。**认窗只认这三条**，绝不认「桌面上有几个记事本」。
    """

    def __init__(self, job=None):
        self.job = job
        self.term = None
        self.app = None
        self.app_title0 = ""
        self.term_pid = None
        self.app_pid = None

    def start(self):
        before = set(visible())
        p_term = spawn_terminal_fixture(self.job)
        register("term", "终端夹具", p_term, None, p_term.pid)
        self.term_pid = p_term.pid
        try:
            p_app = spawn_self_fixture(self.job)
        except Exception as e:
            print("  [!] 自造窗起不来：%s" % e)
            p_app = None
        if p_app is not None:
            register("app", "自造窗夹具", p_app, None, p_app.pid)
            self.app_pid = p_app.pid
        # 三种宿主类都认：conhost 自己的窗 / ConPTY 伪console窗 / WT 托管窗。
        # ★ 不靠类名单认 —— 类名随后端变；属主进程名才是稳的那一半。
        # ★ 窗O3：**只认本跑起的窗**。属主落在 BASELINE_PIDS（跑之前就活着）
        #   的窗是别人的 —— 桌面安静时几乎撞不上，别的判据或用户一开终端/记事本
        #   就会撞上；撞上了的后果是判据在**别人的窗**上断言，收尾还会把它
        #   WM_CLOSE 掉。宁可认不到窗（→ 验不了），不许认错窗。
        def _not_baseline(h):
            p = owner_pid(h)
            return p > 0 and p not in BASELINE_PIDS

        self.term = wait_window(
            lambda h: _not_baseline(h) and (
                win_attr(h, win32gui.GetClassName) in CONSOLE_CLASSES
                or owner_of(h).lower() in ("cmd.exe", "powershell.exe", "pwsh.exe")),
            exclude=before)
        # ★ 窗O4：自造窗的认窗条件是**最严**的一条 ——
        #   类名必须是本跑那个唯一类名，**且**属主必须是本跑起的那一个 pid。
        #   两条都满足还认错窗基本不可能；用户的任何窗更是碰都碰不到。
        self.app = wait_window(
            lambda h: (bool(self.app_pid)
                       and win_attr(h, win32gui.GetClassName) == SELF_FIXTURE_CLASS
                       and owner_pid(h) == self.app_pid),
            exclude=before)
        if self.term:
            register("term", "终端夹具", p_term, self.term, p_term.pid)
        if self.app:
            register("app", "自造窗夹具", p_app, self.app, p_app.pid)
            self.app_title0 = win_attr(self.app, win32gui.GetWindowText) or ""
        return self.term, self.app

    def stop(self):
        return reap_all()


def plant_leftover(job=None):
    """故意在桌面上埋一个「上一轮的残留」，用来验残留检测是不是真在工作。"""
    before = set(visible())
    p = spawn_terminal_fixture(job)
    register("plant", "埋的残留", p, None, p.pid)
    h = wait_window(
        lambda h: (win_attr(h, win32gui.GetClassName) in CONSOLE_CLASSES
                   and INNOCENT_TITLE in win_attr(h, win32gui.GetWindowText)),
        exclude=before)
    if h:
        register("plant", "埋的残留", p, h, p.pid)
    return p.pid, h


def is_terminal_reason(why):
    w = why or ""
    return ("终端" in w) or ("ConsoleWindowClass" in w) or ("CASCADIA" in w)


def summary():
    section("汇总")
    print("  绿 %d 条，红 %d 条，验不了 %d 条" % (len(OK), len(BAD), len(UNV)))
    if BAD:
        for b in BAD:
            print("    红：%s" % b)
        print("  结论：【红】rc=1 —— 有测到的断言不成立")
        return 1
    for u in UNV:
        print("    验不了：%s" % u)
    if UNV:
        print("  结论：【验不了】rc=2 —— 有断言**没测到**（采样条件不具备），"
              "既不算绿也不算红")
        return 2
    print("  结论：【绿】rc=0 —— 每条都测到了，且都成立")
    return 0


# ══════════ Job Object：判据一死，夹具整棵进程树跟着死 ══════════
#   finally + atexit 管的是「判据还活着、只是退出了」那几种死法。
#   这一条管的是**进程直接没了**那几种（taskkill /F、CTRL_BREAK、断电）。
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB = [None]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IOCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def start_kill_on_close_job():
    h = _k32.CreateJobObjectW(None, None)
    if not h:
        return None
    info = _ExtLimits()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
        _k32.CloseHandle(h)
        return None
    _JOB[0] = h
    return h


def attach_to_job(job, pid):
    """把夹具挂进 job。挂不上返回 False（降级，不算红）。"""
    if not job:
        return False
    ph = _k32.OpenProcess(0x0100 | 0x0001, False, int(pid))   # SET_QUOTA | TERMINATE
    if not ph:
        return False
    try:
        return bool(_k32.AssignProcessToJobObject(job, ph))
    except Exception:
        return False
    finally:
        _k32.CloseHandle(ph)


def main(argv=None):
    ap = argparse.ArgumentParser(description="P0 自指保护判据")
    ap.add_argument("--impl", default=ACT_DEFAULT,
                    help="被测的 act.py 路径（默认现行那份；红验证指向改动前的备份）")
    ap.add_argument("--list-bad", action="store_true", help="只打印红验证的跑法")
    a = ap.parse_args(argv)

    if a.list_bad:
        print("红验证（拿只按标题匹配的旧实现跑）：")
        print(r'  python -X utf8 tests_P0_自指保护判据.py --impl "%s"' % BAD_IMPL)
        print("  ⇒ 断言 1 必红：标题无害的终端窗口被放行了。")
        return 0

    print("=" * 74)
    print("P0 自指保护判据 · 被测实现 %s" % a.impl)
    print("=" * 74, flush=True)
    if not os.path.isfile(a.impl):
        print("[红] 被测实现不在：%s" % a.impl)
        return 1
    try:
        act = load_act(a.impl)
    except SystemExit as e:
        print("[红] 被测实现 import 时自己退出了（rc=%s）—— 这也算红" % e.code)
        return 1
    except Exception as e:
        print("[红] 被测实现 import 不了：%s: %s" % (type(e).__name__, e))
        return 1

    # ★ 双保险之一：atexit。任何 sys.exit / 未捕获异常 / Ctrl-C 都走得到收尾。
    atexit.register(reap_all)
    atexit.register(cleanup_self_script)   # 窗O4：自造窗脚本也别留在 %TEMP%

    # ★ 第三条：Job Object（顶住 taskkill /F、CTRL_BREAK 这种「进程直接没了」）
    #   第四条：看门狗（顶住判据本体 hang 住）
    job = start_kill_on_close_job()

    # ★ 窗O2：先把残留扫描作用域钉死，并记住跑之前就活着的进程 ——
    #   收尾只准动本轮起的，绝不许碰用户 WindowsTerminal.exe 那种共享宿主。
    desk, desk_note = anchor_desktop()
    for pr in psutil.process_iter(["pid"]):
        BASELINE_PIDS.add(pr.info["pid"])
    print("  [作用域] 残留扫描桌面 = %r —— %s" % (desk, desk_note))
    print("  [夹具] Job Object（判据一死夹具跟着死）：%s；看门狗 %ds；本轮标记 %s"
          % ("已启用" if job else "★建不上，已降级", FIXTURE_TTL, MARKER), flush=True)

    # ══════════ 窗O3 · 前置断言：不带着「别人在动」的桌面往下跑 ══════════
    section("前置 · 桌面是不是有人在用（有人在用 ⇒ 验不了，rc=2，绝不带着往下断言）")
    quiet, busy = precondition()
    for why in busy:
        print("    占用：%s" % why)
    if not quiet:
        unverifiable("P0 前置：桌面安静，没人和本跑抢桌面",
                     "环境被外部占用 —— %d 条占用信号（见上）；"
                     "**这不是红也不是绿**，本跑不往下断言" % len(busy))
        print("  ⇒ 请等它跑完（或按上面列出的线索手工清掉）再重跑；"
              "判据不代劳去动别人的进程/窗口。")
        return 2
    print("  桌面安静：没有别的判据、没有本项目自证弹窗、没有别的跑的夹具在动")

    try:
        host_before_map = app_windows()
        host_before = len(host_before_map)
    except ScanUnavailable as e:
        host_before_map, host_before = {}, -1
        print("  [!] 开跑前数不了桌面：%s" % e)

    section("断言 0 · 开跑前先清上一轮的残留（带着残留跑 = 让判据变随机数）")
    print("  残留策略：%s" % RESIDUE_POLICY_TEXT)
    print("  跑之前桌面上**别人的**应用窗：%s 个"
          "（只作对照 —— 它们不是我们的残留，也不是我们的指标）" % host_before)
    seen, left, st, st_end = sweep_residue()
    print("  首次扫描状态：%s；检出残留 %d 条 → 清完还剩 %d 条（收尾状态 %s）"
          % (st, len(seen), len(left), st_end))
    for r in left:
        print("    还赖着：%s" % r)
    check("0z 残留扫描有明确作用域且不是「没法扫」（桌面 %r，状态 %s）" % (desk, st),
          st != CANNOT_SCAN, "桌面状态不可知 ⇒ 验不了，不许当绿")
    check("0a 开跑前残留夹具 = 0（检出 %d 条，已清）" % len(seen), not left,
          "还赖着 %d 条：%s ⇒ 环境不干净，先清残留再跑" % (len(left), left))
    if left or st == CANNOT_SCAN:
        print("  ⇒ 按策略「收不掉 / 没法扫就报红停手」：判据不带着残留往下跑。")
        return summary()

    section("断言 0s · 残留检测是不是真在工作（先埋一个，再看它抓不抓得着）")
    plant_pid, plant_h = plant_leftover(job)
    ti0 = describe(plant_h) if plant_h else None
    print("  埋的残留：pid=%s hwnd=%s%s"
          % (plant_pid, plant_h,
             ("（类名 %s / 标题 %r / 属主 %s）"
              % (ti0["class"], ti0["title"], ti0["process"]))
             if ti0 else "（窗没自己冒出来 —— 检测只能靠进程侧，这就是要分三态的地方）"))
    seen2, left2, st2, st2_end = sweep_residue(family_of([plant_pid]))
    print("  首次扫描状态：%s；检出 %d 条 → 清完还剩 %d 条（收尾状态 %s）"
          % (st2, len(seen2), len(left2), st2_end))
    for r in seen2:
        print("    检出：%s" % r)
    check("0s-a 埋进去的残留**被检出**（认出 %d 条，检出时扫描状态 %s）" % (len(seen2), st2),
          st2 == FOUND and len(seen2) >= 1,
          "检出时扫描状态 %s、认出 %d 条 ⇒ 残留检测认不出来；认不出来不是桌面干净"
          % (st2, len(seen2)))
    check("0s-b 检出之后**清干净**（清掉 %d，剩 %d）" % (len(seen2), len(left2)),
          st2 != CANNOT_SCAN and not left2,
          "还赖着 %d 条：%s" % (len(left2), left2))
    # ★ 窗O3：进程退出是**异步**的（刚 kill 完就查，必然查到还活着）—— 轮询等它真退。
    _pgone, _pwaited = wait_pid_gone(plant_pid)
    check("0s-c 埋的进程真的退出了（pid %s，轮询 %.2fs）" % (plant_pid, _pwaited),
          _pgone,
          "等了 %.1fs 进程还活着 —— 只是把窗藏了，不是收干净" % HWND_WAIT)
    check("0s-d 走的是**先清后跑**这条路（策略 %r；清不掉才报红）" % RESIDUE_POLICY,
          RESIDUE_POLICY == "clean", "策略被改成 %r 了" % RESIDUE_POLICY)
    reap_all()

    fx = Fixtures(job)
    watchdog = start_watchdog()
    started = {}
    started_hwnds = {}
    plant_pid = plant_pid
    try:
        term, app = fx.start()
        # ★ 窗O4：**进程一律登记**，窗认到了再补 hwnd。
        #   O3 是「认到窗才登记」，那意味着「夹具进程起了但窗没冒出来」时
        #   那个进程就没人管了 —— 泄漏了也不算数。残留只认我们自己的，
        #   但**自己起过的每一个进程都得进册子**。
        if fx.term_pid:
            started["终端夹具"] = fx.term_pid
        if fx.app_pid:
            started["自造窗夹具"] = fx.app_pid
        if term:
            started_hwnds["终端夹具"] = term
        if app:
            started_hwnds["自造窗夹具"] = app
        section("现场：判据自己起的两个真窗口（寿命上限 %ds，收尾见断言 6）" % FIXTURE_TTL)
        print("  终端夹具：", describe(term) if term else "（没起起来）")
        print("  自造窗：  ", describe(app) if app else "（没起起来）")
        if not app:
            print("  自造窗子进程状态文件：%s" % (self_status_text() or "（读不到）"))

        section("断言 1 · 终端类窗口（标题故意无害）必须被拦")
        if not term:
            check("1a 终端夹具窗口出现", False,
                  "没等到终端窗口（cmd.exe 起不来？还是夹具 %ds 寿命到点自己退了？）"
                  % FIXTURE_TTL)
        else:
            ti = describe(term)
            check("1a 终端夹具窗口出现（类名 %s / 属主 %s）"
                  % (ti["class"], ti["process"]), True)
            check("1a-0 夹具标题确实**无害**（不含任何黑名单关键词）",
                  not any(b in ti["title"] for b in tuple(getattr(act, "BLOCKED_TITLES", ())))
                  and "授权" not in ti["title"] and "急停" not in ti["title"],
                  "标题=%r" % ti["title"])
            info, why = act.check_target(term)
            check("1b 无害标题的终端窗口**被拦**", bool(why),
                  "check_target 放行了（why=%r）—— 这正是只认标题那道闸门漏掉的情形" % (why,))
            check("1c 拒绝理由落在**身份**上（属主进程名 / 窗口类名），不是标题",
                  ("属主进程" in (why or "")) or ("类名" in (why or "")),
                  "理由=%r" % (why or "")[:120])

        section("断言 1s · 负向清单按字面登记（改名单这条立刻红）")
        hard = tuple(str(x).lower() for x in getattr(act, "BLOCKED_PROCESSES_HARD", ()))
        terml = tuple(str(x).lower() for x in getattr(act, "BLOCKED_PROCESSES_TERMINAL", ()))
        for p in ("consent.exe", "systemsettings.exe"):
            check("1s-a 硬拒进程名含 %s" % p, p in hard, "现有=%s" % (hard,))
        for p in ("cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
                  "wt.exe", "windowsterminal.exe", "openconsole.exe"):
            check("1s-b 终端负向名单含 %s" % p, p in terml, "现有=%s" % (terml,))
        check("1s-c 授权弹窗标题仍在 BLOCKED_TITLES 里（兜底层不许删）",
              AUTH_TITLE in tuple(getattr(act, "BLOCKED_TITLES", ())),
              "现有=%s" % (getattr(act, "BLOCKED_TITLES", None),))

        section("断言 2 · 只用标题（属主进程与类名都无关）也必须被拦")
        if not app:
            check("2a 自造窗夹具出现（类名 %s）" % SELF_FIXTURE_CLASS, False,
                  "没等到本跑自造窗（pid %s）—— 夹具起不来就是**没测到**，"
                  "绝不当成「桌面干净」；子进程状态：%s"
                  % (fx.app_pid, self_status_text() or "（读不到）"))
        else:
            ni = describe(app)
            # ★ 窗O4 加的一条：夹具**必须可见**。
            #   把它做成隐形/隐藏窗来换「跑得顺」是不许的 —— 可见性是要件
            #   （窗O2 已提醒过一次），判据自己不许犯这个。
            check("2a-0 自造窗**确实可见**（不许做隐形窗换绿）",
                  bool(win32gui.IsWindowVisible(app)),
                  "IsWindowVisible=False —— 夹具被藏起来了，这不算测到了真窗口")
            check("2a 自造窗夹具出现（属主 %s / 类名 %s，不在任何名单里）"
                  % (ni["process"], ni["class"]),
                  ni["process"].lower() not in hard + terml
                  and ni["class"] not in tuple(getattr(act, "BLOCKED_CLASSES_TERMINAL", ()))
                  and ni["class"] == SELF_FIXTURE_CLASS)
            # ══════════ 窗O3 · 2b：先确认「标题真的落上了」，再断言闸门 ══════════
            #   改标题是异步的，句柄本身也可能被换掉。改完立刻断言 = 把
            #   「我没测到」记成「没拦住」—— 那是假警报，比漏报更坏。
            #   所以：轮询等标题落到**属主是本跑自造窗**的活窗上（≤TITLE_WAIT）；
            #         等不到 ⇒ 验不了，**绝不**报「被放行」。
            landed = waited = None
            try:
                win32gui.SetWindowText(app, AUTH_TITLE)
                landed, waited = wait_title_on_fixture(
                    [fx.app_pid], AUTH_TITLE, hwnds=(app,))
                if landed is None:
                    unverifiable(
                        "2b 只改标题（属主/类名仍是普通应用）⇒ **被拦**",
                        "标题 %r 在 %.1fs 内没落到本跑自造窗的任何窗口上 ⇒ "
                        "**我没测到**（不是「没拦住」）；句柄=%s 现在标题=%r"
                        % (AUTH_TITLE, waited, app,
                           win_attr(app, win32gui.GetWindowText)))
                else:
                    info2, why2 = act.check_target(landed)
                    held = AUTH_TITLE in (win_attr(landed, win32gui.GetWindowText) or "")
                    if not held:
                        unverifiable(
                            "2b 只改标题（属主/类名仍是普通应用）⇒ **被拦**",
                            "标题在断言过程中被改回去了（断言后 %r）⇒ "
                            "**我没测到**，不记「放行了」"
                            % (win_attr(landed, win32gui.GetWindowText),))
                    else:
                        check("2b 只改标题（属主/类名仍是普通应用）⇒ **被拦**"
                              "（标题已落到 hwnd %s，用时 %.2fs）" % (landed, waited),
                              bool(why2),
                              "check_target 放行了（why=%r）—— 这正是只认标题"
                              "那道闸门漏掉的情形" % (why2,))
            finally:
                # 复原标题（同样轮询，不靠 sleep 赌），供断言 3 用
                win32gui.SetWindowText(landed or app, fx.app_title0)
                fx.app_now, _ = wait_title_on_fixture(
                    [fx.app_pid], fx.app_title0, hwnds=(landed or app,))

        section("断言 3 · 普通应用必须放行（别把正常活也挡了）")
        app3 = getattr(fx, "app_now", None) or app
        cur3 = win_attr(app3, win32gui.GetWindowText)
        if app3 and hwnd_alive(app3) and cur3 == fx.app_title0:
            info3, why3 = act.check_target(app3)
            check("3a 自造窗（标题已复原为 %r）⇒ 放行" % fx.app_title0, why3 is None,
                  "被拒了：%s" % (why3,))
        else:
            unverifiable("3a 自造窗（标题复原为 %r）⇒ 放行" % fx.app_title0,
                         "标题没复原成功 / 窗不在了（hwnd=%s 现在标题=%r）⇒ "
                         "**我没测到**放行这一路" % (app3, cur3))

        section("断言 4 · 终端的 30 分钟临时放行：确认则过、过期再拦、且留事件")
        grant = getattr(act, "grant_terminal_pass", None)
        if not term or not grant:
            check("4a 被测实现有临时放行这条路（grant_terminal_pass）", False,
                  "这份实现压根没有 —— 终端要么永远不许碰、要么一刀切放行，两样都不对")
        elif not app:
            check("4a 临时放行", False,
                  "自造窗夹具没起来（pid %s）；子进程状态：%s"
                  % (fx.app_pid, self_status_text() or "（读不到）"))
        else:
            ti = describe(term)
            ok, msg = grant(ti["process"], ti["class"])
            check("4a 用户确认后签发 30 分钟临时放行（%s / %s）" % (ti["process"], ti["class"]),
                  bool(ok), str(msg))
            info4, why4 = act.check_target(term)
            check("4b 临时放行后**同一个终端窗口**放行", why4 is None, "仍被拒：%s" % (why4,))
            ev = []
            if os.path.exists(PASSDB + ".jsonl"):
                with open(PASSDB + ".jsonl", encoding="utf-8") as f:
                    ev = [json.loads(x) for x in f if x.strip()]
            check("4c 放行**写了事件**（%s）" % os.path.basename(PASSDB + ".jsonl"),
                  any(e.get("event") == "terminal_pass_granted"
                      and e.get("process") == ti["process"].lower() for e in ev),
                  "事件内容=%s" % (ev,))
            # 时间旅行：把到期时间挪到 31 分钟前 ≡ 已经过去 30 分钟。
            # 真等 30 分钟不叫判据，叫发呆。
            try:
                with open(PASSDB, encoding="utf-8") as f:
                    db = json.load(f)
                for p in db.get("passes", []):
                    p["expires_at"] = float(time.time()) - 31 * 60
                    p["expires_at_h"] = "（判据把时间拨到 31 分钟前）"
                with open(PASSDB, "w", encoding="utf-8") as f:
                    json.dump(db, f, ensure_ascii=False, indent=1)
            except Exception as e:
                check("4d 造一条过期记录", False, "%s: %s" % (type(e).__name__, e))
            info5, why5 = act.check_target(term)
            check("4d 30 分钟过期后**重新被拦**", bool(why5),
                  "过期了还放行 —— 这就成永久白名单了")
            check("4e 过期的拒绝理由仍是**终端那一族**", is_terminal_reason(why5),
                  "理由=%r" % (why5,))

        section("断言 5 · 授权弹窗那一族**没有**临时放行这条路")
        if not grant:
            check("5a 临时放行只对终端开放", False, "这份实现没有 grant_terminal_pass")
        else:
            for proc, cls in (("python.exe", "TkTopLevel"), ("consent.exe", ""),
                              ("SystemSettings.exe", "")):
                ok, msg = grant(proc, cls)
                check("5a %s / %s 拒签" % (proc, cls or "(无类名)"), not ok,
                      "居然签发了：%s" % msg)
    finally:
        # ★ 双保险之二：finally。上面任何一条红、任何一次异常、Ctrl-C，都走到这儿。
        print("\n  [收尾] 关窗 + 杀整棵进程树 + 确认进程退出 …")
        watchdog.cancel()
        reap_all(verbose=True)
        for p in (PASSDB, PASSDB + ".jsonl"):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        cleanup_self_script()   # 窗O4：自造窗脚本/状态文件不留

    section("断言 6 · 收尾自证：本次起过的夹具窗一个都不剩")
    reap_all()
    st6, rows6 = scan_residue(family_of(list(started.values()) + [plant_pid]))
    own_pids = set(int(p) for p in list(started.values()) + [plant_pid] if p)
    try:
        host_after_map = app_windows(own_pids)
        host_after = len(host_after_map)
    except ScanUnavailable as e:
        host_after_map, host_after = {}, -1
        print("  [!] 收尾后数不了桌面：%s" % e)
    print("  收尾后扫描状态：%s；残留 %d 条" % (st6, len(rows6)))
    for r in rows6:
        print("    残留：%s" % r)
    check("6a 桌面（%r）上按「本跑自造窗类名 / 标题键 / 本轮标记 / 属主进程族 / 三种终端宿主类」"
          "枚举的夹具 = 0（状态 %s）" % (DESKTOP, st6),
          st6 == CLEAN and not rows6,
          "还剩 %d 条：%s" % (len(rows6), rows6))
    alive = ["%s(pid %s)" % (k, v) for k, v in sorted(started.items()) if pid_alive(v)]
    check("6b 本次起过的夹具进程全部退出（%s）"
          % ("、".join("%s=%s" % (k, v) for k, v in sorted(started.items())) or "（没起来）"),
          not alive, "还活着：%s" % alive)
    # ══════════ 窗O3 · 6c：HWND 失效是**异步**的，查一次就下结论必然飘 ══════════
    #   轮询 ≤HWND_WAIT；等不到再分两种：
    #     · 属主还是本跑夹具 ⇒ **真残留**，红；
    #     · 属主已经换了人   ⇒ 句柄被别的窗复用了，**不是我们的残留** ⇒ 验不了。
    ghost, ghost_other, hw_waits = [], [], []
    for k, v in sorted(started_hwnds.items()):
        _gone, _hw = wait_hwnd_gone(v, HWND_WAIT)
        hw_waits.append("%s=%.1fs" % (k, _hw))
        if _gone:
            continue
        if owner_pid(v) in own_pids:
            ghost.append("%s(hwnd %s，仍属本跑夹具)" % (k, v))
        else:
            ghost_other.append("%s(hwnd %s，现在属 %s) —— 句柄被外部窗复用了"
                               % (k, v, owner_of(v)))
    c6 = ("6c 本次起过的夹具窗口 HWND 全部失效（轮询上限 %.1fs：%s）"
          % (HWND_WAIT, "、".join(hw_waits)))
    if ghost:
        check(c6, False, "还活着：%s" % ghost)
    elif ghost_other:
        unverifiable(c6, "句柄被外部窗复用（不是我们的残留，但也证不了窗没了）：%s"
                     % ghost_other)
    else:
        check(c6, True)
    # ══════════ 窗O4 · 6d：**分清「我的残留」与「别人的窗」，各算各的** ══════════
    #   ⛔ O3 那句「桌面上的终端宿主窗 + 记事本没变多」已**废**：
    #     那是**全桌面量** —— 用户开不开记事本都会让它变，拿它当指标天生会飘，
    #     而且 O3 亲跑第 2 次就是被它坑成 rc=2 的。
    #   ⇒ 现在的口径：**我们的残留 = 按本跑自造窗类名/属主 pid 族/轮次标记
    #     认出来、且跑完还在的窗**，一个都不许有（这是指标）；
    #     别人的应用窗**单独数**，只用来判断「跑的过程中是不是有人在用桌面」：
    #       · 新增窗全是我们的 ⇒ 红（真残留）；
    #       · 新增窗里有**外来**的 ⇒ 验不了（环境被外部占用），绝不记红。
    new_hwnds = []
    for h, info in host_after_map.items():
        old_info = host_before_map.get(h)
        if old_info is None or old_info[0] != info[0]:
            new_hwnds.append((h, info))
    ours = [(h, i) for h, i in new_hwnds if host_is_ours(i, own_pids)]
    foreign = [(h, i) for h, i in new_hwnds if not host_is_ours(i, own_pids)]
    c6d = ("6d 本跑起过的窗一个都不剩（我的残留 %d 个）；别人的应用窗 跑前 %d → 跑后 %d，"
           "其中跑的过程中新开了 %d 个 —— 这些**不是**我的残留"
           % (len(ours), host_before, host_after, len(foreign)))
    if host_after < 0:
        unverifiable(c6d, "收尾后桌面状态不可知 ⇒ 验不了，不当绿也不当红")
    elif foreign:
        unverifiable(c6d, "跑后多出来 %d 个**外来**窗（属主不在本跑进程族、"
                     "标题不带本轮标记/标题键）⇒ 环境被外部占用，"
                     "判据不作残留结论：%s"
                     % (len(foreign),
                        ["hwnd %s 属主 %s %s %r" % (h, i[0], i[1], i[2])
                         for h, i in foreign]))
    elif ours:
        check(c6d, False, "多出来 %s 个且**全是本跑夹具** ⇒ 夹具赖着没走：%s"
              % (len(ours), ["hwnd %s %s %r" % (h, i[1], i[2]) for h, i in ours]))
    else:
        check(c6d, True)
    app_left = [r for r in rows6 if r.get("class") == SELF_FIXTURE_CLASS]
    check("6e 本次起的自造窗一个都不剩（类名 %s，pid %s）"
          % (SELF_FIXTURE_CLASS, fx.app_pid), not app_left,
          "还剩 %d 个：%s" % (len(app_left), app_left))
    check("6f 残留结论有明确作用域（桌面 %r）且不是「没法扫」" % DESKTOP,
          st6 != CANNOT_SCAN, "桌面状态不可知 ⇒ 验不了")

    return summary()


if __name__ == "__main__":
    sys.exit(main())
