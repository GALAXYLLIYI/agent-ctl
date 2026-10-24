# -*- coding: utf-8 -*-
r"""_P3_ctl_quiz_verify口径_判据.py —— ctl_quiz verify 的「集合判等」口径判据（2026-10-24）

背景（事件 #2341）：旧口径把「按 key 值排序」的期望与「按 DOM/显示序」读回的实际**逐字符串**比较，
多选只要显示序 ≠ key 值序就必误报 FAIL（实测 300 题里 48 道全这样，单选/判断 0 误报）。

本判据钉住三件事：
  1. 多选**集合相同、顺序不同** ⇒ 不算错（只计「顺序差异」）；
  2. **集合不同**（漏选/错选/没答）⇒ 必须判定不一致（绝不放宽）；
  3. 判断题 `true`/`false` 是**一个整体 key**，不许被拆成字符（2026-10-20 的坑）。

用法：
  python tests\_P3_ctl_quiz_verify口径_判据.py           # 正跑：exit 0
  python tests\_P3_ctl_quiz_verify口径_判据.py --inject  # 把实现换回旧口径 ⇒ 必须 exit 1
"""
import importlib.util
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILS = []


def ok(name, cond, detail=""):
    print("  [%s] %s%s" % ("绿" if cond else "红", name, ("  ← " + detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def load_quiz():
    from agentctl import quiz
    return quiz


def cases(q):
    cmp = q._verify_compare
    # ① 多选：集合相同、顺序不同 ⇒ 0 不一致、1 顺序差异
    bad, od = cmp({"1": ["A", "B", "C", "D"]}, {"1": "BDCA"})
    ok("多选顺序不同不算错（ABCD vs BDCA）", not bad and od == 1, "bad=%r od=%r" % (bad, od))
    # ② 多选：集合相同、顺序相同 ⇒ 0/0
    bad, od = cmp({"2": ["A", "B"]}, {"2": "AB"})
    ok("多选顺序相同 ⇒ 0/0", not bad and od == 0)
    # ③ 集合不同（漏选）⇒ 必须不一致
    bad, od = cmp({"3": ["A", "B"]}, {"3": "A"})
    ok("漏选必须判不一致", len(bad) == 1, "bad=%r" % (bad,))
    # ④ 集合不同（错选）⇒ 必须不一致
    bad, od = cmp({"4": ["A", "B"]}, {"4": "AC"})
    ok("错选必须判不一致", len(bad) == 1)
    # ⑤ 多选没答（空）⇒ 必须不一致
    bad, od = cmp({"5": ["A", "B"]}, {"5": ""})
    ok("没作答必须判不一致", len(bad) == 1)
    # ⑥ 单选顺序无意义
    bad, od = cmp({"6": ["A"]}, {"6": "A"})
    ok("单选一致 ⇒ 0/0", not bad and od == 0)
    # ⑦ 判断题 true 整体（不许拆字符）
    bad, od = cmp({"7": ["true"]}, {"7": "true"})
    ok("判断题 true 一致", not bad and od == 0)
    bad, od = cmp({"7": ["true"]}, {"7": "false"})
    ok("判断题 true vs false 必须不一致", len(bad) == 1)
    # ⑧ 判断题顺序差异不能被误算（一样就是一样）
    bad, od = cmp({"8": ["false"]}, {"8": "false"})
    ok("判断题 false 一致", not bad and od == 0)
    # ⑨ 混合 300 题规模：48 道多选乱序 ⇒ 0 不一致、48 顺序差异
    want, now = {}, {}
    for i in range(300):
        if i < 48:
            want[str(i)] = ["A", "B", "C", "D"]
            now[str(i)] = "BDCA"
        else:
            want[str(i)] = ["A"]
            now[str(i)] = "A"
    bad, od = cmp(want, now)
    ok("300 题/48 道多选乱序 ⇒ 0 不一致、48 顺序差异", not bad and od == 48,
       "bad=%d od=%d" % (len(bad), od))
    # ⑩ 真错仍被抓：上面的 48 道里把一道改成 ABC（漏 D）
    now["0"] = "ABC"
    bad, od = cmp(want, now)
    ok("混入一道真错（漏 D）仍被抓", len(bad) == 1 and bad[0]["qid"] == "0")


def main():
    q = load_quiz()
    if "--inject" in sys.argv:
        # 把实现换回旧口径：字符串比较（排序期望 vs 显示序）
        def old_compare(want, now):
            bad, od = [], 0
            for qid, keys in (want or {}).items():
                exp = "".join(sorted(keys))
                got = (now or {}).get(qid, "")
                if got != exp:
                    bad.append({"qid": qid, "期望": exp, "实际": got})
            return bad, od
        q._verify_compare = old_compare
        print("★ 注入：已换回旧口径（字符串比较）——本判据必须变红")
    print("== ctl_quiz verify 口径判据 ==")
    cases(q)
    if FAILS:
        print("结论：【红】%d 条不成立：%s" % (len(FAILS), FAILS[:4]))
        return 1
    print("结论：【绿】集合判等 / 顺序差异单列 / 判断题整体 key 全部成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
