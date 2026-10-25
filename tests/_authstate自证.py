# -*- coding: utf-8 -*-
r"""_authstate自证.py —— 窗口D-3 · P1-B 授权状态父子模块自证

★ 铁律三条，本脚本自己先做到：
  1. **只读 + 临时目录假状态**：状态文件与 gate.lock 全部落在
     %TEMP%\authstate_self_<pid>\ 下。**绝不碰真 auth_pass.json、绝不碰真 authstate.json、
     绝不碰真急停 flag。** —— 锁路径 = 状态文件所在目录（authstate._Txn.authdir），
     所以状态落临时目录 ⇒ 锁也落临时目录，隔离是**结构性**的，不靠自觉。
  2. **急停用注入**：brake 一律传假值，**不拉用户真急停**
     （沿用 gate.py:401「不能为了跑测试去拉用户的真急停」的老规矩）。
  3. **断言带真实输出**：每条都打印判定依据，不许只印 PASS。

用法：
    python -X utf8 tests\_authstate自证.py
    python -X utf8 tests\_authstate自证.py --keep     # 保留临时目录以便排障

★ 仓根（从 __file__ 推，tests\ 的上一级 = 仓根）：干净 clone 上跑的就是它自己那份仓。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _presrc():
    """--src 要在 import 之前生效，所以先从 argv 里把它捞出来。"""
    for i, a in enumerate(sys.argv):
        if a == "--src" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


SRC = _presrc() or os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from agentctl import authstate as A          # noqa: E402

FAILURES = []


def NO_BRAKE():
    """注入：急停没拉。绝不碰真 flag。"""
    return False


def BRAKE_ON():
    """注入：急停拉下。"""
    return True


def ok(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print("  [%s] %s%s" % (tag, name, ("  ·  " + detail) if detail else ""))
    if not cond:
        FAILURES.append(name)
    return cond


def head(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def worker(state_path, exe, cls, created):
    """子进程入口：只做一次窗口放行。并发那条靠它。"""
    A.allow_window(exe, cls, created, path=state_path)

    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    ap.add_argument("--src", help="把 agentctl 目录指到别处（验判据能红用）")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="authstate_self_%d_" % os.getpid())
    state = os.path.join(tmp, "authstate.json")
    lock = os.path.join(tmp, "gate.lock")
    print("被测模块：%s" % A.__file__)
    print("沙箱目录：%s" % tmp)
    print("状态文件：%s" % state)
    print("锁文件  ：%s   ← 与状态同目录 ⇒ 结构上碰不到真 authdir" % lock)
    rc = 1
    try:
        rc = run_all(state, tmp)
    finally:
        if args.keep:
            print("\n（--keep：临时目录保留在 %s）" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 70)
    if FAILURES:
        print("自证结果：%d 条红 —— %s" % (len(FAILURES), "；".join(FAILURES)))
        return 1
    print("自证结果：全绿")
    return rc


def run_all(state, tmp):
    lock = os.path.join(tmp, "gate.lock")
    W1 = {"exe": "notepad.exe", "cls": "Notepad", "created": 111111}
    W2 = {"exe": "qq.exe", "cls": "QQWindow", "created": 222222}
    W3 = {"exe": "chrome.exe", "cls": "Chrome_WidgetWin_1", "created": 333333}

    head("0. 隔离前提（真 auth_pass.json / 真急停 一概不碰）")
    A.revoke_all(path=state)               # 这一下就会取锁 + 落盘
    ok("锁文件落在沙箱内", os.path.isfile(lock), lock)
    real_authdir = None
    try:
        from agentctl import gate as _g
        real_authdir = getattr(_g, "AUTHDIR", None)
    except Exception:
        pass
    ok("沙箱锁 != 真闸门锁",
       (not real_authdir)
       or os.path.abspath(lock) != os.path.abspath(os.path.join(real_authdir, "gate.lock")),
       "真闸门目录=%s" % real_authdir)

    head("1. 窗口放行只追加（windows ∪ key），重复放行不增殖")
    A.grant_task(["A", "B", "C"], path=state)
    A.allow_window(W1["exe"], W1["cls"], W1["created"], path=state)
    s = A.read(state)
    ok("放了第 1 个窗口", len(s["windows"]) == 1,
       repr([w["exe"] for w in s["windows"]]))

    A.allow_window(W2["exe"], W2["cls"], W2["created"], path=state)
    s = A.read(state)
    ok("再放第 2 个 ⇒ 共 2 个，前一个没被踩掉", len(s["windows"]) == 2,
       repr([(w["exe"], w["created"]) for w in s["windows"]]))

    A.allow_window(W1["exe"], W1["cls"], W1["created"], path=state)
    s = A.read(state)
    ok("同一窗重复放行 ⇒ 仍 2 个（身份键去重，不增殖）", len(s["windows"]) == 2,
       repr([(w["exe"], w["created"]) for w in s["windows"]]))

    A.allow_window(W1["exe"], W1["cls"], W1["created"] + 7, path=state)
    s = A.read(state)
    ok("同 exe+cls 但 created 不同 ⇒ 算新窗（不是同一条）", len(s["windows"]) == 3,
       repr([(w["exe"], w["created"]) for w in s["windows"]]))

    head("2. 窗口放行不抬高 scopes（dispatch_L 的已确诊 bug）")
    before = list(A.read(state)["task"]["scopes"])
    A.allow_window(W3["exe"], W3["cls"], W3["created"], path=state)
    after = A.read(state)
    ok("放行后 task.scopes 一字未改", after["task"]["scopes"] == before,
       "%s → %s" % (before, after["task"]["scopes"]))
    r_b = A.check("B", path=state, brake=NO_BRAKE)
    ok("B 档仍可用（没被降成 C-only）", r_b["ok"], r_b["reason"])
    r_d = A.check("D", path=state, brake=NO_BRAKE)
    ok("D 档仍被拒（放行不扩权）", not r_d["ok"], r_d["reason"])

    A.revoke_all(path=state)
    A.grant_task(["A"], path=state)
    A.allow_window(W1["exe"], W1["cls"], W1["created"], path=state)
    r_dw = A.check("D", window=W1, path=state, brake=NO_BRAKE)
    ok("只授 A：放行窗口后 D 仍拒", not r_dw["ok"], r_dw["reason"])

    head("3. 窗口放行不重置 acts 计数（任务总账）")
    A.revoke_all(path=state)
    A.grant_task(["A", "B"], acts_max=60, path=state)
    for _ in range(5):
        A.consume(1, path=state, brake=NO_BRAKE)
    used_before = A.read(state)["task"]["acts_used"]
    A.allow_window(W2["exe"], W2["cls"], W2["created"], path=state)
    used_after = A.read(state)["task"]["acts_used"]
    ok("放行前后 acts_used 不变", used_before == used_after == 5,
       "%d → %d" % (used_before, used_after))
    A.allow_window(W3["exe"], W3["cls"], W3["created"], path=state)
    ok("连放两个窗也不变", A.read(state)["task"]["acts_used"] == 5,
       "acts_used=%s" % A.read(state)["task"]["acts_used"])

    head("4. 父子寿命：子 exp = min(自身, 父)；父亡 ⇒ 子全灭；急停 ⇒ 子全灭")
    A.revoke_all(path=state)
    base = time.time()
    A.grant_task(["A", "B"], ttl=60, path=state, now=base)
    A.allow_window(W1["exe"], W1["cls"], W1["created"], ttl=1800, path=state, now=base)
    s = A.read(state)
    child_exp = s["windows"][0]["exp"]
    parent_exp = s["task"]["exp"]
    ok("子 exp 被 clip 到父（不是并存）",
       abs(child_exp - parent_exp) < 0.01 and child_exp < base + 1800,
       "父 +%.0fs / 子 +%.0fs（子本来要 1800s）" % (parent_exp - base, child_exp - base))

    r = A.check("B", window=W1, path=state, now=base + 10, brake=NO_BRAKE)
    ok("父在时该窗口放行可用", r["ok"], r["reason"])

    r = A.check("B", window=W1, path=state, now=base + 61, brake=NO_BRAKE)
    ok("父过期 ⇒ 子随之判灭", not r["ok"], r["reason"])
    r = A.check("B", path=state, now=base + 61, brake=NO_BRAKE)
    ok("父过期 ⇒ 连父档位也拒", not r["ok"], r["reason"])

    # ★ clip 正常情况下**造不出**「子 exp > 父 exp」（子 = min(自身, 父) 恒 ≤ 父），
    #   所以上面那条断言其实是 clip 的恒等推论，绿了也说明不了「check 独立复核了父」——
    #   把 check 里的父检查删掉，它照样绿。
    #   这里直接改状态文件造出反常场景（子 exp 未到、父 exp 已过），
    #   验的才是「父亡一票否决」，也才打得住 parent_expired_ignored 那条注入。
    import json as _json
    with open(state, encoding="utf-8") as f:
        st = _json.load(f)
    st["task"]["exp"] = base - 1                  # 父已亡
    st["windows"][0]["exp"] = base + 9999         # 子还活着
    with open(state, "w", encoding="utf-8") as f:
        _json.dump(st, f, ensure_ascii=False)
    r = A.check("B", window=W1, path=state, now=base + 10, brake=NO_BRAKE)
    ok("子 exp 未到但父已亡 ⇒ 仍判灭（父亡一票否决）", not r["ok"], r["reason"])

    r = A.check("B", window=W1, path=state, now=base + 10, brake=BRAKE_ON)
    ok("急停拉下 ⇒ 子判灭", not r["ok"], r["reason"])
    r = A.check("B", path=state, now=base + 10, brake=BRAKE_ON)
    ok("急停拉下 ⇒ 父也判灭", not r["ok"], r["reason"])

    A.revoke_all(path=state)
    b2 = time.time()
    A.grant_task(["A", "B"], ttl=1800, path=state, now=b2)
    A.allow_window(W1["exe"], W1["cls"], W1["created"], ttl=60, path=state, now=b2)
    ok("子先亡：父还在但子没了",
       A.check("B", path=state, now=b2 + 30, brake=NO_BRAKE)["ok"]
       and not A.check("B", window=W1, path=state, now=b2 + 61, brake=NO_BRAKE)["ok"],
       A.check("B", window=W1, path=state, now=b2 + 61, brake=NO_BRAKE)["reason"])

    head("5. 并发两写者不丢更新（同一把锁 + 读-改-写）")
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["PYTHONIOENCODING"] = "utf-8"

    def spawn(w):
        return subprocess.Popen(
            # ★ --src 必须**一起**传给 worker：worker 进程也会自己跑一次
            #   _presrc()，只给 --worker 的话它会退回真源 ⇒ 并发用例变成
            #   「真源 worker + 副本父进程」的混合，等于没测副本。
            [sys.executable, "-X", "utf8", os.path.abspath(__file__),
             "--src", SRC, "--worker",
             state, w["exe"], w["cls"], str(w["created"])],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    A.revoke_all(path=state)
    A.grant_task(["A", "B", "C", "D"], path=state)
    procs = [spawn(W1), spawn(W2)]
    outs = []
    for p in procs:
        o, _ = p.communicate(timeout=60)
        outs.append(o.decode("utf-8", "replace").strip())
    codes = [p.returncode for p in procs]
    ok("两个写者都成功退出", codes == [0, 0],
       "codes=%s %s" % (codes, " | ".join(x[-160:] for x in outs if x)))
    s = A.read(state)
    got = sorted((w["exe"], w["created"]) for w in s["windows"])
    want = sorted([(W1["exe"], W1["created"]), (W2["exe"], W2["created"])])
    ok("两个窗都在（没有丢更新）", got == want,
       "实得 %s / 应得 %s" % (got, want))

    A.revoke_all(path=state)
    A.grant_task(["A", "B", "C", "D"], path=state)
    many = [{"exe": "p%d.exe" % i, "cls": "W", "created": 900000 + i} for i in range(4)]
    procs = [spawn(w) for w in many]
    for p in procs:
        p.communicate(timeout=90)
    codes = [p.returncode for p in procs]
    s = A.read(state)
    ok("4 个写者全成功", codes == [0, 0, 0, 0], "codes=%s" % codes)
    ok("4 个窗一个不缺", len(s["windows"]) == 4,
       repr(sorted(w["exe"] for w in s["windows"])))

    head("6. revoke_all：父与子同时清空")
    A.revoke_all(path=state)
    s = A.read(state)
    ok("task 为空且 windows 清零", s["task"] is None and s["windows"] == [],
       "task=%r windows=%d" % (s["task"], len(s["windows"])))
    r = A.check("A", path=state, brake=NO_BRAKE)
    ok("吊销后任何档位都拒", not r["ok"], r["reason"])

    head("7. 额度用尽即拒（绝不当成功）")
    A.grant_task(["A"], acts_max=3, path=state)
    for _ in range(3):
        A.consume(1, path=state, brake=NO_BRAKE)
    try:
        A.consume(1, path=state, brake=NO_BRAKE)
        ok("第 4 次扣减应被拒", False, "居然成功了")
    except A.AuthStateDenied as e:
        ok("第 4 次扣减被拒", True, str(e))

    head("8. 敏感窗终判（授权弹窗/UAC/系统设置 永久拒 · 坐标点击绕不过）")
    A.revoke_all(path=state)
    A.grant_task(["A", "B", "C", "D"], path=state)

    hard_cases = [
        ("python.exe", "TkTopLevel", "授权弹窗属主"),
        ("AgentCtl.exe", "AgentCtlBrakePopup", "急停弹窗"),
        ("consent.exe", "#32770", "UAC 提权框"),
        ("SystemSettings.exe", "ApplicationFrameWindow", "系统设置页"),
    ]
    for e, c, why in hard_cases:
        r = A.check("B", window={"exe": e, "cls": c, "created": 1},
                    path=state, brake=NO_BRAKE)
        ok("永久拒：%s（%s）" % (e, why),
           (not r["ok"]) and r.get("deny_kind") == "hard", r["reason"][:72])

    # ★ 这条才是「动作时终判」的意义：敏感窗被 allow_window 放行过了，
    #   坐标点击照样拒 —— 原先只有弹窗层那道防线，这里是第二道。
    A.allow_window("python.exe", "TkTopLevel", 424242, path=state)
    r = A.check("B", window={"exe": "python.exe", "cls": "TkTopLevel",
                             "created": 424242}, path=state, brake=NO_BRAKE)
    ok("已放行的敏感窗**仍然拒**（防绕过）",
       (not r["ok"]) and r.get("deny_kind") == "hard", r["reason"][:72])

    r = A.check("B", window={"exe": "cmd.exe", "cls": "ConsoleWindowClass",
                             "created": 2}, path=state, brake=NO_BRAKE)
    ok("终端族默认拒（30 分钟临时放行归 act.py）",
       (not r["ok"]) and r.get("deny_kind") == "terminal", r["reason"][:72])

    A.allow_window("notepad.exe", "Notepad", 555, path=state)
    r = A.check("B", window={"exe": "notepad.exe", "cls": "Notepad",
                             "created": 555}, path=state, brake=NO_BRAKE)
    ok("普通窗不被误拒（粗网不能把正常活儿也毙了）", r["ok"], r["reason"])

    return 0


if __name__ == "__main__":
    if "--worker" in sys.argv:
        # 按位置取，不写死 argv[1] —— --src 可能排在 --worker 前面
        i = sys.argv.index("--worker")
        sys.exit(worker(*sys.argv[i + 1:]))
    sys.exit(main())

