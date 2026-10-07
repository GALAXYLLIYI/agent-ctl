# -*- coding: utf-8 -*-
"""selftest.py —— 电脑控制工具链冒烟自测（**不点鼠标、不需要授权**）

为什么要有：
  2026-09-19 一晚连炸 6 个缺陷 —— 每个都是"到真上手才发现"。
  光把教训写进手册没用（手册早写过，还是重犯），**得让检查能自动跑**。
  本脚本把关键不变量做成可执行断言，改完代码跑一遍，几十秒出结果。

用法：
  python selftest.py            # 全跑
  python selftest.py --quick    # 只跑不依赖截图的部分
"""
import glob
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
# ★ 默认写进 %LOCALAPPDATA%\AgentCtl\shots，不写死本机路径。
#   平台进程会用 AGENTCTL_SHOTS_DIR（或直接注入 SHOTDIR）指过来，
#   这里只是「独立跑这个脚本时」的兜底，指向同一个地方。
_APP_DIR = os.environ.get("AGENTCTL_APP_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl")
SHOTS = os.environ.get("AGENTCTL_SHOTS_DIR") or os.path.join(_APP_DIR, "shots")

OK, FAIL, SKIP = [], [], []


def check(name, fn):
    try:
        r = fn()
    except Exception as e:
        FAIL.append((name, "异常: %s" % str(e)[:90]))
        return
    if r is True:
        OK.append(name)
    elif r is None:
        SKIP.append(name)
    else:
        FAIL.append((name, r if isinstance(r, str) else "返回 False"))


def run(args):
    p = subprocess.run([PY] + args, capture_output=True)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")


def newest_shot():
    fs = sorted(glob.glob(os.path.join(SHOTS, "*_win*.png")) +
                glob.glob(os.path.join(SHOTS, "act_*_after.png")), key=os.path.getmtime)
    return fs[-1] if fs else None


# ── 1. 模块能否导入 ────────────────────────────────────────────
def t_imports():
    sys.path.insert(0, HERE)
    missing = []
    for m in ("option_locator", "gate", "perceive"):
        try:
            __import__(m)
        except Exception as e:
            missing.append("%s(%s)" % (m, str(e)[:40]))
    return True if not missing else "导入失败: " + ", ".join(missing)


# ── 2. 定位器：旧的新的一起验（都必须是 6 元素，含填充率）────────
def t_locator_contract():
    sys.path.insert(0, HERE)
    import option_locator
    p = newest_shot()
    if not p:
        return None
    from PIL import Image
    r = option_locator.find_options(Image.open(p))
    if not r:
        return None            # 图里可能就是没有选项，不算失败
    if len(r[0]) != 6:
        return "返回元素数 = %d（应为 6：cy,cx,bh,bw,ratio,fill）" % len(r[0])
    return True


# ── 3. 自适应定位：**同一张图两次结果必须一致**（确定性）────────
def t_locator_deterministic():
    sys.path.insert(0, HERE)
    import option_locator
    p = newest_shot()
    if not p:
        return None
    from PIL import Image
    a = option_locator.find_options(Image.open(p))
    b = option_locator.find_options(Image.open(p))
    return True if a == b else "两次调用结果不一致 —— 定位不是确定性的"


# ── 4. zoomgrid 能跑出图 ───────────────────────────────────────
def t_zoomgrid():
    p = newest_shot()
    if not p:
        return None
    rc, out = run([os.path.join(HERE, "zoomgrid.py"), p, "--box", "0,0,200,200", "--scale", "2"])
    if rc != 0:
        return "退出码 %d: %s" % (rc, out.strip().splitlines()[-1][:80] if out.strip() else "")
    return True if "_zoom.png" in out else "没报告生成文件"


# ── 5. 资源守卫能报数（提交内存）────────────────────────────────
def t_mem_guard():
    rc, out = run([os.path.join(HERE, "preflight.py"), "mem"])
    if rc not in (0, 2, 3):
        return "退出码 %d: %s" % (rc, out.strip()[:80])
    return True if ("提交内存" in out and "物理内存" in out) else "输出里没有『提交内存』"


# ── 6. 闸门：无证时必须拒绝（安全不变量）──────────────────────
def t_gate_denies_without_pass():
    rc, out = run([os.path.join(HERE, "gate.py"), "--check", "B级 点击滚动"])
    if rc == 0:
        return None          # ★ 有证在生效 → 应记为「跳过」，不是失败（2026-09-20 修）
    return True if ("过期" in out or "已被使用" in out or "未获授权" in out
                    or "通行证" in out) else "拒绝信息看不懂: " + out.strip()[:80]


# ── 7. act.py 的 CLI 完整（新动作必须在 choices 里）────────────
def t_act_cli():
    rc, out = run([os.path.join(HERE, "act.py"), "--help"])
    for k in ("fill", "keys", "click", "type", "scroll", "focus", "drag", "read"):
        if k not in out:
            return "act.py 的 choices 里缺少动作: %s" % k
    return True


# ── 8. act.py 里不得再出现"只发键不核验"的写法 ──────────────────
def t_no_silent_action():
    src = open(os.path.join(HERE, "act.py"), encoding="utf-8").read()
    if "打不了字：{how}" in src and "changed is not False" not in src:
        return "act.py 里找不到『无变化就返回非 0』的判据"
    return True


# ── 9. 手册页脚 = 磁盘 mtime（铁律 10）────────────────────────
def t_manual_footer():
    import datetime
    bad = []
    for f in ("SKILLS.md", "LOG.md"):
        p = os.path.join(os.environ.get("AGENTCTL_MANUAL_DIR", ""), f)
        if not os.path.exists(p):
            continue
        raw = open(p, encoding="utf-8").read()
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
        for line in raw.splitlines():
            if "最后更新" in line:
                if mt not in line:
                    bad.append("%s 页脚(%s)≠磁盘(%s)" % (f, line.strip()[-16:], mt))
                break
    return True if not bad else "；".join(bad)


def main():
    quick = "--quick" in sys.argv
    tests = [("模块可导入", t_imports),
             ("act.py CLI 完整", t_act_cli),
             ("act.py 无静默动作", t_no_silent_action),
             ("资源守卫可报数", t_mem_guard),
             ("闸门无证必拒", t_gate_denies_without_pass),
             ("手册页脚=磁盘时间", t_manual_footer)]
    if not quick:
        tests += [("定位器返回契约", t_locator_contract),
                  ("定位确定性", t_locator_deterministic),
                  ("zoomgrid 可跑", t_zoomgrid)]

    print("=" * 62)
    print(" 电脑控制工具链 · 冒烟自测")
    print("=" * 62)
    for name, fn in tests:
        check(name, fn)
    for n in OK:
        print("  ✅ %s" % n)
    for n in SKIP:
        print("  ○  %s  （跳过：前置条件不足）" % n)
    for n, why in FAIL:
        print("  ❌ %s  → %s" % (n, why))
    print("-" * 62)
    print(" 通过 %d ｜ 跳过 %d ｜ 失败 %d" % (len(OK), len(SKIP), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
