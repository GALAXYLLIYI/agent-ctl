# -*- coding: utf-8 -*-
"""brake.py —— 电脑控制的第 3 步：急停（刹车）

两条通道，任一生效即停：
  1) 全局热键 Ctrl+Alt+Home（常驻监听，`--daemon`）
  2) 急停文件存在（桌面「急停（双击）.bat」建、 「恢复（双击）.bat」删）

**存在即停，必须留痕**：建文件时写入时间与来源，谁拉的手刹要查得到。
所有控制脚本在动手前必须先问 `brake.released()`，不 released 就不许动。

用法：
  python brake.py --daemon        # 常驻监听热键（建议开机后手动起，不注册自启）
  python brake.py stop --why "桌面 bat"    # 拉手刹
  python brake.py release         # 松手刹（删文件，需人工再点一次授权才能继续控制）
  python brake.py status          # 看当前状态
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import datetime
import os
import sys
import time

# ★ T10 治本（2026-10-09）：不再写死本机桌面路径。
#   必须与 agent-ctl\src\agentctl\gate.py 的 BRAKE_FLAG 指向同一个文件，
#   否则「面板拉急停」与「热键拉急停」各拉各的，两边都显示成功、急停却没全局生效。
#   env AGENTCTL_BRAKE_FLAG 保留 —— T9/S2-4 的沙箱测试姿势依赖它。
_APP_DIR = os.environ.get("AGENTCTL_APP_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl")
FLAG = os.environ.get("AGENTCTL_BRAKE_FLAG") or os.path.join(_APP_DIR, "_急停.flag")
LOGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_auth")   # 跟着脚本走
LOGFILE = os.path.join(LOGDIR, "brake.log")

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_HOME = 0x24
WM_HOTKEY = 0x0312
user32 = ctypes.windll.user32

# 输出重定向到管道时 Python 用 GBK 编码，emoji 编不出来会直接抛 UnicodeEncodeError 把脚本打死。
# 控制台里本来走 WriteConsoleW，不受影响；这里只给重定向兜底。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


def log(msg):
    os.makedirs(LOGDIR, exist_ok=True)
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def engaged():
    """刹车是否拉下（急停文件存在即拉下）。"""
    return os.path.exists(FLAG)


def released():
    return not engaged()


def stop(why="未知来源", announce=True):
    if engaged():
        log(f"刹车已处于拉下状态，不重复动作（本次来源：{why}）")
        return False
    os.makedirs(os.path.dirname(FLAG), exist_ok=True)
    with open(FLAG, "w", encoding="utf-8") as f:
        f.write(f"急停已生效\n时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"来源：{why}\n说明：本文件存在期间，一切自动控制必须停止。\n"
                f"恢复：双击桌面「恢复（双击）.bat」并重新授权。\n")
    log(f"🛑 急停已拉下 —— 来源：{why}")
    if announce:
        print("急停文件已建立：", FLAG)
    return True


def release(by="人工"):
    if not engaged():
        log("刹车本来就是松的，无需恢复")
        return False
    try:
        with open(FLAG, encoding="utf-8") as f:
            trace = f.read().strip().splitlines()
    except Exception:
        trace = []
    os.remove(FLAG)
    log(f"✅ 急停已解除 —— 操作人：{by}｜解除的急停记录：{trace[:2]}")
    return True


def daemon():
    """常驻：注册全局热键，按下即拉刹车。"""
    if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_HOME):
        err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        log(f"❌ 热键 Ctrl+Alt+Home 注册失败（可能被别的程序占了）——"
            f"按准则：热键不可用就不许进入 B 级以上的控制。err={err}")
        return 2
    log("✅ 急停热键已注册：Ctrl+Alt+Home（按下即刹车，留痕到 "
        f"{FLAG}）")
    log(f"当前刹车状态：{'已拉下' if engaged() else '松开'}")
    prev = engaged()
    msg = wt.MSG()
    last_poll = 0.0
    while True:
        # 有消息就处理（阻塞等待，不空转），超时 0.5 秒回来顺手看一眼文件状态
        r = user32.MsgWaitForMultipleObjects(0, None, False, 500, 0x04FF)  # QS_ALLINPUT
        if r == 0:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY:
                    stop(why="全局热键 Ctrl+Alt+Home")
                    prev = engaged()      # 自己动的手也要同步，否则留痕里冒出假的"文件被人动过"
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        now = time.time()
        if now - last_poll >= 2:
            last_poll = now
            cur = engaged()
            if cur != prev:
                log(f"刹车状态变化：{'拉下' if cur else '松开'}（外部通道：桌面 bat 或命令行，非热键）")
                prev = cur
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="status",
                    choices=["daemon", "stop", "release", "status"])
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--why", default="命令行调用")
    a = ap.parse_args()
    if a.daemon or a.action == "daemon":
        return daemon()
    if a.action == "stop":
        return 0 if stop(a.why, announce=False) else 7
    if a.action == "release":
        return 0 if release("命令行/桌面 bat") else 7
    on = engaged()
    print("🛑 刹车已拉下（急停文件存在）：" + FLAG if on
          else "✅ 刹车松开，自动控制可申请运行（仍需每次授权）")
    if on:
        try:
            print(open(FLAG, encoding="utf-8").read().strip())
        except Exception:
            pass
    return 0 if on else 1


if __name__ == "__main__":
    raise SystemExit(main())
