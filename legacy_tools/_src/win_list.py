# -*- coding: utf-8 -*-
r'''列窗口：找出 Trae / WorkBuddy / MiMo / 浏览器等目标窗口的 hwnd

用法：
  python win_list.py                # 列出所有有标题的可见窗口
  python win_list.py Trae WorkBuddy # 只列标题/进程名匹配这些关键词的

只读操作（EnumWindows），不碰键鼠，不走闸门。
'''
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
IsWindowVisible = user32.IsWindowVisible
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetClassNameW = user32.GetClassNameW
OpenProcess = kernel32.OpenProcess
CloseHandle = kernel32.CloseHandle
QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def proc_path(hwnd):
    pid = wt.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return pid.value, ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return pid.value, buf.value
        return pid.value, ""
    finally:
        CloseHandle(h)


def cls_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, 256)
    return buf.value


def title_of(hwnd):
    n = GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 2)
    GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def collect():
    out = []

    def cb(hwnd, _):
        if not IsWindowVisible(hwnd):
            return True
        t = title_of(hwnd)
        if not t.strip():
            return True
        pid, path = proc_path(hwnd)
        out.append({
            "hwnd": int(hwnd),
            "hwnd_hex": "0x%08X" % int(hwnd),
            "title": t,
            "cls": cls_of(hwnd),
            "pid": pid,
            "exe": path.split("\\")[-1] if path else "?",
            "path": path,
        })
        return True

    EnumWindows(EnumWindowsProc(cb), 0)
    return out


def main():
    kws = [a for a in sys.argv[1:] if not a.startswith("-")]
    wins = collect()
    if kws:
        low = [k.lower() for k in kws]
        wins = [w for w in wins
                if any(k in w["title"].lower() or k in w["exe"].lower() or k in w["path"].lower()
                       for k in low)]
    print("共 %d 个窗口" % len(wins))
    print("%-12s %-26s %-8s %-24s %s" % ("hwnd", "进程", "pid", "窗口类", "标题"))
    print("-" * 130)
    for w in sorted(wins, key=lambda x: x["exe"]):
        print("%-12s %-26s %-8d %-24s %s"
              % (w["hwnd_hex"], w["exe"][:25], w["pid"], w["cls"][:23], w["title"][:70]))


if __name__ == "__main__":
    main()
