# -*- coding: utf-8 -*-
r"""守护_mcp_http.py —— 让 MCP over HTTP **开机就在、掉了就回来**（2026-10-21）

为什么要有它（现场事实）：
    §config.toml§ 里 agentctl_http 是 **url 型** MCP ⇒ **Codex 自己不会去拉它**
    （url 型没有 command 可执行）。2026-10-21 实测：服务没起 ⇒ 新会话里
    「25 个工具」一条都不出现，agent 侧只看到「连不上」。
    ⇒ 「装完就能用」的前提是：**这个服务自己会站起来，而且掉了会自己回来**。

它做三件事：
    ① **令牌对账**：把 work\state\token 的值同步到**用户环境变量** AGENTCTL_MCP_TOKEN
       （Codex 侧就是靠这个环境变量鉴权）。两边不一致 ⇒ Codex 会一直 401。
    ② **起服务**：pythonw -m agentctl.mcp_http（无控制台窗口）。
    ③ **看门狗**：端口没人听 ⇒ 重新拉起；服务活着 ⇒ 不动它。

用法（开机自启就靠它；★ 路径写相对本仓的 tools 目录，不写死盘符）：
    pythonw.exe tools\守护_mcp_http.py
    pythonw.exe tools\守护_mcp_http.py --once   # 只对账+起一次，不看门狗

★ 为什么用 pythonw 不用 .cmd 跑 Run 项：Run 项启动 .cmd 会闪一个黑框；
  pythonw 没有控制台，安静。
★ 日志：%LOCALAPPDATA%\AgentCtl\mcp_http_daemon.log（追加，不覆盖）。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "src")
STATE = os.path.join(REPO, "work", "state")
TOKEN_FILE = os.path.join(STATE, "token")
HOST = "127.0.0.1"
PORT = int(os.environ.get("AGENTCTL_MCP_PORT") or "8761")
ENV_NAME = "AGENTCTL_MCP_TOKEN"
LOG = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                   "AgentCtl", "mcp_http_daemon.log")
# ★ 2026-10-21（Codex）：20s → 3s。
#   实测：服务被杀后，看门狗要等一个轮询周期才拉起 ⇒ 崩溃后最长 20 秒不可用
#   （A/B 三轮实测端口回来用了 12.2 / 20.3 / 24.3 / 24.7 秒）。
#   本地 TCP connect 到没人听的端口是**瞬时拒绝**（不是超时），每 3 秒探一次的代价可忽略，
#   而「既开既用」要的就是：崩了 ⇒ 几秒内自己回来。
POLL = 3


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s pid=%d] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), msg))
    except Exception:
        pass


def token() -> str:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def set_user_env(name, value):
    """写用户级环境变量（Codex 进程启动时读的就是这一份）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        os.environ[name] = value
        return True
    except Exception as e:
        log("写环境变量失败 %s: %s" % (name, e))
        return False


def user_env(name) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v or "")
    except Exception:
        return ""


def port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex((HOST, PORT)) == 0


def start_server():
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    tok = token()
    if tok:
        env[ENV_NAME] = tok
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    log("拉起服务：%s -X utf8 -m agentctl.mcp_http" % pyw)
    subprocess.Popen([pyw, "-X", "utf8", "-m", "agentctl.mcp_http"],
                     cwd=SRC, env=env,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))



def main():
    once = "--once" in sys.argv
    tok = token()
    if not tok:
        log("★没有令牌文件 %s —— 先跑一次服务让它自签" % TOKEN_FILE)
        return 2
    cur = user_env(ENV_NAME)
    if cur != tok:
        set_user_env(ENV_NAME, tok)
        log("令牌已对齐用户环境变量 %s（原先 %s…）" % (ENV_NAME, (cur or "空")[:8]))
    else:
        log("令牌与环境变量本就一致")
    if not port_open():
        start_server()
        for _ in range(20):
            time.sleep(0.5)
            if port_open():
                break
    log("端口 %d 状态：%s" % (PORT, "在听" if port_open() else "没起来"))
    if once:
        return 0
    log("进入看门狗循环（每 %ds 巡一次）" % POLL)
    while True:
        time.sleep(POLL)
        if not port_open():
            log("★端口 %d 没人听 ⇒ 重新拉起" % PORT)
            start_server()
            time.sleep(3)
            log("重新拉起后：%s" % ("在听" if port_open() else "还是没起来"))


if __name__ == "__main__":
    sys.exit(main())
