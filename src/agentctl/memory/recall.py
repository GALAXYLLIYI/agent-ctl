# -*- coding: utf-8 -*-
r"""agentctl.memory.recall —— 结构化召回（记忆层 v0 · 2026-10-29）

理论位置（用户拍板的 harness 设计律）：
    「先查记忆」不进 prompt（号召=建议），进结构（义务）。
    依据：Tulving 编码特异性——提取失败多是线索不在场，线索供给是环境设计；
    DSH P0-2 实证——行为层习惯可被微调覆盖，结构层不可覆盖（gate 同款理由）。

本模块把「带分仓、带地板、带去重、带出处、三值结局不漂白」的召回做成**一次调用**，
服务面经 ctl_memory action=recall 暴露给 MCP/HTTP/CLI 三宿主（此前只存在于
量化侧的 recall_hook.py，v3 落地后升级为引擎口径）。

口径：
    - subject 分仓（schema v3）：默认只出 agent 仓——人的桌面情景记忆不进代理决策上下文
    - 分数地板 SCORE_FLOOR：滤掉零分伪影命中（E4b 实测：零分=排序噪声）
    - 去重：同一结论的多轮复制品只出示一次
    - 出处强制：每条命中带 session_id/t/outcome（E2：无出处不出示）
    - 三值结局原样展示：unobservable 不漂白（E1）
    - 召回失败不拦业务：任何异常吞掉并标记 recalled=False（召回是增益不是依赖）
"""
from __future__ import annotations

from typing import Any, Optional

from agentctl.memory import contract

SCORE_FLOOR = 0.10
DEFAULT_SUBJECT = contract.SUBJECT_AGENT
_OBS_KEY_CHARS = 48


def recall(
    store: Any,
    text: str,
    k: int = 3,
    subject: Optional[str] = DEFAULT_SUBJECT,
    floor: float = SCORE_FLOOR,
) -> dict:
    """结构化召回。返回 {"recalled", "n", "hits", "block", "subject"}；
    失败时 recalled=False（附 error），绝不抛异常。"""
    try:
        from agentctl.memory.retrieve import Retriever

        res = Retriever(store).query(text, k=max(int(k) * 4, 12), subject=subject)
        hits: list[dict] = []
        seen_obs: set[str] = set()
        for h in res.get("hits", []):
            if h.get("source") != "episode":
                continue
            score = float(h.get("score") or 0.0)
            if score <= floor:
                continue
            h_subject = h.get("subject") or contract.SUBJECT_AGENT
            if subject and h_subject != subject:
                continue
            obs = str(h.get("obs") or "")
            key = obs[:_OBS_KEY_CHARS]
            if key in seen_obs:
                continue
            seen_obs.add(key)
            hits.append({
                "score": round(score, 3),
                "outcome": h.get("outcome", ""),
                "subject": h_subject,
                "obs": obs[:160],
                "result": str(h.get("result") or "")[:120],
                "session_id": h.get("session_id", ""),
                "t": h.get("t", ""),
                "id": h.get("id"),
            })
            if len(hits) >= int(k):
                break
        lines = [f"[记忆召回 {len(hits)} 条｜subject={subject}｜地板={floor}]"]
        for x in hits:
            lines.append(
                f"  ({x['score']:.3f}/{x['outcome']}) {x['obs']} ⇒ {x['result']} "
                f"[{x['session_id']}]"
            )
        return {
            "recalled": bool(hits),
            "n": len(hits),
            "hits": hits,
            "block": "\n".join(lines),
            "subject": subject,
        }
    except Exception as exc:  # noqa: BLE001 —— 召回失败不拦业务
        return {"recalled": False, "n": 0, "hits": [], "block": "",
                "subject": subject, "error": repr(exc)}
