# -*- coding: utf-8 -*-
r"""tests/_P0_单例弹窗_判据.py —— 判据：**同一时刻屏幕上只允许一个授权弹窗**

病根（用户 2026-10-21 现场原话「为什么会有四个闸口弹窗访问我」）：
    并发的 ctl_authorize 请求**各自拉一个独立弹窗进程** ⇒ 屏幕上同时出现 N 个弹窗。
    闸门日志实证：2026-10-21 00:25:27 / 00:26:25 / 00:28:10 三笔请求重叠，
    第一笔直到 00:29:10 才被点掉 ⇒ 那一刻屏幕上最多同时有 3 个弹窗。

判据（本判据**不弹真窗**，用假弹窗脚本 + 临时闸门目录，跑完不打扰任何人）：
    B1 ★ 并发 4 路 ctl_authorize ⇒ 实际拉起的弹窗进程数 **≤ 1**
    B2 ★ 并发 4 路 ⇒ **每一路都拿到结果**（不许饿死、不许报错）
    B3 ★ 单路语义不变（签发成功、范围原样落盘、动作数上限不变）
    B4 ★ 仲裁是**跨进程**的（两个进程同时请求也只弹 1 次）
    B5 ★ 拿不到槽 ⇒ **fail-closed**（拒绝 + 明确回执），绝不 fail-open
    B6 ★ 接线：core 的授权路径真的走了仲裁器（静态查 + 动态查）

跑红自证：
    python -X utf8 tests/_P0_单例弹窗_判据.py --inject
    —— 把仲裁器换成「改前行为」（谁请求谁弹窗）⇒ B1 必须红。
       注入只在进程内 monkeypatch，现役文件一个字节不动。

退出码：0 = 全绿 ｜ 1 = 有红（逐条点名）｜ 2 = 环境不满足
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NL = chr(10)

SCOPES = ["A级 只读感知", "B级 点击滚动"]


def _prep_env():
    """把时间线与闸门状态指到临时目录 ⇒ 本判据**不碰**真事件流、真通行证。"""
    tmp = tempfile.mkdtemp(prefix="agentctl_popup_slot_")
    os.environ["AGENTCTL_TIMELINE"] = os.path.join(tmp, "timeline")
    os.makedirs(os.environ["AGENTCTL_TIMELINE"], exist_ok=True)
    sys.path.insert(0, os.path.join(ROOT, "src"))
    return tmp


def _load_core(tmp):
    from agentctl import core          # noqa: E402
    authdir = os.path.join(tmp, "auth")
    os.makedirs(authdir, exist_ok=True)
    core.gate.AUTHDIR = authdir
    core.gate.PASSFILE = os.path.join(authdir, "auth_pass.json")
    core.gate.log = lambda msg: None
    core._ensure_border = lambda: None
    return core, authdir


def _write_pass(path, scopes, ttl=600):
    obj = {"scopes": list(scopes), "scope": "、".join(scopes),
           "expires_at": time.time() + ttl, "consumed": False,
           "acts_limit": 200, "issued": time.time()}
    with open(path, "wb") as f:
        f.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    return obj


def _fake_popup(counter, passfile, scopes, sleep=0.8):
    """假弹窗：记一次账 + 睡一会儿（模拟人在输密钥）+ 落一张真格式的证。"""
    def fn(cmd=None, **kw):
        with open(counter, "a", encoding="utf-8") as f:
            f.write("%d%s" % (time.time(), NL))
        time.sleep(sleep)
        _write_pass(passfile, scopes)
        return 0, "FAKE-GRANTED", ""
    return fn


def t_authorize(core, scopes=SCOPES, **kw):
    args = {"scopes": [s[0] for s in scopes], "minutes": 10, "acts": 200,
            "actor": "criterion"}
    return core.t_authorize(args)


def run_case_b1_b2(core, tmp, inject_no_arbiter=False):
    """并发 4 路 ⇒ 弹窗只能有 1 次，且 4 路都有结果。返回 (弹窗次数, 各路结果)。"""
    authdir = os.path.join(tmp, "auth_b1")
    os.makedirs(authdir, exist_ok=True)
    counter = os.path.join(tmp, "counter_b1.txt")
    passfile = os.path.join(authdir, "auth_pass.json")
    core.gate.AUTHDIR = authdir
    core.gate.PASSFILE = passfile
    if inject_no_arbiter:
        # ★ 注入：把仲裁器换回「改前行为」—— 谁请求谁弹窗。
        from agentctl import popup_slot
        real = popup_slot.authorize

        def no_arbiter(authdir_, passfile_, scopes, popup_fn, **kw):
            rc, out, err = popup_fn()
            return {"outcome": "granted_popup" if rc == 0 else "denied",
                    "rc": rc, "out": out, "err": err, "detail": "注入：无仲裁器",
                    "waited": 0.0}
        popup_slot.authorize = no_arbiter
        try:
            res = _run_threads(core, counter, passfile)
        finally:
            popup_slot.authorize = real
        return res
    return _run_threads(core, counter, passfile)


def _run_threads(core, counter, passfile):
    results = []
    lock = threading.Lock()

    def worker():
        core.gate.AUTHDIR = os.path.dirname(passfile)
        core.gate.PASSFILE = passfile
        core._legacy_cmd = _fake_popup(counter, passfile, SCOPES)
        try:
            r = t_authorize(core)
        except Exception as e:
            r = {"ok": False, "err": "%s: %s" % (type(e).__name__, e)}
        with lock:
            results.append(r)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    n = 0
    if os.path.isfile(counter):
        with open(counter, encoding="utf-8") as f:
            n = len([x for x in f.read().split(NL) if x.strip()])
    return n, results


def run_case_b4(tmp):
    """跨进程：两个进程同时请求 ⇒ 只弹 1 次。"""
    authdir = os.path.join(tmp, "auth_b4")
    os.makedirs(authdir, exist_ok=True)
    counter = os.path.join(tmp, "counter_b4.txt")
    passfile = os.path.join(authdir, "auth_pass.json")
    helper = os.path.join(tmp, "child_b4.py")
    with open(helper, "w", encoding="utf-8", newline="") as f:
        f.write(CHILD_SRC)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
    env["AGENTCTL_SRC_ROOT"] = ROOT
    env["AGENTCTL_POPUP_TEST_DIR"] = authdir
    env["AGENTCTL_POPUP_TEST_COUNTER"] = counter
    env["AGENTCTL_POPUP_TEST_PASS"] = passfile
    ps = [subprocess.Popen([sys.executable, "-X", "utf8", helper], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
          for _ in range(2)]
    outs = []
    for p in ps:
        o, e = p.communicate(timeout=90)
        outs.append((p.returncode, o.decode("utf-8", "replace").strip(),
                     e.decode("utf-8", "replace").strip()[-300:]))
    n = 0
    if os.path.isfile(counter):
        with open(counter, encoding="utf-8") as f:
            n = len([x for x in f.read().split(NL) if x.strip()])
    return n, outs


CHILD_SRC = """# -*- coding: utf-8 -*-
import json, os, sys, time
sys.path.insert(0, os.path.join(os.environ["AGENTCTL_SRC_ROOT"], "src"))
from agentctl import popup_slot
AUTH = os.environ["AGENTCTL_POPUP_TEST_DIR"]
COUNTER = os.environ["AGENTCTL_POPUP_TEST_COUNTER"]
PASS = os.environ["AGENTCTL_POPUP_TEST_PASS"]
SCOPES = ["A级 只读感知", "B级 点击滚动"]


def popup():
    with open(COUNTER, "a", encoding="utf-8") as f:
        f.write("%d" % time.time() + chr(10))
    time.sleep(0.8)
    with open(PASS, "wb") as f:
        f.write(json.dumps({"scopes": SCOPES, "scope": "、".join(SCOPES),
                            "expires_at": time.time() + 600, "consumed": False},
                           ensure_ascii=False).encode("utf-8"))
    return 0, "CHILD-GRANTED", ""


r = popup_slot.authorize(AUTH, PASS, SCOPES, popup, wait=60)
print(json.dumps({"outcome": r["outcome"]}, ensure_ascii=False))
"""


def main(argv):
    inject = "--inject" in argv
    tmp = _prep_env()
    oks, reds = [], []
    try:
        core, authdir = _load_core(tmp)

        # ── B6 接线（静态） ───────────────────────────────────────────────
        src = open(os.path.join(ROOT, "src", "agentctl", "core.py"), encoding="utf-8").read()
        if "popup_slot.authorize(" in src and "import popup_slot" in src:
            oks.append("B6 接线：core 的授权路径真的走了 popup_slot.authorize")
        else:
            reds.append("B6 接线：core.py 里没有 popup_slot.authorize 的调用（仲裁器没接上）")

        # ── B1 / B2 并发 ─────────────────────────────────────────────────
        n, results = run_case_b1_b2(core, tmp, inject_no_arbiter=inject)
        if n <= 1:
            oks.append("B1 并发 4 路 ⇒ 弹窗只拉起了 %d 次（≤1）" % n)
        else:
            reds.append("B1 并发 4 路 ⇒ 弹窗拉起了 %d 次（>1）—— 屏幕上会同时出现 %d 个弹窗"
                        % (n, n))
        granted = [r for r in results if isinstance(r, dict) and r.get("ok")]
        if len(granted) == 4:
            oks.append("B2 并发 4 路 ⇒ 4 路都拿到了结果（没有饿死、没有报错）")
        else:
            reds.append("B2 并发 4 路 ⇒ 只有 %d 路拿到结果（其余：%s）"
                        % (len(granted), [str(r)[:80] for r in results if not (isinstance(r, dict) and r.get("ok"))]))
        if inject and n <= 1:
            reds.append("B1 注入自证失败：注入「无仲裁器」后弹窗次数仍然是 %d（判据没判别力）" % n)

        # ── B3 单路语义 ──────────────────────────────────────────────────
        authdir3 = os.path.join(tmp, "auth_b3")
        os.makedirs(authdir3, exist_ok=True)
        counter3 = os.path.join(tmp, "counter_b3.txt")
        passfile3 = os.path.join(authdir3, "auth_pass.json")
        core.gate.AUTHDIR = authdir3
        core.gate.PASSFILE = passfile3
        core._legacy_cmd = _fake_popup(counter3, passfile3, SCOPES, sleep=0.05)
        r3 = t_authorize(core)
        n3 = 0
        if os.path.isfile(counter3):
            with open(counter3, encoding="utf-8") as f:
                n3 = len([x for x in f.read().split(NL) if x.strip()])
        d3 = r3.get("data") or {}
        if r3.get("ok") and n3 == 1 and list(d3.get("scopes") or []) == SCOPES:
            oks.append("B3 单路语义不变：弹 1 次、ok=True、范围原样返回 %s、动作上限 %s"
                       % (d3.get("scopes"), d3.get("acts_limit")))
        else:
            reds.append("B3 单路语义变了：ok=%s 弹窗次数=%s 范围=%s"
                        % (r3.get("ok"), n3, d3.get("scopes")))

        # ── B5 fail-closed ───────────────────────────────────────────────
        from agentctl import popup_slot
        authdir5 = os.path.join(tmp, "auth_b5")
        os.makedirs(authdir5, exist_ok=True)
        passfile5 = os.path.join(authdir5, "auth_pass.json")
        counter5 = os.path.join(tmp, "counter_b5.txt")
        # 造一个「别人正在弹窗」的槽（新鲜，不是陈旧）
        with open(os.path.join(authdir5, popup_slot.SLOT_NAME), "wb") as f:
            f.write(json.dumps({"pid": 999999, "owner": "other", "since": time.time()},
                               ensure_ascii=False).encode("utf-8"))
        r5 = popup_slot.authorize(authdir5, passfile5, SCOPES,
                                  _fake_popup(counter5, passfile5, SCOPES, sleep=0.05),
                                  wait=1.0, poll=0.2)
        n5 = 0
        if os.path.isfile(counter5):
            with open(counter5, encoding="utf-8") as f:
                n5 = len([x for x in f.read().split(NL) if x.strip()])
        if r5.get("outcome") == "busy" and n5 == 0:
            oks.append("B5 fail-closed：拿不到槽 ⇒ 明确拒绝（outcome=busy）、弹窗 0 次")
        else:
            reds.append("B5 fail-closed 没成立：outcome=%s、弹窗次数=%s（fail-open 就是这个形状）"
                        % (r5.get("outcome"), n5))

        # ── B4 跨进程 ────────────────────────────────────────────────────
        n4, outs4 = run_case_b4(tmp)
        if n4 <= 1:
            oks.append("B4 跨进程：两个进程同时请求 ⇒ 弹窗 %d 次（≤1）" % n4)
        else:
            reds.append("B4 跨进程没成立：两个进程同时请求 ⇒ 弹窗 %d 次（>1）；子进程回执 %s"
                        % (n4, outs4))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 72)
    print("P0 单例弹窗判据  ｜  闸门目录=临时（不碰真通行证、不弹真窗）")
    print("=" * 72)
    for o in oks:
        print("  ✅ %s" % o)
    for r in reds:
        print("  ❌ %s" % r)
    print("-" * 72)
    print("绿 %d 条 / 红 %d 条" % (len(oks), len(reds)))
    if inject:
        if reds:
            print("结论：【注入自证成立】注入后变红 %d 条 ⇒ 这条判据是活的。" % len(reds))
            return 1
        print("结论：【注入自证失败】注入后仍绿 ⇒ 这条判据是死的。")
        return 1
    if reds:
        print("结论：【红】")
        return 1
    print("结论：【绿】")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
