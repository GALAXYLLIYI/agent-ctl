# -*- coding: utf-8 -*-
r"""tools/agent_loop.py —— 感知→决策→动作→核验→记忆 循环 demo v0（工单：派单_感知动作记忆循环_20261026）

循环形态（台账已定，不再议）：
    感知(ctl_windows / ctl_screen / ctl_shot) → decide(snapshot) → 动作(ctl_focus / ctl_type / ctl_click)
    → 核验(回执 code 三值) → 记忆(episode.contract.Episode → add_episode；每 N 步 compact)

硬约束（工单原文落地）：
    · MCP 走 HTTP POST http://127.0.0.1:8761/mcp（stdio 不可用；URL/端口可用环境变量覆盖，默认 8761）
    · Episode 必须用 agentctl.memory.contract.Episode；outcome 用 verdict_from_code
    · 发图前必须过 agentctl.memory.harvest._win_sensitive；命中 ⇒ 不发图
    · 不接 LLM —— 决策只走 decide() 接口（v0 是规则；DSH 难点①留给未来实现注入）
    · 不碰 core.py / contract / 既有判据；不做 git / pip

真机 demo 用法（服务已在 8761 听着、有通行证+租约时）：
    python -X utf8 tools\\agent_loop.py --max-steps 3 --text "循环测试1"

自证（模拟假 MCP，不碰桌面）见 work\\_长任务_20261027\\自证_循环.py。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ★ 视觉兜底是本循环的内建能力：开关在 vision_assist 导入时读一次 ⇒ 必须在**任何**
#   agentctl.vision_* import 之前把 env 立好（缺省开启；显式关掉用 =0）。
os.environ.setdefault("AGENTCTL_VISION_ASSIST", "1")

from agentctl.memory import open_store  # noqa: E402
from agentctl.memory.compact import Compactor  # noqa: E402
from agentctl.memory.contract import Episode, make_session_id, verdict_from_code  # noqa: E402
from agentctl.memory.harvest import _win_sensitive  # noqa: E402
from agentctl.memory.retrieve import Retriever  # noqa: E402

DEFAULT_MCP_URL = "http://127.0.0.1:8761/mcp"
DEMO_TEXT = "循环测试"


def _now_iso() -> str:
    # make_session_id 解析的是无时区标记的本地时间形态（s-YYYYMMDD-HHMMSS）；
    # UTC 带时区在真机上会落到"昨天的会话桶"，这里统一本地时间。
    return datetime.now().isoformat(timespec="seconds")


def read_token() -> str:
    """令牌：环境变量优先，其次 <repo>/work/state/token（与 mcp_http 同口径）。"""
    tok = (os.environ.get("AGENTCTL_MCP_TOKEN") or "").strip()
    if tok:
        return tok
    for cand in (
        os.path.join(_REPO, "work", "state", "token"),
        os.path.join(_REPO, "state", "token"),
    ):
        try:
            if os.path.isfile(cand):
                with open(cand, encoding="utf-8") as f:
                    return f.read().strip()
        except OSError:
            continue
    return ""


class MCPClient:
    """JSON-RPC over Streamable HTTP：POST <url>，method=tools/call。

    返回**信封**（structuredContent），不是整个 JSON-RPC 包。
    失败不抛网络异常 —— 折成 {ok:False, code:-1, warning:...}，由上层按三值口径消化。
    """

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 60.0,
        actor: str = "agent-loop",
    ):
        self.url = (url or os.environ.get("AGENTCTL_MCP_URL") or DEFAULT_MCP_URL).strip()
        self.token = token if token is not None else read_token()
        self.timeout = float(timeout)
        self.actor = actor
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        if self.actor:
            req.add_header("X-AgentCtl-Actor", self.actor)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                detail = ""
            return {"ok": False, "code": -1, "warning": f"HTTP {e.code} {detail}"}
        except Exception as e:  # noqa: BLE001 —— 网络层异常统一折信封
            return {"ok": False, "code": -1, "warning": f"{type(e).__name__}: {e}"}

        # SSE 包装时 body 前缀是 "data: ..."
        text = raw.strip()
        if text.startswith("data:"):
            for line in text.splitlines():
                if line.strip().startswith("data:"):
                    text = line.strip()[5:].strip()
                    break
        try:
            msg = json.loads(text)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": -1, "warning": f"MCP 响应不是 JSON: {e}"}
        if not isinstance(msg, dict):
            return {"ok": False, "code": -1, "warning": "MCP 响应形状不对"}
        if "error" in msg and msg.get("error"):
            return {"ok": False, "code": -1, "warning": "MCP error: %s" % (msg.get("error"),)}
        result = msg.get("result") or {}
        sc = result.get("structuredContent")
        if isinstance(sc, dict):
            return sc
        # 回退：从 content[].text 再剥一层
        content = result.get("content") or []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                try:
                    inner = json.loads(part.get("text") or "{}")
                    if isinstance(inner, dict):
                        return inner
                except Exception:
                    continue
        return {"ok": False, "code": -1, "warning": "MCP result 里没有 structuredContent"}

    def call(self, name: str, **arguments: Any) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return self._post(payload)


# ── 敏感闸（发图前必过）─────────────────────────────────────────────────────

def image_allowed(win_exe: str, win_cls: str) -> tuple[bool, str]:
    """发图前敏感窗终判。

    ★ 语义（真机踩的坑 + 外部探针 02）：`_win_sensitive` 返回 (hit, why)——
    hit=True = 命中敏感名单 = 要拦；本函数返回"是否允许发图"⇒ not hit 才放行。
    ★ 外部探针 02：exe/cls **均不可得** ⇒ 无法证明该窗不敏感 ⇒ fail-closed 不发
    （"身份不明"不是"安全"的证据）。
    """
    if not (str(win_exe or "").strip() or str(win_cls or "").strip()):
        return False, "窗口身份不可得（exe/cls 均空）⇒ fail-closed 不发图"
    try:
        hit, why = _win_sensitive(str(win_exe or ""), str(win_cls or ""))
        return (not bool(hit)), (why or "")
    except Exception as e:  # noqa: BLE001
        return False, "敏感闸异常 fail-closed: %s: %s" % (type(e).__name__, e)


# ── 感知 ────────────────────────────────────────────────────────────────────

def perceive(client: MCPClient, query: str = "") -> dict:
    """取结构化快照：先列窗，再对候选窗做 ctl_screen（轻量）。截图走兜底且过敏感闸。"""
    snap: dict[str, Any] = {
        "t": _now_iso(),
        "windows": [],
        "gen": None,
        "screen": None,
        "shot": None,
        "sensitive_blocked": False,
        "sensitive_why": "",
    }
    w = client.call("ctl_windows", only_visible=True, limit=40)
    snap["windows_ok"] = bool(w.get("ok"))
    data = w.get("data") or {}
    snap["gen"] = data.get("gen")
    wins = data.get("windows") or []
    if isinstance(wins, list):
        snap["windows"] = wins
    # ★ 信封 windows 项缺 process/exe（敏感闸需要）——从 data.raw（JSON 字符串）补源
    try:
        raw_obj = json.loads(data.get("raw") or "{}")
        for rw in (raw_obj.get("窗口") or []):
            if not isinstance(rw, dict):
                continue
            for w2 in wins:
                if isinstance(w2, dict) and w2.get("hwnd") == rw.get("hwnd"):
                    w2.setdefault("process", rw.get("process"))
                    w2.setdefault("exe", rw.get("process"))
                    w2.setdefault("cls", rw.get("class"))
                    w2.setdefault("class", rw.get("class"))
                    break
    except Exception:
        pass

    target = _pick_window(wins)
    snap["target"] = target
    if not isinstance(target, dict) or target.get("hwnd") is None:
        return snap

    hwnd = int(target["hwnd"])
    scr = client.call("ctl_screen", hwnd=hwnd, query=query or "", top=60, cdp=False,
                      include_offscreen=False)
    if scr.get("ok"):
        d = scr.get("data") or {}
        # ★ 信封解包（外部独立核验探针 01）：真实回执的对象在 data.结果 里，
        #   只读 data.objects ⇒ UIA 对象永远为空（LLM 全盲）。两层都试。
        payload = d.get("结果") if isinstance(d.get("结果"), dict) else d
        snap["screen"] = {
            "screen_version": payload.get("screen_version"),
            "capture_id": d.get("capture_id") or payload.get("capture_id"),
            "objects": payload.get("objects") or [],
            "window": payload.get("window"),
        }

    # 视觉兜底触发（第 2 步口径修订）：不止"UIA 零可交互对象"——
    # UIA 有对象但**没有命中 query 语义**的，同样走本地视觉（真实场景：有对象但没我要的按钮）。
    # ★ 发图前敏感闸照旧（隐私铁律）：命中 ⇒ 一个字节都不发。
    exe = str(target.get("process") or target.get("exe") or "")
    cls = str(target.get("class") or target.get("cls") or "")
    ok_send, why = image_allowed(exe, cls)
    if not ok_send:
        snap["sensitive_blocked"] = True
        snap["sensitive_why"] = why
        return snap
    objs = (snap.get("screen") or {}).get("objects") or []
    n_inter = sum(1 for o in objs if isinstance(o, dict) and o.get("interactive"))
    q = (query or "").strip()
    sem_hit = False
    if q and objs:
        for o in objs:
            if isinstance(o, dict) and q.lower() in str(o.get("label") or "").lower():
                sem_hit = True
                break
    need_vision = (n_inter == 0) or (q and not sem_hit)
    if need_vision:
        shot = client.call("ctl_shot", hwnd=hwnd, why="agent_loop 视觉兜底", inline=False)
        if shot.get("ok"):
            d = shot.get("data") or {}
            # ★ 回执形状：文件路径在 data.raw.截图.文件（中文键）；data 里只有 capture_id
            shot_path, shot_origin = None, None
            try:
                raw_shot = (json.loads(d.get("raw") or "{}").get("截图") or {})
                shot_path = raw_shot.get("文件")
                area = raw_shot.get("区域")
                if isinstance(area, list) and len(area) >= 2:
                    shot_origin = [int(area[0]), int(area[1])]
            except Exception:
                pass
            if not shot_path:
                shot_path = d.get("path") or d.get("file")
            if shot_path:
                snap["shot"] = {
                    "path": shot_path,
                    "origin": shot_origin,        # 截图左上角的屏幕物理坐标（xy 换算用）
                    "capture_id": d.get("capture_id"),
                    "frame_id": d.get("frame_id"),
                    "exe": exe,
                    "cls": cls,
                    "sensitive_why": why,  # 已过闸的记录
                }
                # ★ 第 2 步接线：截图交给本地视觉后端（qwen2.5vl）做"看图定位"，
                #   把 UIA 盲区的候选变成可点对象（视觉 obj 与 UIA obj 同台竞争，LLM 选）。
                #   坐标口径：截图坐标 + 截图原点 = 屏幕物理坐标（ctl_click --xy 同口径）。
                vis = _vision_locate(snap["shot"], query or "", target)
                if vis:
                    snap["vision_hits"] = vis
    return snap


def _vision_locate(shot: dict, target_query: str, win: dict) -> list[dict]:
    """对兜底截图跑本地视觉定位（qwen2.5vl:7b）。失败安静返回 []（兜底不许炸主路）。"""
    path = str(shot.get("path") or "")
    if not path or not os.path.isfile(path):
        return []
    try:
        from agentctl.vision_backend_ollama import OllamaBackend
        from agentctl import vision_assist as _va
        with open(path, "rb") as f:
            png = f.read()
        be = OllamaBackend()
        r = _va.locate_safe(png, target_query or "主按钮", None,
                            backend=be, timeout_sec=getattr(be, "default_timeout_sec", 75.0))
        if not (r.get("ok") and r.get("found")):
            return []
        # 窗口原点：优先 shot 元数据（raw.截图.区域），退回 ctl_windows 的 rect
        wr = shot.get("origin") or (win.get("rect") if isinstance(win, dict) else None)
        ox, oy = (int(wr[0]), int(wr[1])) if isinstance(wr, list) and len(wr) >= 2 else (0, 0)
        out = []
        for c in (r.get("candidates") or [])[:3]:
            bx, by, bw, bh = c["bbox"]
            cx, cy = int(bx + bw / 2) + ox, int(by + bh / 2) + oy
            out.append({
                "eid": None,                       # 视觉候选没有 UIA eid —— 走 xy+from_capture
                "from_capture": shot.get("capture_id"),
                "xy": [cx, cy],                    # 屏幕物理坐标（与 ctl_click --xy 同口径）
                "bbox": [bx + ox, by + oy, bw, bh],
                "label": f"[视觉] {c.get('label') or target_query}",
                "vision_label": target_query,      # 保鲜重定位时按同一目标再找（探针 11）
                "origin": [ox, oy],                # 截图原点（重定位换算用）
                "role": "VisualCandidate",
                "interactive": True,
                "confidence": c.get("confidence"),
                "source": "vision",
            })
        return out
    except Exception as exc:  # noqa: BLE001 —— 兜底失败不许炸感知主路
        print(f"[vision] 兜底定位失败（安静降级）: {type(exc).__name__}: {exc}", flush=True)
        return []


def _pick_window(wins: list) -> Optional[dict]:
    """v0 规则修订（外部探针 14）：**不硬编码记事本**——前台优先，
    排除自家控制面（边框/面板），否则取第一个非最小化窗。"""
    if not isinstance(wins, list):
        return None
    cands = [w for w in wins
             if isinstance(w, dict) and not w.get("minimized")
             and w.get("hwnd") is not None]
    if not cands:
        return None
    for w in cands:
        blob = " ".join(str(w.get(k) or "") for k in ("title", "class", "process", "exe"))
        if w.get("is_foreground") or w.get("foreground"):
            if "AgentCtlCtrlBorder" not in blob and "agent-ctl" not in blob.lower():
                return w
    return cands[0]


# ── 决策（v0 规则；LLM 可注入）──────────────────────────────────────────────

def decide(snapshot: dict, state: Optional[dict] = None) -> Optional[dict]:
    """snapshot → 下一个动作。**这是 LLM 接口**：替换本函数即可接模型，不改循环体。

    返回动作 dict：
        {"tool": "ctl_focus"|"ctl_type"|"ctl_click"|"ctl_shot"|...,
         "kind": "focus"|"type"|"click"|...,   # episode.kind（contract.ACTION_KINDS）
         "args": {...},                        # 原样喂 MCP tools/call
         "target": "...",                      # 写进 episode.action.target
         "expect": "..."}                      # 核验期望（写进 verify.expect）
    返回 None = 循环收工。

    v0 规则（≥3 步演示）：focus → type"循环测试N" → click 文本区（用 screen 的 eid/uid 凭据）。
    """
    st = state if isinstance(state, dict) else {}
    step = int(st.get("step") or 0)
    typed = bool(st.get("typed"))
    clicked = bool(st.get("clicked"))
    target = snapshot.get("target")
    if not isinstance(target, dict) or target.get("hwnd") is None:
        return None
    hwnd = int(target["hwnd"])

    # 窗口身份绑定（外部探针 15 修订）：hwnd 变了 ⇒ **重置计划与完成态**——
    #   新窗口是新计划，旧窗的 typed/clicked 完成度不许继承（否则新窗秒收工）；
    #   真机预演的教训仍在：旧 hwnd 幽灵狂点 ⇒ code=5 洪水。
    plan_hwnd = st.get("plan_hwnd")
    if plan_hwnd is None:
        st["plan_hwnd"] = hwnd
    elif int(plan_hwnd) != hwnd:
        st["plan_hwnd"] = hwnd
        st["focused"] = False
        st["typed"] = False
        st["clicked"] = False
        st["last_result"] = ""
        st["last_type_ok"] = False
        st["last_args_text"] = ""

    if not st.get("focused"):
        return {
            "tool": "ctl_focus",
            "kind": "focus",
            "args": {"hwnd": hwnd},
            "target": "hwnd=%s" % hwnd,
            "expect": "窗口切到前台",
        }
    if not typed:
        text = str(st.get("text") or DEMO_TEXT) + str(step + 1)
        return {
            "tool": "ctl_type",
            "kind": "type",
            "args": {"hwnd": hwnd, "text": text, "mode": "focus", "clear": False},
            "target": "hwnd=%s text=%s" % (hwnd, text),
            "expect": "打出 %s" % text,
        }
    if not clicked:
        # 第 3 步：点回编辑区（凭据来自刚感知的 screen）
        scr = snapshot.get("screen") or {}
        objects = scr.get("objects") if isinstance(scr, dict) else None
        eid = None
        uid = None
        if isinstance(objects, list):
            for obj in objects:
                if isinstance(obj, dict) and obj.get("interactive") and obj.get("eid"):
                    eid = obj.get("eid")
                    uid = obj.get("uid")
                    break
        args: dict[str, Any] = {"hwnd": hwnd, "expect": "文本编辑"}
        if eid:
            args["eid"] = str(eid)
        cap = scr.get("capture_id") if isinstance(scr, dict) else None
        if cap and not eid:
            # 无 eid 时退回坐标：取对象 rect 中心
            if isinstance(objects, list):
                for obj in objects:
                    if isinstance(obj, dict) and obj.get("rect"):
                        r = obj["rect"]
                        if isinstance(r, list) and len(r) == 4:
                            args["xy"] = [int((r[0] + r[2]) // 2), int((r[1] + r[3]) // 2)]
                            args["from_capture"] = str(cap)
                            if obj.get("uid"):
                                args["expect_uid"] = str(obj.get("uid"))
                            break
        if uid and "expect_uid" not in args:
            args["expect_uid"] = str(uid)
        if "xy" not in args and "eid" not in args:
            # 没有可点目标 ⇒ 收工（不硬凑动作）
            return None
        return {
            "tool": "ctl_click",
            "kind": "click",
            "args": args,
            "target": "hwnd=%s eid=%s" % (hwnd, args.get("eid") or args.get("xy")),
            "expect": "点回文本编辑区",
        }
    return None


# ── 记忆 ────────────────────────────────────────────────────────────────────

def build_episode(
    snapshot: dict,
    action: dict,
    resp: dict,
    session_id: str,
    screen_version: Optional[int] = None,
    src_seq: Optional[int] = None,
) -> Episode:
    """回执 → Episode（contract 真源；outcome 三值只认 verdict_from_code）。"""
    ok = 1 if resp.get("ok") else 0
    code = resp.get("code")
    outcome, verdict = verdict_from_code(ok, code)
    # ★ 外部探针 27：code=9 = 闸门拒绝（denied）——outcome 记 **blocked**、
    #   核验判 **unobservable**（动作没发出去：不是 failed，也不能当成功）。
    if code == 9 and not ok:
        outcome, verdict = "blocked", "unobservable"
    win = snapshot.get("target") or {}
    exe = str(win.get("process") or win.get("exe") or "")
    cls = str(win.get("class") or win.get("cls") or "")
    # ★ 外部探针 24（隐私过滤双扩展）：①exe/cls **均缺失** ⇒ 身份不明，
    #   与命中名单同等待遇 fail-closed（文本一并滤除）；②过滤覆盖 **verify.expect**
    #   （"打出 XXX"里的敏感文本此前会从 expect 回流）。
    identity_known = bool(exe.strip() or cls.strip())
    try:
        sens_hit = (not identity_known) or bool(_win_sensitive(exe, cls)[0])
    except Exception:  # noqa: BLE001 —— 与 harvest 同源 fail-closed
        sens_hit = True
    if sens_hit:
        obs = "win_filtered=敏感窗已滤 hwnd=%s gen=%s" % (
            win.get("hwnd"), snapshot.get("gen"))
        args_out = dict(action.get("args") or {})
        for k in ("text", "keys"):
            if args_out.get(k):
                args_out[k] = "[已滤]"
        action_payload = {
            "tool": action.get("tool"),
            "target": "[已滤]",
            "args": args_out,
            "expect": "[已滤]",
        }
    else:
        obs = "win=%s|%s hwnd=%s gen=%s" % (
            exe or "?", cls or "?", win.get("hwnd"), snapshot.get("gen"))
        action_payload = {
            "tool": action.get("tool"),
            "target": action.get("target", ""),
            "args": action.get("args") or {},
            "expect": action.get("expect", ""),
        }
    result = "ok=%s code=%s" % (resp.get("ok"), code)
    if resp.get("warning"):
        result += " warning=%s" % str(resp.get("warning"))[:80]
    after = None
    data = resp.get("data") or {}
    if isinstance(data, dict):
        ad = data.get("after") or {}
        if isinstance(ad, dict) and ad.get("diff") is not None:
            after = ad.get("diff")
        elif "changed" in data:
            after = {"changed": data.get("changed")}
    if after is not None:
        result += " after=%s" % json.dumps(after, ensure_ascii=False)[:120]

    return Episode(
        t=_now_iso(),
        kind=str(action.get("kind") or action.get("tool") or "unknown"),
        outcome=outcome,
        obs=obs,
        action=action_payload,
        result=result,
        verify={
            "expect": str(action_payload.get("expect") or ""),
            "verdict": verdict,
            "cred": {"code": code, "ok": bool(resp.get("ok"))},
        },
        session_id=session_id,
        src_seq=src_seq,
        screen_version=screen_version,
    )


def remember(store: Any, ep: Episode, compact_every: int = 0, step_no: int = 0) -> int:
    ep_id = store.add_episode(ep)
    if compact_every and step_no > 0 and step_no % compact_every == 0:
        try:
            Compactor(store).compact()
        except Exception:
            pass  # 巩固失败不拖垮主循环；episode 已入库
    return int(ep_id)


def recall(store: Any, text: str, k: int = 5, session_id: str = "") -> list[dict]:
    """检索；给了 session_id ⇒ 先查本会话的 episode（新近记忆优先，防历史库淹没）。"""
    if session_id:
        try:
            con = getattr(store, "conn", None)
            if con is not None:
                rows = con.execute(
                    "SELECT * FROM episodes WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, k),
                ).fetchall()
                if rows:
                    out = []
                    for r in rows:
                        d = dict(r)
                        d.pop("embed", None)
                        d["source"] = "episode"
                        d["score"] = 1.0  # 本会话命中置顶展示
                        out.append(d)
                    return out
        except Exception:
            pass
    return list(Retriever(store).query(text, k=k).get("hits") or [])


# ── 循环体 ──────────────────────────────────────────────────────────────────

class AgentLoop:
    """最小闭环：perceive → decide → act → verify(code) → remember。

    动作限额：整个实例生命周期内 act 次数 ≤ max_actions（工单 ≤30）。
    """

    def __init__(
        self,
        client: MCPClient,
        store: Any,
        *,
        max_actions: int = 30,
        compact_every: int = 3,
        decide_fn: Optional[Callable[[dict, dict], Optional[dict]]] = None,
        query: str = "",
        text_prefix: str = DEMO_TEXT,
        log: Callable[[str], None] = None,
    ):
        self.client = client
        self.store = store
        self.max_actions = int(max_actions)
        self.compact_every = int(compact_every)
        self.decide_fn = decide_fn if decide_fn is not None else decide
        self.query = query
        self.text_prefix = text_prefix
        self.log = log if log is not None else (lambda s: print(s, flush=True))
        self.actions_used = 0
        self.episodes: list[Episode] = []
        self.state: dict[str, Any] = {
            "step": 0,
            "focused": False,
            "typed": False,
            "clicked": False,
            "text": text_prefix,
        }
        first_t = _now_iso()
        self.session_id = make_session_id(first_t)
        self.lease_held = False

    # ── 租约：动作被 code=10 拒过一次 ⇒ 尝试 acquire 一次再重放 ─────────────
    def _ensure_lease(self) -> bool:
        if self.lease_held:
            return True
        r = self.client.call("ctl_lease", action="acquire",
                             actor=self.client.actor, scopes=["B", "C"], ttl_sec=600)
        ok = bool(r.get("ok")) and r.get("code") == 0
        if ok:
            self.lease_held = True
            self.log("[lease] acquired (actor=%s)" % self.client.actor)
        else:
            self.log("[lease] acquire 失败: %s" % json.dumps(r, ensure_ascii=False)[:160])
        return ok

    def _act(self, action: dict) -> dict:
        tool = str(action.get("tool") or "")
        args = action.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        # ★ 外部探针 06：租约拿不到 ⇒ **不发动作**（对着闸门 hammer 只会刷出 code=10 洪水）。
        if tool in ("ctl_click", "ctl_type", "ctl_fill", "ctl_keys", "ctl_scroll",
                    "ctl_focus", "ctl_browser"):
            if not self._ensure_lease():
                self.actions_used += 1
                return {"ok": False, "code": 10,
                        "warning": "无租约且获取失败 ⇒ 动作未发出（探针 06 口径）"}
        # ★ 外部探针 18：保鲜+重定位的**每一步都要重过敏感闸**——重拍前重列窗口
        #   复核目标身份（窗口可能已换成敏感窗），命中 ⇒ 一个字节都不发。
        # ★ 外部探针 11：重定位在新帧里找同一目标，新 xy + 新 capture_id 配对；
        #   重定位失败 ⇒ 丢弃本次点击（宁可丢一步，不可点错）。
        if tool == "ctl_click" and args.get("vision_label") and args.get("hwnd"):
            hwnd_int = int(args["hwnd"])
            wins_now = self.client.call("ctl_windows", only_visible=True, limit=40)
            exe_now = cls_now = ""
            found_now = False
            for w2 in (wins_now.get("data") or {}).get("windows") or []:
                if isinstance(w2, dict) and int(w2.get("hwnd") or 0) == hwnd_int:
                    exe_now = str(w2.get("process") or w2.get("exe") or "")
                    cls_now = str(w2.get("class") or w2.get("cls") or "")
                    found_now = True
                    break
            ok_send, why_send = image_allowed(exe_now, cls_now)
            if (not ok_send) or (not found_now):
                self.actions_used += 1
                return {"ok": False, "code": 11,
                        "warning": "保鲜重检：目标窗敏感或已消失 ⇒ 拒发图拒点击"
                                   f"（{why_send or '窗口不在清单'}）"}
            try:
                fresh = self.client.call("ctl_shot", hwnd=hwnd_int,
                                         why="点击前凭据保鲜+重定位", inline=False)
                fd = fresh.get("data") or {}
                new_cap = fd.get("capture_id")
                shot_path = None
                try:
                    raw_shot = (json.loads(fd.get("raw") or "{}").get("截图") or {})
                    shot_path = raw_shot.get("文件")
                    area = raw_shot.get("区域")
                except Exception:
                    area = None
                relocated = None
                if new_cap and shot_path and os.path.isfile(shot_path):
                    from agentctl.vision_backend_ollama import OllamaBackend
                    from agentctl import vision_assist as _va
                    with open(shot_path, "rb") as f:
                        png = f.read()
                    rr = _va.locate_safe(png, str(args["vision_label"]), None,
                                         backend=OllamaBackend(), timeout_sec=75.0)
                    if rr.get("ok") and (rr.get("candidates") or []):
                        c0 = rr["candidates"][0]
                        bx, by, bw, bh = c0["bbox"]
                        origin = [int(area[0]), int(area[1])] if isinstance(area, list) and len(area) >= 2 else [0, 0]
                        relocated = [int(bx + bw / 2) + origin[0], int(by + bh / 2) + origin[1]]
                if relocated and new_cap:
                    args = dict(args, xy=relocated, from_capture=str(new_cap))
                    self.log("[vision] 重定位成功 xy=%s" % (relocated,))
                else:
                    self.log("[vision] 重定位失败 ⇒ 丢弃本次点击（不绑旧 xy 到新证据）")
                    self.actions_used += 1
                    return {"ok": False, "code": 12,
                            "warning": "凭据保鲜重定位失败 ⇒ 点击丢弃（探针 11 口径）"}
            except Exception as exc:  # noqa: BLE001
                self.log("[vision] 保鲜异常 ⇒ 丢弃本次点击: %s" % exc)
                self.actions_used += 1
                return {"ok": False, "code": 12,
                        "warning": "凭据保鲜异常 ⇒ 点击丢弃"}
        # 动作前置租约：没有租约的动作会被闸门 code=10 拒（真机预演踩过）
        if tool in ("ctl_click", "ctl_type", "ctl_fill", "ctl_keys", "ctl_scroll",
                    "ctl_focus", "ctl_browser"):
            if not self._ensure_lease():
                self.actions_used += 1
                return {"ok": False, "code": 10,
                        "warning": "无租约且获取失败 ⇒ 动作未发出"}
        self.actions_used += 1
        self.log(
            "[act#%d] %s %s"
            % (self.actions_used, tool, json.dumps(args, ensure_ascii=False)[:160])
        )
        resp = self.client.call(tool, **args)
        # ★ 外部探针 20：code=10 拒绝后**不得**重新拿租约再打一遍——
        #   闸门说不发就是不发；重试须由下一轮决策基于最新 state 另行判断。
        return resp

    def remember(self, ep: Episode, compact_every: int = 0, step_no: int = 0) -> int:
        """入库+定期巩固（实例方法：便于测试打补丁，外部探针 05/06 依赖）。"""
        return remember(self.store, ep, compact_every, step_no)

    def run(self, max_steps: int = 3) -> list[Episode]:
        """跑 ≤max_steps 步写动作循环；每步落一条 episode。

        ★ 徒劳熔断（外部探针 17）：同一动作（工具+参数全同）**连续失败**两次
          ⇒ 停止循环（等环境/用户干预），不许对着闸门 hammer。
        """
        steps = 0
        last_fail_sig = None
        last_settled = True
        while steps < int(max_steps) and self.actions_used < self.max_actions:
            snapshot = perceive(self.client, query=self.query)
            action = self.decide_fn(snapshot, self.state)
            if action is None:
                self.log("[done] decide 返回 None —— 收工")
                break
            sig = (str(action.get("tool") or "") + "|"
                   + json.dumps(action.get("args") or {},
                                ensure_ascii=False, sort_keys=True))
            if not last_settled and sig == last_fail_sig:
                self.log("[breaker] 同一动作连续失败 ⇒ 停止（探针 17 口径：终止或等待）")
                break
            resp = self._act(action)
            sv = None
            scr = snapshot.get("screen") or {}
            if isinstance(scr, dict):
                sv = scr.get("screen_version")
            if isinstance(sv, (int, float)):
                sv = int(sv)
            ep = build_episode(snapshot, action, resp, self.session_id, screen_version=sv)
            ep.validate()
            ep_id = self.remember(ep, self.compact_every, step_no=steps + 1)
            ep.id = ep_id
            self.episodes.append(ep)
            code = resp.get("code")
            ok = bool(resp.get("ok"))
            self.log(
                "[ep#%d] kind=%s outcome=%s code=%s ok=%s"
                % (ep_id, ep.kind, ep.outcome, code, ok)
            )
            # ★ 三值语义（外部探针 05 修正）：code=8「确认没变」只对 focus 等于
            #   「已在目标状态」；对 type/click，打了/点了画面却没变 = **没生效**，
            #   不许推进状态（把 8 当达成会让重复打字被标成成功）。
            settled = ok or (code == 8 and action.get("kind") == "focus")
            # ★ 给 LLM 决策核的反馈（防重复打字/盲目重试）：上一步工具+三值结果
            self.state["last_result"] = f"{action.get('tool')} code={code} ok={ok}"
            self.state["last_type_ok"] = bool(action.get("kind") == "type" and settled)
            self.state["last_args_text"] = str((action.get("args") or {}).get("text") or "")
            # ★ Kimi 独立复核 P1-3（2026-10-27，主控代改署名）：focus 不算完成证据——
            #   focus 的 code=8 只说明「已在目标状态」，计入 any_success 会让模型
            #   只 focus 一下就能 done 空跑收工（负例：任务「输入abc」未输入即收工）。
            #   完成证据 = 内容动作（type/click/fill/keys/scroll/browser）至少一次 settled。
            if settled and action.get("kind") != "focus":
                self.state["any_success"] = True
            last_settled = settled
            last_fail_sig = None if settled else sig
            if action.get("kind") == "focus" and settled:
                self.state["focused"] = True
            if action.get("kind") == "type" and settled:
                self.state["typed"] = True
            if action.get("kind") == "click" and settled:
                self.state["clicked"] = True
            self.state["step"] = int(self.state.get("step") or 0) + 1
            steps += 1
            time.sleep(0.05)
        return self.episodes


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="感知→动作→记忆 循环 demo v0")
    ap.add_argument("--mcp-url", default=None, help="默认 %s（或 AGENTCTL_MCP_URL）" % DEFAULT_MCP_URL)
    ap.add_argument("--max-steps", type=int, default=3)
    ap.add_argument("--max-actions", type=int, default=30)
    ap.add_argument("--compact-every", type=int, default=3)
    ap.add_argument("--text", default=DEMO_TEXT, help="打字前缀（工单：固定循环测试+步号）")
    ap.add_argument("--query", default="", help="感知 query 关键词")
    ap.add_argument("--db", default=None, help="episode 库路径（默认 work/memory/episodes.db）")
    ap.add_argument("--recall", default="", help="跑完后用该词 retrieve 一遍")
    ap.add_argument("--task", default="", help="自然语言任务（给了 ⇒ 自动启用 LLM 决策）")
    ap.add_argument("--llm", action="store_true", help="启用 LLM 决策核（本地 Ollama qwen2.5:7b）")
    a = ap.parse_args(argv)

    decide_fn = None
    if a.task or a.llm:
        from llm_decide import make_llm_decide
        decide_fn = make_llm_decide(task=a.task or DEMO_TEXT)
        print("[decide] LLM 决策核已装（本地 qwen2.5:7b；坏输出自动兜底规则决策）", flush=True)

    client = MCPClient(url=a.mcp_url)
    store = open_store(a.db)
    try:
        loop = AgentLoop(
            client,
            store,
            max_actions=a.max_actions,
            compact_every=a.compact_every,
            query=a.query,
            text_prefix=a.text,
            decide_fn=decide_fn,
        )
        print("[mcp] url=%s" % client.url, flush=True)
        print("[mem] db=%s session=%s" % (store.db_path, loop.session_id), flush=True)
        eps = loop.run(max_steps=a.max_steps)
        print(
            "[sum] steps=%d actions=%d episodes=%d"
            % (len(eps), loop.actions_used, len(eps)),
            flush=True,
        )
        q = a.recall or (a.text or DEMO_TEXT)
        hits = recall(store, q, k=5, session_id=loop.session_id)
        print("[recall] query=%r hits=%d" % (q, len(hits)), flush=True)
        for h in hits[:5]:
            print(
                "  · id=%s outcome=%s score=%.3f text=%s"
                % (
                    h.get("id"),
                    h.get("outcome"),
                    float(h.get("score") or 0.0),
                    str(h.get("text") or "")[:80],
                ),
                flush=True,
            )
        return 0 if eps else 1
    finally:
        try:
            store.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
