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

★★ ★★★ **2026-10-24（W9 · 窗口b）：第③节也改成假时钟 —— 本判据从「半 hermetic」变成全 hermetic**

  ★ 改之前的自相矛盾（主控 W6 实测抓出来的）：
    · 第①节（参考实现）用**假时钟** ⇒ 轮次确定；
    · 第③节（**产品真代码** `act._wait_effect`）却用**真钟** ⇒ 每轮 87.8ms、理论红线 90ms
      （5 轮 < 450ms 才能采到第 6 轮）⇒ **余量只有 2.2ms** ⇒ 负载稍高就采不到第 6 轮
      ⇒ 产品返回 `{"生效": False}`（「确认没变」）⇒ 判据第③节断言失败 ⇒ **整条判据红**。
    · W6 实测：**空载 10/10 绿、满载 10/10 红**（同一份判据、同一台机器）⇒ 纯非 hermetic。
    · 且它与本文件自己写的「**时序类判据必须用假时钟**」原则**自相矛盾** ⇒ 必须改。

  ★ 改法（**产品文件一个字没动**，只在本进程内换时钟源）：
    · 新增 `_InjectedClock`（**秒制**假时钟：产品的 `perf_counter`/`sleep` 都是秒）
      ＋ `_injected_clock()` 上下文管理器：**临时**把 `time.perf_counter` / `time.sleep`
      换成假源（`finally` 必恢复 ⇒ 不污染任何别的跑法）。
    · 产品的 `act._wait_effect` 内部是 `import time as _t` ⇒ 拿到的是**同一个 time 模块对象**
      ⇒ 换模块属性即可生效（**不碰产品一行**）。
    · 第③节的 capture 回调里把 **capture 24ms ＋ diff 12.2ms 一起在返回前推进**
      （产品的 diff 发生在 capture 之后，判据无法在两者之间插推进）
      ⇒ 每轮推进 **24+12.2+50 = 86.2ms** ⇒ **与第①节参考实现逐毫秒同构**
      ⇒ 同样 450ms 预算下「第 6 轮必被采到」—— **阈值一个没放宽、预算一个没加大**。

  ★ 改造后第③节仍然要证明（**断言只增不减**，见 `suite_product()`）：
    · 变化排第 **6** 轮 ⇒ 采到 ⇒ 判「**没测出来**」(None) —— 这就是本刀的收益；
    · 变化排第 **7** 轮 ⇒ 采不到 ⇒ 判「**确认没变**」(False) —— 边界不许扩大；
    · 预算耗尽 ⇒ 「没测出来」与「确认没变」落**不同码**（**照产品自己的
      `act._rc_from_changed` 口径**：None⇒5、False⇒8，**不新造码**）。

★ ★★ **注入自证升级（--inject）：4 种退化逐一被逮到**
  「退化」= 把 M19′ 的语义改坏的 4 种形态；每一种都必须让第①节那 10 条断言里
  **至少一条失败**（= 判据抓得住这个病）⇒ 4 种全被逮到才算判据有牙；
  只要有**一种没被逮到** ⇒ 判据没牙 ⇒ 退出码 2（更响的错），绝不装作红。

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

import contextlib
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
BUDGET = 450                                        # ★ 模块级：断言套件要用（450ms，一个没放宽）

# ★ 第①节参考实现用的假时钟（**毫秒制**）：预算与 deadline 都按毫秒算
class FakeClock(object):
    """假时钟：把「预算够几轮」钉死，判据不测运气。"""

    def __init__(self):
        self.t = 0.0

    def perf_counter(self):
        return self.t

    def sleep(self, sec):
        self.t += sec * 1000.0


class _InjectedClock(object):
    """★ 第③节用的**秒制**假时钟（产品的 `perf_counter`/`sleep` 都是秒）。

    ★ 为什么要单独一个：产品的 `act._wait_effect` 写的是
      `deadline = t0 + budget_ms / 1000.0` ⇒ **秒**；第①节的参考实现是**毫秒**。
      两套时钟不可混用（混用会把 450ms 预算读成 450 秒 ⇒ 恒采得到 ⇒ 断言形同虚设）。
    """

    def __init__(self):
        self.t = 0.0

    def perf_counter(self):
        return self.t

    def sleep(self, sec):
        self.t += sec


@contextlib.contextmanager
def _injected_clock(clock):
    """★ 把 `time.perf_counter` / `time.sleep` **临时**换成假源（finally 必恢复）。

    ★ 只换**模块属性**，不碰产品文件：产品的 `act._wait_effect` 里写的是
      `import time as _t` ⇒ `_t` 就是本进程同一个 `time` 模块对象
      ⇒ 换 `time.perf_counter` / `time.sleep` 的属性即可让**产品真代码**走假时钟。
    ★ `finally` 恢复 ⇒ 绝不污染后面的跑法（判据自己也不许留后门）。
    """
    real_pc, real_sl = _time.perf_counter, _time.sleep
    _time.perf_counter = clock.perf_counter
    _time.sleep = clock.sleep
    try:
        yield clock
    finally:
        _time.perf_counter = real_pc
        _time.sleep = real_sl


def _mk_capture(clock, change_at_round, flicker=False, extra_ms=0.0):
    """采样回调。

    ★ `extra_ms`（W9 新增）：**第③节专用** —— 产品的 diff 发生在 capture **之后**，
      判据无法在两者之间插推进 ⇒ 于是把「capture 24ms ＋ diff 12.2ms」**在 capture 返回前**
      一起推进 ⇒ 每轮总推进 = 24 + 12.2 + 50 = **86.2ms**，与第①节参考实现**逐毫秒同构**。
      第①节传 0 ⇒ 行为与改造前**逐位一致**。
    """
    st = {"n": 0}

    def cap(_hwnd):
        st["n"] += 1
        clock.sleep(CAPTURE_MS / 1000.0)
        if extra_ms:
            clock.sleep(extra_ms / 1000.0)
        if flicker:
            return CHG if st["n"] == change_at_round else BASE
        return CHG if st["n"] >= change_at_round else BASE
    return cap


# ★ 退化形态（第②节注入自证用）：把 M19′ 的语义改坏的 4 种方式。
#   「none」= 不退化（正常实现）；其余 4 种每一种都必须被第①节的断言逮到。
DEFECTS = (
    ("none", "不退化（正常实现）"),
    ("false", "退化① 末帧有变化却判『确认没变』（M19′ 改前的原缺陷）"),
    ("true", "退化② 末帧有变化却判『成功』（误报成功）"),
    ("always_true", "退化③ 预算耗尽一律判『成功』（把『没测出来』也吞成成功）"),
    ("no_deadline", "退化④ 预算检查失效（轮次封顶兜底 ⇒ 边界轮被误判成功）"),
)


def m19p_wait_effect(budget_ms, change_at_round, flicker=False, inject=False, defect="none"):
    r"""**逐行复刻**产品 act._wait_effect 的时序与逻辑，只改退出分支。

    · inject=False => M19' 修好的版本（预算耗尽：最后一帧有变化 => None）
    · inject=True  => 把 M19' **改回 False**（模拟没修 / 修坏）
      （★ 保留 inject 形参是为了兼容旧调用；它等价于 defect="false"）
    · defect       => W9 新增：4 种退化形态之一（见 DEFECTS）
    """
    clock = FakeClock()
    grab = _mk_capture(clock, change_at_round, flicker)
    streak = 0
    rounds = 0
    last = None
    first_img = None
    deadline = float(budget_ms)

    def _exit():
        if defect == "always_true":
            val = True
        elif defect == "false" or inject:
            val = False
        elif defect == "true":
            # ★ 退化②：末帧**有**变化 ⇒ 却报「成功」
            val = True if saw() else False
        else:
            # ★ M19'：预算耗尽时，区分「从头到尾没变」与「最后一刻有动静」
            val = None if saw() else False
        return {"生效": val, "轮次": rounds, "首变图": first_img}

    def saw():
        if last is None:
            return False
        try:
            d = act._img_diff(BASE, last)
            return bool(d and d.get("changed") is True)
        except Exception:
            return False

    while True:
        rounds += 1
        # ── 检查①（采样前）—— 与产品 act.py:1353 同一位置 ──
        if defect != "no_deadline" and clock.perf_counter() >= deadline:
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
        if defect != "no_deadline" and clock.perf_counter() >= deadline:
            return _exit()
        clock.sleep(act.POLL_INTERVAL_MS / 1000.0)
        # ★ 退化④ 的安全阀：预算检查失效时靠轮次封顶兜底（否则「一直不变」那条会死循环）
        #   ★ 这个上限**只是自证桩的防死循环兜底**，不参与任何断言期望：
        #     边界轮退化在第 7~8 轮就命中（streak 攒够 POLL_HYSTERESIS）⇒ 30 轮绰绰有余；
        #     取 30 而不是 200 ⇒ 注入自证在满载下不必白跑 200 轮真实 PIL 差分。
        if defect == "no_deadline" and rounds >= 30:
            return _exit()


def product_wait_effect(budget_ms, change_at_round, flicker=False):
    r"""直接调**产品真代码** act._wait_effect。

    ★ W9：改成**注入假时钟**（`_injected_clock`）⇒ 轮次确定、**不受机器负载影响**。
      · 产品的 `act._wait_effect` 内 `import time as _t` ⇒ 换 `time` 模块属性即可生效，
        **产品文件一个字没动**；
      · capture 回调把 capture 24ms ＋ diff 12.2ms 一起推进（见 `_mk_capture` 的 extra_ms）
        ⇒ 每轮 86.2ms ⇒ 与第①节参考实现同构 ⇒ 450ms 预算下第 6 轮**必**被采到。
      · 「真钟对照」的老路**已删**（老路就是 W6 抓到的非 hermetic 来源）。
    """
    clock = _InjectedClock()
    cap = _mk_capture(clock, change_at_round, flicker, extra_ms=DIFF_MS)
    with _injected_clock(clock):
        r = act._wait_effect(0, BASE, budget_ms=budget_ms, capture=cap)
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


# ══════════════════════════════════════════════════════════════════════
# 断言套件
#   · `suite_m19p`   = 第①节那 **10 条**断言（改造前逐字搬过来，一字未改）
#   · `suite_product` = 第③节**产品真代码**的断言（注入假时钟后）
# ★ 抽成函数是为了让「4 种退化自证」复用**同一批**断言 ⇒ 期望值不会漂。
# ══════════════════════════════════════════════════════════════════════
def suite_m19p(fn, title):
    """第①节的 10 条断言（**唯一真源**）：fn = 被测实现。返回本套件失败的断言标签。"""
    local = []

    def check(label, got, want):
        ok = (got == want)
        if not ok:
            local.append(label)
        print("  %-52s %-10s %s" % (label, str(got), "PASS" if ok else "FAIL(want=%s)" % (want,)))
        return ok

    print(title)
    t, n, f = run(fn, BUDGET, 6)
    check("边界轮(k=6)：判成功 = False（不许误报成功）", t, 0)
    check("边界轮(k=6)：没测出来 = True（★本刀的收益）", n, 1)
    check("边界轮(k=6)：确认没变 = False", f, 0)
    t, n, f = run(fn, BUDGET, 1)
    check("立刻变化(k=1)：判成功 = True", t, 1)
    t, n, f = run(fn, BUDGET, 5)
    check("第5轮变化：判成功 = True（第5轮仍有余量）", t, 1)
    t, n, f = run(fn, BUDGET, 7)
    check("第7轮变化：确认没变 = True（★边界不许扩大）", f, 1)
    t, n, f = run(fn, BUDGET, 1, flicker=True)
    check("闪烁(k=1)：判成功 = False（★抗假阳性红线）", t, 0)
    t, n, f = run(fn, BUDGET, 6, flicker=True)
    check("闪烁(k=6)：判成功 = False（★边界轮上的抗假阳性）", t, 0)
    t, n, f = run(fn, BUDGET, 10 ** 9)
    check("一直不变：确认没变 = True（★语义不许丢）", f, 1)
    check("一直不变：没测出来 = False（不许降级）", n, 0)
    return local


def suite_product(title):
    """★ 第③节：**产品真代码**（注入假时钟）的断言套件。

    ★ 强度说明（相对改造前**只增不减**）：
      改造前：USE_PRODUCT 分支 2 条（且第 1 条是宽松的 `n >= 1`）、else 分支 1 条；
      改造后：**两档统一**走这套 **8 条**，且第 1 条由宽松 `n >= 1` **收紧为严格 `n == 1`**。
    """
    local = []

    def check(label, got, want):
        ok = (got == want)
        if not ok:
            local.append(label)
        print("  %-52s %-10s %s" % (label, str(got), "PASS" if ok else "FAIL(want=%s)" % (want,)))
        return ok

    print(title)
    # ── ① 变化排第 6 轮 ⇒ 预算内采到 ⇒ 判「没测出来」（本刀的收益）──
    t, n, f = run(product_wait_effect, BUDGET, 6)
    check("产品代码：边界轮(k=6)「没测出来」= True（排第6轮⇒采得到）", (n == 1), True)
    check("产品代码：边界轮(k=6)「判成功」= False（不许误报成功）", t, 0)
    check("产品代码：边界轮(k=6)「确认没变」= False（不许把没测出来说成没变）", f, 0)
    # ── ② 变化排第 7 轮 ⇒ 预算内采不到 ⇒ 判「确认没变」（边界不许扩大）──
    t7, n7, f7 = run(product_wait_effect, BUDGET, 7)
    check("产品代码：第7轮变化「确认没变」= True（排第7轮⇒采不到）", f7, 1)
    check("产品代码：第7轮变化「判成功」= False（不许误报成功）", t7, 0)
    check("产品代码：第7轮变化「没测出来」= False（不许说成没测出来）", n7, 0)
    # ── ③ 预算耗尽 ⇒ 「没测出来」与「确认没变」落**不同码**（照产品自己的口径，不新造）──
    rc_none = act._rc_from_changed(None)
    rc_false = act._rc_from_changed(False)
    check("产品代码：预算耗尽⇒「没测出来」与「确认没变」码不同（不许合并）",
          (rc_none != rc_false), True)
    check("产品代码：码口径 照 act._rc_from_changed（没测出来/确认没变）",
          (rc_none, rc_false), (5, 8))
    # ── ④ 闪烁抗假阳性（真钟下这条最易抖 ⇒ 现在必须确定）──
    tf, nf, ff = run(product_wait_effect, BUDGET, 1, flicker=True)
    check("产品代码：闪烁(k=1)「判成功」= False（★抗假阳性红线）", tf, 0)
    return local


def main():
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
    print("[口径] ★ 第③节产品真代码也走**注入假时钟**（W9）⇒ 轮次确定，不受负载影响")
    print("[模式] %s\n" % ("产品真代码（注入假时钟 · 参与断言）" if USE_PRODUCT else "M19' 参考实现（假时钟，轮次确定）"))

    if not ONLY_INJECT:
        # ── ① 参考实现（假时钟）—— 10 条断言，改造前逐字搬过来 ──
        fails.extend(suite_m19p(m19p_wait_effect, "---- ① M19' 实现（修好的版本）----"))

    print("---- ② 注入模式（M19' 被改回 False）----")
    t, n, f = run(lambda b, k, fl: m19p_wait_effect(b, k, fl, inject=True), BUDGET, 6)
    check("注入：边界轮「没测出来」= False（缺陷暴露）", n, 0)
    check("注入：边界轮「确认没变」= True（缺陷暴露）", f, 1)

    # ★ W9：4 种退化**逐一**自证 —— 每种都必须被第①节那 10 条断言逮到（至少一条失败）
    uncaught = []
    if ONLY_INJECT:
        print("---- ②' 4 种退化逐一自证（每种都必须被逮到）----")
        for defect, desc in DEFECTS:
            if defect == "none":
                continue
            got = suite_m19p(lambda b, k, fl, d=defect: m19p_wait_effect(b, k, fl, defect=d),
                              "  · %s" % desc)
            if got:
                print("    ⇒ %s：被逮到（%d 条断言失败）✔" % (desc, len(got)))
            else:
                print("    ⇒ %s：★**没被逮到**（判据没牙）" % desc)
                uncaught.append(desc)

    if not ONLY_INJECT:
        # ── ③ 产品真代码（注入假时钟 · 参与断言）—— 8 条 ──
        fails.extend(suite_product("---- ③ 当前产品代码 act._wait_effect（注入假时钟 · 轮次确定）----"))

    print()
    print("== 汇总 ==")
    if ONLY_INJECT:
        # ★ 注入模式的退出码语义对齐 M18 各判据：**注入被逮到 ⇒ 退出码 1（红）**；
        #   若注入**没能**让行为退化 ⇒ 判据没牙 ⇒ 退出码 2（更响的错）。
        #   ★ W9 追加：**4 种退化里有任何一种没被逮到 ⇒ 同样算「判据没牙」⇒ exit=2**。
        if uncaught:
            print("【✗崩溃】%d 种退化**没有**被逮到 ⇒ 判据没牙 ⇒ exit=2（%s）"
                  % (len(uncaught), "；".join(uncaught)))
            return 2
        if not fails:
            print("【红】注入被逮到（边界轮退化成『确认没变』⇒ exit=1）")
            print("【红】4 种退化全部被逮到 ⇒ 判据有牙")
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
