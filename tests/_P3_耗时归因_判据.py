# -*- coding: utf-8 -*-
r"""M3 判据 · 耗时归因 `profiling.py`（离线、零依赖、不碰桌面）

★ 本判据验什么（派单 M3 的验收点，逐条对应）：
  A1  `span` 嵌套**正确累加**（外层耗时含内层；用**假时钟** ⇒ 纳秒级可手算，不是"看起来差不多"）
  A2  `snapshot()` 的 p50/p95 用**已知样本**手算核对（1..100 ms ⇒ p50=50.0、p95=95.0、max=100.0、n=100）
  A3  样本 n<20 时 `render()` 必须含「**不足以归因**」
  A4  **并发 8 线程写 1000 条不丢计数**
  A5  `render()` 的中文表基本项齐全（阶段名 / 样本数 / p50 / p95 / max 都出现）
  A6  红线自守：`profiling.py` **不 import core / gate**、**不写盘**（静态核源码）

★ 注入自证（`--inject`）：把 `_percentile` 换成**忽略 pct 参数**的坏实现
  （p95 被算成 p50 —— 一个**不是 max** 的错值）⇒ A2 必须转红 ⇒ 判据有证明力。
  注入只发生在**本进程内**的模块属性上，**不改任何文件**。

★ 红线：本判据**不 import core / gate**、**不跑 GUI**、**不写盘**、只用标准库。

用法：
    python -X utf8 tests\_P3_耗时归因_判据.py            # 正跑（期望 rc=0）
    python -X utf8 tests\_P3_耗时归因_判据.py --inject   # 注入自证（期望 rc=1，必须红）
"""
from __future__ import annotations

import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

# ★ 判据零足迹：import 不许在仓里生成 __pycache__（照本仓其它判据同款纪律）
sys.dont_write_bytecode = True

from agentctl import profiling                        # noqa: E402

ONLY_INJECT = "--inject" in sys.argv

FAKE_NS = 1_000_000          # 1ms = 1e6 ns


class FakeClock(object):
    """假时钟：替掉 `profiling.time.perf_counter` ⇒ span 的纳秒值可手算。

    ★ 脚本按**调用顺序**给值：span 起始一次、span 结束一次。
    """

    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    def perf_counter(self):
        v = self.script[self.i] if self.i < len(self.script) else self.script[-1]
        self.i += 1
        return v


def _rows():
    """跑全部断言，返回 [(标签, 通过?, 详情)]。"""
    out = []

    def check(label, ok, detail):
        out.append((label, bool(ok), detail))

    # ── A1 span 嵌套正确累加（假时钟 ⇒ 精确纳秒）────────────────────
    profiling.reset()
    real_time = profiling.time
    try:
        # 脚本：outer 起 0.000 → inner 起 0.010 → inner 止 0.015 → outer 止 0.020
        profiling.time = FakeClock([0.000, 0.010, 0.015, 0.020])
        with profiling.span("outer"):
            with profiling.span("inner"):
                pass
    finally:
        profiling.time = real_time
    snap = profiling.snapshot()
    st = snap.get("stages") or {}
    o = st.get("outer") or {}
    i = st.get("inner") or {}
    check("A1 span 嵌套正确累加（外层含内层，纳秒级可手算）",
          o.get("n") == 1 and i.get("n") == 1
          and abs(float(o.get("p50") or 0) - 20.0) < 1e-6
          and abs(float(i.get("p50") or 0) - 5.0) < 1e-6,
          "outer.n=%s outer.p50=%s ｜ inner.n=%s inner.p50=%s（期望 20.0 / 5.0）"
          % (o.get("n"), o.get("p50"), i.get("n"), i.get("p50")))

    # ── A2 p50/p95 手算核对（1..100 ms）─────────────────────────────
    profiling.reset()
    for ms in range(1, 101):
        profiling.record("known", float(ms))
    snap = profiling.snapshot()
    v = (snap.get("stages") or {}).get("known") or {}
    check("A2 snapshot 的 p50/p95/max/n 用已知样本手算核对",
          v.get("n") == 100
          and abs(float(v.get("p50") or 0) - 50.0) < 1e-6
          and abs(float(v.get("p95") or 0) - 95.0) < 1e-6
          and abs(float(v.get("max") or 0) - 100.0) < 1e-6,
          "n=%s p50=%s p95=%s max=%s（期望 100 / 50.0 / 95.0 / 100.0）"
          % (v.get("n"), v.get("p50"), v.get("p95"), v.get("max")))
    check("A2b snapshot 形状 = {total_ms, stages:{name:{n,p50,p95,max}}}",
          isinstance(snap.get("total_ms"), (int, float))
          and set(v.keys()) >= {"n", "p50", "p95", "max"},
          "keys=%s total_ms=%s" % (sorted(v.keys()), snap.get("total_ms")))

    # ── A3 n<20 时 render 必须含「不足以归因」────────────────────────
    profiling.reset()
    for ms in range(1, 6):
        profiling.record("thin", float(ms))
    txt = profiling.render()
    check("A3 样本 n<20 ⇒ render 含「不足以归因」",
          "不足以归因" in txt and "样本不足" in txt,
          "render 前两行：%s" % " / ".join(txt.splitlines()[:2]))

    # ── A4 并发 8 线程写 1000 条不丢计数 ─────────────────────────────
    profiling.reset()
    per = 125
    threads = []

    def worker():
        for k in range(per):
            profiling.record("conc", 1.0)

    for _ in range(8):
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    snap = profiling.snapshot()
    c = (snap.get("stages") or {}).get("conc") or {}
    check("A4 并发 8 线程写 1000 条不丢计数",
          c.get("n") == 8 * per,
          "n=%s（期望 %d）" % (c.get("n"), 8 * per))

    # ── A5 render 的中文表基本项齐全 ────────────────────────────────
    profiling.reset()
    for ms in (1.0, 2.0, 3.0):
        profiling.record("表内阶段", ms)
    txt = profiling.render()
    need = ("耗时归因", "阶段", "样本数", "p50", "p95", "max", "表内阶段", "3")
    missing = [k for k in need if k not in txt]
    check("A5 render 中文表基本项齐全", not missing,
          "缺=%s" % (missing or "无"))

    # ── A6 红线自守：不 import core/gate、不写盘 ─────────────────────
    #   ★ 用 tokenize 剥掉注释与字符串再判 —— 否则 profiling.py 自己那句
    #     「不 import core / gate」的**说明文字**会被当成真 import（假阳性）。
    code_only = ""
    src_path = getattr(profiling, "__file__", "")
    try:
        import io
        import tokenize
        with io.open(src_path, "r", encoding="utf-8") as f:
            for tok in tokenize.generate_tokens(f.readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                code_only += tok.string + "\n"
    except Exception as e:
        code_only = ""
        check("A6 红线自守：profiling 不 import core/gate、不写盘", False,
              "读源码失败：%s: %s" % (type(e).__name__, e))
    else:
        bad_import = [k for k in ("import core", "from agentctl import core",
                                  "from agentctl import gate", "import gate")
                      if k in code_only]
        bad_write = [k for k in ("open(", "os.makedirs", "json.dump", "tempfile")
                     if k in code_only]
        check("A6 红线自守：profiling 不 import core/gate、不写盘（只看真代码行）",
              src_path != "" and not bad_import and not bad_write,
              "import=%s 写盘=%s" % (bad_import or "无", bad_write or "无"))
    return out


def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for label, ok, detail in rows:
        print("  [%s] %-52s %s" % ("PASS" if ok else "FAIL", label, detail), flush=True)


def main():
    print("== M3 判据 · 耗时归因 profiling.py（离线）==", flush=True)
    print("[口径] 内部整数纳秒；对外毫秒；分位数 nearest-rank（n=100 ⇒ p50 第50小 / p95 第95小）", flush=True)
    print("[口径] 样本门槛 n<%d ⇒ render 打印「不足以归因」" % profiling.MIN_SAMPLES_FOR_ATTRIBUTION, flush=True)

    if ONLY_INJECT:
        print("[模式] 注入自证：把 _percentile 换成忽略 pct 的坏实现（p95 算成 p50，不是 max）", flush=True)
        real = profiling._percentile

        def broken(sorted_vals, pct):
            # 坏实现：完全忽略 pct ⇒ p95 被算成中位数（一个**不是 max** 的错值）
            n = len(sorted_vals)
            return sorted_vals[max(0, n // 2 - 1)] if n else 0

        profiling._percentile = broken
        try:
            rows = _rows()
        finally:
            profiling._percentile = real
        show_rows("注入后的断言（目标：A2 必须红）", rows)
        by_label = {r[0]: r for r in rows}
        target = by_label.get("A2 snapshot 的 p50/p95/max/n 用已知样本手算核对")
        print()
        if target is not None and not target[1]:
            print("【红】注入被逮到（A2 转红：p95 被算成中位数）⇒ 判据有证明力 ⇒ exit=1", flush=True)
            return 1
        print("【✗崩溃】注入**没有**让 A2 转红 ⇒ 判据没牙 ⇒ exit=2", flush=True)
        return 2

    rows = _rows()
    show_rows("断言", rows)
    bad = [r[0] for r in rows if not r[1]]
    print()
    if bad:
        print("【红】失败 %d 项 -> %s" % (len(bad), bad), flush=True)
        return 1
    print("【绿】全过（%d 条断言）" % len(rows), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
