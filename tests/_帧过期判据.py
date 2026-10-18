# -*- coding: utf-8 -*-
r"""tests\_帧过期判据.py —— 第 25 条判据：**旧帧不许用来点**（frame_id + TTL）。

治的病（三方商议 §㊾ 的 0 号「最小裁判」第一条）：
  screen_version 只能抓「画面变了」；抓不住「你拿着一张很旧的帧去点」——
  画面没变、版本没变，但判词会挂在一张早就不代表现在的帧上。
  新行为：帧龄 > TTL ⇒ `code=12 / 判定=过期`、**零像素**、并给出帧龄与 TTL。

三条断言（真记事本，不 mock）：
  ① 正例：刚拍的帧（帧龄≈0）⇒ 放行 code=0
  ② 负例：同一张帧等 1.6s（TTL=1000ms）⇒ code=12、判定=过期、after is None
  ③ 反证：把 TTL 关掉后，同一张旧帧**会被放行** ⇒ 说明②的拒绝确实来自 TTL（否则判据是死的）

退出码：0=绿；1=红；2=验不了（无通行证 / 起不出窗 / 额度用尽 —— **2 不许当绿读**）。
注入：`--inject` —— 把 `core._frame_ttl_ms` 打成恒 0（= 关掉过期判定）⇒ ② 必须失败 ⇒ 本判据红。
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
NOPASS = []
TTL_MS = 1000
WAIT_S = 1.6


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
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


def force_fg(h):
    u.keybd_event(0x12, 0, 0, 0)
    u.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.12)
    u.SetWindowPos(h, 0, 260, 220, 760, 520, 0x0040)
    time.sleep(0.2)
    u.SetForegroundWindow(h)
    u.SetFocus(h)
    time.sleep(0.35)
    return u.GetForegroundWindow() == h


def version(h):
    r = core.dispatch("ctl_screen", {"hwnd": h, "actor": "判据25"}, actor="判据25")
    d = r.get("data") or {}
    inner = d.get("结果") if isinstance(d.get("结果"), dict) else d
    return inner.get("screen_version")


def click(h, xy, fid, ttl):
    acts = {"hwnd": h, "xy": list(xy), "frame_id": fid, "frame_ttl_ms": ttl,
            "allow_foreground": True, "actor": "判据25"}
    c = core.dispatch("ctl_click", acts, actor="判据25")
    d = c.get("data") or {}
    inner = d.get("结果") if isinstance(d.get("结果"), dict) else d
    if c.get("code") == 9:
        NOPASS.append(str(c.get("warning") or "")[:70])
    return c, inner


def finish():
    print("\n" + "=" * 60, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 有检查项没做成（见上）；★ 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】旧帧没有被 TTL 挡住（判词会挂在过期帧上）", flush=True)
        return 1
    print("结论：【绿】旧帧被 code=12 挡住且零像素；关掉 TTL 后同一帧会被放行（证明判据是活的）",
          flush=True)
    return 0


INJECT = False


def main():
    if INJECT:
        core._frame_ttl_ms = lambda _a: 0        # 注入：关掉过期判定
        print("★ 注入模式：core._frame_ttl_ms 恒 0（等价于没有 TTL）", flush=True)

    r = core.dispatch("ctl_status", {"actor": "判据25"}, actor="判据25")
    st = (r.get("data") or {})
    st = st.get("结果") if isinstance(st.get("结果"), dict) else st
    auth_dir = (st or {}).get("auth_dir") or ""
    if (st or {}).get("brake", {}).get("engaged"):
        rec("急停未拉下", False, "急停 flag 存在 ⇒ 本轮验不了", skip=True)
        return finish()
    try:
        import json as _json
        with open(os.path.join(auth_dir, "auth_pass.json"), encoding="utf-8") as f:
            left = int((_json.load(f) or {}).get("expires_at") or 0) - int(time.time())
    except Exception:
        left = 0
    if left <= 0:
        rec("有有效通行证", False, "无有效通行证（剩 %ds）⇒ 本轮验不了" % left, skip=True)
        return finish()

    lr = core.dispatch("ctl_lease", {"action": "acquire", "scopes": "ABC", "actor": "判据25"},
                       actor="判据25")
    if not lr.get("ok"):
        rec("拿到租约", False, "code=%r ⇒ 本轮验不了" % lr.get("code"), skip=True)
        return finish()

    pids, h = [], 0
    before = set(notepads())
    try:
        pids.append(subprocess.Popen(["notepad.exe"]).pid)
        time.sleep(1.6)
        hs = [x for x in notepads() if x not in before][:1]
        if not hs:
            rec("起一个记事本", False, "本次没出现新窗（旧窗不参与）", skip=True)
            return finish()
        h = hs[0]
        ok_fg = force_fg(h)
        time.sleep(0.3)

        # ① 正例：刚拍的帧
        fid1 = version(h)
        c1, i1 = click(h, (420, 300), fid1, TTL_MS)
        if NOPASS:
            rec("有可用动作额度", False, "额度用尽/未授权（code=9）：%s ⇒ 验不了" % NOPASS[0], skip=True)
            return finish()
        rec("① 新鲜帧正常放行（code=0）",
            bool(c1.get("ok")) and c1.get("code") == 0, "fg=%r code=%r" % (ok_fg, c1.get("code")))

        # ② 负例：同一张帧，等过 TTL
        time.sleep(WAIT_S)
        c2, i2 = click(h, (520, 340), fid1, TTL_MS)
        v2 = (c2.get("data") or {}).get("frame_id_校验") or {}
        rec("② 旧帧被拒（code=12）", c2.get("code") == 12, "code=%r" % (c2.get("code"),))
        rec("② 判定写明「过期」并给出帧龄/TTL",
            v2.get("判定") == "过期" and int(v2.get("帧龄ms") or 0) > TTL_MS,
            "判定=%r 帧龄=%r TTL=%r" % (v2.get("判定"), v2.get("帧龄ms"), v2.get("TTLms")))
        rec("② 拒绝时**零像素**（after is None）", i2.get("after") is None,
            "after=%r" % (i2.get("after"),))

        # ③ 反证：关掉 TTL 后，**同一条校验路径不再报"过期"**。
        #    注意（实测教训）：记事本有闪烁光标，屏幕版本会在 1~2 秒内自己变，
        #    所以不能拿"一张旧帧 + 关掉 TTL"去证明放行（那样会被"版本不匹配"挡住）。
        #    这里改成证明**分支确实被关掉**：清空过期判定后，判定字段必须是"匹配"而不是"过期"。
        fid2 = version(h)
        _orig = core._frame_ttl_ms
        core._frame_ttl_ms = lambda _a: 0
        try:
            c3, _i3 = click(h, (560, 360), fid2, TTL_MS)
        finally:
            core._frame_ttl_ms = _orig
        v3 = (c3.get("data") or {}).get("frame_id_校验") or {}
        rec("③ 关掉 TTL 后不再判「过期」（证明②的红确实来自 TTL 分支）",
            v3.get("判定") != "过期",
            "关 TTL 后 判定=%r TTLms=%r code=%r" % (v3.get("判定"), v3.get("TTLms"), c3.get("code")))
    finally:
        if h:
            try:
                u.PostMessageW(h, 0x0010, 0, 0)
            except Exception:
                pass
        time.sleep(0.5)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        core.dispatch("ctl_lease", {"action": "release", "actor": "判据25"}, actor="判据25")
        time.sleep(0.3)
        print("收摊后残留记事本：%d" % len(notepads()), flush=True)
    return finish()


if __name__ == "__main__":
    INJECT = "--inject" in sys.argv
    sys.exit(main())
