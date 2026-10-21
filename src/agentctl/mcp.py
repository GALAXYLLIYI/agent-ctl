# -*- coding: utf-8 -*-
r"""mcp.py —— MCP stdio 传输层（JSON-RPC 2.0 over stdin/stdout）

设计依据：详细设计 §6.2 ＋ 打包铁律
  ⛔ **本进程绝不能用 `-w`/`--noconsole` 构建** —— PyInstaller 官方原文：那样
     "the bootloader starts Python with **no target for standard output or input**"，
     stdio 协议当场死。这条在打包脚本里要显式断言。

实现范围（够 agent 接上就行）：
  initialize / notifications/initialized / tools/list / tools/call / ping
★ stdout **只走协议**，日志一律 stderr。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentctl import core                                            # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "agentctl", "version": "0.1.0"}


# ── 本地日志（2026-10-20 加）────────────────────────────────────────────
# 为什么加：Codex 侧只看到一句 Transport closed，**死因、时刻、当时在处理什么**
#   全部看不到（stderr 未必有人收）。今天排查「MCP 工具在列表里却调不通」就卡在
#   这条信息缺失上 —— 只能靠猜（stdin EOF？父进程断开？自己崩了？）。
#   ⇒ 落一份本地日志：启动、每次调用、异常、退出原因，都记。
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AgentCtl")
LOG_PATH = os.path.join(LOG_DIR, "mcp_server.log")


def _log(text):
    """写本地日志。**绝不走 stdout**（stdout 是 JSON-RPC 协议专用）。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s pid=%d] %s\n" % (ts, os.getpid(), text))
    except Exception:
        pass


def _send(obj: dict, out=None):
    """★ 2026-10-20：输出目标可注入。

    为什么要注入：MCP 的第二种传输（Streamable HTTP，mcp_http.py）要**复用本文件的
    协议逻辑**，而不是抄一遍 handle()。抄 = 两份实现必然漂移。
    out=None ⇒ 走 stdout（stdio 传输，行为与以前一模一样）。
    """
    if out is not None:
        out(obj)
        return
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(mid, result, out=None):
    _send({"jsonrpc": "2.0", "id": mid, "result": result}, out)


def _error(mid, code, message, data=None, out=None):
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    _send({"jsonrpc": "2.0", "id": mid, "error": e}, out)


def handle(msg: dict, out=None, actor: str | None = None):
    """处理一条 JSON-RPC 消息。

    ★ out / actor 是给 HTTP 传输留的口子（mcp_http.py）：stdio 侧不传，行为不变。
      actor 显式传入时优先于环境变量 AGENTCTL_ACTOR。
    """
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        return _result(mid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }, out)
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None                      # 通知无响应
    if method == "ping":
        return _result(mid, {}, out)
    if method == "tools/list":
        return _result(mid, {"tools": core.tool_list()}, out)
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        who = actor or os.environ.get("AGENTCTL_ACTOR", "mcp-client")
        _log("tools/call %s actor=%s args=%s" % (name, who, json.dumps(args, ensure_ascii=False)[:300]))
        r = core.dispatch(name, args, actor=who)
        _log("tools/call %s -> ok=%s code=%s" % (name, r.get("ok"), r.get("code")))
        # MCP 约定：结果放在 content 里；同时把结构化信封也带上，便于 agent 解析
        return _result(mid, {
            "content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}],
            "structuredContent": r,
            "isError": not bool(r.get("ok")),
        }, out)
    if mid is None:
        return None
    return _error(mid, -32601, f"Method not found: {method}", out=out)


def main() -> int:
    # ★ P0（2026-10-10）：本进程是同步主循环 —— 一个 tools/call 卡住，同一连接后续全部排队
    #   （D17 C 层结论）。标记渠道后，rc=7 弹窗等待用 20s 短上限（core.CHANNEL 注释）。
    core.CHANNEL = "mcp"
    sys.stderr.write("[mcp] AgentCtl stdio server 启动\n")
    _log("server 启动（stdin/stdout 接管）")
    # ★ T1/W3-B：屏幕边缘状态提示跟着 MCP 服务起来（档位/急停自动变色）。
    #   ⛔ 本进程 stdout 是 JSON-RPC 协议专用 —— 边框的一切输出由 ctrl_border 走
    #      stderr + 日志文件，且 spawn 时子进程 stdout/stderr 一并重定向，绝不碰协议。
    try:
        from agentctl import ctrl_border
        ctrl_border.start_follow("mcp")
    except Exception as e:
        sys.stderr.write(f"[border] 接线异常（服务照常，只是没有边框）：{e!r}\n")
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception as e:
                _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {e}"}})
                continue
            try:
                if isinstance(msg, list):                     # 批量
                    for m in msg:
                        handle(m)
                else:
                    handle(msg)
            except Exception as e:
                sys.stderr.write(f"[mcp] 处理异常: {type(e).__name__}: {e}\n")
                if isinstance(msg, dict) and msg.get("id") is not None:
                    _error(msg.get("id"), -32603, f"Internal error: {e}")
    finally:
        _log("stdin 结束（EOF / 父进程断开）⇒ server 退出")
        try:
            ctrl_border.stop_follow()
        except Exception as e:
            sys.stderr.write(f"[border] 收边框异常（边框子进程靠 --parent-pid 自救）：{e!r}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
