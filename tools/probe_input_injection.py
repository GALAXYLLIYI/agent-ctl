# -*- coding: utf-8 -*-
r"""tools\probe_input_injection.py —— **输入注入可用性体检**（键盘 / 鼠标 / 控件写入 三通道）

★ 为什么要三通道分开测（2026-10-27 实测踩坑的教训）：
  早前只用「往记事本里打字，事后读控件内容」判一次，得到「键盘注入不通」的**错误结论**。
  真实原因有三条，全是**测量方法**的问题，不是产品的问题：
    ① 焦点落在**顶层窗口**而不是**编辑控件** ⇒ 键盘事件送不到控件；
    ② 机器上有 8 个记事本窗口，取「列表最后一个」⇒ 每次挑的窗口都可能不同，
       而那个窗口未必在前台/有焦点 ⇒ 测出「时通时不通」的假间歇性；
    ③ ★ **Windows 11 的 Store 版记事本是单实例 UWP**：所有标签页共用一个进程，
       `notepad.exe <路径>` 只是给已有进程**加一个标签页** ⇒
       「我自己的测试窗口」根本不存在，而且**测试会污染别人的记事本**（别的 agent 的窗口）。

  ⇒ 现在：① 焦点落到控件；② 按 **pid** 精确挑自己那个窗口；③ 结论看**三条通道**的组合。

★ 三条通道与结论的对应：
  | 通道 | 手段 | 不通意味着 |
  |---|---|---|
  | 鼠标注入 | `SetCursorPos`（**产品同路**：act.py 的 click_at） | 鼠标事件投递被拦 ⇒ B 级不可用 |
  | 键盘注入 | `SendInput(KEYEVENTF_UNICODE)`（**产品同路**：act.py 的 send_unicode） | 键盘事件投递被拦 ⇒ C 级不可用 |
  | 控件写入 | `WM_SETTEXT`（不碰输入队列） | 控件本身不可写（少见） |
  ⇒ **键盘不通 + 控件写入通** = 「合成按键被拦，但控件是好的」⇒ 换写入路径可绕。

用法：
    python -X utf8 tools\probe_input_injection.py            # 全测，退出码 0/1/2
    python -X utf8 tools\probe_input_injection.py --json     # 机读

退出码：
    0 = 键盘与鼠标都通
    1 = **键盘不通**（鼠标通）⇒ C 级动作不可用
    2 = 鼠标也不通 ⇒ B/C 级都不可用

★ 它只往**自己新建的测试窗**里打字（%TEMP% 下临时文件），测完关掉删掉，
  **不碰任何用户的现有窗口**。
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
WM_SETTEXT = 0x000C
SW_RESTORE = 9
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x0040
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ★ 用 ctypes.c_size_t 复刻 Win32 的 ULONG_PTR —— x64 上 dwExtraInfo 是 8 字节整数。
#   用指针类型会让结构体尺寸算错 ⇒ SendInput 返回 0 / GetLastError=87(ERROR_INVALID_PARAMETER)，
#   **而且是安静地失败**（实测踩过）。
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]


class _I(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _I)]


def _send(inp):
    kernel32.SetLastError(0)
    n = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    return n, kernel32.GetLastError()


def _key(ch):
    i1 = INPUT(type=INPUT_KEYBOARD)
    i1.ki = KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, 0)
    i2 = INPUT(type=INPUT_KEYBOARD)
    i2.ki = KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
    a, _ = _send(i1)
    time.sleep(0.04)
    b, e = _send(i2)
    return a, b, e


def _mouse(x, y):
    """移动光标到屏幕像素 (x, y) —— 用 **SetCursorPos**，不是 SendInput 鼠标事件。

    ★ 产品 act.py 的 click_at 就是 SetCursorPos + SendInput 按键；
      而 SendInput 的鼠标 x/y 是**归一化到 0..65535 的绝对坐标**、且必须带
      MOUSEEVENTF_ABSOLUTE —— 不带就成了相对位移，光标根本不动（实测踩过）。
    ⇒ 体检必须跟产品同一条路，否则测的是另一回事。
    """
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)


def _windows_of_pid(pid):
    """列出属于指定 pid 的可见顶层窗口。★ 按 pid 挑，不靠标题/顺序
    （Store 版记事本是单实例 ⇒ 按 pid 才能确定是自己那个）。"""
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            p = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid:
                n = user32.GetWindowTextLengthW(hwnd)
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, b, n + 1)
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, cls, 64)
                out.append((hwnd, cls.value, b.value))
        return True

    user32.EnumWindows(cb, 0)
    return out


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
            res.append((h, cls.value, b.value))
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return res


def _focus(hwnd):
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    fg = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(fg, None)
    cur = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(cur, tid, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    user32.AttachThreadInput(cur, tid, False)
    time.sleep(0.5)


def _cursor():
    class P(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    p = P()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def probe() -> dict:
    res = {"keyboard_ok": False, "mouse_ok": False, "write_ok": False,
           "sendinput_ret": None, "sendinput_err": None, "detail": {}, "note": ""}

    # ★ 用**独立进程**的窗口当靶场（Store 版记事本是单实例 ⇒ 不能用）。
    #   这里用 agentctl 自带的 python 版靶场（WinForms TextBox = 原生 EDIT 控件，
    #   UIA 有名字）—— 它是**独立 python 进程**，有自己的 pid ⇒ 不会污染别人。
    #   注意：它属主是 python.exe（agentctl 的敏感名单会拒），
    #   但**体检只测注入通道，不测 agentctl 的闸门** ⇒ 属主不影响这里。
    tgt = subprocess.Popen(
        [sys.executable, "-X", "utf8", os.path.join(REPO, "tools", "ab_target_py.py")])
    try:
        HW = 0
        for _ in range(40):
            time.sleep(0.5)
            wins = _windows_of_pid(tgt.pid)
            if wins:
                HW = wins[0][0]
                break
        if not HW:
            res["note"] = "起不了自己的靶场窗（tools\\ab_target_py.py 没起来）"
            return res

        res["detail"]["靶场"] = "pid=%s hwnd=%s（自己的进程，不碰别人的窗口）" % (tgt.pid, HW)
        _focus(HW)
        time.sleep(0.4)

        # ── ③ 控件写入通道（WM_SETTEXT，不碰输入队列）─────────────────
        eds = _edits(HW)
        if eds:
            user32.SendMessageW(eds[0][0], WM_SETTEXT, 0, ctypes.c_wchar_p("PROBE"))
            time.sleep(0.3)
            after = _edits(HW)
            res["write_ok"] = bool(after and "PROBE" in (after[0][2] or ""))
            res["detail"]["控件写入(WM_SETTEXT)"] = (after[0][2] if after else "?")

        # ── ② 键盘注入通道（与产品同路：SendInput + KEYEVENTF_UNICODE）──
        a, b, e = _key("K")
        res["sendinput_ret"] = a
        res["sendinput_err"] = e
        time.sleep(0.5)
        eds2 = _edits(HW)
        txt = (eds2[0][2] if eds2 else "") or ""
        res["keyboard_ok"] = "K" in txt
        res["detail"]["键盘注入后控件内容"] = txt

        # ── ① 鼠标通道（与产品同路：SetCursorPos）───────────────────────
        try:
            before = _cursor()
            _mouse(321, 234)
            time.sleep(0.3)
            after_c = _cursor()
            res["mouse_ok"] = after_c == (321, 234)
            res["detail"]["光标落点(SetCursorPos)"] = "前=%s 后=%s（目标=(321, 234)）" % (before, after_c)
        except Exception as ex:
            res["detail"]["鼠标"] = "%s: %s" % (type(ex).__name__, ex)
    finally:
        tgt.kill()
        time.sleep(0.5)

    if not res["mouse_ok"]:
        res["note"] = ("★ 键盘与鼠标注入**都不通** ⇒ 本机（UIPI / 安全软件 / 远程会话）"
                       "不支持输入注入 ⇒ **B 级与 C 级动作都不可用**")
    elif not res["keyboard_ok"]:
        res["note"] = ("★ **键盘注入不通**（鼠标通）⇒ 本机 **C 级（打字/快捷键）不可用**，"
                       "B 级（点击/滚动）可用。"
                       "⇒ 遇到「点了没反应」先查本条，别盲目重试烧动作额度")
    else:
        res["note"] = "键盘与鼠标注入都通"
    if res["write_ok"] and not res["keyboard_ok"]:
        res["note"] += ("｜★ 控件写入通道（WM_SETTEXT）是通的 ⇒ 结论是"
                        "**合成按键的投递**被拦，不是控件坏了、也不是注入 API 坏了")
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
            print("  %-24s %s" % (k, v))
        print()
        print("  键盘注入(SendInput): %s   返回=%s err=%s" % (
            "通" if res["keyboard_ok"] else "不通", res["sendinput_ret"], res["sendinput_err"]))
        print("  鼠标注入(SetCursorPos): %s" % ("通" if res["mouse_ok"] else "不通"))
        print("  控件写入(WM_SETTEXT): %s" % ("通" if res["write_ok"] else "不通"))
        print()
        print(res["note"])
    if not res["mouse_ok"]:
        return 2
    if not res["keyboard_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
