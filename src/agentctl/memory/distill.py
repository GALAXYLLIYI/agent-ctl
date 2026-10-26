# -*- coding: utf-8 -*-
r"""agentctl.memory.distill —— workflow 经验提炼 + last_verified 降权依据（记忆层刀 · 2026-10-26）

口径（总单 §八）：
    distill_workflows(store, min_success_ratio=0.8, min_actions=3) -> dict
      按 session_id 聚 episode；动作数 ≥ min_actions 且 success 占比 ≥ 阈值的会话才提炼。
      target_sig：normalize 后把数字/坐标/eid 归一为 #（re.sub(r'\d+', '#', …)）。
      signature = '→'.join(f"{kind}:{target_sig}")；
      workflows 表 upsert（n_seen+1、success_ratio 滚动、last_verified 取 max(t)、example_session）。
      返回 {"sessions_seen", "workflows_upserted", "stale": [最后验证超 7 天的 signature]}。

只依赖 contract 与 store 鸭型接口；不 import store.py。
workflows 写路径鸭型：store.upsert_workflow(...)（FakeStore / 主控集成时对齐）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from agentctl.memory import contract

__all__ = ["distill_workflows", "target_sig", "session_signature"]

_STALE_DAYS = 7


def _parse_t(t: str) -> Optional[datetime]:
    s = (t or "").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _as_dict(action: Any) -> dict:
    if isinstance(action, dict):
        return action
    if isinstance(action, str) and action:
        try:
            obj = json.loads(action)
        except Exception:
            return {}
        return obj if isinstance(obj, dict) else {}
    return {}


def target_sig(action: Any) -> str:
    """动作 target 归一：normalize 后数字串 → #（坐标/eid/数字参数同签名）。"""
    target = str(_as_dict(action).get("target", "") or "")
    norm = contract.normalize_text(target)
    return re.sub(r"\d+", "#", norm)


def session_signature(rows: list[dict]) -> str:
    """会话动作序列 → signature；rows 需已按 t/id 升序。"""
    parts = [f"{r.get('kind', '')}:{target_sig(r.get('action'))}" for r in rows]
    return "→".join(parts)


def _success_ratio(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    ok = sum(1 for r in rows if str(r.get("outcome") or "") == contract.OUTCOME_SUCCESS)
    return ok / len(rows)


def _stale_signatures(store: Any, now: Optional[datetime] = None) -> list[str]:
    fn = getattr(store, "iter_workflows", None)
    if not callable(fn):
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=_STALE_DAYS)
    out: list[str] = []
    try:
        workflows = [dict(w) for w in fn()]
    except Exception:
        return []
    for wf in workflows:
        last = _parse_t(str(wf.get("last_verified") or ""))
        if last is None:
            out.append(str(wf.get("signature") or ""))
            continue
        # naive vs aware：两边都转 UTC 或去掉 tzinfo 后比较
        if last.tzinfo is None:
            last_c = last.replace(tzinfo=timezone.utc)
        else:
            last_c = last.astimezone(timezone.utc)
        if last_c < cutoff:
            out.append(str(wf.get("signature") or ""))
    return out


def _upsert_workflow(store: Any, **fields: Any) -> None:
    fn = getattr(store, "upsert_workflow", None)
    if not callable(fn):
        raise RuntimeError(
            "store 缺少鸭型方法 upsert_workflow(...)；请主控集成时对齐 workflows 表写路径"
        )
    fn(**fields)


def distill_workflows(
    store: Any,
    min_success_ratio: float = 0.8,
    min_actions: int = 3,
) -> dict:
    by_session: dict[str, list[dict]] = {}
    for row in store.iter_episode_rows(since_id=0):
        r = dict(row)
        sid = str(r.get("session_id") or "")
        by_session.setdefault(sid, []).append(r)

    sessions_seen = 0
    workflows_upserted = 0

    for sid, rows in by_session.items():
        rows.sort(key=lambda r: (str(r.get("t") or ""), int(r.get("id") or 0)))
        # blocked/unobservable 不计入「动作成功」分子，但计入动作数分母（可观测结局）
        n = len(rows)
        if n < max(1, int(min_actions)):
            continue
        sessions_seen += 1
        ratio = _success_ratio(rows)
        if ratio < float(min_success_ratio):
            continue

        sig = session_signature(rows)
        if not sig:
            continue
        last_t = str(rows[-1].get("t") or "")
        name = contract.normalize_text(
            " ".join(str(r.get("kind") or "") for r in rows[:3])
        )[:80] or "workflow"
        _upsert_workflow(
            store,
            name=name,
            signature=sig,
            steps=[str(r.get("kind") or "") for r in rows],
            success_ratio=ratio,
            last_verified=last_t,
            example_session=sid,
        )
        workflows_upserted += 1

    stale = _stale_signatures(store)
    return {
        "sessions_seen": sessions_seen,
        "workflows_upserted": workflows_upserted,
        "stale": stale,
    }
