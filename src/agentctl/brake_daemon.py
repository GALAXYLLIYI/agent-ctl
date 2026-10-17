# -*- coding: utf-8 -*-
"""brake_daemon —— 全局热键 Ctrl+Alt+Home 的常驻监听（急停通道之一）。

## 为什么单独一个进程，不放进面板
两个理由，都不是「为了好看」：

1. **M1 挂件的底线**：面板的 selftest 每次都审源码，查有没有 import
    能操作屏幕的模块。注册全局热键 = 能监听键盘，塞进面板就等于
    让「只读挂件」长出手脚，这条线一破，selftest 就白写了。
2. **更要命的是可用性**：面板刚给用户加了关闭按钮。用户一关面板，
    热键跟着没了 —— 而急停恰恰是**最后一道防线**，
    不能随一个窗口关闭而失效。所以它必须活得比面板久。

## 为什么不写进闸门
gate.py 负责「判断」（读文件看停没停），这里负责「触发」（按一下）。
混在一起会让闸门依赖 win32，破坏它纯逻辑、可单测的性质。

## 急停弹出来（窗I · 2026-10-16）
 按下热键 / 外部通道拉下急停的那一刻，除了 2.5 秒的红色横幅，
 还会弹一张**桌面置顶、不自己消失**的窗（brake_popup.py），上面写清三件事：
 **是谁在动**（actor + session_id，取不到就如实写「未知」）、
 **它的 MCP 链接**（~/.codex/config.toml 的 [mcp_servers.*]，可选中复制）、
 **怎么恢复**（确切命令 + 重新授权入口 + 「先确认是谁在动」）。
 解除急停后那张窗自动收起。
 ★ 那张窗**不含任何能改急停状态的控件**，标题也进了 act.py 的 BLOCKED_TITLES
   —— 否则 agent 能点掉它，它就从安全提示变成新的自授权口子。

## 用法
    AgentCtl.exe brake-daemon            前台常驻（调试用）
    AgentCtl.exe brake-daemon --quiet    无输出，供安装包开机自启

★ 窗P1（2026-10-16）补的**源码树入口**（本文件此前**根本没有 __main__ 块**，
  374 行到 _pid_alive 就结束了 —— 于是 `python -m agentctl.brake_daemon`
  **import 完就秒退**，屏幕上什么都不出现，还不留任何日志）：
    cd <仓根>/src
    python -X utf8 -m agentctl.brake_daemon            # 与上面两条完全等价
    python -X utf8 src/agentctl_main.py brake-daemon   # 不想切目录就用这条
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

from . import gate

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_HOME = 0x24
WM_HOTKEY = 0x0312
QS_ALLINPUT = 0x04FF
HOTKEY_ID = 0xAC11
# 心跳文件名：daemon 活着就定期写它，面板靠它区分「被自己人占」和「被外人占」
HEARTBEAT = os.path.join(gate.APP_DIR, "brake_daemon.json")
HEARTBEAT_STALE_SEC = 30      # 超过 30s 没更新 = 判定 daemon 不在了
ALERT_MS = 2500               # 急停横幅停留时间

user32 = ctypes.windll.user32
QUIET = False


def _log(msg):
    try:
        gate.log("brake-daemon: " + msg)
    except Exception:
        pass
    if not QUIET:
        try:
            # ★ flush=True（窗P1 · 2026-10-16）：不带 flush 时这行会卡在缓冲区里，
            #   daemon 一被 kill 掉（排障收尾、任务管理器结束进程）证据就**全丢了**。
            #   老线 brake.py:54 一直带着 flush，本文件是漏的 —— 现在对齐。
            #   另：gate.log 写的那份（<legacy_home>\_auth\gate_agentctl.log）才是可靠证据，
            #   它是逐行 open/write/close，不受进程猝死影响。
            print(msg, flush=True)
        except Exception:
            pass


def run():
    global QUIET
    QUIET = "--quiet" in sys.argv

    # ★ 注册失败必须**明确失败**，不能默默跑。
    #   旧 brake.py 原话：「按准则：热键不可用就不许进入 B 级以上的控制」。
    #   悄悄退出的守护 = 用户以为热键能救命，真出事按了没反应。
    if not user32.RegisterHotKey(None, HOTKEY_ID,
                                 MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                                 VK_HOME):
        _log("[X] 热键 Ctrl+Alt+Home 注册失败（可能被别的程序占着）—— "
             "按规矩：热键不可用就不许进入 B 级以上控制。这一条没满足，别当它能用。")
        return 2

    _log("[OK] 已监听 Ctrl+Alt+Home（按下即拉急停）")
    _log("     急停状态载体：%s" % gate.BRAKE_FLAG)
    _log("     当前刹车：%s" % ("已拉下" if gate.brake_engaged() else "松开"))
    _popup_preflight()

    msg = wt.MSG()
    prev = gate.brake_engaged()
    # ★ 窗I（2026-10-16）：**起手就已经是急停** ⇒ 照样弹。
    #   这一支以前没人管：面板没开、机器重启过，用户会坐在一个「早就停了的系统」
    #   面前毫无察觉地干活。弹窗只读状态、不改状态，弹错了大不了用户自己关掉。
    if prev:
        _popup_show("daemon 启动时已处于急停状态")
    last_poll = 0.0
    # ★ 重复触发的真因与修法（2026-10-05 实机实测）★
    #
    # 实测日志（按一次）：
    #   #1 lParam=0x00240003 距上次 7753296 ms → 拉下
    #   #2 lParam=0x00240003 距上次      16 ms → 重复
    #   #3 lParam=0x00240003 距上次    1063 ms → 重复
    #   #4 lParam=0x00240003 距上次       0 ms → 重复
    # lParam 解码：低位 0x0003 = ALT|CTRL ✓；**高位 0x0024 = 36 次击键**。
    #
    # → 真因是**键盘自动重复（key repeat）**，不是消息被重复投递。
    #   MOD_NOREPEAT 本该挡住，但**含 ALT 的组合上它失效**（已知 Windows 行为）。
    #   我先前按 WorkBuddy 的推断把 PM_NOREMOVE 改成 PM_REMOVE，**完全没用**，
    #   因为那个推断从一开始就不成立 —— 这里如实记下来，别再走一遍。
    #
    # 为什么修法是「看状态」而不是「看时间窗」：
    #   时间窗是我第一版加的，**实测漏了**（按住超过 500ms 就漏过去，#3 就是）。
    #   正确判据是**幂等**：急停已拉下时再拉，只是重写同一个文件，后果完全一样。
    #   所以直接问状态 —— **与你按多久、按几次完全无关**，也不会漏。
    _DEDUP_MS = 500          # 保留：额外挡掉「极短时间内两次真实按键」的抖动
    last_hotkey_tick = 0
    hotkey_seq = 0
    dup_count = 0
    dup_logged_seq = 0       # 上次记汇总时的 hotkey_seq
    try:
        while True:
            _write_heartbeat()
            # 阻塞等消息，超时 500ms 回来顺手看一眼文件状态
            r = user32.MsgWaitForMultipleObjects(0, None, False, 500, QS_ALLINPUT)
            if r == 0:
                # ★ PM_REMOVE(0)，不是 PM_NOREMOVE(1)（WorkBuddy 004 的 W4）★
                #   PeekMessage 传 NOREMOVE 时，消息读出来**不移出队列**，
                #   while 循环里再 Peek 一次还是同一条 WM_HOTKEY ——
                #   一次按键可能触发多次 set_brake，日志也会重复刷。
                #   这里只取一次，转成 remove 语义。
                if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0):
                    if msg.message == WM_HOTKEY:
                        hotkey_seq += 1
                        tick = int(ctypes.windll.kernel32.GetTickCount())
                        gap = tick - last_hotkey_tick
                        already = gate.brake_engaged()
                        dup = already or (gap < _DEDUP_MS)
                        if not dup:
                            ok, why = gate.set_brake("全局热键 Ctrl+Alt+Home")
                            _log(("[OK] " if ok else "[X] ") + why)
                            prev = gate.brake_engaged()
                            # ★ 屏幕确认（子线程，不阻塞主循环）★
                            #   急停按下去必须**看得见**，否则用户不知道生效没生效。
                            _flash_alert(ok)
                            # ★ 窗I：横幅只是 2.5 秒的「我按到了」确认；
                            #   真正不能消失的是这张桌面置顶窗（是谁/它的 MCP 链接/怎么恢复）。
                            if gate.brake_engaged():
                                _popup_show(why)
                        else:
                            dup_count += 1
                        # 重复的**不逐条打日志** —— 按住不放会有几百条（实测 lParam
                        #   高位到 0x24=36 次），逐条记会把真记录从日志里冲掉。
                        last_hotkey_tick = tick
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            now = time.time()
            if now - last_poll >= 2:
                last_poll = now
                cur = gate.brake_engaged()
                if cur != prev:
                    # 别的通道（面板按钮/命令行）拉的，留痕方便追谁拉的
                    _log("刹车状态变化：%s（外部通道：面板按钮或命令行，非本热键）"
                         % ("拉下" if cur else "松开"))
                    # ★ 窗I：外部通道拉的也一样要弹 —— 用户没按热键，
                    #   更不知道发生了什么。解除后自动收起（急停期间绝不自退）。
                    if cur:
                        _popup_show("刹车状态变化：外部通道拉下")
                    else:
                        _popup_hide("刹车状态变化：已解除")
                    prev = cur
                # 重复触发的**汇总**（每 2s 最多一条，按住 10 秒也只多两条日志）
                if dup_count and hotkey_seq - dup_logged_seq >= 1:
                    _log("（键盘自动重复 %d 次已忽略：按住不放时系统会连发 WM_HOTKEY，"
                         "急停已幂等，无需重复动作）" % dup_count)
                    dup_logged_seq = hotkey_seq
                    dup_count = 0
    except KeyboardInterrupt:
        _log("监视退出（Ctrl+C）")
    finally:
        _clear_heartbeat()
        _popup_stop()
        user32.UnregisterHotKey(None, HOTKEY_ID)
    return 0


def _write_heartbeat():
    """写心跳：pid + 启动时间 + 热键 ID。面板靠它认「自己人」。

    为什么要它：check_hotkey_free 只知道「这键此刻被占」，
    **分不清是被本项目 brake-daemon 占着（正常）还是被别的程序抢了（危险）**。
    而面板是开机自启的，daemon 正常情况下一定已经在跑 ——
    所以两态判据会变成**每天开机必弹一个红色假告警**，
    久而久之用户对这块红字脱敏，真被抢时反而看不见。
    """
    try:
        os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
        payload = {"pid": os.getpid(), "hotkey_id": HOTKEY_ID,
                   "since": time.time()}
        tmp = HEARTBEAT + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, HEARTBEAT)
    except Exception:
        pass


def _clear_heartbeat():
    """退出时清掉心跳 —— 否则面板会以为 daemon 还活着。"""
    try:
        if os.path.exists(HEARTBEAT):
            os.remove(HEARTBEAT)
    except Exception:
        pass


def _flash_alert_sync(ok=True, why=""):
    """急停触发时在屏幕上闪一条横幅。**没有它等于没有急停。**

    用户 2026-10-05：「我直接按没有程序响应啊」—— 这句话点出了一个安全缺陷：
    daemon 是 --quiet 后台模式，按下热键**屏幕上什么都不发生**。
    而急停是「出事时救命」的动作：用户按了却不知道有没有生效，
    合理反应是「以为没生效，再按一次 / 或者干脆继续操作」——
    **这等于把救命开关做成了哑的。**

    所以：日志该 quiet 仍然 quiet（不刷屏），但**事件本身必须有确认反馈**。
    低频动作（真出事才按）给一次 2.5 秒的横幅，不是打扰，是确认。
    """
    try:
        import tkinter as tk
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        sw, _ = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = 560, 96
        root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, 90))
        bg = "#C42B1C" if ok else "#8A1A1A"
        root.configure(bg=bg)
        tk.Label(root, text=("⛔ 急停已生效" if ok else "⛔ 急停失败"),
                 bg=bg, fg="#FFFFFF", font=("Microsoft YaHei UI", 26, "bold")).pack(pady=(14, 0))
        tk.Label(root, text=why or ("一切自动控制已停止。解除：面板「▶ 解除急停」" if ok else ""),
                 bg=bg, fg="#FFD9D6", font=("Microsoft YaHei UI", 12)).pack()
        root.after(ALERT_MS, root.destroy)
        root.mainloop()
    except Exception:
        # 反馈失败**绝不影响急停本身** —— 闸门早就拉下了，界面只是锦上添花
        pass


def _flash_alert(ok=True, why=""):
    """同上，但**丢到子线程跑**。

    ★ 为什么必须开线程（第一版直接调 mainloop，那是致命 bug）★
      daemon 主循环是 while True + MsgWaitForMultipleObjects。
      在里面调 mainloop() 会**卡死 2.5 秒** —— 而这 2.5 秒恰恰是最需要能用的时刻：
      心跳不更新（面板会误判 daemon 已死）、热键也收不到。
      **急停是最需要其它功能都活着的时候，不能为了显示把主干堵了。**

      Windows 上 tkinter 可在独立线程里跑（自带 Tcl 解释器），
      daemon 主线程这里没有 Tk，所以不冲突。
    """
    try:
        import threading
        threading.Thread(target=_flash_alert_sync, args=(ok, why),
                         name="brake-alert", daemon=True).start()
    except Exception:
        pass          # 连线程都起不来就放弃显示，急停本身早已经在盘上了


# ── 窗I：桌面置顶急停窗（2026-10-16）──────────────────────────────────────
#
# 为什么要它：_flash_alert 那条横幅 2.5 秒就没了。急停是「出事时救命」的动作，
#   2.5 秒之后屏幕上什么都不剩 —— 用户没看见 = 没拉过。
#   所以另有一张**不自己消失**的置顶窗，写清「是谁在动 / 它的 MCP 链接 / 怎么恢复」。
#
# ★ 三条纪律（三条都写在 brake_popup.py 里，这里只管调）：
#   1. **不另起一套检测**：触发用的是本文件**已经有的**那个状态翻转
#      （热键那一支 + 2 秒轮询那一支），没加第二个线程去盯 BRAKE_FLAG。
#   2. **显示失败绝不影响急停**：下面三个函数全部吞异常、只留一行日志。
#      闸门早就拉下了，弹窗是锦上添花 —— 为了显示去动主干才是本末倒置。
#   3. **弹窗上没有任何能改急停状态的控件**（没有「解除急停」按钮），
#      且它的标题已进 legacy_tools\act.py 的 BLOCKED_TITLES，
#      agent 点不掉、盖不住它。这两条是判据 tests\_急停弹窗判据.py 的第 3、6 条。
def _popup_show(why=""):
    """急停拉下 ⇒ 弹置顶窗。非阻塞、失败只记日志。返回有没有真弹出来。"""
    try:
        from . import brake_popup
        ok = bool(brake_popup.show())
        _log("     桌面置顶急停窗：%s%s"
             % ("已弹出" if ok else "**没能弹出**（无桌面会话 / 建窗失败？）",
                ("（%s）" % why if why else "")))
        # ★ 窗P1（2026-10-16）：置顶窗弹不出来时**绝不许就这么算了**。
        #   本窗的病不是「没接线」，是「接线断了也没人吭声」—— 用户按下热键，
        #   屏幕上什么都不剩，他只知道「按了没反应」，于是再按一次、或者干脆继续干活。
        #   置顶窗起不来（多半是无桌面会话 / 建窗失败）还有一条退路：tkinter 横幅。
        #   它一样不是持久的，但**此刻屏幕上一定有东西**，而且日志与 stderr 都留痕。
        if not ok:
            _flash_alert(True, "桌面置顶急停窗没能弹出 —— 这是本次急停的唯一屏幕确认")
            _log("[X] 已降级为 2.5 秒横幅；急停本身**已生效**，恢复照常："
                 "面板「▶ 解除急停」或 legacy_tools\\brake.py release")
        return ok
    except Exception as e:
        _log("     桌面置顶急停窗：起不来（%s: %s）—— 急停本身已生效，不受影响"
             % (type(e).__name__, e))
        _flash_alert(True, "桌面置顶急停窗起不来（%s）—— 这是唯一屏幕确认" % e)
        return False


def _popup_preflight():
    """起 daemon 时**先验一次**「可见层这条路通不通」，通了才敢静默地靠它。

    ★ 为什么要有它（窗P1 · 2026-10-16）★
      母单那个病：一半的老线 daemon 压根没有弹窗接线，而用户照文档起它，
      按下热键**刹车拉下了、屏幕上却什么都没有**。
      这类故障最毒的地方在于**沉默** —— 它不报错，所以没人知道该修哪儿。
      所以规矩定死：**「拉下急停却没人看见」这件事必须自己在日志里喊出来**，
      不许等到出事那天才发现。

    这里验的是**接线在不在**（模块能不能 import、窗类名是什么），
    **不是**「真能不能建出窗」—— 建窗要到真急停那一刻才知道，那时才发现就晚了。
    说清楚这点，免得日后有人把这一行当成「弹窗没问题」的证据。
    """
    try:
        from . import brake_popup
        _log("     急停可见层：桌面置顶窗已接线（类名 %s）—— 真急停时弹出，"
             "解除后自动收起" % brake_popup.WINDOW_CLASS)
        return True
    except Exception as e:
        _log("[X] 急停可见层**没接上**（import brake_popup 失败：%s: %s）—— "
             "急停仍会拉下，但屏幕上只剩 2.5 秒横幅。"
             "先修这个再信「按热键看得见」这句话。" % (type(e).__name__, e))
        return False


def _popup_hide(why=""):
    """急停解除 ⇒ 自动收起置顶窗（**只有急停真的解除了才收**，不等用户点）。"""
    try:
        from . import brake_popup
        brake_popup.hide()
        _log("     桌面置顶急停窗：已收起%s" % ("（%s）" % why if why else ""))
        return True
    except Exception:
        return False


def _popup_stop():
    """daemon 退出前收尾（不关别人的闸门 —— 这里只关我们自己那个 Tk 窗）。"""
    try:
        from . import brake_popup
        brake_popup.stop()
    except Exception:
        pass


def check_hotkey_free() -> tuple[bool, str]:
    """**只查不注册**：试着注册再立刻注销，看这个键此刻有没有被占。

    为什么要有这个：面板是开机自启的，用户每天开机都能看到它；
    如果热键被别的程序占了，面板却什么都不说，用户会以为热键能救命。
    所以面板启动时查一次，占用就在面板上明说——
    「热键不可用，按规矩就不许进 B 级以上控制」。

    ⚠ 这里必须**立刻 UnregisterHotKey**：查完不注销，
    就把用户的 Ctrl+Alt+Home 自己占了，brake-daemon 真正想注册时就注册不上了。

    ★ 返回值改成**三态**（WorkBuddy 004 的 W2）★
    两态会把「daemon 正常占着」报成「被别的程序占用」，变成开机常态假告警。
        ("free",      "空闲")                —— 没人在用
        ("ours",      "本项目 brake-daemon 占用") —— 正常
        ("foreign",   "被其它程序占用")        —— 危险
    """
    ok = user32.RegisterHotKey(None, HOTKEY_ID,
                               MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                               VK_HOME)
    if ok:
        user32.UnregisterHotKey(None, HOTKEY_ID)
        return "free", "空闲"
    # 占着 → 看心跳文件在不在，判断是不是本项目的 daemon
    try:
        with open(HEARTBEAT, encoding="utf-8") as f:
            hb = json.load(f)
        age = time.time() - float(hb.get("since") or 0)
        if age <= HEARTBEAT_STALE_SEC and int(hb.get("pid") or 0) > 0:
            # ⚠ 第一版写成 `for p in _pid_alive(...)` —— 它返回的是 bool 不是序列，
            #   for 迭代 False 直接 TypeError → 掉进 except → 把「自己人」误报成
            #   「外人占用」。**那个 except 是个掩体，把真 bug 盖成了假告警。**
            alive = _pid_alive(int(hb["pid"]))
            if alive:
                return "ours", "已被本项目 brake-daemon 占用（正常，pid %d）" % hb["pid"]
        return "foreign", "被其它程序占用（心跳 pid %s 已停 %ds）" % (
            hb.get("pid"), int(age))
    except Exception:
        # 没有心跳文件 = 不是本项目占的
        return "foreign", "已被别的程序占用（非本项目 brake-daemon）"


def _pid_alive(pid):
    """进程还在吗。用最保守的办法：找不到就当不在。"""
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259      # STILL_ACTIVE
    except Exception:
        return False


# ── 模块入口（窗P1 · 2026-10-16 补）─────────────────────────────────────
#
# ★ 为什么此前没有它，以及为什么那比「不弹窗」更坏 ★
#   本文件此前 374 行到 _pid_alive 就结束了，**一个 __main__ 块都没有**。
#   于是 `python -m agentctl.brake_daemon`（模块入口）会：import 成功 → 什么都不做 →
#   **秒退**。屏幕上什么都不出现，日志里也什么都没有 —— 而它在文档和判据里被当正经入口。
#   用户照着敲，只看到窗口一闪就没了，最好的情况以为「已经起来了」。
#
#   ★ 母单给的两条判据，本块必须同时满足 ★
#     ① 它**必须能跑起来**（不能秒退）；
#     ② 它**必须弹窗**（接线在 run() 里，模块入口与 AgentCtl.exe 走的是同一条路）。
#   只满足①而不满足②，等于把「假成功」从秒退换成静默 —— 那还是看不见。
#
# 为什么用 run() 而不是复制一份逻辑：AgentCtl.exe brake-daemon 走的也是 run()，
#   这里**只做入口转发**，不新增第二份实现 —— 一个功能两份实现，迟早只维护一份。
def main(argv=None) -> int:
    """`python -m agentctl.brake_daemon` 的入口，等价 `AgentCtl.exe brake-daemon`。"""
    try:
        return run()
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        # 崩了必须**出声**。静默退出的守护 = 用户以为热键能救命，真出事按了没反应。
        msg = "[X] brake_daemon 崩了（%s: %s）" % (type(e).__name__, e)
        try:
            _log(msg)
        except Exception:
            pass
        try:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
        except Exception:
            pass
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
