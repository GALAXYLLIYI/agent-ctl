# -*- coding: utf-8 -*-
r"""P1 判据：gate.revoke_lease —— **第三方强制吊销单张租约**（人侧的「只掐一个」）

================================ 为什么要这条 ================================
改前只有两条路能让一个跑飞了的窗停下来：
    ① 等 ttl_sec 到期（默认 600s）；
    ② 拉总闸（急停）⇒ **所有窗一起停**。
缺的是中间档：「**只掐掉那一个，别人照跑**」。

================================ 判据冻结了什么 ================================
七条（派单要求的五条 + 两条本单自带的护栏），期望值全部写死在本文件里
——**不每轮从实现现算**（否则 f(x)==f(x) 恒真，等于没测）：
    1 · 吊销后 require_lease(actor) **立刻失败**（不等 TTL 到期）
    2 · 吊销**只**影响目标租约：另一个人重新拿的租约照旧可用；
        再拿**已吊销的旧 id** 去吊一次，**不许连坐**现役那张
    3 · 幂等：连吊两次不报错，第二次 revoked=False / already=True；
        吊一张根本不存在的 id 同样是「已是吊销态」而不是异常
    4 · 空 lease_id 且**未**传 confirm_all ⇒ **拒绝执行**（防手滑），
        且**什么都不许动**（现役租约必须照旧可用）
    5 · 急停拉下 ⇒ 租约被**自动全部吊销**，且留痕里 by="brake"
    6 · ★**权限面**：模型**没有**吊销别人租约的入口
        （契约 action 枚举里没有 revoke / 真调 t_lease --action revoke 被拒 /
          全仓只有闸门与面板引它）
    7 · 留痕：每次真吊销都写**闸门日志** + **事件**（kind 沿用现成的 lease_release）
    8 · 吊销**不许被 renew 复活** —— 否则跑飞的那个窗顺手续一下期就又活了

================================ 能跑红 ================================
    ① 改前基线（没有 revoke_lease）：多数条数报红
       python -X utf8 tests/_P1_租约吊销判据.py
    ② 坏实现（造坏闸门，见 work/_P1_造坏实现.py）：
       set P1_GATE=<坏闸门目录>/gate.py
       python -X utf8 tests/_P1_租约吊销判据.py
       · 坏A「吊销后仍放行」  ⇒ 实测红 7 条（含 1 不拦 / 5 急停不联动）
       · 坏B「连坐别人租约」  ⇒ 实测红 4 条（含 2 ★连坐 / 4 空 id 没确认也照吊）
    ③ fail-closed：P1_GATE 指到一份**语法坏掉**的 gate.py ⇒ 判据自身崩了也记红、退出码 1

★ 本判据**不碰真机状态**：全部走临时目录（env 覆盖），跑完即删。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# ★ 红验证口：把它指到「坏闸门」那份 gate.py 上，整条判据就按那个实现判绿红
GATE_PATH = os.environ.get("P1_GATE") or os.path.join(REPO, "src", "agentctl", "gate.py")
CONTRACT = os.path.join(REPO, "contract", "tools.json")
SRC = os.path.join(REPO, "src", "agentctl")

# ── 隔离环境：必须在 import 闸门**之前**设好（闸门是模块级算路径的）──
TMP = tempfile.mkdtemp(prefix="P1revoke_")
STATE = os.path.join(TMP, "state")      # ← AGENTCTL_LEGACY_HOME（当**状态**目录用）
APP = os.path.join(TMP, "app")
os.makedirs(STATE, exist_ok=True)
os.makedirs(APP, exist_ok=True)
os.environ["AGENTCTL_LEGACY_HOME"] = STATE   # 里面**不放 gate.py** ⇒ 闸门豁免体检（见 gate.py 注释）
os.environ["AGENTCTL_APP_DIR"] = APP
os.environ["AGENTCTL_BRAKE_FLAG"] = os.path.join(APP, "_急停.flag")
os.environ["AGENTCTL_TIMELINE"] = os.path.join(TMP, "timeline")

OK, BAD, SKIP = [], [], []


def section(t):
    print("\n" + "=" * 74 + "\n" + t + "\n" + "=" * 74, flush=True)


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("绿" if cond else "红", name,
                           ("  ← %s" % detail) if detail else ""), flush=True)
    return bool(cond)


def attempt(name, fn):
    """跑一条判据；**抛异常一律记红**（不许因崩了而没结论）。"""
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, "抛异常 %s: %s" % (type(e).__name__, e)
    return check(name, ok, detail)


def load_gate(path):
    """按文件路径单独装一份闸门（能指到别处 ⇒ 坏实现红验证）。"""
    spec = importlib.util.spec_from_file_location("p1_gate_under_test", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def denied(fn, *a, **kw):
    """返回 (是否按预期被 GateDenied 拒掉, 说明)。"""
    try:
        fn(*a, **kw)
    except Exception as e:
        return type(e).__name__ == "GateDenied", "%s: %s" % (type(e).__name__, e)
    return False, "居然放行了（没抛 GateDenied）"


def allowed(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    return True, "放行"


def fresh(gate):
    """每条判据前把租约库与急停文件清空。

    ★ 为什么必须清：改前基线里 c1 留下的活租约会**顶死** c3 的 acquire，
      于是 c3 报的是「租约被窗A 持有」—— 那是**串味**（上一条的残留），
      不是本条的缺陷。红基线必须一条一条独立，否则「哪条坏了」会被淹没。
    """
    for p in (gate.LEASE_DB, gate.BRAKE_FLAG):
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError as e:
            print("      ⚠ 清 %s 失败：%s" % (p, e), flush=True)


def main():
    section("P1 · 第三方强制吊销单张租约（revoke_lease）判据")
    print("  闸门实现 = %s" % GATE_PATH, flush=True)
    print("  隔离状态 = %s" % STATE, flush=True)

    if not os.path.isfile(GATE_PATH):
        check("0 · 被测闸门存在（%s）" % GATE_PATH, False, "文件不在，验不了")
        return
    gate = load_gate(GATE_PATH)
    print("  AUTHDIR  = %s" % gate.AUTHDIR, flush=True)
    print("  BRAKE    = %s" % gate.BRAKE_FLAG, flush=True)

    # ══ 1 · 吊销后立刻拦 ══════════════════════════════════════════════
    def c1():
        fresh(gate)
        L = gate.acquire_lease("窗A", ["B"], 600)
        ok0, d0 = allowed(gate.require_lease, "窗A")          # 对照：吊销前必须能过
        if not ok0:
            return False, "前置就废了：吊销前 require_lease 就被拒（%s）" % d0
        r = gate.revoke_lease(lease_id=L["lease_id"], by="panel", reason="跑飞了，手动掐掉")
        if not (r.get("ok") and r.get("revoked") is True and r.get("already") is False):
            return False, "返回值不对：%s" % r
        ok1, d1 = denied(gate.require_lease, "窗A")
        st = gate.lease_status()
        if not ok1:
            return False, "吊销后 require_lease 仍放行（active=%s）" % st.get("active")
        if st.get("active"):
            return False, "吊销后 lease_status 还报 active（不该等 600s TTL）"
        return True, "立刻拒：%s" % d1

    attempt("1 · 吊销后 require_lease **立刻失败**（不等 TTL 到期）", c1)

    # ══ 2 · 只影响目标租约 ════════════════════════════════════════════
    def c2():
        fresh(gate)
        LA = gate.acquire_lease("窗A", ["B"], 600)
        gate.revoke_lease(lease_id=LA["lease_id"], by="panel", reason="只掐这一个")
        LB = gate.acquire_lease("窗B", ["B", "C"], 600)      # 另一个人重新拿租约
        ok, d = allowed(gate.require_lease, "窗B")            # 必须照旧可用
        if not ok:
            return False, "连坐了：窗B 的新租约也被拒（%s）" % d
        st = gate.lease_status()
        if st.get("lease_id") != LB["lease_id"] or not st.get("active"):
            return False, "现役租约被换掉/失效：%s" % st
        # ★ 拿**已吊销的旧 id** 再吊一次：必须不许连坐窗B
        gate.revoke_lease(lease_id=LA["lease_id"], by="panel", reason="旧 id 二次吊销")
        ok2, d2 = allowed(gate.require_lease, "窗B")
        if not ok2:
            return False, "★连坐：用已吊销的旧 id 去吊，把窗B 的现役租约也掐了（%s）" % d2
        st2 = gate.lease_status()
        if st2.get("lease_id") != LB["lease_id"]:
            return False, "现役租约记录被旧 id 覆盖：%s" % st2.get("lease_id")
        return True, "窗B 照旧可用；旧 id 二次吊销是空操作"

    attempt("2 · 吊销**只**影响目标租约（别人照跑，旧 id 二次吊销不连坐）", c2)

    # ══ 3 · 幂等 ══════════════════════════════════════════════════════
    def c3():
        fresh(gate)
        L = gate.acquire_lease("窗C", ["C"], 600)
        r1 = gate.revoke_lease(lease_id=L["lease_id"], by="panel", reason="第一次")
        if not (r1.get("ok") and r1.get("revoked") is True):
            return False, "第一次就没吊成：%s" % r1
        r2 = gate.revoke_lease(lease_id=L["lease_id"], by="panel", reason="第二次")
        if not r2.get("ok"):
            return False, "第二次报错/不 ok：%s" % r2
        if r2.get("revoked") is not False or r2.get("already") is not True:
            return False, "第二次必须报「已是吊销态」（revoked=False, already=True）：%s" % r2
        r3 = gate.revoke_lease(lease_id="这张压根不存在", by="panel", reason="不存在的 id")
        if not r3.get("ok") or r3.get("already") is not True or r3.get("revoked") is not False:
            return False, "吊一张不存在的租约必须也是「已是吊销态」而不是异常：%s" % r3
        return True, "连吊三次（含不存在的 id）都不报错，第二次起 already=True"

    attempt("3 · 幂等：连吊两次不报错，第二次返回「已是吊销态」", c3)

    # ══ 4 · 空 lease_id 必须显式确认 ═══════════════════════════════════
    def c4():
        fresh(gate)
        gate.acquire_lease("窗D", ["B", "D"], 600)
        ok0, _ = allowed(gate.require_lease, "窗D")          # 前置：这张是真的活着
        if not ok0:
            return False, "前置就废了"
        r = gate.revoke_lease(by="panel", reason="手滑了，空 id")     # ← 没传 confirm_all
        if r.get("ok") is not False or r.get("revoked") is not False:
            return False, "空 id 且未传 confirm_all 必须拒绝执行：%s" % r
        if not (r.get("why") or r.get("need")):
            return False, "拒绝时必须说清为什么（why/need）：%s" % r
        ok, d = allowed(gate.require_lease, "窗D")            # 拒绝 ⇒ 什么都不能动
        if not ok:
            return False, "拒绝执行却把现役租约也掐了（连坐）：%s" % d
        return True, "拒绝执行且现役租约照旧可用（why=%s）" % (r.get("why") or "")[:40]

    attempt("4 · 空 lease_id 且未传 confirm_all ⇒ 拒绝执行（防手滑，且不动任何东西）", c4)

    # ══ 5 · 急停联动：全部吊销 ═════════════════════════════════════════
    def c5():
        fresh(gate)
        gate.acquire_lease("窗E", ["B"], 600)
        ok, why = gate.set_brake("P1 判据：拉急停")
        try:
            if not ok:
                return False, "急停自己就失败了：%s" % why
            st = gate.lease_status()
            print("      急停后 lease_status = %s" % json.dumps(st, ensure_ascii=False), flush=True)
            if st.get("active"):
                return False, "急停拉下了，租约却还活着（active=True）"
            okd, dd = denied(gate.require_lease, "窗E")
            if not okd:
                return False, "急停后 require_lease 仍放行：%s" % dd
            by = ((st.get("revoked") or {}).get("by"))
            if by != "brake":
                return False, "留痕里的 by 必须是 brake（实测 %r）" % (by,)
            return True, "急停 ⇒ 租约自动全吊，by=brake"
        finally:
            gate.clear_brake()

    attempt("5 · 急停拉下 ⇒ 全部租约被自动吊销（lease_status 里一个都不剩，by=brake）", c5)

    # ══ 6 · ★权限面：模型不许有吊销别人租约的入口 ═════════════════════
    def c6():
        bad = []
        with open(CONTRACT, encoding="utf-8") as f:
            c = json.load(f)
        hit = False
        for t in c.get("tools", []):
            if t.get("name") == "ctl_lease":
                hit = True
                enum = ((t.get("input_schema") or {}).get("properties") or {}
                        ).get("action", {}).get("enum") or []
                if "revoke" in enum:
                    bad.append("契约 ctl_lease 的 action 枚举里混进了 revoke：%s" % enum)
                break
        if not hit:
            bad.append("契约里找不到 ctl_lease 条目（判据失效，请核契约结构）")
        # 全仓引用面：除了闸门自己，只有面板（人点）与急停路径可以引
        allowed_files = {"gate.py", "widget.py"}
        for fn in sorted(os.listdir(SRC)):
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(SRC, fn), encoding="utf-8", errors="replace") as f:
                src = f.read()
            if "revoke_lease" in src and fn not in allowed_files:
                bad.append("%s 里出现了 revoke_lease（模型可达的面上不许有）" % fn)
        # 行为上再钉一道：真去调那个「模型能碰到的入口」，必须被拒
        try:
            if os.path.join(REPO, "src") not in sys.path:
                sys.path.insert(0, os.path.join(REPO, "src"))
            from agentctl import core
            r = core.t_lease({"action": "revoke", "actor": "DSH", "lease_id": "随便"})
            if r.get("ok") or int(r.get("code") or 0) == 0:
                bad.append("t_lease --action revoke 居然成功了：%s" % r)
        except Exception as e:
            bad.append("验不了 t_lease（导入/调用抛异常）：%s: %s" % (type(e).__name__, e))
        return (not bad), "；".join(bad) if bad else "契约枚举/引用面/真调全被拒"

    attempt("6 · ★模型没有吊销别人租约的入口（契约枚举无 revoke / 无工具 / 真调被拒）", c6)

    # ══ 7 · 留痕：闸门日志 + 事件 ══════════════════════════════════════
    def c7():
        fresh(gate)
        L = gate.acquire_lease("窗F", ["B"], 600)
        r = gate.revoke_lease(lease_id=L["lease_id"], by="panel", reason="留痕判据")
        if not r.get("revoked"):
            return False, "前置就废了：%s" % r
        bad = []
        logp = gate.LOG
        txt = ""
        if logp and os.path.exists(logp):
            with open(logp, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        if "吊销" not in txt:
            bad.append("闸门日志里没有「吊销」字样（%s）" % logp)
        if L["lease_id"] not in txt:
            bad.append("闸门日志里没有这张租约的 id")
        tdir = os.environ["AGENTCTL_TIMELINE"]
        rows = []
        if os.path.isdir(tdir):
            for fn in sorted(os.listdir(tdir)):
                if not fn.startswith("timeline-"):
                    continue
                with open(os.path.join(tdir, fn), encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                        except Exception:
                            continue
                        body = e.get("body") if isinstance(e.get("body"), dict) else e
                        rows.append((body.get("kind"), body.get("payload") or {}))
        hit = [p for k, p in rows if k == "lease_release" and p.get("revoked") is True]
        if not hit:
            bad.append("事件流里没有 kind=lease_release 且 revoked=true 的事件（实测 kinds=%s）"
                       % sorted({k for k, _ in rows}))
        else:
            p = hit[-1]
            if p.get("by") != "panel":
                bad.append("事件 payload 里 by 应为 panel（实测 %r）" % p.get("by"))
            if p.get("lease_id") != L["lease_id"]:
                bad.append("事件 payload 里 lease_id 对不上")
        return (not bad), "；".join(bad) if bad else "闸门日志 + 事件（kind=lease_release）都在"

    attempt("7 · 每次真吊销都留痕（闸门日志 + 事件 kind=lease_release，不新增码）", c7)

    # ══ 8 · 吊销不许被 renew 复活 ══════════════════════════════════════
    def c8():
        fresh(gate)
        L = gate.acquire_lease("窗G", ["B"], 600)
        gate.revoke_lease(lease_id=L["lease_id"], by="panel", reason="掐掉")
        ok, d = denied(gate.renew_lease, L["lease_id"], 600)
        if not ok:
            return False, "★被吊销的租约还能 renew 复活（续期被受理了：%s）" % d
        ok2, d2 = denied(gate.require_lease, "窗G")
        if not ok2:
            return False, "renew 之后 require_lease 又放行了：%s" % d2
        return True, "renew 被拒（%s）" % d

    attempt("8 · 吊销后不许靠 renew 复活（续期必须被拒，否则「吊销」只是一句建议）", c8)


if __name__ == "__main__":
    _completed = False
    try:
        main()
        _completed = True
    except BaseException as e:                                  # noqa: BLE001
        # ★ fail-closed：判据自己崩了**必须记红**。
        #   这里有个真踩过的坑：早先只写 `try: main() finally: …sys.exit(0)`，
        #   结果 main() 抛异常时，finally 里的 sys.exit(0) 把异常**顶掉**了，
        #   崩掉的判据反而报「绿 0 条 / 结论【绿】」—— 正是 run_all.py 文档里
        #   写的那个教训（「少一条判据不能当全绿」）的同款。
        print("  ★ 判据自身崩了（fail-closed 记红）：%s: %s" % (type(e).__name__, e),
              flush=True)
    finally:
        section("汇总")
        print("  绿 %d 条 / 红 %d 条 / 验不了 %d 条" % (len(OK), len(BAD), len(SKIP)), flush=True)
        for s in SKIP:
            print("  ~ %s" % s)
        try:
            shutil.rmtree(TMP, ignore_errors=True)
        except Exception:
            pass
        if BAD or not _completed:
            if _completed:
                print("  结论：【红】%d 条不成立" % len(BAD), flush=True)
            else:
                print("  结论：【红】判据没跑完（自身崩了，fail-closed）—— 一条都不算绿",
                      flush=True)
            for b in BAD:
                print("     - %s" % b)
            print("")
            print("  ★ 红验证（造坏闸门）：python -X utf8 work/_P1_造坏实现.py")
            print("    然后 set P1_GATE=<坏闸门目录>/gate.py 再跑本判据")
            sys.exit(1)
        print("  结论：【绿】吊销/幂等/防手滑/只影响目标/急停联动/模型无入口/留痕 全部成立",
              flush=True)
        sys.exit(0)
