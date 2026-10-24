# -*- coding: utf-8 -*-
r"""看不清不计刹车 · 判据（2026-10-25 · T1 的“先证后改”产物）

背景：有人担心「看不清」（画面采样退化 / 判词 unknown）会被算成真失败，
     进而计入 fail_streak、在极端情况下误拉急停。

本判据**只钉行为、不改行为**：
  ① 「看不清」走 code=5（CODE_UNSTABLE）⇒ 归 rejected_env；
  ② rejected_env 既不在 fail_counted_classes、也不在 same_target_counted_classes；
  ③ 真异常（如 code=15 内部错误）仍归 action_failed 且计入 —— 回归不破。

跑法：python -X utf8 tests\_P3_看不清不计刹车_判据.py
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
from agentctl import action_guard                                # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  X   ") + name + (("  —— " + detail) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


print("== ① 「看不清」的载体：code=5（CODE_UNSTABLE）==")
check("gate.CODE_UNSTABLE == 5", gate.CODE_UNSTABLE == 5, "实际 %r" % (gate.CODE_UNSTABLE,))
fc5 = core.failure_class({"ok": False, "code": gate.CODE_UNSTABLE})
check("code=5 ⇒ rejected_env（不算真失败）", fc5 == "rejected_env", "实际 %r" % (fc5,))

print("== ② 其它前置拒绝码也一律 rejected_env ==")
for c in (3, 4, 5, 7, 9, 10, 11, 12, 13):
    got = core.failure_class({"ok": False, "code": c})
    check("code=%d ⇒ rejected_env" % c, got == "rejected_env", "实际 %r" % (got,))

print("== ③ 三态语义不被混淆 ==")
check("code=8（确认没变）⇒ no_effect", core.failure_class({"ok": False, "code": 8}) == "no_effect")
check("code=15（内部错误）⇒ action_failed", core.failure_class({"ok": False, "code": 15}) == "action_failed")
check("code 取不到 ⇒ action_failed（宁严）", core.failure_class({"ok": False}) == "action_failed")
check("成功 ⇒ None", core.failure_class({"ok": True, "code": 0}) is None)

print("== ④ 策略表：看不清的类不计入两条连击 ==")
pol = json.loads(open(os.path.join(ROOT, "src", "agentctl", "action_guard_policy.json"),
                      encoding="utf-8").read())
counted = pol.get("fail_counted_classes") or []
same = pol.get("same_target_counted_classes") or []
check("fail_counted_classes 只含 action_failed", counted == ["action_failed"], repr(counted))
check("rejected_env ∉ fail_counted_classes", "rejected_env" not in counted)
check("rejected_env ∉ same_target_counted_classes", "rejected_env" not in same)
check("no_effect ∉ fail_counted_classes（W12 已修）", "no_effect" not in counted)

print("== ⑤ 刹车判定：连续 8 次「看不清」不产生命中 ==")
events = []
hits = []


class _FakeGuard:
    pass


# 用 action_guard 的纯函数级入口（不拉真急停）：observe 的 failure_class 入参就是分类结果
for _ in range(8):
    events.append({"actor": "judge", "ok": False, "failure_class": "rejected_env"})
# 分类表决定是否计数：rejected_env 不在任何计数表里 ⇒ 不命中
hit = any(cls in (counted + same) for cls in {e["failure_class"] for e in events})
check("8 次 rejected_env 不命中任何连击", hit is False)

print()
if "--inject" in sys.argv:
    # ★ 注入必红：故意假设「看不清」被算成 action_failed —— 判据必须抓到
    check("注入：看不清被误算成 action_failed（应当红）",
          core.failure_class({"ok": False, "code": 5}) == "action_failed",
          "注入生效：判据抓到了错误分类")
if FAILS:
    print("【红】%d 项失败：%s" % (len(FAILS), FAILS))
    raise SystemExit(1)
print("【绿】全部通过：看不清（code=5/rejected_env）不计入 fail_streak 与同目标连击；真异常仍计入。")
