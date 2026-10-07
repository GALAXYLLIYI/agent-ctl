# -*- coding: utf-8 -*-
r"""S5 打包：用 PyInstaller 把 AgentCtl 冻成 onedir

设计依据：详细设计 §9 / §18 / §18.2 官方原文
  · **onedir，不用 onefile** —— 官方原文：onefile 被杀/崩溃时 `_MEI_xxxxxx_` **不清理**，
    常驻服务被反复杀掉会把磁盘吃光；且 onefile 启动更慢、多开不共享。
  · ⛔ **绝不能加 -w/--noconsole** —— 官方原文：那样 "the bootloader starts Python with
    **no target for standard output or input**"，MCP stdio 协议当场死。
  · 用 `--paths src` 带上源码；`--add-data` 带上契约与脚本等运行期要读的文件。

产物：agent-ctl\dist\AgentCtl\AgentCtl.exe（onedir）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")
ENTRY = os.path.join(SRC, "agentctl_main.py")

# 运行期要读的文件（按"打在包里的相对路径"给）
ADD_DATA = [
    (os.path.join(ROOT, "contract", "tools.json"), "contract"),
]

# ★ 这些是**带原生 DLL 的包**，冻结后最容易失效 —— 显式声明，别指望自动发现
HIDDEN = [
    "winrt.windows.media.ocr",
    "winrt.windows.graphics.imaging",
    "winrt.windows.storage.streams",
    "winrt.windows.globalization",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "PIL._tkinter_finder",
]


def main() -> int:
    if not os.path.exists(ENTRY):
        print("入口不存在:", ENTRY)
        return 2
    for d in (DIST, BUILD):
        os.makedirs(d, exist_ok=True)

    cmd = [sys.executable, "-X", "utf8", "-m", "PyInstaller",
           "--noconfirm", "--clean",
           "--onedir",                      # ★ 不用 onefile（见文件头）
           "--name", "AgentCtl",
           "--distpath", DIST,
           "--workpath", BUILD,
           "--specpath", BUILD,
           "--paths", SRC,
           "--paths", os.path.join(ROOT, "vendor"),   # winrt / windows_capture
           "--paths", os.path.join(ROOT, "stubs"),    # cv2 stub（顶掉 42 MB 的 OpenCV）
           "--collect-submodules", "winrt",
           "--collect-submodules", "uiautomation",
           "--collect-submodules", "comtypes",
           ]
    for m in HIDDEN:
        cmd += ["--hidden-import", m]
    for src, dst in ADD_DATA:
        cmd += ["--add-data", f"{src}{os.pathsep}{dst}"]
    # ⛔ 这里**故意不加** --windowed / -w
    cmd += [ENTRY]

    print("开始打包，命令：")
    print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    t0 = time.time()
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    dt = time.time() - t0
    tail = (p.stdout or "").strip().splitlines()[-12:]
    print(f"\nPyInstaller 退出码 {p.returncode}，耗时 {dt:.0f} 秒")
    for ln in tail:
        print("   |", ln)
    if p.returncode != 0:
        print("\n--- stderr 末尾 ---")
        for ln in (p.stderr or "").strip().splitlines()[-25:]:
            print("   !", ln)
        return p.returncode

    exe = os.path.join(DIST, "AgentCtl", "AgentCtl.exe")
    if not os.path.exists(exe):
        print("❌ 没找到产物 exe:", exe)
        return 3
    total = 0
    n = 0
    for dp, _dn, fn in os.walk(os.path.join(DIST, "AgentCtl")):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f)); n += 1
            except OSError:
                pass
    print(f"\n✅ 产物：{exe}")
    print(f"   目录合计 {total/1024/1024:.1f} MB / {n} 个文件")
    print(f"   exe 本体 {os.path.getsize(exe)/1024/1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
