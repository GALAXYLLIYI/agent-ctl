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
    exclude_ids: Optional[list[int]] = None,
) -> dict:
    """结构化召回。返回 {"recalled", "n", "hits", "block", "subject"}；
    失败时 recalled=False（附 error），绝不抛异常。
    exclude_ids（DSH 验收 P2-2）：调用方排除最近已出示的 episode id——
    真库 95% 语料是同一观测的循环写回，不去重的话义务式召回每次都是同一条。"""
    try:
        from agentctl.memory.retrieve import Retriever

        excl = {int(x) for x in (exclude_ids or [])}
        res = Retriever(store).query(text, k=max(int(k) * 4, 12), subject=subject)
        hits: list[dict] = []
        seen_obs: set[str] = set()
        excluded_top: dict | None = None          # 被排除命中里的最高分（饥饿豁免用）
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
            # DSH 验收 P2-3 修正：去重键带 outcome——同一观测先 failed 后 success
            # 是两行独立信息（intake 去重键同口径），展示层不许把兄弟结局并掉
            key = f"{obs[:_OBS_KEY_CHARS]}\x1f{h.get('outcome', '')}"
            if key in seen_obs:
                continue
            is_excluded = bool(excl and h.get("id") is not None
                               and int(h["id"]) in excl)
            item = {
                "score": round(score, 3),
                "outcome": h.get("outcome", ""),
                "subject": h_subject,
                "obs": obs[:160],
                "result": str(h.get("result") or "")[:120],
                "session_id": h.get("session_id", ""),
                "t": h.get("t", ""),
                "id": h.get("id"),
                # v5：被验证过多少次 + 谁写的（机器仓内跨宿主可见，这正是扩展的目的）
                "recurrences": int(h.get("recurrences") or 1),
                "actor": h.get("actor") or contract.ACTOR_UNKNOWN,
            }
            if is_excluded:
                # 排除 ≠ 湮没：低多样性语料里把唯一相关经验长期压死 = 义务式召回
                # 被自己的去重饿死（A/B 分析 v5·B 时代 116/128 轮零召回的根因）。
                # 豁免规则：被排除命中中分数最高的一条始终放行，标记 shown_before。
                if excluded_top is None or score > float(excluded_top["score"]):
                    excluded_top = item
                continue
            seen_obs.add(key)
            hits.append(item)
            if len(hits) >= int(k):
                break
        if not hits and excluded_top is not None:
            excluded_top["shown_before"] = True
            hits.append(excluded_top)
        lines = [f"[记忆召回 {len(hits)} 条｜subject={subject}｜地板={floor}]"]
        for x in hits:
            times = f" ×{x['recurrences']}" if x["recurrences"] > 1 else ""
            lines.append(
                f"  ({x['score']:.3f}/{x['outcome']}{times}) [{x['actor']}] {x['obs']} "
                f"⇒ {x['result']} [{x['session_id']}]"
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
