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
        return "MCP HTTP :%d —— 未运行" % info["port"]
    return "MCP HTTP :%d —— 运行中（PID %s）" % (info["port"], "、".join(map(str, info["pids"])))
