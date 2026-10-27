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
    task = str(state.get("task") or "")
    win = snapshot.get("target") or {}
    scr = snapshot.get("screen") or {}
    objs = scr.get("objects") if isinstance(scr.get("objects"), list) else []
    lines = [
        f"任务: {task or '（未给定，沿用既有计划）'}",
        f"目标窗口: hwnd={win.get('hwnd')} title={win.get('title')} process={win.get('process')}",
        f"capture_id: {scr.get('capture_id')}",
        f"已完成步数: {state.get('step') or 0}；已focus={bool(state.get('focused'))} "
        f"已typed={bool(state.get('typed'))} 已clicked={bool(state.get('clicked'))}",
    ]
    # 最近一步的结果（防重复打字：模型看到"上一步 type 已 success"就该选 done）
    hist = str(state.get("last_result") or "")
    last_tool = hist.split(" ")[0] if hist else ""
    # ★ 完成度证据链（DSH 难点①配套）——**放在投影最前**（实测放对象清单后面模型读漏）：
    task_text = ""
    mt = re.search(r"(?:输入|打字|写入|打)[:：]?\s*[『\"“]?([^』\"”，。]+)", task or "")
    if mt:
        task_text = mt.group(1).strip()
    typed_ok = bool(state.get("last_type_ok"))
    last_action = str(state.get("last_args_text") or "")
    lines.append(f"任务目标文字: 「{task_text or '（非打字任务）'}」 最近成功打的内容: 「{last_action}」")
    if task_text and typed_ok and task_text in last_action:
        lines.append("★★ 任务已达成（要求打的字已成功写入）——现在必须选 done，不要再打字/按键")
    if last_tool == "ctl_keys" and "code=5" in hist:
        lines.append("!! 上一次按键 unobservable（焦点可能不在编辑区）——不要重复同一按键，"
                     "改选 done 或先 ctl_focus")
    if hist:
        lines.append(f"上一步动作结果: {hist}")
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
        lines.append("  （本屏没有可交互对象——若下面有[视觉候选]可用 ctl_click xy；打字任务直接选 done）")
    # 视觉候选（UIA 盲区时本地模型看图找的）：xy 已是屏幕物理坐标，可直接当 args.xy
    for vh in (snapshot.get("vision_hits") or [])[:3]:
        if isinstance(vh, dict):
            lines.append(f"  [视觉候选] label={vh.get('label')!r} xy={vh.get('xy')} "
                         f"from_capture={vh.get('from_capture')} ⇒ ctl_click args="
                         f"{{\"hwnd\": {win.get('hwnd')}, \"xy\": {vh.get('xy')}, "
                         f"\"from_capture\": \"{vh.get('from_capture')}\"}}")
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
    # ★ 外部探针 10：done 不是可归位的动作——归一层对 done 返回 None
    #   （收工判定在 llm_decide 的调用层处理，normalize 只管"会改变屏幕状态的动作"）。
    if tool == "done":
        return None
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
        # code=5 洪水的教训：按键需要窗口在前台且焦点正确——给 allow_foreground 提高命中
        # ★ 外部探针 09：ctrl/times 等模型给的参数**透传**，不许丢。
        ka = {"hwnd": hwnd, "keys": keys, "allow_foreground": True}
        if args.get("ctrl"):
            ka["ctrl"] = True
        try:
            if args.get("times") is not None:
                ka["times"] = int(args["times"])
        except (TypeError, ValueError):
            pass
        return {"tool": tool, "kind": "keys",
                "args": ka,
                "target": f"hwnd={hwnd} keys={keys}",
                "expect": f"按键 {keys}（画面变化）"}
    if tool == "ctl_click":
        # ★ 外部探针 16：args 不放泛化 expect——点击的核验期望要**点名观测到的元素**。
        a = {"hwnd": hwnd}
        if args.get("xy"):
            # ★ 外部探针 08：模型给的 xy 若来自**视觉候选**，必须绑视觉自己的
            #   from_capture（截图凭据）——绑 UIA 快照凭据是张冠李戴，必 code=12。
            xy = [int(t) for t in args["xy"]]
            matched = None
            for vh in (snapshot.get("vision_hits") or []):
                if isinstance(vh, dict) and [int(t) for t in (vh.get("xy") or [])] == xy:
                    matched = vh
                    break
            if matched is not None:
                a["xy"] = xy
                a["from_capture"] = str(matched.get("from_capture"))
                if matched.get("vision_label"):
                    a["vision_label"] = str(matched["vision_label"])
                if matched.get("origin"):
                    a["win_origin"] = list(matched["origin"])
                return {"tool": tool, "kind": "click", "args": a,
                        "target": f"hwnd={hwnd} vision_xy={xy}",
                        "expect": "点击视觉候选"}
            a["xy"] = xy
            # ★ 外部探针 25：xy 必须绑定一个**真实候选**（视觉命中或本屏 UIA 对象）——
            #   凭空坐标 [9999,9999] 是幻觉，直接丢弃（不绑任何凭据发出去）。
            snap_objs = (snapshot.get("screen") or {}).get("objects") or []
            bound = any(
                isinstance(o, dict) and o.get("interactive")
                and isinstance(o.get("rect"), list) and len(o.get("rect")) == 4
                and o["rect"][0] <= xy[0] <= o["rect"][2]
                and o["rect"][1] <= xy[1] <= o["rect"][3]
                for o in snap_objs
            )
            if not bound:
                return None
            cap = (snapshot.get("screen") or {}).get("capture_id")
            if cap:
                a["from_capture"] = str(cap)
            return {"tool": tool, "kind": "click", "args": a,
                    "target": f"hwnd={hwnd} xy={xy}",
                    "expect": "点击生效"}
        if args.get("eid"):
            # ★ eid 幻觉硬闸（外部探针 10 同源）：eid 必须在本屏快照白名单里；
            #   编造的 ⇒ **直接丢弃**（不静默换目标——静默改靶会点错东西）。
            snap_objs = (snapshot.get("screen") or {}).get("objects") or []
            valid = next((o for o in snap_objs
                          if isinstance(o, dict) and str(o.get("eid")) == str(args["eid"])), None)
            if valid is None:
                return None
            a["eid"] = str(args["eid"])
            cap = (snapshot.get("screen") or {}).get("capture_id")
            if cap:
                a["from_capture"] = str(cap)
            # ★ 外部探针 16：期望**点名观测到的元素**，不是一句泛泛的"点击"
            label = str(valid.get("label") or "").strip()
            return {"tool": tool, "kind": "click", "args": a,
                    "target": f"hwnd={hwnd} eid={args['eid']}",
                    "expect": f"点击『{label}』生效" if label else "点击生效"}
        return None
    if tool == "ctl_focus":
        return {"tool": tool, "kind": "focus", "args": {"hwnd": hwnd},
                "target": f"hwnd={hwnd}", "expect": "窗口切前台"}
    if tool == "ctl_scroll":
        return {"tool": tool, "kind": "scroll",
                "args": {"hwnd": hwnd, "amount": int(args.get("amount") or -3),
                         "allow_foreground": True},
                "target": f"hwnd={hwnd}", "expect": "滚动"}
    return None


def make_llm_decide(task: str, fallback=None, log=None,
                    llm_budget: int = 8, action_budget: int = 30):
    """工厂：返回签名兼容 decide(snapshot, state) 的**分层**决策函数（DSH 难点①落地）。

    分层（2026-10-27）：
        · L0 例行步走规则（fallback=规则 decide）——零 LLM 成本、零延迟；
        · L1 升级触发（六条，任一）：首步计划 / 上步失败(code≠0 且≠8) / 同错连 2 次 /
          窗口变了 / 每 K=5 步锚定 / 规则收工但动作预算还多（规则说没招了，问模型）；
        · LLM 硬上限：llm_budget 次 / action_budget 动作（默认 8/30）——超限只走规则；
        · 上下文：状态块**每步重写**（_project_snapshot，不累积历史）⇒ 单次输入 ≤1.5K tok。
    """
    if fallback is None:
        from agent_loop import decide as _rule_decide
        fallback = _rule_decide
    if log is None:
        log = lambda s: print(s, flush=True)  # noqa: E731

    st_counts = {"llm_used": 0, "actions": 0, "last_err": "", "same_err_n": 0,
                 "last_hwnd": None}

    def llm_decide(snapshot: dict, state: Optional[dict] = None) -> Optional[dict]:
        st = state if isinstance(state, dict) else {}
        st.setdefault("task", task)
        st_counts["actions"] += 1

        # ── L1 升级判定 ─────────────────────────────────────────────────────
        step = int(st.get("step") or 0)
        result = str(st.get("last_result") or "")
        is_fail = bool(result) and ("ok=False" in result) and ("code=8" not in result)
        if is_fail and result == st_counts["last_err"]:
            st_counts["same_err_n"] += 1
        else:
            st_counts["same_err_n"] = 1 if is_fail else 0
        st_counts["last_err"] = result
        hwnd_now = (snapshot.get("target") or {}).get("hwnd")
        win_changed = (st_counts["last_hwnd"] is not None
                       and hwnd_now != st_counts["last_hwnd"])
        st_counts["last_hwnd"] = hwnd_now

        escalate = (
            step == 0                                    # ① 首步计划
            or is_fail                                   # ② 上步失败
            or st_counts["same_err_n"] >= 2              # ③ 同错连 2 次
            or win_changed                               # ④ 窗口变了
            or (step > 0 and step % 5 == 0)              # ⑤ 每 5 步锚定
        )
        # ⑥ 规则收工但预算还多 ⇒ 先问一次模型（规则视野窄，模型可能还有招）
        #    （在 fallback 返回 None 后的二次机会，见下方 fallback-first 结构）

        llm_allowed = (st_counts["llm_used"] < llm_budget
                       and st_counts["actions"] <= action_budget)

        # ── 分层执行：例行步先走规则（省 LLM），升级步才问模型 ────────────────
        if not escalate and llm_allowed:
            act = fallback(snapshot, st)
            if act is not None:
                log(f"[L0 规则] {act.get('tool')} {json.dumps(act.get('args') or {}, ensure_ascii=False)[:80]}")
                return act
            # 规则没招（返回 None）⇒ 升级问模型（⑥）
            escalate = True

        if not llm_allowed:
            log(f"[预算] LLM 已用 {st_counts['llm_used']}/{llm_budget} ⇒ 只走规则")
            return fallback(snapshot, st)

        try:
            # ★ 外部探针 07：预算按**尝试**计数（超时/坏输出同样烧预算）——
            #   否则持续超时的本地模型会被无限重试（hammering）。
            st_counts["llm_used"] += 1
            user = _project_snapshot(snapshot, st)
            raw = _ollama_chat([{"role": "system", "content": SYS_PROMPT},
                                {"role": "user", "content": user}])
            m = _JSON_RE.search(raw)
            if not m:
                raise ValueError("回复里没有 JSON")
            obj = json.loads(m.group(0))
            tool = str(obj.get("tool") or "")
            # ★ 外部探针 13：done 需要**客观完成证据**（本会话至少一次 settled 成功）。
            #   证据不足时模型说 done ⇒ **不收工也不停**——转规则决策继续干活
            #   （规则会从"目标窗未 focus"这类第一步续起）。
            if tool == "done":
                if st.get("any_success"):
                    log(f"[L1 LLM#{st_counts['llm_used']}] done — "
                        f"{(obj.get('args') or {}).get('why') or '任务完成（有成功证据）'}")
                    return None
                # ★ 证据不足 ⇒ 忽略 done，**就地转规则续命**（从目标窗 focus 续起）；
                #   不经 fallback（探针的 fallback 可能是 None 桩 ⇒ 收工=中了模型的懒）。
                win = snapshot.get("target") or {}
                try:
                    hwnd = int(win.get("hwnd"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("done 无证据且无目标窗 ⇒ 无法继续") from exc
                st["focused"] = False
                log("[L1] 无证据 done ⇒ 忽略，转规则继续（focus 目标窗）")
                return {"tool": "ctl_focus", "kind": "focus", "args": {"hwnd": hwnd},
                        "target": f"hwnd={hwnd}",
                        "expect": "窗口切到前台（done 无证据，继续任务）"}
            action = _normalize(tool, obj.get("args") or {}, snapshot)
            if action is None:
                raise ValueError(f"工具/参数不合法: {tool}")
            if action.get("__done__"):
                log(f"[L1 LLM#{st_counts['llm_used']}] done — {action.get('why')}")
                return None
            log(f"[L1 LLM#{st_counts['llm_used']}] {action['tool']} — {obj.get('why', '')[:60]}")
            return action
        except Exception as exc:
            log(f"[L1 坏输出] {exc} ⇒ 兜底规则决策")
            return fallback(snapshot, st)

    return llm_decide
