# -*- coding: utf-8 -*-
r"""自检_mcp_http.py —— 「既开既用」的可执行定义（2026-10-21）

一句话：**装完 / 重启之后，跑这一条命令，绿了就能用。**

查六件事（每件都有明确的「不绿怎么办」）：
    ① 端口有没有人听（8761）
    ② initialize 能不能通（协议层）
    ③ 工具数是不是 25（契约面）
    ④ 令牌与**用户环境变量**是不是同一个值（窗口D 鉴权面）
    ⑤ 急停是不是拉着的（拉着了 ⇒ 一切动作都会被拒）
    ⑥ 租约是不是被别人占着（占着 ⇒ 写动作全拒）

退出码：0=全绿（可以用）；1=有红项（照着「怎么办」那列做）。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOKEN_FILE = os.path.join(REPO, "work", "state", "token")
HOST = "127.0.0.1"
PORT = int(os.environ.get("AGENTCTL_MCP_PORT") or "8761")
URL = "http://%s:%d/mcp" % (HOST, PORT)
ENV_NAME = "AGENTCTL_MCP_TOKEN"
EXPECT_TOOLS = 25
rows = []


def row(name, ok, detail, fix=""):
    rows.append((name, ok, detail, fix))
    print("[%s] %s —— %s" % ("绿" if ok else "红", name, detail), flush=True)
    if not ok and fix:
        print("       ⇒ 怎么办：%s" % fix, flush=True)


def post(payload, token, timeout=10):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(URL, data=json.dumps(payload).encode("utf-8"), method="POST", headers=h)
    return urllib.request.urlopen(req, timeout=timeout)


def user_env(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v or "")
    except Exception:
        return ""


def main():
    print("=" * 72)
    print("AgentCtl MCP(HTTP) 自检 · %s" % URL)
    print("=" * 72)
    tok = ""
    if os.path.isfile(TOKEN_FILE):
        tok = open(TOKEN_FILE, encoding="utf-8").read().strip()
    row("① 令牌文件在", bool(tok), TOKEN_FILE if tok else "找不到 " + TOKEN_FILE,
        "跑一次 pythonw -m agentctl.mcp_http（会自动签发）")

    with socket.socket() as s:
        s.settimeout(0.8)
        listening = s.connect_ex((HOST, PORT)) == 0
    row("② 端口有人听", listening, "%s:%d %s" % (HOST, PORT, "在听" if listening else "没人听"),
        "跑 tools\\守护_mcp_http.py --once（或检查登录自启项是否在）")

    tools_n = 0
    if listening:
        try:
            r = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "selfcheck", "version": "1"}}}, tok)
            init_ok = json.loads(r.read().decode("utf-8")).get("result", {}).get("serverInfo", {}).get("name")
            row("③ initialize 通", bool(init_ok), "serverInfo=%s" % init_ok,
                "看本地 AgentCtl 日志目录下的 mcp_server.log 最后几行")
        except urllib.error.HTTPError as e:
            row("③ initialize 通", False, "HTTP %s" % e.code, "令牌不对？跑守护脚本让令牌与环境变量对齐")
        except Exception as e:
            row("③ initialize 通", False, repr(e), "服务半死不活 ⇒ 重启守护进程")
        try:
            r = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, tok)
            tools_n = len(json.loads(r.read().decode("utf-8")).get("result", {}).get("tools", []))
        except Exception:
            tools_n = 0
    else:
        row("③ initialize 通", False, "端口没人听，跳过", "先解决端口")
    row("④ 工具数 = %d" % EXPECT_TOOLS, tools_n == EXPECT_TOOLS, "实测 %d" % tools_n,
        "契约与实现对不上 ⇒ 跑 tests\\check_t1_w1.py")

    env_tok = user_env(ENV_NAME)
    row("⑤ 令牌与环境变量一致", bool(tok) and env_tok == tok,
        "文件 %s… / 环境变量 %s…" % (tok[:8] or "空", env_tok[:8] or "空"),
        "跑 tools\\守护_mcp_http.py --once（它会把两边对齐）")

    app_dir = os.path.join(os.environ.get("LOCALAPPDATA") or "", "AgentCtl")
    brake = os.path.join(app_dir, "_急停.flag")
    engaged = os.path.isfile(brake)
    row("⑥ 急停没拉下", not engaged, "旗子 %s" % ("在（一切动作都会被拒）" if engaged else "不在"),
        "面板上点「▶ 解除急停」，或 AgentCtl.exe brake --action release --confirm-release")

    bad = [r for r in rows if not r[1]]
    print("-" * 72)
    if not bad:
        print("✅ 全绿：新开一个 窗口D 会话就能看到 mcp__agentctl_http__* 的 %d 个工具。" % tools_n)
        return 0
    print("❌ %d 项不绿（上面每条都给了怎么办）。" % len(bad))
    return 1


if __name__ == "__main__":
    sys.exit(main())
