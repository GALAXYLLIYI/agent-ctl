# -*- coding: utf-8 -*-
r"""M11 · 动作链分段测量（**只测量，不改产品行为**）

▌本文件是**测量器**，不是判据。判据在 `tests\_P1_动作链分段_判据.py`。
▌**插桩只发生在本文件里**（monkeypatch 本进程里的模块属性，跑完还原）⇒
  `src/` **一个字节都没动** ⇒ 不需要备份/MD5/diff，也不可能留残留。

▌测什么：一次生产路径的 `ctl_click`（core.dispatch → 子进程 act.py → 回读 → 落盘），
  拆成**互斥**的分段；外加 act 侧等待的**直测**（回答「是不是同一件事等了两遍」）。

▌目标窗口：**本文件自己开的记事本**（不碰用户正在用的任何窗口）。

用法：
    python -X utf8 tests\_M11_分段测量.py --out work\_M11_分段输出.txt
    python -X utf8 tests\_M11_分段测量.py --json      # 机器可读（判据用）
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agentctl import core as core_mod          # noqa: E402
from agentctl import framecache               # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


# ────────────────────────────── 窗口小工具 ──────────────────────────────
def _enum_windows():
    out = []
    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        if user32.IsWindowVisible(h):
            out.append(int(h))
        return True
    user32.EnumWindows(cb, 0)
    return out


def _title(h):
    n = user32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def _cls(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


#: ★ 本次开窗**新出现**的其它窗口（记事本自己弹出来的附属框，如
#:   「是否打开来自不受信任来源的文件?」）⇒ 收摊时一并关掉。
#:   派单 M16 红线第 9 条 / 用户 2026-10-22 12:58 当面提：**靶场窗口用完即关**。
OPEN_EXTRA = []
#: ★ 本次开窗**新出现**的窗口各自的 **pid**（收摊时只杀这些 ⇒ 用户的进程绝不会被碰）。
#:   实测（2026-10-22 16:2x 更正）：Win11 记事本是**单进程多 tab**，
#:   **每个 tab 一个顶层窗口**（同 pid）⇒ 一次开窗可能拉出 N 个窗口。
#:   ⇒ 杀「我这次记录下来的 pid」是安全的；而按进程名批量杀会连用户的那个一起杀 ⇒ ⛔。
OPEN_PIDS = []
#: ★ 本次开窗**新出现**的可见 Notepad 窗口 hwnd 集合（主控 2026-10-22 18:0x 给的认领口径）。
#:   收摊时**只关这些** ⇒ 不在集合里的（用户自己开的）**一个都不碰**。
#:   ⚠ 与 `OPEN_PIDS` 的区别：`OPEN_PIDS` 是**新起的进程** pid（才可以杀）；
#:     「窗口的 pid」可能属于**常驻实例**（用户 7:48 那个）⇒ 绝不许按窗口的 pid 杀。
OPEN_NEW_HWNDS = []
#: ★ 开窗**之前**的前台窗（收摊时先把它还回去）。
#:   ⚠⚠ 2026-10-22 16:2x 实测踩到的坑：**杀掉「正在前台的记事本」⇒ 桌面当场失去前台
#:     ⇒ 系统把前台判给一个**隐形的系统服务窗**（GameInputServiceWindow）
#:     ⇒ 之后**任何**进程都抢不到前台（UIPI 更高权限，连 PostMessage 都「拒绝访问」）
#:     ⇒ 所有需要真实点击的验收全灭（「置前失败 ⇒ 环境受阻」）。
#:   ⇒ 收摊顺序必须是：**先把前台还给用户原来的窗口 → 再关/杀记事本**。
PREV_FG = 0

# ─────────────────── ★ 记事本「会话恢复」的治本开关（2026-10-22 16:2x 实测）───────────────────
#: ★★ 根因（用户 2026-10-22 12:58 当面提的「一天堆到 185 个记事本」的真实机制）：
#:   Win11 记事本（Store 版）启动时从
#:     %LOCALAPPDATA%\Packages\Microsoft.WindowsNotepad_8wekyb3d8bbwe\LocalState\TabState\*.bin
#:   **恢复上次的每一个 tab**，而 Win11 的多文档架构里**每个 tab 就是一个顶层窗口**
#:   ⇒ 判据每开一次记事本，就把历史 tab **全部拉出来变成可见窗口**，
#:     而它只关自己那一个 ⇒ 每跑一次留下 N 个 ⇒ 一天下来 185 个。
#:   实测证据：一次开窗拉出 **11 个** `Notepad` 类窗口（标题 `*MM` / `*MMMM` / `*MMMMMMMMMM`），
#:   全部同一个 pid ⇒ 不是「多个进程」，是**一个进程里的多个 tab 窗口**。
#:
#: ★ 治本办法：**开窗前把 TabState 暂存（改名）** ⇒ 记事本没得恢复 ⇒ 只出 1 个干净窗口；
#:   **收摊后把 TabState 原样还原** ⇒ 用户的会话状态一个字节都不动。
#:   实测（work\_M16_加速第二刀_20221022\_探针_TabState暂存.py）：暂存后开窗只出 1 个「无标题」，
#:   收摊后可见窗口 0，TabState 11 个文件逐文件一致 ✔
#:
#: ⚠ 还原用**合并**而不是「删了再改名」：用户在我暂存期间若自己开了记事本，
#:   他新写的 TabState 文件必须**留着** ⇒ 只把暂存的那批**覆盖**回去。
NP_TABSTATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Packages", "Microsoft.WindowsNotepad_8wekyb3d8bbwe",
    "LocalState", "TabState")
#: 当前暂存目录（None = 没暂存）
TABSTATE_STASH = None
#: 暂存目录的后缀（用来认出「上次异常退出留下的孤儿」）
TABSTATE_STASH_SUFFIX = ".__agentctl_stash"


def _tabstate_stash_dirs():
    """当前 LocalState 下所有本工具的暂存目录。"""
    out = []
    try:
        base = os.path.dirname(NP_TABSTATE_DIR)
        for n in os.listdir(base):
            if n.startswith("TabState" + TABSTATE_STASH_SUFFIX):
                out.append(os.path.join(base, n))
    except Exception:
        pass
    return sorted(out)


def _merge_restore(stash_dir):
    """把 `stash_dir` 里的文件**原样搬回** TabState 目录。

    ★ 先**清空**当前 TabState 目录再搬回 ⇒ 本次会话自己造出来的 tab 条目不会留下
      （否则下一次开窗还会把它恢复出来 ⇒ 靶场窗口又变成 2 个）。
    ⚠ 代价：用户在**本次会话期间**自己新开的记事本 tab，其条目也会被清掉
      （记事本的内容另有自动保存文件兜着，不至于丢正文）。
      实测概率极低（判据/测量一次 1~3 分钟），且这条只影响「标签页列表」。
    """
    if not (stash_dir and os.path.isdir(stash_dir)):
        return False
    try:
        # ① 清掉本次会话造出来的
        if os.path.isdir(NP_TABSTATE_DIR):
            for n in os.listdir(NP_TABSTATE_DIR):
                try:
                    p = os.path.join(NP_TABSTATE_DIR, n)
                    if os.path.isfile(p):
                        os.remove(p)
                except Exception:
                    pass
        # ② 把暂存的那批原样搬回（用户原来的会话一个字都不改）
        os.makedirs(NP_TABSTATE_DIR, exist_ok=True)
        for n in os.listdir(stash_dir):
            s = os.path.join(stash_dir, n)
            if os.path.isfile(s):
                try:
                    os.replace(s, os.path.join(NP_TABSTATE_DIR, n))
                except Exception:
                    pass
        try:
            os.rmdir(stash_dir)
        except Exception:
            pass
        return True
    except Exception:
        return False


def restore_notepad_tabstate():
    """把暂存的 TabState 还原回去（**幂等**；没有暂存就什么都不做）。"""
    global TABSTATE_STASH
    st = TABSTATE_STASH
    TABSTATE_STASH = None
    if not st:
        return False
    return _merge_restore(st)


def stash_notepad_tabstate():
    """开靶场窗口**之前**调它：把记事本的 TabState 暂存起来。

    ★ 幂等 + 自愈：
      - 已经有暂存（同一进程里开了第二个窗）⇒ 什么都不做；
      - 发现**上次异常退出留下的孤儿暂存**（判据炸了没还原）⇒ 先把它还原回去
        （那份就是用户在等的会话数据，**必须先还**）⇒ 再暂存新的。
    """
    global TABSTATE_STASH
    if TABSTATE_STASH:
        return False
    # ① 孤儿自愈：上次崩了留下的暂存 ⇒ 先还原（用户在等这份数据）
    for orphan in _tabstate_stash_dirs():
        _merge_restore(orphan)
    # ② 暂存当前的
    if not os.path.isdir(NP_TABSTATE_DIR):
        return False
    stash = NP_TABSTATE_DIR + TABSTATE_STASH_SUFFIX + "_%d" % os.getpid()
    try:
        os.replace(NP_TABSTATE_DIR, stash)
    except Exception:
        return False
    TABSTATE_STASH = stash
    return True



def _pid_of(hwnd):
    """窗口的宿主进程 pid（取不到返回 0）。"""
    try:
        import win32process
        return int(win32process.GetWindowThreadProcessId(int(hwnd))[1])
    except Exception:
        return 0


def _visible_notepad_hwnds():
    """当前**可见**的 Notepad 类顶层窗口 hwnd 集合。"""
    out = set()
    for h in _enum_windows():
        try:
            if _cls(h) == "Notepad":
                out.add(int(h))
        except Exception:
            continue
    return out


def _notepad_pids():
    """当前**所有** Notepad 进程 pid 集合（不管有没有窗口）。

    ★ 用来分辨「**新起的实例**」与「**早就常驻的实例**」：
      主控实测本机记事本是**单进程常驻**（pid 41492 从 7:48 活到现在）⇒
      「本次新出现的窗口」**可能就属于那个常驻实例**
      ⇒ **按窗口算 pid 再杀进程 = 可能杀掉用户的实例** ⇒ ⛔ 绝对不许。
      ⇒ 只允许杀「开窗**后新出现**的进程」＝ after_pids − before_pids。
    """
    out = set()
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if (p.info.get("name") or "").lower() == "notepad.exe":
                    out.add(int(p.info["pid"]))
            except Exception:
                continue
        return out
    except Exception:
        pass
    try:
        import subprocess as _sp
        txt = _sp.run(["tasklist", "/FI", "IMAGENAME eq Notepad.exe", "/NH"],
                       capture_output=True, text=True, timeout=15).stdout
        for line in txt.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].lower() == "notepad.exe":
                try:
                    out.add(int(parts[1]))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _clear_notepad_doc(hwnd):
    """把记事本窗口的**文档内容清空**（对子窗口 `RichEditD2DPT` 发 `WM_SETTEXT ""`）。

    ★ 为什么必须先清空再关：Win11 记事本有未保存内容时，`WM_CLOSE` 会逐个弹
      「是否保存」⇒ 收摊要多一轮弹框处理，而且**弹框本身又是一个可见窗口**
      ⇒ 正是「越跑越多」的另一半来源。清空后标题的 `*` 消失 ⇒ 关窗是静默的。
    ★ 只对**我认领的**窗口做（用户自己的窗口一个字节都不碰）。
    """
    try:
        import win32gui
        h = int(hwnd)
        if not win32gui.IsWindow(h):
            return False
        cands = []

        def _cb(c, _l, _out=cands):
            try:
                if win32gui.GetClassName(c) == "RichEditD2DPT":
                    _out.append(c)
            except Exception:
                pass
            return True
        try:
            win32gui.EnumChildWindows(h, _cb, None)
        except Exception:
            pass
        ok = False
        for c in cands:
            try:
                win32gui.SendMessage(int(c), 0x000C, 0, "")      # WM_SETTEXT
                ok = True
            except Exception:
                pass
        return ok
    except Exception:
        return False


def open_own_notepad(content=""):
    """开**自己的**记事本，返回 (hwnd, proc)。找不到可见窗口就如实报错。

    ★★★ 认领三件套（主控 2026-10-22 18:0x 给的精确机制，照它做，不靠"记得关"）：
      ① `OPEN_NEW_HWNDS` = **开窗后新出现的可见 Notepad 窗口**（差集认亲）
         ⇒ 收摊时**只关这些**；不在集合里的（用户自己开的）**一个都不碰**；
      ② `OPEN_PIDS` = **开窗后新出现的 Notepad 进程** pid（不是「窗口的 pid」！）
         ⇒ 只杀这些 ⇒ **常驻实例（用户 7:48 那个）绝不会被杀**
         （★ 早前的写法是「按新窗口算 pid」⇒ 若新窗口属于常驻实例就会误杀用户 ⇒ 已改）；
      ③ `OPEN_EXTRA` = 新出现的**非 Notepad** 顶层窗口（附属弹框，如 README 信任弹框）。

    ★ `content`（可选）：开窗后**往靶场里敲几个字**。
      ⚠ 为什么需要它：**TabState 暂存治本之后，靶场记事本是空的**（不再恢复上次的 tab）
        ⇒ 判据若要拿「**有字的**」画面（例：A② 两条抓图路径必须比同一块有字的区域），
        就**没有字可读** ⇒ 只能自己写一点。
        **这不是改判据口径，是把靶场备好**（靶场本来就是造出来给判据用的）。
    """
    global OPEN_EXTRA, OPEN_PIDS, PREV_FG, OPEN_NEW_HWNDS
    # ★ 治本：先把记事本的 TabState 暂存起来 ⇒ 记事本**不会**把上次的 tab 恢复成窗口
    #   （不开这个开关，一次开窗就会把 N 个历史 tab 拉出来，而判据只关自己那一个
    #     ⇒ 每跑一次留 N 个 ⇒ 一天 185 个。用户 2026-10-22 12:58 当面提的问题。）
    stash_notepad_tabstate()
    # ★ 记下**开窗前**的前台（收摊时先还回去 ⇒ 桌面不会因为「前台窗被杀」而失去前台）
    try:
        PREV_FG = int(user32.GetForegroundWindow() or 0)
    except Exception:
        PREV_FG = 0
    before = set(_enum_windows())
    before_np = _visible_notepad_hwnds()        # ★ 认领用：开前已有的 Notepad 窗口
    before_pids = _notepad_pids()              # ★ 认领用：开前已有的 Notepad 进程
    p = subprocess.Popen(["notepad.exe"])
    hwnd = 0
    for _ in range(40):
        time.sleep(0.25)
        now = set(_enum_windows())
        new = [h for h in now - before if _cls(h) == "Notepad"]
        if new:
            hwnd = new[0]
            # ★ 本次新出现的**全部**可见 Notepad 窗口（主窗 + 可能带出来的 tab 窗口）
            OPEN_NEW_HWNDS = [int(h) for h in sorted(_visible_notepad_hwnds() - before_np)]
            if hwnd not in OPEN_NEW_HWNDS:
                OPEN_NEW_HWNDS.insert(0, int(hwnd))
            # ★ 非 Notepad 的新窗口 = 附属弹框（README 信任弹框之类）
            OPEN_EXTRA = [h for h in now - before if _cls(h) != "Notepad"]
            # ★ ★ 只认领「**新起的进程**」⇒ 常驻实例（用户那个）绝不被杀
            OPEN_PIDS = [x for x in sorted(_notepad_pids() - before_pids) if x]
            break
    if not hwnd:
        try:
            p.kill()
        except Exception:
            pass
        OPEN_EXTRA = []
        OPEN_PIDS = []
        OPEN_NEW_HWNDS = []
        # ★ 开不出窗口 ⇒ 立刻把暂存的 TabState 还原回去（不许让用户的会话停在暂存态）
        try:
            restore_notepad_tabstate()
        except Exception:
            pass
        raise RuntimeError("没能开出自己的记事本（无新可见 Notepad 窗口）")
    if content:
        try:
            type_into_own_notepad(hwnd, content)
        except Exception:
            pass
    return hwnd, p


def type_into_own_notepad(hwnd, text):
    """往**自己的**靶场记事本里敲几个字（只让画面上有字，判据才拿得到「有字的」区域）。

    ★ 只碰自己的窗口：先把靶场顶到前台 ⇒ 敲字 ⇒ **把前台还给用户原来的窗口**。
    ⚠ 敲完记事本是「脏」的（有未保存内容）⇒ 收摊会弹「是否保存」
      ⇒ `close_own_notepad` 已经管这个（认领新弹框 → 点「不保存」→ 只杀本次 pid）。
    """
    prev = int(user32.GetForegroundWindow() or 0)
    try:
        user32.SetForegroundWindow(int(hwnd))
        time.sleep(0.25)
        for ch in str(text):
            # ★ 用 VkKeyScanExW 取**修饰键状态**（Shift 等）——
            #   早前用 VkKeyScanW 只取低字节 ⇒ 大写字母打成了小写、还串了别的键
            #   （实测敲 "MM" 出来是 "m'm"）⇒ 靶场内容不可控 ⇒ OCR 读到的词也不可控。
            #   ⚠ VkKeyScanExW 的修饰键是**出参**（pKeyState），**不是**返回值的高位
            #   ⇒ 必须传 byref 的 c_ulong（传 None 会让这次调用直接失效）。
            _state = ctypes.c_ulong(0)
            sc = user32.VkKeyScanExW(ord(ch), ctypes.byref(_state))
            vk = sc & 0xFF
            shift = bool(_state.value & 1)
            if not vk:
                continue
            if shift:
                user32.keybd_event(0x10, 0, 0, 0)      # VK_SHIFT down
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(vk, 0, 2, 0)
            if shift:
                user32.keybd_event(0x10, 0, 2, 0)      # VK_SHIFT up
            time.sleep(0.02)
        time.sleep(0.3)
    finally:
        try:
            if prev and user32.IsWindow(prev):
                user32.SetForegroundWindow(prev)
                time.sleep(0.1)
        except Exception:
            pass


def count_visible_notepads():
    """当前**可见**的 Notepad 窗口数（判据的「靶场窗口用完即关」断言用）。

    ⚠ 用 **ctypes**（本文件通篇用 ctypes，没 import win32gui）——
      改前写的是 `win32gui.IsWindowVisible` ⇒ NameError 被 except 吞掉 ⇒ **恒返回 0**
      ⇒ 断言会**假绿**（实测踩到：开着时也是 0）。
    """
    n = 0
    for h in _enum_windows():
        try:
            if _cls(h) == "Notepad":
                n += 1
        except Exception:
            continue
    return n


def snapshot_visible_notepads():
    """当前**可见**的 Notepad 窗口快照集合：``{(hwnd, 标题), ...}``。

    ★ 主控 2026-10-22 18:0x 要求的断言口径：「**跑前可见 Notepad 窗口集合
      ⇒ 跑后完全相同**」—— 只比**个数**不够（关掉 A 又开出 B，个数相同但集合变了）。
      ⇒ 这里连 **hwnd 与标题**一起记，判据做**集合逐项比对**。
    ★ hwnd 会被系统复用 ⇒ 所以**标题也带上**（换了内容/标题 ⇒ 也算变了）。
    """
    out = set()
    for h in _enum_windows():
        try:
            if _cls(h) == "Notepad":
                out.add((int(h), _title(h)))
        except Exception:
            continue
    return out


def _dismiss_save_dialog(after_win, timeout=2.0, extra_before=None):
    """把「要保存吗？」那个弹框按掉（点**不保存**）。

    ★ 为什么需要它：记事本有未保存内容时，WM_CLOSE 会弹这个框 ⇒ 窗口**不消失**
      ⇒ 判据跑完就留下一个可见窗口（用户 2026-10-22 12:58 当面提的：一天堆到 185 个）。
    ★ 认亲用 **「关窗后新出现的窗口」**（`extra_before` = 关窗前的可见窗口集合）：
      只点**新出现**的那个 ⇒ 用户自己的弹框/窗口**绝不**被碰。
    ★ 两条找法（Win11 的保存对话框是 **XAML**，类名常常**不是** #32770）：
      ① win32gui 枚举子窗口，按按钮文本匹配；
      ② UIA 按 Name 匹配（现代 XAML 对话框只有 UIA 读得到按钮名）。
    """
    import win32gui
    t0 = time.time()
    before = set(extra_before or [])
    while time.time() - t0 < timeout:
        cands = []
        for h in _enum_windows():
            if h in before:
                continue                       # 用户原有的 ⇒ 不碰
            try:
                if not win32gui.IsWindowVisible(h):
                    continue
                cn = win32gui.GetClassName(h)
                ti = win32gui.GetWindowText(h) or ""
                if cn == "#32770" or "记事本" in ti or "Notepad" in ti:
                    cands.append(h)
            except Exception:
                continue
        for h in cands:
            btns = []
            def _cb2(c, _l, _out=btns):
                try:
                    t = win32gui.GetWindowText(c)
                    if t and ("不保存" in t or t.strip() in ("否", "No", "否(&N)")):
                        _out.append(c)
                except Exception:
                    pass
                return True
            try:
                win32gui.EnumChildWindows(h, _cb2, None)
            except Exception:
                pass
            if btns:
                for b in btns:
                    try:
                        win32gui.PostMessage(int(b), 0x00F5, 0, 0)     # BM_CLICK
                    except Exception:
                        pass
                return True
            # ② UIA 兜底（XAML 对话框）
            try:
                import uiautomation as auto
                w = auto.ControlFromHandle(int(h))
                for name in ("不保存", "不保存(&N)", "No", "否"):
                    try:
                        btn = w.ButtonControl(searchDepth=6, Name=name)
                        if btn.Exists(0.2):
                            btn.Click(simulateMove=False)
                            return True
                    except Exception:
                        continue
            except Exception:
                pass
        time.sleep(0.1)
    return False


def _set_notepad_text(hwnd, text):
    """给记事本窗口**写入文本**（靶场备料用）。

    ★ 为什么不用键盘：`SetForegroundWindow` 在**后台进程**里常常失败
      （前台锁）⇒ 键打不到记事本，还可能**打到用户正在用的窗口**⛔ ⇒ 不可接受。
    ★ UIA 走 ValuePattern 直接给编辑控件设值 ⇒ **不依赖前台、不乱打字**。
    ★ 失败才回退到键盘（并且**必须**先确认前台就是它，否则**不许**发键）。
    """
    try:
        import win32gui
        h = int(hwnd)
        cands = []

        def _cb(c, _l, _out=cands):
            try:
                if win32gui.GetClassName(c) == "RichEditD2DPT":
                    _out.append(c)
            except Exception:
                pass
            return True
        try:
            win32gui.EnumChildWindows(h, _cb, None)
        except Exception:
            pass
        if cands:
            try:
                import uiautomation as auto
                ctrl = auto.ControlFromHandle(int(cands[0]))
                vp = ctrl.GetValuePattern()
                vp.SetValue(str(text))
                time.sleep(0.25)
                return True
            except Exception:
                pass
        # ── 回退：键盘（**先确认前台是自己**）──
        try:
            fg = int(user32.GetForegroundWindow() or 0)
            if fg != h:
                return False
            for ch in str(text):
                _state = ctypes.c_ulong(0)
                sc = user32.VkKeyScanExW(ord(ch), ctypes.byref(_state))
                vk = sc & 0xFF
                shift = bool(_state.value & 1)
                if not vk:
                    continue
                if shift:
                    user32.keybd_event(0x10, 0, 0, 0)
                user32.keybd_event(vk, 0, 0, 0)
                time.sleep(0.02)
                user32.keybd_event(vk, 0, 2, 0)
                if shift:
                    user32.keybd_event(0x10, 0, 2, 0)
                time.sleep(0.02)
            time.sleep(0.25)
            return True
        except Exception:
            return False
    except Exception:
        return False


def type_into_own_notepad(hwnd, text):
    """往**自己的**靶场记事本里敲几个字（只让画面上有字，判据才拿得到「有字的」区域）。

    ★ 只碰自己的窗口：UIA 设值**不需要前台** ⇒ 不会打断用户正在做的事；
      回退到键盘时**必须**先确认前台就是自己的窗口，否则**一个键都不发**。
    ⚠ 写完记事本是「脏」的（有未保存内容）⇒ 收摊会弹「是否保存」
      ⇒ `close_own_notepad` 已经管这个（清文档去 `*` ⇒ 静默关）。
    """
    return _set_notepad_text(hwnd, text)


def _dismiss_in_window_xaml_dialog(hwnd):
    """把**窗口内嵌**的 XAML 对话框按掉（例：「是否打开来自不受信任来源的文件?」）。

    ★ 为什么单列：那个弹框**不是独立顶层窗口** ⇒ 上面「认领新出现的顶层窗口」的办法
      **找不到它**（主控 2026-10-22 18:0x 实测看到它卡在记事本窗口里）。
    ★ 只在**我认领的窗口**里找 ⇒ 用户自己窗口里的弹框绝不碰。
    """
    try:
        import uiautomation as auto
        w = auto.ControlFromHandle(int(hwnd))
        hit = False
        for name in ("否(&N)", "否", "No", "取消", "Cancel"):
            try:
                btn = w.ButtonControl(searchDepth=8, Name=name)
                if btn.Exists(0.2):
                    btn.Click(simulateMove=False)
                    hit = True
                    time.sleep(0.2)
                    break
            except Exception:
                continue
        return hit
    except Exception:
        return False


def close_own_notepad(hwnd, proc):
    """关掉**自己**开的那个记事本窗口（连同它弹出的附属窗口）。

    ★★★ 四条硬规矩（派单 M16 红线第 9 条 + 用户 2026-10-22 12:58 当面提
       + 主控 2026-10-22 18:0x 给的**精确机制**）：
      ① **按窗口关，不按进程杀** —— 本机记事本**可能单进程常驻**（用户 7:48 那个
         pid 一直活着）⇒ 按 pid 杀会**连用户自己开的那个一起关掉** ⇒ ⛔ 不许。
      ② **只关我认领的**：`OPEN_NEW_HWNDS`（开窗后**新出现**的可见 Notepad 窗口）
         ⇒ 不在集合里的（用户原有的）**一个都不碰**。
      ③ **先清文档再关窗**（主控给的手法）：对子窗口 `RichEditD2DPT` 发
         `WM_SETTEXT ""` ⇒ 标题的 `*` 消失 ⇒ `WM_CLOSE` **静默**关闭，
         不会逐个弹「是否保存」（弹框本身又是一个可见窗口 ⇒ 正是泄漏的另一半）。
      ④ **异常路径也要关**：调用方必须在 finally 里调本函数。
    """
    global OPEN_EXTRA, OPEN_PIDS, PREV_FG, OPEN_NEW_HWNDS
    try:
        import win32gui
    except Exception:
        win32gui = None
    # ── ⚠⚠ **先把前台还回去**（在关/杀之前！）——
    #   记事本此刻多半在前台（判据刚点过它）⇒ 直接杀 ⇒ 桌面当场失去前台
    #   ⇒ 系统把前台判给一个**隐形的系统服务窗**（GameInputServiceWindow）
    #   ⇒ 之后谁都抢不到前台（UIPI 更高权限，连 PostMessage 都拒绝访问）
    #   ⇒ 所有需要真实点击的验收全灭（实测踩过，详见 PREV_FG 处的注释）。
    _prev = int(PREV_FG or 0)
    PREV_FG = 0
    if _prev:
        try:
            if user32.IsWindow(_prev):
                user32.SetForegroundWindow(_prev)
                time.sleep(0.15)
        except Exception:
            pass
    # ① 附属弹框（README 信任弹框之类）⇒ 先关
    for h in list(OPEN_EXTRA or []):
        try:
            if win32gui and win32gui.IsWindow(int(h)) and win32gui.IsWindowVisible(int(h)):
                win32gui.PostMessage(int(h), 0x0010, 0, 0)     # WM_CLOSE
        except Exception:
            pass
    OPEN_EXTRA = []
    # ② ★ 关**我认领的**每一个 Notepad 窗口：先清文档（去 `*`）⇒ 再 WM_CLOSE（静默）
    _mine = [int(x) for x in (OPEN_NEW_HWNDS or []) if x]
    if hwnd and int(hwnd) not in _mine:
        _mine.insert(0, int(hwnd))
    before = set(_enum_windows())
    for h in _mine:
        try:
            if not (win32gui and win32gui.IsWindow(h) and win32gui.IsWindowVisible(h)):
                continue
        except Exception:
            continue
        try:
            _clear_notepad_doc(h)                       # ★ 去 `*` ⇒ 关窗不弹框
        except Exception:
            pass
        try:
            _dismiss_in_window_xaml_dialog(h)           # ★ 窗口内嵌的 XAML 弹框（README 信任）
        except Exception:
            pass
        try:
            win32gui.PostMessage(h, 0x0010, 0, 0)       # WM_CLOSE
        except Exception:
            pass
    OPEN_NEW_HWNDS = []
    # ③ 等它们真的消失；还赖着 ⇒ 认领「新出现」的保存弹框 ⇒ 点「不保存」
    try:
        for _ in range(15):
            if not any(win32gui and win32gui.IsWindow(h) and win32gui.IsWindowVisible(h)
                       for h in _mine):
                break
            time.sleep(0.1)
        if any(win32gui and win32gui.IsWindow(h) and win32gui.IsWindowVisible(h) for h in _mine):
            _dismiss_save_dialog(_mine[0], timeout=1.5, extra_before=before)
            for _ in range(15):
                if not any(win32gui and win32gui.IsWindow(h) and win32gui.IsWindowVisible(h)
                           for h in _mine):
                    break
                time.sleep(0.1)
    except Exception:
        pass
    # ④ ★ 最后手段：**只杀「本次新起的进程」**（`OPEN_PIDS` = after_pids − before_pids）
    #   ⇒ **常驻实例（用户 7:48 那个）不在其中 ⇒ 绝不会被碰**。
    #   ⚠ 早前的写法是「按新窗口算 pid」⇒ 若新窗口属于常驻实例就会**误杀用户的实例** ⇒ 已改。
    _my_pids = list(OPEN_PIDS or [])          # ★ 先留一份：下面要把 OPEN_PIDS 清空
    for _pid in _my_pids:
        try:
            if _pid and _pid != os.getpid():
                os.kill(_pid, 9)          # SIGKILL：Windows 上 = TerminateProcess
        except Exception:
            pass
    OPEN_PIDS = []
    try:
        if proc is not None:
            proc.kill()                          # 启动器也收掉
    except Exception:
            pass
    # ⑤ ★ 还原 TabState：记事本**退出时**会写 TabState ⇒ 必须等它真的退出再还原，
    #   否则它会把还原结果覆盖掉。
    try:
        _wait_pids_gone(_my_pids, timeout=3.0)
    except Exception:
        pass
    try:
        restore_notepad_tabstate()
    except Exception:
        pass
    try:
        import win32gui
        return (not win32gui.IsWindow(int(hwnd))) if hwnd else True
    except Exception:
        return True


def _wait_pids_gone(pids, timeout=3.0):
    """等这些 pid 真的退出（记事本退出时才会把 TabState 写回去 ⇒ 还原前必须等它走）。"""
    import ctypes
    from ctypes import wintypes as wt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    t0 = time.time()
    while (time.time() - t0) < timeout:
        alive = []
        for p in (pids or []):
            try:
                h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(p))
                if h:
                    k32.CloseHandle(h)
                    alive.append(p)
            except Exception:
                pass
        if not alive:
            return True
        time.sleep(0.1)
    return False


def bring_to_front(hwnd, attempts=3, method="auto"):
    """置前。直调 SetForegroundWindow 常常返回 False ⇒ 用 AttachThreadInput 绕。

    ★ **重试**：记事本刚起来时 UI 还没准备好接前台（TabState 暂存后开窗更快 ⇒
      更容易撞上这个窗口期）⇒ 一次不成**再试几次**，别把「刚开窗」误判成
      「环境受阻」（判据铁律：环境红不许算产品红，但也不许把可测的判成测不了）。
    ★ `method`（M16 派单 §2 测量口径用）：
      - `"auto"`（默认）：先 `SetForegroundWindow`，失败再 `AttachThreadInput`；
      - `"setforeground"`：**只用** `SetForegroundWindow`（测量口径固定 ⇒ 前后一致）；
      - `"attach"`：只用 `AttachThreadInput`。
      ⚠ 主控 2026-10-22 18:2x 指出：改前基线与改后**置前方式不同**是个**未控变量**
        ⇒ 测量必须**统一成同一种**（本单统一用 `"setforeground"`，失败即报环境受阻，不偷偷换招）。
    """
    if method == "setforeground":
        last = "**置前失败**"
        for _ in range(max(1, int(attempts))):
            try:
                user32.SetForegroundWindow(int(hwnd))
                time.sleep(0.2)
                if int(user32.GetForegroundWindow() or 0) == int(hwnd):
                    return "SetForegroundWindow"
            except Exception:
                pass
            last = "**置前失败**"
            time.sleep(0.4)
        return last
    if method == "attach":
        last = "**置前失败**"
        for _ in range(max(1, int(attempts))):
            r = _bring_to_front_attach(int(hwnd))
            if "失败" not in r:
                return r
            last = r
            time.sleep(0.4)
        return last
    last = "**置前失败**"
    for i in range(max(1, int(attempts))):
        r = _bring_to_front_once(hwnd)
        if "失败" not in r:
            return r
        last = r
        time.sleep(0.4)
    return last


def _bring_to_front_attach(hwnd):
    """只用 AttachThreadInput 置前（口径固定用）。"""
    fg = int(user32.GetForegroundWindow() or 0)
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_tid = kernel32.GetCurrentThreadId()
    try:
        if fg_tid:
            user32.AttachThreadInput(my_tid, fg_tid, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if fg_tid:
            try:
                user32.AttachThreadInput(my_tid, fg_tid, False)
            except Exception:
                pass
    time.sleep(0.3)
    return "AttachThreadInput" if int(user32.GetForegroundWindow() or 0) == int(hwnd) else "**置前失败**"


def _bring_to_front_once(hwnd):
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    if int(user32.GetForegroundWindow()) == int(hwnd):
        return "SetForegroundWindow"
    fg = int(user32.GetForegroundWindow())
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    my_tid = kernel32.GetCurrentThreadId()
    try:
        user32.AttachThreadInput(my_tid, fg_tid, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        user32.AttachThreadInput(my_tid, fg_tid, False)
    time.sleep(0.3)
    return "AttachThreadInput" if int(user32.GetForegroundWindow()) == int(hwnd) else "**置前失败**"


# ────────────────────────────── 计时器（只在本进程） ──────────────────────────────
class Ticker:
    """记录事件：(名字, 起点, 终点)。互斥分段靠它切时间轴。"""

    def __init__(self):
        self.ev = []

    def add(self, name, t0, t1, extra=None):
        self.ev.append({"名称": name, "t0": t0, "t1": t1, "ms": round((t1 - t0) * 1000, 3),
                        "额外": extra or {}})

    def mark(self, name, extra=None):
        self.ev.append({"名称": name, "t0": time.perf_counter(), "t1": None, "ms": 0.0,
                        "额外": extra or {}})


def _wrap(obj, name, ticker, label, on=None):
    """给 obj.name 包一层计时（返回原函数以便还原）。"""
    orig = getattr(obj, name)

    def w(*a, **kw):
        t0 = time.perf_counter()
        try:
            return orig(*a, **kw)
        finally:
            t1 = time.perf_counter()
            extra = on(a, kw) if on else None
            ticker.add(label, t0, t1, extra)
    setattr(obj, name, w)
    return orig


# ────────────────────────────── 主测量 ──────────────────────────────
def _pick_eid(screen_data, want_change=False):
    """从 `ctl_screen` 的结果里挑一个**带 eid**的元素。

    ★ `want_change=False`：挑一个**普通可点**的元素（点了画面多半不变）；
      `want_change=True`：挑一个**会改画面**的元素（记事本的「文件」菜单 ⇒ 弹出菜单）
      ⇒ 用例「点击真的生效（早停）」就靠它。
    """
    try:
        res = (screen_data or {}).get("结果") or screen_data or {}
        objs = res.get("objects") or []
        # ① 会改画面的：名字像「文件」（记事本菜单）且可交互
        if want_change:
            for o in objs:
                if not o.get("eid"):
                    continue
                lab = str(o.get("label") or "")
                if ("文件" in lab or "File" in lab) and (o.get("interactive") or o.get("actions")):
                    return o.get("eid")
        # ② 兜底：第一个带 eid 的可交互元素
        for o in objs:
            if o.get("eid") and (o.get("interactive") or o.get("actions")):
                return o.get("eid")
        for o in objs:
            if o.get("eid"):
                return o.get("eid")
    except Exception:
        pass
    return ""


def _eid_expect_text(screen_data, eid):
    """给 `verify_expect` 造一个**期望文本**（取该 eid 元素的 label）。

    ★ `verify_expect` 是**谓词**（期望画面里出现某段文本）⇒ 必须给个像样的期望值；
      取该元素自己的 label 最稳（它点完就该还在/或变成别的）。
    """
    try:
        res = (screen_data or {}).get("结果") or screen_data or {}
        for o in (res.get("objects") or []):
            if str(o.get("eid") or "") == str(eid or ""):
                lab = str(o.get("label") or "").strip()
                return lab or "记事本"
    except Exception:
        pass
    return "记事本"


def measure_click(hwnd, xy, label, inject=False, extra_args=None, actor="M11分段测量",
                  scenario="frame_id", fg_method="auto"):
    """跑一次生产 `ctl_click`，返回一张互斥分段账本。

    ★ `extra_args`（M16 派单 A 用）：给这次点击**追加**参数（如 `verify_expect`），
      好让判据能拿「带 verify_expect / 不带」的**同一类动作**做端到端对照。
    ★ `actor`（M16 派单 §2「测量协议」第 3 条用）：**每次测量用独立 actor 名**（带时间戳）——
      判据与测量器共用 `M11分段测量` 这个 actor ⇒ **连击计数会跨 run 累加**
      （主控 13:02 那次误触发急停，就是这么来的）。默认值保持原样 ⇒ 现有判据零影响。
    ★ `scenario`（M16 派单 §2 验收口径 + 主控 2026-10-22 18:2x 纠偏用）：
      - `"frame_id"`（默认，现状）：xy + `frame_id` ⇒ 带凭据那条路；
      - `"from_capture"`：xy + `from_capture`（**不带** frame_id）⇒ 普通坐标路；
      - `"eid"`：**生产 eid 链** —— `ctl_screen` 的 objects[] 里取一个带 eid 的元素
        ⇒ `ctl_click --eid <eid> --from_capture <cap>`
        ⇒ ★ **B（动作后 UIA 重扫按需化）的 ~191ms 只在这条路上兑现**
          （`_click_after_state` 只在 `ok 且 _eid_hit` 时跑）；
      - `"eid_verify"`：同上 **+ `verify_expect`** ⇒ ★ **A（双 spawn 消除）只在这条路上兑现**
        （声明了 verify_expect 的动作才走「拍前/后快照 + 独立进程判定」）；
      - `"eid_change"`：eid 点**会改画面**的元素（记事本的「文件」菜单）⇒ 画面变 ⇒ **早停**
        ⇒ 这是「用户感知的响应速度」那条典型路（不是「点了没反应」的最坏情况）。
      ★ 这四条**不是同一件事**（端到端耗时差很多）⇒ 量 p50 时必须说清是哪条。
    ★ `fg_method`（主控 2026-10-22 18:2x 指出「置前方式」是**未控变量**）：
      ★ **统一的是「策略」，不是「某一个 API」**：`"auto"` = 先 `SetForegroundWindow`、
        失败再 `AttachThreadInput`（**本单前后所有测量用的都是这一套** ⇒ 口径一致）；
        `"setforeground"` / `"attach"` = 强制只用某一种（实测：`setforeground` 在前台被占时
        **拿不到前台** ⇒ 会让整次测量「环境受阻」⇒ 不适合当默认）。
      ★ 每轮**实际生效的方式**都会记进结果（`置前方式`）⇒ 前后是否同一种，**可逐轮核对**。
    """
    ticker = Ticker()
    restores = []
    mark = {"legacy_start": None}
    try:
        # ① 子进程（spawn+import+act 全部工时）
        def on_legacy(a, kw):
            return {"首次调用": mark["legacy_start"] is None}
        restores.append((core_mod, "_legacy",
                         _wrap(core_mod, "_legacy", ticker, "子进程 spawn+import+act 工时", on_legacy)))
        # 记下**子进程被调用的那一刻**（用于直接量「前置」，不做减法倒推）
        _orig_legacy = core_mod._legacy

        def _legacy_timed(*a, **kw):
            if mark["legacy_start"] is None:
                mark["legacy_start"] = time.perf_counter()
            return _orig_legacy(*a, **kw)
        core_mod._legacy = _legacy_timed
        restores.append((core_mod, "_legacy", _orig_legacy))
        # ② 动作后回读（core 侧 wait_change 及其兜底抓帧）
        restores.append((core_mod, "_act_after",
                         _wrap(core_mod, "_act_after", ticker, "动作后回读(core wait_change)")))
        # ③ 落盘 diff（PNG + diff）
        restores.append((core_mod, "_act_diff_store",
                         _wrap(core_mod, "_act_diff_store", ticker, "落盘 PNG+diff")))
        # ④ UIA 元素查找 + 身份复核
        restores.append((core_mod, "_eid_recheck",
                         _wrap(core_mod, "_eid_recheck", ticker, "UIA 元素查找+身份复核")))
        # ⑤ 抓帧（客户区）：记次数与总耗时；phase 用来分「动作前」与「变化探测」
        state = {"phase": "pre"}

        def on_cap(args, kw):
            return {"phase": state["phase"]}
        restores.append((framecache, "capture_client",
                         _wrap(framecache, "capture_client", ticker, "抓帧(客户区)", on_cap)))
        # ⑥ core 侧 wait_change 单独记（含轮次）
        def on_wait(args, kw):
            return {"预算ms": kw.get("budget_ms", framecache.WAIT_BUDGET_MS)}
        restores.append((framecache, "wait_change",
                         _wrap(framecache, "wait_change", ticker, "core wait_change(含轮次)", on_wait)))

        # ── 真正跑一次生产点击（每轮都重新置前 + 重新观测）──
        sv = ""
        res = {}
        fg_used = []
        for attempt in range(4):
            try:
                _fg = bring_to_front(hwnd, method=fg_method)   # 前台可能被别人抢走 ⇒ 每轮重新置前
                fg_used.append(_fg)
            except Exception:
                fg_used.append("**异常**")
            screen = core_mod.dispatch("ctl_screen", {"hwnd": int(hwnd)}, actor=actor)
            _sd = screen.get("data") or {}
            sv = (_sd.get("结果") or _sd).get("screen_version") or ""
            # ★ 「普通坐标路」要用的 capture_id（**同一次观测**拿的，不额外观测一次）
            cap = ((_sd.get("结果") or _sd).get("capture_id") or "")
            # ★ 「画面到底变了没」的**地面真值**：两张都在**计时窗口之外**取
            pre_img = framecache.capture_client(int(hwnd))
            framecache.reset_stats()
            t_start = time.perf_counter()
            state["phase"] = "pre"
            _click_args = {"hwnd": int(hwnd), "xy": list(xy),
                           "frame_id": sv, "allow_foreground": True}
            if scenario == "from_capture":
                # ★ 「普通坐标路」：用**同一次观测**的 capture_id，**不带** frame_id
                #   ⇒ `_need_diff` 为假 ⇒ 走的是 agent 真正调用的那条路。
                _click_args["from_capture"] = cap
                _click_args["frame_id"] = None
            elif scenario in ("eid", "eid_verify", "eid_change"):
                # ★ 生产 eid 链：ctl_screen 的 objects[] 里取一个**带 eid**的元素
                #   ⇒ ctl_click --eid <eid> --from_capture <cap>
                #   （坐标由 core 从 eid 旁表解析 + 复核 ⇒ 「① 前置」这段会变重，
                #     而 B 的「动作后 UIA 重扫」也只在这条路上出现。）
                _eid = _pick_eid(_sd, want_change=(scenario == "eid_change"))
                if not _eid:
                    raise RuntimeError("ctl_screen 没给出可点的 eid（场景 %s）" % scenario)
                _click_args = {"hwnd": int(hwnd), "eid": _eid,
                               "from_capture": cap, "allow_foreground": True}
                if scenario == "eid_verify":
                    # ★ A 的触发条件：**声明了 verify_expect** 的动作
                    #   ⇒ 它才会走「拍前/后快照 + 独立进程判定」这条路。
                    _click_args["verify_expect"] = _eid_expect_text(_sd, _eid)
            if extra_args:
                _click_args.update(extra_args)      # ★ M16 A：带 verify_expect 的那一次
            res = core_mod.dispatch("ctl_click", _click_args,
                                    actor=actor)
            t_end = time.perf_counter()
            state["phase"] = "post"
            total_ms = (t_end - t_start) * 1000
            stats = dict(framecache.STATS)
            post_img = framecache.capture_client(int(hwnd))
            if scenario == "eid_change":
                # ★ 菜单点开后要**收回去**（ESC）⇒ 否则下一轮点的是菜单项本身
                #   （会误触发「新建/打开」等）。ESC 放在**计时窗口之外** ⇒ 不污染端到端。
                try:
                    user32.keybd_event(0x1B, 0, 0, 0)      # VK_ESCAPE
                    time.sleep(0.05)
                    user32.keybd_event(0x1B, 0, 2, 0)
                    time.sleep(0.30)
                except Exception:
                    pass
            # ★ 凭据过期（code=12）/ 前台被抢（code=5）⇒ **重观测重试**（不是产品缺陷）
            if res.get("code") in (12, 5) and attempt < 3:
                continue
            break
        try:
            # ★ 地面真值：**用产品同一把尺子**（framecache.ChangeTracker = 指纹口径），
            #   但由**我**拿自己抓的 before/after 两帧事后算 —— **不读产品的返回值**。
            #   （全分辨率 + 光标滤除的口径对光标不敏感，会与产品的指纹口径打架 ⇒ 只作参考。）
            if pre_img is not None and post_img is not None:
                truth = framecache.ChangeTracker().feed(pre_img, post_img)
                truth_changed = truth.get("changed")
            else:
                truth_changed = None
        except Exception:
            truth_changed = None
    finally:
        for obj, name, orig in restores:
            setattr(obj, name, orig)

    # ── 互斥分段：**每段都是自己打点量出来的**（不靠减法倒推）──
    ev = sorted([e for e in ticker.ev if e["t1"] is not None], key=lambda e: e["t0"])
    legacy_ms = sum(e["ms"] for e in ev if e["名称"] == "子进程 spawn+import+act 工时")
    after_ms = sum(e["ms"] for e in ev if e["名称"] == "动作后回读(core wait_change)")
    diff_ms = sum(e["ms"] for e in ev if e["名称"] == "落盘 PNG+diff")
    uia_ms = sum(e["ms"] for e in ev if e["名称"] == "UIA 元素查找+身份复核")
    cap_pre = sum(e["ms"] for e in ev if e["名称"] == "抓帧(客户区)" and e["额外"].get("phase") == "pre")
    cap_post = sum(e["ms"] for e in ev if e["名称"] == "抓帧(客户区)" and e["额外"].get("phase") == "post")
    wait_ms = sum(e["ms"] for e in ev if e["名称"] == "core wait_change(含轮次)")

    # ★ 「前置」= 从端到端起点到**子进程被调用那一刻**（直接量，不倒推）
    pre_ms = 0.0
    if mark["legacy_start"] is not None:
        pre_ms = max(0.0, (mark["legacy_start"] - t_start) * 1000)
    # ★ **未归因**＝端到端 − 各实测段之和（**单列，不许当成分段**）
    seg_sum = pre_ms + legacy_ms + after_ms + diff_ms
    unattributed_ms = total_ms - seg_sum
    segs = [
        {"段": "① 前置（门禁/凭据/元素查找/复核树）", "ms": round(pre_ms, 1),
         "说明": "从端到端起点到子进程被调用那一刻（**直接打点**）；含 UIA 元素查找+身份复核 %.1f ms" % uia_ms},
        {"段": "② 子进程 spawn+import+act 全部工时", "ms": round(legacy_ms, 1),
         "说明": "★ 内含 act 侧 spawn/import/UIA/注入/等待（子表见「act 侧子表」）"},
        {"段": "③ 动作后回读（core wait_change）", "ms": round(after_ms, 1),
         "说明": "含变化探测抓帧 %.1f ms；wait_change 本体 %.1f ms；轮次 %s"
                 % (cap_post, wait_ms, stats.get("变化即停轮次"))},
        {"段": "④ 落盘 PNG+diff", "ms": round(diff_ms, 1), "说明": ""},
    ]
    return {
        "用例": label,
        "端到端ms": round(total_ms, 1),
        "置前方式": fg_used,
        "分段和ms": round(seg_sum, 1),
        "未归因ms": round(unattributed_ms, 1),
        "未归因pct": round(abs(unattributed_ms) / total_ms * 100, 2) if total_ms else None,
        "误差pct": round(abs(seg_sum - total_ms) / total_ms * 100, 2) if total_ms else None,
        "分段": segs,
        "抓帧次数": {"动作前": int(stats.get("动作前抓帧") or 0),
                     "变化探测": int(stats.get("指纹探测") or 0),
                     "动作后留帧": int(stats.get("动作后留帧") or 0)},
        "变化即停轮次": stats.get("变化即停轮次"),
        "结果": {"ok": bool(res.get("ok")), "code": res.get("code"),
                 "warning": (res.get("warning") or "")[:160]},
        "画面真变了没": truth_changed,
        "原始事件": [{"名称": e["名称"], "ms": e["ms"], "额外": e["额外"]} for e in ev],
    }


# ────────────────────────────── act 侧等待「直测」 ──────────────────────────────
def measure_act_waits(hwnd):
    """**直接**调 act.py 的 `_wait_effect` / `_wait_ready`（不经闸门），
    量它们在「画面会变」与「画面不变」两种情形下各花多少 ms。

    ★ 为什么能直测：这两个是**纯函数**（只看窗口画面），不碰闸门 ⇒ 不用租约。
    ★ 画面怎么变：**只用键盘**（往记事本里打一个字符）⇒ **不移动用户的鼠标**。
    """
    legacy_home = getattr(core_mod, "LEGACY", "") or ""
    sys.path.insert(0, legacy_home)
    import act as legacy_act                                    # noqa: E402
    out = {"legacy_home": legacy_home}

    def _poll_cost(n=5):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            legacy_act._poll_sample(int(hwnd))
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        return round(ts[len(ts) // 2], 2)

    out["act_每轮采样成本ms"] = _poll_cost()

    # 画面会变：后台线程 120ms 后打一个字符（不动鼠标）
    def _type_later():
        time.sleep(0.12)
        try:
            legacy_act.send_unicode("M")
        except Exception:
            pass

    # ① 画面会变
    base = legacy_act._poll_base(int(hwnd))
    import threading
    th = threading.Thread(target=_type_later, daemon=True)
    t0 = time.perf_counter()
    th.start()
    r = legacy_act._wait_effect(int(hwnd), base)
    out["act_wait_effect_画面会变_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["act_wait_effect_会变_返回"] = {k: r.get(k) for k in ("changed", "轮次") if k in r}

    # ② 画面不变（什么都不做 ⇒ 等满预算）
    base = legacy_act._poll_base(int(hwnd))
    t0 = time.perf_counter()
    r = legacy_act._wait_effect(int(hwnd), base)
    out["act_wait_effect_画面不变_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["act_wait_effect_不变_返回"] = {k: r.get(k) for k in ("changed", "轮次") if k in r}

    # ③ core 侧 framecache.wait_change 直测（不变 / 变）
    before = framecache.capture_client(int(hwnd))
    t0 = time.perf_counter()
    rr = framecache.wait_change(int(hwnd), before, budget_ms=framecache.WAIT_BUDGET_MS,
                                 poll_ms=framecache.WAIT_POLL_MS)
    out["core_wait_change_画面不变_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["core_wait_change_不变_返回"] = {"changed": rr.get("changed"), "轮次": rr.get("轮次")}

    def _type_later2():
        time.sleep(0.12)
        try:
            legacy_act.send_unicode("M")
        except Exception:
            pass
    th = threading.Thread(target=_type_later2, daemon=True)
    t0 = time.perf_counter()
    th.start()
    rr = framecache.wait_change(int(hwnd), before, budget_ms=framecache.WAIT_BUDGET_MS,
                                 poll_ms=framecache.WAIT_POLL_MS)
    out["core_wait_change_画面会变_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["core_wait_change_会变_返回"] = {"changed": rr.get("changed"), "轮次": rr.get("轮次")}

    # ④ act 侧 **UIA 查找**（按名找控件 = 坐标点击路径里真正用的那个函数）
    uia_ts = []
    for _ in range(3):
        t0 = time.perf_counter()
        try:
            legacy_act.find_control(int(hwnd), "编辑")
        except Exception:
            pass
        uia_ts.append((time.perf_counter() - t0) * 1000)
    uia_ts.sort()
    out["act_UIA查找_find_control_中位ms"] = round(uia_ts[len(uia_ts) // 2], 2)

    # ⑤ act 侧 **注入**（SendInput 本身；零位移绝对移动）
    inj_ts = []
    _orig_send = legacy_act.send_mouse

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _u32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
    _u32.GetCursorPos.restype = ctypes.c_bool
    _u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _u32.GetSystemMetrics.restype = ctypes.c_int

    def _send_timed(*a, **kw):
        t0 = time.perf_counter()
        try:
            return _orig_send(*a, **kw)
        finally:
            inj_ts.append((time.perf_counter() - t0) * 1000)
    legacy_act.send_mouse = _send_timed
    try:
        p3 = _POINT()
        if _u32.GetCursorPos(ctypes.byref(p3)):
            sw = _u32.GetSystemMetrics(0) or 1
            sh = _u32.GetSystemMetrics(1) or 1
            legacy_act.send_mouse(0x8000 | 0x0001,     # MOUSEEVENTF_ABSOLUTE | MOVE
                                 int((p3.x * 65535) / max(1, sw - 1)),
                                 int((p3.y * 65535) / max(1, sh - 1)), 0)
    finally:
        legacy_act.send_mouse = _orig_send
    out["act_注入_send_mouse_ms"] = round(inj_ts[0], 2) if inj_ts else None
    out["act_注入_次数"] = len(inj_ts)

    # ⑥ act 侧 **_wait_ready**（动作**前**的就绪等待；与 _wait_effect 是两回事）
    base = legacy_act._poll_base(int(hwnd))
    t0 = time.perf_counter()
    rr = legacy_act._wait_ready(int(hwnd), base, budget_ms=250)
    out["act_wait_ready_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["act_wait_ready_返回"] = {k: rr.get(k) for k in ("就绪", "轮次", "耗时ms") if k in rr}
    return out


# ────────────────────────────── 跑一遍 ──────────────────────────────
def run_all(rounds=1, out_path=None):
    ledger = {"开始时间": time.strftime("%Y-%m-%d %H:%M:%S"), "轮次": rounds, "用例": []}
    hwnd, proc = open_own_notepad()
    ledger["记事本hwnd"] = int(hwnd)
    try:
        ledger["置前方式"] = bring_to_front(hwnd)
        if "失败" in ledger["置前方式"]:
            ledger["环境受阻"] = "置前失败 ⇒ 生产点击会被 act.py 的 ensure_foreground 拒（rc=5）"
        # 租约（写动作要）
        core_mod.dispatch("ctl_lease", {"action": "acquire", "scopes": "ABC",
                                         "actor": "M11分段测量"}, actor="M11分段测量")
        # 记事本正文区坐标（客户区中部）
        cl = framecache.client_origin(int(hwnd))
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        xy = (int(cl[0] + 200), int(cl[1] + 150)) if cl else (int(sw / 2), int(sh / 2))
        ledger["点击坐标"] = list(xy)
        ledger["act_侧直测"] = measure_act_waits(hwnd)
        for i in range(rounds):
            ledger["用例"].append(measure_click(hwnd, xy, "第%d轮·点正文区(画面多半不变)" % (i + 1)))
    finally:
        # ★ M16 §2.9：关掉**自己**开的那个窗口（连同它的宿主进程）——
        #   改前只 `proc.kill()`（启动器）⇒ Win11 记事本窗口在**另一个进程**里活着
        #   ⇒ 一天堆到 185 个可见窗口（用户当面提的）。
        close_own_notepad(hwnd, proc)
    ledger["结束时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
    txt = json.dumps(ledger, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
    return ledger, txt


def main() -> int:
    ap = argparse.ArgumentParser(description="M11 动作链分段测量")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "_M11_分段输出.txt"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    led, txt = run_all(a.rounds, a.out)
    if a.json:
        print(txt)
    else:
        for c in led["用例"]:
            print("== %s ｜ 端到端 %.1f ms ｜ 分段和 %.1f ms ｜ 误差 %.2f%%"
                  % (c["用例"], c["端到端ms"], c["分段和ms"], c["误差pct"] or 0))
            for s in c["分段"]:
                print("   %-38s %8.1f ms  %s" % (s["段"], s["ms"], s["说明"]))
        print(json.dumps(led.get("act_侧直测", {}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
