# -*- coding: utf-8 -*-
r"""agentctl.memory.retrieve —— 记忆检索：粗下钻 + 混合排序（记忆层刀 · 2026-10-26）

口径（总单 §七）：
    Retriever(store).query(text, k=5) -> dict
      粗：全部存活段按 level 从高到低算摘要余弦，取前 3 段 ⇒
          其 episode id 区间为候选集；候选不足 k ⇒ 放开为全库。
      细：score = 0.7 * cos(query_vec, ep_vec) + 0.3 * keyword_fraction，降序取 k。
      返回 {"query", "hits": [{source: episode|segment-summary, ...}], "workflows": [...]}。
          segment-summary 命中也要返回（带出处：区分「我记得」和「我记得个大概」）。
      workflows：查询词与 workflow.name/signature 的关键词命中，最多 3 条。

只依赖 contract 与 store 鸭型接口；不 import store.py。
相似度一律：向量 L2 归一后点积。
"""
from __future__ import annotations

from typing import Any

from agentctl.memory import contract
from agentctl.memory.compact import _row_vec, cosine_l2

__all__ = ["Retriever"]


def _normalize_row(row: dict) -> dict:
    """检索结果不带 embed BLOB（便于上层 JSON 序列化）。"""
    out = dict(row)
    out.pop("embed", None)
    return out


def _iter_workflows(store: Any) -> list[dict]:
    """鸭型：store.iter_workflows() -> Iterable[dict]；缺失则空表。"""
    fn = getattr(store, "iter_workflows", None)
    if not callable(fn):
        return []
    try:
        return [dict(w) for w in fn()]
    except Exception:
        return []


class Retriever:
    """检索器：粗段下钻 → 细粒度混合排序；附 segment-summary 与 workflow 命中。"""

    def __init__(self, store: Any):
        self.store = store

    def _seg_vec(self, seg: dict) -> list[float]:
        vec = _row_vec(seg)
        if vec:
            return vec
        summary = contract.normalize_text(str(seg.get("summary") or ""))
        return self.store.embedder.embed(summary)

    def _coarse_candidates(self, qvec: list[float], k: int) -> list[int] | None:
        """存活段按 level 降序 + 摘要余弦取前 3；返回候选 episode id 列表或 None（全库）。"""
        live = [dict(s) for s in self.store.iter_segments(live_only=True)]
        if not live:
            return None
        live.sort(key=lambda s: int(s.get("level") or 0), reverse=True)
        scored: list[tuple[float, dict]] = []
        for seg in live:
            sim = cosine_l2(qvec, self._seg_vec(seg))
            scored.append((sim, seg))
        scored.sort(key=lambda p: (-p[0], int(p[1].get("id") or 0)))
        top = [seg for _, seg in scored[:3]]

        cand: list[int] = []
        for seg in top:
            lo = int(seg.get("first_ep") or 0)
            hi = int(seg.get("last_ep") or 0)
            cand.extend(range(lo, hi + 1))
        if len(cand) < k:
            return None
        return cand

    def _fine_hits(
        self, qtext: str, qvec: list[float], k: int, candidate_ids: list[int] | None
    ) -> list[dict]:
        pool = max(k * 4, 16)
        cos_hits = self.store.search_episodes(
            qvec, k=pool, candidate_ids=candidate_ids
        )
        kw_hits = self.store.search_keyword(
            qtext, k=pool, candidate_ids=candidate_ids
        )
        cos_map = {int(row["id"]): float(score) for row, score in cos_hits}
        kw_map = {int(row["id"]): float(score) for row, score in kw_hits}
        row_map: dict[int, dict] = {}
        for row, _ in cos_hits:
            row_map[int(row["id"])] = row
        for row, _ in kw_hits:
            row_map.setdefault(int(row["id"]), row)

        scored: list[dict] = []
        for ep_id, row in row_map.items():
            score = 0.7 * cos_map.get(ep_id, 0.0) + 0.3 * kw_map.get(ep_id, 0.0)
            item = _normalize_row(row)
            item["source"] = "episode"
            item["score"] = score
            scored.append(item)
        scored.sort(key=lambda h: (-float(h["score"]), int(h.get("id") or 0)))
        return scored[:k]

    def _segment_hits(self, qvec: list[float]) -> list[dict]:
        live = [dict(s) for s in self.store.iter_segments(live_only=True)]
        scored: list[tuple[float, dict]] = []
        for seg in live:
            sim = cosine_l2(qvec, self._seg_vec(seg))
            scored.append((sim, seg))
        scored.sort(key=lambda p: (-p[0], int(p[1].get("id") or 0)))
        out: list[dict] = []
        for sim, seg in scored[:3]:
            item = _normalize_row(seg)
            item["source"] = "segment-summary"
            item["score"] = sim
            item["segment_id"] = seg.get("id")
            out.append(item)
        return out

    def _workflow_hits(self, qtext: str) -> list[dict]:
        q_tokens = set(contract.normalize_text(qtext).split())
        if not q_tokens:
            return []
        hits: list[dict] = []
        for wf in _iter_workflows(self.store):
            name = contract.normalize_text(str(wf.get("name") or ""))
            sig = contract.normalize_text(str(wf.get("signature") or ""))
            tokens = set((name + " " + sig).split())
            # 子串命中：查询词出现在 name/signature 即算
            name_sig = name + " " + sig
            hit = bool(tokens & q_tokens) or any(
                tok and tok in name_sig for tok in q_tokens
            )
            if hit:
                item = _normalize_row(wf)
                item.pop("embed", None)
                hits.append(item)
            if len(hits) >= 3:
                break
        return hits

    def query(self, text: str, k: int = 5) -> dict:
        qtext = contract.normalize_text(text or "")
        qvec = self.store.embedder.embed(qtext)

        candidate_ids: list[int] | None = None
        if qvec and any(abs(x) > 1e-12 for x in qvec):
            candidate_ids = self._coarse_candidates(qvec, k)

        fine = self._fine_hits(qtext, qvec, k, candidate_ids) if qvec else []
        segs = self._segment_hits(qvec) if qvec else []
        wfs = self._workflow_hits(qtext)

        # segment-summary 置前（粗粒度出处），再接细粒度 episode
        hits = segs + fine
        return {
            "query": text or "",
            "hits": hits,
            "workflows": wfs,
            "candidate_ids": candidate_ids,
            "k": k,
        }
