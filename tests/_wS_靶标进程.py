# -*- coding: utf-8 -*-
r"""tests\_wS_靶标进程.py —— 端到端判据的**靶窗宿主进程**（窗S 夹具，DSH 2026-10-17 拆出来）

为什么单独一个进程：
    自指保护（窗K/P0）会按**身份**拦「把动作打给自己」——同进程自建自点会被正确拒绝
    （code=3）。那不是缺陷，是设计。⇒ 靶窗必须活在**别的进程**里，判据才点得到。

用法（判据里起它）：
    python -X utf8 tests\_wS_靶标进程.py <报告文件> [停止文件] [最长寿命秒]

    · 建好窗后把 {"top":hwnd,"btn":hwnd,"pid":pid} 写进 <报告文件>（先写 .tmp 再改名，读端不会读到半个）；
    · 之后一直泵消息；**发现 <停止文件> 出现**（或到寿命）就关窗退出；
    · ★ 不用 stdin/stdout 握手：父进程 readline 在子进程沉默时会永久阻塞（第一版就挂死在这）。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

u32 = ctypes.windll.user32
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
BS_CHECKBOX = 0x00000002


def main():
    report = sys.argv[1] if len(sys.argv) > 1 else ""
    stop = sys.argv[2] if len(sys.argv) > 2 else ""
    life = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
    if not report:
        print("用法: _wS_靶标进程.py <报告文件> [停止文件] [寿命秒]", flush=True)
        return 2
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    hInst = ctypes.windll.kernel32.GetModuleHandleW(None)
    top = u32.CreateWindowExW(0, "STATIC", "窗S_自起测试靶",
                              WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                              300, 300, 360, 180, None, None, hInst, None)
    if not top:
        _write(report, {"error": "CreateWindowExW(STATIC) 失败"})
        return 2
    btn = u32.CreateWindowExW(0, "BUTTON", "靶：点我（窗S 自起）",
                              WS_CHILD | WS_VISIBLE | BS_CHECKBOX,
                              30, 45, 260, 30, top, None, hInst, None)
    if not btn:
        _write(report, {"error": "CreateWindowExW(BUTTON) 失败"})
        return 2
    u32.SetForegroundWindow(top)
    msg = wt.MSG()
    _pump(msg, 0.4)
    _write(report, {"top": int(top), "btn": int(btn), "pid": os.getpid()})

    t0 = time.time()
    while time.time() - t0 < life:
        _pump(msg, 0.05)
        if stop and os.path.exists(stop):
            break
    try:
        u32.DestroyWindow(btn)
        u32.DestroyWindow(top)
    except Exception:
        pass
    return 0


def _pump(msg, seconds):
    end = time.time() + seconds
    while time.time() < end:
        while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
