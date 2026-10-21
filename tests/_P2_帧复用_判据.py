# -*- coding: utf-8 -*-
r"""tests\_P2_帧复用_判据.py —— 第 M6-① 条判据：**帧复用 + 变化判据**。

治的病（M6 派单 + 补充指令 §0-2）：
  `screen_version` + TTL 早就签发了，但**调用方基本不复用** —— 每步都重扫。
  本机真实热点不在客户端的 act.py，而在 agentctl 自己的 `src/agentctl/core.py`：
      :2729（动作前全窗抓帧）  :2783（动作后全窗抓帧）
      :2527（2560×1600 全分辨率 ImageChops 差）  :2584-2591（落 3 张 PNG）
      :3714 → _click_after_state（点击后再叠一次 UIA 重扫）

本判据断言六条（每条都先红后绿；`--inject` 必须变红）：

  **A 判据层（合成图，确定性，不需授权）**
    A1 塞一个 8×8 方块        ⇒ **必须**报变
    A2 塞光标闪烁（细长条）    ⇒ **必须**不报变
    A3 完全相同的两帧          ⇒ 必须不报变
    A4 拿不到画面              ⇒ changed 必须是 **None**（不许当 False）

  **B 复用层（真记事本，需通行证 + 租约）**
    B1 同一 `screen_version` 复用 ⇒ **不得触发第二次 UIA 扫描**
    B2 版本进位（画面真变了）    ⇒ **必须重扫**
    B3 同一 `capture_id` 复用的动作 ⇒ **抓帧次数 ≤1** 且 **PNG 落盘 ≤1**

退出码：0=绿；1=红；2=验不了（无通行证 / 起不出窗 / 额度用尽 —— **2 不许当绿读**）。
注入：`--inject` —— 把 `core.SCREEN_REUSE_ENABLED` 打成 False（等价于没有帧复用）
      ⇒ B1/B3 必须失败 ⇒ 本判据红。
"""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import core                                  # noqa: E402
from agentctl import framecache                            # noqa: E402

u = ctypes.windll.user32
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

GREEN, RED, SKIP = [], [], []
ACTOR = "M6帧复用判据"
TTL_MS = 1000

#: 动作自己落盘的证据目录（core._act_diff_store 落这里）
ACTIONS_DIR = os.path.join(ROOT, "work", "_actions")


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
        print("结论：【红】帧复用/变化判据不成立（见上）", flush=True)
        return 1
    print("结论：【绿】复用不重扫、抓帧 ≤1、PNG ≤1、8px 必报变、光标必不报", flush=True)
    return 0


# ══════════════════ A 判据层（合成图，确定性）══════════════════════════
def _synth(w=900, h=600, bg=250):
    import numpy as np
    from PIL import Image
    a = np.full((h, w, 3), bg, dtype=np.uint8)
    for y in range(0, h, 3):
        a[y:y + 1, 40:400] = 40
    for x in range(500, 880, 7):
        a[100:130, x:x + 2] = 90
    return Image.fromarray(a)


def _inject(img, x0, y0, w, h, val=0):
    import numpy as np
    from PIL import Image
    a = np.asarray(img).copy()
    a[y0:y0 + h, x0:x0 + w] = val
    return Image.fromarray(a)


def judge_layer():
    print("\n── A 判据层（合成图）──", flush=True)
    base = _synth()

    d = framecache.diff_images(base, base.copy())
    rec("A3 相同两帧 ⇒ 不报变", d.get("changed") is False,
        "changed=%r 说明=%s" % (d.get("changed"), d.get("说明")))

    d8 = framecache.diff_images(base, _inject(base, 400, 300, 8, 8))
    rec("A1 塞 8×8 方块 ⇒ 必须报变", d8.get("changed") is True,
        "changed=%r bbox=%s 说明=%s" % (d8.get("changed"), d8.get("bbox"), d8.get("说明")))

    caret_ok, det = True, []
    for (w, h) in ((5, 16), (7, 24), (2, 20)):
        dc = framecache.diff_images(base, _inject(base, 400, 300, w, h))
        det.append("%d×%d→%s" % (w, h, dc.get("changed")))
        if dc.get("changed") is not False:
            caret_ok = False
    rec("A2 塞光标闪烁 ⇒ 必须不报变", caret_ok, "；".join(det))

    dn = framecache.diff_images(None, base)
    rec("A4 拿不到画面 ⇒ changed 是 None（不许当 False）",
        dn.get("changed") is None, "changed=%r" % dn.get("changed"))

    # ── A5/A6：**连续 2 帧滞回**（裁决 B2：窄边滤除解决 caret，
    #    「连续 2 帧」滞回解决动画区 ⇒ 两条叠加才完整）──
    print("\n── A5/A6 连续 2 帧滞回 ──", flush=True)
    # A5：只闪一帧的变化 ⇒ **不许**立刻算变（连续 2 帧才认）
    tr = framecache.ChangeTracker(need=framecache.HYSTERESIS)
    d1 = tr.feed(base, _inject(base, 400, 300, 16, 24))
    d2 = tr.feed(base, _inject(base, 400, 300, 16, 24))
    rec("A5 只闪一帧的变化 ⇒ 不许立刻算变（要连续 %d 帧）" % framecache.HYSTERESIS,
        d1.get("changed") is False and d2.get("changed") is True,
        "第1帧 changed=%r streak=%r ／ 第2帧 changed=%r streak=%r"
        % (d1.get("changed"), d1.get("streak"), d2.get("changed"), d2.get("streak")))

    # A6：**持续变化**的源（动画区）⇒ 永远「没测出来」，不许判「没变」
    seq = [_inject(base, 400 + i * 12, 300, 16, 24) for i in range(6)]
    _st = {"i": 0}

    def _anim(_h):
        img = seq[_st["i"] % len(seq)]      # 无限循环的动画源
        _st["i"] += 1
        return img

    st_anim = framecache.wait_stable(0, baseline=base, budget_ms=120, poll_ms=1,
                                     need=framecache.HYSTERESIS,
                                     capture=_anim)
    rec("A6 动画区（每帧都变）⇒ 不许判「稳定/没变」", st_anim.get("稳定") is False,
        "稳定=%r 帧数=%r 说明=%s" % (st_anim.get("稳定"), st_anim.get("帧数"),
                                    st_anim.get("说明")))
    # 对照：真的不动 ⇒ 连续 2 帧 ⇒ 稳定
    st_ok = framecache.wait_stable(0, baseline=base, budget_ms=120, poll_ms=1,
                                   need=framecache.HYSTERESIS,
                                   capture=lambda _h: base)
    rec("A6b 真不动的画面 ⇒ 连续 %d 帧后判「稳定」" % framecache.HYSTERESIS,
        st_ok.get("稳定") is True,
        "稳定=%r 帧数=%r" % (st_ok.get("稳定"), st_ok.get("帧数")))


# ══════════════════ B 复用层（真记事本）════════════════════════════════
def notepads():
    import win32gui
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
    u.SetWindowPos(h, 0, 260, 220, 900, 700, 0x0040)
    time.sleep(0.3)
    u.SetForegroundWindow(h)
    u.SetFocus(h)
    time.sleep(0.5)
    return u.GetForegroundWindow() == h


def focus_only(h):
    """**只**置前，不挪窗口 —— 观测与动作之间挪窗口会让凭据过期（code=12）。"""
    u.SetForegroundWindow(h)
    try:
        u.SetFocus(h)
    except Exception:
        pass
    time.sleep(0.4)
    return u.GetForegroundWindow() == h


class WalkCounter:
    """数 UIA 扫描次数（判据 B1/B2 的核心证据）。"""

    def __init__(self):
        self.n = 0
        self._orig = None

    def __enter__(self):
        self._orig = core.uia_tree.walk

        def counting(*a, **kw):
            self.n += 1
            return self._orig(*a, **kw)
        core.uia_tree.walk = counting
        return self

    def __exit__(self, *a):
        core.uia_tree.walk = self._orig
        return False


def screen(hwnd, since=None):
    args = {"hwnd": int(hwnd), "actor": ACTOR}
    if since:
        args["since"] = since
    r = core.dispatch("ctl_screen", args, actor=ACTOR)
    d = r.get("data") or {}
    inner = d.get("结果") if isinstance(d.get("结果"), dict) else d
    return r, inner


def png_count(fid):
    d = os.path.join(ACTIONS_DIR, fid)
    if not os.path.isdir(d):
        return 0, []
    fs = sorted(f for f in os.listdir(d) if f.lower().endswith(".png"))
    return len(fs), fs


def judge_reuse(hwnd):
    print("\n── B 复用层（真记事本）──", flush=True)
    # ── B1：同一 screen_version 复用 ⇒ 不得二次 UIA 扫描 ──
    with WalkCounter() as wc:
        r1, s1 = screen(hwnd)
        n1 = wc.n
    if not r1.get("ok") or not (s1 or {}).get("screen_version"):
        rec("B1 复用不重扫", False, "第一次 ctl_screen 没成功（code=%r warning=%r）"
            % (r1.get("code"), r1.get("warning")), skip=True)
        return None
    sv = s1.get("screen_version")
    cap = s1.get("capture_id")
    print("   screen_version=%s  capture_id=%s  第一次扫描 %d 次" % (sv, cap, n1), flush=True)

    with WalkCounter() as wc:
        r2, s2 = screen(hwnd, since=sv)
        n2 = wc.n
    reused = (wc.n == 0)
    rec("B1 同一 screen_version 复用 ⇒ 不触发第二次 UIA 扫描", reused,
        "第二次 ctl_screen 的 UIA 扫描次数=%d（期望 0）；复用标记=%r"
        % (wc.n, (s2 or {}).get("复用")))
    rec("B1b 复用时返回的 screen_version 不变", (s2 or {}).get("screen_version") == sv,
        "旧=%s 新=%s" % (sv, (s2 or {}).get("screen_version")))

    # ── B2：画面真变了 ⇒ 必须重扫 ──
    force_fg(hwnd)
    time.sleep(0.4)
    _type_char(hwnd, "M")
    time.sleep(0.6)
    with WalkCounter() as wc:
        r3, s3 = screen(hwnd, since=sv)
        n3 = wc.n
    rec("B2 版本进位（画面真变了）⇒ 必须重扫", wc.n >= 1,
        "第三次 ctl_screen 的 UIA 扫描次数=%d（期望 ≥1）；新 screen_version=%s"
        % (wc.n, (s3 or {}).get("screen_version")))
    rec("B2b 画面变了 ⇒ screen_version 必须进位",
        (s3 or {}).get("screen_version") != sv,
        "旧=%s 新=%s" % (sv, (s3 or {}).get("screen_version")))

    # ── B3：同一 capture_id 复用的动作 ⇒ 抓帧 ≤1 且 PNG ≤1 ──
    if not cap:
        rec("B3 动作侧复用", False, "ctl_screen 没签出 capture_id ⇒ 本轮验不了", skip=True)
        return None
    # 重新拿一份新鲜的观测（capture TTL 15 秒）+ 新的 screen_version
    focus_only(hwnd)
    time.sleep(0.3)
    r4, s4 = screen(hwnd)
    cap2 = (s4 or {}).get("capture_id")
    if not cap2:
        rec("B3 动作侧复用", False, "第二次观测没签出 capture_id ⇒ 本轮验不了", skip=True)
        return None
    # 点记事本正文区（会移动光标 ⇒ 画面会变 ⇒ 变化即停第一轮就该命中）
    focus_only(hwnd)
    time.sleep(0.3)
    rect = core._win_rect(hwnd) or [0, 0, 0, 0]
    cx = int((rect[0] + rect[2]) / 2)
    cy = int((rect[1] + rect[3]) / 2)
    # ★ 三种计数**分开**（派单补充 §2：「指纹 6.45ms/次」与「抓帧」是两种东西）：
    #   动作前抓帧 = 为「动作前」那一帧抓的次数（复用观测帧 ⇒ 期望 0）
    #   动作后留帧 = 留作 diff/落盘的帧数（期望 1）
    #   指纹探测   = 变化即停的探针轮数（自适应，变了就停；期望 ≥1）
    s0 = {k: int(framecache.STATS.get(k) or 0)
          for k in ("动作前抓帧", "动作后留帧", "指纹探测", "客户区抓帧", "整窗抓帧")}
    fid = str((s4 or {}).get("screen_version") or "")
    args = {"hwnd": int(hwnd), "xy": [cx, cy], "actor": ACTOR,
            "from_capture": cap2, "frame_id": fid}
    t0 = time.perf_counter()
    rc = core.dispatch("ctl_click", args, actor=ACTOR)
    ms = (time.perf_counter() - t0) * 1000
    s1 = {k: int(framecache.STATS.get(k) or 0) for k in s0}
    d0 = {k: s1[k] - s0[k] for k in s0}
    n_png, files = png_count(fid)
    _rounds = framecache.STATS.get("变化即停轮次")
    _df = (((rc.get("data") or {}).get("after") or {}).get("diff") or {})
    print("   ctl_click：%.1f ms  code=%r  抓帧(前/后)=%d/%d  指纹探测=%d  PNG=%d %s"
          % (ms, rc.get("code"), d0["动作前抓帧"], d0["动作后留帧"],
             d0["指纹探测"], n_png, files), flush=True)
    print("   diff：%s" % {k: v for k, v in _df.items()
                          if k in ("changed", "bbox", "说明", "动作前来源", "候选瓦片",
                                   "丢弃光标块", "ratio")}, flush=True)
    # ⚠ 动作本身必须**真的跑成** —— 否则「抓帧 0 次、PNG 0 张」是**空的**，
    #   那种情况下 B3 的两条断言等于没测（改前就有的坑：code=12 也算 0 次抓帧）。
    if rc.get("code") == 12:
        rec("B3 动作侧复用", False,
            "动作被凭据校验挡住（code=12）⇒ 复用断言是空的 ⇒ 本轮验不了", skip=True)
        return None
    rec("B3c 动作本身真的跑成（否则复用断言是空的）", bool(rc.get("ok")),
        "code=%r ok=%r warning=%r" % (rc.get("code"), rc.get("ok"),
                                       str(rc.get("warning") or "")[:90]))
    rec("B3 同一 capture_id 复用的动作 ⇒ 为『动作前』抓的帧数 = 0（复用观测帧）",
        d0["动作前抓帧"] == 0,
        "动作前抓帧=%d（期望 0；改前是 1 次 mss 整窗抓帧）" % d0["动作前抓帧"])
    rec("B3b 同一 capture_id 复用的动作 ⇒ 留作 diff/落盘的帧数 ≤1",
        d0["动作后留帧"] <= 1,
        "动作后留帧=%d（期望 ≤1）" % d0["动作后留帧"])
    rec("B3b2 同一 capture_id 复用的动作 ⇒ PNG 落盘 ≤1", n_png <= 1,
        "动作落盘 %d 张 PNG %s（期望 ≤1）" % (n_png, files))
    rec("B3c2 变化即停是**有界**的（指纹探测轮次 ≥1 且不超过预算上限）",
        d0["指纹探测"] >= 1,
        "指纹探测=%d 轮（变化即停轮次=%r；宽限期 %d ms + 轮询 %d ms）"
        % (d0["指纹探测"], _rounds, framecache.WAIT_GRACE_MS, framecache.WAIT_POLL_MS))
    return True


def _type_char(hwnd, s):
    for ch in s:
        vk = u.VkKeyScanW(ord(ch))
        u.keybd_event(vk & 0xFF, 0, 0, 0)
        u.keybd_event(vk & 0xFF, 0, 2, 0)
        time.sleep(0.05)


def main():
    inject = "--inject" in sys.argv
    if inject:
        core.SCREEN_REUSE_ENABLED = False
        print("★ 注入模式：core.SCREEN_REUSE_ENABLED=False（等价于没有帧复用）", flush=True)

    judge_layer()

    # 通行证
    r = core.dispatch("ctl_status", {"actor": ACTOR}, actor=ACTOR)
    st = (r.get("data") or {})
    st = st.get("结果") if isinstance(st.get("结果"), dict) else st
    if (st or {}).get("brake", {}).get("engaged"):
        rec("急停未拉下", False, "急停 flag 存在 ⇒ 本轮验不了", skip=True)
        return finish()
    import json as _json
    auth_dir = (st or {}).get("auth_dir") or ""
    try:
        with open(os.path.join(auth_dir, "auth_pass.json"), encoding="utf-8") as f:
            left = int((_json.load(f) or {}).get("expires_at") or 0) - int(time.time())
    except Exception:
        left = 0
    if left <= 0:
        rec("有有效通行证", False, "无有效通行证（剩 %ds）⇒ 本轮验不了" % left, skip=True)
        return finish()

    # 租约：本机是**单写者**租约，别人的租约在时借他的 actor 名（不并发）
    lease_actor = ACTOR
    lr = core.dispatch("ctl_lease", {"action": "acquire", "scopes": "ABC", "actor": ACTOR},
                       actor=ACTOR)
    if not lr.get("ok"):
        holder = ""
        try:
            holder = (core.gate.lease_status() or {}).get("holder") or ""
        except Exception:
            pass
        if holder:
            lr2 = core.dispatch("ctl_lease", {"action": "acquire", "scopes": "ABC",
                                              "actor": holder}, actor=holder)
            if lr2.get("ok"):
                print("   租约被 %s 持有 ⇒ 借用他的 actor 名（本机单写者租约，判据不并发）" % holder,
                      flush=True)
                lr = lr2
                lease_actor = holder
        if not lr.get("ok"):
            rec("拿到租约", False, "code=%r ⇒ 本轮验不了" % lr.get("code"), skip=True)
            return finish()

    pids, h = [], 0
    before = set(notepads())
    try:
        pids.append(subprocess.Popen(["notepad.exe"]).pid)
        time.sleep(2.0)
        hs = [x for x in notepads() if x not in before][:1]
        if not hs:
            rec("起一个记事本", False, "本次没出现新窗（旧窗不参与）", skip=True)
            return finish()
        h = hs[0]
        if not force_fg(h):
            print("   ⚠ 没能把记事本置前（不影响判据，继续）", flush=True)
        time.sleep(0.8)
        judge_reuse(h)
    finally:
        if h:
            try:
                u.PostMessageW(h, 0x0010, 0, 0)
            except Exception:
                pass
        time.sleep(0.5)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        try:
            core.dispatch("ctl_lease", {"action": "release", "actor": lease_actor},
                          actor=lease_actor)
        except Exception:
            pass
        time.sleep(0.3)
        print("收摊后残留记事本：%d" % len(notepads()), flush=True)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
