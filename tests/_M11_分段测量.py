# -*- coding: utf-8 -*-
r"""M11 · 动作链分段测量（**只测量，不改产品行为**）

▌本文件是**测量器**，不是判据。判据在 `tests\_P1_动作链分段_判据.py`。
▌**插桩只发生在本文件里**（monkeypatch 本进程里的模块属性，跑完还原）⇒
  `src/` **一个字节都没动** ⇒ 不需要备份/MD5/diff，也不可能留残留。

▌测什么：一次生产路径的 `ctl_click`（core.dispatch → 子进程 act.py → 回读 → 落盘），
  拆成**互斥**的分段；外加 act 侧等待的**直测**（回答「是不是同一件事等了两遍」）。

▌目标窗口：**本文件自己开的记事本**（不碰用户正在用的任何窗口）。

用法：
    python -X utf8 tests\_M11_分段测量.py --out work\_M11_分段输出.txt
    python -X utf8 tests\_M11_分段测量.py --json      # 机器可读（判据用）
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agentctl import core as core_mod          # noqa: E402
from agentctl import framecache               # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


# ────────────────────────────── 窗口小工具 ──────────────────────────────
def _enum_windows():
    out = []
    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        if user32.IsWindowVisible(h):
            out.append(int(h))
        return True
    user32.EnumWindows(cb, 0)
    return out


def _title(h):
    n = user32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def _cls(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def open_own_notepad():
    """开**自己的**记事本，返回 (hwnd, proc)。找不到可见窗口就如实报错。"""
    before = set(_enum_windows())
    p = subprocess.Popen(["notepad.exe"])
    hwnd = 0
    for _ in range(40):
        time.sleep(0.25)
        now = set(_enum_windows())
        new = [h for h in now - before if _cls(h) == "Notepad"]
        if new:
            hwnd = new[0]
            break
    if not hwnd:
        try:
            p.kill()
        except Exception:
            pass
        raise RuntimeError("没能开出自己的记事本（无新可见 Notepad 窗口）")
    return hwnd, p


def bring_to_front(hwnd):
    """置前。直调 SetForegroundWindow 常常返回 False ⇒ 用 AttachThreadInput 绕。"""
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    if int(user32.GetForegroundWindow()) == int(hwnd):
        return "SetForegroundWindow"
    fg = int(user32.GetForegroundWindow())
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    my_tid = kernel32.GetCurrentThreadId()
    try:
        user32.AttachThreadInput(my_tid, fg_tid, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        user32.AttachThreadInput(my_tid, fg_tid, False)
    time.sleep(0.3)
    return "AttachThreadInput" if int(user32.GetForegroundWindow()) == int(hwnd) else "**置前失败**"


# ────────────────────────────── 计时器（只在本进程） ──────────────────────────────
class Ticker:
    """记录事件：(名字, 起点, 终点)。互斥分段靠它切时间轴。"""

    def __init__(self):
        self.ev = []

    def add(self, name, t0, t1, extra=None):
        self.ev.append({"名称": name, "t0": t0, "t1": t1, "ms": round((t1 - t0) * 1000, 3),
                        "额外": extra or {}})

    def mark(self, name, extra=None):
        self.ev.append({"名称": name, "t0": time.perf_counter(), "t1": None, "ms": 0.0,
                        "额外": extra or {}})


def _wrap(obj, name, ticker, label, on=None):
    """给 obj.name 包一层计时（返回原函数以便还原）。"""
    orig = getattr(obj, name)

    def w(*a, **kw):
        t0 = time.perf_counter()
        try:
            return orig(*a, **kw)
        finally:
            t1 = time.perf_counter()
            extra = on(a, kw) if on else None
            ticker.add(label, t0, t1, extra)
    setattr(obj, name, w)
    return orig


# ────────────────────────────── 主测量 ──────────────────────────────
def measure_click(hwnd, xy, label, inject=False):
    """跑一次生产 `ctl_click`，返回一张互斥分段账本。"""
    ticker = Ticker()
    restores = []
    mark = {"legacy_start": None}
    try:
        # ① 子进程（spawn+import+act 全部工时）
        def on_legacy(a, kw):
            return {"首次调用": mark["legacy_start"] is None}
        restores.append((core_mod, "_legacy",
                         _wrap(core_mod, "_legacy", ticker, "子进程 spawn+import+act 工时", on_legacy)))
        # 记下**子进程被调用的那一刻**（用于直接量「前置」，不做减法倒推）
        _orig_legacy = core_mod._legacy

        def _legacy_timed(*a, **kw):
            if mark["legacy_start"] is None:
                mark["legacy_start"] = time.perf_counter()
            return _orig_legacy(*a, **kw)
        core_mod._legacy = _legacy_timed
        restores.append((core_mod, "_legacy", _orig_legacy))
        # ② 动作后回读（core 侧 wait_change 及其兜底抓帧）
        restores.append((core_mod, "_act_after",
                         _wrap(core_mod, "_act_after", ticker, "动作后回读(core wait_change)")))
        # ③ 落盘 diff（PNG + diff）
        restores.append((core_mod, "_act_diff_store",
                         _wrap(core_mod, "_act_diff_store", ticker, "落盘 PNG+diff")))
        # ④ UIA 元素查找 + 身份复核
        restores.append((core_mod, "_eid_recheck",
                         _wrap(core_mod, "_eid_recheck", ticker, "UIA 元素查找+身份复核")))
        # ⑤ 抓帧（客户区）：记次数与总耗时；phase 用来分「动作前」与「变化探测」
        state = {"phase": "pre"}

        def on_cap(args, kw):
            return {"phase": state["phase"]}
        restores.append((framecache, "capture_client",
                         _wrap(framecache, "capture_client", ticker, "抓帧(客户区)", on_cap)))
        # ⑥ core 侧 wait_change 单独记（含轮次）
        def on_wait(args, kw):
            return {"预算ms": kw.get("budget_ms", framecache.WAIT_BUDGET_MS)}
        restores.append((framecache, "wait_change",
                         _wrap(framecache, "wait_change", ticker, "core wait_change(含轮次)", on_wait)))

        # ── 真正跑一次生产点击（每轮都重新置前 + 重新观测）──
        sv = ""
        res = {}
        for attempt in range(4):
            try:
                bring_to_front(hwnd)          # 前台可能被别人抢走 ⇒ 每轮重新置前
            except Exception:
                pass
            screen = core_mod.dispatch("ctl_screen", {"hwnd": int(hwnd)}, actor="M11分段测量")
            _sd = screen.get("data") or {}
            sv = (_sd.get("结果") or _sd).get("screen_version") or ""
            # ★ 「画面到底变了没」的**地面真值**：两张都在**计时窗口之外**取
            pre_img = framecache.capture_client(int(hwnd))
            framecache.reset_stats()
            t_start = time.perf_counter()
            state["phase"] = "pre"
            res = core_mod.dispatch("ctl_click", {"hwnd": int(hwnd), "xy": list(xy),
                                                  "frame_id": sv, "allow_foreground": True},
                                    actor="M11分段测量")
            t_end = time.perf_counter()
            state["phase"] = "post"
            total_ms = (t_end - t_start) * 1000
            stats = dict(framecache.STATS)
            post_img = framecache.capture_client(int(hwnd))
            # ★ 凭据过期（code=12）/ 前台被抢（code=5）⇒ **重观测重试**（不是产品缺陷）
            if res.get("code") in (12, 5) and attempt < 3:
                continue
            break
        try:
            # ★ 地面真值：**用产品同一把尺子**（framecache.ChangeTracker = 指纹口径），
            #   但由**我**拿自己抓的 before/after 两帧事后算 —— **不读产品的返回值**。
            #   （全分辨率 + 光标滤除的口径对光标不敏感，会与产品的指纹口径打架 ⇒ 只作参考。）
            if pre_img is not None and post_img is not None:
                truth = framecache.ChangeTracker().feed(pre_img, post_img)
                truth_changed = truth.get("changed")
            else:
                truth_changed = None
        except Exception:
            truth_changed = None
    finally:
        for obj, name, orig in restores:
            setattr(obj, name, orig)

    # ── 互斥分段：**每段都是自己打点量出来的**（不靠减法倒推）──
    ev = sorted([e for e in ticker.ev if e["t1"] is not None], key=lambda e: e["t0"])
    legacy_ms = sum(e["ms"] for e in ev if e["名称"] == "子进程 spawn+import+act 工时")
    after_ms = sum(e["ms"] for e in ev if e["名称"] == "动作后回读(core wait_change)")
    diff_ms = sum(e["ms"] for e in ev if e["名称"] == "落盘 PNG+diff")
    uia_ms = sum(e["ms"] for e in ev if e["名称"] == "UIA 元素查找+身份复核")
    cap_pre = sum(e["ms"] for e in ev if e["名称"] == "抓帧(客户区)" and e["额外"].get("phase") == "pre")
    cap_post = sum(e["ms"] for e in ev if e["名称"] == "抓帧(客户区)" and e["额外"].get("phase") == "post")
    wait_ms = sum(e["ms"] for e in ev if e["名称"] == "core wait_change(含轮次)")

    # ★ 「前置」= 从端到端起点到**子进程被调用那一刻**（直接量，不倒推）
    pre_ms = 0.0
    if mark["legacy_start"] is not None:
        pre_ms = max(0.0, (mark["legacy_start"] - t_start) * 1000)
    # ★ **未归因**＝端到端 − 各实测段之和（**单列，不许当成分段**）
    seg_sum = pre_ms + legacy_ms + after_ms + diff_ms
    unattributed_ms = total_ms - seg_sum
    segs = [
        {"段": "① 前置（门禁/凭据/元素查找/复核树）", "ms": round(pre_ms, 1),
         "说明": "从端到端起点到子进程被调用那一刻（**直接打点**）；含 UIA 元素查找+身份复核 %.1f ms" % uia_ms},
        {"段": "② 子进程 spawn+import+act 全部工时", "ms": round(legacy_ms, 1),
         "说明": "★ 内含 act 侧 spawn/import/UIA/注入/等待（子表见「act 侧子表」）"},
        {"段": "③ 动作后回读（core wait_change）", "ms": round(after_ms, 1),
         "说明": "含变化探测抓帧 %.1f ms；wait_change 本体 %.1f ms；轮次 %s"
                 % (cap_post, wait_ms, stats.get("变化即停轮次"))},
        {"段": "④ 落盘 PNG+diff", "ms": round(diff_ms, 1), "说明": ""},
    ]
    return {
        "用例": label,
        "端到端ms": round(total_ms, 1),
        "分段和ms": round(seg_sum, 1),
        "未归因ms": round(unattributed_ms, 1),
        "未归因pct": round(abs(unattributed_ms) / total_ms * 100, 2) if total_ms else None,
        "误差pct": round(abs(seg_sum - total_ms) / total_ms * 100, 2) if total_ms else None,
        "分段": segs,
        "抓帧次数": {"动作前": int(stats.get("动作前抓帧") or 0),
                     "变化探测": int(stats.get("指纹探测") or 0),
                     "动作后留帧": int(stats.get("动作后留帧") or 0)},
        "变化即停轮次": stats.get("变化即停轮次"),
        "结果": {"ok": bool(res.get("ok")), "code": res.get("code"),
                 "warning": (res.get("warning") or "")[:160]},
        "画面真变了没": truth_changed,
        "原始事件": [{"名称": e["名称"], "ms": e["ms"], "额外": e["额外"]} for e in ev],
    }


# ────────────────────────────── act 侧等待「直测」 ──────────────────────────────
def measure_act_waits(hwnd):
    """**直接**调 act.py 的 `_wait_effect` / `_wait_ready`（不经闸门），
    量它们在「画面会变」与「画面不变」两种情形下各花多少 ms。

    ★ 为什么能直测：这两个是**纯函数**（只看窗口画面），不碰闸门 ⇒ 不用租约。
    ★ 画面怎么变：**只用键盘**（往记事本里打一个字符）⇒ **不移动用户的鼠标**。
    """
    legacy_home = getattr(core_mod, "LEGACY", "") or ""
    sys.path.insert(0, legacy_home)
    import act as legacy_act                                    # noqa: E402
    out = {"legacy_home": legacy_home}

    def _poll_cost(n=5):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            legacy_act._poll_sample(int(hwnd))
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        return round(ts[len(ts) // 2], 2)

    out["act_每轮采样成本ms"] = _poll_cost()

    # 画面会变：后台线程 120ms 后打一个字符（不动鼠标）
    def _type_later():
        time.sleep(0.12)
        try:
            legacy_act.send_unicode("M")
        except Exception:
            pass

    # ① 画面会变
    base = legacy_act._poll_base(int(hwnd))
    import threading
    th = threading.Thread(target=_type_later, daemon=True)
    t0 = time.perf_counter()
    th.start()
    r = legacy_act._wait_effect(int(hwnd), base)
    out["act_wait_effect_画面会变_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["act_wait_effect_会变_返回"] = {k: r.get(k) for k in ("changed", "轮次") if k in r}

    # ② 画面不变（什么都不做 ⇒ 等满预算）
    base = legacy_act._poll_base(int(hwnd))
    t0 = time.perf_counter()
    r = legacy_act._wait_effect(int(hwnd), base)
    out["act_wait_effect_画面不变_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["act_wait_effect_不变_返回"] = {k: r.get(k) for k in ("changed", "轮次") if k in r}

    # ③ core 侧 framecache.wait_change 直测（不变 / 变）
    before = framecache.capture_client(int(hwnd))
    t0 = time.perf_counter()
    rr = framecache.wait_change(int(hwnd), before, budget_ms=framecache.WAIT_BUDGET_MS,
                                 poll_ms=framecache.WAIT_POLL_MS)
    out["core_wait_change_画面不变_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["core_wait_change_不变_返回"] = {"changed": rr.get("changed"), "轮次": rr.get("轮次")}

    def _type_later2():
        time.sleep(0.12)
        try:
            legacy_act.send_unicode("M")
        except Exception:
            pass
    th = threading.Thread(target=_type_later2, daemon=True)
    t0 = time.perf_counter()
    th.start()
    rr = framecache.wait_change(int(hwnd), before, budget_ms=framecache.WAIT_BUDGET_MS,
                                 poll_ms=framecache.WAIT_POLL_MS)
    out["core_wait_change_画面会变_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["core_wait_change_会变_返回"] = {"changed": rr.get("changed"), "轮次": rr.get("轮次")}

    # ④ act 侧 **UIA 查找**（按名找控件 = 坐标点击路径里真正用的那个函数）
    uia_ts = []
    for _ in range(3):
        t0 = time.perf_counter()
        try:
            legacy_act.find_control(int(hwnd), "编辑")
        except Exception:
            pass
        uia_ts.append((time.perf_counter() - t0) * 1000)
    uia_ts.sort()
    out["act_UIA查找_find_control_中位ms"] = round(uia_ts[len(uia_ts) // 2], 2)

    # ⑤ act 侧 **注入**（SendInput 本身；零位移绝对移动）
    inj_ts = []
    _orig_send = legacy_act.send_mouse

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _u32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
    _u32.GetCursorPos.restype = ctypes.c_bool
    _u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _u32.GetSystemMetrics.restype = ctypes.c_int

    def _send_timed(*a, **kw):
        t0 = time.perf_counter()
        try:
            return _orig_send(*a, **kw)
        finally:
            inj_ts.append((time.perf_counter() - t0) * 1000)
    legacy_act.send_mouse = _send_timed
    try:
        p3 = _POINT()
        if _u32.GetCursorPos(ctypes.byref(p3)):
            sw = _u32.GetSystemMetrics(0) or 1
            sh = _u32.GetSystemMetrics(1) or 1
            legacy_act.send_mouse(0x8000 | 0x0001,     # MOUSEEVENTF_ABSOLUTE | MOVE
                                 int((p3.x * 65535) / max(1, sw - 1)),
                                 int((p3.y * 65535) / max(1, sh - 1)), 0)
    finally:
        legacy_act.send_mouse = _orig_send
    out["act_注入_send_mouse_ms"] = round(inj_ts[0], 2) if inj_ts else None
    out["act_注入_次数"] = len(inj_ts)

    # ⑥ act 侧 **_wait_ready**（动作**前**的就绪等待；与 _wait_effect 是两回事）
    base = legacy_act._poll_base(int(hwnd))
    t0 = time.perf_counter()
    rr = legacy_act._wait_ready(int(hwnd), base, budget_ms=250)
    out["act_wait_ready_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["act_wait_ready_返回"] = {k: rr.get(k) for k in ("就绪", "轮次", "耗时ms") if k in rr}
    return out


# ────────────────────────────── 跑一遍 ──────────────────────────────
def run_all(rounds=1, out_path=None):
    ledger = {"开始时间": time.strftime("%Y-%m-%d %H:%M:%S"), "轮次": rounds, "用例": []}
    hwnd, proc = open_own_notepad()
    ledger["记事本hwnd"] = int(hwnd)
    try:
        ledger["置前方式"] = bring_to_front(hwnd)
        if "失败" in ledger["置前方式"]:
            ledger["环境受阻"] = "置前失败 ⇒ 生产点击会被 act.py 的 ensure_foreground 拒（rc=5）"
        # 租约（写动作要）
        core_mod.dispatch("ctl_lease", {"action": "acquire", "scopes": "ABC",
                                         "actor": "M11分段测量"}, actor="M11分段测量")
        # 记事本正文区坐标（客户区中部）
        cl = framecache.client_origin(int(hwnd))
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        xy = (int(cl[0] + 200), int(cl[1] + 150)) if cl else (int(sw / 2), int(sh / 2))
        ledger["点击坐标"] = list(xy)
        ledger["act_侧直测"] = measure_act_waits(hwnd)
        for i in range(rounds):
            ledger["用例"].append(measure_click(hwnd, xy, "第%d轮·点正文区(画面多半不变)" % (i + 1)))
    finally:
        try:
            proc.kill()
        except Exception:
            pass
    ledger["结束时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
    txt = json.dumps(ledger, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
    return ledger, txt


def main() -> int:
    ap = argparse.ArgumentParser(description="M11 动作链分段测量")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "_M11_分段输出.txt"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    led, txt = run_all(a.rounds, a.out)
    if a.json:
        print(txt)
    else:
        for c in led["用例"]:
            print("== %s ｜ 端到端 %.1f ms ｜ 分段和 %.1f ms ｜ 误差 %.2f%%"
                  % (c["用例"], c["端到端ms"], c["分段和ms"], c["误差pct"] or 0))
            for s in c["分段"]:
                print("   %-38s %8.1f ms  %s" % (s["段"], s["ms"], s["说明"]))
        print(json.dumps(led.get("act_侧直测", {}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
