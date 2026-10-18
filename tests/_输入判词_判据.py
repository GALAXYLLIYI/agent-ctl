# -*- coding: utf-8 -*-
r"""tests\_输入判词_判据.py —— 第 26 条判据：**点没点到 ≠ 点到了没生效**。

治的病：`code=8`（发出去了但画面没变）原来分不清原因：
  ① 根本没点到目标（被挡/前台被别人抢）  ← 输入通道
  ② 点到了但界面本来就没可见变化          ← 感知判词
新行为：回执带 `input{fg_ok,hit_ok,cursor,verdict}`，把这两类分开。

断言（真记事本）：
  ① 正例：目标前台点可见区 ⇒ input.verdict=accepted，且带 cursor 坐标
  ② code=8 场景（同点连点两下，第二下无变化）⇒ 回执仍必须带 input.verdict=accepted
     （= "点了没生效"，不是"没点到"）
  ③ 注入（_input_snapshot 打回 unknown）⇒ ① 必须失败 ⇒ 判据红
退出码：0 绿 / 1 红 / 2 验不了
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
        print("结论：【红】输入通道判词没把「没点到」和「点了没生效」分开", flush=True)
        return 1
    print("结论：【绿】回执能把「没点到 / 点到没生效」分开（input.verdict）", flush=True)
    return 0


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


def fg(h):
    u.keybd_event(0x12, 0, 0, 0)
    u.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.1)
    u.SetWindowPos(h, 0, 240, 200, 720, 480, 0x0040)
    time.sleep(0.2)
    u.SetForegroundWindow(h)
    u.SetFocus(h)
    time.sleep(0.35)


def ver(h):
    r = core.dispatch("ctl_screen", {"hwnd": h, "actor": "判据26"}, actor="判据26")
    d = r.get("data") or {}
    inner = d.get("结果") if isinstance(d.get("结果"), dict) else d
    return inner.get("screen_version")


def click(h, xy):
    c = core.dispatch("ctl_click", {"hwnd": h, "xy": list(xy), "frame_id": ver(h),
                                    "allow_foreground": True, "actor": "判据26"}, actor="判据26")
    d = c.get("data") or {}
    inner = d.get("结果") if isinstance(d.get("结果"), dict) else d
    return c, inner


def main():
    if INJECT:
        core._input_snapshot = lambda *_a, **_k: {"verdict": "unknown", "fg_ok": None,
                                                  "hit_ok": None, "cursor": None}
        print("★ 注入：_input_snapshot 恒 unknown", flush=True)

    st = core.dispatch("ctl_status", {"actor": "判据26"}, actor="判据26").get("data") or {}
    st = st.get("结果") if isinstance(st.get("结果"), dict) else st
    if (st or {}).get("brake", {}).get("engaged"):
        rec("急停未拉下", False, "急停 flag 存在", skip=True)
        return finish()
    try:
        import json as _j
        with open(os.path.join((st or {}).get("auth_dir") or "", "auth_pass.json"),
                  encoding="utf-8") as f:
            left = int((_j.load(f) or {}).get("expires_at") or 0) - int(time.time())
    except Exception:
        left = 0
    if left <= 0:
        rec("有有效通行证", False, "无有效通行证（剩 %ds）" % left, skip=True)
        return finish()

    lr = core.dispatch("ctl_lease", {"action": "acquire", "scopes": "ABC", "actor": "判据26"},
                       actor="判据26")
    if not lr.get("ok"):
        rec("拿到租约", False, "code=%r" % lr.get("code"), skip=True)
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
        fg(h)
        time.sleep(0.3)

        c1, i1 = click(h, (420, 300))
        if c1.get("code") == 9:
            rec("有可用动作额度", False, "code=9 额度用尽", skip=True)
            return finish()
        inp1 = (c1.get("data") or {}).get("input") or {}
        rec("① 回执带 input{}", isinstance(inp1, dict) and "verdict" in inp1, "input=%r" % (inp1,))
        rec("① 目标前台点击 ⇒ input.verdict=accepted",
            inp1.get("verdict") == "accepted", "verdict=%r fg_ok=%r" % (inp1.get("verdict"), inp1.get("fg_ok")))
        rec("① 带光标坐标", isinstance(inp1.get("cursor"), list), "cursor=%r" % (inp1.get("cursor"),))

        c2, i2 = click(h, (420, 300))
        inp2 = (c2.get("data") or {}).get("input") or {}
        rec("② 第二次同点（画面多半没变）仍判 accepted，而不是 unknown/not_target",
            inp2.get("verdict") == "accepted" and c2.get("code") in (0, 8),
            "code=%r verdict=%r" % (c2.get("code"), inp2.get("verdict")))
    finally:
        if h:
            try:
                u.PostMessageW(h, 0x0010, 0, 0)
            except Exception:
                pass
        time.sleep(0.4)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        core.dispatch("ctl_lease", {"action": "release", "actor": "判据26"}, actor="判据26")
        print("收摊后残留记事本：%d" % len(notepads()), flush=True)
    return finish()


if __name__ == "__main__":
    INJECT = "--inject" in sys.argv
    sys.exit(main())
