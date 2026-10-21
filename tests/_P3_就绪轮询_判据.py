# -*- coding: utf-8 -*-
r"""tests\_P3_就绪轮询_判据.py —— 第 M7 条判据：**固定 sleep 链 → 就绪轮询**。

治的病（派单 M7）：`legacy_tools/act.py` 每次动作都有一串**固定 sleep**
（点击 250ms 反竞态 + 450ms 动作后；填字段 350+200+500ms），
再加**每动作 spawn 一个 python 子进程**（冷启动 + import ≈ 200ms+）
⇒ **单动作 0.8~1.3 s**，是感知轮（~100ms）的 8~15 倍。

本判据断言七条（先红后绿；`--inject` 必须变红）：

  **A 判据层（合成图，确定性，不需授权）**
    A1 **真变化 ⇒ 必须判变**（不许漏动）
    A2 **光标闪烁 ⇒ 不得判变**（不误动）
    A3 **连续 2 帧滞回**：只闪一帧的变化 ⇒ 不许立刻算变
    A4 **动画区（每帧都变）⇒ 永远「没测出来」**（不许判「没变」）
    A5 **超时 ⇒ 三值里的 None**，且**不得**当成功

  **B 真机层（真实窗口）**
    B1 **界面已稳定 ⇒ 不得等满固定 sleep**（断言耗时上限）
    B2 **界面未稳定 ⇒ 不得提前发动作**

  **C 两边口径**
    C1 `legacy_tools/act.py` 的判据常量 **必须**与 `agentctl/framecache.py` 一致
       （两单共用一套阈值，否则会出现「core.py 说没变、act.py 说变了」）

退出码：0=绿；1=红；2=验不了（起不出窗口 / 环境限制 —— **2 不许当绿读**）。
注入：`--inject` —— 把 act.py 的 `POLL_HYSTERESIS` 打成 1（去掉时间滞回）
      ⇒ A3/A4 必须失败 ⇒ 本判据红。
"""
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import framecache                            # noqa: E402

GREEN, RED, SKIP = [], [], []
ACT_SRC = os.path.join(ROOT, "legacy_tools", "_src", "act.py")


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def finish():
    print("\n" + "=" * 66, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 有检查项没做成（见上）；★ 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】就绪轮询不成立（见上）", flush=True)
        return 1
    print("结论：【绿】就绪轮询成立：稳了就走、变了就停、光标不误动、超时报没测出来", flush=True)
    return 0


def load_act():
    """加载**真源**里的 act.py（模块级自检闸在环境正常时不会拦）。"""
    spec = importlib.util.spec_from_file_location("_m7_act", ACT_SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── 合成图工具 ─────────────────────────────────────────────────────────
def synth(w=640, h=400, bg=250):
    import numpy as np
    from PIL import Image
    a = np.full((h, w, 3), bg, dtype=np.uint8)
    for y in range(0, h, 3):
        a[y:y + 1, 30:300] = 40
    return Image.fromarray(a)


def inject(img, x0, y0, w, h, val=0):
    import numpy as np
    from PIL import Image
    a = np.asarray(img).copy()
    a[y0:y0 + h, x0:x0 + w] = val
    return Image.fromarray(a)


def judge_layer(act):
    print("\n── A 判据层（合成图）──", flush=True)
    base = synth()
    gb = act._poll_gray(base)
    if gb is None:
        rec("A 层准备", False, "合成图降采样失败 ⇒ 本轮验不了", skip=True)
        return

    # A1 真变化 ⇒ 必须判变
    d = act._img_diff(gb, act._poll_gray(inject(base, 200, 150, 16, 24)))
    rec("A1 真变化 ⇒ 必须判变", d.get("changed") is True,
        "changed=%r ratio=%s" % (d.get("changed"), d.get("ratio")))

    # A2 光标闪烁 ⇒ 不得判变
    caret_ok, det = True, []
    for (w, h) in ((5, 16), (7, 24), (2, 20)):
        dc = act._img_diff(gb, act._poll_gray(inject(base, 200, 150, w, h)))
        det.append("%dx%d→%s" % (w, h, dc.get("changed")))
        if dc.get("changed") is not False:
            caret_ok = False
    rec("A2 光标闪烁 ⇒ 不得判变", caret_ok, "；".join(det))

    # A3 连续 2 帧滞回（只闪一帧不算变）
    tr = framecache.ChangeTracker(need=framecache.HYSTERESIS)
    d1 = tr.feed(base, inject(base, 200, 150, 16, 24))
    d2 = tr.feed(base, inject(base, 200, 150, 16, 24))
    rec("A3 只闪一帧的变化 ⇒ 不许立刻算变（要连续 %d 帧）" % framecache.HYSTERESIS,
        d1.get("changed") is False and d2.get("changed") is True,
        "第1帧 changed=%r ／ 第2帧 changed=%r" % (d1.get("changed"), d2.get("changed")))

    # A4 动画区 ⇒ 永远「没测出来」
    seq = [inject(base, 120 + i * 20, 150, 16, 24) for i in range(8)]
    st = {"i": 0}

    def _anim(_h):
        img = seq[st["i"] % len(seq)]
        st["i"] += 1
        return img

    r = act._wait_ready(0, base, budget_ms=120, need=act.POLL_HYSTERESIS, capture=_anim)
    rec("A4 动画区（每帧都变）⇒ 不许判「就绪/没变」", r.get("就绪") is not True,
        "就绪=%r 轮次=%r 耗时=%.0f ms" % (r.get("就绪"), r.get("轮次"), r.get("耗时ms") or 0))

    # A5 超时 ⇒ None（没测出来），不是 False
    def _dead(_h):
        return None

    r2 = act._wait_ready(0, base, budget_ms=60, need=act.POLL_HYSTERESIS, capture=_dead)
    rec("A5 取不到画面 ⇒ 就绪必须是 None（没测出来，不许当「没变」）",
        r2.get("就绪") is None, "就绪=%r" % r2.get("就绪"))
    r3 = act._wait_effect(0, base, budget_ms=60, need=act.POLL_HYSTERESIS, capture=_dead)
    rec("A5b 取不到画面 ⇒ 生效必须是 None（不许当「没生效」）",
        r3.get("生效") is None, "生效=%r" % r3.get("生效"))


def judge_real(act):
    print("\n── B 真机层（真实窗口）──", flush=True)
    import ctypes
    import subprocess
    u = ctypes.windll.user32
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

    def notepads():
        import win32gui
        out = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(h, _):
            c = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, c, 64)
            if c.value == "Notepad" and u.IsWindowVisible(h):
                out.append(int(h))
            return True
        u.EnumWindows(cb, 0)
        return out

    before = set(notepads())
    subprocess.Popen(["notepad.exe"])
    time.sleep(2.2)
    hs = [x for x in notepads() if x not in before][:1]
    if not hs:
        rec("B 层准备", False, "没起成记事本 ⇒ 本轮验不了", skip=True)
        return
    hwnd = hs[0]
    u.SetWindowPos(hwnd, -1, 260, 220, 1000, 760, 0x0040)   # 置顶
    u.SetForegroundWindow(hwnd)
    time.sleep(1.2)
    try:
        base_img = act._poll_sample(hwnd)
    except Exception as e:
        rec("B 层准备", False, "取不到画面：%s ⇒ 本轮验不了" % e, skip=True)
        return
    if base_img is None:
        rec("B 层准备", False, "取不到画面 ⇒ 本轮验不了", skip=True)
        return

    # B1 界面已稳定 ⇒ 不得等满固定 sleep（老口径是 250 ms）
    t0 = time.perf_counter()
    r = act._wait_ready(hwnd, base_img, budget_ms=act.POLL_BUDGET_MS)
    ms = (time.perf_counter() - t0) * 1000
    rec("B1 界面已稳定 ⇒ 不得等满固定 sleep（老口径 250 ms）",
        bool(r.get("就绪")) and ms < 250,
        "就绪=%r 轮次=%r 耗时=%.0f ms（内部报 %.0f ms）"
        % (r.get("就绪"), r.get("轮次"), ms, r.get("耗时ms") or 0))

    # B2 界面未稳定 ⇒ 不得提前「就绪」（注入一个每帧都变的源）
    seq = [inject(base_img, 100 + i * 15, 100, 20, 30) for i in range(8)]
    st = {"i": 0}

    def _anim(_h):
        img = seq[st["i"] % len(seq)]
        st["i"] += 1
        return img

    r2 = act._wait_ready(hwnd, base_img, budget_ms=200, need=act.POLL_HYSTERESIS,
                         capture=_anim)
    rec("B2 界面未稳定（每帧都变）⇒ 不得判「就绪」", r2.get("就绪") is not True,
        "就绪=%r 轮次=%r 耗时=%.0f ms" % (r2.get("就绪"), r2.get("轮次"),
                                         r2.get("耗时ms") or 0))
    # B3 **变化即停的发现延迟**：界面真的变了 ⇒ 轮询多久就发现
    #    （老口径：固定等 450 ms ⇒ 无论变没变都等满）
    import numpy as np
    seq2 = [inject(base_img, 100 + i * 10, 100, 24, 30) for i in range(4)]
    st2 = {"i": 0}

    def _later(_h):
        # 第 1 帧还是原样，第 2 帧开始变成「另一个样子」⇒ 模拟界面在 ~50 ms 后变了
        img = base_img if st2["i"] < 1 else seq2[st2["i"] % len(seq2)]
        st2["i"] += 1
        return img

    t0 = time.perf_counter()
    r3 = act._wait_effect(hwnd, base_img, budget_ms=800, need=act.POLL_HYSTERESIS,
                           capture=_later)
    ms3 = (time.perf_counter() - t0) * 1000
    rec("B3 界面变了 ⇒ 轮询**很快就发现**（远快于固定 sleep 450 ms）",
        bool(r3.get("生效")) and ms3 < 450,
        "生效=%r 轮次=%r 发现延迟=%.0f ms（老口径固定等 450 ms ⇒ 省 %.0f ms）"
        % (r3.get("生效"), r3.get("轮次"), ms3, 450 - ms3))

    try:
        u.PostMessageW(hwnd, 0x0010, 0, 0)
    except Exception:
        pass


def judge_constants(act):
    print("\n── C 两边口径 ──", flush=True)
    pairs = [("POLL_STEP", act.POLL_STEP, framecache.DIFF_STEP),
             ("POLL_CARET_PX", act.POLL_CARET_PX, framecache.DIFF_CARET_PX),
             ("POLL_MIN_AREA", act.POLL_MIN_AREA, framecache.DIFF_MIN_AREA),
             ("POLL_CARET_ASPECT", act.POLL_CARET_ASPECT, framecache.DIFF_CARET_ASPECT),
             ("POLL_HYSTERESIS", act.POLL_HYSTERESIS, framecache.HYSTERESIS)]
    bad = [(n, a, b) for n, a, b in pairs if a != b]
    rec("C1 act.py 与 framecache 的判据常量**完全一致**（两单不许打架）", not bad,
        "；".join("%s=%r vs %r" % (n, a, b) for n, a, b in pairs)
        + ("　★不一致★" if bad else ""))


def main():
    inject = "--inject" in sys.argv
    try:
        act = load_act()
    except SystemExit as e:
        rec("加载 act.py 真源", False, "act.py 的启动自检闸拦住了（rc=%r）⇒ 本轮验不了" % e,
            skip=True)
        return finish()
    except Exception as e:
        rec("加载 act.py 真源", False, "%s: %s ⇒ 本轮验不了" % (type(e).__name__, e),
            skip=True)
        return finish()
    if inject:
        # ★ 注入：**把就绪轮询整个关掉**（退回「固定 sleep 250 ms + 不给就绪信号」）
        #   ⇒ B1（界面已稳定也不许等满固定 sleep）必须失败，C1（两边常量一致）也必须失败
        #   ⇒ 证明判据盯的是**行为**，不是「有没有写这个函数」。
        import time as _t

        def _no_poll(hwnd, base_img, budget_ms=None, need=None, capture=None):
            _t.sleep(0.25)          # ★ 老口径：固定等 250 ms
            return {"就绪": None, "生效": None, "轮次": 0, "耗时ms": 250.0, "图": None}

        act._wait_ready = _no_poll
        act._wait_effect = _no_poll
        act.POLL_HYSTERESIS = 1
        print("★ 注入模式：就绪轮询被关掉（退回固定 sleep 250 ms）+ 滞回打成 1", flush=True)

    judge_layer(act)
    judge_real(act)
    judge_constants(act)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
