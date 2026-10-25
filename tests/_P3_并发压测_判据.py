# -*- coding: utf-8 -*-
r"""P3 判据 · 并发压测（工单 W1 · 2026-10-25）—— 用离线、可重复的压测把既有并发承诺钉住

★ 只读产品代码，不改产品（派单红线）。四个场景全部离线、全部落在 %TEMP%：

    A · 40 进程并发签发：40 个子进程指到**同一个** gate 目录（AGENTCTL_AUTHDIR→临时目录），
        各调一次 authstate.grant_task —— 成功签发数必须 = 40、无异常，
        最终 authstate.json 是合法 JSON 且 task 在。
    B · 8 线程并发 acquire_lease（gate._exclusive = msvcrt.locking 跨进程+跨线程互斥）：
        B1 同一 actor（先签一次让 holder=自己）→ 8/8 全成 —— 同人重入换新 id 是设计允许；
        B2 8 个互异 actor → 恰 1 个成功，其余 7 个 GateDenied(code=CODE_LEASE_HELD=10)。
    C · 8 线程并发写 eid 旁表（core._eid_sessions_put，各自唯一 capture_id）
        → 8/8 返回 True；读回 8 条全在、文件是合法 JSON、无 .tmp 残留。
    D · 8 线程并发 gate.set_brake / clear_brake 各 100 次/线程（共 1600 op）
        → 最终旗子状态 == 最后一次**真实改变状态**的操作；无 .tmp 残留。
        （派单门槛就这两条；操作失败计数是**输出数据**不是门槛——概率性发现不做硬门槛，
        免得判据自己抖动。本单实测确实压出了 set_brake 的一个低概率竞态，见报告 §场景 D。）

★ D 的「最后操作」口径（先讲清楚，避免测试替产品说话）：
    set_brake 成功 = 置位；clear_brake 返回「急停本来就未拉下」是**无状态变化的 no-op**
    （它的 exists 检查与返回之间可能有并发 set 落地），计算期望时跳过 no-op，
    取**最后一条真实改变状态的操作**（set ok=True ⇒ 在；clear ok=True 且非 no-op ⇒ 不在）。

★ --inject（注入自证，判据必须有牙）：把 B2 的期望成功数故意从 1 改成 8
    ⇒ 场景 B 必须红 ⇒ exit 1；没红 ⇒ exit 2（判据没牙，比红更糟）。

★ 跑法（约定）：
    正跑        python -X utf8 tests\_P3_并发压测_判据.py     ⇒ exit 0（全过）/ 1（任一失败）
    注入必红    … --inject                                    ⇒ exit 1
    未知参数    … --未知参数                                   ⇒ exit 0（静默忽略，不许假红）

★ 本判据**不碰真机状态**：AGENTCTL_AUTHDIR / AGENTCTL_LEGACY_HOME / AGENTCTL_APP_DIR /
    AGENTCTL_BRAKE_FLAG / AGENTCTL_TIMELINE / AGENTCTL_EID_SESSIONS 全指到 %TEMP%。
    AGENTCTL_LEGACY_HOME 指一个只放了**仓内 gate_lock.py 副本**的状态目录（authstate._gate_lock
    的硬要求；不放 gate.py ⇒ 闸门按 gate.py 的「状态目录」口径豁免体检）。
    子进程 PYTHONDONTWRITEBYTECODE=1 + 本进程 sys.dont_write_bytecode=True ⇒ 仓内零写入；
    跑完即删临时根。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

sys.dont_write_bytecode = True          # ★ 先于 import：仓内零写入（连 .pyc 都不落）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "src")

# ── 隔离环境：必须在 import agentctl **之前**设好（路径是模块级算死的）──
TMP = tempfile.mkdtemp(prefix="W1stress_")


def _fresh(*parts):
    d = os.path.join(TMP, *parts)
    os.makedirs(d, exist_ok=True)
    return d


HOME_DIR = _fresh("legacy_home")        # 只放 gate_lock.py 副本（不放 gate.py ⇒ 体检豁免）
shutil.copyfile(os.path.join(REPO, "legacy_tools", "gate_lock.py"),
                os.path.join(HOME_DIR, "gate_lock.py"))
APP_DIR = _fresh("app")
os.environ["AGENTCTL_LEGACY_HOME"] = HOME_DIR
os.environ["AGENTCTL_APP_DIR"] = APP_DIR
os.environ["AGENTCTL_BRAKE_FLAG"] = os.path.join(APP_DIR, "_急停.flag")
os.environ["AGENTCTL_TIMELINE"] = _fresh("timeline")
AUTHDIR = _fresh("auth")                # 父进程（场景 B/C/D）的账本目录
os.environ["AGENTCTL_AUTHDIR"] = AUTHDIR
EID_DB = os.path.join(TMP, "eid_sessions.json")
os.environ["AGENTCTL_EID_SESSIONS"] = EID_DB

sys.path.insert(0, SRC)
from agentctl import authstate          # noqa: E402
from agentctl import core               # noqa: E402（场景 C 的 _eid_sessions_put；也捎带验证 import 无副作用）
from agentctl import gate               # noqa: E402

OK, BAD = [], []                        # 判词账本（与仓内其它判据同一习惯）


def section(t):
    print("\n" + "=" * 74 + "\n" + t + "\n" + "=" * 74, flush=True)


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("绿" if cond else "红", name,
                           ("  ← %s" % detail) if detail else ""), flush=True)
    return bool(cond)


def run_threads(n, fn):
    """n 个线程各跑一次 fn(i)；返回 (按序结果或异常对象, 耗时秒)。线程内异常绝不静默。"""
    out = [None] * n
    errs = [None] * n

    def body(i):
        try:
            out[i] = fn(i)
        except BaseException as e:      # noqa: BLE001 —— 压测要把异常当数据收
            errs[i] = "%s: %s" % (type(e).__name__, e)

    t0 = time.perf_counter()
    ts = [threading.Thread(target=body, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=120)
    return out, errs, time.perf_counter() - t0


# ════════════════════════ 场景 A · 40 进程并发签发 ════════════════════════
CHILD_CODE = r'''
import json, os, sys, time, traceback
res = {"ok": False}
t0 = time.time()
try:
    sys.path.insert(0, os.environ["W1_SRC"])
    from agentctl import authstate
    st = authstate.grant_task(scopes=["A"], path=os.environ["W1_STATE"])
    task = (st or {}).get("task") or {}
    res = {"ok": bool(task.get("nonce")), "nonce": str(task.get("nonce") or "")}
except BaseException as e:
    res = {"ok": False, "err": "%s: %s" % (type(e).__name__, e),
           "tb": traceback.format_exc(limit=4)}
res["ms"] = int((time.time() - t0) * 1000)
with open(os.environ["W1_OUT"], "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False)
'''


def scenario_a(n=40):
    section("场景 A · %d 进程并发签发（同一 gate 目录）" % n)
    a_auth = _fresh("A_auth")
    a_home = _fresh("A_home")
    shutil.copyfile(os.path.join(REPO, "legacy_tools", "gate_lock.py"),
                    os.path.join(a_home, "gate_lock.py"))
    state = os.path.join(a_auth, "authstate.json")
    base = dict(os.environ)
    base.update({
        "AGENTCTL_AUTHDIR": a_auth,
        "AGENTCTL_LEGACY_HOME": a_home,
        "AGENTCTL_APP_DIR": _fresh("A_app"),
        "AGENTCTL_BRAKE_FLAG": os.path.join(TMP, "A_app", "_急停.flag"),
        "AGENTCTL_TIMELINE": _fresh("A_timeline"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    t0 = time.perf_counter()
    procs = []
    for i in range(n):
        env = dict(base)
        env["W1_SRC"] = SRC
        env["W1_STATE"] = state
        env["W1_OUT"] = os.path.join(TMP, "A_out_%02d.json" % i)
        procs.append(subprocess.Popen(
            [sys.executable, "-X", "utf8", "-B", "-c", CHILD_CODE],
            env=env, cwd=tempfile.gettempdir(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for p in procs:
        p.wait(timeout=180)
    elapsed = time.perf_counter() - t0

    oks, fails = 0, []
    for i in range(n):
        out = os.path.join(TMP, "A_out_%02d.json" % i)
        try:
            with open(out, encoding="utf-8") as f:
                r = json.load(f)
        except Exception as e:
            fails.append("#%d 结果文件缺失/坏：%s" % (i, e))
            continue
        if r.get("ok"):
            oks += 1
        else:
            fails.append("#%d %s%s" % (i, r.get("err", "?"),
                                       ("｜%s" % r["tb"].splitlines()[-1]) if r.get("tb") else ""))
    print("  耗时 %.2fs · 成功 %d/%d · 失败 %d" % (elapsed, oks, n, len(fails)), flush=True)
    for s in fails[:3]:
        print("    失败样例：%s" % s, flush=True)
    check("A1 · 成功签发数 = %d" % n, oks == n, "实得 %d" % oks)
    check("A2 · 无子进程异常", not fails, "；".join(fails[:2]))

    # 最终账本：合法 JSON、task 在、未过期
    st_err, task = "", {}
    try:
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        task = st.get("task") or {}
        if not task.get("nonce"):
            st_err = "task 缺失"
        elif float(task.get("exp", 0)) <= time.time():
            st_err = "task 已过期"
        elif "A" not in (task.get("scopes") or []):
            st_err = "scopes 不含 A"
    except Exception as e:
        st_err = "%s: %s" % (type(e).__name__, e)
    check("A3 · 最终 authstate.json 合法且 task 在", not st_err, st_err)
    residue = glob.glob(os.path.join(a_auth, "*.tmp*"))
    check("A4 · 账本目录无 .tmp 残留", not residue, "；".join(residue[:3]))
    return {"elapsed": elapsed, "ok": oks, "fail": len(fails)}


# ════════════════════════ 场景 B · 8 线程并发 acquire_lease ════════════════════════
def _b2(i):
    """互异 actor 抢租约；GateDenied 当结果收（code=CODE_LEASE_HELD 才算按预期拒绝）。"""
    try:
        return gate.acquire_lease("W1_other_%d" % i, ["B"], ttl_sec=600)
    except gate.GateDenied as e:
        return e


def scenario_b(expect_other_ok=1):
    section("场景 B · 8 线程并发 acquire_lease（期望：同 actor 8/8；异 actor 恰 %d 成功）"
            % expect_other_ok)
    # B1 同一 actor：先签一次让 holder=自己，再 8 线程并发重入
    gate.acquire_lease("W1_same", ["B"], ttl_sec=600)

    def _b1(i):
        try:
            return gate.acquire_lease("W1_same", ["B"], ttl_sec=600)
        except gate.GateDenied as e:        # 拒绝也是数据：带 code 收进结果
            return e

    res, errs, el = run_threads(8, _b1)
    ok1 = sum(1 for r in res if isinstance(r, dict) and r.get("lease_id"))
    hard1 = [r for r in res if isinstance(r, gate.GateDenied)]
    print("  B1 耗时 %.2fs · 成功 %d/8 · GateDenied %d · 线程异常 %d"
          % (el, ok1, len(hard1), sum(1 for e in errs if e)), flush=True)
    if any(errs):
        print("    异常样例：%s" % next(e for e in errs if e), flush=True)
    check("B1 · 同 actor 并发重入 8/8 全成", ok1 == 8 and not hard1 and not any(errs),
          "成功 %d、GateDenied %d" % (ok1, len(hard1)))

    # B2 互异 actor：先释放当前租约（id 已被 B1 的重入顶替，传空 = 释放现行那张），再抢
    gate.release_lease("")
    res2, errs2, el2 = run_threads(8, lambda i: _b2(i))
    ok2 = [r for r in res2 if isinstance(r, dict) and r.get("lease_id")]
    denied2 = [r for r in res2 if isinstance(r, gate.GateDenied)]
    other = [r for r in res2 if r is not None and not isinstance(r, (dict, gate.GateDenied))]
    print("  B2 耗时 %.2fs · 成功 %d · CODE_LEASE_HELD 拒绝 %d · 其它 %d · 线程异常 %d"
          % (el2, len(ok2), len(denied2), len(other), sum(1 for e in errs2 if e)), flush=True)
    bad_code = [repr(getattr(r, "code", None)) for r in denied2 if r.code != gate.CODE_LEASE_HELD]
    if any(errs2):
        print("    异常样例：%s" % next(e for e in errs2 if e), flush=True)
    check("B2 · 异 actor 恰 %d 个成功、其余 GateDenied(CODE_LEASE_HELD=%d)"
          % (expect_other_ok, gate.CODE_LEASE_HELD),
          len(ok2) == expect_other_ok and not bad_code and not any(errs2),
          "成功 %d、拒绝 %d（错误码异常 %s）" % (len(ok2), len(denied2), bad_code or "无"))
    if ok2:
        gate.release_lease("")                      # 清场：别把活租约带进场景 D

    lerr = ""
    try:
        with open(gate.LEASE_DB, encoding="utf-8") as f:
            json.load(f)
    except Exception as e:
        lerr = "%s: %s" % (type(e).__name__, e)
    check("B3 · lease.json 全程合法 JSON", not lerr, lerr)
    return {"elapsed": el + el2, "ok": ok1 + len(ok2), "fail": len(denied2)}


# ════════════════════════ 场景 C · 8 线程并发写 eid 旁表 ════════════════════════
def _eid_node(i, j):
    return {"eid": "e%d" % j, "type": "Button", "name": "按钮%d_%d" % (i, j),
            "rect": [i, j, i + 10, j + 10], "center": [i + 5, j + 5], "enabled": True}


def scenario_c(n=8):
    section("场景 C · %d 线程并发写 eid 旁表（各自唯一 capture_id）" % n)
    res, errs, el = run_threads(
        n, lambda i: core._eid_sessions_put("W1_cap_%02d" % i, 1000 + i,
                                            [_eid_node(i, 0), _eid_node(i, 1)],
                                            {"tool": "probe"}))
    oks = sum(1 for r in res if r is True)
    print("  耗时 %.2fs · put 成功 %d/%d · 线程异常 %d"
          % (el, oks, n, sum(1 for e in errs if e)), flush=True)
    if any(errs):
        print("    异常样例：%s" % next(e for e in errs if e), flush=True)
    check("C1 · %d 次 _eid_sessions_put 全部返回 True" % n, oks == n and not any(errs),
          "成功 %d" % oks)

    parse_err, missing = "", []
    try:
        with open(EID_DB, encoding="utf-8") as f:
            db = json.load(f)
        now = time.time()
        for i in range(n):
            ent = db.get("W1_cap_%02d" % i)
            if not ent or float(ent.get("expires_at", 0)) <= now:
                missing.append("W1_cap_%02d" % i)
            elif len(ent.get("eids") or {}) != 2:
                missing.append("W1_cap_%02d(eids=%d)" % (i, len(ent.get("eids") or {})))
    except Exception as e:
        parse_err = "%s: %s" % (type(e).__name__, e)
    check("C2 · 读回 %d 条全在且文件是合法 JSON" % n,
          not parse_err and not missing, parse_err or ("缺：%s" % missing if missing else ""))
    residue = glob.glob(EID_DB + ".tmp*")
    check("C3 · 旁表无 .tmp 残留", not residue, "；".join(residue[:3]))
    return {"elapsed": el, "ok": oks, "fail": n - oks}


# ════════════════════════ 场景 D · 8 线程并发 brake set/release ════════════════════════
def scenario_d(threads=8, rounds=100):
    section("场景 D · %d 线程并发 brake set/release 各 %d 次（共 %d op）"
            % (threads, rounds, threads * rounds * 2))
    ledger = []
    lk = threading.Lock()

    def worker(n):
        for _ in range(rounds):
            ok, msg = gate.set_brake("W1 并发压测")
            with lk:
                ledger.append((time.perf_counter(), "set", ok, msg))
            ok, msg = gate.clear_brake()
            with lk:
                ledger.append((time.perf_counter(), "clear", ok, msg))

    t0 = time.perf_counter()
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=300)
    elapsed = time.perf_counter() - t0

    n_set = sum(1 for e in ledger if e[1] == "set")
    n_clear = sum(1 for e in ledger if e[1] == "clear")
    bad_ops = [(k, m) for _, k, ok, m in ledger if not ok]
    print("  耗时 %.2fs · set %d · clear %d · 操作失败 %d" % (elapsed, n_set, n_clear, len(bad_ops)),
          flush=True)
    for m in bad_ops[:3]:
        print("    失败样例：%s %s" % m, flush=True)
    # D-观察：操作失败**不是**派单门槛（门槛=最终状态一致 + 无残留），但当数据如实报。
    # ★ 概率性发现不做硬门槛（否则判据本身抖动）；机理与复现写进并发压测报告。
    set_fail = sum(1 for k, _ in bad_ops if k == "set")
    clear_fail = len(bad_ops) - set_fail
    print("  · 数据：set 成功 %d / 失败 %d；clear 成功 %d / 失败 %d"
          % (n_set - set_fail, set_fail, n_clear - clear_fail, clear_fail), flush=True)
    if bad_ops:
        print("  【发现-未修】%d 次 set_brake/clear_brake 重试耗尽仍失败（不许改产品代码，"
              "机理与复现见 docs\\并发压测报告_20261025.md §场景 D）" % len(bad_ops), flush=True)

    # 期望状态 = 最后一条**真实改变状态**的操作（no-op 的 clear 跳过，见文件头口径）
    expect = None
    who = ""
    for ts_, kind, ok, msg in ledger:
        if not ok:
            continue
        if kind == "set":
            expect, who = True, "set@%.4f" % ts_
        elif "本来就未拉下" in msg:
            continue                                    # no-op：没动状态
        else:
            expect, who = False, "clear@%.4f" % ts_
    actual = gate.brake_engaged()
    check("D1 · 最终旗子状态 == 最后一次真实改变状态的操作",
          expect is not None and actual == expect,
          "期望=%s 实得=%s（%s）" % (expect, actual, who))
    residue = glob.glob(os.path.join(os.path.dirname(gate.BRAKE_FLAG), "*.tmp*"))
    check("D2 · 急停目录无 .tmp 残留", not residue, "；".join(residue[:3]))
    check("D3 · 操作账本完整（%d 条）" % (threads * rounds * 2), len(ledger) == threads * rounds * 2,
          "实得 %d" % len(ledger))
    return {"elapsed": elapsed, "ok": len(ledger) - len(bad_ops), "fail": len(bad_ops)}


# ════════════════════════ 主流程 ════════════════════════
def do_normal():
    print("== P3 判据 · 并发压测（W1）==  临时根：%s" % TMP)
    print("[隔离] AUTHDIR/LEGACY_HOME/APP_DIR/BRAKE_FLAG/TIMELINE/EID_SESSIONS 全在 %%TEMP%")
    stats = {"A": scenario_a(), "B": scenario_b(expect_other_ok=1),
             "C": scenario_c(), "D": scenario_d()}
    section("汇总")
    for k, s in stats.items():
        print("  场景 %s：耗时 %.2fs · 成功 %d · 失败/拒绝 %d" % (k, s["elapsed"], s["ok"], s["fail"]))
    print()
    print("=" * 74)
    if BAD:
        print("【红】失败 %d 项：%s" % (len(BAD), BAD))
        return 1
    print("【绿】A/B/C/D 四场景全部达标（%d 条判词）⇒ 并发承诺钉住" % len(OK))
    return 0


def do_inject():
    """注入自证：只跑场景 B，把 B2 期望成功数故意 1→8 ⇒ 必须红。"""
    print("== P3 判据 · 并发压测（W1）—— 注入自证 ==")
    print("【注入】把 B2 的期望成功数故意从 1 改成 8（错的）⇒ 场景 B 必须红；其余场景跳过")
    scenario_b(expect_other_ok=8)
    print()
    print("=" * 74)
    if any(r.startswith("B2") for r in BAD):
        print("【红】注入被逮到（B2 转红）⇒ 判据有牙 ⇒ exit=1")
        return 1
    print("【✗】期望值改错了居然没红 ⇒ 判据没牙 ⇒ exit=2")
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红，exit=1）")
    args, _unknown = ap.parse_known_args()      # ★ 未知参数静默忽略（不许假红）
    try:
        return do_inject() if args.inject else do_normal()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)  # 仓内/系统不留产物


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1)
