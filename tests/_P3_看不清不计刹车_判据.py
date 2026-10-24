# -*- coding: utf-8 -*-
r"""看不清不计刹车 · 判据（2026-10-25 · T1 的“先证后改”产物；同日「拆标签」后扩展）

背景：有人担心「看不清」（画面采样退化 / 判词 unknown）会被算成真失败，
     进而计入 fail_streak、在极端情况下误拉急停。

本判据**只钉行为、不改行为**：
  ① 「看不清」走 code=5（CODE_UNSTABLE）⇒ 归 **unobservable**
     （2026-10-25 从 rejected_env 里拆出来：只贴标签，计数口径一个字没动）；
  ② unobservable **与** rejected_env 既不在 fail_counted_classes、
     也不在 same_target_counted_classes；
  ③ 其它前置拒绝码（3/4/7/9/10/11/12/13）仍归 rejected_env —— 标签没漂回去；
  ④ no_effect（8）与真异常（15 / 判不出 code）的归类不变 —— 回归不破；
  ⑤ 策略表**键值一个字不改**：fail_counted_classes == ["action_failed"]、
     same_target_counted_classes == ["no_effect", "action_failed"]；
  ⑥ 刹车判定：连续 8 次「看不清」/ 连续 8 次环境拒绝 / 两者混着 —— 都不命中任何连击。

跑法：python -X utf8 tests\_P3_看不清不计刹车_判据.py
      python -X utf8 tests\_P3_看不清不计刹车_判据.py --inject   # 必红
      --inject 干的事：把「看不清」故意当作 action_failed（坏实现），
      再用**同一组断言**重跑一遍 —— 只要判据没被写死/没自证，就必须抓到并报红。
退出码：0 绿；1 红。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import core                                        # noqa: E402
from agentctl import gate                                        # noqa: E402

FAILS = []

# 其它前置拒绝码（★ 5 已拆到 unobservable，这里故意不列 5）
REJECTED_CODES = (3, 4, 7, 9, 10, 11, 12, 13)


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  X   ") + name + (("  —— " + detail) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


def run_mapping(fc, tag=""):
    """把「code ⇒ 失败标签」这组断言跑一遍。

    fc 是被测实现：正常时传 core.failure_class；--inject 时传坏实现（5 → action_failed）。
    ★ 这组断言**不绑实现**，所以同一份断言既能验现役、也能验注入。
    """
    check(tag + "gate.CODE_UNSTABLE == 5", gate.CODE_UNSTABLE == 5,
          "实际 %r" % (gate.CODE_UNSTABLE,))
    check(tag + "FAILURE_CLASS_UNOBSERVABLE == unobservable",
          getattr(core, "FAILURE_CLASS_UNOBSERVABLE", None) == "unobservable",
          "实际 %r" % (getattr(core, "FAILURE_CLASS_UNOBSERVABLE", None),))
    check(tag + "_UNOBSERVABLE_CODES == {5}",
          set(getattr(core, "_UNOBSERVABLE_CODES", ())) == {5},
          "实际 %r" % (getattr(core, "_UNOBSERVABLE_CODES", None),))
    check(tag + "5 已从 _REJECTED_ENV_CODES 移除",
          5 not in getattr(core, "_REJECTED_ENV_CODES", ()),
          "实际 %r" % (getattr(core, "_REJECTED_ENV_CODES", None),))
    fc5 = fc({"ok": False, "code": gate.CODE_UNSTABLE})
    check(tag + "code=5 ⇒ unobservable（不算真失败）", fc5 == "unobservable", "实际 %r" % (fc5,))
    fc5b = fc({"ok": False, "code": 5})
    check(tag + "code=5 不再归 rejected_env（标签已拆开）",
          fc5b != "rejected_env", "实际 %r" % (fc5b,))
    for c in REJECTED_CODES:
        got = fc({"ok": False, "code": c})
        check(tag + "code=%d ⇒ rejected_env" % c, got == "rejected_env", "实际 %r" % (got,))
    check(tag + "code=8（确认没变）⇒ no_effect",
          fc({"ok": False, "code": 8}) == "no_effect",
          "实际 %r" % (fc({"ok": False, "code": 8}),))
    check(tag + "code=15（内部错误）⇒ action_failed",
          fc({"ok": False, "code": 15}) == "action_failed",
          "实际 %r" % (fc({"ok": False, "code": 15}),))
    check(tag + "code 取不到 ⇒ action_failed（宁严）",
          fc({"ok": False}) == "action_failed",
          "实际 %r" % (fc({"ok": False}),))
    check(tag + "成功 ⇒ None", fc({"ok": True, "code": 0}) is None,
          "实际 %r" % (fc({"ok": True, "code": 0}),))


print("== ① code ⇒ 失败标签（含「看不清」拆标签） ==")
run_mapping(core.failure_class)

print("== ② 策略表：两个不计入的类都不在两条连击里（键值一个字不改） ==")
pol = json.loads(open(os.path.join(ROOT, "src", "agentctl", "action_guard_policy.json"),
                      encoding="utf-8").read())
counted = pol.get("fail_counted_classes") or []
same = pol.get("same_target_counted_classes") or []
check("fail_counted_classes == ['action_failed']", counted == ["action_failed"], repr(counted))
check("same_target_counted_classes == ['no_effect', 'action_failed']",
      same == ["no_effect", "action_failed"], repr(same))
check("unobservable ∉ fail_counted_classes", "unobservable" not in counted)
check("unobservable ∉ same_target_counted_classes", "unobservable" not in same)
check("rejected_env ∉ fail_counted_classes", "rejected_env" not in counted)
check("rejected_env ∉ same_target_counted_classes", "rejected_env" not in same)
check("no_effect ∉ fail_counted_classes（W12 已修）", "no_effect" not in counted)

print("== ③ 刹车判定：连续失败不产生命中（看不见 / 环境拒绝 / 混着都不算） ==")
ALL_COUNTED = counted + same


def hits_of(classes):
    """按「分类表是否含该类」判命中；返回命中的类集合。"""
    return set(classes) & set(ALL_COUNTED)


h_unobs = hits_of(["unobservable"] * 8)
check("8 次 unobservable 不命中任何连击", h_unobs == set(), repr(h_unobs))
h_env = hits_of(["rejected_env"] * 8)
check("8 次 rejected_env 不命中任何连击", h_env == set(), repr(h_env))
h_mix = hits_of(["unobservable", "rejected_env"] * 4)
check("8 次「看不清 + 环境拒绝」混着也不命中", h_mix == set(), repr(h_mix))
h_sanity = hits_of(["action_failed"])
check("对照（不是全放过）：action_failed 命中 fail_streak", h_sanity == {"action_failed"},
      repr(h_sanity))

print()
if "--inject" in sys.argv:
    print("== 注入：把「看不清」故意当成 action_failed（坏实现）—— 上面的断言必须抓到 ==")
    def _bad_fc(r):
        # 坏实现 = 回到拆标签之前的口径：5 走 action_failed
        fc = core.failure_class(r)
        return "action_failed" if fc == "unobservable" else fc
    run_mapping(_bad_fc, "注入·")
    check("注入：坏实现确实把 5 判成了 action_failed（否则注入无效）",
          _bad_fc({"ok": False, "code": 5}) == "action_failed",
          "注入没生效：判据抓不到东西，别信这次的绿")
if FAILS:
    print("【红】%d 项失败：%s" % (len(FAILS), FAILS))
    raise SystemExit(1)
print("【绿】全部通过：看不清（code=5/unobservable）与环境拒绝（3/4/7/9/10/11/12/13/rejected_env）"
      "都不计入 fail_streak 与同目标连击；真异常（15/action_failed）仍计入。")
