# -*- coding: utf-8 -*-
r"""tools\probe_type_cost.py —— **C 级（打字）耗时归因**：注入成本 vs 字符间隙

★ 为什么要有这个（2026-10-27 能力边界探测的结果）：

  C 级打字的耗时**几乎全是 `send_text` 里那个固定的字符间隙**，不是注入本身：
    · `send_unicode` 单字符            = **0.5ms**   ← 真正的注入成本
    · `send_text('hello')` 5 字符     = **63.8ms**  ← 12.8ms/字符
    · `send_text('a'×20)`  20 字符    = **254.8ms** ← 12.7ms/字符（线性）
    · `send_text('中文字')` 3 字符     = **37.9ms**  ← 12.6ms/字符（中文同价）
  ⇒ 差值 ≈ **12ms/字符**，正好等于 `act.send_text(text, gap=0.012)` 的 sleep。

  ★ 而那个 12ms 间隙是**故意留的**（act.py 的 docstring 记着「2026-10-10 记事本
    229 → 224 字」的实测坑：打太快目标程序会漏收）。⇒ **提速只能调 gap，
    不能动注入本身**（注入已经只要 0.5ms）。

★ 本脚本把这件事测成可复现的数据，并顺带测：
    ① gap 敏感性（gap=0 / 0.004 / 0.012 / 0.024 时的每字符成本）
    ② 中文 vs ASCII 每字符成本
    ③ emoji（非 BMP）是否翻倍（UTF-16 代理对 → 2 次注入 + 1 次间隙）

用法：
    python -X utf8 tools\probe_type_cost.py            # 全测
    python -X utf8 tools\probe_type_cost.py --json     # 机读

★ 它只往**记事本测试窗**（%TEMP% 临时文件）里打字，测完关掉删掉，
  不碰任何用户的真实文档。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import statistics
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "legacy_tools"))

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E


def _edits(hwnd):
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value in ("Edit", "RichEditD2DPT", "RichEdit20W"):
            n = user32.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0)
            b = ctypes.create_unicode_buffer(n + 2)
            user32.SendMessageW(h, WM_GETTEXT, n + 2, b)
            res.append((cls.value, b.value))
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return res


def _windows_of_pid(pid):
    """列出属于指定 pid 的可见顶层窗口。★ 按 pid 挑，不靠标题/顺序。

    ★ 为什么必须按 pid（2026-10-27 实测踩过）：
      Windows 11 的 **Store 版记事本是单实例 UWP** —— 所有标签页共用一个进程，
      `notepad.exe <路径>` 只是给已有进程**加一个标签页**。
      ⇒ 「我自己的测试窗口」根本不存在，而且**测试会污染别人的记事本**；
      ⇒ 按标题/列表顺序挑 ⇒ 每次挑的可能不是同一个窗口 ⇒ 测出「时通时不通」的假结论。
    """
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        if user32.IsWindowVisible(h):
            p = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == pid:
                n = user32.GetWindowTextLengthW(h)
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(h, b, n + 1)
                out.append((h, b.value))
        return True

    user32.EnumWindows(cb, 0)
    return out


def _focus(hwnd):
    """置前 + **把焦点落到编辑控件上**。

    ★ 焦点必须落在**编辑控件**而不是顶层窗口 —— 键盘事件只投给持有输入焦点的控件
      （2026-10-27 实测：只 SetForegroundWindow 顶层窗 ⇒ 字符进不去；
       焦点真在编辑控件上 ⇒ 字符正常进去）。
    """
    user32.ShowWindow(hwnd, 9)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
    fg = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(fg, None)
    cur = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(cur, tid, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    user32.AttachThreadInput(cur, tid, False)
    time.sleep(0.5)


def _focus_edit(hwnd, edit_hwnd):
    """把**输入焦点真正落到编辑控件**上（鼠标点击进去）。

    ★ 只 SetForegroundWindow(顶层窗) 不够 —— 键盘事件投给的是**持有输入焦点的控件**。
      2026-10-27 实测：焦点不在编辑控件上 ⇒ 字符一个都进不去（而 SendInput 仍返回 1）。

    ★★ 返回值 = **这次点击是否真的落在自己的编辑控件上**。
      本机有别的 agent 的窗口在前台 ⇒ 点错位置会打到**别人的窗口**上
      （2026-10-27 实测踩过：测试字符跑进了别人的记事本窗口）。
      ⇒ 点之前必须核对「坐标处的窗口」确实是自己的编辑控件；不对就**不点**。
    """
    class R(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    r = R()
    user32.GetWindowRect(edit_hwnd, ctypes.byref(r))
    x = (r.l + r.r) // 2
    y = (r.t + r.b) // 2
    # ★ 核对坐标处的窗口：必须是**自己的**编辑控件（或它的子孙窗口）
    pt = POINT(x, y)
    hit = user32.WindowFromPoint(ctypes.byref(pt))
    if hit != edit_hwnd:
        cur = hit
        for _ in range(4):
            if cur == edit_hwnd:
                break
            cur = user32.GetParent(cur)
        if cur != edit_hwnd:
            return False          # ★ 那个位置不是我的控件 ⇒ 不点（避免打到别人的窗口）
    _focus(hwnd)
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    import act as _act
    _act.send_mouse(0x0002)          # 左键按下
    time.sleep(0.06)
    _act.send_mouse(0x0000)          # 左键抬起
    time.sleep(0.35)
    return True


def _stats(s):
    s = sorted(s)
    return {"n": len(s), "min": round(s[0], 2), "p50": round(statistics.median(s), 2),
            "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2),
            "max": round(s[-1], 2), "mean": round(statistics.fmean(s), 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    import act  # noqa: E402

    # ★ 靶场用 **agentctl 自带的独立进程 WinForms 靶场**（原生 EDIT 控件 + 自己的 pid）
    #   —— 不用记事本：Store 版记事本是**单实例 UWP**，`notepad.exe <路径>` 只是加标签页，
    #   「自己的窗口」不存在，而且**会往别人的记事本里打字**（本机实测有 8 个别的 agent 的窗口）。
    tgt = subprocess.Popen(
        [sys.executable, "-X", "utf8", os.path.join(REPO, "tools", "ab_target_py.py")])
    out = {"single_char": {}, "gap_sensitivity": {}, "unicode_vs_ascii": {},
           "emoji": {}, "note": ""}
    try:
        HW = 0
        for _ in range(40):
            time.sleep(0.5)
            wins = _windows_of_pid(tgt.pid)
            if wins:
                HW = wins[0][0]
                break
        if not HW:
            print("起不了自己的靶场窗（tools\\ab_target_py.py）")
            return 2
        out["靶场"] = "pid=%s hwnd=%s（独立进程，不碰别人的窗口）" % (tgt.pid, HW)
        _focus(HW)

        # ── ① 单字符注入成本（无间隙）────────────────────────────────
        s = []
        for _ in range(10):
            t0 = time.perf_counter()
            act.send_unicode("a")
            s.append((time.perf_counter() - t0) * 1000)
            time.sleep(0.05)
        out["single_char"] = _stats(s)

        # ── ② gap 敏感性 ───────────────────────────────────────────
        for gap in (0.0, 0.004, 0.012, 0.024):
            s = []
            for _ in range(5):
                t0 = time.perf_counter()
                act.send_text("x" * 10, gap=gap)
                s.append((time.perf_counter() - t0) * 1000)
                time.sleep(0.2)
            st = _stats(s)
            st["每字符"] = round(st["p50"] / 10.0, 2)
            out["gap_sensitivity"][str(gap)] = st

        # ── ③ 中文 vs ASCII ─────────────────────────────────────────
        for label, text in (("ascii_20", "a" * 20), ("cjk_20", "中" * 20)):
            s = []
            for _ in range(5):
                t0 = time.perf_counter()
                act.send_text(text)
                s.append((time.perf_counter() - t0) * 1000)
                time.sleep(0.2)
            st = _stats(s)
            st["每字符"] = round(st["p50"] / len(text), 2)
            out["unicode_vs_ascii"][label] = st

        # ── ④ emoji（非 BMP ⇒ UTF-16 代理对）────────────────────────
        s = []
        for _ in range(4):
            t0 = time.perf_counter()
            act.send_text("😀" * 5)
            s.append((time.perf_counter() - t0) * 1000)
            time.sleep(0.2)
        st = _stats(s)
        st["每字符"] = round(st["p50"] / 5.0, 2)
        st["说明"] = "emoji 走 UTF-16 代理对 ⇒ 每个是 2 次注入 + 1 次间隙"
        out["emoji"] = st

        # ── 效果验证（证明字真的进去了，不是空跑）────────────────────
        # ★ 必须**先把焦点落到编辑控件**上，否则字符进不去 ⇒ 验证会误报「没进去」。
        # ★★ 且**必须点在自己的靶场窗口上** —— 本机有别的 agent 的窗口在前台，
        #   点错位置会打到别人的窗口上（2026-10-27 实测踩过：测试字符跑进了
        #   别人的记事本窗口）。⇒ 点之前先核对坐标处的窗口确实是自己的编辑控件。
        es = _edits(HW)
        landed = False
        if es:
            eh = es[0][0]
            landed = _focus_edit(HW, eh)
        before_v = len(es[0][1] or "") if es else 0
        act.send_text("ZZ")
        time.sleep(0.6)
        es2 = _edits(HW)
        after_v = len(es2[0][1] or "") if es2 else 0
        out["验证_控件内容"] = (es2[0][1][-40:] if es2 else "?")
        out["验证_字符是否落进"] = (after_v > before_v)
        out["验证_点击是否落在自己的控件上"] = landed
        if not landed:
            out["note"] += ("｜⚠ 点击**没落在自己的编辑控件上**（可能被别的窗口挡住）"
                            "⇒ 耗时数据仍有效（注入调用本身的成本），但「效果」这一项不可信，"
                            "**且不要在这种状态下继续打字**（会打到别人的窗口）。")
        elif not out["验证_字符是否落进"]:
            out["note"] += ("｜⚠ 本轮**字符没落进控件** ⇒ 耗时数据仍有效"
                            "（注入调用本身的成本），但「效果」这一项不可信。")
        else:
            out["note"] += "｜★ 字符已验证落进自己的控件（读回可见）"
    finally:
        tgt.kill()
        time.sleep(0.5)

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("== C 级打字耗时归因 ==")
        print()
        print("① 单字符注入成本        %s" % json.dumps(out["single_char"], ensure_ascii=False))
        print()
        print("② gap 敏感性（10 字符）")
        for g, st in out["gap_sensitivity"].items():
            print("   gap=%-6s p50=%-8s 每字符=%sms" % (g, st["p50"], st["每字符"]))
        print()
        print("③ 中文 vs ASCII（20 字符）")
        for k, st in out["unicode_vs_ascii"].items():
            print("   %-12s p50=%-8s 每字符=%sms" % (k, st["p50"], st["每字符"]))
        print()
        print("④ emoji（5 个）        p50=%s  每字符=%sms" % (
            out["emoji"]["p50"], out["emoji"]["每字符"]))
        print()
        print("   验证：控件内容尾部 = %r" % out["验证_控件内容"])
        print()
        print(out["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
