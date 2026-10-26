<div align="center">

# computer-use-gate

## Windows 版 Computer Use

### 无需显卡 · 任何 AI 都能接 · 你不点头它不动手

**真正让你感受一下 Windows 版的 Computer Use。**

**让你的 AI 真正在 Windows 上动手：看屏幕、点按钮、打字、切窗口、操作网页。**
**每一下都要你授权，每一下都留证据，按一个键全部停下。**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
![Platform](https://img.shields.io/badge/Windows-10%20%2F%2011-0078D6)
![GPU](https://img.shields.io/badge/GPU-不需要-brightgreen)
![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20HTTP-8A2BE2)
![Status](https://img.shields.io/badge/status-0.x%20实验版-orange)

[5 分钟上手](#5-分钟上手) · [任何 AI 都能接](#任何-ai-都能接) · [为什么不要显卡](#为什么不要显卡) · [为什么敢让它动手](#为什么敢让它动手)

*Computer Use for Windows. No GPU. Works with any agent via MCP, HTTP or CLI. Default-deny, evidence for every action, one-key emergency stop.*

</div>

> **TL;DR (English)** — **Computer Use for Windows**, running on your own desktop: your AI agent can see the screen, click, type, switch windows and drive web pages.
> - **No GPU needed**: controls are read via Windows UI Automation, built-in Windows OCR and browser CDP, not a local vision model.
> - **Works with any agent**: Claude Code, Codex, Gemini CLI, Cursor, Cherry Studio or your own scripts, via **MCP (stdio / HTTP)**, **HTTP API** or **CLI**.
> - **Default-deny**: nothing moves until **you** click "Allow" on an on-screen prompt. A pass lasts 30 minutes or 60 actions.
> - **Evidence for every action**: before/after screenshots, a hash-chained tamper-evident log, and exportable replay videos.
> - **One-key stop**: `Ctrl+Alt+Home` halts everything immediately.
>
> Experimental 0.x: no SLA, APIs may change. Needs **Windows 10/11 x64 + Python 3.12 + git**. Quick start: [5 分钟上手](#5-分钟上手); scope and limits: [先说清楚适用范围](#先说清楚适用范围).
>
> *Keywords: computer-use agent · MCP server · Windows automation · desktop action gate · agent safety · audit trail.*

---

## 一句话说明

> 别的 Computer Use 方案，要么只能跑在 Linux 虚拟机里，要么要一块好显卡跑视觉模型，要么只认某一家的 AI。
>
> **computer-use-gate 直接跑在你自己的 Windows 桌面上，普通笔记本就够用，Claude、Codex、Gemini、Cursor、Cherry Studio，或者你自己写的脚本，都能接。**

| | 常见方案 | **computer-use-gate** |
|---|---|---|
| 跑在哪 | Linux 虚拟机 / 云端沙箱 | **你自己的 Windows 桌面** |
| 要不要显卡 | 本地视觉模型要一块好显卡 | **不要**，Windows 自带的能力就够 |
| 能接哪个 AI | 通常绑定一家 | **任何 AI**：MCP、HTTP、命令行三种接法 |
| 动手前问不问你 | 装好就能动 | **默认全部拒绝**，你点"允许"才放行 |
| 它说"成功了" | 动作发出去就算 | **再看一眼屏幕**，画面没变不算成功 |
| 想叫停 | 关程序 | **`Ctrl+Alt+Home`**，立刻全停 |
| 事后查账 | 基本没有 | **前后截图 + 防篡改日志 + 回放视频** |

---

## 为什么不要显卡

很多 Computer Use 每走一步都要截一张图，丢给视觉模型去猜"按钮在哪"。又慢、又贵，本地跑还得有显卡。

computer-use-gate 换了个思路：**先问 Windows，再看截图。**

| 能力 | 用的是什么 | 要不要显卡 |
|---|---|---|
| 找按钮、读控件 | Windows 自带的 **UI Automation**，直接拿到控件的名字和位置 | ❌ |
| 认屏幕上的字 | Windows 自带的 **OCR**，不用下载模型 | ❌ |
| 读网页 | 浏览器自带的 **CDP**，直接读页面文字和元素 | ❌ |
| 截图 | 窗口被挡住也能截 | ❌ |

AI 拿到的是"保存按钮在 (812, 430)"这样的准确答案，不用自己从像素里猜。**普通笔记本、核显、轻薄本都能跑。**

---

## 任何 AI 都能接

同一套工具，提供三种接法。**支持 MCP 的 AI 直接接；不支持的，用 HTTP 或命令行也能调。**

| 接法 | 地址 / 命令 | 适合谁 |
|---|---|---|
| **MCP stdio** | `agentctl.cmd mcp` | 绝大多数 AI 客户端，最省事 |
| **MCP HTTP** | `http://127.0.0.1:8761/mcp` + Bearer 令牌 | 想让多个客户端共用一个常驻服务 |
| **HTTP API** | `http://127.0.0.1:8760/v1/...` + `X-AgentCtl-Token` | 自己写的 agent、Python / Node 脚本、工作流工具 |
| **命令行** | `agentctl.cmd click --hwnd N --name 保存` | 批处理、PowerShell、人工调试 |

下面的 `<安装目录>` 换成你实际的安装位置。令牌默认在 `%LOCALAPPDATA%\AgentCtl\state\token`，设了 `AGENTCTL_HOME` 的话就在 `%AGENTCTL_HOME%\state\token`。

<details open>
<summary><b>Claude Code</b></summary>

```powershell
claude mcp add agentctl -- "<安装目录>\agentctl.cmd" mcp
```
</details>

<details>
<summary><b>Codex CLI</b></summary>

```powershell
codex mcp add agentctl -- "<安装目录>\agentctl.cmd" mcp
```

或者接常驻的 HTTP 服务：

```powershell
$env:AGENTCTL_MCP_TOKEN = (Get-Content "$env:LOCALAPPDATA\AgentCtl\state\token" -Raw).Trim()
codex mcp add agentctl --url http://127.0.0.1:8761/mcp --bearer-token-env-var AGENTCTL_MCP_TOKEN
```
</details>

<details>
<summary><b>Gemini CLI / Cursor / Cherry Studio / VS Code 等任何 MCP 客户端</b></summary>

在客户端的 MCP 配置里加一个 stdio 服务器。大多数客户端都认这个格式（Gemini CLI 是 `~/.gemini/settings.json`，Cursor 是 `.cursor/mcp.json`，Cherry Studio 在设置界面里填同样的命令和参数）：

```json
{
  "mcpServers": {
    "agentctl": {
      "command": "<安装目录>\\agentctl.cmd",
      "args": ["mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>不支持 MCP？直接用 HTTP</b></summary>

先启动 HTTP 服务：

```powershell
agentctl.cmd serve
```

然后任何语言都能调，比如 Python：

```python
import os
import requests

token = open(os.path.expandvars(r"%LOCALAPPDATA%\AgentCtl\state\token"), encoding="utf-8").read().strip()
api = "http://127.0.0.1:8760/v1"
headers = {"X-AgentCtl-Token": token}

print(requests.get(f"{api}/status", headers=headers).json())                 # 查状态
print(requests.post(f"{api}/perceive/windows", json={}, headers=headers).json())  # 列窗口
```

完整接口见 [`docs/AGENT接口.md`](docs/AGENT接口.md)。
</details>

---

## 它能做什么

接上之后，你的 AI 会多出 20 多个桌面工具：

| 类别 | 工具 | 用来做什么 |
|---|---|---|
| 👀 **看** | `ctl_windows` `ctl_shot` `ctl_tree` `ctl_find` `ctl_screen` | 列出窗口；截图（窗口被挡住也能截）；读控件名称和位置；按文字找按钮；OCR 认字 |
| 🖱 **动** | `ctl_click` `ctl_type` `ctl_fill` `ctl_scroll` `ctl_keys` `ctl_focus` | 点击、打字（中文直接输入）、一步填好输入框、滚动、快捷键、切换窗口 |
| 🌐 **网页** | `ctl_browser` | 通过 CDP 读网页文字、找元素、精确滚动 |
| 🛡 **管** | `ctl_authorize` `ctl_lease` `ctl_brake` `ctl_preflight` `ctl_status` | 申请授权、一次只让一个 AI 动手、急停、动手前体检、查状态 |
| 🧾 **证据** | `ctl_timeline` `ctl_verify` `ctl_replay` `ctl_record` `ctl_witness` | 查动作记录、校验记录有没有被改、导出回放视频、录屏、核对系统时间 |

**你可以这样跟 AI 说：**

> "打开记事本，写一段会议纪要，存到桌面。"
> "把这个网页表格里的数据抄进 Excel。"
> "帮我在设置里找到显示缩放，截图给我看现在是多少。"

AI 会自己列窗口、找控件、点击、打字，每一步都先过闸门，做完给你看前后截图。

---

## 为什么敢让它动手

把键鼠直接交给 AI，等于把电脑借给一个没见过面的人。computer-use-gate 在 AI 和键鼠中间加了五道闸，每个动作都要依次通过：

```
① 你签发通行证 → ② 租约绑定窗口 → ③ 截图必须是新的 → ④ 敏感窗口一律拦 → ⑤ 急停一票否决
                                                                          ⇓
                                                     每一步都写进证据链（前后截图 + 决策日志）
```

| 闸 | 防的是什么 | 怎么做到 |
|---|---|---|
| ① **通行证** | AI 偷偷动手 | 装完默认全部拒绝。屏幕弹窗由**你本人**点"允许"，一张通行证管 30 分钟或 60 个动作。AI 没法替你点这个弹窗 |
| ② **租约** | 几个 AI 同时抢鼠标 | 同一时间只有一个 AI 能动手，其他的只能看 |
| ③ **帧凭据** | 拿旧截图点新画面 | 每次点击都要带截图编号，画面已经变了就拒绝 |
| ④ **敏感窗口** | AI 去点授权弹窗 | 授权弹窗由服务端直接拦截，给坐标也点不进去 |
| ⑤ **急停** | 停不下来 | 面板 ⏹ 按钮、`Ctrl+Alt+Home` 全局快捷键、删除急停文件，任选一种，立刻全停 |

控制期间屏幕四周会亮一圈细边框，你随时知道 AI 正在动手。

### 它不会骗你说"成功了"

每个动作做完，它都会再看一眼屏幕，给出三种结果之一：

| 结果 | 代码 | 意思 |
|---|---|---|
| ✅ 生效 | `0` | 画面确实变了 |
| ⚠ 确认没变 | `8` | 动作发出去了，画面没变，**不算成功** |
| ❓ 没测出来 | `5` | 没测到或环境不稳，**可以重试**，也不算成功 |

点错窗口时，这一下根本不会发出去，它还会告诉你该关掉哪个窗口。

### 事后能查、能证明

- 每个动作都记进时间线，记录带**哈希链**。改过一个字，`ctl_verify` 就能指出从第几条开始坏的。
- 任意一段操作都能用 `ctl_replay` 导出回放视频，不用装 ffmpeg。
- 复盘、交接、需要留记录的场景都用得上。

---

## 5 分钟上手

**你需要**：Windows 10/11 x64、Python 3.12、git。**不需要显卡，不需要虚拟机，不需要 Docker。**

### 1. 安装

```powershell
# 国内用 Gitee 镜像；海外换成 https://github.com/GALAXYLLIYI/computer-use-gate
git clone https://gitee.com/GALAXYLIYI/computer-use-gate
cd computer-use-gate
pwsh -File .\install.ps1
```

想先看清脚本再运行（推荐）：

```powershell
irm https://gitee.com/GALAXYLIYI/computer-use-gate/raw/main/install.ps1 -OutFile install.ps1
notepad install.ps1      # 看一眼
.\install.ps1
```

从 Release 下载 zip 的话，先核对哈希：

```powershell
Get-FileHash .\agentctl.zip -Algorithm SHA256
# 和 agentctl.zip.sha256 里的值逐字比对，不一样就别装
```

### 2. 接到你的 AI

按上面 [任何 AI 都能接](#任何-ai-都能接) 选一种方式，一条命令或一段配置就够了。

### 3. 签发通行证，开始干活

让 AI 调用 `ctl_authorize`，屏幕上会弹出授权窗口，**你本人**点"允许"。接下来 30 分钟或 60 个动作内，它可以在你授权的范围里动手。

想亲自验证"默认全拒"是不是真的：

```powershell
python -X utf8 tests\_默认关闭_判据.py
```

> 💡 第一次用，建议拿记事本这类无关紧要的窗口练手，别一上来就在有真实账号的软件上开闸。

---

## 先说清楚适用范围

**✅ 适合**

- 在**自己的 Windows 电脑**上，让 AI 做有人看着的桌面操作
- 需要事后说清楚"AI 到底干了什么"的场景
- 愿意开工前点一下授权的人。这一步就是它的价值所在

**⛔ 不适合**

- **无人值守批量点击**：每 30 分钟或 60 个动作要重新授权，这是设计，不是缺陷
- **绕过验证码、风控或权限检查**：不做，也请不要这样用
- **Linux / macOS、云端或远程控制**：目前只支持本机 Windows
- **需要 SLA 的生产环境**：这是 0.x 实验项目，接口还会变

**🚧 已知限制**

- Canvas、游戏、PDF 内容、没开无障碍的 Electron 应用读不到控件，只能靠截图兜底
- 多显示器和屏幕缩放（125%、150%）的适配还在开发中，点击位置可能偏
- 界面响应超过约 200ms 时，可能误报"确认没变"
- OCR 第一次识别要暖机，大约 3 秒

---

## 隐私：数据不出你的电脑

- **没有遥测，不上传任何数据**，服务只监听 `127.0.0.1`
- 证据存在本机 `work\` 目录，**里面有屏幕截图和窗口标题，属于敏感数据**，别提交到仓库，也别随手发给别人
- 授权状态、通行证、急停标志默认存在 `%LOCALAPPDATA%\AgentCtl\`，可以用 `AGENTCTL_HOME` 环境变量改到别的盘
- 通行证密钥只存加盐哈希，不存明文

---

## 更新与卸载

| 操作 | 命令 |
|---|---|
| 更新 | 重新运行 `.\install.ps1` |
| 卸载（保留数据） | `.\uninstall.ps1` |
| 卸载并清空数据 | `.\uninstall.ps1 -Purge` |
| 离线安装 | `python -X utf8 tools\pack_dist.py` 生成 zip，目标机运行 `.\install.ps1 -Zip .\agentctl.zip` |

---

## 项目状态

**实验性 0.x，由作者与几位协作者共同维护。** 当前版本 `v0.1.1`（2026-10-24）。

- 设计接近成熟：安全模型、授权租约、帧凭据都经过推演
- 实现还有欠账，接口还会调整
- 判据都在维护者自己的电脑上跑过，**外部用户还很少**

所以最需要的，是**你来把它跑坏**。遇到问题请提 Issue，写清版本、复现步骤、看到了什么、期望看到什么。

本仓已开放 GitHub / Gitee 协作者权限，欢迎提 PR。贡献者名单见 [`CONTRIBUTORS.md`](CONTRIBUTORS.md)，贡献流程见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

**第一次来？照着 [`docs/贡献者上手指南.md`](docs/贡献者上手指南.md) 走**：接受邀请 → 配环境 → 认证 → 切分支 → 提 PR，每一步都有命令和常见坑。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/AGENT接口.md`](docs/AGENT接口.md) | 给 AI 的完整接入说明 |
| [`docs/开发环境.md`](docs/开发环境.md) | 想改代码的人看这里 |
| [`docs/开源声明_隐私与安全边界.md`](docs/开源声明_隐私与安全边界.md) | 隐私、安全、免责的正式声明 |
| [`CHANGELOG.md`](CHANGELOG.md) | 更新日志 |
| [`SECURITY.md`](SECURITY.md) | 漏洞报告（请走私密渠道，别发公开 Issue） |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 贡献指南 |
| [`CONTRIBUTORS.md`](CONTRIBUTORS.md) | 贡献者名单 |

---

## 免责声明

本项目让 AI 的动作**可以审计**，不保证动作**一定正确**。证据链只能证明"发生了什么"，不能证明"这应该发生"。因误点、误填、误删、账号风控等造成的损失，作者不承担责任。请先在测试环境和测试账号上验证。

## 许可

[Apache License 2.0](LICENSE)，允许商用。第三方组件许可见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

Copyright (c) 2026 GALAXYLLIYI

<div align="center">

**无需显卡，任何 AI长眼睛接管你自己的 Windows。**

**觉得有用？点个 ⭐ Star，让更多人用上 Windows 版的 Computer Use。**

</div>
