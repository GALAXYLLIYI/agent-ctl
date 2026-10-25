# -*- coding: utf-8 -*-
"""耗时归因的**零依赖**计时器（2026-10-25 · 派单 M3）。

★ 为什么要有它：
  通宵记录 §七 里那条「800–1300ms 延迟带」**自认无归属** —— 知道"有一坨时间不知道花在哪"，
  但手上没有一把能**分阶段**量的尺子 ⇒ 只能靠猜。
  ⇒ 本模块只做一件事：**把时间按阶段分桶记下来**，并且诚实地告诉你
  「样本够不够下结论」（n<20 ⇒ 渲染时明写"不足以归因"）。

★ 口径（三条，别改）：
  ① **单位**：内部一律 **整数纳秒**（int）⇒ 不用浮点累加 ⇒ 长时间跑不漂；
     对外（snapshot / render）换算成**毫秒**。
  ② **计时源**：`time.perf_counter()`（单调钟）⇒ **不依赖 clock.py**，
     也不受系统时钟跳变（NTP 校时）影响。
  ③ **分位数**：**nearest-rank**（向上取整到第 k 小，1-based）⇒ 人手可核对：
     n=100 时 p50 取第 50 小、p95 取第 95 小。

★ 本模块自己守的红线：
  · **不 import core / gate** ⇒ 不碰闸门、不碰契约、不引入循环依赖。
  · **不写盘** ⇒ 只在内存里；进程退出即散。
  · **只用标准库**。

★ 线程安全：所有写入/读取都过同一把 `threading.Lock` ⇒ 8 线程并发写不丢计数。

★ 用法（先看这个，再看 docs\\耗时归因_20261025.md）：
      from agentctl import profiling
      with profiling.span("dispatch.total"):
          ...
      profiling.record("ocr.recognize", 42.7)        # 外部已知耗时（毫秒）
      print(profiling.render())                      # 中文表 + 样本够不够的警告
"""
from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager

__all__ = [
    "span", "record", "snapshot", "render", "reset",
    "MIN_SAMPLES_FOR_ATTRIBUTION", "NS_PER_MS",
]

#: 1 毫秒 = 1e6 纳秒
NS_PER_MS = 1_000_000

#: 低于这个样本数，render 会打「不足以归因」—— 样本太少时 p50/p95 只是噪声，不是结论。
MIN_SAMPLES_FOR_ATTRIBUTION = 20

_LOCK = threading.Lock()
#: 阶段名 -> 该阶段的全部样本（整数纳秒）
_STAGES: dict[str, list[int]] = {}


def _record_ns(stage: str, ns: int) -> None:
    """把一个**整数纳秒**样本写进阶段桶（线程安全）。"""
    ns = int(ns)
    if ns < 0:          # 理论上不会（单调钟），但负数会污染 max/p95
        ns = 0
    with _LOCK:
        _STAGES.setdefault(str(stage), []).append(ns)


@contextmanager
def span(name: str):
    """计一段代码的耗时，落进 `name` 这个阶段桶。

    ★ **异常也会记账**（`finally`）⇒ 抛异常的路径**不丢样本**
      （否则"只有成功路径有样本"⇒ p95 会被系统性拉低）。
    ★ 嵌套：`span` 可以套 `span` ⇒ **外层的耗时包含内层** ⇒
      「总耗时」用外层桶，「子阶段」用内层桶 ⇒ 两者不重复加总（只有快照的 total 会重复计，见下）。
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _record_ns(name, (time.perf_counter() - t0) * 1e9)


def record(stage: str, ms: float) -> None:
    """把**外部已知**的耗时（毫秒）记进某个阶段。

    ★ 什么时候用这个：某段耗时的钟在别处（别的进程/别的库/Windows API 返回的毫秒数），
      没法用 `span` 包住 ⇒ 直接把毫秒数报进来。
    """
    _record_ns(stage, round(float(ms) * NS_PER_MS))


def _percentile(sorted_vals: list[int], pct: int) -> int:
    """nearest-rank 分位数：取第 `ceil(pct/100 * n)` 小（1-based）。

    ★ 人手可核对：n=100 ⇒ p50 取第 50 小、p95 取第 95 小。
    ★ 入参**必须已排序**（调用方负责）。
    """
    n = len(sorted_vals)
    if n == 0:
        return 0
    k = math.ceil(pct / 100.0 * n)
    if k < 1:
        k = 1
    if k > n:
        k = n
    return sorted_vals[k - 1]


def snapshot() -> dict:
    """当前所有阶段的统计（毫秒）。

    返回形状（判据按这个形状手算核对）：
        {"total_ms": <所有样本耗时之和>,
         "stages": {"<阶段名>": {"n": <样本数>, "p50": <毫秒>,
                                "p95": <毫秒>, "max": <毫秒>}}}

    ★ `total_ms` = **所有阶段样本耗时之和** ⇒ 嵌套 span 会**重复计**
      （外层已含内层）⇒ 它是"被计时的总量"，**不是**"整条链路的墙钟时间"。
    """
    with _LOCK:
        stages: dict[str, dict] = {}
        total_ns = 0
        for name, vals in _STAGES.items():
            s = sorted(vals)
            n = len(s)
            if not n:
                continue
            total_ns += sum(s)
            stages[name] = {
                "n": n,
                "p50": round(_percentile(s, 50) / NS_PER_MS, 3),
                "p95": round(_percentile(s, 95) / NS_PER_MS, 3),
                "max": round(s[-1] / NS_PER_MS, 3),
            }
    return {"total_ms": round(total_ns / NS_PER_MS, 3), "stages": stages}


def reset() -> None:
    """清空所有已记的样本（**只给测试/长跑分段用**；生产路径不要调）。"""
    with _LOCK:
        _STAGES.clear()


def render(summary: dict | None = None) -> str:
    """把 snapshot() 渲染成中文表；**样本不够就明说"不足以归因"**。

    ★ 这个函数的价值有一半在**诚实**：样本 <20 的阶段，它的 p50/p95 只是噪声
      ⇒ 逐行打「⚠ 样本不足（<20），不足以归因」，并在末尾汇总提醒。
    """
    if summary is None:
        summary = snapshot()
    summary = summary or {}
    stages = summary.get("stages") or {}
    lines: list[str] = []
    total = summary.get("total_ms", 0.0)
    n_all = sum(int((v or {}).get("n") or 0) for v in stages.values())
    lines.append("耗时归因（样本合计 %d 条 ｜ 计量总时长 %.3f ms）" % (n_all, float(total)))
    lines.append("-" * 68)
    lines.append("%-26s %8s %12s %12s %12s" % ("阶段", "样本数", "p50(ms)", "p95(ms)", "max(ms)"))
    lines.append("-" * 68)
    thin: list[str] = []
    for name in sorted(stages):
        v = stages[name] or {}
        n = int(v.get("n") or 0)
        mark = ""
        if n < MIN_SAMPLES_FOR_ATTRIBUTION:
            mark = "  ⚠ 样本不足（<%d），不足以归因" % MIN_SAMPLES_FOR_ATTRIBUTION
            thin.append(name)
        lines.append("%-26s %8d %12.3f %12.3f %12.3f%s"
                     % (name, n, float(v.get("p50") or 0.0),
                        float(v.get("p95") or 0.0), float(v.get("max") or 0.0), mark))
    if not stages:
        lines.append("（还没有任何样本）")
    lines.append("-" * 68)
    if thin:
        lines.append("⚠ %d 个阶段的样本数 < %d ⇒ 其 p50/p95 **不足以归因**（别拿它下结论）：%s"
                     % (len(thin), MIN_SAMPLES_FOR_ATTRIBUTION, "、".join(thin)))
    else:
        lines.append("✓ 各阶段样本数均 ≥ %d ⇒ p50/p95 可用于归因（仍需看分布，别只看一个数）"
                     % MIN_SAMPLES_FOR_ATTRIBUTION)
    return "\n".join(lines)
