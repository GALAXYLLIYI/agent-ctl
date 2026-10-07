# -*- coding: utf-8 -*-
"""gate.py —— 唯一闸门：任何"看屏幕/动键鼠"的动作都必须先过这里

过闸条件（全部满足才放行）：
  1. 刹车松开（`<工作区>\\桌面\\_急停.flag` 不存在）
  2. 有一张**没过期、没被用过、范围完全匹配**的通行证（`_auth/auth_pass.json`，由 auth_popup 签发）
  3. 动作范围与通行证范围**字符串完全一致**（A/B/C/D 级不通用，一字之差都不行）

为什么要有这个文件：光有三件套不够，**干活的脚本得真的去问它**。
绕过闸门的脚本 = 闸门不存在。所以 perceive.py 与 act.py 一律先 require()。

用法（脚本里）：
    import gate
    gate.require(gate.A, "读控件树")      # 不过就抛 GateDenied，脚本不许继续
用法（命令行）：
    python gate.py --check "A级 只读感知"
    python gate.py --end                    # 主动结束会话（消费掉通行证）
"""
import datetime
import json
import os
import sys
import time

# 输出被重定向/捕获时，Windows 下 Python 默认用 locale(GBK) 编码 → 打印 ⛔/✅ 会抛
# UnicodeEncodeError 把脚本打死（实测：gate.py --check 就这样崩过）。强制 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
AUTHDIR = os.path.join(HERE, "_auth")
PASSFILE = os.path.join(AUTHDIR, "auth_pass.json")
GATELOG = os.path.join(AUTHDIR, "gate.log")
# ★ 不写死本机路径：换机器/换用户名就废（开源的最低要求）。
#   而且**必须**和 src/agentctl/gate.py 的 BRAKE_FLAG 指向同一个文件，
#   否则「面板里拉急停」和「命令行 brake stop」各拉各的，
#   两边都显示成功，急停却没全局生效 —— 本机永远看不出来，一换机就露馅。
_APP_DIR = os.environ.get("AGENTCTL_APP_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl")
BRAKE_FLAG = os.environ.get("AGENTCTL_BRAKE_FLAG") or os.path.join(_APP_DIR, "_急停.flag")

A = "A级 只读感知"
B = "B级 点击滚动"
C = "C级 打字快捷键"
D = "D级 拖拽"
# 感知是所有级别的底座：手里拿着 B 证却读不了控件树，是自相矛盾（点之前总得先看）
PERCEIVE_SCOPES = (A, B, C, D)

# 会话内允许重复动作的级别（A 只读可多看几次；B 以上高危，每张证限定动作次数）
ACT_LIMIT = {A: 200, B: 20, C: 20, D: 10}


class GateDenied(Exception):
    pass


def log(msg):
    os.makedirs(AUTHDIR, exist_ok=True)
    with open(GATELOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def brake_engaged():
    return os.path.exists(BRAKE_FLAG)


def check(scope):
    """返回 (是否放行, 原因)。"""
    if brake_engaged():
        return False, "刹车已拉下（急停文件存在）—— 一切自动控制停止"
    p = _load(PASSFILE, {})
    if not p:
        return False, "没有通行证（需先由用户在弹窗里授权）"
    if p.get("consumed"):
        return False, "通行证已被使用（一次性，重新授权）"
    if time.time() > p.get("expires_at", 0):
        return False, f"通行证已过期（{p.get('expires')}）"
    # 范围：用户可以在一次授权里**明确列出**多个（如 B+C+D），但**绝不隐式升权**
    allowed = p.get("scopes") or ([p["scope"]] if p.get("scope") else [])
    if scope not in allowed:
        return False, f"范围不符：本次持 {'、'.join(allowed)}，本动作要「{scope}」"
    n = p.get("acts", 0)
    limit = p.get("acts_limit") or ACT_LIMIT.get(scope, 10)
    if n >= limit:
        return False, f"本张通行证动作次数已用尽（{n}/{limit}）"
    return True, f"范围「{scope}」有效至 {p.get('expires')}，已用 {n}/{limit} 次"


def require(scope, note="", count=True):
    """过闸；不过就抛 GateDenied。count=True 时记一次动作（会话动作计数）。"""
    ok, why = check(scope)
    if not ok:
        log(f"⛔ 拒绝放行（{note or scope}）：{why}")
        raise GateDenied(why)
    if count:
        p = _load(PASSFILE, {})
        p["acts"] = p.get("acts", 0) + 1
        p["last_act"] = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {note or scope}"
        _save(PASSFILE, p)
    log(f"✅ 放行（{note or scope}）：{why}")
    return why


def require_any(scopes, note="", count=True):
    """多个级别里任一有效即可放行（用于"感知"这种所有级别共用的动作）。"""
    reasons = []
    for s in scopes:
        ok, why = check(s)
        if ok:
            return require(s, note, count)
        reasons.append(f"{s} → {why}")
    log(f"⛔ 拒绝放行（{note or '任一范围'}）：" + "；".join(reasons))
    raise GateDenied("；".join(reasons))


def end_session(why="主动结束"):
    p = _load(PASSFILE, {})
    if not p or p.get("consumed"):
        return False, "没有可用会话"
    p["consumed"] = True
    p["consumed_at"] = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
    p["ended_why"] = why
    _save(PASSFILE, p)
    log(f"🔚 会话结束（{why}）：范围「{p.get('scope')}」，动作 {p.get('acts', 0)} 次")
    return True, f"会话结束：范围「{p.get('scope')}」，动作 {p.get('acts', 0)} 次"


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 1
    if a[0] == "--end":
        ok, why = end_session("命令行结束")
        print(("🔚 " if ok else "— ") + why)
        return 0 if ok else 3
    if a[0] == "--check":
        scope = a[1] if len(a) > 1 else A
        ok, why = check(scope)
        print(("✅ " if ok else "⛔ ") + why)
        return 0 if ok else 3
    if a[0] == "--brake":
        print("🛑 刹车已拉下" if brake_engaged() else "✅ 刹车松开")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
