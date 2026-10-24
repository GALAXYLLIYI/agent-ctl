# -*- coding: utf-8 -*-
"""service.py —— 后台服务的**状态**与**停止**（给面板和命令行用）。

为什么需要它：
  MCP HTTP、急停监听这些进程是用 `pythonw.exe` 起的（刻意不弹控制台窗口），
  于是任务栏/托盘里什么都看不到；以前只能开任务管理器按 PID 杀。
  本模块把「谁在听 8761 / 怎么体面地停掉」收成一个口子。

诚实边界：
  · 只按**端口**找 PID（netstat 解析），不猜进程名；
  · 停止用 `taskkill /PID <pid> /T /F`——**会连带子进程**；急停旗子不受影响；
  · 急停监听（brake_daemon）不在此列：它没有监听端口，需要按 PID 或登录项处理。
"""
import os
import re
import subprocess
import sys
import time

PORT = int(os.environ.get("AGENTCTL_MCP_PORT") or 8761)

# TCP 表里 0.0.0.0:8761 / 127.0.0.1:8761 / [::]:8761 都算
_RX_LISTEN = re.compile(r"^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$", re.I)


def probe(port: int = None) -> dict:
    """返回 {port, listening, pids, error}。不抛异常。"""
    port = int(port or PORT)
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, errors="replace", timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception as e:                                   # noqa: BLE001
        return {"port": port, "listening": False, "pids": [], "error": str(e)[:120]}
    pids = []
    for line in out.splitlines():
        m = _RX_LISTEN.match(line)
        if m and int(m.group(1)) == port:
            pid = int(m.group(2))
            if pid and pid not in pids:
                pids.append(pid)
    return {"port": port, "listening": bool(pids), "pids": pids, "error": None}


def stop(port: int = None) -> dict:
    """停掉监听该端口的进程（含子进程）。返回 {stopped, failed, pids}。"""
    info = probe(port)
    stopped, failed = [], []
    for pid in info["pids"]:
        try:
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, text=True, errors="replace", timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            (stopped if r.returncode == 0 else failed).append(pid)
        except Exception:                                     # noqa: BLE001
            failed.append(pid)
    after = probe(port)
    return {"stopped": stopped, "failed": failed, "pids": info["pids"],
            "listening_after": after["listening"], "port": info["port"]}


def status_line() -> str:
    info = probe()
    if info["error"]:
        return "MCP HTTP :%d —— 查不到（%s）" % (info["port"], info["error"])
    if not info["listening"]:
        base = "MCP HTTP :%d —— 未运行" % info["port"]
    else:
        base = "MCP HTTP :%d —— 运行中（PID %s）" % (info["port"], "、".join(map(str, info["pids"])))
    return base + " ｜ " + daemon_status_line()


# ── 急停监听（brake_daemon）─────────────────────────────────────────
# 为什么归这里管：热键 Ctrl+Alt+Home 与蓝色急停窗都由它负责；它不在跑时，
# 面板「终止」其实生效了（flag 已建立）但**没有任何视觉反馈** —— 2026-10-24 现场踩过。
_STATE = os.environ.get("AGENTCTL_STATE") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AgentCtl", "state")
_DAEMON_PID = os.path.join(_STATE, "brake_daemon.pid")


def _alive(pid: int) -> bool:
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
    except Exception:                                         # noqa: BLE001
        pass
    return False


def daemon_pid():
    """读 pid 文件并确认进程还在；pid 文件只是线索，不是锁。"""
    try:
        pid = int(open(_DAEMON_PID, encoding="utf-8").read().strip())
    except Exception:                                         # noqa: BLE001
        return None
    return pid if _alive(pid) else None


def start_daemon() -> dict:
    """拉起急停监听（幂等）。返回 {started, pid, reason}。"""
    pid = daemon_pid()
    if pid:
        return {"started": False, "pid": pid, "reason": "已在运行"}
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"       # 无控制台窗口
        if os.path.exists(cand):
            exe = cand
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.Popen([exe, "-m", "agentctl.brake_daemon"], cwd=src, env=env,
                             creationflags=flags, close_fds=True)
    except Exception as e:                                     # noqa: BLE001
        return {"started": False, "pid": None, "reason": "启动失败：%s" % e}
    for _ in range(20):                                        # 等它把 pid 文件写出来（≤2s）
        time.sleep(0.1)
        pid = daemon_pid()
        if pid:
            return {"started": True, "pid": pid, "reason": "ok"}
    return {"started": False, "pid": p.pid, "reason": "已拉起但未见 pid 文件"}


def ensure_daemon() -> dict:
    """组件启动 / 看护线程用：没在跑就拉起。"""
    return start_daemon()


def stop_daemon() -> dict:
    pid = daemon_pid()
    if not pid:
        return {"stopped": [], "reason": "未运行"}
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15)
        return {"stopped": [pid], "reason": "ok"}
    except Exception as e:                                     # noqa: BLE001
        return {"stopped": [], "reason": str(e)[:120]}


def daemon_status_line() -> str:
    pid = daemon_pid()
    return ("急停监听 —— 运行中（PID %d）" % pid) if pid else "急停监听 —— **未运行**（热键/弹窗不生效）"
