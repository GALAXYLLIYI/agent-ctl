# -*- coding: utf-8 -*-
r"""急停置顶弹窗判据（窗I · 2026-10-16）

================================ 它在守什么 ================================
急停（brake）拉下那一刻，必须弹一张**桌面置顶**的窗，写清
「是谁在动 / 它的 MCP 链接 / 怎么恢复」，并且：

    ① N 秒内真的出现（按窗口标题/类名枚举**真实 HWND**，不是问实现「你觉得弹了吗」）
    ② 它真的是置顶的（GetWindowLong(GWL_EXSTYLE) & WS_EX_TOPMOST 实读那一位）
    ③ 它上面**没有任何能改变急停状态的控件**（枚举子 HWND 取 BUTTON 文本，
       命中 解除/恢复/清除/release/clear/resume 即红）—— 这是安全硬约束①
    ④ 解除急停后它自动消失
    ⑤ 三段内容齐全，且「是谁」来自夹具里的真实 actor（取不到必须如实写「未知」）
    ⑥ 它的标题**和类名**都登记在 legacy_tools\act.py 的 BLOCKED_TITLES / BLOCKED_CLASSES
       —— 这是安全硬约束②，漏了 agent 就能点掉它，它就从安全提示变成自授权口子
    ⑦ MCP 那条**可选中复制**（复制往返 + 正文里逐字含 config.toml 里那条 command）

================================ 窗N（2026-10-16）追加 ==============================
⑨ 这张窗在**状态源不是生产路径**时，必须**自报「自证测试 / 非真实急停」**，
   并把那个沙箱路径原样印在窗上；状态源是生产路径时，**一个标注都不许有**。

★ 为什么加这一组：窗口A（GLM）在真桌面上看到本判据弹的窗，**认不出是测试还是真急停**，
  按「解除前先确认是谁在动」的规矩做了五路只读取证，最后差点建议跑
  `brake --action release` —— 真跑了只会往**真**日志里写一条假的「已解除」记录。

★★ 口径只有一条：**状态源路径**。不许按 actor 名字猜（不许出现「名字里含『夹具』
   就算测试」这种启发式）—— 第 9j 条就是盯着这一条的。
   反向同样危险：真急停若被误标成「自证测试」，有人就会放过一个真的急停。

★ 这条判据会**真的弹一次窗**（约 3 秒，自己收掉）。它验的是桌面行为，
  绕不开 —— 跑完窗口一定自己隐藏，进程退出时一定销毁。

================================ 窗P2（2026-10-16）追加 ==============================
⑩ **三条入口，三种期望** —— 母单的缺口是「文档入口从未进判据」（当时第 ⑧ 条起的是
   `python -c "from agentctl import brake_daemon as b; b.run()"`，只测了 src 那一份）。
   更正之后是三条路、三种期望，缺一条那个缺口就换个形状回来：
     · A = `legacy_tools\brake.py --daemon`（窗Q2 已退役）⇒ 必须**响亮拒绝**：
       rc=2 + 说清自己退役了 + 指向新线入口；且**不常驻、不弹窗、不偷偷拉闸**，
       同时 stop/release/status 三个手工通道**一条不许被连累**。
     · B = `python -X utf8 src\agentctl_main.py brake-daemon`（更正(b)：**真·文档入口**）
       ⇒ 必须真起 daemon，拉闸**弹窗**、置顶、正文有内容、解除后消失。
     · C = `python -X utf8 -m agentctl.brake_daemon`（更正(a)：此前**没有 __main__**，
       import 完就秒退）⇒ 必须是 B 的等价转发，钉的就是「不许再秒退」。
   另有 ⑧'4 抢占必须显式（第二份 daemon 抢不到热键要 rc=2 + 出声 + 留痕）
   与 ⑩ 收尾自证（flag 不存在 / 可见弹窗 0 个 / daemon 进程 0 个）。

★★ **内容证据分两档，别再栽在这上面**：跨进程 `GetWindowTextW` 读 EDIT 控件
   **必然读到空**（该 API 只保证拿自己进程的控件），而标准 EDIT 的正文
   `SendMessage(WM_GETTEXT)` 是**跨进程编组的**。实测同一张窗：GWT='' 而 WM=1969 字。
   本地 agent 与 窗口A 曾各自据「GWT 读到空」判「正文没渲染」—— **两次都错了**。
   本判据因此：**先轮询 → 档①GWT → 档②跨进程 WM_GETTEXT → 都空才落像素**；
   而**像素那一档只报「验不了」，永不单独给人判红**（启发式没有资格判红：
   窗N3 那条「非底色像素占比」在深色主题下 100% ⇒ 把画满的窗判成了没画）。

================================ 怎么跑 ========================================
    python -X utf8 tests\_急停弹窗判据.py                    # 绿（好实现）
    python -X utf8 tests\_急停弹窗判据.py --bad notopmost   # 红（去掉置顶）
    python -X utf8 tests\_急停弹窗判据.py --bad release     # 红（多挂一个「解除急停」按钮）
    python -X utf8 tests\_急停弹窗判据.py --bad none        # 红（干脆不弹）
    python -X utf8 tests\_急停弹窗判据.py --bad sticky      # 红（解除后不消失）
    python -X utf8 tests\_急停弹窗判据.py --bad nomarker    # 红（沙箱状态源却不自报身份）
    python -X utf8 tests\_急停弹窗判据.py --list-bad       # 打印红验证的完整跑法

★ 红验证怎么做：坏实现在本文件内的 _BadBackend，它**复用真源的 Win32 那层**，
  只抽掉/加坏那一处（去掉 WS_EX_TOPMOST / 多挂一个「解除急停」按钮 / hide 空转 /
  根本不弹）。这样红是「就那一处坏了」的红，不是「整个实现是另写的」的红。
"""
from __future__ import annotations

import argparse
import ast
import ctypes
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "src")
ACT = os.path.join(REPO, "legacy_tools", "act.py")

# ── 冻结期望值（改实现必须同步改这里；改这里也必须重跑一遍红验证）──
FROZEN_CLASS = "AgentCtlBrakePopup"
FROZEN_TITLE = "⛔ AgentCtl 急停 · 所有动作已停止"
FROZEN_BLOCK_TITLE_KEY = "AgentCtl 急停"     # 须与 act.py BLOCKED_TITLES 某项逐字相同
FROZEN_BLOCK_CLASS = "AgentCtlBrakePopup"    # 须与 act.py BLOCKED_CLASSES 某项逐字相同
POPUP_DEADLINE_SEC = 5.0      # ①「N 秒内」里的 N
STAY_SEC = 2.0                # ① 急停期间不许自动消失：至少还在 N 秒
GONE_DEADLINE_SEC = 5.0       # ④ 解除后多久之内必须消失
FROZEN_SECTIONS = ("① 是谁在动（actor / session_id）", "② 它的 MCP 链接", "③ 怎么恢复")
FROZEN_ACTOR = "窗I夹具agent"              # 夹具时间线里写死的 actor
FROZEN_SESSION = "sess-窗I-0001"
# ③ 控件文本里出现这些词 = 能改状态的控件 —— 命中即红
FORBIDDEN_VERBS = ("解除", "恢复", "清除", "release", "clear", "resume", "reengage")
WS_EX_TOPMOST = 0x00000008
GWL_EXSTYLE = -20

# ── 窗P2（2026-10-16）追加的冻结期望值：三条入口各自「应该是什么」──────
#
# ★ 为什么现在才有「三条」：母单时代只有两条（老线 brake.py --daemon / 新线 -m），
#   更正(b) 点明**真正被文档写下来的那条**是 `src\agentctl_main.py brake-daemon`，
#   而更正(c) 记下老线那条已被窗Q2 **退役**（rc=2 + 出声）。于是三条入口的期望各不相同：
#     A = legacy_tools\brake.py --daemon   → 必须**响亮拒绝**（rc=2 + 指向新线），
#                                         绝不许常驻、绝不许弹窗、绝不许偷偷拉闸；
#     B = src\agentctl_main.py brake-daemon → 必须真起 daemon，拉闸**弹窗**；
#     C = -m agentctl.brake_daemon          → 必须是 B 的**等价转发**，
#                                         钉的就是「不许再秒退」（更正(a)）。
#   ★ 三条都验，才不会重演「一个功能两份实现、而文档那条不弹窗」的病。
ENTRY_A = ("legacy_tools/brake.py", "--daemon")       # 退役线
ENTRY_B = ("src/agentctl_main.py", "brake-daemon")      # 真·文档入口
ENTRY_C = ("-m", "agentctl.brake_daemon")               # 模块入口（更正(a) 的病根）

# 急停 daemon 的**轮询间隔是 2 秒**（brake_daemon.py:168 `now - last_poll >= 2`）。
# ★ 派单要求：观察「没有发生什么」的时间**至少 2 倍**这个间隔，否则会把
#   「还没轮到它轮询」读成「它没反应」。这里取 3 倍（6 秒），留一次余量。
DAEMON_POLL_SEC = 2.0
OBSERVE_SEC = DAEMON_POLL_SEC * 3            # 6.0
# 「它得弹出来」是**有**事件发生：daemon 最多 2s 后才发现 + 建窗 + 跨进程读文本，
# 实测 1.8~2.2 秒 ⇒ 给 5 倍轮询间隔。
POPUP_E2E_DEADLINE_SEC = DAEMON_POLL_SEC * 5  # 10.0
# 「秒退」的判据：比一次轮询还快就等于没活过（更正(a) 那个 bug 就是 0.09 秒退了）。
SILENT_EXIT_SEC = DAEMON_POLL_SEC

EDIT_CLASS = "EDIT"          # 正文控件
WM_GETTEXT, WM_GETTEXTLENGTH = 0x000D, 0x000E
HOTKEY_FAIL_MARKS = ("注册失败", "可能被别的程序占")   # 抢占时必须出现的那句话

# ── 窗N（2026-10-16）追加的冻结期望值：弹窗必须自报身份 ──────────────
#
# ★ 口径只有一条：**状态源路径**。
#   · 状态源落在生产根（%LOCALAPPDATA%\AgentCtl\）下 ⇒ **真急停** ⇒ 保持原样，一个标记都不许有；
#   · 落在别处（%TEMP%、work\_*沙箱*、AGENTCTL_BRAKE_FLAG 覆盖到别处）⇒ **自证测试**
#     ⇒ 标题与正文都要出现下面这两个标记，且正文必须印出那个沙箱路径。
# ⛔ 不许按 actor 名字猜真假（判据 9j 盯着这一条）。反向同样危险：
#   真急停被误标成测试 ⇒ 有人会放过一个真的急停。
SELFTEST_MARKS = ("【自证测试】", "非真实急停")
PROD_ROOT_NAME = "AgentCtl"
PROD_FLAG_NAME = "_急停.flag"

# ── 窗R（2026-10-16）追加的冻结期望值：幽灵急停窗必须自曝 ──────────────
#
# 本组存在的理由（派单 work/_2h_wR_派单.md）：本判据过去只沙箱化**时间线**，
# 漏设 AGENTCTL_BRAKE_FLAG ⇒ 路径回退到默认值 = **恰好是生产路径** ⇒
# 窗N 的「按路径自报」把它判成真急停 ⇒ 桌面上那张窗跟真急停一模一样，
# 而真急停**从未存在**。看窗的人会当真急停去处理。
#
# 判定口径**一条都不许改**：路径仍然是唯一依据，绝不许改成「按 actor 名字猜」
# （窗N 红线，判据 9j/9k 盯着）。窗R 只要求把「路径所指的状态并不存在」
# 这件**事实**如实印在窗上。
GHOST_MARK = "该状态源文件当前不存在"   # 产品侧那行异常标注的锚点子串
GHOST_TAIL = "未沙箱化的测试进程"          # 同一行的后半句，一并钉住（免得只剩半句废话）

# 真·生产锚点：在**任何沙箱化动作之前**先把真的 %LOCALAPPDATA% 记下来。
# 判据随后会把 LOCALAPPDATA 挪到沙箱（挪了之后 production_root() 就是沙箱根），
# 而收尾自证要拿真路径去查「生产 flag 仍然不存在」—— 挪过之后就算不出真路径了。
_REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
_REAL_PROD_ROOT = os.path.join(_REAL_LOCALAPPDATA, PROD_ROOT_NAME)
_REAL_PROD_FLAG = os.path.join(_REAL_PROD_ROOT, PROD_FLAG_NAME)

# 生产目录的「跑前快照」：(名字, 大小, mtime) 逐项 —— 收尾时逐项比对，
# 多出任何一个文件都判红。只记名字不够（同名覆盖看不出来），只记大小也不够。
_PROD_SNAP = None

OK, BAD, SKIP = [], [], []
NL = chr(10)          # 拼多行文本用；源码里不写死换行，免得 PowerShell 抽风


def section(t):
    print("\n" + "=" * 74 + "\n" + t + "\n" + "=" * 74, flush=True)


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("绿" if cond else "红", name,
                           ("  ← %s" % detail) if (detail and not cond) else ""),
          flush=True)
    return bool(cond)

# ── 枚举真实窗口（黑盒：不问实现「你觉得弹了吗」，自己去桌面上找）────
#
# ⚠ 这里**故意不用** ctypes.windll.user32：那个对象是全局缓存的，
#   任何 import 过 brake_popup 的模块都会改掉同一批函数的 argtypes，
#   改完判据的回调类型就对不上了（报 “expected WinFunctionType instance”）。
#   自己拿一个私有 CDLL，谁也动不着。
_u = ctypes.WinDLL("user32", use_last_error=False)
_u.GetWindowTextLengthW.restype = ctypes.c_int
_u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
_u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_u.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_u.IsWindowVisible.argtypes = [ctypes.c_void_p]
_u.GetWindowLongW.restype = ctypes.c_long
_u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
# ⚠ WINFUNCTYPE 每调一次返回**一个新的类型** —— 声明原型和建回调必须用同一个，
#   否则 ctypes 报 “expected WinFunctionType instance instead of WinFunctionType”。
_ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
_u.EnumWindows.argtypes = [_ENUMPROC, ctypes.c_ssize_t]
_u.EnumChildWindows.argtypes = [ctypes.c_void_p, _ENUMPROC, ctypes.c_ssize_t]


def enum_windows():
    out = []

    def _cb(h, _l):
        out.append(int(h))
        return True

    _u.EnumWindows(_ENUMPROC(_cb), 0)
    return out


def enum_children(h):
    out = []

    def _cb(ch, _l):
        out.append(int(ch))
        return True

    _u.EnumChildWindows(ctypes.c_void_p(h), _ENUMPROC(_cb), 0)
    return out


def wtext(h):
    n = _u.GetWindowTextLengthW(ctypes.c_void_p(h))
    buf = ctypes.create_unicode_buffer(max(n + 2, 8))
    _u.GetWindowTextW(ctypes.c_void_p(h), buf, len(buf))
    return buf.value


def wcls(h):
    buf = ctypes.create_unicode_buffer(256)
    _u.GetClassNameW(ctypes.c_void_p(h), buf, 256)
    return buf.value


def exstyle(h):
    return int(_u.GetWindowLongW(ctypes.c_void_p(h), GWL_EXSTYLE)) & 0xFFFFFFFF


def is_visible(h):
    return bool(_u.IsWindowVisible(ctypes.c_void_p(h)))


def find_popup():
    """按**标题或类名**找那个可见的窗（派单原话：按窗口标题/类名枚举）。"""
    for h in enum_windows():
        if not is_visible(h):
            continue
        if wtext(h) == FROZEN_TITLE or wcls(h) == FROZEN_CLASS:
            return h
    return 0


def child_buttons(h):
    """真子 HWND 里 BUTTON 类控件的文本（黑盒：判据不碰实现的内部状态）。"""
    return [wtext(c) for c in enum_children(h) if wcls(c) == "Button"]


def wait_popup(sec):
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        h = find_popup()
        if h:
            return h, time.monotonic() - t0
        time.sleep(0.05)
    return 0, time.monotonic() - t0


def wait_gone(sec):
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        if not find_popup():
            return True, time.monotonic() - t0
        time.sleep(0.05)
    return False, time.monotonic() - t0


# ── 静态读 act.py 的两个黑名单 ──────────────────────────────────────
#
# 为什么不 import act.py：它 import 就要求先有一份合格的 legacy home
# （gate.py 的 T7 体检），判据不该被部署状态绑架。用 ast 静态读，
# 顺带保证「判据只看那一行字，不看实现怎么用」。
def read_blocklists():
    try:
        src = open(ACT, encoding="utf-8").read()
    except OSError as e:
        return None, None, repr(e)
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return None, None, repr(e)
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in ("BLOCKED_TITLES", "BLOCKED_CLASSES"):
                try:
                    found[t.id] = ast.literal_eval(node.value)
                except Exception:
                    found[t.id] = None
    return found.get("BLOCKED_TITLES"), found.get("BLOCKED_CLASSES"), None


# ── 夹具：一条带 actor 的时间线事件（让「是谁」那条有真东西可读）─────
def make_fixture_timeline():
    """造一个只含一条事件的临时时间线，并让 brake_popup 读它。

    为什么要夹具而不是读真时间线：真时间线里那个 actor 是**上一次**谁在动，
    判据跑的时候可能已经变了 —— 那就成了「碰巧对」。冻结值必须是判据自己写的。
    """
    import json
    d = tempfile.mkdtemp(prefix="wI_timeline_")
    rec = {"v": 1, "seq": 1, "kind": "lease_acquire", "t_mono": 0.0,
           "t_wall": "2026-10-16T10:00:00.000+08:00", "actor": FROZEN_ACTOR,
           "tool": "ctl_lease", "ok": True, "code": 0, "payload": {},
           "session": FROZEN_SESSION, "prev_hash": "0" * 64, "hash": "f" * 64}
    with open(os.path.join(d, "timeline-20261016.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.environ["AGENTCTL_TIMELINE"] = d      # 必须在 import brake_popup 之前设
    return d


# ── 窗R：判据进程自己的沙箱装置（**不许依赖任何默认值**）────────────────
#
# ★ 这段就是本单的病根所在。过去 make_fixture_timeline() 只设 AGENTCTL_TIMELINE，
#   AGENTCTL_BRAKE_FLAG 留着没设 ⇒ 实现回退到 gate.BRAKE_FLAG ⇒ 它的默认值
#   = %LOCALAPPDATA%\AgentCtl\_急停.flag = **真生产 flag**。
#   于是本判据弹出来的窗自报「状态源 = 生产路径」，看起来就是真急停。
#
# ★ 两道锁，缺一不可（第二道是本单新加的）：
#   锁① AGENTCTL_BRAKE_FLAG **显式**指向沙箱文件 —— 不留默认值这一条退路；
#   锁② LOCALAPPDATA 本身挪到沙箱 —— 连「生产根」都搬进沙箱了。
#      ★ 为什么锁②不能省：锁①是可以被人「顺手删掉」的一行代码，而锁②让即便删掉了，
#        production_root() 也只会落到沙箱里 ⇒ 窗N 那条「落在生产根下 ⇒ 真急停」
#        根本不会命中 ⇒ **漏设 env 最多让判据自己跑红，不会再造幽灵窗**。
_ENV_SANDBOX = None          # 判据自己的沙箱根目录（收尾时删）


def install_env_sandbox():
    """把判据进程自己要用的状态源/时间线**全部**指进沙箱。返回沙箱根。

    ★ 必须在 `from agentctl import brake_popup` **之前**调用：
      TIMELINE_DIR 是模块级常量（import 时算一次），LOCALAPPDATA 也要赶在
      第一次解析状态源之前挪走。
    """
    global _ENV_SANDBOX, _PROD_SNAP
    tl = make_fixture_timeline()
    _ENV_SANDBOX = tempfile.mkdtemp(prefix="wR_env_")
    local = os.path.join(_ENV_SANDBOX, "LocalAppData")
    root = os.path.join(local, PROD_ROOT_NAME)
    os.makedirs(root, exist_ok=True)
    os.environ["LOCALAPPDATA"] = local                  # 锁②：生产根搬进沙箱
    os.environ["AGENTCTL_BRAKE_FLAG"] = os.path.join(root, PROD_FLAG_NAME)  # 锁①
    os.environ["AGENTCTL_APP_DIR"] = os.path.join(_ENV_SANDBOX, "appdir")
    os.environ["AGENTCTL_LEGACY_HOME"] = os.path.join(_ENV_SANDBOX, "lstate")
    _PROD_SNAP = snapshot_real_prod()
    # ★ 用 .get() 而不是 []：这两行是**给红验证看的**。
    #   故意把下面两把锁拆掉时，这里必须照实打印「未设置」并让 ⓪ 组去判红，
    #   而不是自己先 KeyError 崩掉 —— 崩掉虽然也是 rc≠0，但那验的是「崩了」，
    #   不是「env 没沙箱化」，两者在台账上长得一样，混起来就成了假证据。
    print("  判据沙箱：状态源=%s"
          % (os.environ.get("AGENTCTL_BRAKE_FLAG") or "**未设置（吃默认值 = 生产路径！）**"),
          flush=True)
    print("       LOCALAPPDATA=%s（真生产根 %s 已挪开）" % (local, _REAL_PROD_ROOT),
          flush=True)
    print("       时间线=%s；生产目录跑前快照 %d 项" % (tl, len(_PROD_SNAP)),
          flush=True)
    return _ENV_SANDBOX


def snapshot_real_prod():
    """真生产目录的逐项快照 [(名字, 大小, mtime)]，按名字排序。目录不存在记空表。"""
    out = []
    try:
        for nm in os.listdir(_REAL_PROD_ROOT):
            fp = os.path.join(_REAL_PROD_ROOT, nm)
            try:
                st = os.stat(fp)
                out.append((nm, st.st_size, int(st.st_mtime)))
            except OSError:
                out.append((nm, -1, -1))
    except OSError:
        return []
    return sorted(out)


def check_env_explicit():
    """0 组：**本进程认的状态源与时间线必须显式落在沙箱里**。

    ★ 这一组是窗R 的「自证不碰生产」，也是本单要求里「必须能跑红」的那一条：
      故意把 install_env_sandbox() 删掉/改坏 ⇒ 下面三条立刻红。
    """
    section("⓪ 窗R · 判据自己的 env 必须显式沙箱化（删掉这一步就必红）")
    flag = (os.environ.get("AGENTCTL_BRAKE_FLAG") or "").strip()
    tl = (os.environ.get("AGENTCTL_TIMELINE") or "").strip()
    check("0a · AGENTCTL_BRAKE_FLAG **显式**指向沙箱文件（不许留空吃默认值 —— "
          "默认值恰好就是生产路径，那正是本单的病根）",
          bool(flag) and not _under(flag, _REAL_PROD_ROOT) and _norm(flag) != _norm(
              _REAL_PROD_FLAG),
          "实测 AGENTCTL_BRAKE_FLAG=%r（真生产 flag=%s）" % (flag, _REAL_PROD_FLAG))
    check("0b · AGENTCTL_TIMELINE **显式**指向沙箱目录（不许留空吃默认 "
          "%s\\work\\timeline —— 那是真时间线）" % REPO,
          bool(tl) and _norm(tl) != _norm(os.path.join(REPO, "work", "timeline")),
          "实测 AGENTCTL_TIMELINE=%r" % tl)
    cur = prod_root()
    check("0c · ★本进程认定的**生产根也在沙箱里**（LOCALAPPDATA 已挪走）—— "
          "这是第二道锁：即便上面两条被人删了，也碰不到真生产（实测 %s）" % cur,
          _norm(cur) != _norm(_REAL_PROD_ROOT),
          "生产根=%s 与真生产根=%s 相同 ⇒ env 装置没生效" % (cur, _REAL_PROD_ROOT))
    print("       真生产 flag 现在在不在：%s（跑前快照 %d 项）"
          % ("在" if os.path.exists(_REAL_PROD_FLAG) else "**不存在**",
             len(_PROD_SNAP or [])), flush=True)


def _under(path, root):
    """path 在 root 之内（含 root 本身）。跨盘符时按「不在内」算。"""
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


# ── 坏实现（红验证用）：复用真源 Win32 层，只坏一处 ────────────────
class BadBackend:
    """kind ∈ {none, notopmost, release, sticky, nomarker}；每个只坏一处，方便定位。"""

    TITLE = FROZEN_TITLE
    CLASS = FROZEN_CLASS

    def __init__(self, real, kind):
        self._real = real
        self._kind = kind
        self._c = None if kind == "none" else real.controller()
        if kind == "nomarker":
            # 坏法⑤（窗N 追加）：**掐掉「状态源不是生产路径 ⇒ 要自报」这条判定**，
            # 复现窗N 之前的实现 —— 沙箱状态源照样弹出一张长得跟真急停一模一样的窗。
            # 只坏这一处（判定），置顶/控件/消失那几条都不碰 ⇒
            # 跑出来必须是「就 9b/9c 那几条红」，而不是一片红。
            real.state_source = lambda *a, **k: {
                "path": os.path.join(prod_root(), PROD_FLAG_NAME),
                "origin": "（坏实现：不判状态源）",
                "sandbox": False,
                "why": "（坏实现：不判状态源）"}

    def controller(self):
        return self

    def show(self, info=None):
        if self._kind == "none":
            return False                                    # 坏法①：干脆不弹
        self._c.show(info)
        if self._kind == "notopmost":
            h = self._c.hwnd_of()
            # 坏法②：去掉置顶。**必须走 SetWindowPos(HWND_NOTOPMOST)** ——
            # 只 SetWindowLongW 改 WS_EX_TOPMOST 那一位是不作数的（实测：
            # 改完判据照样绿）。这就是「靠改位标志假装置顶」骗不过判据的原因。
            _u.SetWindowPos(ctypes.c_void_p(h), ctypes.c_void_p(-2),
                            0, 0, 0, 0, 0x0003)
        elif self._kind == "release":
            # 坏法③：加「解除急停」。**必须丢进窗口线程去建** ——
            # 从别的线程 CreateWindowEx 到别人的窗口上会把两边一起卡死
            # （实测：判据直接卡在 hide() 上 5s 超时）。
            self._c._call(self._add_release_button)
        return True

    def _add_release_button(self):
        k = ctypes.windll.kernel32
        k.GetModuleHandleW.restype = ctypes.c_void_p
        _u.CreateWindowExW.restype = ctypes.c_void_p
        _u.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                       ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        _u.CreateWindowExW(0, "BUTTON", "▶ 解除急停", 0x50000001,
                           20, 20, 150, 34, ctypes.c_void_p(self._c.hwnd_of()),
                           None, ctypes.c_void_p(k.GetModuleHandleW(None)), None)

    def hide(self):
        if self._kind == "sticky":
            return True                                      # 坏法④：解除后不消失
        return self._c.hide()

    def stop(self):
        if self._c is not None:
            try:
                self._c.stop()
            except Exception:
                pass

    def __getattr__(self, name):
        return getattr(self._c, name)


# ── ⑨ 窗N 追加：弹窗必须**自报身份**（真急停 vs 自证测试）───────────────
#
# 为什么单独立一组，而不是往上面那七条里塞一条：
#   「状态源」是**每次弹窗时**才解析的（AGENTCTL_BRAKE_FLAG 可以在进程内切），
#   所以它要连着弹两次窗 —— 一次沙箱源、一次生产源 —— 才比得出「该不该标注」。
#   混进上面那七条，会把「真急停长什么样」也改成自证测试的样子，反向就测不到了。
def prod_root() -> str:
    r"""生产状态源根目录（%LOCALAPPDATA%\AgentCtl）—— 与 gate.py 默认 _APP_DIR 同一口径。"""
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                        PROD_ROOT_NAME)


def _norm(p):
    """比路径用：绝对化 + 大小写归一。★ **只用于比较**，绝不拿去显示。"""
    return os.path.normcase(os.path.abspath(p or ""))


def _has(hay, needle):
    """子串包含（大小写归一 —— Windows 上路径不区分大小写）。"""
    return (os.path.normcase(hay or "").find(os.path.normcase(needle or "")) >= 0)


def with_flag(flag, fn):
    """在**指定的急停状态源**下跑 fn()，跑完把环境变量还原。

    ★ 为什么能在进程内切：实现方是**每次弹窗时**重读 AGENTCTL_BRAKE_FLAG 的
      （不是 import 时算一次的常量）。这既是判据能测的前提，也正好是对的 ——
      判据换了状态源，就必须**在下一张窗**上体现，不许留上一次的记忆。
    """
    had = "AGENTCTL_BRAKE_FLAG" in os.environ
    old = os.environ.get("AGENTCTL_BRAKE_FLAG")
    if flag is None:
        os.environ.pop("AGENTCTL_BRAKE_FLAG", None)
    else:
        os.environ["AGENTCTL_BRAKE_FLAG"] = flag
    try:
        return fn()
    finally:
        if had:
            os.environ["AGENTCTL_BRAKE_FLAG"] = old
        else:
            os.environ.pop("AGENTCTL_BRAKE_FLAG", None)


def popup_under(REAL, flag):
    """按给定状态源真弹一次窗，黑盒把它的 标题 / 正文 / 按钮 读回来。"""
    box = {}

    def _go():
        REAL.show()
        h, dt = wait_popup(POPUP_DEADLINE_SEC)
        if not h:
            box["err"] = "等了 %.2fs 桌面上枚举不到这个窗" % dt
            return
        box["h"], box["title"], box["cls"] = h, wtext(h), wcls(h)
        box["body"] = REAL.controller().body_text()
        box["btns"] = child_buttons(h)

    try:
        with_flag(flag, _go)
    finally:
        try:
            REAL.hide()
            wait_gone(GONE_DEADLINE_SEC)
        except Exception:
            pass
    return box


def code_only(mod):
    r"""取一个模块的**可执行代码**：剔掉注释与文档字符串，拼成一段文本。

    ★ 为什么要剔：注释与 docstring 是**解释「为什么不按名字猜」的地方**，
      允许、甚至应该出现「夹具」这两个字 —— 那是理由，不是判据。
      判据只该盯**运行时真会去比较字符串**的那部分代码
      （比如 `if "夹具" in actor:`）—— 那才是启发式。
    ★ 用 tokenize 而不是「按行 grep 注释」：按行 grep 认不出行尾注释，
      也认不出多行 docstring，会把一堆正当说明误判成红。
    """
    import ast
    import tokenize

    path = getattr(mod, "__file__", None)
    if not path or not os.path.isfile(path):
        return None
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(node, "body", None) or []
            if (b and isinstance(b[0], ast.Expr)
                    and isinstance(getattr(b[0], "value", None), ast.Constant)
                    and isinstance(b[0].value.value, str)):
                docs.add((b[0].value.lineno, b[0].value.end_lineno))
    keep = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and any(
                    a <= tok.start[0] <= z for a, z in docs):
                continue
            keep.append(tok.string)
    return " ".join(keep)

def run_identity_checks(REAL):
    """窗N ⑨ 组：状态源 ⇒ 弹窗自报身份。**只认路径，不认名字。**"""
    section("⑨ 窗N · 弹窗自报身份：状态源不是生产路径 ⇒ 必须标注自证测试")

    sb = tempfile.mkdtemp(prefix="wN_sandbox_")
    sb_flag = os.path.join(sb, PROD_FLAG_NAME)
    # 假 host 目录：挂在**生产根**下面，但故意不是生产根本身。
    # 只拼路径、不落盘 —— 弹窗的判据只需要「解析出来的那个路径」，不需要文件真存在；
    # 落盘反而把这条判据绑到 %LOCALAPPDATA% 的写权限上，换机器就得改判据。
    host_flag = os.path.join(prod_root(), "_wN_假host_判据用", PROD_FLAG_NAME)
    try:
        # ── ① 沙箱状态源 ⇒ 必须自报 ─────────────────────────────
        s = popup_under(REAL, sb_flag)
        check("9a · 沙箱状态源 ⇒ 窗照弹（%.1fs 内；状态源 %s）"
              % (POPUP_DEADLINE_SEC, sb_flag), bool(s.get("h")), s.get("err", ""))
        title, body = s.get("title", ""), s.get("body", "")
        check("9b · ★**标题**带 %s 两个标记（一眼能认出这不是真急停）"
              % " + ".join(SELFTEST_MARKS),
              all(m in title for m in SELFTEST_MARKS), "实测标题=%r" % title)
        check("9c · ★**正文**把沙箱状态源路径原样印出来（%s）" % sb_flag,
              _has(body, sb_flag), "正文里找不到这个沙箱路径")
        check("9d · 正文写明**不要解除**（省得有人照着去跑 brake --action release）",
              ("不要" in body and "brake --action release" in body),
              "正文里没有劝阻字样")
        check("9e · 自证测试窗**同样没有能改状态的控件**（标注不是新开口子）",
              not [(b, v) for b in s.get("btns", []) for v in FORBIDDEN_VERBS if v in b],
              "实测按钮 %s" % (s.get("btns"),))
        check("9f · 标注**没有把防自授权那两层弄丢**（类名仍是 %r，标题仍含黑名单键 %r）"
              % (FROZEN_CLASS, FROZEN_BLOCK_TITLE_KEY),
              s.get("cls") == FROZEN_CLASS and FROZEN_BLOCK_TITLE_KEY in s.get("title", ""),
              "实测类名=%r 标题=%r" % (s.get("cls"), s.get("title")))

        # ── ② 生产状态源 ⇒ 一个标记都不许有（反向同样危险）──────────
        p = popup_under(REAL, host_flag)
        check("9g · 生产状态源（%s）⇒ 窗照弹" % host_flag,
              bool(p.get("h")), p.get("err", ""))
        check("9h · ★生产状态源 ⇒ 标题**逐字等于**真急停的冻结标题（%r）" % FROZEN_TITLE,
              p.get("title") == FROZEN_TITLE, "实测标题=%r" % p.get("title", ""))
        leak = [m for m in SELFTEST_MARKS
                if m in p.get("title", "") or m in p.get("body", "")]
        check("9i · ★生产状态源 ⇒ 标题与正文**都没有**%s 标注"
              % ("/".join(SELFTEST_MARKS)), not leak,
              "泄漏了 %s —— 真急停若被误标成测试，有人就会放过一个真的急停" % leak)

        # ── ③ 判据本身：只看路径，不看名字 ──────────────────────────
        fn = getattr(REAL, "state_source", None)
        if not callable(fn):
            check("9j · ★实现里有 state_source() 这条「按状态源路径判定」的口径"
                  "（没有它 ⇒ 只能按别的什么猜）", False,
                  "brake_popup 里找不到 state_source()")
        else:
            s_sb = with_flag(sb_flag, fn)
            s_pr = with_flag(host_flag, fn)
            check("9j · ★state_source() 这条判据**只认路径**："
                  "沙箱⇒sandbox=True、生产⇒sandbox=False（actor 叫什么一律不影响）",
                  bool(s_sb.get("sandbox")) and not s_pr.get("sandbox")
                  and _norm(s_sb.get("path")) == _norm(sb_flag),
                  "实测 沙箱=%r / 生产=%r" % (s_sb, s_pr))
        code = code_only(REAL)
        hits = ([] if code is None else
                [w for w in ("夹具", FROZEN_ACTOR) if w in code])
        check("9k · ★实现里**没有**「按 actor 名字猜真假」的启发式"
              "（可执行代码里不得出现 %r / %r 这类测试名）" % (FROZEN_ACTOR, "夹具"),
              not hits, "可执行代码里出现了 %s —— 口径只认状态源路径" % hits)
    finally:
        shutil.rmtree(sb, ignore_errors=True)


def run_ghost_checks(REAL):
    r"""窗R ⑪ 组：状态源落在生产根下，**可那个文件压根不存在** ⇒ 窗必须自曝。

    ★ 为什么这两组必须分开跑（一个不存在、一个存在）：
      只验「不存在」那一半，等于给了实现方一条便宜的绕路 —— 把异常标注
      无条件打出来就绿了，可那样**真急停时窗上也会多这一行**，
      那就是真急停被误标（窗N 9i 守的正是这个方向，代价是有人会放过一个真的急停）。
      所以这里两个分支都要弹一次窗：
        甲（文件不存在）⇒ 必须有那行异常标注，形态仍是真急停；
        乙（文件确实存在）⇒ **一个标注都不许加**，恢复今天逐字的样子。

    ★ 为什么敢在「生产根」下真的建一个文件来跑乙分支：
      install_env_sandbox() 已把 LOCALAPPDATA 挪进沙箱 ⇒ 本进程认的
      「生产根」是 <沙箱>\LocalAppData\AgentCtl，真生产目录一个字节都碰不到。
      这也正是 0c 那条要钉的事：**判定口径一个字没改**，改的只是「生产根在哪儿」。
    """
    section("⑪ 窗R · 状态源说自己是生产，可那个文件不存在 ⇒ 窗必须自曝")
    root = prod_root()                       # 本进程认的「生产根」（install_env_sandbox 挪过的）
    flag = os.path.join(root, PROD_FLAG_NAME)
    if _norm(root) == _norm(_REAL_PROD_ROOT):
        # 走到这里说明 env 装置没生效 —— 那就不能在真生产根下建文件，立刻退出。
        SKIP.append("⑪ 窗R：生产根仍是真生产根（%s），env 装置没生效 ⇒ 本组不跑"
                    % root)
        print("  [~] %s" % SKIP[-1], flush=True)
        return
    print("       本进程的『生产根』=%s（真生产根 %s —— 碰不到）" % (root, _REAL_PROD_ROOT),
          flush=True)
    try:
        if os.path.exists(flag):
            os.remove(flag)

        # ── 甲：状态源 = 生产路径，而那个文件**不存在** ──────────────
        a = popup_under(REAL, flag)
        check("11a · 生产状态源（%s）⇒ 窗照弹（形态一个字都不许变）" % flag,
              bool(a.get("h")), a.get("err", ""))
        if not a.get("h"):
            for tail in ("11b · 正文**自曝状态源文件不存在**", "11c · 标题逐字不变",
                         "11d · **不许**出现【自证测试】/非真实急停", "11e · 仍判 sandbox=False",
                         "11f' · 实存分支：正文**一个标注都不加**"):
                SKIP.append("⑪ %s：窗压根没出现（11a 已红），无从验" % tail)
                print("  [~] ⑪ %s：窗压根没出现（11a 已红），无从验" % tail, flush=True)
            return
        abody, atitle = a.get("body", ""), a.get("title", "")
        check("11b · ★正文**自曝「%s」**（状态源说是生产，可它压根不在 —— "
              "这正是幽灵急停窗要说出口的那句话）" % GHOST_MARK,
              GHOST_MARK in abody, "正文里找不到这行标注（长度 %d）" % len(abody))
        check("11b2 · 同一行还点明来源可疑（正文含 %r）—— 只说「不在」不说「所以呢」"
              "等于让人自己猜" % GHOST_TAIL, GHOST_TAIL in abody,
              "正文里没有「未沙箱化的测试进程」这句归因")
        check("11c · ★标题**逐字未变**（仍是真急停的冻结标题 %r）—— "
              "窗R 只准**加一行**，不许改形态" % FROZEN_TITLE,
              atitle == FROZEN_TITLE, "实测标题=%r" % atitle)
        leak = [m for m in SELFTEST_MARKS if m in atitle or m in abody]
        check("11d · ★**不许**出现 %s —— 路径口径一个字不许改（窗N 9i 同一红线）"
              % "/".join(SELFTEST_MARKS), not leak,
              "泄漏了 %s：状态源确实在生产根下，判定不许变" % leak)
        s_abs = with_flag(flag, REAL.state_source)
        check("11e · 判定本身**没动**：仍判 sandbox=False（真急停形态），"
              "只是多了实存性字段 prod_exists=False",
              (s_abs.get("sandbox") is False) and (s_abs.get("prod_exists") is False),
              "实测 state_source()=%r" % s_abs)

        # ── 乙：状态源 = 生产路径，且那个文件**确实存在** ────────────
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write("判据拉闸（窗R 乙分支，沙箱根内）")
        b = popup_under(REAL, flag)
        check("11f · 生产状态源文件**确实存在**（%s）⇒ 窗照弹" % flag,
              bool(b.get("h")), b.get("err", ""))
        if b.get("h"):
            bbody = b.get("body", "")
            check("11f' · ★实存分支**一个异常标注都不许加**（正文里没有 %r）—— "
                  "真急停就该是今天逐字的样子，多一行就是误标" % GHOST_MARK,
                  GHOST_MARK not in bbody,
                  "真急停窗上出现了这行：%r" % bbody[:160])
            miss = [s for s in FROZEN_SECTIONS if s not in bbody]
            check("11g · 实存分支正文三段仍齐全（缺：%s）—— 加一行不许加掉别的"
                  % (miss or "无"), not miss)
    finally:
        try:
            if os.path.exists(flag):
                os.remove(flag)
        except OSError:
            pass


# ── 七条判据 ──────────────────────────────────────────────────────
def _static_checks(realmod):
    """与窗口无关的两组判据：act.py 的黑名单（⑥）与 MCP 匹配逻辑（7c/7d）。

    单独立出来，是因为它们**在窗没出现时也照样验得了** ——
    把它们跟着窗一起跳过，等于坏实现「一坏就全不验」，红基线就废了。
    """
    titles, classes, err = read_blocklists()
    if err or titles is None or classes is None:
        SKIP.append("⑥ 读不出 legacy_tools/act.py 的两个黑名单（%s）" % (err or "没找到"))
        print("  [~] %s" % SKIP[-1], flush=True)
    else:
        check("6a · 弹窗标题键 %r 已登记进 act.py 的 BLOCKED_TITLES" % FROZEN_BLOCK_TITLE_KEY,
              FROZEN_BLOCK_TITLE_KEY in titles, "实测 BLOCKED_TITLES=%r" % (titles,))
        check("6b · 弹窗类名 %r 已登记进 act.py 的 BLOCKED_CLASSES" % FROZEN_BLOCK_CLASS,
              FROZEN_BLOCK_CLASS in classes, "实测 BLOCKED_CLASSES=%r" % (classes,))
        check("6c · 原有的防自授权条目没被顺手删掉（本地 agent电脑控制授权 仍在）",
              "本地 agent电脑控制授权" in titles, "实测 BLOCKED_TITLES=%r" % (titles,))
    mcp_txt = NL.join(realmod._mcp_block("agentctl"))
    servers, _src = realmod.read_mcp_servers()
    want = [s for s in servers if s.get("name") == "agentctl"]
    check("7c · actor 能精确对上时**点名那一条**（actor=agentctl → mcp_servers.agentctl）",
          bool(want) and ("已匹配到" in mcp_txt)
          and (want[0]["command"] in mcp_txt)
          and ("列出全部候选" not in mcp_txt),
          "config.toml 里没有 mcp_servers.agentctl 段（换机器就验不了这一条）")
    miss_txt = NL.join(realmod._mcp_block("查无此actorxyz"))
    check("7d · actor 匹配不到时**列出全部候选**并写明来源文件",
          ("列出全部候选" in miss_txt) and ("mcp_servers." in miss_txt))


def run_checks(backend, kind):
    real = backend._real if kind != "good" else None
    ctrl = backend.controller()
    label = "好实现" if kind == "good" else "坏实现(%s)" % kind

    section("窗I · 急停置顶弹窗判据 · %s" % label)
    print("  实现：%s" % backend.__class__.__name__, flush=True)

    # ① 拉下急停 ⇒ N 秒内真的出现
    backend.show()
    h, dt = wait_popup(POPUP_DEADLINE_SEC)
    got = check("1a · 急停后 %.1fs 内出现该窗（按标题/类名枚举**真实 HWND**）" % POPUP_DEADLINE_SEC,
                bool(h), "实测 %.2fs 后仍未找到" % dt)
    if not got:
        # 窗都没出现，后面那些「在窗上验」的就不是红、是**验不了** ——
        # 混成红会让人以为「内容不全」「有解除按钮」这些也坏了，那是冤枉它。
        SKIP.append("①之后的窗上判据（1a之后/1b/2/3/3之后/4/5/7a/7b）：窗压根没出现，无从验")
        print("  [~] %s" % SKIP[-1], flush=True)
        _static_checks(real or backend)
        return
    print("       hwnd=%d 标题=%r 类名=%r"
          % (h, wtext(h), wcls(h)), flush=True)
    check("1a' · 标题/类名与冻结值一致（%r / %r）" % (FROZEN_TITLE, FROZEN_CLASS),
          wtext(h) == FROZEN_TITLE and wcls(h) == FROZEN_CLASS)

    # ①' 急停期间不许自动消失
    time.sleep(STAY_SEC)
    still = find_popup()
    check("1b · 急停期间**不自动消失**（再等 %.1fs 仍在）" % STAY_SEC,
          bool(still), "等 %.1fs 后窗口不见了" % STAY_SEC)

    # ② 置顶位实读
    ex = exstyle(h)
    check("2 · 该窗是**桌面置顶**的（GWL_EXSTYLE & WS_EX_TOPMOST = 0x%08X）" % ex,
          bool(ex & WS_EX_TOPMOST), "WS_EX_TOPMOST 那一位没置上")

    # ③ 窗上不许有能改急停状态的控件
    btns = child_buttons(h)
    hits = [(b, v) for b in btns for v in FORBIDDEN_VERBS if v in b]
    check("3 · 该窗上**找不到任何能改变急停状态的控件**（枚举子 HWND 的 BUTTON 文本，"
          "命中 %s 即红）" % "/".join(FORBIDDEN_VERBS),
          not hits, "命中：%s" % (hits,))
    check("3' · 不是靠「一个控件都没有」蒙过去的（实测按钮 %d 个：%s）"
          % (len(btns), btns), len(btns) >= 1, "窗上一个按钮都没有，判据 3 就没有意义")

    # ⑤ 三段内容齐全 + 身份来自夹具
    body = ctrl.body_text()
    miss = [s for s in FROZEN_SECTIONS if s not in body]
    check("5a · 三段内容齐全且逐字对得上（谁 / MCP 链接 / 怎么恢复）",
          not miss, "缺：%s" % miss)
    check("5b · 「是谁」取到夹具里的真 actor（%r）与 session_id（%r）"
          % (FROZEN_ACTOR, FROZEN_SESSION),
          FROZEN_ACTOR in body and FROZEN_SESSION in body,
          "正文里没有夹具身份 —— 要么读错了源，要么编了一个")
    check("5c · 给了**确切恢复命令**（brake --action release）+ 提醒先确认是谁在动",
          "brake --action release" in body and "恢复前先确认是谁在动" in body)

    # ⑦ MCP 那条可选中复制
    check("7a · 正文里列出了 [mcp_servers.*] 且写明来源文件",
          "[mcp_servers." in body and ".窗口D" in body.replace("\\", "/"),
          "正文里没有 MCP 段或没写来源")
    clip = ctrl.copy_all()
    check("7b · MCP 那条**可选中复制**（复制往返：剪贴板 == 正文，%d 字）" % len(clip),
          clip.strip() == body.strip(), "剪贴板 %d 字 / 正文 %d 字" % (len(clip), len(body)))

    # ⑥ 与 7c/7d 与窗口无关，交给 _static_checks（窗没出现时它也照样验）
    _static_checks(real or backend)

    # ④ 解除急停 ⇒ 自动消失
    backend.hide()
    ok, dt2 = wait_gone(GONE_DEADLINE_SEC)
    check("4 · 解除急停后该窗**自动消失**（%.1fs 内桌面枚举不到）" % GONE_DEADLINE_SEC,
          ok, "等了 %.1fs 还在" % dt2)


def main(argv=None):
    ap = argparse.ArgumentParser(description="窗I 急停置顶弹窗判据")
    ap.add_argument("--bad", default="good",
                    choices=["good", "none", "notopmost", "release", "sticky",
                             "nomarker"],
                    help="跑一个坏实现（红验证用）")
    ap.add_argument("--list-bad", action="store_true", help="只打印红验证跑法")
    ap.add_argument("--only-p2", action="store_true",
                    help="只跑窗P2 的 ⑧' 三条入口矩阵（含抢占与收尾自证）—— "
                         "红验证用：跑红一条新断言不必等前面那 40 秒")
    ap.add_argument("--no-e2e", action="store_true",
                    help="跳过第 ⑧ 组端到端**与**窗P2 三条入口矩阵"
                         "（会另起多个真 brake_daemon 进程，约 90s）")
    ap.add_argument("--only-r", action="store_true",
                    help="只跑窗R 的 ⓪/⑪ 两组（env 显式性 + 幽灵急停窗自曝）—— "
                         "红验证用：约 15s，不必等前面那 40 秒与 90 秒")
    a = ap.parse_args(argv)

    if a.list_bad:
        print("红验证（每条都应当**报红**，且是不同那一处报红）：")
        for k, why in (("none", "干脆不弹 ⇒ 1a 红"),
                       ("notopmost", "去掉 WS_EX_TOPMOST ⇒ 2 红"),
                       ("release", "多挂一个「解除急停」按钮 ⇒ 3 红"),
                       ("sticky", "解除后 hide 空转 ⇒ 4 红"),
                       ("nomarker", "沙箱状态源却不自报身份（= 窗N 之前的实现）⇒ 9b/9c/9d 红")):
            print("  python -X utf8 %s --bad %-9s  # %s"
                  % (os.path.relpath(__file__, REPO), k, why))
        print("  python -X utf8 %s                     # 好实现 ⇒ 全绿"
              % os.path.relpath(__file__, REPO))
        return 0

    if a.only_r:
        # 红验证用：只跑窗R 两组。**照样建沙箱 env** —— 判据自己没有沙箱就没有资格
        # 验「env 显式性」，而没有 env 就没有资格在桌面上弹窗（那正是本单的病根）。
        install_env_sandbox()
        try:
            if SRC not in sys.path:
                sys.path.insert(0, SRC)
            from agentctl import brake_popup as REAL
            check_env_explicit()
            try:
                run_ghost_checks(REAL)
            except Exception as e:
                BAD.append("判据自身崩了")
                print("  [红] 判据自身崩了：%s: %s" % (type(e).__name__, e), flush=True)
                import traceback
                traceback.print_exc()
            section("汇总")
            print("  绿 %d 条 / 红 %d 条 / 验不了 %d 条"
                  % (len(OK), len(BAD), len(SKIP)), flush=True)
            for x in SKIP:
                print("  ~ %s" % x)
            if BAD:
                print("  结论：【红】%d 条不成立" % len(BAD))
                for b in BAD:
                    print("     - %s" % b)
                return 1
            print("  结论：【绿】窗R 全部成立")
            return 0
        finally:
            shutil.rmtree(_ENV_SANDBOX or "", ignore_errors=True)

    if a.only_p2:
        # 红验证用：只跑窗P2 那一组。**不建时间线夹具、不 import 弹窗实现**，
        # 这样跑红的到底是「入口矩阵那一组」而不是别处的噪声。
        # ★ 窗R：即便只跑这一组，也照样装沙箱 env —— 子进程是从 os.environ 继承的，
        #   这里不挪 LOCALAPPDATA，子进程里的 daemon 就仍有可能摸到真生产。
        install_env_sandbox()
        try:
            run_entry_matrix()
        finally:
            shutil.rmtree(_ENV_SANDBOX or "", ignore_errors=True)
        section("汇总")
        print("  绿 %d 条 / 红 %d 条 / 验不了 %d 条"
              % (len(OK), len(BAD), len(SKIP)), flush=True)
        for x in SKIP:
            print("  ~ %s" % x)
        if BAD:
            print("  结论：【红】%d 条不成立" % len(BAD))
            for b in BAD:
                print("     - %s" % b)
            return 1
        print("  结论：【绿】窗P2 三条入口矩阵，全部成立")
        return 0
    # ★ 窗R：env 沙箱化必须在 import 实现**之前**，且由一处装置统一做。
    #   过去这里只有 make_fixture_timeline()（只管时间线），AGENTCTL_BRAKE_FLAG
    #   漏设 ⇒ 状态源回退到默认值 = 生产路径 ⇒ 桌面上那张幽灵急停窗。
    install_env_sandbox()
    tmp = os.environ["AGENTCTL_TIMELINE"]
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    from agentctl import brake_popup as REAL
    backend = REAL if a.bad == "good" else BadBackend(REAL, a.bad)
    try:
        check_env_explicit()
        try:
            run_checks(backend, a.bad)
            # 窗N ⑨ 组：自报身份。坏实现里只登记了 "nomarker"（掐掉状态源判定）
            # —— 其余坏法只坏置顶/控件/消失那一处，状态源判定它们根本没碰，
            # 让它们跑这一组只会得到「它没坏在那儿」的噪声。
            if a.bad in ("good", "nomarker"):
                run_identity_checks(REAL)
                # 窗R ⑪ 组：状态源说自己是生产，可那个文件压根不存在 ⇒ 必须自曝。
                # ★ 只在好实现与 nomarker 坏法上跑：那两种的「状态源判定」是通的，
                #   别的坏法压根没碰判定，让它们跑这一组只会搅浑「坏在哪一处」。
                run_ghost_checks(REAL)
        except Exception as e:
            # 判据自己崩了也要**留一条红**，不能只吐个 traceback 就没下文 ——
            # run_all 只看退出码，崩了和「判绿了」在台账上会长得一样。
            BAD.append("判据自身崩了")
            print("  [红] 判据自身崩了：%s: %s" % (type(e).__name__, e), flush=True)
            import traceback
            traceback.print_exc()
        if not a.no_e2e and a.bad == "good":
            # 端到端只在好实现上跑：坏实现的弹窗是自己造的，
            # 再叠一层真 daemon 只会把「哪一处坏了」搅浑。
            section("⑧ 端到端：真 brake_daemon + 急停标志文件")
            run_e2e()
            # 窗P2：三条入口矩阵（A 退役线 / B 真·文档入口 / C 模块入口）
            # + 抢占显式 + 收尾自证。母单 §二② 的缺口就是「文档入口
            # 从未进判据」—— 补在这里，两组 ⑧ 串着跑、共用同一台桌面的前提。
            run_entry_matrix()
    finally:
        try:
            backend.stop()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(_ENV_SANDBOX or "", ignore_errors=True)

    section("汇总")
    print("  绿 %d 条 / 红 %d 条 / 验不了 %d 条" % (len(OK), len(BAD), len(SKIP)), flush=True)
    for s in SKIP:
        print("  ~ %s" % s)
    if BAD:
        print("  结论：【红】%d 条不成立" % len(BAD))
        for b in BAD:
            print("     - %s" % b)
        return 1
    print("  结论：【绿】急停置顶弹窗七条判据 + 窗N 自报身份一组 + "
          "窗P2 三条入口一组，全部成立")
    return 0


# ── ⑧ 端到端：真起一个 brake_daemon，写/删急停标志文件，看弹窗跟不跟 ──
def run_e2e():
    """前面七条验的是「弹窗本身对不对」；这一条验的是**触发链真的接上了**：

        BRAKE_FLAG 由无变有  ⇒  brake_daemon 那个已有的轮询发现  ⇒  弹窗出现
        BRAKE_FLAG 由有变无  ⇒  同一个轮询发现  ⇒  弹窗自动消失

    ★ 为什么用**子进程**跑真 daemon：那个模块一 import 就注册全局热键并进死循环，
      本进程里跑不了。而「标志文件一写、窗就出来」这件事，只有走真轮询才算数。
    ★ 抢不到热键就**跳过**不判红：机器上本来就常驻着一个 brake-daemon，
      那是正常状态，不是这条判据的红。
    """
    import json
    import shutil
    import subprocess
    import tempfile
    import threading

    tmp = tempfile.mkdtemp(prefix="wI_e2e_")
    flag = os.path.join(tmp, "_\u6025\u505c.flag")
    tl = os.path.join(tmp, "timeline")
    os.makedirs(tl, exist_ok=True)
    rec = {"v": 1, "seq": 1, "kind": "lease_acquire", "t_mono": 0.0,
           "t_wall": "2026-10-16T10:00:00.000+08:00", "actor": FROZEN_ACTOR,
           "tool": "ctl_lease", "ok": True, "code": 0, "payload": {},
           "session": FROZEN_SESSION, "prev_hash": "0" * 64, "hash": "f" * 64}
    with open(os.path.join(tl, "timeline-20261016.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + NL)

    env = dict(os.environ)
    env["AGENTCTL_BRAKE_FLAG"] = flag
    env["AGENTCTL_APP_DIR"] = tmp
    env["AGENTCTL_TIMELINE"] = tl
    env["PYTHONPATH"] = SRC
    lines = []
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c",
         "import sys; sys.path.insert(0, r\'%s\');"
         "from agentctl import brake_daemon as b; b.run()" % SRC],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def _drain():
        try:
            for ln in proc.stdout:
                lines.append(ln.decode("utf-8", "replace"))
        except Exception:
            pass

    threading.Thread(target=_drain, daemon=True).start()
    try:
        time.sleep(2.5)
        blob = NL.join(lines)
        if "\u70ed\u952e" in blob and "\u5931\u8d25" in blob:
            SKIP.append("⑧ 端到端：真 brake_daemon 抢不到 Ctrl+Alt+Home"
                        "（本机已常驻一个，机器的正常状态）⇒ 本条跳过，不判红")
            print("  [~] ⑧ %s" % SKIP[-1], flush=True)
            return
        check("8a · 起 daemon 时**不该有窗**（急停还没拉）", not find_popup(),
              "还没拉急停就已经有窗了")
        open(flag, "w", encoding="utf-8").write("\u6025\u505c\u5df2\u751f\u6548")
        h, dt = wait_popup(POPUP_DEADLINE_SEC)
        check("8b · ★**拉下急停标志文件**后 %.1fs 内弹窗自动出现（走 daemon 真轮询）"
              % POPUP_DEADLINE_SEC, bool(h), "等了 %.2fs 没出现" % dt)
        if h:
            check("8c · 这时候它也是置顶的（0x%08X）" % exstyle(h),
                  bool(exstyle(h) & WS_EX_TOPMOST))
        os.remove(flag)
        ok, dt2 = wait_gone(GONE_DEADLINE_SEC)
        check("8d · ★**解除急停**后弹窗自动消失（%.1fs 内）" % GONE_DEADLINE_SEC,
              ok, "等了 %.1fs 还在" % dt2)
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


# ── 窗P2：跨进程读文本的两档口径（+ 一档像素兜底）────────────────────
#
# ★ 这段是更正(d) 的核心，也是今晚连着栽过两次跟头的地方 ──────────────────
#   本地 agent 与 窗口A 都曾用**跨进程 GetWindowTextW** 读弹窗正文，读到空，
#   于是各自判「正文根本没画在屏上」—— **两次都判错了**。
#   真相：GetWindowTextW 对**另一个进程的控件**返回空，是 Win32 的**设计**
#   （它只保证拿自己进程的控件文本）；而标准 EDIT 控件的正文，
#   **SendMessage(WM_GETTEXT) 是跨进程编组的**（USER32 代为投递）。
#   实测同一张窗（work\_wP2_探针_三条入口.py 原始输出）：
#       [Edit]  GWT=''   WM=1969字
#   窗N3 的位图 + 跨进程 WM_GETTEXT + 窗口A 像素复核三方对上：**正文一直都在渲染**。
#
# ★ 所以本判据只认一条口径：**API 没读到 ≠ 屏上没画**。三档递进：
#     档① GetWindowTextW    —— 最省事，但对跨进程控件**基本必空**（先排除它）
#     档② 跨进程 WM_GETTEXT —— 本判据认定的**内容口径**
#     档③ 像素（位图物证）  —— 前两档都空时的兜底；**只报「验不了」，永不单独判红**
#   ★ 为什么档③不给红：像素阈值是启发式，换个主题就误判。更正(e) 实测：
#     窗N3 那条 R4 判「100% 非底色 ⇒ 没渲染」，在深色主题下**恰好把画满的窗判成没画**。
#     启发式没有资格给人判红 —— 它只能把「读不到」如实写成「读不到」。

_g = ctypes.WinDLL("gdi32", use_last_error=False)
_g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_g.CreateCompatibleDC.restype = ctypes.c_void_p
_g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_g.CreateCompatibleBitmap.restype = ctypes.c_void_p
_g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
_g.GetDIBits.restype = ctypes.c_int
_g.DeleteObject.argtypes = [ctypes.c_void_p]
_g.DeleteDC.argtypes = [ctypes.c_void_p]
_u.GetWindowDC.argtypes = [ctypes.c_void_p]
_u.GetWindowDC.restype = ctypes.c_void_p
_u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
_u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_u.SendMessageW.restype = ctypes.c_ssize_t
_u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                            ctypes.c_ssize_t]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_ulong),
                ("biSizeImage", ctypes.c_ulong), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_ulong),
                ("biClrImportant", ctypes.c_ulong)]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BIH), ("bmiColors", ctypes.c_ulong * 3)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _visible_popups():
    """桌面上**可见**的急停弹窗 HWND 列表。

    ★ 计数一律先过 IsWindowVisible —— 弹窗 hide() 走的是 ShowWindow(SW_HIDE)，
      窗口对象还在、还枚举得到、数得出 1。不按可见性过滤就会把「已经收起的窗」
      读成「还留在屏幕上」，那种红是假的（而且是判据自己造的假红）。
    """
    return [h for h in enum_windows()
            if is_visible(h) and (wcls(h) == FROZEN_CLASS or wtext(h) == FROZEN_TITLE)]


def edit_child(h):
    for c in enum_children(h):
        if (wcls(c) or "").upper() == EDIT_CLASS:
            return c
    return 0


def xproc_text(h):
    r"""档②：跨进程 SendMessage(WM_GETTEXT)。USER32 对这条消息做跨进程编组。"""
    n = int(_u.SendMessageW(ctypes.c_void_p(h), WM_GETTEXTLENGTH, 0, 0) or 0)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 2)
    _u.SendMessageW(ctypes.c_void_p(h), WM_GETTEXT, n + 2,
                    ctypes.cast(buf, ctypes.c_void_p).value)
    return buf.value


def read_body(h, sec):
    r"""按「先轮询 → 档① → 档②」的顺序读**跨进程**正文。返回 (文本, 用了哪一档)。

    为什么先轮询再降档：弹窗刚出现时正文可能还没填完，一读到空就断定
    「没渲染」正是今晚那两次误判的来路。轮询窗口由调用方给（≥ 一次轮询间隔）。
    """
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        e = edit_child(h)
        if e:
            t = wtext(e)                       # 档①：跨进程读 EDIT 基本必空
            if t:
                return t, "档①GetWindowTextW"
            t = xproc_text(e)                  # 档②：真正读得到的那一档
            if t:
                return t, "档②跨进程WM_GETTEXT"
        time.sleep(0.2)
    return "", "（两档都空 → 交像素档）"


def _write_bmp(path, ww, hh, stride, raw):
    with open(path, "wb") as f:
        f.write(b"BM")
        f.write((54 + stride * hh).to_bytes(4, "little"))
        f.write(b"\x00\x00\x00\x00")
        f.write((54).to_bytes(4, "little"))
        f.write((40).to_bytes(4, "little"))
        f.write(ww.to_bytes(4, "little"))
        f.write(hh.to_bytes(4, "little"))
        f.write((1).to_bytes(2, "little") + (24).to_bytes(2, "little"))
        f.write(b"\x00" * 24)
        pad = b"\x00" * ((4 - (ww * 3) % 4) % 4)
        for row in range(hh - 1, -1, -1):
            f.write(raw[row * stride: row * stride + ww * 3])
            f.write(pad)


def pixel_evidence(h, bmp_path):
    r"""档③：抓一张位图当**物证**，并报「不同颜色数 / 主色占比 / 中位色」。

    ★ 阈值**不抄窗N3**（更正(e)）：它那条「非底色像素占比」在深色主题下
      100% ⇒ 直接判「没渲染」，恰好把画满的窗判成没画。
      这里改成**与主题无关**的三个数：
        · 不同颜色数（量化到 5bit/通道）—— 画了字就一定有多种颜色，纯底色只有 1 种；
        · 主色占比 —— 纯底色接近 100%，有字就明显低；
        · 中位色 —— 给人眼看的「这块到底是深是浅」。
      三个数只**如实报出来**给人复核；本函数**不返回任何红/绿判决**。
    """
    out = {"ok": False, "why": "", "colours": 0, "modal": 0.0, "median": "",
           "rect": (0, 0, 0, 0), "bmp": ""}
    r = _RECT()
    if not _u.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(r)):
        out["why"] = "GetWindowRect 失败"
        return out
    ww, hh = r.right - r.left, r.bottom - r.top
    if ww <= 0 or hh <= 0:
        out["why"] = "窗口尺寸异常 %dx%d" % (ww, hh)
        return out
    e = edit_child(h)
    region = None
    if e:
        cr = _RECT()
        if _u.GetWindowRect(ctypes.c_void_p(e), ctypes.byref(cr)):
            region = (max(cr.left - r.left, 0), max(cr.top - r.top, 0),
                      min(cr.right - r.left, ww), min(cr.bottom - r.top, hh))
    if region is None or region[2] <= region[0] or region[3] <= region[1]:
        out["why"] = "正文控件(rect)取不到"
        return out
    out["rect"] = region
    hdc = _u.GetWindowDC(ctypes.c_void_p(h))
    mdc = _g.CreateCompatibleDC(ctypes.c_void_p(hdc))
    bmp = _g.CreateCompatibleBitmap(ctypes.c_void_p(hdc), ww, hh)
    _g.SelectObject(ctypes.c_void_p(mdc), ctypes.c_void_p(bmp))
    ok = _u.PrintWindow(ctypes.c_void_p(h), ctypes.c_void_p(mdc), 0x00000002)
    bi = _BMI()
    bi.bmiHeader.biSize = ctypes.sizeof(_BIH)
    bi.bmiHeader.biWidth = ww
    bi.bmiHeader.biHeight = -hh                    # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 24
    bi.bmiHeader.biCompression = 0
    stride = (ww * 3 + 3) // 4 * 4
    bi.bmiHeader.biSizeImage = stride * hh
    buf = ctypes.create_string_buffer(stride * hh)
    got = _g.GetDIBits(ctypes.c_void_p(mdc), ctypes.c_void_p(bmp), 0, hh, buf,
                       ctypes.byref(bi), 0)
    _g.DeleteObject(ctypes.c_void_p(bmp))
    _g.DeleteDC(ctypes.c_void_p(mdc))
    _u.ReleaseDC(ctypes.c_void_p(h), ctypes.c_void_p(hdc))
    if not got:
        out["why"] = "GetDIBits=%s（抓不到位图）" % got
        return out
    try:
        _write_bmp(bmp_path, ww, hh, stride, buf.raw)
        out["bmp"] = bmp_path
    except Exception as e:
        out["why"] = "BMP 落盘失败：%r" % (e,)
    x0, y0, x1, y1 = region
    raw = buf.raw
    counts, rs, gs, bs = {}, [], [], []
    for row in range(y0, y1, 3):
        base = row * stride
        for p in range(base + x0 * 3, base + x1 * 3, 9):
            b_, g_, r_ = raw[p], raw[p + 1], raw[p + 2]   # BMP 是 BGR
            key = (r_ >> 3, g_ >> 3, b_ >> 3)
            counts[key] = counts.get(key, 0) + 1
            rs.append(r_)
            gs.append(g_)
            bs.append(b_)
    n = max(len(rs), 1)
    if not counts:
        out["why"] = "正文区一个像素都没取到"
        return out
    modal = max(counts.items(), key=lambda kv: kv[1])
    rs.sort()
    gs.sort()
    bs.sort()
    out.update(ok=bool(ok), colours=len(counts),
               modal=round(modal[1] * 100.0 / n, 2),
               median="rgb(%d,%d,%d)" % (rs[n // 2], gs[n // 2], bs[n // 2]))
    return out

# ── 窗P2：进程侧的只读工具 ─────────────────────────────────────────────
#
# ★ 为什么不用 tasklist / Get-CimInstance：本机沙箱里两者都是「拒绝访问」（窗P1 实测）。
# ★ 为什么匹配 argv **逐项**而不是拼字符串做子串：派单原文与 窗口D 的命令行里
#   就含 'agentctl.brake_daemon' 这个串 —— 子串匹配会把「正在审这份派单的 agent」
#   数成一个 daemon（我第一版就这么数出了 3 个假 daemon）。

_PROC = ctypes.WinDLL("kernel32", use_last_error=False)
_PROC.OpenProcess.restype = ctypes.c_void_p
_PROC.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
_PROC.WaitForSingleObject.restype = ctypes.c_ulong
_PROC.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_PROC.CloseHandle.argtypes = [ctypes.c_void_p]


def proc_alive(pid):
    r"""进程是否还活着（纯 ctypes，不依赖 psutil —— 判据不该被可选依赖绑住）。"""
    h = _PROC.OpenProcess(0x00100000, 0, int(pid))      # SYNCHRONIZE
    if not h:
        # 打开失败有可能是「没权限」而不是「没这进程」—— 两者不能混。
        return ctypes.get_last_error() == 5            # ERROR_ACCESS_DENIED ⇒ 它还在
    try:
        return _PROC.WaitForSingleObject(h, 0) == 0x00000102   # WAIT_TIMEOUT
    finally:
        _PROC.CloseHandle(h)


def _is_daemon_argv(argv):
    """这个 argv 是不是急停 daemon 的起法（入口 A / B / C 各自认自己的）？"""
    low = [a.replace("/", "\\").rsplit("\\", 1)[-1].lower() for a in argv]
    if "-m" in low and "agentctl.brake_daemon" in low:
        return True                                  # 入口 C
    if "agentctl_main.py" in low and "brake-daemon" in low:
        return True                                  # 入口 B
    if "brake.py" in low and "--daemon" in low:
        return True                                  # 入口 A（退役线，也算 daemon 起法）
    return False


def _ancestor_pids():
    out = set()
    try:
        import psutil
        cur = psutil.Process(os.getpid())
        while cur is not None and cur.pid not in out:
            out.add(cur.pid)
            cur = cur.parent()
    except Exception:
        pass
    return out


def scan_daemons():
    r"""全桌面扫「急停 daemon 进程」⇒ [(pid, argv 摘要)]。psutil 缺了就如实说扫不了。"""
    try:
        import psutil
    except Exception:
        return None
    skip = _ancestor_pids()
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            argv = p.info["cmdline"] or []
        except Exception:
            continue
        if p.pid in skip or not argv:
            continue
        if _is_daemon_argv(argv):
            out.append((p.pid, " ".join(argv)[:110]))
    return out


def kill_proc(p):
    for fn in (lambda: p.kill(), lambda: p.wait(timeout=10)):
        try:
            fn()
        except Exception:
            pass


# ── 窗P2：起入口的沙箱 ─────────────────────────────────────────────────
def _mk_sandbox(prefix="wP2_entry_"):
    r"""每个入口一份独立沙箱状态源。

    ★ 六条安全设计照 work\_急停演练_受控_20261016.py：状态源一律指到 %TEMP%，
      拉闸/解除走**正规通道**（这里就是建/删沙箱 flag —— daemon 只认 flag 文件），
      全程只读枚举，绝不碰真急停文件。
    """
    import json
    sb = tempfile.mkdtemp(prefix=prefix)
    flag = os.path.join(sb, PROD_FLAG_NAME)
    tl = os.path.join(sb, "tl")
    os.makedirs(tl, exist_ok=True)
    rec = {"v": 1, "seq": 1, "kind": "lease_acquire", "t_mono": 0.0,
           "t_wall": "2026-10-16T22:00:00.000+08:00", "actor": FROZEN_ACTOR,
           "tool": "ctl_lease", "ok": True, "code": 0, "payload": {},
           "session": FROZEN_SESSION, "prev_hash": "0" * 64, "hash": "f" * 64}
    with open(os.path.join(tl, "timeline-20261016.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + NL)
    return sb, flag


def _sb_env(sb, flag):
    e = dict(os.environ)
    e["AGENTCTL_BRAKE_FLAG"] = flag
    e["AGENTCTL_APP_DIR"] = sb
    # 里面**不放 gate.py** ⇒ 按 gate.py:296 的口径它只是个「状态目录」，豁免体检。
    # 于是 gate.log 落在 <sb>\lstate\_auth\ 下，抢占证据一沙箱一份，不污染仓。
    e["AGENTCTL_LEGACY_HOME"] = os.path.join(sb, "lstate")
    e["AGENTCTL_TIMELINE"] = os.path.join(sb, "tl")
    e["PYTHONPATH"] = SRC
    return e


def _spawn(argv, env):
    import subprocess
    import threading
    p = subprocess.Popen([sys.executable, "-X", "utf8"]
                         + [str(a).replace("/", os.sep) for a in argv],
                         env=env, cwd=REPO, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    lines = []

    def _drain():
        try:
            for ln in p.stdout:
                lines.append(ln.decode("utf-8", "replace"))
        except Exception:
            pass

    threading.Thread(target=_drain, daemon=True).start()
    return p, lines


def _sample_popups(sec, step=0.25):
    """在 sec 秒里反复数**可见**弹窗，返回 (峰值, 采样次数)。"""
    peak, n = 0, 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        n += 1
        peak = max(peak, len(_visible_popups()))
        time.sleep(step)
    return peak, n


def _wait_gone_visible(sec):
    """等到桌面上**可见**的急停弹窗 0 个（不可见的不算 —— 它们没在屏幕上）。"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        if not _visible_popups():
            return True, time.monotonic() - t0
        time.sleep(0.2)
    return False, time.monotonic() - t0


def _strip_ctrl(s):
    """去掉控制字符（保留换行/制表）—— 只用于**比对**，绝不拿去显示。"""
    return "".join(c for c in (s or "") if c in "\r\n\t" or ord(c) >= 32)

# ── 窗P2：⑧' 三条入口矩阵 ────────────────────────────────────────────
#
# 母单 §二② 的缺口是「文档入口从未进判据」。更正之后这件事变成**三条入口三种期望**：
#   A 退役线 ⇒ 必须**响亮拒绝**；B 真·文档入口 ⇒ 必须弹窗；C 模块入口 ⇒ 不许再秒退。
#   只测其中一条，那个缺口就换个形状再回来 —— 「一个功能两条路，只维护了一条」。
WORK = os.path.join(REPO, "work")
_SPAWNED = []            # 本次起过的子进程 pid，收尾复查逐个验死活
_SANDBOXES = []          # 本次用过的沙箱目录，收尾复查逐个验 flag 不存在


def _assert_body(tag, f, flag):
    r"""正文断言：两档口径 + （万一）像素兜底**只报验不了**。

    ★ 更正(d)：一个 API 没读到，只能推出「这个 API 没读到」。
      本函数先把档①/档②用尽；两档都空才落到像素，而像素**只报「验不了」**，
      绝不单独给人判红 —— 更正(e) 实测过那条启发式在深色主题下会把画满的窗判成没画。
    """
    body, tier = f.get("body", ""), f.get("tier", "")
    if body:
        check("%s6 · ★正文**跨进程读到非空**（%s，%d 字）—— "
              "GetWindowTextW 读空只说明那个 API 读不到跨进程控件，不说明屏上没画"
              % (tag, tier, len(body)), bool(body))
        miss = [s for s in FROZEN_SECTIONS if s not in body]
        check("%s6' · 正文三段齐全（谁 / MCP 链接 / 怎么恢复），缺：%s"
              % (tag, miss or "无"), not miss)
        check("%s6'' · 正文里带着**沙箱状态源原路径**（%s）与夹具 actor（%r）—— "
              "证明它读的是这条链路，不是编的" % (tag, flag, FROZEN_ACTOR),
              _has(body, flag) and FROZEN_ACTOR in body)
        return True
    px = f.get("px") or {}
    print("  [~] %s6 · 前两档都读不到正文 ⇒ **只能报「验不了」**。像素是启发式，"
          % tag)
    print("       按更正(e) 不许拿它判红（窗N3 那条就在深色主题下把画满的窗判成了没画）。")
    print("       人眼可复核的物证：BMP=%s" % (px.get("bmp") or "（没抓到）"))
    print("       像素三个数（与主题无关）：不同颜色数=%s 主色占比=%s%% 中位色=%s"
          % (px.get("colours"), px.get("modal"), px.get("median")))
    print("       正文区 rect=%s PrintWindow=%s 抓图备注=%s"
          % (px.get("rect"), px.get("ok"), px.get("why") or "无"))
    SKIP.append("%s6 正文内容（档①/档②都空 ⇒ 按更正(e) 不拿像素判红；物证见 %s）"
                % (tag, px.get("bmp") or "work（未抓到）"))
    return False


def _live_entry(tag, entry):
    """起一条真入口 → 写沙箱 flag → 等弹窗 → 两档读正文 → 解除 → 收干净。"""
    import shutil as _sh
    sb, flag = _mk_sandbox("wP2_%s_" % tag)
    _SANDBOXES.append(sb)
    env = _sb_env(sb, flag)
    f = {"tag": tag, "entry": entry, "sb": sb, "flag": flag, "popup": False,
         "tier": "", "body": "", "verdict": "?", "t0": time.monotonic()}
    p, lines = _spawn(entry, env)
    _SPAWNED.append(p.pid)
    f["proc"] = p
    try:
        time.sleep(3.0)                     # 注册热键 + 可见层 preflight
        blob = NL.join(lines)
        f["pre_popup"] = len(_visible_popups())
        if p.poll() is not None and any(m in blob for m in HOTKEY_FAIL_MARKS):
            f["verdict"], f["blob"] = "hotkey-busy", blob
            return f
        if p.poll() is not None:
            f["verdict"], f["blob"] = "died", blob
            return f
        f["verdict"] = "alive"
        open(flag, "w", encoding="utf-8").write("判据拉闸（入口 %s）" % tag)
        h, dt = 0, 0.0
        t0 = time.monotonic()
        while time.monotonic() - t0 < POPUP_E2E_DEADLINE_SEC:
            ps = _visible_popups()
            if ps:
                h, dt = ps[0], time.monotonic() - t0
                break
            time.sleep(0.2)
        f["h"], f["dt"] = h, dt
        if h:
            f["popup"] = True
            f["title"] = wtext(h)
            f["topmost"] = bool(exstyle(h) & WS_EX_TOPMOST)
            body, tier = read_body(h, POPUP_E2E_DEADLINE_SEC)
            f["body"], f["tier"] = body, tier
            if not body:
                f["px"] = pixel_evidence(
                    h, os.path.join(WORK, "_wP2_弹窗像素_入口%s.bmp" % tag))
        try:
            os.remove(flag)
        except OSError:
            pass
        ok, dt2 = _wait_gone_visible(DAEMON_POLL_SEC * 4)
        f["gone"], f["gone_dt"] = ok, dt2
        f["blob"] = NL.join(lines)
        return f
    finally:
        f["alive_sec"] = round(time.monotonic() - f["t0"], 2)
        kill_proc(p)
        time.sleep(1.5)      # ★ 等 Windows 放掉热键，否则下一个入口会「假抢不到」
        _sh.rmtree(sb, ignore_errors=True)


def _assert_live(tag, f):
    """把一条真入口的结果摊成断言（入口 B / C 共用）。"""
    cmd = " ".join(str(a) for a in f["entry"])
    if f["verdict"] == "hotkey-busy":
        why = "机器上已有别的 daemon 占着 Ctrl+Alt+Home（本窗没抢到，弹窗那几条验不了）"
        for tail in ("2 · 抢到热键并出声（输出里有「已监听 Ctrl+Alt+Home」）",
                     "3 · 未拉急停时桌面上**不该有窗**",
                     "4 · ★写沙箱 flag 后 %.0fs 内弹出可见窗口" % POPUP_E2E_DEADLINE_SEC,
                     "5 · 弹窗是**桌面置顶**的（跨进程实读 EXSTYLE）",
                     "6 · 正文内容（两档口径）",
                     "7 · 解除 flag 后弹窗**自动消失**（%.0fs 内）"
                     % (DAEMON_POLL_SEC * 4)):
            SKIP.append("%s%s：%s" % (tag, tail, why))
            print("  [~] %s%s：%s" % (tag, tail, why), flush=True)
        print("       占着热键的那份原话：%s" % f.get("blob", "").strip()[:260],
              flush=True)
        return
    if f["verdict"] == "died":
        check("%s1 · ★**不许静默秒退**（起 %s 后 %.0fs 内就退了 ⇒ %s）"
              % (tag, cmd, SILENT_EXIT_SEC, f.get("blob", "").strip()[:200] or "输出全空"),
              False, "守护进程退了就等于热键是哑的，而用户以为它能救命")
        SKIP.append("%s2/3/4/5/6/7：入口 %s 没活下来，弹窗那几条无从验" % (tag, cmd))
        return
    blob = f.get("blob", "")
    check("%s1 · ★**不许静默秒退**（起 %s 后活了 %.1fs，远大于一次轮询间隔 %.0fs）"
          % (tag, cmd, f.get("alive_sec", 0.0), DAEMON_POLL_SEC),
          f.get("alive_sec", 0.0) >= SILENT_EXIT_SEC,
          "只活了 %.2fs" % f.get("alive_sec", 0.0))
    check("%s2 · 抢到热键并出声（输出里有「已监听 Ctrl+Alt+Home」）" % tag,
          "已监听 Ctrl+Alt+Home" in blob, "输出=%r" % blob[:300])
    check("%s3 · 未拉急停时桌面上**不该有窗**（实测可见弹窗 %d 个）"
          % (tag, f.get("pre_popup", -1)), f.get("pre_popup") == 0)
    check("%s4 · ★写沙箱 flag 后 %.0fs 内弹出**可见**窗口（实测 %.2fs；"
          "daemon 轮询间隔 %.0fs，这里给了 %d 倍余量）"
          % (tag, POPUP_E2E_DEADLINE_SEC, f["dt"], DAEMON_POLL_SEC,
             int(POPUP_E2E_DEADLINE_SEC / DAEMON_POLL_SEC)), bool(f["popup"]),
          "等了 %.2fs 没有可见弹窗" % f["dt"])
    if not f["popup"]:
        for tail in ("5 · 弹窗是**桌面置顶**的（跨进程实读 EXSTYLE）",
                     "6 · 正文跨进程读到非空（两档口径）",
                     "6' · 正文三段齐全",
                     "6'' · 正文带沙箱状态源原路径与夹具 actor",
                     "7 · 解除 flag 后弹窗**自动消失**"):
            SKIP.append("%s%s：窗压根没出现（%s 已红），无从验"
                        % (tag, tail, tag + "4"))
            print("  [~] %s%s：窗压根没出现（%s 已红），无从验"
                  % (tag, tail, tag + "4"), flush=True)
        return
    if f["popup"]:
        check("%s5 · 弹窗是**桌面置顶**的（跨进程实读 EXSTYLE，标题=%r）"
              % (tag, f.get("title", "")), f.get("topmost"),
              "WS_EX_TOPMOST 那一位没置上")
        _assert_body(tag, f, f["flag"])
        check("%s7 · 解除 flag 后弹窗**自动消失**（%.0fs 内可见弹窗 0 个；实测 %.2fs）"
              % (tag, DAEMON_POLL_SEC * 4, f["gone_dt"]), f["gone"],
              "等了 %.2fs 还在屏幕上" % f["gone_dt"])

def _run_cmd(argv, env, timeout=40):
    """跑一条命令等它结束，返回 (rc, 原文, 剔掉控制字符的原文)。"""
    import subprocess
    p = subprocess.run([sys.executable, "-X", "utf8"]
                      + [str(a).replace("/", os.sep) for a in argv],
                       env=env, cwd=REPO, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    raw = ((p.stdout or b"").decode("utf-8", "replace") + NL
           + (p.stderr or b"").decode("utf-8", "replace"))
    return p.returncode, raw, _strip_ctrl(raw)


def run_entry_A():
    r"""入口 A：**已退役的文档入口**（更正(c)：窗Q2 已把它改成 rc=2）。

    这里断言的是「**响亮拒绝**」这一整套，而不是「它不弹窗」这一件事：
      退役线最容易出的两种坏 —— ① 悄悄常驻（用户以为热键有人管，其实没人管）；
      ② 悄悄拉闸（屏幕上什么都没有，却把所有自动控制都停了）。
    前者更坏的是它**还占着** Ctrl+Alt+Home，让新线注册不上。
    """
    import shutil as _sh
    section("⑧'1 窗P2 · 入口 A = legacy_tools\\brake.py --daemon（窗Q2 已退役）")
    sb, flag = _mk_sandbox("wP2_A_")
    _SANDBOXES.append(sb)
    env = _sb_env(sb, flag)
    try:
        t0 = time.monotonic()
        rc, raw, clean = _run_cmd(ENTRY_A, env)
        dt = time.monotonic() - t0
        check("8A1 · ★退役线**响亮拒绝**：rc=2（不是 rc=0 的假成功，也不是无输出地退）",
              rc == 2, "实测 rc=%s，耗时 %.2fs，输出=%r" % (rc, dt, clean[:260]))
        check("8A2 · 拒绝文案**说清自己已退役**（出现「已退役」）",
              "已退役" in clean, "实测输出=%r" % clean[:260])
        new_entry = ("agentctl_main.py brake-daemon" in clean
                     or "AgentCtl.exe brake-daemon" in clean)
        check("8A3 · 拒绝文案**指向新线入口**（`agentctl_main.py brake-daemon` 或 "
              "`AgentCtl.exe brake-daemon` —— 告诉人该敲哪条）", new_entry,
              "实测输出=%r" % clean[:400])
        ctrl = sorted(set(c for c in raw if ord(c) < 32 and c not in "\r\n\t"))
        if ctrl:
            # ★ 只播报、**不判红**（红会让 run_all 永远绿不了，而这是产品侧的缺陷）。
            #   但必须让人看见：源码里写了未转义的反斜杠（\\a = BEL），
            #   照抄的用户会敲错命令 —— 那正是「以为看得见其实看不见」的另一种形状。
            print("  [⚠ 产品缺陷·本窗无权改·不计红] 退役文案里有裸控制字符 %s："
                  % ["U+%04X" % ord(c) for c in ctrl])
            print("       说明 legacy_tools\\brake.py:132 的非 raw 字符串里 "
                  "`src\\agentctl_main.py` 的 \\a 被当成了 BEL，用户照抄会敲错命令。")
            print("       判据 8A3 用的是「剔掉控制字符后再比对」的口径，"
                  "所以它判的是**意图**（指没指对地方），不是这段排版。")
            print("       处置：交产品侧（窗Q2 那一侧）把该行改成 raw 字符串或转义双反斜杠。")
        check("8A4 · **不常驻**：命令自己退了（%.2fs，rc=%s）—— "
              "它要是还占着 Ctrl+Alt+Home，新线就注册不上" % (dt, rc),
              rc is not None and rc == 2, "rc=%s" % rc)
        peak, n = _sample_popups(OBSERVE_SEC)
        check("8A5 · 观察 %.0fs（3×%.0fs 轮询间隔，采样 %d 次）：可见弹窗 0 个、"
              "沙箱 flag 不存在 —— 退役线不许偷偷变成「看不见的刹车」"
              % (OBSERVE_SEC, DAEMON_POLL_SEC, n),
              peak == 0 and not os.path.exists(flag),
              "峰值 %d 个可见弹窗；flag %s"
              % (peak, "还在" if os.path.exists(flag) else "已不在"))
        # ── 手工通道没被连累（退役的是常驻热键，不是 stop/release/status）──
        rc1, _, o1 = _run_cmd(("legacy_tools/brake.py", "stop", "--why",
                               "窗P2 判据（沙箱）"), env)
        check("8A6a · 手工通道 **stop 仍可用**（rc=0 且沙箱 flag 建起来了）",
              rc1 == 0 and os.path.exists(flag), "rc=%s 输出=%r" % (rc1, o1[:200]))
        rc2, _, o2 = _run_cmd(("legacy_tools/brake.py", "status"), env)
        check("8A6b · 手工通道 **status 仍可用**（rc=0 且说得出「已拉下」）",
              rc2 == 0 and "已拉下" in o2, "rc=%s 输出=%r" % (rc2, o2[:200]))
        rc3, _, o3 = _run_cmd(("legacy_tools/brake.py", "release"), env)
        check("8A6c · 手工通道 **release 仍可用**（rc=0 且沙箱 flag 删掉了）",
              rc3 == 0 and not os.path.exists(flag), "rc=%s 输出=%r" % (rc3, o3[:200]))
        rc4, _, _ = _run_cmd(("legacy_tools/brake.py", "status"), env)
        check("8A6d · 松手后 status 报 rc=1（老线的退出码口径没被改坏）",
              rc4 == 1, "rc=%s" % rc4)
    finally:
        shutil.rmtree(sb, ignore_errors=True)


def run_contention():
    r"""⑧'4 抢占必须**显式**：两份 daemon 同时抢 Ctrl+Alt+Home，谁输谁必须出声。

    ★ 为什么这条不能省：老线退役、新线上位之后，Ctrl+Alt+Home 全机器**只有一把**。
      第二份 daemon 抢不到时如果默默跑，它就是一个「在日志里说自己活着、
      其实热键在别人手上」的进程 —— 和当初「以为看得见其实看不见」一模一样。
    """
    import shutil as _sh
    section("⑧'4 窗P2 · 抢占必须显式：两份 daemon 同时抢 Ctrl+Alt+Home")
    sb, flag = _mk_sandbox("wP2_P_")
    _SANDBOXES.append(sb)
    env = _sb_env(sb, flag)
    pa = pb = None
    try:
        pa, la = _spawn(ENTRY_B, env)
        _SPAWNED.append(pa.pid)
        time.sleep(3.0)
        if pa.poll() is not None:
            SKIP.append("⑧'4 抢占：连第一份都抢不到 Ctrl+Alt+Home（机器上已有别的 daemon）"
                        "⇒ 本组验不了，不判红")
            print("  [~] %s" % SKIP[-1], flush=True)
            print("       原话：%s" % NL.join(la).strip()[:260], flush=True)
            return
        pb, lb = _spawn(ENTRY_B, env)
        _SPAWNED.append(pb.pid)
        t0 = time.monotonic()
        while pb.poll() is None and time.monotonic() - t0 < 15:
            time.sleep(0.25)
        rcb = pb.poll()
        blob_b = NL.join(lb)
        check("8P1 · ★第二份必须**明确失败**（rc=2），不许默默跑、也不许默默退成 rc=0",
              rcb == 2, "实测 rc=%s（等了 %.1fs）输出=%r"
              % (rcb, time.monotonic() - t0, blob_b[:300]))
        hit = [m for m in HOTKEY_FAIL_MARKS if m in _strip_ctrl(blob_b)]
        check("8P2 · ★输的那份**说得出输在哪**（输出里有 %s）"
              % " / ".join(HOTKEY_FAIL_MARKS), bool(hit),
              "输出=%r" % _strip_ctrl(blob_b)[:300])
        gl = os.path.join(sb, "lstate", "_auth", "gate_agentctl.log")
        glog = ""
        if os.path.isfile(gl):
            with open(gl, encoding="utf-8", errors="replace") as fh:
                glog = fh.read()
        check("8P3 · 输家**留痕**在 gate.log 里（不只在屏幕上那一瞬；%s）"
              % os.path.basename(gl), "注册失败" in glog,
              "gate.log=%r" % glog[-300:])
        print("       —— 谁输了、输在哪，看得见（输家原文）：")
        for ln in blob_b.strip().splitlines()[:4]:
            print("         " + ln)
        pre = len(_visible_popups())
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write("判据拉闸（抢占组）")
        t0 = time.monotonic()
        h = 0
        while time.monotonic() - t0 < POPUP_E2E_DEADLINE_SEC:
            ps = _visible_popups()
            if ps:
                h = ps[0]
                break
            time.sleep(0.2)
        check("8P4 · ★赢的那份**不受影响**：抢不到的那份退了，赢家照样按 flag 弹窗"
              "（%.0fs 内；实测 %.2fs）"
              % (POPUP_E2E_DEADLINE_SEC, time.monotonic() - t0), bool(h),
              "赢家也没弹（拉闸前可见弹窗 %d 个）" % pre)
        if h:
            body, tier = read_body(h, POPUP_E2E_DEADLINE_SEC)
            if body:
                check("8P5 · 抢占之后赢家的正文**照样读得到**（%s，%d 字）—— "
                      "抢占不许把「看得见」也一起挤掉" % (tier, len(body)), True)
            else:
                # ★ 与 8B6/8C6 **同一条口径**：两档都空只能写「验不了」。
                #   8P5 若在这里判红，就等于本判据自己又犯了更正(d) 那个错。
                px = pixel_evidence(h, os.path.join(WORK,
                                                    "_wP2_弹窗像素_抢占.bmp"))
                print("  [~] 8P5 · 前两档都读不到正文 ⇒ **报「验不了」不判红**"
                      "（同 8B6/8C6 的口径：API 没读到 ≠ 屏上没画）")
                print("       物证 BMP=%s / 不同颜色数=%s 主色占比=%s%% 中位色=%s"
                      % (px.get("bmp"), px.get("colours"), px.get("modal"),
                         px.get("median")))
                SKIP.append("8P5 正文内容（两档都空 ⇒ 不拿像素判红）")
        try:
            os.remove(flag)
        except OSError:
            pass
        gone, _ = _wait_gone_visible(DAEMON_POLL_SEC * 4)
        check("8P6 · 抢占组解除后弹窗也自动消失（%.0fs 内）"
              % (DAEMON_POLL_SEC * 4), gone)
    finally:
        for p in (pb, pa):
            if p is not None:
                kill_proc(p)
        time.sleep(1.5)
        _sh.rmtree(sb, ignore_errors=True)


def run_cleanup():
    """⑩ 收尾自证：跑完桌面必须干净 —— 三样都断言，一样都不许含糊。"""
    section("⑩ 窗P2 · 收尾自证：跑完桌面必须干净")
    left = [os.path.join(sb, PROD_FLAG_NAME) for sb in _SANDBOXES
            if os.path.exists(os.path.join(sb, PROD_FLAG_NAME))]
    check("10a · 沙箱急停 flag **全部不存在**（%d 个沙箱逐个查；不碰真急停）"
          % len(_SANDBOXES), not left, "还剩：%s" % left)
    peak, n = _sample_popups(1.0, 0.2)
    check("10b · 桌面上**可见**的急停弹窗 0 个（按 IsWindowVisible 过滤；采样 %d 次）"
          % n, peak == 0,
          "还有 %d 个可见弹窗：%s"
          % (peak, [(h, wtext(h)) for h in _visible_popups()]))
    alive = [pid for pid in _SPAWNED if proc_alive(pid)]
    check("10c · ★本次起的 daemon 进程 **0 个**（自己 spawn 的 %d 个 pid 全死）"
          % len(_SPAWNED), not alive, "还活着：%s" % alive)
    allp = scan_daemons()
    if allp is None:
        SKIP.append("10d 全桌面扫「急停 daemon」：本机没有 psutil，扫不了"
                    "（10c 仍验了本次起的那些 pid）")
        print("  [~] %s" % SKIP[-1], flush=True)
    else:
        check("10d · ★全桌面扫「急停 daemon」进程 **0 个**（argv **逐项**精确匹配，"
              "不做子串匹配 —— 否则派单原文里的 'agentctl.brake_daemon' "
              "会把正在审单的人数成 daemon）", not allp, "实测：%s" % allp)

    # ── 窗R 追加：自证「我一步都没碰生产」 ──────────────────────────────
    #
    # ★ 10a 只查沙箱 flag，那证明不了「没碰生产」—— 沙箱查得再干净，
    #   真生产目录里多出一个 _急停.flag 也没人管。这两条就是补这个洞。
    #   而且它们**能跑红**：把 install_env_sandbox() 的锁①去掉（回到本单出事前的样子），
    #   本判据弹的窗就会顶着生产路径判成真急停，10e 会当场红给你看。
    check("10e · ★★**真生产 flag 仍然不存在**（%s）—— 本判据一步都没碰它"
          % _REAL_PROD_FLAG, not os.path.exists(_REAL_PROD_FLAG),
          "它出现了！这说明有代码路径在往真生产写急停状态")
    after = snapshot_real_prod()
    before = _PROD_SNAP or []
    added = [a for a in after if a not in before]
    gone = [b for b in before if b not in after]
    check("10f · ★★**真生产目录没多出任何东西**（跑前 %d 项 / 跑后 %d 项，"
          "逐项比对 名字+大小+mtime）" % (len(before), len(after)),
          not added and not gone,
          "多出 %s / 少了 %s" % (added, gone))
    print("       真生产目录 %s：跑前 %s" % (_REAL_PROD_ROOT,
          [b[0] for b in before] or "（空）"), flush=True)
    print("                  跑后 %s" % ([a[0] for a in after] or "（空）"),
          flush=True)


def run_entry_matrix():
    """⑧' 组总入口：三条入口 + 抢占 + 收尾。"""
    # ★ 用**真**生产 flag 判活闸门，不是本进程挪过的那个：
    #   真有急停正在生效时，不该在桌面上再造任何窗（哪怕全都指向沙箱）。
    real_flag = _REAL_PROD_FLAG
    if os.path.exists(real_flag):
        msg = ("⑧' 三条入口矩阵：真急停文件已存在（%s）⇒ 整组验不了"
               "（本组绝不碰真急停，只用 %TEMP% 沙箱）" % real_flag)
        SKIP.append(msg)
        section("⑧' 窗P2 · 三条入口矩阵")
        print("  [~] %s" % msg, flush=True)
        return
    run_entry_A()
    for tag, entry, why in (
            ("B", ENTRY_B, "真·文档入口（更正(b)）"),
            ("C", ENTRY_C, "模块入口（更正(a) 补了 __main__ 之后）")):
        section("⑧'%s 窗P2 · 入口 %s = %s" % ("2" if tag == "B" else "3", tag, why))
        f = _live_entry(tag, entry)
        if tag == "C":
            # ★ 更正(a) 单独钉一条：`-m agentctl.brake_daemon` 在补 __main__ 之前
            #   是 **import 完就秒退**：rc=0、stdout/stderr 全空、屏幕上什么都不出现。
            #   判据要把「不许再秒退」单独写成一条，而不是混在「弹不弹窗」里 ——
            #   后者在「热键被别人占着」时会 SKIP，那正好放过秒退这个最毒的形态。
            alive = f.get("alive_sec", 0.0)
            blob = f.get("blob", "")
            silent = f["verdict"] != "alive" and alive < SILENT_EXIT_SEC and (
                not blob.strip() or _strip_ctrl(blob).strip() == "")
            check("C0 · ★`-m agentctl.brake_daemon` **不许秒退**："
                  "退出码非 0 或输出非空即算「有话可说」；"
                  "红 = rc=0 且无输出（= 改前的 __main__ 缺失那个 bug）",
                  not silent,
                  "活了 %.2fs，rc=%s，输出=%r"
                  % (alive, (f.get("proc").poll() if f.get("proc") else None),
                     _strip_ctrl(blob)[:200]))
        _assert_live(tag, f)
    run_contention()
    run_cleanup()

if __name__ == "__main__":
    sys.exit(main())
