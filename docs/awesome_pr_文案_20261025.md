# awesome-list 投递文案（2026-10-25）

> **目标清单类型**：`awesome-computer-use-agents`、`awesome-ai-agents`、`awesome-mcp-servers` 这一类。
> 各清单规则不同（有的要求条目按字母序、有的可能禁 `(0.x)` 尾注、有的要 `- ` 而不是 `—`），所以下面写**通用格式 + 四个变体**，投之前先读一遍目标清单的 `CONTRIBUTING.md`。
> **配套英文摘要**：`docs\英文摘要_20261025.md`（同一批事实口径）。

---

## 一、投递前先核对（这些不齐，PR 大概率被关）

| 项 | 现状 | 备注 |
|---|---|---|
| 仓库公开 | ✅ GitHub 主仓 `GALAXYLLIYI/computer-use-gate`；Gitee 镜像 `GALAXYLIYI/agent-ctl` | 条目链接用 **GitHub 主仓** |
| License | ✅ Apache-2.0（仓根 `LICENSE`） | 多数清单硬性要求 |
| 英文简介 | ✅ README 首屏有 `TL;DR (English)` 三行 | 2026-10-25 已在 |
| Release 可下载 | ✅ `v0.1.0` / `v0.1.1` 两个 tag 的 Release 都附 zip + sha256（`CHANGELOG.md:6-8`） | 清单一般不查，但维护者会点；投递前再确认 Release 页面还在 |
| 截图 / GIF | ⚠ **目前没有**（仓内 png 属证据/私密，未放 `docs/images/`） | 清单要求演示图的话，得先补图，别拿不出 |
| 成熟度自我声明 | ✅ README「成熟度自评」写明 0.x / 零外部用户 / 验证层基本没有 | **别在 PR 里洗掉这段**，维护者一读 README 就能对上 |

> 现实预期：仓目前 star 很少（派单时记的是 1 star，**投递时以仓库页面实数为准**）、单人维护、文档以中文为主。**被拒很正常**，理由多半是「成熟度不够 / 不是 agent 框架 / 太垂直」。被拒不是失败，把理由记下来即可。

---

## 二、一行条目文案（主用，194 字符 ≤ 200）

```
- [AgentCtl](https://github.com/GALAXYLLIYI/computer-use-gate) — Windows-only desktop action gate for computer-use agents: human-issued pass, three-state verdict, audit trail, kill switch. (0.x)
```

**变体 B（清单用 ASCII 连字符、禁用长破折号）**：

```
- [AgentCtl](https://github.com/GALAXYLLIYI/computer-use-gate) - Windows-only desktop action gate for computer-use agents: human-issued pass, three-state verdict, audit trail, kill switch. (0.x)
```

**变体 C（清单限长、或要求条目不带成熟度尾注 —— 成熟度挪到 PR 正文里说）**：

```
- [AgentCtl](https://github.com/GALAXYLLIYI/computer-use-gate) — Windows-only desktop action gate for computer-use agents. (0.x)
```

**变体 D（中文清单）**：

```
- [AgentCtl](https://github.com/GALAXYLLIYI/computer-use-gate) —— Windows 桌面动作闸门：人类签发通行证、三态判词、证据链、急停。（0.x 实验性）
```

> 四条都**只陈述仓内可查的事实**，没有速度/效果类数字。清单若要求「一句话说明它对你有什么用」，用变体 C + 正文里的「Why it fits」那两行。

---

## 三、PR 标题

```
Add AgentCtl — desktop action gate for computer-use agents (Windows, 0.x)
```

备选（清单惯例是 `Add <name>` 时用这个）：

```
Add AgentCtl
```

中文对照：`新增 AgentCtl —— 给电脑操作类 agent 用的 Windows 桌面动作闸门（0.x）`。

---

## 四、PR 正文模板（英文，直接粘贴）

```markdown
## What this is

[AgentCtl](https://github.com/GALAXYLLIYI/computer-use-gate) is a Windows-only desktop action gate
that sits between a computer-use agent and the keyboard and mouse. An agent can only act on a window
a human has authorised, and every action has to leave evidence behind (timeline, before/after frames,
decision log).

## Why it fits this list

- It is a **computer-use** project, not a general agent framework: the unit of work is a single desktop
  action (click, type, scroll, window operation) on Windows.
- It ships an **MCP server**, so it plugs into MCP-speaking agents the same way the other MCP entries here do:
  Streamable HTTP endpoint `http://127.0.0.1:8761/mcp`.
- It addresses a problem that the rest of the list mostly leaves open: once an agent can click, what makes
  that click reviewable afterwards, and stoppable while it happens?

## How it differs from entries already on the list

This project deliberately does **not** compete on perception or planning. It does not try to see the screen
better or decide what to do next; other entries in this list are far ahead on that, and I am not claiming
otherwise. It is the layer *around* the act:

| The list already has | This entry adds |
|---|---|
| Agents that decide what to click | A gate that decides whether that click is **allowed**, **recorded**, and **interruptible** |
| Tools that report success after issuing an action | A three-state verdict: `changed` / `confirmed unchanged` / `could not be measured` — "could not measure" is never reported as success |
| Logs you can read afterwards | An audit trail written locally, plus a file-level kill switch that stops every automatic action at once |

If the list's scope is "agents that use a computer", this entry is adjacent but compatible: it is what you put
underneath such an agent, not instead of it. I am happy to drop it if you consider authorisation/audit tooling
out of scope for this list — that is a reasonable call, and I would rather ask than argue.

## Honest limits

- **0.x, experimental** (latest tag `v0.1.1`). Single maintainer, personal project. No SLA, no compatibility promise; expect breaking changes.
- **No external users yet.** Zero real-world failure history. Every test record was produced on the author's own machine.
- **Windows-only** (Windows 10/11 x64). No Linux, no macOS, no remote control.
- **Default-deny.** Out of the box nothing moves: a human must issue a pass from an on-screen dialog before any
  write action is accepted. That is the design, not a broken install.
- **Desktop tests cannot run in CI.** They need a real interactive Windows session, so CI only covers syntax,
  static checks, contract checks and a pre-submit scan. I am not claiming CI-verified coverage of the gate itself.
- **Not a correctness guarantee.** The evidence chain shows *what happened*, not whether it *should* have happened.

## Three questions you might ask

**1. "Is this ready for production / who is using it?"**
No and nobody yet. It is 0.x, maintained by one person, with no external users and no real failure history.
README says this in the first screen ("设计层接近成熟，实现层半熟，验证层基本没有"). Treat it as a local,
human-supervised experiment and a design reference — not as a production safety layer.

**2. "Isn't this just another GUI-automation wrapper? What is actually new?"**
The clicking part is routine. The part I would point at is the gate: default-deny until a human issues a pass,
lease per task/window, stale-frame clicks refused, sensitive windows rejected server-side, a three-state verdict
so a failed measurement cannot masquerade as success, and a kill switch that outranks everything.
The interface contract is machine-readable in `contract/tools.json` (a draft baseline that moves with its
`schema_version`), and the gate and the verdict codes each have test files under `tests/`.

**3. "Windows-only, one maintainer — how do I verify the claims, and what happens if you stop?"**
Every claim in the entry and in the README points at a file under `docs/` with measured records; nothing here is
a design wish (the README says explicitly which parts are still being built). Apache-2.0 means it can be forked
without asking me, and the docs are the handover. I have no bus-factor plan and will not pretend to have one.
```

### 中文对照（自己看，不贴出去）

- **What this is**：是什么 —— Windows 桌面动作闸门，夹在 agent 与键鼠之间；只在人授权的窗口上动手，每个动作留证据（时间线、前后帧、决策日志）。
- **Why it fits**：为什么符合清单 —— 它是「computer-use」项目（工作单位是单个桌面动作）；自带 MCP server（Streamable HTTP，`http://127.0.0.1:8761/mcp`）；它回应清单里大多数条目没回答的问题：动作发出去之后怎么复核、正在发生时怎么停。
- **How it differs**：与已有条目区别 —— **不比感知、不比规划**，明说别人在这两块远强于本仓；它加的是「动作周围那一层」：放不放行、记不记录、停不停得下来；三态判词保证「没测出来」不会被报成成功。若清单认为授权/审计类工具不属于范围，愿意撤。
- **Honest limits**：0.x / 单人 / 无 SLA / **零外部用户** / Windows-only / 默认全拒 / 桌面判据进不了 CI / 证据链不保证动作正确。
- **三问三答**：①生产可用吗 —— 不是，也没人用；②是不是又一个 GUI 自动化壳子 —— 壳子部分很普通，值得看的是闸门，契约在 `contract/tools.json`；③怎么核你的话、你停更怎么办 —— 每条都能指到 `docs/` 里的实测文件，Apache-2.0 可自由 fork，无 bus-factor 计划、不装样子。

---

## 五、维护者可能追问的其它问题（提前备好，别现场编）

| 追问 | 诚实回答 |
|---|---|
| 「有 benchmark 吗？」 | 没有跨方案 benchmark。仓内只有**自测**记录（等生效预算、动作链分段计时等），跑在作者本机，不能当横向对比用。 |
| 「能无人值守跑吗？」 | 不能，也不打算。默认每 60 次动作要人签发一次，这是设计。 |
| 「支持 macOS / Linux 吗？」 | 不支持。控制链路走 UIA / 抓屏 / WinRT OCR，是 Windows 体系。 |
| 「能绕过验证码/风控吗？」 | 不做，也不应被这么用（README 明确写了）。 |
| 「为什么叫 AgentCtl，仓名却是 computer-use-gate？」 | GitHub 主仓更名过，旧链接自动重定向；Gitee 镜像仍叫 `agent-ctl`。 |
| 「你这 1 star 的项目来投什么？」 | 就是来求真实反馈的：被拒的理由比 star 有用。 |

---

## 六、投递后

1. 被拒 → 记下原话（多半是「too early / out of scope」），**不要争辩**，把这条理由写进 `docs\` 的复盘。
2. 被要求改格式 → 只改**条目那一行**，别顺手改 README。
3. 被要求补图 → 先补 `docs\images\` 并显式放行（`.gitignore` 里已有这条约定），别拿证据截图充数。
4. 被合并 → 回仓更新 README「被谁收录」，并核对条目里的描述**是否仍是事实**（0.x 会变）。

---

最后更新：2026-10-25 18:34
