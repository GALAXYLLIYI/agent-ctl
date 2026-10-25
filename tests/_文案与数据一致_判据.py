# -*- coding: utf-8 -*-
r"""tests\_文案与数据一致_判据.py —— 第 36 条判据：**日志说的话，必须和回执的数据一致**。

起因（窗口D 2026-10-19 自曝的第 4 类病，当时一条判据都没有）：
  滚轮分支删掉 `return 0` 后漏了 `else` ⇒ 画面**变了**（`_ch=True`）时，
  第二条 log 仍按 else 渲染成「画面**没变** ⇒ 落回前台兜底」。
  **数据说变了，日志说没变** —— 判据 31/32/34 都查控制流，这一类查的是**文案与数据的对应关系**。

做法（不看关键词猜，只看**同一次动作的三方对账**）：
  每次动作后同时拿三样：① 回执 code ② 回执 after.verdict ③ act.log 的新增行
  然后按"变了/没变"两条语义断言：
    · code=0 或 verdict=met  ⇒ 日志**不许**出现「没变」
    · code=8 或 verdict=no_effect_in_region ⇒ 日志**必须**出现「没变」或「判不了(unknown)」
  任一不一致 ⇒ 红。

自证（这条判据自己也得能被证伪）：
  内置一对**已知坏样本**（code=0 配「画面没变」日志）交给同一个检查函数，它必须判 False。
  `--inject` 把检查函数换成"恒 True" ⇒ 这对坏样本就抓不出来了 ⇒ 本判据必定红。

退出码：0 绿 / 1 红 / 2 验不了（无通行证 / 起不出窗 / 读不到 act.log）。
"""
import ctypes
import ctypes.wintypes as wt
import json
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
ACT_LOG = ""      # ★ 不写死：main() 里从 ctl_status 的 auth_dir 推导


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
        print("结论：【红】日志与数据不自洽（数据说变了，日志说没变 —— 或反之）", flush=True)
        return 1
    print("结论：【绿】日志与回执三方对账一致，且判据自身的坏样本能被抓出", flush=True)
    return 0


def log_mark():
    """记录 act.log 当前字节偏移（动作前调用）。读不到返回 None。"""
    try:
        return os.path.getsize(ACT_LOG)
    except Exception:
        return None


def log_since(offset, n_lines=8):
    """只读**本次动作之后新增**的日志（读不到返回 None，**不假装空**）。

    ★ 教训：动作后读"最后 8 行"会把上一个动作的「没变」也读进来 ⇒ 假红。
    """
    try:
        if offset is None:
            return None
        with open(ACT_LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            text = f.read()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return lines[-n_lines:]
    except Exception:
        return None


def says_unchanged(text):
    """act.py 的"没变"用词（★ 实测口径 2026-10-19 现场取词）：
       成功：「✔ 页面已变化」｜失败：「⚠⚠ 页面无变化」「〔无变化〕」
    ⇒ 认「无变化 / 未变化 / 没变 / unknown / 判不了」；**不认「已变化」**。
    （判据过严也会假红：原本只认"没变"，于是把"无变化"当没写判词 → 误报。）
    """
    t = text or ""
    if "已变化" in t:
        return False
    return ("无变化" in t) or ("未变化" in t) or ("没变" in t) or \
           ("unknown" in t) or ("判不了" in t)


def check_consistency(code, verdict, logtext):
    """核心断言：变了不许说没变；没变必须说没变。返回 (ok, why)。"""
    changed = (code == 0) or (verdict == "met")
    unchanged = (code == 8) or (verdict == "no_effect_in_region")
    said_unchanged = says_unchanged(logtext)
    if changed and said_unchanged:
        return False, "code=%r verdict=%r 但日志说『没变/unknown』" % (code, verdict)
    if unchanged and not said_unchanged:
        return False, "code=%r verdict=%r 但日志没说『没变』" % (code, verdict)
    return True, "一致"


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
    time.sleep(0.3)


def ver(h):
    r = core.dispatch("ctl_screen", {"hwnd": h, "actor": "判据36"}, actor="判据36")
    d = r.get("data") or {}
    inner = d.get("结果") if isinstance(d.get("结果"), dict) else d
    return inner.get("screen_version")


def main():
    global check_consistency
    if INJECT:
        # 注入：把检查函数打成"恒通过"（= 不检查）⇒ 下面的坏样本自证必然失败 ⇒ 判据红
        check_consistency = lambda *_a, **_k: (True, "注入：不检查")
        print("★ 注入：check_consistency 恒 True（等价于不检查）", flush=True)

    st = core.dispatch("ctl_status", {"actor": "判据36"}, actor="判据36").get("data") or {}
    st = st.get("结果") if isinstance(st.get("结果"), dict) else st
    if (st or {}).get("brake", {}).get("engaged"):
        rec("急停未拉下", False, "急停 flag 存在", skip=True)
        return finish()
    try:
        with open(os.path.join((st or {}).get("auth_dir") or "", "auth_pass.json"),
                  encoding="utf-8") as f:
            exp = json.load(f).get("expires_at") or 0
        left = int(exp) - int(time.time())
    except Exception:
        left = 0
    if left <= 0:
        rec("有有效通行证", False, "无有效通行证（剩 %ds）" % left, skip=True)
        return finish()
    global ACT_LOG
    ACT_LOG = os.path.join((st or {}).get("auth_dir") or "", "act.log")
    if log_mark() is None:
        rec("能读到 act.log", False, "读不到 %s ⇒ 验不了" % ACT_LOG, skip=True)
        return finish()

    lr = core.dispatch("ctl_lease", {"action": "acquire", "scopes": "ABC", "actor": "判据36"},
                       actor="判据36")
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

        # 动作一：打字（必然"变了"）
        mark = log_mark()
        t1 = core.dispatch("ctl_type", {"hwnd": h, "text": "一致性探针", "mode": "focus",
                                       "allow_foreground": True, "actor": "判据36"}, actor="判据36")
        if t1.get("code") == 9:
            rec("有可用动作额度", False, "code=9 额度用尽", skip=True)
            return finish()
        time.sleep(0.6)
        tail1 = "".join(log_since(mark, 8) or [])
        rec("① 打字（变）⇒ code=%r 且日志不说『没变』" % (t1.get("code"),),
            t1.get("code") in (0, 8),
            "code=%r" % (t1.get("code"),))
        ok1, why1 = check_consistency(t1.get("code"), (t1.get("data") or {})
                                      .get("after", {}).get("verdict") if isinstance(
                                          t1.get("data"), dict) else None, tail1)
        rec("① 打字的三方对账一致", ok1, why1)

        # 动作二：同一位置连点两次（第二次多半"没变"）
        for i in (1, 2):
            fg(h)
            mark = log_mark()
            c = core.dispatch("ctl_click", {"hwnd": h, "xy": [400, 300], "frame_id": ver(h),
                                           "allow_foreground": True, "actor": "判据36"},
                              actor="判据36")
            time.sleep(0.6)
            tail = "".join(log_since(mark, 8) or [])
            v2 = None
            try:
                v2 = ((c.get("data") or {}).get("结果") or {}).get("after", {}).get("verdict")
            except Exception:
                pass
            ok2, why2 = check_consistency(c.get("code"), v2, tail)
            rec("② 第 %d 次点击（code=%r）三方对账一致" % (i, c.get("code")), ok2, why2)

        # ③ 判据自证：已知坏样本必须被判不一致
        ok3, why3 = check_consistency(0, "met", "后台滚轮投递了但画面没变 ⇒ 落回前台兜底")
        rec("③ 判据自证：『code=0 但日志说没变』必须被判不一致", not ok3, why3)
    finally:
        if h:
            try:
                u.PostMessageW(h, 0x0010, 0, 0)
            except Exception:
                pass
        time.sleep(0.4)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        core.dispatch("ctl_lease", {"action": "release", "actor": "判据36"}, actor="判据36")
        print("收摊后残留记事本：%d" % len(notepads()), flush=True)
    return finish()


if __name__ == "__main__":
    INJECT = "--inject" in sys.argv
    sys.exit(main())
