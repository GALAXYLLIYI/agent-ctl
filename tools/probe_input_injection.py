# -*- coding: utf-8 -*-
r"""tools\probe_input_injection.py —— **输入注入可用性体检**（键盘 / 鼠标 分开测）

★ 为什么要有这个（2026-10-27 沙箱能力边界探测撞出来的）：

  实测这台机器上：
    · **鼠标注入通** —— SendInput 移动光标精确落点、点击能让前台窗口切换；
    · **键盘注入不通** —— 连**原生控件**（记事本的 Edit）都收不到字符，
      agentctl 自己的 send_unicode（带 INJECT_MAGIC 魔数）也一样不通。
  ⇒ 这不是 agentctl 的 bug，是**本机输入注入被拦**（UIPI / 安全软件 / 远程会话限制）。

  ⇒ 但 agentctl 的 B 级（点击/滚动）能用、C 级（打字/快捷键）不能用，
    **这个区分必须能被机器判出来**，否则 agent 会以为「我打字没成功 = 目标没响应」
    ⇒ 去盲目重试、白白烧动作额度、甚至误判成 code=8「画面没变」。

用法：
    python -X utf8 tools\probe_input_injection.py            # 全测，退出码 0/1
    python -X utf8 tools\probe_input_injection.py --json     # 机读

退出码：
    0 = 键盘与鼠标都通
    1 = **键盘不通**（鼠标通或不通都会报出来）⇒ C 级动作不可用
    2 = 鼠标也不通 ⇒ B/C 级都不可用（环境整体不支持注入）

★ 它会开一个**记事本测试窗**（%TEMP% 下临时文件），测完自己关掉、删掉临时文件。
  不碰任何用户的真实文档。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
SW_RESTORE = 9
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x0040

user32 = ctypes.windll.user32


def _find_notepad():
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if "Notepad" in buf.value or "记事本" in buf.value:
                    out.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return out


def _edit_text(hwnd):
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value == "Edit":
            n = user32.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0)
            buf = ctypes.create_unicode_buffer(n + 2)
            user32.SendMessageW(h, WM_GETTEXT, n + 2, buf)
            res.append(buf.value)
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return res


def _focus(hwnd):
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    fg = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(fg, None)
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(cur, tid, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    user32.AttachThreadInput(cur, tid, False)
    return user32.GetForegroundWindow() == hwnd


def _cursor():
    class P(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    p = P()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def probe() -> dict:
    res = {"keyboard_ok": False, "mouse_ok": False,
           "detail": {}, "note": ""}

    tmp = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"),
                       "_agentctl_input_probe.txt")
    np_proc = subprocess.Popen(["notepad.exe", tmp])
    try:
        hwnd = 0
        for _ in range(40):
            time.sleep(0.5)
            f = _find_notepad()
            if f:
                hwnd = f[0][0]
                break
        if not hwnd:
            res["note"] = "起不了记事本测试窗（没桌面会话？）"
            return res

        got_focus = _focus(hwnd)
        time.sleep(0.6)
        res["detail"]["前台是记事本"] = bool(got_focus)

        # ── 键盘：用 agentctl 自己的注入路径（带 INJECT_MAGIC）────────────
        try:
            sys.path.insert(0, os.path.join(REPO, "legacy_tools"))
            import act  # noqa: E402
            for ch in "HK":
                act.send_unicode(ch)
                time.sleep(0.06)
            time.sleep(0.8)
            txt = "".join(_edit_text(hwnd))
            res["keyboard_ok"] = bool(txt)
            res["detail"]["键盘写入"] = txt or "（空）"
        except Exception as e:
            res["detail"]["键盘"] = "%s: %s" % (type(e).__name__, e)

        # ── 鼠标：光标是否精确落到目标点 ───────────────────────────────────
        try:
            before = _cursor()
            act.click_at(321, 234)
            time.sleep(0.5)
            after = _cursor()
            res["mouse_ok"] = after == (321, 234)
            res["detail"]["光标落点"] = "前=%s 后=%s（目标=(321, 234)）" % (before, after)
        except Exception as e:
            res["detail"]["鼠标"] = "%s: %s" % (type(e).__name__, e)
    finally:
        np_proc.kill()
        try:
            os.remove(tmp)
        except Exception:
            pass

    if not res["keyboard_ok"] and not res["mouse_ok"]:
        res["note"] = ("★ 键盘与鼠标注入**都不通** ⇒ 本机（UIPI / 安全软件 / 远程会话）"
                       "不支持输入注入 ⇒ **B 级与 C 级动作都不可用**")
    elif not res["keyboard_ok"]:
        res["note"] = ("★ **键盘注入不通**（鼠标通）⇒ 本机 **C 级（打字/快捷键）不可用**，"
                       "B 级（点击/滚动）可用。"
                       "⇒ 遇到「点了没反应」先查本条，别盲目重试烧动作额度")
    else:
        res["note"] = "键盘与鼠标注入都通"
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="机读输出")
    a = ap.parse_args()

    res = probe()
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("== 输入注入可用性体检 ==")
        for k, v in res["detail"].items():
            print("  %-14s %s" % (k, v))
        print()
        print("  键盘注入: %s" % ("通" if res["keyboard_ok"] else "不通"))
        print("  鼠标注入: %s" % ("通" if res["mouse_ok"] else "不通"))
        print()
        print(res["note"])
    if not res["mouse_ok"]:
        return 2
    if not res["keyboard_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
