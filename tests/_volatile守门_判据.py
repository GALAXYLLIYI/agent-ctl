# -*- coding: utf-8 -*-
r"""判据 · volatile 守门（2026-10-17 22:4x，窗Z3 的规则表落地后由 DSH 建）。

治的病（Z3 拿实地代码找到、我 grep 复核过）：
  ① `SpinnerControl` 同时在 volatile 豁免名单与可交互名单 ⇒ 既豁免又能交互，
     "set 动作后核验"对它全盲（Z2 的"路径 C"不是推测，是现状）；
  ② 正则单位表缺单字母 `s`，注释却宣称命中 `3.5s` ⇒ 注释与实现漂移；
  ③ 裸关键词 `剩余` 把 `剩余 3 项` 误判成 volatile ⇒ J2 假绿的**活体靶心**。

本判据只看**纯函数**（不发鼠标、不建窗、不碰桌面），所以能进 run_all：
  A 八例分类逐条对表（含上面三处靶心）；
  B 交互节点永不豁免（SpinnerControl 是样本，防止再被加回豁免名单）；
  C 注入自证：把 SpinnerControl 塞回豁免名单 / 把单位表退回旧版 ⇒ 必须转红。

用法：
  python -X utf8 tests\_volatile守门_判据.py                 # 绿（rc=0）
  python -X utf8 tests\_volatile守门_判据.py --inject spinner  # 必红（rc=1）
  python -X utf8 tests\_volatile守门_判据.py --inject units    # 必红（rc=1）
"""
import argparse
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "src", "agentctl", "core.py")

CASES = [
    # (role, label, 期望 volatile, 说明)
    ("TextControl", "剩余 3 项", False, "③ 靶心：计数类但承载任务信息，不该豁免"),
    ("TextControl", "3.5s", True, "② 靶心：注释宣称命中，旧正则实际不命中"),
    ("TextControl", "剩余 5 min", True, "时限类靠单位命中（删了关键词也不漏）"),
    ("TextControl", "距结束 12:30", True, "时刻形"),
    ("TextControl", "第 7/20 步", False, "进程序号，稳，不该豁免"),
    ("TextControl", "共 166 题", False, "总量，稳，不该豁免"),
    ("SpinnerControl", "数量", False, "① 靶心：可交互节点永不豁免"),
    ("ProgressBarControl", "下载", True, "进度条：真 volatile"),
]


def load_core(path=CORE):
    sys.path.insert(0, os.path.join(ROOT, "src"))
    spec = importlib.util.spec_from_file_location("_core_vol", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", choices=["spinner", "units"], default=None)
    a = ap.parse_args(argv)

    m = load_core()
    if a.inject == "spinner":
        # 退回旧名单：把 SpinnerControl 塞回豁免名单
        object.__setattr__(m, "SCREEN_VOLATILE_TYPES",
                           frozenset({"ProgressBarControl", "SpinnerControl"}))
    elif a.inject == "units":
        # 退回旧正则：单位表去掉 s 与 min，关键词加回「剩余」
        import re
        object.__setattr__(m, "SCREEN_VOLATILE_RE", re.compile(
            r"(\d{1,3}:\d{1,2}(?::\d{1,2})?)"
            r"|(\d+(?:\.\d+)?\s*(?:ms|毫秒|秒|分钟|分|fps|hz|kb|mb|gb|%))"
            r"|(剩余|倒计时|已用时|用时|elapsed|remaining|countdown|timer|clock)"))

    red = []
    print("==" * 60)
    print("A · 八例分类逐条对表%s" % ("（**注入模式**，红了才说明判据有判别力）" if a.inject else ""))
    for role, label, want, why in CASES:
        got = bool(m._screen_is_volatile(role, label))
        tag = "绿" if got == want else "红"
        if got != want:
            red.append("%s/%s 期望 volatile=%s 实得 %s（%s）" % (role, label, want, got, why))
        print("  [%s] %-18s %-12r volatile=%-5s 期望=%-5s  %s"
              % (tag, role, label, got, want, why))

    print("\nB · 交互节点与 volatile 名单不相交")
    # ★ INTERACTIVE_TYPES 在 uia_tree 里，不在 core —— 2026-10-17 修：原来只看 core
    #   导致本项"验不了"（等于没验）。两份都合起来判，缺一个也不放过。
    inter = set(getattr(m, "INTERACTIVE_TYPES", set()) or set())
    try:
        sys.path.insert(0, os.path.join(ROOT, "src"))
        import agentctl.uia_tree as _ut
        inter |= set(getattr(_ut, "INTERACTIVE_TYPES", set()) or set())
    except Exception as e:
        print("  [注] uia_tree 导入失败（%s），只用 core 的名单" % str(e)[:60])
    vol = set(m.SCREEN_VOLATILE_TYPES)
    if inter:
        overlap = sorted(inter & vol)
        if overlap:
            red.append("可交互 ∩ volatile 非空：%s（既豁免又能交互 ⇒ 动作后核验全盲）" % overlap)
            print("  [红] 交集 = %s" % overlap)
        else:
            print("  [绿] 交集为空（可交互节点不被豁免）")
    else:
        print("  [注] core 没有导出 INTERACTIVE_TYPES，改看 uia_tree —— 本项验不了")

    print("\n" + "==" * 60)
    if red:
        print("结论：【红】%d 条" % len(red))
        for r in red:
            print("   - " + r)
        return 1
    print("结论：【绿】八例全对、交互与豁免名单不相交")
    return 0


if __name__ == "__main__":
    sys.exit(main())
