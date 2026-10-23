# -*- coding: utf-8 -*-
r"""M19' 判据 · 等生效「语义修复」：预算耗尽时，最后一帧有变化 => 判 None（没测出来）。

★ 本刀要修的缺陷（**当前代码里真实存在**）：
  act.py:1353-1357 预算耗尽时返回 {"生效": False}，
  而 core.py:2896 把 False 判成 **code=8「确认没变」**、只有 None 才走 code=5「没测出来」。
  => 事实是「我没测出来」，判词却是「确认没变」=> **违反 core.py:2881 自己写的铁律**。

★★ ★★ **为什么本判据用「假时钟」**（本轮实测踩出来的关键）：
  真实一轮 = 抓帧 24ms ＋ diff 12.2ms ＋ 间隔 50ms = **86.2ms**；
  但**单轮耗时会随机器负载抖动** => 「变化落第几轮 => 预算够不够采到它」在真实时钟下**不确定**
  （主控实测同一格 n=20 得过 18/20、判据跑却只拿到 <15/20 => **比例型阈值就是测运气**）。
  => **时序类判据必须用假时钟把「预算够几轮」钉死**，否则判据本身就是不可靠的。
  => 假时钟的轮次模型（与产品 act.py:1329-1382 的检查顺序逐行对齐）：
       每轮 = 检查①(采样前) -> capture(24) -> diff(12.2) -> 检查②(diff 后) -> sleep(50)
       => 450ms / 86.2ms => **第 6 轮能被采样、第 7 轮采样前退出**（确定，不抖）

★ 本判据是**缺陷驱动**的（与 M18 那些「先红后绿」相反）：
  · **当前代码跑 => 必须红**（因为缺陷在）—— 这证明判据有牙；
  · **M19' 实现跑 => 必须绿**；
  · **注入（把 M19' 改回 False）=> 必须红**。

用法：
    python -X utf8 tests\_P3_M19_语义修复_判据.py            # 三态对照
    python -X utf8 tests\_P3_M19_语义修复_判据.py --inject  # 只跑注入（必须红）
    # 改完产品代码后：M19_USE_PRODUCT=1 python -X utf8 tests\_P3_M19_语义修复_判据.py
"""
from __future__ import annotations

import os
import sys
import time as _time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (os.path.join(ROOT, "legacy_tools"), os.path.join(ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import act                                          # noqa: E402  产品真代码（只读导入）
from PIL import Image                               # noqa: E402

USE_PRODUCT = os.environ.get("M19_USE_PRODUCT") == "1"
ONLY_INJECT = "--inject" in sys.argv

W, H = 1632, 1191
BASE = Image.new("RGB", (W, H), (250, 250, 250))
CHG = BASE.copy()
for _x in range(100, 108):
    for _y in range(100, 108):
        CHG.putpixel((_x, _y), (0, 0, 0))

# ★ 假时钟的单轮成本（实测值，见 framecache.py 头注与 M18-d）
CAPTURE_MS = 24.0
DIFF_MS = 12.2
INTERVAL_MS = float(act.POLL_INTERVAL_MS)
ROUND_MS = CAPTURE_MS + DIFF_MS + INTERVAL_MS      # = 86.2ms


class FakeClock(object):
    """假时钟：把「预算够几轮」钉死，判据不测运气。"""

    def __init__(self):
        self.t = 0.0

    def perf_counter(self):
        return self.t

    def sleep(self, sec):
        self.t += sec * 1000.0


def _mk_capture(clock, change_at_round, flicker=False):
    st = {"n": 0}

    def cap(_hwnd):
        st["n"] += 1
        clock.sleep(CAPTURE_MS / 1000.0)
        if flicker:
            return CHG if st["n"] == change_at_round else BASE
        return CHG if st["n"] >= change_at_round else BASE
    return cap


def m19p_wait_effect(budget_ms, change_at_round, flicker=False, inject=False):
    r"""**逐行复刻**产品 act._wait_effect 的时序与逻辑，只改退出分支。

    · inject=False => M19' 修好的版本（预算耗尽：最后一帧有变化 => None）
    · inject=True  => 把 M19' **改回 False**（模拟没修 / 修坏）
    """
    clock = FakeClock()
    grab = _mk_capture(clock, change_at_round, flicker)
    streak = 0
    rounds = 0
    last = None
    first_img = None
    deadline = float(budget_ms)

    def _exit():
        if inject:
            val = False
        else:
            # ★ M19'：预算耗尽时，区分「从头到尾没变」与「最后一刻有动静」
            saw = False
            if last is not None:
                try:
                    d = act._img_diff(BASE, last)
                    saw = bool(d and d.get("changed") is True)
                except Exception:
                    saw = False
            val = None if saw else False
        return {"生效": val, "轮次": rounds, "首变图": first_img}

    while True:
        rounds += 1
        # ── 检查①（采样前）—— 与产品 act.py:1353 同一位置 ──
        if clock.perf_counter() >= deadline:
            return _exit()
        cur = grab(0)
        if cur is None:
            return {"生效": None, "轮次": rounds, "首变图": first_img}
        last = cur
        clock.sleep(DIFF_MS / 1000.0)
        d = act._img_diff(BASE, cur)
        if d.get("changed") is True:
            streak += 1
            if first_img is None:
                first_img = cur
        else:
            streak = 0
        if streak >= max(1, int(act.POLL_HYSTERESIS)):
            return {"生效": True, "轮次": rounds, "首变图": first_img}
        # ── 检查②（diff 后）—— 与产品 act.py:1377 同一位置 ──
        if clock.perf_counter() >= deadline:
            return _exit()
        clock.sleep(act.POLL_INTERVAL_MS / 1000.0)


class _RealClock(object):
    """给产品真代码用的 capture 回调：真 sleep（保留真实耗时）。"""

    def sleep(self, sec):
        _time.sleep(sec)


def product_wait_effect(budget_ms, change_at_round, flicker=False):
    r"""直接调**产品真代码** act._wait_effect（真时钟 => 有抖动，只作对照不作断言）。"""
    r = act._wait_effect(0, BASE, budget_ms=budget_ms,
                         capture=_mk_capture(_RealClock(), change_at_round, flicker))
    return {"生效": r.get("生效"), "轮次": r.get("轮次"), "首变图": r.get("首变图")}


def run(fn, budget_ms, k, flicker=False):
    r"""假时钟下完全确定（不取平均，单次即代表）；返回 (T, N, F)。"""
    r = fn(budget_ms, k, flicker)
    v = r.get("生效")
    if v is True:
        return 1, 0, 0
    if v is None:
        return 0, 1, 0
    return 0, 0, 1


def main():
    BUDGET = 450
    fails = []

    def check(label, got, want):
        ok = (got == want)
        if not ok:
            fails.append(label)
        print("  %-52s %-10s %s" % (label, str(got), "PASS" if ok else "FAIL(want=%s)" % (want,)))
        return ok

    print("== M19' 判据 · 等生效语义修复 ==")
    print("[口径] 产品常量只读：POLL_INTERVAL_MS=%dms ｜ POLL_HYSTERESIS=%d" % (act.POLL_INTERVAL_MS, act.POLL_HYSTERESIS))
    print("[口径] ★ 假时钟：一轮 = 抓帧 %.1fms ＋ diff %.1fms ＋ 间隔 %.1fms = **%.1fms** => 450ms 够 **%d** 轮"
          % (CAPTURE_MS, DIFF_MS, INTERVAL_MS, ROUND_MS, int(BUDGET // ROUND_MS)))
    print("[模式] %s\n" % ("产品真代码（真时钟，仅对照不作断言）" if USE_PRODUCT else "M19' 参考实现（假时钟，轮次确定）"))

    if not ONLY_INJECT:
        print("---- ① M19' 实现（修好的版本）----")
        t, n, f = run(m19p_wait_effect, BUDGET, 6)
        check("边界轮(k=6)：判成功 = False（不许误报成功）", t, 0)
        check("边界轮(k=6)：没测出来 = True（★本刀的收益）", n, 1)
        check("边界轮(k=6)：确认没变 = False", f, 0)
        t, n, f = run(m19p_wait_effect, BUDGET, 1)
        check("立刻变化(k=1)：判成功 = True", t, 1)
        t, n, f = run(m19p_wait_effect, BUDGET, 5)
        check("第5轮变化：判成功 = True（第5轮仍有余量）", t, 1)
        t, n, f = run(m19p_wait_effect, BUDGET, 7)
        check("第7轮变化：确认没变 = True（★边界不许扩大）", f, 1)
        t, n, f = run(m19p_wait_effect, BUDGET, 1, flicker=True)
        check("闪烁(k=1)：判成功 = False（★抗假阳性红线）", t, 0)
        t, n, f = run(m19p_wait_effect, BUDGET, 6, flicker=True)
        check("闪烁(k=6)：判成功 = False（★边界轮上的抗假阳性）", t, 0)
        t, n, f = run(m19p_wait_effect, BUDGET, 10 ** 9)
        check("一直不变：确认没变 = True（★语义不许丢）", f, 1)
        check("一直不变：没测出来 = False（不许降级）", n, 0)

    print("---- ② 注入模式（M19' 被改回 False）----")
    t, n, f = run(lambda b, k, fl: m19p_wait_effect(b, k, fl, inject=True), BUDGET, 6)
    check("注入：边界轮「没测出来」= False（缺陷暴露）", n, 0)
    check("注入：边界轮「确认没变」= True（缺陷暴露）", f, 1)

    if not ONLY_INJECT:
        print("---- ③ 当前产品代码 act._wait_effect（真时钟，仅对照）----")
        t, n, f = run(product_wait_effect, BUDGET, 6)
        if USE_PRODUCT:
            # ★ 产品已改：真时钟有抖动 ⇒ 用**宽松**阈值（≥1/2 拿到可重试）
            check("产品代码：边界轮「没测出来」≥ 1（产品已落地）", (n >= 1), True)
            check("产品代码：边界轮「判成功」= False（不许误报成功）", t, 0)
        else:
            # ★★ 缺陷驱动：**缺陷还在 ⇒ 本条必须算失败 ⇒ 整体 exit=1（红）**
            bad = (n != 1)
            print("  %-52s %-10s %s" % ("产品代码：边界轮「没测出来」= True", str(n),
                                        "FAIL(缺陷仍在 ⇒ 整判据应为红)" if bad else "PASS"))
            if bad:
                fails.append("产品代码：边界轮仍被判『确认没变』（缺陷未修）")
            print("  => 当前产品代码**缺陷仍在** ⇒ 本判据此刻**应为红**（exit=1）")

    print()
    print("== 汇总 ==")
    if ONLY_INJECT:
        # ★ 注入模式的退出码语义对齐 M18 各判据：**注入被逮到 ⇒ 退出码 1（红）**；
        #   若注入**没能**让行为退化 ⇒ 判据没牙 ⇒ 退出码 2（更响的错）。
        if not fails:
            print("【红】注入被逮到（边界轮退化成『确认没变』）⇒ exit=1")
            return 1
        print("【✗崩溃】注入**没有**让行为退化 ⇒ 判据没牙 ⇒ exit=2")
        return 2
    if fails:
        print("【红】失败 %d 项 -> %s" % (len(fails), fails))
        print("★ 判据是活的（能抓住这个缺陷）")
        return 1
    print("【绿】全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
