# -*- coding: utf-8 -*-
"""tools/llm_decide.py —— LLM 决策核：快照 → 动作 JSON（工单：第 1 步智能闭环）

定位：agent_loop.decide() 的**注入实现**（签名兼容 decide(snapshot, state)）。
模型：本地 Ollama qwen2.5:7b（文本决策，热态 ~0.8s；不联网、不花钱）。

三道安全层（决策核实测踩出的）：
    ① 工具白名单：模型只能从 {ctl_type, ctl_click, ctl_focus, ctl_keys, ctl_scroll, done} 选；
    ② 参数修正：模型常把 type 的文本塞进 keys/text 混用 ⇒ 按 tool 归位；
    ③ 兜底：JSON 解不开 / 工具不认识 / 连续 2 次坏输出 ⇒ 回退规则 decide()（永不死循环）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, "src")
import sys as _sys
if _SRC not in _sys.path:
    _sys.path.insert(0, _SRC)

ENV_MODEL = "AGENTCTL_DECISION_MODEL"
ENV_BASE = "AGENTCTL_DECISION_BASE"
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_BASE = "http://127.0.0.1:11434"

ALLOWED_TOOLS = {"ctl_type", "ctl_click", "ctl_focus", "ctl_keys", "ctl_scroll", "done"}

SYS_PROMPT = (
    "你是桌面 GUI agent 的决策核。根据快照选**下一个动作**，只输出一行 JSON，不要解释：\n"
    '{"tool": "ctl_type"|"ctl_click"|"ctl_focus"|"ctl_keys"|"ctl_scroll"|"done",\n'
    ' "args": {...}, "why": "一句话理由"}\n'
    "工具用法：\n"
    "· ctl_type：往窗口打字。args={\"hwnd\": 整数, \"text\": \"要打的原文\", \"mode\": \"focus\", \"clear\": false}\n"
    "· ctl_keys：按一个键。args={\"hwnd\": 整数, \"keys\": \"Enter\"}（键名如 Enter/Escape/Tab）\n"
    "· ctl_click：点控件。args={\"hwnd\": 整数, \"eid\": \"e数字\", \"from_capture\": \"快照给的capture_id\"}\n"
    "· ctl_focus：切前台。args={\"hwnd\": 整数}\n"
    "· done：任务已达成。args={}\n"
    "规则：打字用 ctl_type（不要用 ctl_keys 打文本）；hwnd 一律用快照里 target 的 hwnd；"
    "目标文字必须**原样**出现在 text 里，不许改写；一步只做一个动作。"
)

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _ollama_chat(messages: list[dict], timeout: float = 60.0) -> str:
    base = os.environ.get(ENV_BASE, DEFAULT_BASE).rstrip("/")
    model = os.environ.get(ENV_MODEL, DEFAULT_MODEL)
    payload = {"model": model, "stream": False, "keep_alive": "30m",
               "messages": messages, "options": {"temperature": 0}}
    req = urllib.request.Request(f"{base}/api/chat",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
    return (body.get("message") or {}).get("content") or ""


def _project_snapshot(snapshot: dict, state: dict) -> str:
    """快照 → 紧凑投影（≤25 条；DSH 难点①的落地：上下文每步重写，不累积）。"""
    win = snapshot.get("target") or {}
    scr = snapshot.get("screen") or {}
    objs = scr.get("objects") if isinstance(scr.get("objects"), list) else []
    lines = [
        f"任务: {state.get('task') or '（未给定，沿用既有计划）'}",
        f"目标窗口: hwnd={win.get('hwnd')} title={win.get('title')} process={win.get('process')}",
        f"capture_id: {scr.get('capture_id')}",
        f"已完成步数: {state.get('step') or 0}；已focus={bool(state.get('focused'))} "
        f"已typed={bool(state.get('typed'))} 已clicked={bool(state.get('clicked'))}",
    ]
    # 最近一步的结果（防重复打字：模型看到"上一步 type 已 success"就该选 done）
    hist = state.get("last_result") or ""
    if hist:
        lines.append(f"上一步动作结果: {hist}")
    if state.get("typed") and state.get("last_type_ok"):
        lines.append("!! 打字任务已成功完成（上一次 ctl_type code=0）—— 若任务只是打字，现在应选 done")
    if int(state.get("step") or 0) == 0:
        lines.append("!! 还没有任何动作（步数=0）—— 此时不许选 done，先完成任务的第一个动作")
    lines.append("可交互对象（eid 逐字照抄，编造 eid 会被闸门 code=12 拒）:")
    n = 0
    for o in objs:
        if isinstance(o, dict) and o.get("interactive") and o.get("eid"):
            lines.append(f"  eid={o.get('eid')} role={o.get('role')} label={o.get('label')!r}")
            n += 1
            if n >= 12:
                break
    if n == 0:
        lines.append("  （本屏没有可交互对象——不要选 ctl_click，打字任务直接选 done）")
    if snapshot.get("plan_lost"):
        lines.append("!! 计划窗口已丢失")
    return "\n".join(lines)


def _normalize(tool: str, args: Any, snapshot: dict) -> Optional[dict]:
    """白名单 + 参数修正 → 循环体认得的动作 dict；不合法 ⇒ None。"""
    if tool not in ALLOWED_TOOLS:
        return None
    if not isinstance(args, dict):
        args = {}
    win = snapshot.get("target") or {}
    try:
        hwnd = int(win.get("hwnd"))
    except (TypeError, ValueError):
        return None
    if tool == "done":
        return {"__done__": True, "why": str(args.get("why") or "模型判定任务完成")}
    # 参数归位：模型混用 text/keys ⇒ 按工具修正
    if tool == "ctl_type":
        text = str(args.get("text") or args.get("keys") or "").strip()
        if not text:
            return None
        return {"tool": tool, "kind": "type",
                "args": {"hwnd": hwnd, "text": text, "mode": "focus", "clear": False},
                "target": f"hwnd={hwnd} text={text}",
                "expect": f"打出 {text}"}
    if tool == "ctl_keys":
        keys = str(args.get("keys") or args.get("text") or "").strip()
        if not keys:
            return None
        return {"tool": tool, "kind": "keys",
                "args": {"hwnd": hwnd, "keys": keys},
                "target": f"hwnd={hwnd} keys={keys}",
                "expect": f"按键 {keys}"}
    if tool == "ctl_click":
        a = {"hwnd": hwnd, "expect": "点击"}
        if args.get("eid"):
            a["eid"] = str(args["eid"])
            cap = (snapshot.get("screen") or {}).get("capture_id")
            if cap:
                a["from_capture"] = str(cap)
        elif args.get("xy"):
            a["xy"] = args["xy"]
            cap = (snapshot.get("screen") or {}).get("capture_id")
            if cap:
                a["from_capture"] = str(cap)
        else:
            return None
        return {"tool": tool, "kind": "click", "args": a,
                "target": f"hwnd={hwnd} {a.get('eid') or a.get('xy')}",
                "expect": "点击生效"}
    if tool == "ctl_focus":
        return {"tool": tool, "kind": "focus", "args": {"hwnd": hwnd},
                "target": f"hwnd={hwnd}", "expect": "窗口切前台"}
    if tool == "ctl_scroll":
        return {"tool": tool, "kind": "scroll",
                "args": {"hwnd": hwnd, "amount": int(args.get("amount") or -3)},
                "target": f"hwnd={hwnd}", "expect": "滚动"}
    return None


def make_llm_decide(task: str, fallback=None, log=None):
    """工厂：返回签名兼容 decide(snapshot, state) 的 LLM 决策函数。

    fallback：坏输出时的兜底（默认规则 decide）；连续 2 次坏输出 ⇒ 本轮直接交棒 fallback。
    """
    if fallback is None:
        from agent_loop import decide as _rule_decide
        fallback = _rule_decide
    if log is None:
        log = lambda s: print(s, flush=True)  # noqa: E731

    bad_streak = {"n": 0}

    def llm_decide(snapshot: dict, state: Optional[dict] = None) -> Optional[dict]:
        st = state if isinstance(state, dict) else {}
        st.setdefault("task", task)
        try:
            user = _project_snapshot(snapshot, st)
            raw = _ollama_chat([{"role": "system", "content": SYS_PROMPT},
                                {"role": "user", "content": user}])
            m = _JSON_RE.search(raw)
            if not m:
                raise ValueError("回复里没有 JSON")
            obj = json.loads(m.group(0))
            tool = str(obj.get("tool") or "")
            action = _normalize(tool, obj.get("args") or {}, snapshot)
            if action is None:
                raise ValueError(f"工具/参数不合法: {tool}")
            bad_streak["n"] = 0
            if action.get("__done__"):
                log(f"[llm] done — {action.get('why')}")
                return None
            log(f"[llm] {action['tool']} — {obj.get('why', '')[:60]}")
            return action
        except Exception as exc:
            bad_streak["n"] += 1
            log(f"[llm] 坏输出({bad_streak['n']}): {exc} ⇒ 兜底规则决策")
            act = fallback(snapshot, st)
            if act is None:
                bad_streak["n"] = 0  # fallback 也收工 = 真结束
            return act

    return llm_decide
