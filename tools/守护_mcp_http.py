# -*- coding: utf-8 -*-
r"""守护_mcp_http.py —— 让 MCP over HTTP **开机就在、掉了就回来**（2026-10-21）

为什么要有它（现场事实）：
    §config.toml§ 里 agentctl_http 是 **url 型** MCP ⇒ **窗口D 自己不会去拉它**
    （url 型没有 command 可执行）。2026-10-21 实测：服务没起 ⇒ 新会话里
    「25 个工具」一条都不出现，agent 侧只看到「连不上」。
    ⇒ 「装完就能用」的前提是：**这个服务自己会站起来，而且掉了会自己回来**。

它做四件事：
    ① **令牌对账**：把 work\state\token 的值同步到**用户环境变量** AGENTCTL_MCP_TOKEN
       （窗口D 侧就是靠这个环境变量鉴权）。两边不一致 ⇒ 窗口D 会一直 401。
    ② **起服务**：pythonw -m agentctl.mcp_http（无控制台窗口）。
    ③ **看门狗**：端口没人听 ⇒ 重新拉起；服务活着 ⇒ 不动它。
       ★ 轮询 20 秒 → **3 秒**（实测：崩了以后端口回来 12~25 秒 → 3.2~6.9 秒）。
    ④ **暖机**：起来之后**自己先调一次** tools/call。
       ★ 为什么：新进程的第一个 tools/call 要付约 400~430ms 的一次性代价
       （实测 409.5ms；第二次起 6~30ms；同一台服务器换另一个客户端来，首调只要 7ms
       ⇒ 是「服务器进程 × 首次 tools/call」的一次性代价，不是会话、也不是客户端的事）。
       暖机替 agent 先付掉 ⇒ agent 接手时第一调用就是热的（实测 7ms）。
       ★ 暖机**必须用 tools/call**（tools/list 不走 dispatch 那条路，暖不了）。

★ 起动期间**不许重复拉起**（2026-10-21 实测踩到）：服务起动要好几秒（python 启动＋导入），
  3 秒轮询会在它还没监听端口时又拉一个 ⇒ 连起三个实例互相抢端口。
  ⇒ 起动后**轮询等就绪**（最多 25 秒），并且**启动期不重复拉起**（30 秒内不再试）。

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
POLL = 3            # ★ 2026-10-21：20 → 3（本地 TCP connect 到没人听的端口是瞬时拒绝，代价可忽略）
START_WAIT = 25.0   # 起动后等端口的最长时间


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s pid=%d] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), msg) + chr(10))
    except Exception:
        pass


def token() -> str:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def set_user_env(name, value):
    """写用户级环境变量（窗口D 进程启动时读的就是这一份）。"""
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


def start_and_wait():
    """起服务并**等它真的在听**。

    ★ 这一步是**阻塞**的（最多 START_WAIT 秒）⇒ 看门狗循环不可能在起动期间重入
    ⇒ **天然不会重复拉起**。2026-10-21 实测：起动要几秒，3 秒轮询若不等就绪会连起三个实例
    （后来又加过 30 秒冷却 ⇒ 反而把合法重启挡住，端口回来要 30 秒，已回退）。

    ★ 实测：服务从拉起到端口在听约 **0.8 秒**。

    返回 (起来了吗, 等了多久秒)。
    """
    start_server()
    t0 = time.time()
    while time.time() - t0 < START_WAIT:
        time.sleep(0.5)
        if port_open():
            return True, time.time() - t0
    return False, time.time() - t0


def warmup():
    """★ 暖机：新进程的第一个 tools/call 要付约 400~430ms 的一次性代价。
    这里**替 agent 先付掉**：暖机自己调一次 tools/call，之后 agent 接手时第一调用就是热的。

    ★ 每一步单独计时写日志；★ 回执里带 error 的要当没暖成功（JSON-RPC 的 error 不抛异常）。
    """
    import json
    import urllib.request
    tok = token()
    hdr = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}
    if tok:
        hdr["Authorization"] = "Bearer " + tok

    def rpc(method, params, sid=None):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        h = dict(hdr)
        if sid:
            h["Mcp-Session-Id"] = sid
        req = urllib.request.Request("http://%s:%d/mcp" % (HOST, PORT),
                                     data=json.dumps(body).encode("utf-8"), headers=h)
        r = urllib.request.urlopen(req, timeout=30)
        return r.read(), r.headers.get("Mcp-Session-Id")

    marks = []
    t_all = time.time()
    try:
        t0 = time.time()
        _, sid = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "agentctl-warmup", "version": "1"}})
        marks.append("initialize %.0fms" % ((time.time() - t0) * 1000))
        try:
            rpc("notifications/initialized", {}, sid)
        except Exception:
            pass
        t0 = time.time()
        rpc("tools/list", {}, sid)
        marks.append("tools/list %.0fms" % ((time.time() - t0) * 1000))
        t0 = time.time()
        raw, _ = rpc("tools/call", {"name": "ctl_status", "arguments": {}}, sid)
        marks.append("tools/call %.0fms" % ((time.time() - t0) * 1000))
        body = raw.decode("utf-8", "replace")
        if '"error"' in body[:400]:
            log("★暖机没成功（回执是 error）：%s" % body[:200])
            return False
        log("暖机完成（%s；共 %.0fms）—— agent 接手时第一调用已经是热的"
            % (" / ".join(marks), (time.time() - t_all) * 1000))
        return True
    except Exception as e:
        log("暖机没成功（不影响服务，只是首调慢一点）：%s: %s" % (type(e).__name__, e))
        return False


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
        ok, dt = start_and_wait()
        log("起服务：%s（等了 %.1f 秒）" % ("在听" if ok else "还没起来", dt))
    log("端口 %d 状态：%s" % (PORT, "在听" if port_open() else "没起来"))
    if port_open():
        warmup()
    if once:
        return 0
    log("进入看门狗循环（每 %ds 巡一次）" % POLL)
    while True:
        time.sleep(POLL)
        if not port_open():
            log("★端口 %d 没人听 ⇒ 重新拉起" % PORT)
            ok, dt = start_and_wait()
            log("重新拉起后：%s（等了 %.1f 秒）" % ("在听" if ok else "还是没起来", dt))
            if ok:
                warmup()
        else:
            # 端口有人听，但可能刚好是「另一个实例」在服务 ⇒ 不做任何事
            pass


if __name__ == "__main__":
    sys.exit(main())
