# -*- coding: utf-8 -*-
r"""agentctl.memory.compact —— episode 巩固：promote + merge（记忆层刀 · 2026-10-26）

口径（总单 §六；阈值/常量只认 contract）：
    promote()  未入段 episodes（id > meta['promoted_until']）按 t 序成段；
               段边界任一触发：
                 ① 与前条时间差 > SEG_GAP_SEC；
                 ② 距上个强制锚点 ≥ ANCHOR_N 条（锚点=强制段边界）；
                 ③ 与段内已累积摘要相似度 < SPLIT_SIM（话题切分）。
               summary = 段内 episode.text 顺序拼接，超 SUMMARY_MAX_CHARS 截断。
    merge()    自 level 0 向上，相邻存活段两两算摘要相似度；
               > MERGE_SIM 且合并后 n_ep ≤ ANCHOR_N ⇒ 建 level+1 父段，
               子段 merged_into=父 id。一轮只做一层，层间迭代直到无合并。
    compact()  = promote() + merge()，幂等（重复调用不产生重复段/合并）。

只依赖 contract 与 store 鸭型接口；不 import store.py（并行开发，集成由主控做）。
相似度一律：向量 L2 归一后点积。summary 文本 embed 走
store.embedder.embed(contract.normalize_text(...))。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

from agentctl.memory import contract

__all__ = ["Compactor", "cosine_l2"]

_ZERO = 1e-12


def _l2_normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    if n < _ZERO:
        return [0.0] * len(vec)
    return [x / n for x in vec]


def cosine_l2(a: Optional[list[float]], b: Optional[list[float]]) -> float:
    """L2 归一后点积；空/零范数 ⇒ 0。"""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    na = _l2_normalize(list(a[:n]))
    nb = _l2_normalize(list(b[:n]))
    return sum(x * y for x, y in zip(na, nb))


def _parse_t(t: str) -> Optional[datetime]:
    s = (t or "").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _gap_sec(t1: str, t2: str) -> float:
    a, b = _parse_t(t1), _parse_t(t2)
    if a is None or b is None:
        return 0.0
    return abs((b - a).total_seconds())


def _row_vec(row: dict) -> list[float]:
    blob = row.get("embed")
    if not blob:
        return []
    try:
        return contract.unpack_vec(bytes(blob))
    except Exception:
        return []


def _action_target(action: Any) -> str:
    if isinstance(action, dict):
        return str(action.get("target", "") or "")
    if isinstance(action, str) and action and action != "{}":
        try:
            import json as _json
            obj = _json.loads(action)
        except Exception:
            return ""
        if isinstance(obj, dict):
            return str(obj.get("target", "") or "")
    return ""


class Compactor:
    """巩固器：promote（粗段）+ merge（层级合并）。store 为鸭型接口。"""

    def __init__(self, store: Any):
        self.store = store

    # ── promote ─────────────────────────────────────────────────────────────

    def promote(self) -> dict:
        raw = self.store.get_meta("promoted_until", "0") or "0"
        try:
            until_id = int(raw)
        except (TypeError, ValueError):
            until_id = 0

        rows = [dict(r) for r in self.store.iter_episode_rows(since_id=until_id)]
        rows.sort(key=lambda r: (str(r.get("t") or ""), int(r.get("id") or 0)))
        if not rows:
            return {"segments_created": 0, "episodes": 0, "promoted_until": until_id}

        # 当前段缓冲：行列表 + 已累积 summary 文本
        buf_rows: list[dict] = []
        buf_text: list[str] = []
        n_segments = 0

        def flush() -> None:
            nonlocal n_segments
            if not buf_rows:
                return
            summary = "\n".join(buf_text)[: contract.SUMMARY_MAX_CHARS]
            self.store.add_segment(
                level=0,
                t_start=str(buf_rows[0].get("t") or ""),
                t_end=str(buf_rows[-1].get("t") or ""),
                first_ep=int(buf_rows[0]["id"]),
                last_ep=int(buf_rows[-1]["id"]),
                summary=summary,
                n_ep=len(buf_rows),
            )
            n_segments += 1

        for row in rows:
            text = str(row.get("text") or "")
            if not buf_rows:
                buf_rows.append(row)
                buf_text.append(text)
                continue

            force = False
            prev = buf_rows[-1]
            # ① 时间断裂
            if _gap_sec(str(prev.get("t") or ""), str(row.get("t") or "")) > contract.SEG_GAP_SEC:
                force = True
            # ② 锚点：距上个强制边界已累积 ≥ ANCHOR_N 条
            elif len(buf_rows) >= contract.ANCHOR_N:
                force = True
            # ③ 话题切分：与段内已累积摘要相似度 < SPLIT_SIM
            else:
                acc = self.store.embedder.embed(contract.normalize_text("\n".join(buf_text)))
                ep_vec = _row_vec(row)
                if not ep_vec:
                    ep_vec = self.store.embedder.embed(contract.normalize_text(text))
                if cosine_l2(ep_vec, acc) < contract.SPLIT_SIM:
                    force = True

            if force:
                flush()
                buf_rows = [row]
                buf_text = [text]
            else:
                buf_rows.append(row)
                buf_text.append(text)

        flush()

        max_id = max(int(r["id"]) for r in rows)
        self.store.set_meta("promoted_until", str(max_id))
        return {
            "segments_created": n_segments,
            "episodes": len(rows),
            "promoted_until": max_id,
        }

    # ── merge ───────────────────────────────────────────────────────────────

    def _merge_level(self, level: int) -> tuple[int, int]:
        """一层贪心合并相邻存活段；返回 (父段数, 被合并子段数)。"""
        segs = [
            dict(s)
            for s in self.store.iter_segments(live_only=True, level=level)
        ]
        segs.sort(key=lambda s: (int(s.get("first_ep") or 0), int(s.get("id") or 0)))
        if len(segs) < 2:
            return 0, 0

        parents = 0
        children = 0
        i = 0
        n = len(segs)
        while i + 1 < n:
            a, b = segs[i], segs[i + 1]
            if a.get("merged_into") or b.get("merged_into"):
                i += 1
                continue
            n_ep = int(a.get("n_ep") or 0) + int(b.get("n_ep") or 0)
            if n_ep > contract.ANCHOR_N:
                i += 1
                continue

            va = _row_vec(a)
            if not va:
                va = self.store.embedder.embed(
                    contract.normalize_text(str(a.get("summary") or ""))
                )
            vb = _row_vec(b)
            if not vb:
                vb = self.store.embedder.embed(
                    contract.normalize_text(str(b.get("summary") or ""))
                )
            if cosine_l2(va, vb) > contract.MERGE_SIM:
                summary = (
                    str(a.get("summary") or "") + "\n" + str(b.get("summary") or "")
                )[: contract.SUMMARY_MAX_CHARS]
                parent_id = self.store.add_segment(
                    level=level + 1,
                    t_start=str(a.get("t_start") or ""),
                    t_end=str(b.get("t_end") or ""),
                    first_ep=int(a.get("first_ep") or 0),
                    last_ep=int(b.get("last_ep") or 0),
                    summary=summary,
                    n_ep=n_ep,
                )
                self.store.mark_merged(
                    [int(a["id"]), int(b["id"])], int(parent_id)
                )
                parents += 1
                children += 2
                i += 2
            else:
                i += 1
        return parents, children

    def merge(self) -> dict:
        total_parents = 0
        total_children = 0
        # 层间迭代直到无合并；一轮内自低到高各扫一层
        while True:
            levels = sorted(
                {
                    int(s.get("level") or 0)
                    for s in self.store.iter_segments(live_only=True)
                }
            )
            round_parents = 0
            round_children = 0
            for level in levels:
                p, c = self._merge_level(level)
                round_parents += p
                round_children += c
            total_parents += round_parents
            total_children += round_children
            if round_parents == 0:
                break
        return {
            "parents_created": total_parents,
            "children_marked": total_children,
        }

    # ── compact ─────────────────────────────────────────────────────────────

    def compact(self) -> dict:
        pr = self.promote()
        mr = self.merge()
        return {
            "promote": pr,
            "merge": mr,
            "segments_created": int(pr.get("segments_created") or 0),
            "parents_created": int(mr.get("parents_created") or 0),
            "episodes": int(pr.get("episodes") or 0),
        }
