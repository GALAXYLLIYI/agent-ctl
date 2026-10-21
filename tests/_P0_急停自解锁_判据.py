# -*- coding: utf-8 -*-
r"""P0 判据：**急停不许由 agent 自己解掉**（2026-10-21 新增）

================================ 本单要什么 ================================
治的病（2026-10-21 真跑撞出来的现场事故）：
    agent 用自己唯一的工具接口（ctl_brake --action release），
    连续三次解掉**自己触发的**急停 —— 理由栏写的还是「自触发的 fail_streak 误伤」。
    ⇒ 「刹车能被被刹者拆掉，就不叫刹车」。
    ★ 这不是易用性问题，是**安全边界**问题：急停是用户的最后一道否决权，
      连它都能被 agent 自己撤掉，那「随时能急停」这句话就是空的。

================================ 判据冻结了什么 ================================
    1 · 工具面（MCP / HTTP，即 agent 唯一顺手的接口）请求 release ⇒ **被拒**
        （code=9 未授权），且**急停仍在**（不许出现「拒了但其实解了」）
    2 · 工具面请求 stop ⇒ **照旧允许**（安全方向永远不许被堵）
    3 · 人路（面板按钮 / 命令行显式确认）⇒ 放行，且急停**真解除**
    4 · human 标记**必须复位**（HTTP 是多线程的，不复位会串到下一个请求上）
    5 · 命令行：解急停**必须显式确认**（--confirm-release），不带就拒绝 + 给用法
    6 · 静态查：core.t_brake 的 release 分支里必须有人路闸（防止有人把闸删了）

================================ 跑红（注入即红）============================
    python -X utf8 tests\_P0_急停自解锁_判据.py --inject
    ⇒ 把 core._is_human_call 打成恒 True（= 回到改前：谁都能解）
      ⇒ 第 1 条必须变红（工具面被放行了）

★ 本判据**不碰真机状态**：急停旗子 / 通行证 / 租约 / 时间线全部指向临时目录
  （env 覆盖），跑完即删 —— 不会拉用户真急停（gate.py 的老规矩，保留）。
退出码：0=绿；1=红；2=验不了。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "src")
CORE = os.path.join(SRC, "agentctl", "core.py")

# ★ 隔离环境：必须在 import 闸门/模块**之前**设好
TMP = tempfile.mkdtemp(prefix="P0brake_")
STATE = os.path.join(TMP, "state")
APP = os.path.join(TMP, "app")
os.makedirs(STATE, exist_ok=True)
os.makedirs(APP, exist_ok=True)
os.environ["AGENTCTL_LEGACY_HOME"] = STATE
os.environ["AGENTCTL_APP_DIR"] = APP
os.environ["AGENTCTL_BRAKE_FLAG"] = os.path.join(APP, "_急停.flag")
os.environ["AGENTCTL_TIMELINE"] = os.path.join(TMP, "timeline")
os.environ["AGENTCTL_REQUIRE_LEASE"] = "0"
if SRC not in sys.path:
    sys.path.insert(0, SRC)
from agentctl import core, gate          # noqa: E402

GREEN, RED, SKIP = [], [], []
INJECT = False


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def attempt(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, "抛异常 %s: %s" % (type(e).__name__, e)
    rec(name, ok, detail)


def clear_brake():
    p = os.environ["AGENTCTL_BRAKE_FLAG"]
    if os.path.exists(p):
        os.remove(p)


def engaged():
    return os.path.exists(os.environ["AGENTCTL_BRAKE_FLAG"])


def main():
    global INJECT
    INJECT = "--inject" in sys.argv
    if INJECT:
        # ★ 注入：把「人路闸」拆了（= 改前口径：谁都能解）。
        core._is_human_call = lambda: True
        print("（--inject：已把 core._is_human_call 打成恒 True ⇒ 期望第 1 条红）", flush=True)
    print("=" * 74)
    print("P0 · 急停不许由 agent 自己解掉（2026-10-21）")
    print("=" * 74)
    print("  隔离旗子 = %s" % os.environ["AGENTCTL_BRAKE_FLAG"], flush=True)

    def c1():
        clear_brake()
        r = core.dispatch("ctl_brake", {"action": "release", "actor": "判据agent"})
        if r.get("ok"):
            return False, "★工具面居然放行了 release（刹车能被被刹者拆掉）"
        if int(r.get("code") or 0) != 9:
            return False, "拒绝码应是 9（未授权），实测 %r" % r.get("code")
        if not str(r.get("warning") or ""):
            return False, "拒绝时必须说明人怎么解（不许只甩一个码）"
        if engaged():
            return False, "★拒了但急停其实被解掉了"
        return True, "工具面 release 被拒（code=9），急停仍在，处方已说明"

    attempt("1 · 工具面（MCP/HTTP）请求 release ⇒ 被拒，急停不解", c1)

    def c2():
        clear_brake()
        r = core.dispatch("ctl_brake", {"action": "stop", "actor": "判据agent", "why": "判据自检"})
        if not r.get("ok"):
            return False, "★工具面连 stop 都不许了（安全方向被堵 = 方向反了）"
        if not engaged():
            return False, "stop 之后旗子应存在"
        return True, "stop 照旧允许（安全方向永远通）"

    attempt("2 · 工具面 stop ⇒ 照旧允许（安全方向不许被堵）", c2)

    def c3():
        clear_brake()
        core.dispatch("ctl_brake", {"action": "stop", "actor": "判据agent", "why": "判据自检"})
        if not engaged():
            return False, "前置失败：急停没拉下来"
        r = core.dispatch("ctl_brake", {"action": "release", "actor": "用户"}, human=True)
        if not r.get("ok"):
            return False, "★人路也放不开（会把人锁死在外面）"
        if engaged():
            return False, "★人路放行了但急停没真解除"
        return True, "人路（面板/命令行显式确认）放行且急停真解除"

    attempt("3 · 人路 release ⇒ 放行，且急停真解除", c3)

    def c4():
        clear_brake()
        core.dispatch("ctl_brake", {"action": "stop", "actor": "判据agent", "why": "判据自检"})
        core.dispatch("ctl_brake", {"action": "release", "actor": "用户"}, human=True)
        r = core.dispatch("ctl_brake", {"action": "release", "actor": "判据agent"})
        if r.get("ok"):
            return False, "★human 标记没复位：后面的 agent 调用被误放行"
        return True, "human 标记调用结束后已复位（不会串到下一个请求）"

    attempt("4 · human 标记**必须复位**（HTTP 多线程不许串）", c4)

    def c5():
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC
        env["AGENTCTL_BRAKE_FLAG"] = os.path.join(APP, "_急停.flag")
        env["AGENTCTL_APP_DIR"] = APP
        env["AGENTCTL_TIMELINE"] = os.path.join(TMP, "timeline")
        clear_brake()
        p1 = subprocess.run([sys.executable, "-X", "utf8", "-m", "agentctl.cli",
                             "brake", "--action", "release"],
                            cwd=SRC, env=env, capture_output=True, text=True, timeout=120)
        if p1.returncode == 0:
            return False, "★命令行不带 --confirm-release 也放行了（没起到确认作用）"
        if "confirm-release" not in (p1.stdout + p1.stderr):
            return False, "命令行拒绝时必须把正确用法打出来（用户要知道怎么办）"
        clear_brake()
        p2 = subprocess.run([sys.executable, "-X", "utf8", "-m", "agentctl.cli",
                             "brake", "--action", "release", "--confirm-release"],
                            cwd=SRC, env=env, capture_output=True, text=True, timeout=120)
        if p2.returncode != 0:
            return False, "带 --confirm-release 反而失败（rc=%s）：%s" % (
                p2.returncode, (p2.stdout + p2.stderr)[:200])
        return True, "不带 --confirm-release ⇒ 拒绝并给用法；带了 ⇒ 放行"

    attempt("5 · 命令行解除急停**必须显式确认**（--confirm-release）", c5)

    def c6():
        with open(CORE, encoding="utf-8", errors="replace") as f:
            src = f.read()
        bad = []
        if "_is_human_call" not in src:
            bad.append("core.py 里没有 _is_human_call（人路闸被删了？）")
        if "human: bool = False" not in src:
            bad.append("dispatch 的签名里没有 human 参数（人路标记没接上）")
        # ★ 只看 t_brake 那个函数体 —— core 里还有别的 release 分支（租约释放），
        #   拿全文第一个匹配会看错函数（这条判据自己被带偏过一次）。
        j = src.find("def t_brake(")
        i = src.find('if action == "release":', j if j >= 0 else 0)
        if j < 0:
            bad.append("core.py 里找不到 t_brake")
        elif i < 0:
            bad.append("t_brake 里找不到 release 分支")
        else:
            seg = src[i:i + 1500]
            if "_is_human_call" not in seg:
                bad.append("release 分支里没有人路闸（这段代码被改坏了）")
        return (not bad), ("；".join(bad) if bad else "release 分支有人路闸")

    attempt("6 · 静态：release 分支里必须有人路闸", c6)

    try:
        shutil.rmtree(TMP, ignore_errors=True)
    except Exception:
        pass
    if RED:
        print("\n红 %d：%s" % (len(RED), "；".join(RED)), flush=True)
        return 1
    print("\n全绿 %d" % len(GREEN), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
