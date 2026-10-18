# -*- coding: utf-8 -*-
"""brake.py —— 电脑控制的第 3 步：急停（刹车）

两条通道，任一生效即停：
  1) 全局热键 Ctrl+Alt+Home —— 常驻监听**已迁新线**（`brake-daemon`，带弹窗）；
     老线 `brake.py --daemon` **已退役**：只打印去向并 rc=2，不再常驻。
  2) 急停文件存在（桌面「急停（双击）.bat」建、 「恢复（双击）.bat」删）

**存在即停，必须留痕**：建文件时写入时间与来源，谁拉的手刹要查得到。
所有控制脚本在动手前必须先问 `brake.released()`，不 released 就不许动。

用法：
  python brake.py --daemon        # ❌ 已退役：只打印去向 + rc=2，不再常驻监听
  # 新线入口（带弹窗，拉闸时看得见）：
  #   python -X utf8 src\agentctl_main.py brake-daemon
  #   AgentCtl.exe brake-daemon
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
    """老线常驻热键 —— **已退役**（窗Q2 · 2026-10-16）。

    为什么退役，而不是「在老线补一个弹窗继续跑」：
      · 原来这个函数注册热键、按下即写急停文件，**全程没有任何用户可见输出** ——
        用户照旧文档 / 旧横幅起 daemon 之后，屏幕上什么都不会出现，真出事时只能靠猜。
        「以为看得见，其实看不见」比没有刹车更危险。
      · 弹窗能力已经落在新线 agentctl.brake_daemon（那才是唯一一份实现）。
        在老线再接一份 = 第三份并行副本，正是本仓反复出病的形状；
        而且 legacy_tools 必须能独立打包，不能反过来依赖新线。

    现在它只做一件事：大声说出「我退役了、去哪起」，并 **rc=2**。
    rc=2 是硬要求 —— 返回 0 等于告诉调用方「一切正常」，那比静默更坏。

    ★ 函数名**故意保留**：guardian.ensure_brake() 会 Popen 本脚本的 --daemon，
      别处也可能 from brake import daemon。保留名字，它们拿到的是 rc=2（可见失败）；
      改名 / 删函数，它们拿到的是 AttributeError（静默到下一层才炸）。

    ★ 模块顶部的 time / ctypes.wintypes / MOD_* / VK_HOME / WM_HOTKEY / user32
      **有意保留**：退役后确实没人用了，但 pack_dist 的 pyflakes 门槛把
      imported-but-unused 归在「提醒」级（不拦发包），留着零风险、删了多一处回归面。

    stop / release / status **不受影响**，继续可用（桌面 bat 与受控演练脚本在用）。
    """
    notice = (
        "\n"
        "================================================================\n"
        "  老线 brake.py --daemon 已退役（2026-10-16）\n"
        "\n"
        "  它不再注册热键、也不再常驻。原因很直白：它全程没有任何可见输出 ——\n"
        "  按旧文档起 daemon，屏幕上什么都不会出现，真出事时只能靠猜。\n"
        "\n"
        "  请改用新线常驻热键（带弹窗，拉闸时看得见）：\n"
        "      python -X utf8 src\\agentctl_main.py brake-daemon\n"
        "      AgentCtl.exe brake-daemon\n"
        "\n"
        "  手工通道不受影响，仍然可用：\n"
        "      python brake.py stop --why \"原因\"\n"
        "      python brake.py release\n"
        "      python brake.py status\n"
        "================================================================"
    )
    try:
        print(notice, file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        log("❌ brake.py --daemon 已退役：拒绝常驻监听（rc=2）—— 请改用新线 brake-daemon")
    except Exception:
        # ★ 留痕失败**绝不能**把「响亮拒绝」降级成 traceback + rc=1。
        #   拒绝永远优先于留痕（与 gate.py 的 fail-safe 同一形状）。
        pass
    return 2

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
