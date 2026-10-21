# -*- coding: utf-8 -*-
r"""tests/_P1_通行证并集_判据.py —— 判据：批准后的证里装什么，必须是「现有 ∪ 本次要的」

病根（2026-10-21 单 B，主改前实测）：
    `auth_popup.py` 的 `merge_pass()` 是 `base["scopes"] = new["scopes"]`（**覆盖**）
    ⇒ 一张「只要 A」的小请求，会把你**已经批过的 B、C 抹掉**。
    用户 2026-10-21 拍板：**方案 A（并集，一个概念）** —— 本次范围 = 现有证的有效范围 ∪ 本次要的。
    ★ 并集**不是放宽**：每一项要么你早已批过，要么你此刻正在批 ⇒ 它是「不撤销你已有的授权」。

判据（本判据**不弹真窗、不用真密钥、不碰真账**：真源模块直接调 + 临时账本目录）：
    A1 ★ **并集**：旧证（A+B，有效）+ 本次要 C ⇒ 批准集 = A、B、C
    A2 ★ **不凭空多给**：没有旧证 + 本次要 B ⇒ 批准集 = B（不许变成 ABCD）
    A3 ★ **废证不恢复权限**：旧证已过期 / 已消费 ⇒ 它的范围**不进并集**
    A4 ★ **--no-d 优先**：既有 D 也不并进来（否则并集把产品开关破掉）
    A5 ★ **窗口追加模式不并集**：那条路只动窗口，范围/时限/次数一律不碰
    A6 ★ **拒绝 ⇒ 旧证原封不动**（写盘只发生在签发路径里；用户拒绝压根不写）
    A7 ★ **接线**：`_initial_scope_states` 真的接了「现有范围」这个入参（改了但没接 ⇒ 红）
    A8 ★ **fail-closed**：一个范围都拿不到 ⇒ **不签发**
    A9 ★ `--inject` 必须变红：注入「不取并集」（回到改前的覆盖行为）⇒ A1 必须红

跑红自证：
    python -X utf8 tests/_P1_通行证并集_判据.py --inject
    —— 注入只在**进程内 monkeypatch**（把「现有范围」这个入参丢掉），现役文件一个字节不动。

退出码：0 = 全绿 ｜ 1 = 有红（逐条点名）｜ 2 = 环境不满足（读不到真源/造不出临时账本）
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "legacy_tools", "_src")

RC_GREEN, RC_RED, RC_ENV = 0, 1, 2


# ══════════════════════════ 环境准备 ══════════════════════════
def _prep_env():
    """真源目录进 sys.path；账本指到临时目录 ⇒ 不碰真通行证、不弹真窗。"""
    if not os.path.isdir(SRC):
        return None, "真源目录不在：%s" % SRC
    tmp = tempfile.mkdtemp(prefix="agentctl_union_")
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    return tmp, ""


def _load_auth(tmp):
    """导入真源 auth_popup，并把它的账本/锁全部指到临时目录。"""
    try:
        import auth_popup as ap
    except Exception as e:
        return None, "import 真源 auth_popup 失败：%s: %s" % (type(e).__name__, e)
    ap.AUTHDIR = tmp
    ap.PASSFILE = os.path.join(tmp, "auth_pass.json")
    ap.KEYFILE = os.path.join(tmp, "auth_key.json")
    ap.LOCKFILE = os.path.join(tmp, "gate.lock")
    ap.log = lambda *a, **k: None
    return ap, ""


def _write_pass(ap, scopes, expires_in=600, consumed=False):
    obj = {"scopes": list(scopes), "scope": "、".join(scopes),
           "acts_limit": 200, "acts": 0, "issued": time.time(),
           "expires_at": time.time() + expires_in, "consumed": consumed,
           "nonce": "test", "windows": []}
    with open(ap.PASSFILE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _read_pass(ap):
    try:
        with open(ap.PASSFILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _letters(ap, scopes):
    return sorted({ap._letter_of(s) for s in (scopes or []) if ap._letter_of(s)})


def _approve(ap, request_items, no_d=False, existing=None, drop_existing=False):
    """走**真路径**：算范围（并集）→ 签发。返回 (批准集字母列表, 证里的范围字母列表, why)。"""
    have = [] if drop_existing else list(existing or [])
    states, extra = ap._initial_scope_states(request_items, no_d, have)
    approved = sorted(k for k, v in states.items() if v)
    merged, why = ap.issue_pass(states, extra, 10, 200, None, [], False)
    got = _letters(ap, (merged or {}).get("scopes") or [])
    return approved, got, why


def _run_main(ap, request, acts=None):
    """用**假弹窗**驱动真源 main()（拒绝 ⇒ 不写盘）：拿它算出来的范围与动作上限。

    ★ 为什么加这一段：并集与「动作上限夹紧」都发生在 main() 里
      （弹窗进程拿到的 acts 已经被夹过）⇒ 只测 issue_pass 看不到它们。
      假弹窗让这条路径**无人值守**也能跑（不弹真窗、不用真密钥）。
    """
    captured = {}

    def fake_popup(kind, scope="", minutes=10, acts=20, note="", targets=None,
                   scope_states=None, scope_extra=None, no_d=False,
                   candidates=None, window_ack=False):
        captured["scope_states"] = scope_states
        captured["scope_extra"] = scope_extra
        captured["acts"] = acts
        captured["minutes"] = minutes
        return "denied"                 # 拒绝 ⇒ 一个字节都不写盘

    saved_popup = ap._popup
    ap._popup = fake_popup
    argv = ["auth_popup.py", "--request", request]
    if acts is not None:
        argv += ["--acts", str(acts)]
    old = sys.argv
    sys.argv = argv
    try:
        rc = ap.main()
    finally:
        sys.argv = old
        ap._popup = saved_popup
    return captured, rc


# ══════════════════════════ 判据正文 ══════════════════════════
def run_behavior(ap, drop_existing=False):
    """drop_existing=True ⇒ **注入**：把「现有范围」这个入参丢掉（回到改前的覆盖行为）。"""
    oks, reds = [], []

    # ── A1 并集 ───────────────────────────────────────────────────────
    _write_pass(ap, ["A级 只读感知", "B级 点击滚动"])
    have = ap._existing_valid_scopes()
    approved, got, _ = _approve(ap, ["C"], existing=have, drop_existing=drop_existing)
    if approved == ["A", "B", "C"] and got == ["A", "B", "C"]:
        oks.append("A1 并集：旧证 A+B + 本次要 C ⇒ 批准集 %s，证里 %s"
                   % (approved, got))
    else:
        reds.append("A1 并集：旧证 A+B + 本次要 C ⇒ 批准集 %s，证里 %s（应都是 A,B,C）"
                    % (approved, got))

    # ── A2 不凭空多给 ────────────────────────────────────────────────
    try:
        os.remove(ap.PASSFILE)
    except OSError:
        pass
    approved, got, _ = _approve(ap, ["B"])
    if approved == ["B"] and got == ["B"]:
        oks.append("A2 不凭空多给：没有旧证 + 本次要 B ⇒ 只有 B（没变成 ABCD）")
    else:
        reds.append("A2 不凭空多给：没有旧证 + 本次要 B ⇒ 批准集 %s、证里 %s（应只有 B）"
                    % (approved, got))

    # ── A3 废证不恢复权限（过期 / 已消费） ────────────────────────────
    _write_pass(ap, ["A级 只读感知"], expires_in=-10)
    have = ap._existing_valid_scopes()
    if have == []:
        oks.append("A3a 过期旧证不进并集（现有有效范围 = 空）")
    else:
        reds.append("A3a 过期旧证仍被算进并集：%s" % have)
    _write_pass(ap, ["A级 只读感知"], consumed=True)
    have = ap._existing_valid_scopes()
    if have == []:
        oks.append("A3b 已消费旧证不进并集（现有有效范围 = 空）")
    else:
        reds.append("A3b 已消费旧证仍被算进并集：%s" % have)
    approved, got, _ = _approve(ap, ["C"], existing=have)
    if approved == ["C"] and got == ["C"]:
        oks.append("A3c 废证 + 本次要 C ⇒ 只有 C（没凭废证恢复权限）")
    else:
        reds.append("A3c 废证 + 本次要 C ⇒ 批准集 %s、证里 %s（应只有 C）"
                    % (approved, got))

    # ── A4 --no-d 优先 ───────────────────────────────────────────────
    _write_pass(ap, ["A级 只读感知", "D级 拖拽"])
    have = ap._existing_valid_scopes()
    approved, got, _ = _approve(ap, ["C"], no_d=True, existing=have)
    if "D" not in approved and "D" not in got:
        oks.append("A4 --no-d 优先：既有 D 也没并进来（批准集 %s）" % approved)
    else:
        reds.append("A4 --no-d 被并集破掉：批准集 %s、证里 %s（D 必须不在内）"
                    % (approved, got))

    # ── A5 窗口追加模式不并集 ────────────────────────────────────────
    _write_pass(ap, ["A级 只读感知", "B级 点击滚动"])
    have = ap._existing_valid_scopes()
    states, extra = ap._initial_scope_states(["C"], False, [])
    merged, why = ap.issue_pass(states, extra, 10, 200, None, [], True)   # window_ack=True
    got = _letters(ap, (merged or {}).get("scopes") or [])
    if got == ["A", "B"]:
        oks.append("A5 窗口追加模式不并集：范围原样不动（%s），只动窗口" % got)
    else:
        reds.append("A5 窗口追加模式：范围变成了 %s（应保持 A,B 不动）" % got)

    # ── A6 拒绝 ⇒ 旧证原封不动（静态：拒绝路径不写通行证） ────────────
    src = open(os.path.join(SRC, "auth_popup.py"), encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        reds.append("A6 真源语法错（%s）" % e)
        tree = None
    deny_seg = ""
    issue_seg = ""
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "deny":
                deny_seg = ast.get_source_segment(src, node) or ""
            if isinstance(node, ast.FunctionDef) and node.name == "issue_pass":
                issue_seg = ast.get_source_segment(src, node) or ""
    writes_pass = ("_save(PASSFILE" in deny_seg) or ("save_json(PASSFILE" in deny_seg)
    if deny_seg and not writes_pass:
        oks.append("A6 拒绝不动旧证：拒绝路径（deny）不写通行证 ⇒ 旧证原封不动")
    else:
        reds.append("A6 拒绝路径会写通行证 ⇒ 用户点「拒绝」也会动旧证（deny 段里出现 PASSFILE 写）")
    if issue_seg and ("_save(PASSFILE" in issue_seg or "save_json(PASSFILE" in issue_seg):
        oks.append("A6b 写盘只有一处：签发路径（issue_pass）写通行证")
    else:
        reds.append("A6b 签发路径里没找到写通行证的动作（判据与实现脱节）")

    # ── A7 接线：_initial_scope_states 真的接了「现有范围」 ────────────
    try:
        tree = ast.parse(src)
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_initial_scope_states":
                fn = node
        if fn is None:
            reds.append("A7 接线：真源里找不到 _initial_scope_states")
        else:
            args = [a.arg for a in fn.args.args]
            seg = ast.get_source_segment(src, fn) or ""
            used = "existing_scopes" in seg.split('"""', 2)[-1]
            if "existing_scopes" in args and used:
                oks.append("A7 接线：_initial_scope_states 真的用了 existing_scopes（并集不是摆设）")
            else:
                reds.append("A7 接线：_initial_scope_states 的入参 %s 里没有 existing_scopes"
                            "或没真正用它 ⇒ 改了但没接上" % args)
    except SyntaxError as e:
        reds.append("A7 接线：真源语法错（%s）" % e)

    # ── A8 fail-closed：一个范围都拿不到 ⇒ 不签发 ─────────────────────
    #   ★ 注意：**空请求 ≠ 没有范围** —— 空请求的既有口径是「ABCD 一次给齐」（分工书 ①）。
    #     「没有范围」指的是：scope_states 全空（且没有可用的范围项）。
    merged, why = ap.issue_pass({}, [], 10, 200, None, [], False)
    if merged is None and why:
        oks.append("A8a fail-closed：一个范围都拿不到 ⇒ 不签发（%s）" % why[:40])
    else:
        reds.append("A8a fail-closed：范围为空却签出了证（%s）" % str(merged)[:80])
    # 空白范围项**不算一个范围**（不许签出 scopes=[""] 这种废证）
    merged2, why2 = ap.issue_pass({}, [""], 10, 200, None, [], False)
    if merged2 is None:
        oks.append("A8b fail-closed：空白范围项不算范围 ⇒ 不签发")
    else:
        reds.append("A8b 空白范围项被当成了范围 ⇒ 签出了 scopes=%s"
                    % (merged2 or {}).get("scopes"))

    # ── A9 动作上限：并集带进来的范围要按它自己的上限夹紧（main() 里夹） ──
    #   背景：调用方算 acts 时看的是**它申请的范围**；并集带进来的 D（上限 10）
    #   不在那个计算里 ⇒ 不夹的话，一张含 D 的并集证会带着 20 的额度（比 D 宽松）。
    _write_pass(ap, ["A级 只读感知", "D级 拖拽"])
    cap, _rc = _run_main(ap, "C", acts=20)
    got_acts = cap.get("acts")
    if got_acts == 10:
        oks.append("A9 额度夹紧：旧证含 D + 要 C ⇒ 弹窗拿到的 acts=%s（D 的上限 10，不比它宽松）"
                   % got_acts)
    else:
        reds.append("A9 额度夹紧：旧证含 D + 要 C ⇒ acts=%s（应夹到 10）" % got_acts)
    #   没有并集 ⇒ 原样（默认行为不变）
    try:
        os.remove(ap.PASSFILE)
    except OSError:
        pass
    cap2, _rc2 = _run_main(ap, "C", acts=20)
    if cap2.get("acts") == 20:
        oks.append("A9b 默认不变：没有旧证 ⇒ acts 原样 20（不夹）")
    else:
        reds.append("A9b 默认不变：没有旧证 ⇒ acts=%s（应原样 20）" % cap2.get("acts"))
    #   用户 2026-10-19 定「取消动作次数上限」⇒ acts=0（不封顶）不被并集破掉
    _write_pass(ap, ["A级 只读感知", "D级 拖拽"])
    cap3, _rc3 = _run_main(ap, "C", acts=0)
    if cap3.get("acts") == 0:
        oks.append("A9c 不封顶不被破：旧证含 D + acts=0 ⇒ 仍是 0（不封顶）")
    else:
        reds.append("A9c 不封顶被破：acts=0 却变成 %s" % cap3.get("acts"))
    #   夹紧的接线：main() 真的用了 _clamp_acts / _carried_letters
    try:
        mtree = ast.parse(src)
        mseg = ""
        for node in ast.walk(mtree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                mseg = ast.get_source_segment(src, node) or ""
        if "_clamp_acts" in mseg and "_carried_letters" in mseg:
            oks.append("A9d 接线：main() 用了 _clamp_acts + _carried_letters（夹紧不是摆设）")
        else:
            reds.append("A9d 接线：main() 里没看到 _clamp_acts / _carried_letters")
    except SyntaxError as e:
        reds.append("A9d 接线：真源语法错（%s）" % e)

    #   额度表认不出 ⇒ 必须**保守**夹（不许「静默不夹」= 并集顺带放宽额度）
    saved_tbl = ap.gate.ACT_LIMIT
    ap.gate.ACT_LIMIT = {"XX级 不认识": 999}
    try:
        v = ap._clamp_acts(20, {"D"})
    finally:
        ap.gate.ACT_LIMIT = saved_tbl
    if v <= 10:
        oks.append("A9e 额度表认不出 ⇒ 保守夹（acts=%s ≤ 10，不静默放行）" % v)
    else:
        reds.append("A9e 额度表认不出 ⇒ acts=%s（应保守夹到 ≤10；静默不夹 = 顺带放宽额度）" % v)

    return oks, reds


# ══════════════════════════ 主流程 ══════════════════════════
def main(argv):
    inject = "--inject" in argv
    tmp, why = _prep_env()
    if not tmp:
        print("环境不满足：%s" % why)
        return RC_ENV
    oks, reds = [], []
    try:
        ap, err = _load_auth(tmp)
        if ap is None:
            print("环境不满足：%s" % err)
            return RC_ENV
        if inject:
            # 注入：把「现有范围」这个入参丢掉 ⇒ 回到改前的覆盖行为 ⇒ A1 必须红
            i_oks, i_reds = run_behavior(ap, drop_existing=True)
            oks += i_oks
            reds += i_reds
            if not i_reds:
                reds.append("A9 注入自证失败：注入「不取并集」后仍然全绿 ⇒ 这条判据是死的")
            else:
                print("  · 注入「不取并集」⇒ 变红 %d 条：%s" % (len(i_reds), i_reds[0][:120]))
        else:
            oks, reds = run_behavior(ap)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 72)
    print("P1 通行证并集判据  ｜  账本=临时（不碰真通行证、不弹真窗、不用真密钥）")
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
            return RC_RED
        print("结论：【注入自证失败】注入后仍绿 ⇒ 这条判据是死的。")
        return RC_RED
    if reds:
        print("结论：【红】")
        return RC_RED
    print("结论：【绿】")
    return RC_GREEN


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
