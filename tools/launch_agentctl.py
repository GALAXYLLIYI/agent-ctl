# -*- coding: utf-8 -*-
r"""tools/launch_agentctl.py —— agentctl 全家桶一键启动器（工单：派单_打包启动器_20261026）

形态（台账已定）：
    python tools\launch_agentctl.py            # 起 服务+面板（幂等：已在跑的跳过）
    python tools\launch_agentctl.py --with-loop --loop-steps 5   # 加起循环 demo
    python tools\launch_agentctl.py --status   # 各件状态
    python tools\launch_agentctl.py --stop     # 反向收尾：循环→面板→服务，零残留

幂等硬要求：连跑两次不双开；--stop 后无残留（按命令行特征匹配进程，不误杀无关 python）。
面板/服务用 DETACHED_PROCESS 拉起（不随终端死）；pythonw 免黑窗。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import socket
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, "src")
_PYTHON = sys.executable
_PYTHONW = os.path.join(os.path.dirname(_PYTHON), "pythonw.exe")
MCP_PORT = int(os.environ.get("AGENTCTL_MCP_PORT", "8761"))
PANEL_TITLE = "Windows agent-ctl 组件"

DETACHED = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def _ps(cmd: str) -> str:
    """借 PowerShell 查进程（按命令行特征），输出 PID 列表。GBK 输出容错（cmd 返回的是本地码页）。"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True, timeout=30,
    )
    return (out.stdout or b"").decode("utf-8", errors="replace").strip()


def _pids_by_marker(marker: str) -> list[int]:
    """按命令行包含 marker 找 PID（Get-CimInstance 比 tasklist 准）。"""
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" "
        "| Where-Object { $_.CommandLine -like '*%s*' } "
        "| ForEach-Object { $_.ProcessId }" % marker.replace("'", "''")
    )
    pids = []
    for line in _ps(cmd).splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _window_exists(title: str) -> bool:
    """枚举顶层窗找精确标题（面板 tkinter 窗）。"""
    user32 = ctypes.windll.user32
    EnumWindows = user32.EnumWindows
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = []

    @WNDENUMPROC
    def _cb(hwnd, _l):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title:
            found.append(hwnd)
        return True

    EnumWindows(_cb, None)
    return bool(found)


def service_alive() -> bool:
    """8761 端口可连 ⇒ 服务活着（不要求 MCP 握手成功）。"""
    try:
        with socket.create_connection(("127.0.0.1", MCP_PORT), timeout=1.0):
            return True
    except OSError:
        return False


def start_service() -> bool:
    if service_alive():
        print(f"[service] 已在跑（port {MCP_PORT}）—— 跳过")
        return True
    exe = _PYTHONW if os.path.exists(_PYTHONW) else _PYTHON
    subprocess.Popen(
        [exe, "-X", "utf8", "-m", "agentctl.mcp_http"],
        cwd=_REPO, env={**os.environ, "PYTHONPATH": _SRC},
        creationflags=DETACHED | CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        time.sleep(0.5)
        if service_alive():
            print(f"[service] 已拉起（port {MCP_PORT}）")
            return True
    print("[service] 拉起失败（端口 30s 内未就绪）")
    return False


def start_panel() -> bool:
    if _window_exists(PANEL_TITLE):
        print("[panel] 已在跑 —— 跳过")
        return True
    exe = _PYTHONW if os.path.exists(_PYTHONW) else _PYTHON
    subprocess.Popen(
        [exe, "-X", "utf8", "-m", "agentctl.widget"],
        cwd=_REPO, env={**os.environ, "PYTHONPATH": _SRC},
        creationflags=DETACHED,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(12):
        time.sleep(0.5)
        if _window_exists(PANEL_TITLE):
            print("[panel] 已拉起")
            return True
    print("[panel] 拉起失败（窗口 6s 内未出现）")
    return False


def start_loop(steps: int, text: str) -> bool:
    if _pids_by_marker("agent_loop.py"):
        print("[loop] 已在跑 —— 跳过")
        return True
    exe = _PYTHONW if os.path.exists(_PYTHONW) else _PYTHON
    subprocess.Popen(
        [exe, "-X", "utf8", os.path.join(_HERE, "agent_loop.py"),
         "--max-steps", str(steps), "--text", text],
        cwd=_REPO, env={**os.environ, "PYTHONPATH": _SRC},
        creationflags=DETACHED | CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"[loop] 已拉起（steps={steps}）")
    return True


def stop_all() -> None:
    """反向收尾：循环 → 面板 →（可选）服务。按命令行/标题特征杀，不碰无关 python。"""
    for marker in ("agent_loop.py", "agentctl.widget", "agentctl.mcp_http"):
        pids = _pids_by_marker(marker)
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            print(f"[stop] pid={pid} ({marker})")
    print("[stop] 残留检查: " + (
        "零残留" if not (_pids_by_marker("agent_loop.py")
                        or _pids_by_marker("agentctl.widget")
                        or _pids_by_marker("agentctl.mcp_http")) else "仍有残留!"))


def status() -> None:
    rows = []
    rows.append(("service", "UP" if service_alive() else "DOWN",
                 f"port {MCP_PORT}"))
    rows.append(("panel", "UP" if _window_exists(PANEL_TITLE) else "DOWN", PANEL_TITLE))
    rows.append(("loop", "UP" if _pids_by_marker("agent_loop.py") else "DOWN",
                 "agent_loop.py"))
    rows.append(("brake_daemon", "UP" if _pids_by_marker("brake_daemon") else "DOWN", ""))
    for name, st, extra in rows:
        print(f"  {name:<12} {st:<5} {extra}")


def main() -> int:
    ap = argparse.ArgumentParser(description="agentctl 全家桶启动器")
    ap.add_argument("--with-loop", action="store_true", help="同时起循环 demo")
    ap.add_argument("--loop-steps", type=int, default=5)
    ap.add_argument("--loop-text", default="循环测试")
    ap.add_argument("--stop", action="store_true", help="全部收尾")
    ap.add_argument("--status", action="store_true", help="只看状态")
    a = ap.parse_args()

    if a.status:
        status()
        return 0
    if a.stop:
        stop_all()
        return 0

    ok = start_service()
    ok = start_panel() and ok
    if a.with_loop:
        time.sleep(1.0)  # 服务先就绪
        ok = start_loop(a.loop_steps, a.loop_text) and ok
    print("[done] " + ("全家桶就绪" if ok else "有件失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
