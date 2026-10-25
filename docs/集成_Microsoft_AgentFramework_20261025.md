# 集成：Microsoft Agent Framework（MAF）· 2026-10-25

> 为什么"落地"它：MAF 是**宿主**（会看会想的那一层），AgentCtl 是**手脚+闸门**。
> 把 AgentCtl 作为 MCP 工具挂到 MAF 的 agent 上，一行配置就能让 MAF 的 agent 操作 Windows——并且每一步过闸、可判定、可急停。

## 一、它是什么

[microsoft/agent-framework](https://github.com/microsoft/agent-framework)：微软开源的 agent 框架，
Python / .NET 双栈，支持多 agent 编排、工作流、以及 **MCP 工具接入**。

## 二、接入方式（MCP streamable HTTP）

AgentCtl 的 MCP 端点是 `POST http://127.0.0.1:8761/mcp`，JSON-RPC 2.0，
请求头 `Authorization: Bearer <token>`（token 在 `<安装目录>\state\token`）。

```python
import os
from agent_framework import ChatAgent, MCPStreamableHTTPTool

agentctl = MCPStreamableHTTPTool(
    name="agentctl",
    url="http://127.0.0.1:8761/mcp",
    headers={"Authorization": "Bearer " + open(r"state\token", encoding="utf-8").read().strip()},
)

agent = ChatAgent(
    chat_client=your_model_client,     # 模型由宿主提供（MAF 侧）
    tools=[agentctl],                  # ← AgentCtl 的手脚就在这
    instructions=(
        "你在操作一台真实 Windows 电脑。每个动作前必须先 ctl_find 定位，"
        "再 ctl_click --eid / ctl_fill；动作后读回判词：生效=met 才算成，"
        "确认没变=no_effect，没测出来=unobservable（重新观测，不许当成功）。"
    ),
)

print(agent.run("在记事本里写下 hello，并读回来确认"))
```

完整可跑示例见 `examples\agent_framework_agentctl.py`（没装 MAF 时会打印安装提示，不会崩）。

## 三、必须遵守的四条

1. **模型归宿主**：AgentCtl 不接模型、不发网络；它只提供 UIA / 点击 / 打字 / 判定 / 证据。
2. **通行证要人签**：agent 不能自己点授权（面板是敏感窗，code=3）；MAF 侧第一次调用前要有人授权。
3. **判词必须读**：三个值 `met / no_effect / unobservable`；**unobservable 绝不能当成功**。
4. **急停在旗子级**：面板 / `Ctrl+Alt+Home` / 删旗子三条通道；MAF 的取消不能代替急停。

## 四、验收（本机）

```powershell
# 1) 服务在听
python -X utf8 -m agentctl.cli service status

# 2) token 能通（匿名不行）
$env:AGENTCTL_MCP_TOKEN = (Get-Content .\state\token -Raw).Trim()

# 3) 示例能构造出工具（不需要模型也能过 import/构造这一段）
python -X utf8 examples\agent_framework_agentctl.py --dry
```

> 最后更新：2026-10-25 14:2x
