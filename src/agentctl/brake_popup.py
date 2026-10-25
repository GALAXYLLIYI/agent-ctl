# -*- coding: utf-8 -*-
r"""brake_popup —— 急停拉下那一刻弹到**桌面最前**的告警窗（窗I · 2026-10-16）

## 为什么要有它（派单 work\_2h_wI_派单.md §一）
急停（brake）本来就有：`gate.BRAKE_FLAG` 是唯一真相源，`brake_daemon` 轮询它，
面板上也有一行「急停」。**缺的是「那一刻」**：用户不在面板前就完全不知道。
而急停是「出事时立刻停手」的事 —— 不知道 = 等于没拉。

所以本模块只做一件事：**BRAKE_FLAG 由无变有的那一刻，弹一张红的、置顶的、
不自己消失的窗口**，上面写清三件事：

    ① 是谁在动（actor + session_id，取自闸门/时间线日志；**取不到就写「未知」，不许编**）
    ② 它的 MCP 链接（本机清单来自 ~/.窗口D/config.toml 的 [mcp_servers.*]）
    ③ 怎么恢复（确切命令 + 重新授权入口 + 「先确认是谁在动」的提醒）

## 形态上刻意**不做**的三件事（做了等于没做）
    x 任务栏闪烁   x 托盘气泡   x 面板里多一行
用户看不见的提示，在安全这件事上没有价值。

## ★★ 两条硬约束（安全，违反即整单退回）
    1. 本窗**不含任何能改变急停状态的控件** —— 没有「解除急停」按钮。
       把闸门搬进弹窗 = 新开一个口子。最多只有「复制」「我知道了（仅关窗）」。
    2. 本窗的**类名**（WINDOW_CLASS）与**标题**（BLOCK_TITLE_KEY）都登记在
       legacy_tools\act.py 的 BLOCKED_CLASSES / BLOCKED_TITLES 里 ——
       否则 agent 能点掉它、盖住它，那它就从安全提示变成自授权口子。
       那一行旁边写了理由；本模块这两个常量是判据 tests\_急停弹窗判据.py 的冻结期望值。

## ★★★ 窗N（2026-10-16）追加：这张窗必须**自报身份**
    起因：窗口A（GLM）在本机桌面上看到**判据弹的**这张窗，认不出是测试还是真急停，
    做了五路只读取证后差点建议跑 `brake --action release` —— 真跑了只会往
    **真**日志里写一条假的「已解除」记录。

    口径只有一条：**急停状态源的路径**。
      · 状态源落在生产根（%LOCALAPPDATA%\AgentCtl\）下 ⇒ **真急停**
        ⇒ 标题、横幅**与窗N 之前逐字相同**，一个字节都不动；正文**第一行印出状态源路径**
        （窗N2 加的：真急停也要自报家门 —— 但**一个字的自证标注都不许有**）；
      · 落在别处（%TEMP%、work\_*沙箱*、AGENTCTL_BRAKE_FLAG 覆盖到别处）
        ⇒ **自证测试** ⇒ 标题/横幅/正文全部换成自证形态，并把那个沙箱路径**原样印出来**。
    ⛔ **不认名字**：不许因为 actor 叫「夹具」「test」「sandbox」就改判。
      名字是日志里的一句自由文本，谁都能写；路径是闸门真去读的那个文件。
      判据 tests\_急停弹窗判据.py 第 9k 条就盯着这一条（源码里不许出现测试名）。
    ⚠ 反向同样危险：**真急停若被误标成测试**，有人就会放过一个真的急停。
      所以生产状态源那一张，标题必须与冻结值逐字相同（判据 9h）。

## ★★ 窗N2（2026-10-16）：状态源路径要**印在正文里**
    裁决来源：窗口A 第 2 趟真屏幕验收。定性文案（「状态源不在生产路径」这类）
    **谁都能写** —— 一个写错的实现照样能印出那句话；而**路径是身份的证据**：
    要印对，就必须真读到闸门真正在用的那个状态源。
      · 自证模式 ⇒ 正文第一行印沙箱路径；真急停模式 ⇒ 正文第一行印生产路径。
      · 两种模式**同一个句式**（`状态源：<路径>`），这样判据能**对偶断言**。
      · 印在**正文**（EDIT 子控件）而不是标题栏 —— 长路径塞标题栏既难看，
        也只有正文才是「子控件文本级」可读的（判据/探针都从这里读）。
    ⚠ 这**动了一条旧承诺**：窗N 曾写「生产正文与窗N 之前逐字相同、一个字节都不动」。
      现在生产正文多了第一行 `状态源：<生产路径>`。改动它是有理由的
      （人看到窗要知道这是不是生产那一份），但**代价必须写在明处**，见回执。

## 为什么用原生 Win32（ctypes），不用 tkinter
★ 实测（2026-10-16，本机）：`import tkinter` 能过，但 `tk.Tk()` 直接崩 ——
  `TclError: Can't find a usable init.tcl`。这台机器的 Python 3.12 **Tcl/Tk 是坏的**，
  所以一切基于 Tk 的弹窗（包括 brake_daemon 里那条老横幅 _flash_alert）在这台机器上
  **根本弹不出来**。这不是「少一个依赖」，是依赖本身残废。
原生 Win32 顺带白送四样 Tk 给不了的东西：
  ① WS_EX_TOPMOST 是**真的**（判据直接 GetWindowLong 读得到那一位）；
  ② 按钮是**真的子 HWND**（判据能逐个 EnumChildWindows + GetWindowText 查文本，
     不用 UIA 去猜 Tk 画在纸上的东西）；
  ③ 窗口类名是**我们自己注册的**（能按类名拦，不受标题被改的影响）；
  ④ 正文用标准 EDIT 控件（ES_READONLY）—— **原生可选中、Ctrl+C 可复制**，
     这正是派单要求的「MCP 链接必须可选中复制」。

## 线程模型
窗口跑在**独立线程**（窗口消息循环必须在建它的线程）。别的线程要动它，
一律 PostMessage 一个 WM_PUMP 唤醒消息 + 排队等回话 —— 不跨线程直接调 Win32。
daemon 主循环只丢命令，**绝不阻塞**。

## 对外 API（brake_daemon 与判据 tests\_急停弹窗判据.py 都只用这几个）
    show(info=None) -> bool     非阻塞弹；返回有没有真弹出来
    hide()          -> bool     急停解除后收起
    stop()                      进程退出前收尾
    controller()                拿到实例（判据要读正文 / 控件文本用）

    窗N 另外公开两个（判据要拿它们断言「口径只认路径」）：
    state_source()    -> dict   {path, origin, sandbox, why}：真急停 还是 自证测试
    window_title(src) -> str    按状态源给标题（真急停 = 冻结值，一个字不多）
"""
from __future__ import annotations

import ctypes
import os
import queue
import re
import sys
import threading
import time
from ctypes import wintypes as wt

# ── 冻结常量（判据 tests\_急停弹窗判据.py 按字面比对，改这里必须同步判据）──
WINDOW_CLASS = "AgentCtlBrakePopup"
WINDOW_TITLE = "⛔ AgentCtl 急停 · 所有动作已停止"
BLOCK_TITLE_KEY = "AgentCtl 急停"

BANNER = "⚠ 已急停 · 所有动作已停止"
SUBLINE = "这张窗会一直留在最前面，直到你解除急停。"
SEC_WHO = "① 是谁在动（actor / session_id）"
SEC_MCP = "② 它的 MCP 链接"
SEC_HOW = "③ 怎么恢复"
UNKNOWN_ACTOR = "未知（闸门/时间线日志里没有 actor）"
FOOTER_PROD = "（本窗只显示信息，不含任何能改变急停状态的控件 —— 解除急停请用上面 ③ 的命令）"

# ── 窗N（2026-10-16）追加：自证测试的标注（判据 tests/_急停弹窗判据.py 第 ⑨ 组）──
#
# 为什么要有（派单 work/_2h_wN_派单.md）：这张窗过去**长得和真急停一模一样**，
# 于是第三方 agent 看到它认不出是测试，差点去解除一个根本不存在的急停。
#
# ★★★ 口径只有一条：**状态源路径**。不认 actor 名字、不认时间线内容、不认任何启发式。
#   落在生产状态源下 ⇒ 真急停，保持今天的样子；落在别处 ⇒ 自证测试，自报家门。
SELFTEST_TITLE = "⛔【自证测试】AgentCtl 急停窗 · 非真实急停"
SELFTEST_MARK = "【自证测试】"
SELFTEST_NOTE = "非真实急停"
SELFTEST_BANNER = "⛔【自证测试】非真实急停 · 不要解除"
SELFTEST_SUB = "状态源不在生产路径：这是判据/自证弹的窗，不是真急停。"
SELFTEST_FOOTER = ("（本窗只显示信息，不含任何能改变急停状态的控件 —— "
                   "这里**也没有**解除命令，因为根本没有急停）")

# 生产状态源根目录。与 gate.py 的默认 _APP_DIR（行 405-406）**同一口径**：
# %LOCALAPPDATA%/AgentCtl。这里**故意不吃 AGENTCTL_APP_DIR** ——
# 那个覆盖本身就是「把状态源挪到别处」，挪了就该自报测试。
PROD_ROOT_NAME = "AgentCtl"
PROD_FLAG_NAME = "_急停.flag"

# ── 窗R（2026-10-16）追加：「状态源说自己是生产，可那个文件压根不存在」────
#
# ★ 病根：判据进程把**时间线**沙箱化了，却漏设 AGENTCTL_BRAKE_FLAG ⇒ 路径回退到
#   默认值，而默认值**恰好就是生产路径** ⇒ 窗N 的「按路径自报」把它判成真急停 ⇒
#   一个标注都不加 ⇒ 桌面上那张窗跟真急停**一模一样**，而真急停根本不存在。
#   看窗的人会当真急停去处理（这正是窗N 那次差点出事的位置）。
#
# ★ 这里**不改判定口径**：路径仍然是唯一依据，绝不改成「按 actor 名字猜」（窗N 红线）。
#   只做一件把话讲全的事：**既然状态源指向生产，那就去看那个文件到底在不在**。
#     · 在    ⇒ 保持今天的样子，真急停形态，一个标注都不加（判据 9h/9i 不能破）；
#     · 不在  ⇒ 如实说「这个状态源当前不存在」，让窗自己把疑点摆到看的人眼前。
#   这是**如实陈述**，不是另立一条判定 —— 它不改变「真/自证」这个结论，
#   只把「路径所指的状态并不存在」这件事实补上。
GHOST_NOTE_MARK = "该状态源文件当前不存在"
GHOST_NOTE = ("⚠ %s —— 本窗可能来自**未沙箱化的测试进程**（请核对是谁在动）。\r\n"
              "   真被拉下的急停，这个文件此刻一定在；它不在 ⇒ 这张窗的状态源是空的。"
              % GHOST_NOTE_MARK)

# 红底沿用 brake_daemon 老横幅那一档（#C42B1C），不另配色。
# COLORREF 是 0x00BBGGRR，所以这里的字节序是反的，别「顺手改成 RGB」。
BG_RED = 0x00C42B1C
FG_WHITE = 0x00FFFFFF
FG_DIM = 0x00D6D9FF          # #FFD9D6
FG_TEXT = 0x001A1A1A
BG_PANEL = 0x00F1F2F7        # #F7F2F1

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(REPO_ROOT, "src")
TIMELINE_DIR = os.environ.get("AGENTCTL_TIMELINE", os.path.join(REPO_ROOT, "work", "timeline"))

# 本机 MCP 客户端清单的来源。允许用 AGENTCTL_CODEX_CONFIG 指到别处（测试/换机）。
CODEX_CONFIG = os.environ.get("AGENTCTL_CODEX_CONFIG") or os.path.join(
    os.path.expanduser("~"), ".窗口D", "config.toml")


# ═══════════════════ ① 读 [mcp_servers.*]（自带极简 TOML 解析）═══════════════════
#
# 为什么不用 tomllib：① 它要 Python 3.11+，而这套东西是要打包成 exe 发的；
#   ② 更要紧的是 —— 我们**只要 command/args/enabled 三个键**，
#   绝不能把 `[mcp_servers.*.env]` 里的 Key 读出来贴到屏幕上。
#   自己解析 = 这个边界由代码保证，不靠「记得别打印」。
def _strip_comment(line: str) -> str:
    """去掉 # 注释，但引号里的 # 不算（引号外的第一个 # 才是注释起点）。"""
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#":
            return line[:i]
    return line


_SEC = re.compile(r"^\s*\[\s*([^\]]+?)\s*]\s*$")
_KV = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*=\s*(.*)$")
_BAL = re.compile(r"[\[\]]")


def _unquote(v: str) -> str:
    """TOML 的字符串值带引号。弹窗是要给人**复制**去用的，
    带着引号粘到命令行里跑不起来 ⇒ 显示前统一剥掉。"""
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def read_mcp_servers(path: str | None = None) -> tuple:
    """读 config.toml 的 [mcp_servers.*] -> ([{name,command,args,enabled}], 来源路径)。

    ★ 读不到返回 ([], 路径) —— **不抛、不猜**。调用方要如实告诉用户「读不到」。
    ★ 子表（`[mcp_servers.X.env]` 这类带点的段名）一律跳过：那里面是密钥。
    """
    path = path or CODEX_CONFIG
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return [], path

    out, cur, pending = [], None, None

    def flush():
        nonlocal cur, pending
        if cur is not None:
            cur["args"] = " ".join(str(cur.get("args", "")).split())
            out.append(cur)
        cur, pending = None, None

    for raw in lines:
        line = _strip_comment(raw)
        if not line.strip():
            continue
        m = _SEC.match(line)
        if m:
            flush()
            parts = m.group(1).strip().split(".")
            # 只要 `mcp_servers.<名字>` 这一层；带点的子表（env / http_headers）不进
            cur = ({"name": parts[1], "command": "", "args": "", "enabled": "true"}
                   if len(parts) == 2 and parts[0] == "mcp_servers" else None)
            continue
        if cur is None:
            continue
        km = _KV.match(line)
        if not km:
            continue
        k, v = km.group(1), km.group(2).strip()
        if pending is not None:
            # 上一行是跨行数组（如 args = ["a",
            cur[pending] = v
            if len(_BAL.findall(v)) % 2 == 0:
                pending = None
            continue
        if k in ("command", "args", "enabled"):
            cur[k] = _unquote(v)
            if k == "args" and len(_BAL.findall(v)) % 2 == 1:
                pending = k
    flush()
    return out, path


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_mcp(actor: str, servers: list) -> list:
    """按 actor 匹配 [mcp_servers.*]。**分三档**，只取命中的最高那一档：

        ① 段名完全相同        agentctl            -> agentctl
        ② 段名互相包含        companion_ball      -> companion
        ③ command 里含该名    npx minimax-mcp-js  -> minimax

    分档是为了不把「名字里恰好也有这个词」的无关服务器一起端上来。
    匹配不到返回 []，由调用方**列出全部候选**（不许空手而归）。
    """
    a = _norm(actor)
    if not a:
        return []
    for tier in (1, 2, 3):
        hit = []
        for s in servers:
            n = _norm(s.get("name") or "")
            if tier == 1 and n == a:
                hit.append(s)
            elif tier == 2 and n and (n in a or a in n):
                hit.append(s)
            elif tier == 3 and a in _norm(s.get("command") or ""):
                hit.append(s)
        if hit:
            return hit
    return []


def _mcp_block(actor: str) -> list:
    """拼 ② 那段的正文行。匹配不到就**列全部候选 + 写明来源文件**。"""
    servers, src = read_mcp_servers()
    lines = []
    hit = match_mcp(actor, servers)
    if hit:
        exact = any(_norm(s.get("name") or "") == _norm(actor) for s in hit)
        lines.append("已匹配到 actor「%s」对应的 MCP 段（来源：%s）：" % (actor, src) if exact
                     else "actor「%s」**没有同名**的 [mcp_servers.*] 段；"
                          "下面是 command 里带这个关键字的候选（**不一定就是它，自己核一眼**）"
                          "（来源：%s）：" % (actor, src))
        rows = hit
    else:
        why = ("actor 未知，无法匹配" if not actor
               else "actor「%s」没有对应的 [mcp_servers.*] 段" % actor)
        lines.append("%s ⇒ **列出全部候选，请自己认领**（来源：%s）：" % (why, src))
        rows = servers
    if not rows:
        lines.append("（%s 读不到任何 [mcp_servers.*] 段 —— 清单来源文件缺失或格式变了，"
                      "这一条**无法核实**，不是「没有 MCP」）" % src)
        return lines
    for s in rows:
        cmd = s.get("command") or "（没写 command）"
        args = s.get("args") or ""
        en = s.get("enabled") or "true"
        lines.append("  · [mcp_servers.%s]%s"
                     % (s.get("name"), "" if en == "true"
                        else "  （enabled=%s，当前是关的）" % en))
        lines.append("      command = %s%s" % (cmd, (" " + args) if args else ""))
    return lines


# ═══════════════════ ② 读「是谁」（actor / session_id）═══════════════════
def _timeline_tail(n: int = 400) -> list:
    """时间线 jsonl 的末尾若干条（**只读**，倒序扫，够用就停）。"""
    try:
        names = sorted(f for f in os.listdir(TIMELINE_DIR)
                       if f.startswith("timeline-") and f.endswith(".jsonl"))
    except OSError:
        return []
    out = []
    for nm in reversed(names):
        try:
            with open(os.path.join(TIMELINE_DIR, nm), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        out.append(line)
        except OSError:
            continue
        if len(out) >= n:
            break
    return list(reversed(out))


_GATE_ACTOR = re.compile(r"actor[=：]\s*([A-Za-z0-9_.\-]+)")


def read_last_actor() -> dict:
    """取**最近一次与闸门交互的身份** -> {actor, session_id, source}。

    口径（两条都读，谁先有值用谁）：
      ① 时间线事件（core.py 的 envelope 与 lease 都带 actor/session，结构化、优先）
      ② 闸门日志 gate_agentctl.log 里的 `actor=xxx`（自由文本，兜底）

    ★ **取不到就是空串**，由调用方如实显示「未知」。这里绝不许返回
      "unknown" / "system" / 主机名这类**编出来的身份** ——
      弹窗的全部价值就在于「是谁」，编一个等于把这窗变回一条没用的横幅。
    """
    import json
    # ① 时间线（倒序找第一条有 actor 的）
    for line in reversed(_timeline_tail()):
        try:
            r = json.loads(line)
        except Exception:
            continue
        actor = str(r.get("actor") or "").strip()
        if actor:
            return {"actor": actor,
                    "session_id": str(r.get("session") or "").strip(),
                    "source": "时间线事件 %s（tool=%s）" % (r.get("kind") or "?",
                                                            r.get("tool") or "?")}
    # ② 闸门日志
    try:
        from . import gate
        logp = gate.LOG
    except Exception:
        logp = None
    if logp:
        try:
            with open(logp, encoding="utf-8") as f:
                tail = f.readlines()[-400:]
            for line in reversed(tail):
                m = _GATE_ACTOR.search(line)
                if m and m.group(1):
                    return {"actor": m.group(1), "session_id": "",
                            "source": "闸门日志 %s" % logp}
        except OSError:
            pass
    return {"actor": "", "session_id": "", "source": "（日志里没有 actor）"}


# ═══════ ②bis 窗N：状态源判定（**这张窗是真急停还是自证测试**）════════════
#
# ★★★ 本单唯一的口径就是这一节，别的地方**不许**再判真假。
#   判据：急停 flag 落在哪儿。
#     · 环境变量 AGENTCTL_BRAKE_FLAG 优先（判据靠它切沙箱/生产）；
#     · 否则 gate.BRAKE_FLAG（gate.py 行 418，与 AGENTCTL_APP_DIR 同一口径）；
#     · 读不到 ⇒ 判「不是生产」—— fail-closed：拿不准就**不许当真急停**。
def _norm_path(p: str) -> str:
    """绝对化 + 大小写归一，**只用于比较**（拿去显示会把路径变成小写，不能用）。"""
    return os.path.normcase(os.path.abspath(p or ""))


def _under(path: str, root: str) -> bool:
    """path 在 root 之内（含 root 本身）。跨盘符时按「不在内」算。"""
    p, r = _norm_path(path), _norm_path(root)
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def production_root() -> str:
    """生产状态源根目录 = %LOCALAPPDATA%/AgentCtl（**唯一**的真急停状态源口径）。

    ★ 故意**不吃** AGENTCTL_APP_DIR：那个覆盖的作用就是「把状态源挪到别处」，
      挪走的那一份就不该顶着真急停的样子上桌面。
    """
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                        PROD_ROOT_NAME)


def production_flag() -> str:
    """生产状态源的具体文件（只作**说明**用：本模块从不写它）。"""
    return os.path.join(production_root(), PROD_FLAG_NAME)


def resolve_brake_flag() -> tuple:
    """**当场**解析急停状态源 -> (路径, 来自哪儿)。

    ★ 为什么不当成模块级常量：判据 tests/_急停弹窗判据.py 要在**同一个进程里**
      连着弹两张窗（先沙箱源、后生产源）。缓存下来的口径会让第二张窗还顶着
      第一张的身份 —— 那等于「改一次状态源得重启进程才生效」，是 bug 不是性能。
    """
    env = (os.environ.get("AGENTCTL_BRAKE_FLAG") or "").strip()
    if env:
        return os.path.abspath(env), "环境变量 AGENTCTL_BRAKE_FLAG"
    try:
        from . import gate
        p = str(getattr(gate, "BRAKE_FLAG", "") or "")
    except Exception:
        p = ""
    if p:
        return os.path.abspath(p), "gate.BRAKE_FLAG"
    return "", "（读不到急停标志路径）"


def state_source() -> dict:
    """判定这张窗是「真急停」还是「自证测试」→ {path, origin, sandbox, why}。

    ★★★ **只看状态源路径**。以下一律**不许**作为依据（判据 9j/9k 盯着）：
        actor 叫什么、时间线里写了什么、进程名、命令行 —— 全都不算。
        理由：那些都是**日志里的自由文本**，谁都能写；只有这个路径是
        闸门真去读的那个文件。那次认错人，正是因为手里只有名字。
    """
    path, origin = resolve_brake_flag()
    root = production_root()
    if not path:
        return {"path": "", "origin": origin, "sandbox": True,
                "prod_exists": None,
                "why": "读不到急停标志路径 —— 拿不准就不许当真急停"}
    if _under(path, root):
        # ★ 窗R：判定为「真急停」之后**再查一次实存性**。
        #   路径是判定依据（口径不变），而「这个路径指着的文件在不在」是事实。
        #   两者分开报，build_info() 才知道要不要加那行异常标注。
        try:
            exists = bool(path) and os.path.exists(path)
        except OSError:
            exists = False            # 查不动就当不在 —— 宁可多说一句，不许装作没事
        return {"path": path, "origin": origin, "sandbox": False,
                "prod_exists": exists,
                "why": "状态源在生产根 %s 之内" % root}
    why = "状态源不在生产根 %s 之内" % root
    try:
        import tempfile
        tmp = tempfile.gettempdir()
        if tmp and _under(path, tmp):
            why += "，且落在临时目录 %s 之下" % tmp
    except Exception:
        pass
    return {"path": path, "origin": origin, "sandbox": True,
            "prod_exists": None, "why": why}


def is_selftest() -> bool:
    return bool(state_source().get("sandbox"))


def window_title(src: dict = None) -> str:
    """该用哪个标题。真急停 = 冻结值（一个字都不许多）；沙箱源 = 自证测试标题。"""
    src = src or state_source()
    return SELFTEST_TITLE if src.get("sandbox") else WINDOW_TITLE


def _selftest_notice(src: dict) -> list:
    """自证测试时正文最上面那一块：把「凭什么判它是测试」直接印在窗上。

    ★ 为什么印得这么啰嗦：看这张窗的可能是个**不认识这套东西的第三方 agent**。
      它要的是「能不能动手」的判断依据，不是一句「这是测试哦」——
      得让它看到路径，自己能去核。
    """
    return [
        "%s AgentCtl 急停窗 · %s" % (SELFTEST_MARK, SELFTEST_NOTE),
        "状态源（沙箱）：%s" % (src.get("path") or "（读不到急停标志路径）"),
        "判定依据：%s —— **只认状态源路径**，不看 actor 名字、不看时间线内容。"
        % src.get("why", ""),
        "生产状态源（真急停长这样）：%s" % production_flag(),
        "",
        "⛔ 这是判据 / 自证跑出来的窗，**没有真的急停**。",
        "⛔ 不要执行 brake --action release，也不要点面板上的「解除急停」——",
        "     那两条动的是**真闸门**，跑一次就在真日志里写下一条假的「已解除」记录。",
    ]


def _selftest_how(src: dict) -> list:
    """自证测试时**刻意不给解除指引**。

    ★ 给了就等于在教一个认不出真假的 agent 去解除一个不存在的急停 ——
      那正是本单要堵的那个事故（它差点就跑了 brake --action release）。
    ③ 的小标题照样在（结构与真急停一致），内容换成「不要解除 + 怎么收工」。
    """
    return [
        "★ 这一段在**真急停**里给的是「确切解除命令」，这里**刻意不给** —— 没有急停可解除。",
        "  收工方式：右下角「✖ 我知道了（仅关窗，不动急停）」或标题栏的 ×。",
        "  ⚠ 就算你确信此刻真有急停，也请另拉一次**真**急停去确认，",
        "     别拿这张窗当凭据 —— 它证明的只是「有人在跑判据」。",
        "",
        "  状态源（沙箱）：%s" % (src.get("path") or "（读不到）"),
        "  真状态源长这样：%s" % production_flag(),
    ]


# ═══════════════════ ③ 恢复指引（**只印文字，不给按钮**）═══════════════════
def _release_lines() -> list:
    frozen = bool(getattr(sys, "frozen", False))
    exe = (r'"<安装目录>\AgentCtl.exe" brake release' if frozen
           else "python -X utf8 -m agentctl.cli brake --action release")
    prefix = ("（发行包：直接跑 exe，不用设 PYTHONPATH）" if frozen
              else "（源码树：先 set PYTHONPATH=%s）" % SRC_DIR)
    return [
        "解除急停的**确切命令**（本窗上没有这个按钮 —— 按设计，急停只能由你解除）：",
        "  命令行：  %s" % exe,
        "            %s" % prefix,
        "  控制面板：点面板上的「▶ 解除急停」按钮（同一条通道，只是图形界面）",
        "",
        "解除**之后**还要重新授权（急停解除 ≠ 通行证回来了）：",
        "  python -X utf8 -m agentctl.cli lease --action acquire --actor <你的名字> --scopes BC",
        "  然后由你在授权弹窗里签发当次通行证（闸门三闸照旧生效）。",
        "",
        "⚠ **恢复前先确认是谁在动** —— 急停不是故障，是有人把车停住了。",
        "  先看上面 ① 的 actor 和它的 MCP 链接，确认那个 agent 已经收手，",
        "  再解除急停。解除的一瞬间，所有自动控制立刻恢复。",
    ]


def build_info() -> dict:
    """弹窗要显示的全部内容（**纯数据、无副作用**，判据直接拿它断言）。

    窗N：先问一句「状态源是不是生产路径」（state_source）——
      · 是 ⇒ 标题/横幅/标注**与窗N 之前逐字相同**（真急停不多一个自证字）；
      · 否 ⇒ 标题/横幅/正文全换成自证测试的形态，并把**那个沙箱路径**印出来。

    窗N2：**两种模式都在正文第一行印状态源路径**（见模块文档「窗N2」那节）。
    """
    src = state_source()
    sandbox = bool(src.get("sandbox"))
    who = read_last_actor()
    actor = who.get("actor") or ""
    sid = who.get("session_id") or ""
    head = [
        "actor：%s" % (actor or UNKNOWN_ACTOR),
        "session_id：%s" % (sid or "未知（日志里没有 session_id）"),
        "取自：%s" % who.get("source", ""),
    ]
    if sandbox:
        # ★ 沙箱状态源：把「凭什么判它是测试」直接印在窗上 ——
        #   看窗的人（尤其是第三方 agent）不用去猜，也不用去翻目录。
        head = [
            "状态源（沙箱）：%s" % (src.get("path") or "（读不到）"),
            "为什么判它是测试：%s" % src.get("why", ""),
            "状态源来自：%s" % src.get("origin", ""),
            "时间线：%s" % TIMELINE_DIR,
        ] + head
    # ★ 窗N2：状态源路径**印在正文第一行**，两种模式同一个句式（判据可对偶断言）。
    #   路径**只从上面那次 state_source() 的结果里取**，绝不在这里自己再拼一遍 ——
    #   自己拼就成了第二个真源，而「印对路径」这件事的全部价值恰恰在于
    #   它必须是闸门真在读的那一个（写错的实现印不出对的路径）。
    src_line = "状态源：%s" % (src.get("path") or "（读不到急停标志路径）")
    # ★ 窗R：真急停形态 + 状态源文件**实存**，两条件同时成立时才一个标注都不加。
    #   缺第二个条件（路径指生产、文件却不在）⇒ 如实补一行，把疑点摆到看的人眼前。
    #   ⛔ 绝不用 actor 名字、时间线内容去「猜」这张窗是不是测试（窗N 红线，判据 9j/9k 盯着）。
    anomaly = ""
    if not sandbox and src.get("prod_exists") is False:
        anomaly = GHOST_NOTE
    return {
        "selftest": sandbox,
        "srcline": src_line,
        "anomaly": anomaly,
        "title": SELFTEST_TITLE if sandbox else WINDOW_TITLE,
        "banner": SELFTEST_BANNER if sandbox else BANNER,
        "sub": SELFTEST_SUB if sandbox else SUBLINE,
        "notice": _selftest_notice(src) if sandbox else "",
        "footer": SELFTEST_FOOTER if sandbox else FOOTER_PROD,
        "who": head,
        "mcp": _mcp_block(actor),
        "how": _selftest_how(src) if sandbox else _release_lines(),
    }

# ═══════════════════ 原生 Win32 那层 ═══════════════════
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WM_DESTROY, WM_CLOSE, WM_COMMAND, WM_SIZE = 0x0002, 0x0010, 0x0111, 0x0005
WM_SETFONT, WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x0030, 0x000C, 0x000D, 0x000E
WM_CTLCOLORSTATIC, WM_CTLCOLOREDIT = 0x0138, 0x0133
WM_PUMP = 0x8001                      # 自定义：唤醒消息循环去跑 Python 队列

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE, WS_CHILD, WS_TABSTOP = 0x10000000, 0x40000000, 0x00010000
WS_VSCROLL, ES_MULTILINE, ES_READONLY = 0x00200000, 0x0004, 0x0800
ES_AUTOVSCROLL, WS_BORDER, BS_PUSHBUTTON, SS_LEFT = 0x0040, 0x00800000, 0x0000, 0x0000
WS_EX_TOPMOST, WS_EX_TOOLWINDOW = 0x00000008, 0x00000080
SW_HIDE, SW_SHOW = 0, 5
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
GWL_EXSTYLE = -20
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
TRANSPARENT = 1
ERROR_CLASS_ALREADY_EXISTS = 1410

_ID_BANNER, _ID_SUB, _ID_EDIT, _ID_COPY, _ID_CLOSE = 1001, 1002, 1003, 1004, 1005

LRESULT = ctypes.c_ssize_t
LPARAM = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HANDLE),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, LPARAM)

# ── 显式原型：ctypes 默认按 c_int 处理指针，64 位句柄会被静默截断 ──
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HANDLE, wt.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, WPARAM, LPARAM]
user32.SendMessageW.restype = LRESULT
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, WPARAM, LPARAM]
user32.SetWindowTextW.restype = wt.BOOL
user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
user32.PostMessageW.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, WPARAM, LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetMessageW.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.IsWindow.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetDlgItem.restype = wt.HWND
user32.GetDlgItem.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowPos.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetForegroundWindow.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.MoveWindow.restype = wt.BOOL
user32.MoveWindow.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wt.BOOL]
user32.GetClientRect.restype = wt.BOOL
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.DestroyWindow.restype = wt.BOOL
user32.DestroyWindow.argtypes = [wt.HWND]
user32.EnumChildWindows.restype = wt.BOOL
user32.EnumChildWindows.argtypes = [wt.HWND, WNDENUMPROC, LPARAM]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.EmptyClipboard.restype = wt.BOOL
user32.SetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.GetClipboardData.restype = wt.HANDLE
user32.GetClipboardData.argtypes = [wt.UINT]
user32.CloseClipboard.restype = wt.BOOL
gdi32.CreateSolidBrush.restype = wt.HANDLE
gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateFontW.restype = wt.HANDLE
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [ctypes.c_int] * 8 + [wt.LPCWSTR]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteObject.argtypes = [wt.HANDLE]
gdi32.SetTextColor.restype = wt.COLORREF
gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
gdi32.SetBkColor.restype = wt.COLORREF
gdi32.SetBkColor.argtypes = [wt.HDC, wt.COLORREF]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GlobalAlloc.restype = wt.HANDLE
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = wt.LPVOID
kernel32.GlobalLock.argtypes = [wt.HANDLE]
kernel32.GlobalUnlock.restype = wt.BOOL
kernel32.GlobalUnlock.argtypes = [wt.HANDLE]
kernel32.GetLastError.restype = wt.DWORD

# 窗口过程要靠全局拿到控制器（一个进程只注册一个类，只有一个实例）
_CURRENT = None
_BRUSH = None
_FONT_BIG = None
_FONT_TXT = None
_FONT_BTN = None


def _child(cid):
    return user32.GetDlgItem(_CURRENT.hwnd, cid) if _CURRENT else 0


def _win_text(hwnd, want_class=False):
    if want_class:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(max(n + 2, 8))
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def _children(hwnd):
    out = []

    def cb(h, _l):
        out.append(h)
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return out


def _get_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.SendMessageW(hwnd, WM_GETTEXT, n + 1, ctypes.cast(buf, ctypes.c_void_p).value)
    return buf.value


def _set_text(hwnd, s):
    buf = ctypes.create_unicode_buffer(s)
    user32.SendMessageW(hwnd, WM_SETTEXT, 0, ctypes.cast(buf, ctypes.c_void_p).value)


def _clip_set(text):
    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        data = text + "\x00"
        size = len(data) * ctypes.sizeof(ctypes.c_wchar)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, ctypes.create_unicode_buffer(data), size)
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        user32.CloseClipboard()


def _clip_get():
    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return ""
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        return ctypes.wstring_at(kernel32.GlobalLock(h))
    finally:
        user32.CloseClipboard()


def _layout(hwnd):
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    pad = 14
    y = pad
    user32.MoveWindow(_child(_ID_BANNER), pad, y, w - 2 * pad, 44, True)
    y += 46
    user32.MoveWindow(_child(_ID_SUB), pad, y, w - 2 * pad, 22, True)
    y += 26
    user32.MoveWindow(_child(_ID_EDIT), pad, y, w - 2 * pad, max(80, h - y - 64), True)
    by = h - 52
    user32.MoveWindow(_child(_ID_COPY), pad, by, 170, 34, True)
    user32.MoveWindow(_child(_ID_CLOSE), w - pad - 250, by, 250, 34, True)


def _wndproc(hwnd, msg, wp, lp):
    """窗口过程。**只做三件事**：显示/隐藏、复制、关窗。没有一条改闸门状态。"""
    if msg == WM_PUMP:
        if _CURRENT:
            _CURRENT._drain()
        return 0
    if msg == WM_COMMAND:
        cid = wp & 0xFFFF
        if cid == _ID_COPY:
            try:
                _clip_set(_CURRENT._body if _CURRENT else "")
            except Exception:
                pass
        elif cid == _ID_CLOSE:
            user32.ShowWindow(hwnd, SW_HIDE)          # ★ 只关窗，**不动闸门**
        return 0
    if msg == WM_SIZE:
        _layout(hwnd)
        return 0
    if msg == WM_CTLCOLORSTATIC:
        gdi32.SetTextColor(wp, FG_WHITE if ((lp >> 16) & 0xFFFF) == _ID_BANNER else FG_DIM)
        gdi32.SetBkMode(wp, TRANSPARENT)
        return _BRUSH
    if msg == WM_CTLCOLOREDIT:
        gdi32.SetTextColor(wp, FG_TEXT)
        gdi32.SetBkColor(wp, BG_PANEL)
        return gdi32.CreateSolidBrush(BG_PANEL)
    if msg == WM_CLOSE:
        user32.ShowWindow(hwnd, SW_HIDE)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


_WNDPROC_PTR = WNDPROC(_wndproc)      # 必须留活引用：被 GC 了窗口过程就失效


def _compose(info: dict) -> str:
    """正文合成。

    窗N2：**第一行永远是状态源路径**（`状态源：<路径>`）—— 生产与自证**同句式**，
      一行就回答了「这张窗认的是哪个状态源」。判据在两种模式下读的是同一行。
    自证测试时在它下面多插一块 notice —— 看窗的人第一眼就该看到。
    """
    blocks = []
    if info.get("srcline"):
        blocks.append(info["srcline"])
    # 窗R：异常标注紧跟在状态源那一行下面 —— 它说的就是那一行的事，隔远了没人看得懂。
    if info.get("anomaly"):
        blocks.append(info["anomaly"])
    if info.get("notice"):
        blocks.append("\r\n".join(info["notice"]))
    blocks += [
        SEC_WHO + "\r\n" + "\r\n".join(info["who"]),
        SEC_MCP + "\r\n" + "\r\n".join(info["mcp"]),
        SEC_HOW + "\r\n" + "\r\n".join(info["how"]),
        info.get("footer", FOOTER_PROD),
    ]
    return "\r\n\r\n".join(blocks)


class _Popup:
    """弹窗控制器：一个窗口 + 一个命令队列。

    ★ 为什么命令要走队列 + PostMessage：Win32 窗口过程只能在**建它的线程**里跑，
      别的线程直接调 Win32 = 随机崩。
    ★ 为什么不 DestroyWindow：急停期间窗**不许自己消失**。用户点「我知道了」也只 SW_HIDE，
      窗口留着 —— 下次急停还能原样再弹（销毁重建会闪一下）。
    """

    PUMP_TIMEOUT = 5.0

    def __init__(self):
        global _CURRENT
        self.hwnd = 0
        self.q = queue.Queue()
        self._ready = threading.Event()
        self._body = ""
        _CURRENT = self
        self._th = threading.Thread(target=self._thread_main, name="brake-popup", daemon=True)
        self._th.start()
        self._ready.wait(8.0)

    # ── 窗口线程 ────────────────────────────────────────────────
    def _thread_main(self):
        global _BRUSH, _FONT_BIG, _FONT_TXT, _FONT_BTN
        hinst = kernel32.GetModuleHandleW(None)
        _BRUSH = gdi32.CreateSolidBrush(BG_RED)
        wc = WNDCLASSW()
        wc.style = 0x0003                      # CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = ctypes.cast(_WNDPROC_PTR, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.hIcon = user32.LoadIconW(None, 32512)
        wc.hCursor = user32.LoadCursorW(None, 32512)
        wc.hbrBackground = _BRUSH
        wc.lpszClassName = WINDOW_CLASS
        if not user32.RegisterClassW(ctypes.byref(wc)) and \
                kernel32.GetLastError() != ERROR_CLASS_ALREADY_EXISTS:
            self._ready.set()
            return
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        w, h = min(900, sw - 80), min(680, sh - 140)
        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW, WINDOW_CLASS, WINDOW_TITLE,
            WS_OVERLAPPEDWINDOW,
            max(0, (sw - w) // 2), max(0, (sh - h) // 3), w, h,
            None, None, hinst, None)
        if not self.hwnd:
            self._ready.set()
            return
        face = "Microsoft YaHei UI"
        _FONT_BIG = gdi32.CreateFontW(-34, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 0, 0, face)
        _FONT_TXT = gdi32.CreateFontW(-15, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, face)
        _FONT_BTN = gdi32.CreateFontW(-15, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, face)
        self._mk(_ID_BANNER, "STATIC", BANNER, SS_LEFT, _FONT_BIG)
        self._mk(_ID_SUB, "STATIC", SUBLINE, SS_LEFT, _FONT_TXT)
        self._mk(_ID_EDIT, "EDIT", "",
                 ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | WS_VSCROLL | WS_BORDER,
                 _FONT_TXT)
        # ★★ 硬约束①：这一段**只能有复制和关窗**。
        #   「解除急停」按钮一旦出现在这里，就等于给系统开了一个新的状态改动口 ——
        #   这条是本单的第一条硬约束，判据 tests\_急停弹窗判据.py 会按按钮文本逐条查。
        self._mk(_ID_COPY, "BUTTON", "📋 复制全部内容", BS_PUSHBUTTON | WS_TABSTOP,
                 _FONT_BTN)
        self._mk(_ID_CLOSE, "BUTTON", "✖ 我知道了（仅关窗，不动急停）",
                 BS_PUSHBUTTON | WS_TABSTOP, _FONT_BTN)
        _layout(self.hwnd)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _mk(self, cid, cls, text, style, font):
        h = user32.CreateWindowExW(0, cls, text, style | WS_CHILD | WS_VISIBLE,
                                   0, 0, 10, 10, self.hwnd, wt.HANDLE(cid),
                                   kernel32.GetModuleHandleW(None), None)
        if h and font:
            user32.SendMessageW(h, WM_SETFONT, font, 1)
        return h

    def _drain(self):
        while True:
            try:
                fn, a, box, ev = self.q.get_nowait()
            except queue.Empty:
                return
            try:
                box.append(fn(*a) if a else fn())
            except Exception as e:
                box.append(e)
            finally:
                ev.set()

    def _call(self, fn, *a, **kw):
        """把 fn 丢到窗口线程跑并等结果。窗没了/超时 -> 抛异常，不假装成功。"""
        timeout = kw.get("timeout", self.PUMP_TIMEOUT)
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            raise RuntimeError("弹窗窗口不在（建窗失败？）")
        box, ev = [], threading.Event()
        self.q.put((fn, a, box, ev))
        if not user32.PostMessageW(self.hwnd, WM_PUMP, 0, 0):
            raise RuntimeError("唤醒弹窗窗口失败")
        if not ev.wait(timeout):
            raise TimeoutError("弹窗窗口线程没回话（%.1fs）" % timeout)
        r = box[0] if box else None
        if isinstance(r, Exception):
            raise r
        return r

    # ── 对外 ────────────────────────────────────────────────────
    def show(self, info=None):
        def _do():
            inf = info or build_info()
            self._body = _compose(inf)
            _set_text(_child(_ID_EDIT), self._body)
            # ★ 窗N：**标题与横幅也一起自报身份**。只改正文是不够的 ——
            #   标题栏、任务栏预览、Alt-Tab 那一行、别的 agent 用
            #   FindWindow(标题) 找过来的那一份，全都还在按「真急停」理解。
            user32.SetWindowTextW(self.hwnd, inf.get("title") or window_title())
            _set_text(_child(_ID_BANNER), inf.get("banner") or BANNER)
            _set_text(_child(_ID_SUB), inf.get("sub") or SUBLINE)
            user32.ShowWindow(self.hwnd, SW_SHOW)
            # 置顶**每次都重设**：别的窗口随时可能抢到前面，置顶那一步是一次性的。
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            try:
                user32.SetForegroundWindow(self.hwnd)
            except Exception:
                pass
            return True
        self._call(_do)

    def hide(self):
        self._call(lambda: user32.ShowWindow(self.hwnd, SW_HIDE))

    def stop(self):
        try:
            self.hide()
        except Exception:
            pass
        try:
            self._call(lambda: user32.DestroyWindow(self.hwnd))
        except Exception:
            pass

    def visible(self):
        return bool(self._call(lambda: bool(user32.IsWindowVisible(self.hwnd))))

    def hwnd_of(self):
        return int(self.hwnd or 0)

    def body_text(self):
        """**自证接口**：窗上那段正文（判据据此查三段内容与 MCP 链接）。"""
        return self._call(lambda: _get_text(_child(_ID_EDIT)))

    def control_texts(self):
        """**自证接口**：本窗所有子控件的 [类名, 文本]（判据据此查「没有改状态的控件」）。"""
        return self._call(lambda: [[_win_text(h, True), _win_text(h)]
                                   for h in _children(self.hwnd)])

    def copy_all(self):
        """**自证接口**：复制按钮背后那个动作。返回真正进剪贴板的内容。"""
        self._call(lambda: _clip_set(self._body))
        return _clip_get()

    def clipboard_text(self):
        return _clip_get()


_CTRL = None
_CTRL_LOCK = threading.Lock()


def controller():
    """进程内单例。建窗失败（无桌面会话等）返回 None —— **急停本身绝不能因此受影响**。"""
    global _CTRL
    with _CTRL_LOCK:
        if _CTRL is None:
            try:
                _CTRL = _Popup()
            except Exception:
                _CTRL = None
        return _CTRL


def show(info=None) -> bool:
    """弹窗（非阻塞）。返回有没有真弹出来 —— 调用方照实记日志。"""
    try:
        c = controller()
        if c is None:
            return False
        c.show(info)
        src = state_source()
        if src.get("sandbox"):
            # 自证测试的那一张：往 stdout 也打一行，状态源路径是什么一眼可见 ——
            # 谁在控制台里跑判据、谁在翻日志，都不必只靠那张窗。
            print("[brake_popup] %s%s · 状态源（沙箱）：%s（%s）"
                  % (SELFTEST_MARK, SELFTEST_NOTE,
                     src.get("path") or "（读不到）", src.get("why", "")), flush=True)
        return True
    except Exception:
        return False


def hide() -> bool:
    try:
        c = controller()
        if c is None:
            return False
        c.hide()
        return True
    except Exception:
        return False


def stop():
    try:
        c = controller()
        if c is not None:
            c.stop()
    except Exception:
        pass


if __name__ == "__main__":                 # 人工看一眼窗长什么样
    print("show ->", show())
    c = controller()
    print("hwnd =", c.hwnd_of(), " 标题：", window_title(), " 类名：", WINDOW_CLASS)
    print("按 Ctrl+C 结束。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop()
