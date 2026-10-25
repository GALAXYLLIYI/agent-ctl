# -*- coding: utf-8 -*-
r"""技能库 · args 扩展判据（2026-10-25，第 60 条）

为什么要它：`skills.py` 原先的步骤只有 action/selector/expect，
「打字打什么」没处放 ⇒ 技能回放不了输入类动作（只测过点击）。
本判据钉住三件事：
  ① `args` 必须是 dict 或 None（写错要报错、指明字段）；
  ② 回放时 **args 真的传给了 executor**（新签名 perform(action, element, args)）；
  ③ **老签名 executor（只收 2 个参数）照样能跑**（不破坏既有技能/执行器）。

跑法：python -X utf8 tests\_P3_技能args_判据.py [--inject]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import skills                                    # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  X   ") + name + (("  —— " + detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class NewExec:
    """新签名：perform(action, element, args)。"""

    def __init__(self):
        self.seen = []

    def locate(self, selector):
        return {"ok": True, "element": {"name": selector.get("name"), "visible": True}}

    def perform(self, action, element, args=None):
        self.seen.append((action, dict(args or {})))
        return {"ok": True, "reason": "ok"}


class OldExec:
    """老签名：perform(action, element)。"""

    def __init__(self):
        self.calls = 0

    def locate(self, selector):
        return {"ok": True, "element": {"name": selector.get("name"), "visible": True}}

    def perform(self, action, element):
        self.calls += 1
        return {"ok": True, "reason": "ok-old"}


def _skill(steps):
    return skills.make_skill(name="judge_args", app="unit", window_fingerprint="", steps=steps)


def main():
    inject = "--inject" in sys.argv

    # ① args 必须是 dict / None
    bad = {
        "name": "judge_args", "app": "unit", "window_fingerprint": "",
        "steps": [{"action": "ctl_type", "selector": {"name": "编辑器"},
                   "args": "不是dict", "expect": None}],
        "stats": {"runs": 0, "ok": 0, "avg_ms": 0}, "version": 1,
    }
    try:
        skills.validate(bad)
        ok_bad = False
        why = "validate 没拦住字符串 args"
    except ValueError as e:
        ok_bad = "args" in str(e)
        why = "报错但没点名 args：%s" % e
    check("validate 拒绝非 dict 的 args（且点名 args）", ok_bad, why)

    # ② args 真的传给 executor
    ex = NewExec()
    sk = _skill([
        {"action": "ctl_focus", "selector": {"name": "编辑器"}, "expect": {"visible": True}},
        {"action": "ctl_type", "selector": {"name": "编辑器"},
         "args": {"text": "hello-args"}, "expect": None},
    ])
    r = skills.replay(sk, ex)
    sent = [a for (act, a) in ex.seen if act == "ctl_type"]
    check("回放成功", r["ok"], r.get("reason", ""))
    check("args 传到了 executor（text=hello-args）",
          bool(sent) and sent[0].get("text") == "hello-args", repr(sent))

    # ③ 老签名 executor 仍可用
    old = OldExec()
    r2 = skills.replay(_skill([{"action": "ctl_focus", "selector": {"name": "编辑器"},
                                "expect": {"visible": True}}]), old)
    check("老签名 executor 仍能回放（2 参数 perform）", r2["ok"] and old.calls == 1,
          "ok=%s calls=%s" % (r2.get("ok"), old.calls))

    # ④ 注入：故意把「args 未传」期望成成功 ⇒ 判据必须红
    if inject:
        ex2 = NewExec()
        skills.replay(sk, ex2)
        got = ex2.seen[-1][1] if ex2.seen else {}
        check("注入·args 未传却期望成功（应当红）", got.get("text") == "hello-args" and False,
              "注入生效：判据抓到了 args 丢失")

    print()
    if FAILS:
        print("【红】%d 项失败：%s" % (len(FAILS), FAILS))
        raise SystemExit(1)
    print("【绿】args 扩展：形状校验、真传参、老签名兼容 全过。")


if __name__ == "__main__":
    main()
