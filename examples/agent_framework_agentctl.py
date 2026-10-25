# -*- coding: utf-8 -*-
"""把 AgentCtl 作为 MCP 工具挂到 Microsoft Agent Framework（MAF）的 agent 上。

两种跑法：
  python -X utf8 examples/agent_framework_agentctl.py --dry
      只做“能不能构造出 MCP 工具”的自检（不需要模型、不碰桌面）
  python -X utf8 examples/agent_framework_agentctl.py
      用真实模型跑一个最小任务（需要宿主模型 client；见文末 TODO）

依赖：pip install agent-framework（MS 开源，Python 3.10+）
"""
import argparse
import os
import sys

MCP_URL = os.environ.get("AGENTCTL_MCP_URL", "http://127.0.0.1:8761/mcp")


def read_token():
    """按工程口径找 token：环境变量优先，其次 <repo>/work/state/token。"""
    tok = os.environ.get("AGENTCTL_MCP_TOKEN")
    if tok:
        return tok.strip()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "work", "state", "token"),
                 os.path.join(here, "state", "token")):
        if os.path.exists(cand):
            return open(cand, encoding="utf-8").read().strip()
    return ""


def build_tool():
    """构造 MAF 的 MCP 工具；MAF 没装时给出安装提示（不崩）。"""
    try:
        from agent_framework import MCPStreamableHTTPTool
    except Exception as e:                                    # noqa: BLE001
        print("[!] 没装 Microsoft Agent Framework：%r" % (e,))
        print("    安装：python -m pip install agent-framework")
        return None
    tok = read_token()
    if not tok:
        print("[!] 找不到 token：设置 AGENTCTL_MCP_TOKEN，或确认 work/state/token 存在")
        return None
    tool = MCPStreamableHTTPTool(
        name="agentctl",
        url=MCP_URL,
        # ★ MAF 的签名里是 static_headers（不是 headers）；传错会被静默吞进 kwargs。
        static_headers={"Authorization": "Bearer " + tok},
        request_timeout=90,
    )
    return tool


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只构造工具，不跑模型")
    a = ap.parse_args(argv)

    tool = build_tool()
    if tool is None:
        return 2
    print("[OK] MCP 工具已构造：name=agentctl url=%s" % MCP_URL)

    if a.dry:
        print("[OK] --dry：到此为止（不建 agent、不调模型、不碰桌面）")
        return 0

    # 真实运行需要一个模型 client（由宿主提供）。这里不替宿主选模型。
    print("[!] 未提供模型 client：请把下面的片段接进你的 MAF 脚本：")
    print("""
    from agent_framework import ChatAgent
    agent = ChatAgent(chat_client=<你的模型 client>, tools=[tool],
                      instructions="先 ctl_find 定位，再 ctl_click --eid / ctl_fill；"
                                   "只有 verdict=met 才算成功，unobservable 必须重新观测。")
    agent.run("在记事本里写下 hello 并读回确认")
    """)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
