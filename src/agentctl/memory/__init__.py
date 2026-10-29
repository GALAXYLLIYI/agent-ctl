# -*- coding: utf-8 -*-
r"""agentctl.memory —— 记忆层 v0（episode 库：收集·巩固·检索·提炼）

公开口径（记忆层总单 2026-10-26）：
    · Episode / 三值结局 / schema / 阈值常量 → contract（唯一契约真源）
    · TimelineHarvester → timeline.db 只读提炼（同一原始事件流，两个索引）
    · MemoryStore（deferred import，存于 store.py）→ sqlite + 向量
    · Compactor / Retriever / distill_workflows（deferred import）
入口：python -m agentctl.memory {harvest|compact|query|stats}
铁律：纯本地、拉取式、无守护进程；模型只决策，记忆层不碰桌面。
"""
from __future__ import annotations

import os
from typing import Optional

from agentctl.memory import contract
from agentctl.memory.contract import (  # noqa: F401 —— 再导出=对外口径
    ANCHOR_N,
    EMBED_DIM,
    MERGE_SIM,
    SEG_GAP_SEC,
    SPLIT_SIM,
    SUMMARY_MAX_CHARS,
    Episode,
    VALID_OUTCOMES,
    verdict_from_code,
)

MEMORY_VERSION = contract.MEMORY_VERSION

__all__ = [
    "MEMORY_VERSION",
    "Episode",
    "VALID_OUTCOMES",
    "verdict_from_code",
    "EMBED_DIM",
    "MERGE_SIM",
    "SPLIT_SIM",
    "ANCHOR_N",
    "SEG_GAP_SEC",
    "SUMMARY_MAX_CHARS",
    "default_db_path",
    "open_store",
]


def repo_root() -> str:
    # src/agentctl/memory/__init__.py → 向上三级 = 仓根
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )


def default_db_path() -> str:
    """episode 库默认落 <仓根>/work/memory/episodes.db（AGENTCTL_MEMORY_DB 可覆盖）。"""
    return os.environ.get(
        "AGENTCTL_MEMORY_DB", os.path.join(repo_root(), "work", "memory", "episodes.db")
    )


def open_store(db_path: Optional[str] = None, **kwargs):
    """延迟导入的便捷工厂（避免包导入期强依赖并行开发中的 store.py）。
    v6：按库 meta['embedder'] 自动匹配嵌入器——库用什么编码，连接就用什么编码，
    杜绝「库是 bge、连接拿哈希写」的口径混写。新库（无 meta）用哈希（轻量默认，
    轻量路径不依赖模型文件）。"""
    if db_path is None:
        db_path = default_db_path()
    from agentctl.memory.store import MemoryStore

    if "embedder" not in kwargs:
        import sqlite3 as _sq
        name = None
        try:
            con = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT v FROM meta WHERE k='embedder'").fetchone()
                name = row[0] if row else None
            finally:
                con.close()
        except Exception:
            name = None
        if name and "bge" in str(name):
            from agentctl.memory.embedder_bge import BgeEmbedder
            kwargs["embedder"] = BgeEmbedder()
    return MemoryStore(db_path, **kwargs)
