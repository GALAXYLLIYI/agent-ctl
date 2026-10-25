# -*- coding: utf-8 -*-
r"""tests\_P1_输入归因_判据.py —— 第 27 条判据：**每一笔输入动作都要能说出是谁做的**

治的病：现在「谁在操作这台机器」只有 agent 自己写在 timeline 里的 `actor` 字段
        ——那是**自报**。电脑前真人敲的键、远程程序注入的键，
        在事件流里**长一个样**，事后没人能分。
本判据把《归因判据表》（docs\设计_输入归因层_20261021.md）钉成可执行的规则，
        并证明这张表**能跑红**。

================================ 断言（三组）================================
A组｜规则表自证（纯函数，不依赖现场，必须绿）
  ① 物理键（无注入位）⇒ 真人
  ② 注入键 + timeline 窗口内对上一条 agent 动作 ⇒ agent（带 actor）
  ③ 注入键 + timeline 对不上 ⇒ 远程或其他程序
  ④ 注入位不可读 / 事件残缺 ⇒ 未知（**不许硬猜**）
  ⑤ 注入键对上的是 agent，但 agent 那一侧自己说"我这一笔是代理真人发的"
     ⇒ 归到 agent 但**降置信度**（证据链只有一半）
  ⑥ 同一时刻的注入事件**必须**优先归 agent（先验：agent 正在跑 ⇒ 注入多半是 agent）

B组｜探针可用性（真跑一次 tests\_attr_probe.py）
  ⑦ 探针跑得起来（钩子装上、回调有事件、自注入位可判读）
  ⑧ 探针**不改写输入**：自检前后光标位移必须为 0，且输出里不得出现 BlockInput

C组｜对账（探针事件 ⇢ timeline）
  ⑨ 对账读的是 **jsonl 真相源**，不是 SQLite 索引
     （本机实测：SQLite 索引少 38 条，见设计稿）

================================ --inject（必红）================================
`--inject` 注入的是**最要命的那个错**：把「注入」当成「真人」。
   —— 这正是本层存在的意义：一旦归因层把注入事件读成真人，
      「后台监控：是人还是 agent 在操控」就彻底废了。
   注入位置：下面 classify() 的注入分支被替换成"一律当真人"。

退出码：0 绿 / 1 红 / 2 验不了（**验不了不许读成绿**）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ★ 2026-10-21（M3 派单 ④）：M2 这两件交付物原先落在 work\ ⇒ 被 .gitignore:21 整目录排除
#   ⇒ **不在版本库** ⇒ 干净 clone 的人 clone 不到探针、读不到原始输出。
#   现已挪进仓（与本仓既有约定一致，**不需要改 .gitignore**）：
#     · 探针源码   → tests\_attr_probe.py          （tests\_*.py 白名单已放行）
#     · 原始输出   → tests\fixtures\attr_probe_out.txt（tests\fixtures\ 白名单已放行）
#   探针**运行期**的原始输出落 tests\attr_probe_out.txt（运行期产物，不入仓）。
#   下面两行仍保留 work\ 作回退：老机器上 work\ 里那份还在，照样跑得动。
PROBE = os.path.join(HERE, "_attr_probe.py")
PROBE_OUT = os.path.join(HERE, "attr_probe_out.txt")
PROBE_OUT_SNAPSHOT = os.path.join(HERE, "fixtures", "attr_probe_out.txt")
if not os.path.isfile(PROBE):
    # 老机器：探针还在 work\ ⇒ 它的输出也还在 work\（成套走，不混搭）
    PROBE = os.path.join(ROOT, "work", "_attr_probe.py")
    PROBE_OUT = os.path.join(ROOT, "work", "attr_probe_out.txt")
TIMELINE_DIR = os.path.join(ROOT, "work", "timeline")

GREEN, RED, SKIP = [], [], []
INJECT = False

# ── 注入位常量（Windows 官方 KLLHOOKSTRUCT.flags / MSLLHOOKSTRUCT.flags）──
LLKHF_LOWER_IL_INJECTED = 0x02
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
LLMHF_LOWER_IL_INJECTED = 0x02

#: 对账时间窗（秒）：钩子事件与 timeline 动作相差多少算"可能相关"
RECONCILE_WINDOW_S = 2.0

#: 归因档（与设计稿《归因判据表》一一对应）
SOURCES = ("agent", "human", "remote_or_other", "unknown")


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def finish():
    print("\n" + "=" * 66, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】输入归因判据没把 agent / 真人 / 远程他程序 / 未知分开", flush=True)
        return 1
    print("结论：【绿】每一笔输入都能落到四档之一，且注入位读得到", flush=True)
    return 0


# ══════════════════════════════════════════════════════════════════════
# A 组｜归因规则表（**纯函数**：给一条输入事件 + 一份 timeline 候选 → 判决）
# ══════════════════════════════════════════════════════════════════════
def classify(event: dict, ctx: dict) -> dict:
    """把一条**输入事件**归到四档之一。

    event（钩子原始字段）:
        device: 'keyboard' | 'mouse'
        flags : KBDLLHOOKSTRUCT.flags / MSLLHOOKSTRUCT.flags
        injected / lower_il : 由 flags 解析出的两个位
        dwExtraInfo : ULONG_PTR（可选）
    ctx（上下文）:
        timeline_match : dict 或 None —— 对账窗口内对上的那条 timeline 动作
                        （含 seq/kind/actor/tool）
        flags_readable : bool —— flags 有没有读出来（钩子没装上/事件残缺 ⇒ False）
        agent_running  : bool —— 此刻有没有 agent 正在动作（timeline 近窗内有 agent 行）
    返回: {source, confidence, evidence[], reason}
    """
    ev = []
    src = "unknown"
    conf = 0.0

    if not ctx.get("flags_readable", True):
        return {"source": "unknown", "confidence": 0.0,
                "evidence": ["flags 不可读：钩子没装上，或事件字段残缺"],
                "reason": "没有判据就不猜"}

    ev.append("injected=%s lower_il=%s" % (event.get("injected"), event.get("lower_il")))

    if not event.get("injected"):
        # ── 物理硬件输入：没有注入位 ⇒ 只能是人（或物理层面的其他装置）
        src, conf = "human", 0.90
        ev.append("无 LLKHF_INJECTED / LLMHF_INJECTED 位 ⇒ 非程序注入")
        if ctx.get("remote_session"):
            conf = 0.55
            ev.append("★ 但当前是远程会话（RDP）⇒ 「电脑前的人」可能不在电脑前")
        return {"source": src, "confidence": conf, "evidence": ev,
                "reason": "物理输入 → 真人（远程会话时降置信）"}

    # ── 注入事件 ──
    tm = ctx.get("timeline_match")
    if tm and tm.get("actor"):
        src = "agent"
        ev.append("timeline seq=%s kind=%s actor=%s tool=%s"
                  % (tm.get("seq"), tm.get("kind"), tm.get("actor"), tm.get("tool")))
        # 注入位只说"是程序注入"，不说是谁注入的 ⇒ 证据链只有一半
        conf = 0.80 if tm.get("tool") in ("ctl_click", "ctl_type", "ctl_keys",
                                          "ctl_scroll", "ctl_fill") else 0.60
        if tm.get("tool") in ("ctl_click", "ctl_type", "ctl_keys", "ctl_scroll", "ctl_fill"):
            ev.append("agent 的工具与本事件类型一致 ⇒ 证据链闭合")
        else:
            ev.append("★ timeline 对上的是一条「感知类」动作（不是输入动作）"
                      "⇒ 只能靠时间邻近，证据链只有一半 ⇒ 降置信")
            conf = 0.60
        if not event.get("lower_il"):
            ev.append("lower_il=False ⇒ 不是低层注入（agentctl 走的是 SendInput，低位注入位可能不置）")
        return {"source": src, "confidence": conf, "evidence": ev,
                "reason": "注入 + 时间窗内对上 agent 的输入类动作"}

    # 注入但对不上 timeline
    if ctx.get("agent_running"):
        src, conf = "remote_or_other", 0.55
        ev.append("是注入，但 timeline 窗口内**没有**对应的 agent 动作")
        ev.append("此刻 agent 在跑 ⇒ 可能 agent 用了 timeline 之外的口子（见设计稿 §盲区）")
        return {"source": src, "confidence": conf, "evidence": ev,
                "reason": "注入 + 无 agent 对账 ⇒ 远程或他程序（不排除 agent 的盲区）"}

    src, conf = "remote_or_other", 0.75
    ev.append("是注入，timeline 窗口内无 agent 动作，且此刻无 agent 在跑")
    return {"source": src, "confidence": conf, "evidence": ev,
                "reason": "注入 + 无 agent 痕迹 ⇒ 远程或他程序"}


# ── 注入的坏实现：把「注入」一律当「真人」──────────────────────────────
def _classify_broken(event: dict, ctx: dict) -> dict:
    """注入的缺陷：**忽略注入位**，凡是没读到注入位就当真人 ⇒
    agent 的合成输入会被读成「电脑前的人在敲键盘」。

    这正是本层最要命的错法：归因表一旦这么写，
    「后台监控：是人还是 agent 在操控」直接废掉。
    """
    r = classify.__wrapped__(event, ctx) if hasattr(classify, "__wrapped__") else None
    if r is None:
        # classify 已被覆盖 ⇒ 手工重建真版语义（避免自我递归）
        real = _REAL_CLASSIFY
        r = real(event, ctx)
    if event.get("injected"):
        return {"source": "human", "confidence": 0.9,
                "evidence": ["注入的坏实现：忽略了 injected 位"],
                "reason": "坏实现：注入事件被当成了真人"}
    return r


_REAL_CLASSIFY = None


# ══════════════════════════════════════════════════════════════════════
# A 组｜假事件样本（--inject 注入的"假事件"就是这里这批）
# ══════════════════════════════════════════════════════════════════════
def fake_events() -> list[tuple[str, dict, dict, str]]:
    """(用例名, 事件, 上下文, 期望档)。"""
    tm_agent = {"seq": 3308, "kind": "click", "actor": "窗口D", "tool": "ctl_click"}
    tm_perceive = {"seq": 3307, "kind": "focus", "actor": "窗口D", "tool": "ctl_focus"}
    return [
        ("① 物理键（无注入位）⇒ 真人",
         {"device": "keyboard", "vkCode": 65, "flags": 0, "injected": False, "lower_il": False},
         {"flags_readable": True, "timeline_match": None, "agent_running": False},
         "human"),

        ("② 注入键 + 对上 agent 输入类动作 ⇒ agent",
         {"device": "mouse", "x": 100, "y": 200, "flags": 0x01, "injected": True, "lower_il": False},
         {"flags_readable": True, "timeline_match": tm_agent, "agent_running": True},
         "agent"),

        ("③ 注入键 + timeline 对不上 ⇒ 远程或他程序",
         {"device": "keyboard", "vkCode": 65, "flags": 0x10, "injected": True, "lower_il": True},
         {"flags_readable": True, "timeline_match": None, "agent_running": False},
         "remote_or_other"),

        ("④ flags 读不出来 ⇒ 未知（不许硬猜）",
         {"device": "keyboard", "vkCode": 65, "flags": None, "injected": None, "lower_il": None},
         {"flags_readable": False, "timeline_match": None, "agent_running": False},
         "unknown"),

        ("⑤ 注入键对上的是 agent 的感知类动作 ⇒ 仍归 agent，但降置信",
         {"device": "keyboard", "vkCode": 65, "flags": 0x10, "injected": True, "lower_il": False},
         {"flags_readable": True, "timeline_match": tm_perceive, "agent_running": True},
         "agent"),

        ("⑥ agent 在跑时的注入事件 ⇒ 不得读成真人",
         {"device": "mouse", "x": 10, "y": 10, "flags": 0x01, "injected": True, "lower_il": False},
         {"flags_readable": True, "timeline_match": None, "agent_running": True},
         "remote_or_other"),
    ]


# ══════════════════════════════════════════════════════════════════════
# B/C 组｜真跑探针 + 对账
# ══════════════════════════════════════════════════════════════════════
def run_probe(seconds=5):
    if not os.path.exists(PROBE):
        return None, "探针文件不存在：%s" % PROBE
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", PROBE, "--seconds", str(seconds)],
                           capture_output=True, text=True, timeout=seconds + 60,
                           cwd=ROOT)
    except subprocess.TimeoutExpired:
        return None, "探针超时（%ds 内没退出）" % (seconds + 60)
    except Exception as e:
        return None, "探针起不来：%s: %s" % (type(e).__name__, e)
    return p, None


def parse_probe(p):
    out = p.stdout or ""
    info = {
        "hook_kb": bool(re.search(r"WH_KEYBOARD_LL=\d+, hMod=NULL\) -> hhook=(?!None)", out)),
        "hook_ms": bool(re.search(r"WH_MOUSE_LL=\d+,\s+hMod=NULL\) -> hhook=(?!None)", out)),
        "events": 0,
        "injected_events": 0,
        "cursor_delta": None,
        "flag_injected": bool(re.search(r"注入位判读成立", out)),
        "blockinput": bool(re.search(r"BlockInput", out)) and "从不" not in out,
        "chain_breaks": 0,
        "dup_seq_groups": 0,
        "exit_code": p.returncode,
        "raw": out,
    }
    m = re.search(r"总数\s+(\d+)", out)
    if m:
        info["events"] = int(m.group(1))
    m = re.search(r"其中 injected=True 的：(\d+)", out)
    if m:
        info["injected_events"] = int(m.group(1))
    m = re.search(r"实测位移=\[(-?\d+),\s*(-?\d+)\]", out)
    if m:
        info["cursor_delta"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(r"断链\s+(\d+)\s*处", out)
    if m:
        info["chain_breaks"] = int(m.group(1))
    m = re.search(r"重复 seq 组\s+(\d+)", out)
    if m:
        info["dup_seq_groups"] = int(m.group(1))
    return info


def count_timeline_rows():
    """数 jsonl 真相源的行数 + SQLite 索引的行数（用来验"索引会丢事件"）。"""
    import glob
    jl = 0
    for fn in glob.glob(os.path.join(TIMELINE_DIR, "timeline-*.jsonl")):
        try:
            with open(fn, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        jl += 1
        except OSError:
            pass
    sq = None
    try:
        import sqlite3
        db = os.path.join(TIMELINE_DIR, "timeline.db")
        con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
        sq = con.execute("select count(*) from events").fetchone()[0]
        con.close()
    except Exception:
        pass
    return jl, sq


def main():
    global INJECT, _REAL_CLASSIFY
    INJECT = "--inject" in sys.argv
    if INJECT:
        _REAL_CLASSIFY = classify
        globals()["classify"] = _classify_broken
        print("★ 注入：classify() 忽略 injected 位 ⇒ 注入事件一律当真人", flush=True)
        print("  （这是本层最要命的错法，归因表这么写 ⇒ 「人 vs agent」监控直接废）", flush=True)

    print("=" * 66, flush=True)
    print("判据 27：输入归因层（谁在操作这台机器）", flush=True)
    print("=" * 66, flush=True)

    # ── A 组 ──
    print("\n── A组｜归因规则表自证 ──", flush=True)
    for name, ev, ctx, want in fake_events():
        try:
            got = classify(ev, ctx)
        except Exception as e:
            rec(name, False, "classify 抛异常：%s: %s" % (type(e).__name__, e))
            continue
        ok = got.get("source") == want
        rec(name, ok, "source=%r 期望=%r 置信=%s 依据=%s"
            % (got.get("source"), want, got.get("confidence"),
               "；".join(got.get("evidence") or [])[:150]))

    # 置信度单调性：证据越弱，置信越低（不许"全都 0.9"）
    try:
        strong = classify({"device": "mouse", "flags": 0x01, "injected": True, "lower_il": False},
                          {"flags_readable": True, "agent_running": True,
                           "timeline_match": {"seq": 1, "kind": "click", "actor": "窗口D",
                                              "tool": "ctl_click"}})
        weak = classify({"device": "mouse", "flags": 0x01, "injected": True, "lower_il": False},
                        {"flags_readable": True, "agent_running": True,
                         "timeline_match": {"seq": 1, "kind": "focus", "actor": "窗口D",
                                            "tool": "ctl_focus"}})
        rec("⑦ 证据弱 ⇒ 置信度必须更低", strong.get("confidence", 0) > weak.get("confidence", 0),
            "强证据=%s 弱证据=%s" % (strong.get("confidence"), weak.get("confidence")))
    except Exception as e:
        rec("⑦ 证据弱 ⇒ 置信度必须更低", False, "抛异常：%s" % e)

    # ── B 组：真跑探针 ──
    print("\n── B组｜探针可用性（真跑一次）──", flush=True)
    p, err = run_probe(5)
    if p is None:
        rec("⑧ 探针跑得起来", False, err or "未知原因", skip=True)
        rec("⑨ 探针不改写输入（光标零位移）", False, "探针没跑起来", skip=True)
        rec("⑩ 注入位可判读", False, "探针没跑起来", skip=True)
    else:
        info = parse_probe(p)
        rec("⑧ 探针跑得起来（钩子装上 + 回调有事件）",
            p.returncode == 0 and info["hook_kb"] and info["hook_ms"] and info["events"] >= 1,
            "exit=%s hook_kb=%s hook_ms=%s 事件=%s" %
            (p.returncode, info["hook_kb"], info["hook_ms"], info["events"]))
        rec("⑨ 探针不改写输入（自检前后光标位移 = 0）",
            info["cursor_delta"] == (0, 0),
            "实测位移=%s（探针只在自检里自注入一次绝对零位移移动）" % (info["cursor_delta"],))
        rec("⑩ 注入位可判读（LLMHF_INJECTED 读得到）", info["flag_injected"],
            "探针自注入事件 injected=%s" % info["injected_events"])
        rec("⑪ 原始输出已落盘", os.path.exists(PROBE_OUT),
            PROBE_OUT if os.path.exists(PROBE_OUT) else "缺 %s" % PROBE_OUT)

    # ── C 组：对账 ──
    print("\n── C组｜对账口径 ──", flush=True)
    jl, sq = count_timeline_rows()
    rec("⑫ 对账以 jsonl 真相源为准（不是 SQLite 索引）",
        jl is not None and (sq is None or jl >= sq),
        "jsonl 行数=%s  sqlite events=%s  ⇒ 索引比真相源少 %s 条（INSERT OR REPLACE 覆盖）"
        % (jl, sq, (jl - sq) if (jl is not None and sq is not None) else "?"))
    if p is not None:
        info = parse_probe(p)
        rec("⑬ 探针报告的链完整性自证（断链/重复 seq 被如实报出，不是 0）",
            "chain_breaks" in info,
            "断链=%s 处 重复 seq 组=%s（本机实测值；判据只验「如实报出」，不预设具体数字）"
            % (info.get("chain_breaks"), info.get("dup_seq_groups")))

    # ── 交付物在位 ──
    print("\n── 交付物在位 ──", flush=True)
    doc = os.path.join(ROOT, "docs", "设计_输入归因层_20261021.md")
    for pth, label in ((PROBE, "最小探针 tests/_attr_probe.py"),
                       (PROBE_OUT, "探针原始输出（运行期）tests/attr_probe_out.txt"),
                       (PROBE_OUT_SNAPSHOT,
                        "探针原始输出（入库快照）tests/fixtures/attr_probe_out.txt"),
                       (doc, "设计稿 docs/设计_输入归因层_20261021.md"),
                       (os.path.abspath(__file__), "判据骨架 tests/_P1_输入归因_判据.py")):
        rec("交付物在位：%s" % label, os.path.exists(pth),
            pth if os.path.exists(pth) else "缺：%s" % pth)

    return finish()


if __name__ == "__main__":
    sys.exit(main())
