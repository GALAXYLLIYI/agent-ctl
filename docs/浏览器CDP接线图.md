# 浏览器CDP接线图 —— 浏览器怎么被 CLI / MCP / 面板驱动

> **这份文件是写给"没读过这条线源码的 agent"的。** 读完它你应该能自己把四层拼起来，
> 不需要再去翻 `core.py` 的大段注释。
> 服务名：**AgentCtl**｜契约原件：[`tools.json`](../contract/tools.json)（机器可读，字段以它为准）
> 真源 CDP 客户端：[`cdp.py`](../dsh-viewer-plugin/tools/cdp.py)（**不重写 CDP**，见 §2）
> 最后实测：2026-10-20（超星 300 题 + QQ 发送，全部走这条线）

---

## 0. 30 秒速览

浏览器**不是**被鼠标驱动的，是被 **CDP 协议**（Chrome DevTools Protocol）驱动的：
浏览器自己开一个带调试端口的**独立实例**，agentctl 通过 DevTools 的 HTTP 端点找到标签页、
用 WebSocket 逐条发 CDP 方法，于是能直接读 DOM、跑 JS、导航、截图 —— **一帧像素都不碰、不抢你鼠标**。

    浏览器（自己开实例，开调试端口）
        ↓  HTTP 端点 /json/list  +  WebSocket
    真源 cdp.py（选页 + 发 CDP 方法）        ← 闸门在它里面
        ↓  子命令 + --port
    core.dispatch("ctl_browser")            ← 档位在这里分
        ↓
    CLI ／ MCP ／ 面板                       ← 三态同源

---

## 1. 接线图（主链）

    ┌───────────────────────────────────────────────────────────────────────────────┐
    │ ① 浏览器侧：起一个「带调试端口的独立实例」                                    │
    │                                                                              │
    │   ctl_browser --action launch --port 9333 --profile <独立 profile 目录>        │
    │        │                                                                   │
    │        └─▶ core.py:4072  _cdp_launch()                                       │
    │                 │  端口已被别人占 ⇒ 明确拒绝，绝不抢                          │
    │                 ▼                                                           │
    │   msedge.exe --user-data-dir=<独立 profile>                                   │
    │               --remote-debugging-port=9333                                  │
    │               --no-first-run --no-default-browser-check --disable-sync       │
    │                 │                                                           │
    │                 ▼  Edge 真正监听调试端口                                     │
    │   http://127.0.0.1:9333/json/version   ← DevTools HTTP 端点（版本/WS 地址）  │
    │   http://127.0.0.1:9333/json/list      ← 标签页清单（title/url/ws 地址）     │
    │                                                                              │
    │   ⛔ 硬事实（2026-10-20 实测）：**端口只在「非默认 profile 目录」才监听**。     │
    │      用日常 User Data 起 Edge：20+ 进程起来了、端口不监听；                   │
    │      用 junction 把同一目录换条路径指过去：仍然不监听（按**真实路径**判定）。 │
    │      ⇒ 想用 CDP，要么复制一份 profile，要么接受"拿不到已登录态"。           │
    └──────────────────────────────────┬───────────────────────────────────────────┘
                                       ↓
    ┌───────────────────────────────────────────────────────────────────────────────┐
    │ ② 协议侧：真源 cdp.py（**这层不重写**）                                        │
    │                                                                              │
    │   cdp.py:78          PORT = 9222（模块级变量；main() 解析 --port 写进来）      │
    │   cdp.py             http_get("/json/list") → pick_page(keyword)              │
    │                      ⇒ 选一个 type=="page" 的标签页                          │
    │   cdp.py CDP.__init__  websocket.create_connection(p["webSocketDebuggerUrl"]) │
    │   cdp.py CDP.call()  发 {"id":n,"method":<CDP 方法>,"params":{…}}              │
    │                      按 id 收回包；出错抛 RuntimeError                        │
    │   cdp.py CDP.js()    call("Runtime.evaluate", expression=…, returnByValue)    │
    │                      → 页面里跑 JS，**这就是"读 DOM / 拿数据"的那一步**      │
    │                                                                              │
    │   常用 CDP 方法：Runtime.evaluate / Page.navigate / Page.captureScreenshot /  │
    │                 DOM.querySelector / DOM.getDocument / Network.getAllCookies / │
    │                 Input.dispatchMouseEvent / Input.dispatchKeyEvent /           │
    │                 Autofill.trigger / Target.getTargets                        │
    └──────────────────────────────────┬───────────────────────────────────────────┘
                                       ↓
    ┌───────────────────────────────────────────────────────────────────────────────┐
    │ ③ 闸门侧：两道闸，别以为换个通道就绕过了                                      │
    │                                                                              │
    │   A. cdp.py 自己的闸（真源里，**在类里**）：                                   │
    │      cdp.py CDP.__init__  → gate_check("eval")  ⇒ **连上就要 A 级只读**       │
    │      cdp.py CDP.need_action() → gate_check("click") ⇒ 点击/填表要 B+C 级    │
    │      ★ 为什么在类里：以前只在 main() 里过闸，于是任何 import cdp 的脚本       │
    │        （person_add / fjm_* / formfill…）都能绕开闸门 —— 那等于"换条路       │
    │        就不用拿钥匙"，是最该堵的洞。                                          │
    │                                                                              │
    │   B. core.py 侧再分档（core.py:4054 t_browser）：                             │
    │      action ∈ {launch, close} ⇒ B 级（会起/杀进程）                          │
    │      其余子命令            ⇒ A 级（只读感知）                                 │
    │      goto 实际要 C 级（见 §4 实测第 2 条）                                    │
    │                                                                              │
    │   C. 动作类子命令（cdp.py:58 ACTION_CMDS）= click / fill / goto / open        │
    │      只读类（READONLY_CMDS）= pages / text / html / find / eval / shot /     │
    │      cookies                                                                  │
    └──────────────────────────────────┬───────────────────────────────────────────┘
                                       ↓
    ┌───────────────────────────────────────────────────────────────────────────────┐
    │ ④ 同源侧：core.dispatch 是唯一入口，三态都走它                                  │
    │                                                                              │
    │   core.py:4053  t_browser(args)                                               │
    │        │  把 action 翻成 argv：[<子命令>, "--port", <port>, …]                │
    │        │  按上面的 B 档规则过闸                                                │
    │        ▼                                                                   │
    │   core.py:4093  _legacy("cdp.py", argv, timeout=…)   ← 调真源，返回包成信封  │
    │        │                                                                   │
    │        ▼                                                                   │
    │   core.dispatch("ctl_browser", args, actor=…)  ← 三种传输共用这一条          │
    │        │                                                                   │
    │   ┌────┴──────────────┬──────────────────────┬───────────────────────────┐    │
    │   │                   │                      │                           │    │
    │   ▼                   ▼                      ▼                           │    │
    │ CLI                MCP                     面板（widget.py）            │    │
    │ cli.py ALIAS        mcp.py handle()         通用电脑控制                 │    │
    │ "browser"          tools/call               无作业入口                  │    │
    │  →ctl_browser       →core.dispatch          ——                          │    │
    │                    ★ AGENTCTL_ACTOR 环境变量给 actor                        │    │
    │                                                                              │
    │ ⇒ 三态行为一致、同一份闸门、同一本事件账（timeline）。                       │
    └───────────────────────────────────────────────────────────────────────────────┘

---

## 2. 为什么「不重写 CDP」

`core.py` 里 `_cdp_eval` 附近有一段明写的注释：**「这段不重写 CDP：DOM 那一侧仍然走现役的
legacy_tools\\cdp.py」**。

理由：CDP 的边角太多（页签选择、WS 重连、超时、WS 帧大小、错误包），
自己重写一遍等于重开一个永久维护面；而 `cdp.py` 是**天天在用的现役真源**。
`t_browser` 只做三件不重复造轮子的事：

    ① 把 action 翻成 cdp.py 的子命令 argv
    ② 按档位过闸（launch/close = B，其余 = A）
    ③ 把真源的返回包成统一信封（ok/code/data/event_id/…）

---

## 3. 怎么用（三种接法，可直接抄）

    # CLI（Windows PowerShell / pwsh）
    cd <仓根>\src
    python -X utf8 -m agentctl.cli browser --action pages  --port 9223
    python -X utf8 -m agentctl.cli browser --action eval   --port 9223 --js "document.title"
    python -X utf8 -m agentctl.cli browser --action launch --port 9333 --profile <独立 profile>

    # MCP（Codex / 任何 MCP 客户端）
    mcp__agentctl__ctl_browser { "action": "pages", "port": 9223 }
    mcp__agentctl__ctl_browser { "action": "eval", "port": 9223, "js": "location.href" }

    # 面板（GUI）
    面板只做**通用电脑控制**（授权 / 终止 / 解除急停 / 掐掉租约），**不含作业入口**。
    ⇒ 2026-10-20 18:05 按用户要求撤下「📝 作业」按钮。
      工具 ctl_quiz 本身保留，CLI / MCP 照常用：
      `python -m agentctl.cli quiz status` / MCP `tools/call ctl_quiz`。
    ⇒ 底下的 CDP 底座（ctl_browser）一点没动：CLI / MCP 仍走同一条时间线。

★ **三种接法的语义完全一致**：同一个 `ctl_browser` 契约（`contract/tools.json`）、
同一套闸门、同一条时间线。你用哪个接法，能力边界都相同。

---

## 4. 实测出来的硬事实（2026-10-20，每条都有脚本可复跑）

1. **调试端口只给非默认 profile 目录。**
   日常 `User Data` 起 Edge：进程起了、端口不监听；junction 换路径：同样不监听。
   ⇒ **"已登录" 与 "有 CDP" 凑不到一起**（见第 5 条）。

2. **`--action goto` 要 C 级（打字档）。**
   导航网页在闸门里被归到 C：当时通行证只签了 A、B ⇒ 被拦 `code=9`。
   ⇒ 只读页面用 eval/ A 级；**导航要先把 C 级签上**。

3. **CDP 能拿明文 cookie**：`Network.getAllCookies` 返回浏览器**已解密**的值
   （`document.cookie` 读不到 httpOnly 的那些，它能读到）。

4. **但登录态搬不走**：Edge 155 的 app-bound 加密**绑定 profile 真实路径**。
   把 Cookies + Local State 复制到独立 profile 后，Edge 启动时把那 51 条
   （含 `p_auth_token`/`uf`/`vc3`）**全删了** ⇒ 解密失败。
   试过：改 `is_persistent`/`expires_utc`、清 `top_frame_site_key`、junction 改路径、
   完整复制 profile、副本里开 `credentials_enable_service` + `password_manager_enabled`
   再用 CDP `Autofill.trigger` 触发自动填充 —— **全部无效**。
   ⇒ 真站验证只能让用户扫一次码，登录态落在一个带 CDP 的 profile 里。

5. **网页场景优先 CDP，别看像素。**
   超星那 300 题的 dump/apply/verify 全在 DOM 层做完，一帧没截。
   DOM 层还顺手避开了两个大坑：选项乱序要按**原始 key** 定位（按显示字母会点错）、
   单选再点会取消 ⇒ 灌入必须幂等。

6. **CDP 事件也是真事件**：`Input.dispatchMouseEvent`/`Input.dispatchKeyEvent`
   派发的是浏览器内的 trusted 事件（能触发自动填充），
   但**不属于系统键鼠**，所以不走屏幕层的遮挡/归属那一套。

---

## 5. 常见故障与自查

    症状                                   真正的原因 / 怎么办
    ────────────────────────────────────────────────────────────────────────────
    Transport closed                        MCP server 进程没了。看
                                           %LOCALAPPDATA%\AgentCtl\mcp_server.log
                                           （2026-10-20 加的日志会写明是"stdin EOF
                                           父进程断开"还是别的）。stdio 管道归父进程，
                                           服务器侧救不活，只能重开会话。
    端口连不上 / 页面列表为空               Edge 没开调试端口 ⇒ 多半用了默认 profile。
                                           换独立 profile 目录重启实例。
    cdp.py 报 "cdp.py --port … 拒绝"        cdp.py 版本旧（写死 9222）。现行版已支持
                                           --port（2026-10-18 改的）。
    ⛔ 闸门不放行（A/B/C 档）              通行证没有对应档位，或租约不在你名下。
                                           ctl_status 看当前档位与租约。
    落点判成 wrong_target（遮挡者=面板）    agentctl 自己的悬浮面板挡住了目标窗。
                                           2026-10-20 起会自动让位（面板收起来、
                                           动作完弹回），见 panel_yield.py。
    点自己面板被拒                          **这是设计**：面板是 sensitive_window
                                           （防绕过授权）。让位 ≠ 允许点它。

---

## 6. 相关文件清单

    文件                                          位置 / 行号        干什么
    ────────────────────────────────────────────────────────────────────────────
    src/agentctl/core.py                        4054  t_browser()    action→argv、分档、包信封
    src/agentctl/core.py                        4072  _cdp_launch   起浏览器实例（端口占用⇒拒）
    src/agentctl/core.py                        4093  _legacy(...)   调真源 cdp.py
    src/agentctl/core.py                        4100+ 注释          「这段不重写 CDP」
    src/agentctl/cli.py                         ALIAS  "browser"→ctl_browser
    src/agentctl/mcp.py                         handle() tools/call → core.dispatch
    src/agentctl/quiz.py                        ——      ctl_quiz：超星客观题（只答不提交）
    src/agentctl/widget.py                      ——      面板（通用电脑控制，无作业入口）
    src/agentctl/panel_yield.py                 ——      面板让位（2026-10-20 新增）
    src/agentctl/authstate.py                   206    敏感窗终判（读侧）
    dsh-viewer-plugin/tools/cdp.py              58     READONLY_CMDS / ACTION_CMDS
    dsh-viewer-plugin/tools/cdp.py              78     PORT（模块级，--port 写进来）
    dsh-viewer-plugin/tools/cdp.py              CDP 类  __init__ 过闸 / need_action / call / js
    contract/tools.json                         ctl_browser 的 schema（机器可读，以它为准）

---

## 7. 一句话给下一个人

**网页的事走 CDP（DOM/JS，精确、不抢鼠标）；桌面其它的事走 UIA + 坐标动作 + OCR。**
两条线都在 `core.dispatch` 收口，都过同一套闸门，都记同一条时间线。
网页场景先想"能不能用 `Runtime.evaluate` 拿到结构"，别急着截屏找像素。


## 7. 同一套 CDP 通道怎么开给 Electron 应用（2026-10-20 实测：MiniMax Code）

MiniMax Code（`F:\MiniMax Code\MiniMax Code.exe`，Electron 42.8 / Chromium 148）
也能用 agentctl 驱动，走的是**同一条 `ctl_browser` 通道**，不用真键鼠。

| 步骤 | 做法 | 踩到的坑 |
|---|---|---|
| 0 | `ctl_preflight` 探窗口 | 窗口**最小化**时 UIA 树是塌的（报「窗口已最小化，UIA 树是塌的」）⇒ 先 `ctl_focus` 叫醒 |
| 1 | 探通道 | UIA **0 节点**（Electron 默认不开无障碍）⇒ 只能走 CDP |
| 2 | 开端口 | 运行中的实例没法补开端口 ⇒ 必须重启：`--remote-debugging-port=9335` |
| 3 | 保留登录态 | **不要**加 `--user-data-dir`（换 profile 会掉登录态）；单实例锁要 taskkill /T /F 清干净，否则新实例把参数转给老实例就退出 |
| 4 | 接上 | `ctl_browser action=pages port=9335` ⇒ 主窗口 target 是 `app://./archon`（另一个是 react-screenshots） |
| 5 | 填字 | 输入框是 tiptap/ProseMirror 的 `contenteditable` ⇒ `document.execCommand('insertText', false, text)`，`ok:true`（ProseMirror 收到 input 事件，state 正确） |
| 6 | 发送 | 发送键是 `DIV[aria-label='发送消息']` |
| 7 | 读回复 | `ctl_browser action=eval` 读 `document.body.innerText` |

**端到端实测**：发「你好，我是通过 agentctl 的 CDP 通道发过来的…」⇒ 对方回
「agentctl 通道已打通。」（M3.1-Flash，6 秒 / 9.5 token/s）。

三个必须记住的坑：

1. `core.py:1058` 把 legacy 输出截到 **4000 字符** ⇒ 大探针结果会被砍，
   要么让 JS 自己过滤，要么分批查。
2. **PowerShell 会吃掉 CSS selector 里的引号** ⇒ `[aria-label="发送消息"]`
   传过去就废了（NOT_FOUND）。改法：在 JS 里遍历元素、直接比对
   `getAttribute('aria-label')`。
3. `ctl_browser` 的 `url_contains` 要给 `archon` 才选对 target（一个端口上有多个 target）。

脚本留在 `agent-ctl/work/_mm_chat.py`（probe / fill / clickaria / read / send）
+ `_mm_cdp_up.py`（带端口重启）。


## 8. MCP 的传输层：stdio 与 HTTP（2026-10-20 新增）

```
Codex 宿主
  │ ① stdio：父进程持有 stdin 管道 ──► agentctl.mcp（随宿主生死）
  │ ② HTTP ：POST /mcp ──────────────► agentctl.mcp_http（常驻，自己守着连接）
                                          │
                                          └─► core.dispatch() ─► 同一套闸门/租约/急停
```

| | stdio（`agentctl`） | Streamable HTTP（`agentctl_http`） |
|---|---|---|
| 端口/地址 | 无（走父进程管道） | `127.0.0.1:8761/mcp` |
| 认证 | 无（管道即凭据） | `Authorization: Bearer <state\token>` + Host 白名单 |
| 谁决定生死 | **宿主**（父进程一断，server EOF 退出） | **我们自己** |
| 典型故障 | `Transport closed`（工具还在列表里，一调就死） | 端口没监听 ⇒ 连接被拒（报错直说） |
| 适合 | 一次性、随会话起落的调用 | **长跑 / 无人值守** |

★ 死因实测（日志原话）：
```
[17:38:30] tools/call ctl_quiz actor=codex args={"action":"status","port":9333}
[17:38:34] tools/call ctl_quiz -> ok=True code=0
[17:38:34] stdin 结束（EOF / 父进程断开）⇒ server 退出
```
⇒ **调用是成功的**，6 秒后父进程关管道才退出 —— 不是崩溃，是宿主走了。
⇒ 心跳/重试是错层（stdin EOF 后没法 reopen）；**重连永远是宿主的事** ⇒ 换传输。

实现要点：`src/agentctl/mcp_http.py` **不复制协议逻辑**，直接复用 `mcp.py` 的
`handle()`（输出目标与 actor 由 HTTP 层注入）；stdio 侧行为完全不变。


### 2026-10-21 实测修正：握手的真凶是「Accept 头顺序」

```
accept: text/event-stream, application/json      ← SSE 在前
```

服务端若「见 text/event-stream 就回 SSE」，而 SSE 又不写 chunked 结束块，
客户端就会**干等流结束直到超时**，报成
`error sending request ... when send initialize request`（**看着像网络错**）。
⇒ 两种都接受时**一律回 JSON**；SSE 必须写结束块。

实测成功的样子（服务端日志）：

```
HTTP POST 令牌=有 鉴权=过 actor=mcp-http ua=codex-mcp-client/0.163.0-alpha.10.2
tools/call ctl_status actor=mcp-http args={}
tools/call ctl_status -> ok=True code=0
HTTP 响应形态=JSON（客户端 Accept=text/event-stream, application/json）
```

⇒ Codex 侧：`mcp: agentctl_http/ctl_status (completed)` ⇒ **HTTP 传输打通**。
