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


def _send(obj: dict):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(mid, result):
    _send({"jsonrpc": "2.0", "id": mid, "result": result})


def _error(mid, code, message, data=None):
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    _send({"jsonrpc": "2.0", "id": mid, "error": e})


def handle(msg: dict):
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        return _result(mid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None                      # 通知无响应
    if method == "ping":
        return _result(mid, {})
    if method == "tools/list":
        return _result(mid, {"tools": core.tool_list()})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        actor = os.environ.get("AGENTCTL_ACTOR", "mcp-client")
        r = core.dispatch(name, args, actor=actor)
        # MCP 约定：结果放在 content 里；同时把结构化信封也带上，便于 agent 解析
        return _result(mid, {
            "content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}],
            "structuredContent": r,
            "isError": not bool(r.get("ok")),
        })
    if mid is None:
        return None
    return _error(mid, -32601, f"Method not found: {method}")


def main() -> int:
    # ★ P0（2026-10-10）：本进程是同步主循环 —— 一个 tools/call 卡住，同一连接后续全部排队
    #   （D17 C 层结论）。标记渠道后，rc=7 弹窗等待用 20s 短上限（core.CHANNEL 注释）。
    core.CHANNEL = "mcp"
    sys.stderr.write("[mcp] AgentCtl stdio server 启动\n")
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
        try:
            ctrl_border.stop_follow()
        except Exception as e:
            sys.stderr.write(f"[border] 收边框异常（边框子进程靠 --parent-pid 自救）：{e!r}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
