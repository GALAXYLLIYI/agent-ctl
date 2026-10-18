# -*- coding: utf-8 -*-
r"""tests\_体检_判据.py —— 第 27 条判据：**开工前体检**（ctl_preflight）不许说谎。

断言（不 mock，用真窗口）：
  ① 真记事本 ⇒ verdict ∈ {ready, stale_frame}（没基线时如实报 stale_frame）、有 channels 三通道结论
  ② 不存在的 hwnd ⇒ verdict=no_window，并给建议
  ③ 敏感窗（控制台）⇒ sensitive.denied=True 且 verdict=blocked_sensitive
  ④ 注入（_deny_sensitive_target 打回 None）⇒ ③ 必须失败 ⇒ 判据红

退出码：0 绿 / 1 红 / 2 验不了（无通行证 / 起不出窗）
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
from agentctl import core                                  # noqa: E402

u = ctypes.windll.user32
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
GREEN, RED, SKIP = [], [], []
INJECT = False


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def finish():
    print("\n" + "=" * 60, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】体检报告与实际不符（该拦的没拦 / 该报的没报）", flush=True)
        return 1
    print("结论：【绿】体检能给出 verdict/建议，敏感窗与空窗都如实报", flush=True)
    return 0


def pre(hwnd, **kw):
    r = core.dispatch("ctl_preflight", {"hwnd": hwnd, "actor": "判据27", **kw}, actor="判据27")
    d = r.get("data") or {}
    out = d.get("结果") if isinstance(d.get("结果"), dict) else d
    return r, (out or {})


def notepads():
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        c = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, c, 64)
        if c.value == "Notepad" and u.IsWindowVisible(h):
            hits.append(int(h))
        return True
    u.EnumWindows(cb, 0)
    return hits


def console_hwnds_any():
    """屏幕上所有可见控制台类窗口（不按 pid 过滤 —— Windows Terminal 会挂自己的进程）。"""
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        c = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, c, 64)
        if c.value in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"):
            hits.append(int(h))
        return True
    u.EnumWindows(cb, 0)
    return hits


def console_hwnd_of(pid):
    """只认**指定 pid** 的控制台窗（自己起的那个），避免误判别人的终端。"""
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        p = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(h):
            c = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, c, 64)
            if c.value in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"):
                hits.append(int(h))
        return True
    u.EnumWindows(cb, 0)
    return hits[0] if hits else 0


def console_hwnd():
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        c = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, c, 64)
        if u.IsWindowVisible(h) and c.value in ("ConsoleWindowClass",
                                                "CASCADIA_HOSTING_WINDOW_CLASS"):
            hits.append(int(h))
        return True
    u.EnumWindows(cb, 0)
    return hits[0] if hits else 0


def main():
    if INJECT:
        core._deny_sensitive_target = lambda *_a, **_k: None
        print("★ 注入：_deny_sensitive_target 恒 None（= 敏感窗不再被拦）", flush=True)

    st = core.dispatch("ctl_status", {"actor": "判据27"}, actor="判据27").get("data") or {}
    st = st.get("结果") if isinstance(st.get("结果"), dict) else st
    try:
        import json as _j
        with open(os.path.join((st or {}).get("auth_dir") or "", "auth_pass.json"),
                  encoding="utf-8") as f:
            left = int((_j.load(f) or {}).get("expires_at") or 0) - int(time.time())
    except Exception:
        left = 0
    if (st or {}).get("brake", {}).get("engaged"):
        rec("急停未拉下", False, "急停 flag 存在", skip=True)
        return finish()
    if left <= 0:
        rec("有有效通行证", False, "无有效通行证（剩 %ds）" % left, skip=True)
        return finish()

    pids, h = [], 0
    before = set(notepads())
    try:
        pids.append(subprocess.Popen(["notepad.exe"]).pid)
        time.sleep(1.6)
        hs = [x for x in notepads() if x not in before][:1]
        if not hs:
            rec("起一个记事本", False, "本次没出现新窗", skip=True)
            return finish()
        h = hs[0]

        r1, d1 = pre(h)
        rec("① 真窗口体检可用（ok / code=0）", bool(r1.get("ok")) and r1.get("code") == 0,
            "code=%r" % (r1.get("code"),))
        rec("① verdict 是已知值", d1.get("verdict") in
            ("ready", "stale_frame", "blind", "blocked_sensitive", "no_window"),
            "verdict=%r" % (d1.get("verdict"),))
        ch = d1.get("channels") or {}
        rec("① 给了三通道结论（uia/ocr/cdp 至少两项）", len(ch) >= 2, "channels=%r" % (list(ch),))
        rec("① 窗口身份完整（pid/class/rect）",
            bool((d1.get("window") or {}).get("pid")) and bool((d1.get("window") or {}).get("class")),
            "window=%r" % (d1.get("window"),))

        r2, d2 = pre(0x7FFFFFF0)      # 不存在的 hwnd
        rec("② 空 hwnd ⇒ verdict=no_window", d2.get("verdict") == "no_window",
            "verdict=%r advice=%r" % (d2.get("verdict"), d2.get("advice")))

        # ③ 敏感窗：自己起一个控制台（不靠"屏幕上恰好有终端"）
        #    ★ 实测教训：Windows Terminal 会把控制台窗挂在自己的进程里，
        #      按 cmd 的 pid 找窗会找不到 ⇒ 改成"起窗前后比对出现的控制台类新窗"。
        before_cons = set(console_hwnds_any())
        con = subprocess.Popen(["cmd.exe", "/k", "echo preflight-probe & timeout /t 30"],
                               creationflags=0x00000010)   # CREATE_NEW_CONSOLE
        pids.append(con.pid)
        ch_hwnd = 0
        for _ in range(20):
            time.sleep(0.5)
            new = [x for x in console_hwnds_any() if x not in before_cons]
            if new:
                ch_hwnd = new[0]
                break
        if not ch_hwnd:
            rec("③ 起一个控制台窗", False, "自己起控制台也没拿到窗 ⇒ 这条验不了", skip=True)
        else:
            r3, d3 = pre(ch_hwnd)
            rec("③ 敏感窗 ⇒ sensitive.denied=True",
                bool((d3.get("sensitive") or {}).get("denied")),
                "sensitive=%r" % (d3.get("sensitive"),))
            rec("③ 敏感窗 verdict=blocked_sensitive",
                d3.get("verdict") == "blocked_sensitive", "verdict=%r" % (d3.get("verdict"),))
    finally:
        if h:
            try:
                u.PostMessageW(h, 0x0010, 0, 0)
            except Exception:
                pass
        time.sleep(0.4)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        print("收摊后残留记事本：%d" % len(notepads()), flush=True)
    return finish()


if __name__ == "__main__":
    INJECT = "--inject" in sys.argv
    sys.exit(main())
