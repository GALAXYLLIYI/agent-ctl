#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""P3 判据 · 动作库（skills.py）：结构、校验、回放纪律。

★ 本判据**不碰真界面**：全用假 executor（纯字典 + 计数）⇒ 不发输入、不截图、不碰桌面。
  被测模块 `src/agentctl/skills.py` 本身也不 import core / gate、不做 IO。

★ 覆盖（卡上点名的都在）：
  ① 正常 3 步全过 ⇒ ok=True、steps_done=3
  ② 第 2 步定位失败 ⇒ **立即停** ⇒ steps_done=1、step=1（0 基）、verdict=定位失败
  ③ expect 不满足 ⇒ 停且 ok=False
  ④ guard 拒绝 ⇒ 停（一步都不做 ⇒ steps_done=0）
  ⑤ validate 缺字段 / 未知 action / selector 空 ⇒ ValueError（中文、带字段名）
  ⑥ `--inject` 必红（把「定位失败」改成「继续」⇒ 引擎有 bug ⇒ 判据必须逮到）

★ 另外补了几条**同一条纪律的不同面**（不替代上面任何一条）：
  · 「没测出来」≠ 成功：expect 要的属性取不到 ⇒ 判词「没测出来」、ok=False
  · 动作失败 ⇒ 停
  · 窗口指纹对不上 ⇒ 停（记忆用错地方）
  · record_from 提炼（只留成功动作、统计正确）
  · save/load 往返（路径由调用方给 ⇒ 用系统临时目录，跑完即删）

用法：
    python -X utf8 tests\_P3_技能库_判据.py            # 正跑（期望 exit=0）
    python -X utf8 tests\_P3_技能库_判据.py --inject   # 注入自证（期望 exit=1）

退出码：
    0 = 全过 ｜ 1 = 有红（断言失败）｜ 2 = 注入没让行为退化（判据没牙）
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agentctl import skills                                        # noqa: E402

ONLY_INJECT = "--inject" in sys.argv

# ── 被测技能：3 步（点提交 → 敲字 → 点确定）────────────────────────────
SKILL = skills.make_skill(
    name="填表并提交",
    app="chrome",
    window_fingerprint="w-1",
    steps=[
        {"action": "ctl_click",
         "selector": {"name": "提交", "type": "Button"},
         "expect": {"visible": True}},
        {"action": "ctl_type",
         "selector": {"name": "备注", "type": "Edit"},
         "expect": {"visible": True, "text": "hello"}},
        {"action": "ctl_click",
         "selector": {"name": "确定", "type": "Button"},
         "expect": {"visible": True}},
    ],
)

# ── 假 executor：按 plan 回放「找得到 / 找不到」「动作成功 / 失败」──────
class FakeExecutor(object):
    """假执行器（不碰真界面）。

    plan：每步一张表 `{"locate": bool, "perform": bool, "element": dict}`。
    inject_continue=True 时，**定位失败被改成「继续」**（模拟引擎的 bug）⇒ 判据要逮到。
    """

    def __init__(self, plan, fingerprint=None, inject_continue=False):
        self.plan = plan
        self._fp = fingerprint
        self.inject_continue = inject_continue
        self.locate_calls = []
        self.perform_calls = []

    def locate(self, selector):
        i = len(self.locate_calls)
        self.locate_calls.append(selector)
        step = self.plan[i] if i < len(self.plan) else {}
        if not step.get("locate", True):
            if self.inject_continue:
                # 注入：把「定位失败」改成「继续」（假装找着了）
                return {"ok": True, "element": step.get("element") or {"visible": True},
                        "reason": "注入：定位失败被改成继续"}
            return {"ok": False, "element": None,
                    "reason": "找不到：%s" % (selector.get("name") or selector)}
        return {"ok": True, "element": step.get("element") or {"visible": True}, "reason": ""}

    def perform(self, action, element):
        i = len(self.perform_calls)
        self.perform_calls.append(action)
        step = self.plan[i] if i < len(self.plan) else {}
        if not step.get("perform", True):
            return {"ok": False, "reason": "动作没生效"}
        return {"ok": True, "reason": ""}


class FakeFpExecutor(FakeExecutor):
    """**支持窗口指纹核对**的假执行器（基类故意不提供 `fingerprint` ⇒ 默认不核对）。"""

    def fingerprint(self):
        return self._fp


PLAN_OK = [
    {"locate": True, "perform": True, "element": {"visible": True}},
    {"locate": True, "perform": True, "element": {"visible": True, "text": "hello"}},
    {"locate": True, "perform": True, "element": {"visible": True}},
]
PLAN_LOCATE_FAIL_2 = [
    {"locate": True, "perform": True, "element": {"visible": True}},
    {"locate": False, "perform": True, "element": {"visible": True, "text": "hello"}},
    {"locate": True, "perform": True, "element": {"visible": True}},
]
PLAN_EXPECT_BAD = [
    {"locate": True, "perform": True, "element": {"visible": True}},
    {"locate": True, "perform": True, "element": {"visible": True, "text": "别的"}},
    {"locate": True, "perform": True, "element": {"visible": True}},
]
PLAN_UNMEASURABLE = [
    {"locate": True, "perform": True, "element": {"visible": True}},
    {"locate": True, "perform": True, "element": {"visible": True}},   # 没有 text 属性
    {"locate": True, "perform": True, "element": {"visible": True}},
]
PLAN_PERFORM_FAIL = [
    {"locate": True, "perform": True, "element": {"visible": True}},
    {"locate": True, "perform": False, "element": {"visible": True, "text": "hello"}},
    {"locate": True, "perform": True, "element": {"visible": True}},
]


def main():
    fails = []

    def check(label, got, want):
        ok = (got == want)
        if not ok:
            fails.append(label)
        print("  %-54s %-12s %s" % (label, str(got), "PASS" if ok else "FAIL(want=%s)" % (want,)))
        return ok

    def check_true(label, got):
        return check(label, bool(got), True)

    print("== P3 判据 · 动作库（skills.py） ==")
    print("[口径] 被测模块零 IO、不 import core/gate；本判据全用假 executor ⇒ 不碰真界面")

    # ── ① 正常 3 步全过 ──
    print("---- ① 正常 3 步全过 ----")
    ex = FakeExecutor(PLAN_OK)
    r = skills.replay(SKILL, ex)
    check_true("① ok（3 步全过）", r["ok"])
    check("① steps_done", r["steps_done"], 3)
    check("① verdict", r["verdict"], "完成")
    check("① step（无停 ⇒ None）", r["step"], None)
    check_true("① total_ms 是个数", isinstance(r["total_ms"], (int, float)))
    check("① executor 定位次数", len(ex.locate_calls), 3)
    check("① executor 执行次数", len(ex.perform_calls), 3)

    # ── ② 第 2 步定位失败 ⇒ 立即停（steps_done=1）──
    print("---- ② 第 2 步定位失败 ⇒ 立即停 ----")
    # ★ 注入模式：这里也用**注入版** executor（把「定位失败」改成「继续」）⇒
    #   「定位失败即停」这条断言必须**变红** —— 这就是 --inject 必红的由来。
    ex2 = FakeExecutor(PLAN_LOCATE_FAIL_2, inject_continue=ONLY_INJECT)
    r2 = skills.replay(SKILL, ex2)
    check("② ok（必须 False）", r2["ok"], False)
    check("② steps_done（第 1 步做完就停）", r2["steps_done"], 1)
    check("② step（0 基 ⇒ 第 2 步 = 1）", r2["step"], 1)
    check("② step_no（1 基）", r2["step_no"], 2)
    check("② verdict", r2["verdict"], "定位失败")
    check_true("② reason 说了为什么", bool(r2["reason"]))
    check("② 定位只问了 2 次（不许问第 3 步）", len(ex2.locate_calls), 2)
    check("② 执行只做了 1 次（不许盲做）", len(ex2.perform_calls), 1)

    # ── ③ expect 不满足 ⇒ 停且 ok=False ──
    print("---- ③ expect 不满足 ⇒ 停 ----")
    r3 = skills.replay(SKILL, FakeExecutor(PLAN_EXPECT_BAD))
    check("③ ok（必须 False）", r3["ok"], False)
    check("③ steps_done", r3["steps_done"], 1)
    check("③ verdict", r3["verdict"], "期望不满足")
    check("③ step", r3["step"], 1)

    # ── ④ guard 拒绝 ⇒ 停 ──
    print("---- ④ guard 拒绝 ⇒ 停（一步都不做）----")
    ex4 = FakeExecutor(PLAN_OK)
    r4 = skills.replay(SKILL, ex4, guard=lambda sk, step: False)
    check("④ ok（必须 False）", r4["ok"], False)
    check("④ steps_done（一步都没做）", r4["steps_done"], 0)
    check("④ verdict", r4["verdict"], "闸门拒绝")
    check("④ 连定位都没发生", len(ex4.locate_calls), 0)

    # ── ④' guard 放行时正常走完（guard 不是摆设）──
    r4b = skills.replay(SKILL, FakeExecutor(PLAN_OK), guard=lambda sk, step: True)
    check("④' guard 放行 ⇒ 正常走完", r4b["steps_done"], 3)

    # ── ⑤ validate：缺字段 / 未知 action / selector 空 ⇒ ValueError ──
    print("---- ⑤ validate 报错（中文、带字段名）----")

    def expect_value_error(label, bad, must_contain):
        try:
            skills.validate(bad)
        except ValueError as e:
            msg = str(e)
            return check("%s（ValueError）" % label, must_contain in msg, True)
        return check("%s（ValueError）" % label, "没报错", True)

    def raw_skill(**over):
        """**故意绕过** `make_skill` 造一个（可能非法的）技能 dict —— 只为测 `validate`。

        ★ `make_skill` 是「唯一合法入口」，它内部就过 `validate` ⇒ 拿它造非法技能
          会当场抛错，测不到 `validate` 自己。⇒ 非法样本必须手搓 dict。
        """
        sk = {"name": "x", "app": "a", "window_fingerprint": "",
              "steps": [{"action": "ctl_click", "selector": {"name": "n"}, "expect": {}}],
              "stats": {"runs": 0, "ok": 0, "avg_ms": 0}, "version": 1}
        sk.update(over)
        return sk

    for field in skills.SKILL_FIELDS:
        bad = dict(SKILL)
        bad.pop(field, None)
        expect_value_error("⑤ 缺字段 %s" % field, bad, field)
    bad_action = raw_skill(steps=[
        {"action": "ctl_magic", "selector": {"name": "n"}, "expect": {}}])
    expect_value_error("⑤ 未知 action", bad_action, "action")
    bad_sel = raw_skill(steps=[
        {"action": "ctl_click", "selector": {"name": "", "type": ""}, "expect": {}}])
    expect_value_error("⑤ selector 空", bad_sel, "selector")
    bad_steps = raw_skill(steps=[])
    expect_value_error("⑤ steps 为空", bad_steps, "steps")
    bad_unknown = dict(SKILL)
    bad_unknown["extra"] = 1
    expect_value_error("⑤ 未知字段", bad_unknown, "extra")

    # ── ⑥ 其余失败模式（补）──
    print("---- ⑥ 其余失败模式 ----")
    r5 = skills.replay(SKILL, FakeExecutor(PLAN_UNMEASURABLE))
    check("⑥ 「没测出来」ok=False（不许当成功）", r5["ok"], False)
    check("⑥ 「没测出来」verdict", r5["verdict"], "没测出来")
    check("⑥ 「没测出来」steps_done", r5["steps_done"], 1)
    r6 = skills.replay(SKILL, FakeExecutor(PLAN_PERFORM_FAIL))
    check("⑥ 动作失败 ok=False", r6["ok"], False)
    check("⑥ 动作失败 verdict", r6["verdict"], "动作失败")
    r7 = skills.replay(SKILL, FakeFpExecutor(PLAN_OK, fingerprint="w-2"))
    check("⑥ 窗口指纹不匹配 ⇒ 停", r7["ok"], False)
    check("⑥ 窗口指纹不匹配 verdict", r7["verdict"], "窗口指纹不匹配")
    check("⑥ 窗口指纹不匹配 steps_done", r7["steps_done"], 0)

    # ── ⑦ record_from 提炼 ──
    print("---- ⑦ record_from 提炼技能 ----")
    log = [
        {"action": "ctl_click", "selector": {"name": "提交", "type": "Button"},
         "ok": True, "ms": 100},
        {"action": "ctl_type", "selector": {"name": "备注", "type": "Edit"},
         "ok": False, "ms": 50},                       # 失败的动作不进技能
        {"action": "ctl_click", "selector": {"name": "确定", "type": "Button"},
         "ok": True, "ms": 200},
    ]
    sk = skills.record_from(log, name="填表并提交", app="chrome", fingerprint="w-1")
    check("⑦ 提炼出的步数（只留成功动作）", len(sk["steps"]), 2)
    check("⑦ 提炼出的第 1 步 action", sk["steps"][0]["action"], "ctl_click")
    check("⑦ 提炼出的第 2 步 action", sk["steps"][1]["action"], "ctl_click")
    check("⑦ stats.runs", sk["stats"]["runs"], 1)
    check("⑦ stats.ok（=成功跑过几次，不是步数）", sk["stats"]["ok"], 1)
    check("⑦ stats.avg_ms", sk["stats"]["avg_ms"], 150.0)
    check_true("⑦ 提炼结果过 validate", skills.validate(sk) is None)

    # ── ⑧ save / load 往返（路径由调用方给 ⇒ 用系统临时目录，跑完即删）──
    print("---- ⑧ save / load 往返 ----")
    tmpdir = tempfile.mkdtemp(prefix="_skill_判据_")
    try:
        path = os.path.join(tmpdir, "skill.json")
        skills.save_skill(SKILL, path)
        back = skills.load_skill(path)
        check("⑧ 往返后步数", len(back["steps"]), 3)
        check("⑧ 往返后 name", back["name"], SKILL["name"])
        check("⑧ 往返后字段顺序一致", list(back.keys()), list(skills.SKILL_FIELDS))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    print("== 汇总 ==")
    if ONLY_INJECT:
        # 注入：把「定位失败」改成「继续」⇒ 引擎有 bug ⇒ 上面的「② 定位失败即停」必须变红。
        #   若一条都没红 ⇒ 判据没牙 ⇒ exit=2（更响的错），绝不装作红。
        print("---- 注入自证：把定位失败改成继续 ----")
        ex_inj = FakeExecutor(PLAN_LOCATE_FAIL_2, inject_continue=True)
        r_inj = skills.replay(SKILL, ex_inj)
        check_true("注入确实让引擎继续往下走了（steps_done=3）", r_inj["steps_done"] == 3)
        check_true("注入确实让引擎继续往下走了（第 3 步也执行了）", len(ex_inj.perform_calls) == 3)
        if not fails:
            print("【✗崩溃】注入**没有**让行为退化 ⇒ 判据没牙 ⇒ exit=2")
            return 2
        print("【红】注入被逮到（定位失败后引擎继续往下走 ⇒ 判据抓到了）⇒ exit=1")
        return 1
    if fails:
        print("【红】失败 %d 项 -> %s" % (len(fails), fails))
        print("★ 判据是活的（能抓住这个缺陷）")
        return 1
    print("【绿】全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
