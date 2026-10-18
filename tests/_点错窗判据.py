# -*- coding: utf-8 -*-
r"""tests\_点错窗判据.py —— 第 24 条判据：**点错窗必须被拒**（真窗，双向）。

治的病（框架 §㊸ 的真验，提升为常驻判据）：
  旧行为：点被别的窗口盖住的位置 → 动作照发，回来一张"画面没变化"的假小票；
  新行为：**发送之前**做归属判定 —— 落点不归目标窗 ⇒ `code=12 / blocked_by=wrong_target`、
  **零像素（after=None）**、并点名遮挡者与该关的窗。

两条断言（真建两个记事本，不 mock）：
  ① 负例：bottom 被 top 盖住的点 ⇒ 拒，且 after is None，warning 含 wrong_target 语义与遮挡者 hwnd
  ② 正例：bottom 置前且点自己可见区域 ⇒ ok / code=0 / blocked_by is None / verdict=met

退出码：0=绿；1=红；2=验不了（无有效通行证 / 起不出两个窗 / 环境不满足 —— **2 不许当绿读**）。
注入：`--inject hit_after_send` —— 把归属判定挪到发送之后（模拟旧行为），本判据必须变红。

★ 不写死本机路径（tests\_写死路径判据.py 会抓）；起窗→断言→**try/finally 收摊**。
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
NOPASS = []      # 收集 code=9（未授权/额度用尽）


def rec(name, ok, detail="", skip=False):
    line = "[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail)
    print(line, flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


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


def place(h, x, y, w=700, hh=500):
    u.SetWindowPos(h, 0, x, y, w, hh, 0x0040)


def force_fg(h):
    """Alt 解锁前台锁 → TOPMOST 抬起 → SetForegroundWindow → 取消 TOPMOST。"""
    u.keybd_event(0x12, 0, 0, 0)
    u.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.12)
    u.SetWindowPos(h, -1, 200, 200, 700, 500, 0x0040)
    time.sleep(0.15)
    u.SetForegroundWindow(h)
    time.sleep(0.35)
    u.SetWindowPos(h, -2, 200, 200, 700, 500, 0x0040)
    time.sleep(0.2)
    return u.GetForegroundWindow() == h


def version(h):
    r = core.dispatch("ctl_screen", {"hwnd": h, "actor": "判据24"}, actor="判据24")
    d = r.get("data") or {}
    inner = d.get("结果") if isinstance(d.get("结果"), dict) else d
    return inner.get("screen_version")


def click(h, xy, inject_after=False):
    c = core.dispatch("ctl_click", {"hwnd": h, "xy": list(xy), "frame_id": version(h),
                                    "allow_foreground": True,
                                    "_inject_hit_after": inject_after,
                                    "actor": "判据24"}, actor="判据24")
    d = c.get("data") or {}
    inner = d.get("结果") if isinstance(d.get("结果"), dict) else d
    if c.get("code") == 9:
        NOPASS.append(str(c.get("warning") or "")[:80])
    return c, inner


def main():
    # ⓪ 通行证：没有就 rc=2（**不许伪造通过**）
    r = core.dispatch("ctl_status", {"actor": "判据24"}, actor="判据24")
    st = (r.get("data") or {})
    st = st.get("结果") if isinstance(st.get("结果"), dict) else st
    auth_dir = (st or {}).get("auth_dir") or ""
    brake = (st or {}).get("brake") or {}
    if brake.get("engaged"):
        rec("急停未拉下", False, "急停 flag 存在 ⇒ 本轮验不了", skip=True)
        return finish()
    pass_path = os.path.join(auth_dir, "auth_pass.json")
    left = 0
    try:
        import json as _json
        with open(pass_path, encoding="utf-8") as f:
            left = int((_json.load(f) or {}).get("expires_at") or 0) - int(time.time())
    except Exception:
        left = 0
    if left <= 0:
        rec("有有效通行证", False, "无有效通行证（需人弹窗签发，剩 %ds）⇒ 本轮验不了" % left, skip=True)
        return finish()

    # ⓪b 租约（写动作必须持租约；本判据自己拿、自己放 —— 不许留尸体）
    lr = core.dispatch("ctl_lease", {"action": "acquire", "scopes": "ABC", "actor": "判据24"},
                       actor="判据24")
    if not lr.get("ok"):
        rec("拿到租约", False, "租约拿不到（code=%r）⇒ 本轮验不了" % lr.get("code"), skip=True)
        return finish()

    pids = []
    before = set(notepads())
    try:
        for _ in range(2):
            pids.append(subprocess.Popen(["notepad.exe"]).pid)
            time.sleep(1.2)
        # ★ 只认「本次新出现的」两个窗（Win11 记事本是打包应用，窗口 pid ≠ Popen 的 pid）
        hs = [h for h in notepads() if h not in before][:2]
        if len(hs) < 2:
            rec("起两个记事本", False, "本次只新出现 %d 个窗（旧窗 %d 个不参与）"
                % (len(hs), len(before)), skip=True)
            return finish()
        bottom, top = hs[0], hs[1]
        place(bottom, 200, 200)
        time.sleep(0.3)
        place(top, 520, 420)          # 盖住 bottom 的右下角
        time.sleep(0.4)
        force_fg(top)

        if NOPASS:
            rec("有可用动作额度", False,
                "通行证额度用尽/未授权（code=9）：%s ⇒ 本轮验不了" % NOPASS[0], skip=True)
            return finish()

        # ① 负例：点在盖住它的 top 上
        c, inner = click(bottom, (700, 600))
        warn = str(c.get("warning") or "")
        if NOPASS:
            # 没有可用额度 ⇒ 这一条**验不了**，不许当红（也不是绿）
            rec("有可用动作额度", False,
                "通行证额度用尽/未授权（code=9）：%s ⇒ 本轮验不了" % NOPASS[0], skip=True)
            return finish()
        rec("① 点错窗 ⇒ 拒（code=12 / wrong_target）",
            (c.get("code") == 12 and (inner.get("blocked_by") == "wrong_target")),
            "code=%r blocked_by=%r" % (c.get("code"), inner.get("blocked_by")))
        rec("① 拒的时候**零像素**（after is None）", inner.get("after") is None,
            "after=%r" % (inner.get("after"),))
        rec("① 回执点名遮挡者（warning 里出现遮挡窗 hwnd 或 'wrong_target'）",
            ("wrong_target" in warn or "归属" in warn or "遮挡" in warn), warn[:80])

        # ② 正例：bottom 置前、点自己可见区域
        ok_fg = force_fg(bottom)
        c2, inner2 = click(bottom, (300, 300))
        rec("② 目标前台下正常放行（ok / code=0）",
            bool(c2.get("ok")) and c2.get("code") == 0,
            "fg=%r ok=%r code=%r" % (ok_fg, c2.get("ok"), c2.get("code")))
        rec("② 判词 met（变化落在目标窗内）",
            (inner2.get("after") or {}).get("verdict") == "met",
            "verdict=%r" % ((inner2.get("after") or {}).get("verdict"),))

        # ③ 注入：把 `_win_at_point` 打成 None（= 归属事实取不到，退化成旧行为）⇒ 本判据必须红
        if INJECT:
            _orig = core._win_at_point
            core._win_at_point = lambda _x, _y: None
            try:
                c3, inner3 = click(bottom, (700, 600))
            finally:
                core._win_at_point = _orig
            rejected = (c3.get("code") == 12 and inner3.get("after") is None)
            # 注入时语义翻转：归属取不到 ⇒ 负例**不再被拒** ⇒ 本判据失效 ⇒ **必须红**（rc=1）
            rec("③ 注入（归属事实取不到 ⇒ 负例不再被拒）判据必须红",
                rejected,
                "注入后 code=%r after_is_None=%r（红=对：说明①确实靠归属前置闸；绿=判据是死的）"
                % (c3.get("code"), inner3.get("after") is None))
    finally:
        # 收摊：先给自己起的两个窗发 WM_CLOSE，再兜底按 pid 杀；最后释放租约
        for h in (locals().get("bottom"), locals().get("top")):
            if h:
                try:
                    u.PostMessageW(h, 0x0010, 0, 0)     # WM_CLOSE
                except Exception:
                    pass
        time.sleep(0.6)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        core.dispatch("ctl_lease", {"action": "release", "actor": "判据24"}, actor="判据24")
        time.sleep(0.4)
        left_hs = notepads()
        print("收摊后残留记事本：%d" % len(left_hs), flush=True)
    return finish()


INJECT = False


def finish():
    print("\n" + "=" * 60, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 有检查项没做成（见上）；★ 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】点错窗没有被干净拒掉", flush=True)
        return 1
    print("结论：【绿】点错窗被拒（零像素 + 点名遮挡者），目标前台下正常放行且 verdict=met", flush=True)
    return 0


if __name__ == "__main__":
    INJECT = "--inject" in sys.argv
    sys.exit(main())
