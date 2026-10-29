# -*- coding: utf-8 -*-
r"""agentctl.memory.store —— 记忆层 v0 的 sqlite 存储 + 向量检索（记忆层刀 · 2026-10-26）

口径（总单 §五）：
    - DDL 只执行 contract.SQL_SCHEMA，本模块**不另写建表语句**；
    - 向量 BLOB 一律 contract.pack_vec（float32 小端，定长 EMBED_DIM）；
    - 两条检索路径：
        use_sqlite_vec=True   → vec0 影子表 vec_episodes(a float[EMBED_DIM])，rowid=episodes.id
        use_sqlite_vec=False  → numpy 暴力余弦（懒加载全库 BLOB 为 float32 矩阵）
        use_sqlite_vec=None   → 能加载 sqlite_vec 就用影子表，否则自动落 numpy
    - 查询向量与库内向量都按余弦处理；L2 范数 < 1e-12 的向量相似度记 0。

本模块只依赖 contract / embedder / numpy / 标准库；不碰桌面、不发 ctl_* 动作。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

import numpy as np

from . import contract
from .embedder import HashNGramEmbedder, tokenize as _default_tokenize

__all__ = ["MemoryStore"]

_ZERO_NORM = 1e-12
_ROW_CHUNK = 500
_KNN_LIMIT = 4096  # sqlite-vec 0.1.x vec0 KNN 查询的 LIMIT 硬上限


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    """episode 记忆库：sqlite 持久化 + 向量/关键词检索（拉取式，无守护进程）。"""

    def __init__(
        self,
        db_path: str,
        *,
        embedder=None,
        use_sqlite_vec: bool | None = None,
    ):
        self.db_path = os.fspath(db_path)
        if self.db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(self.db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)

        self._embedder = embedder if embedder is not None else HashNGramEmbedder()
        self._mat_ids: list[int] | None = None
        self._mat: np.ndarray | None = None

        self.conn: sqlite3.Connection | None = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(contract.SQL_SCHEMA)
        self._migrate_segments()
        self._migrate_subject()
        self._migrate_segments_subject()

        self.use_sqlite_vec = self._setup_vec(use_sqlite_vec)
        self._ensure_meta()
        self.conn.commit()

    def _migrate_segments(self) -> None:
        """v1→v2 迁移：segments 补 ep_ids 列；旧段（区间口径）整体作废待重建。

        起因（Kimi 独立复核 P1-1，2026-10-27）：多写者下「新 id 旧时间」使
        promote 的按时间成段与 first_ep..last_ep 区间脱钩——真实库 46 段
        n_ep≠区间长、2 段反向 ⇒ 旧段不可信，检索也不再读区间。
        旧段由 episodes 完全可重建：wipe + promoted_until 归零，下次 compact 全量重promote。
        """
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(segments)").fetchall()]
        if "ep_ids" not in cols:
            self.conn.execute("ALTER TABLE segments ADD COLUMN ep_ids TEXT NOT NULL DEFAULT '[]'")
            self.conn.execute("DELETE FROM segments")
            self.conn.execute("UPDATE meta SET v='0' WHERE k='promoted_until'")

    def _migrate_subject(self) -> None:
        """v2→v3：episodes 补 subject 列（ALTER 只做一次）；
        口径自愈（DSH 验收 P1-2）：`subject='human' ⇔ src_seq 非空` 这条核心账
        **每次开库都幂等校正一遍**——旧码直插（显式列 INSERT 不带 subject）会落
        DEFAULT 'agent'，把人的时间线记成代理经验；一条 UPDATE 代价可忽略，
        比只在首迁算一次可靠。"""
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(episodes)").fetchall()]
        if "subject" not in cols:
            self.conn.execute(
                "ALTER TABLE episodes ADD COLUMN subject TEXT NOT NULL DEFAULT 'agent'"
            )
            cur = self.conn.execute(
                "UPDATE episodes SET subject = 'human' WHERE src_seq IS NOT NULL"
            )
            self.conn.execute(
                "INSERT INTO meta(k, v) VALUES('schema_version', ?) "
                "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (str(contract.SCHEMA_VERSION),),
            )
            self.conn.commit()
            if self.get_meta("v3_subject_backfill") is None:
                self.set_meta(
                    "v3_subject_backfill",
                    f"human={int(cur.rowcount)} at {_now_iso()}",
                )
            return
        # 列已存在：口径自愈（幂等，DSH P1-2 修复）
        cur = self.conn.execute(
            "UPDATE episodes SET subject = 'human' "
            "WHERE src_seq IS NOT NULL AND subject <> 'human'"
        )
        if cur.rowcount:
            self.conn.commit()

    def _migrate_segments_subject(self) -> None:
        """v3→v4（DSH 验收 P0-1）：segments 补 subject 列并按成员 ep_ids 回填。
        口径：成员全 human ⇒ human；全 agent ⇒ agent；混合/空 ⇒ mixed。
        范围查询只出同主体段，mixed 不进任何单仓。幂等：列在即跳过。"""
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(segments)").fetchall()]
        if "subject" in cols:
            return
        self.conn.execute(
            "ALTER TABLE segments ADD COLUMN subject TEXT NOT NULL DEFAULT 'mixed'"
        )
        rows = self.conn.execute(
            "SELECT id, ep_ids FROM segments WHERE merged_into IS NULL"
        ).fetchall()
        import json as _json
        n_human = n_agent = n_mixed = 0
        for r in rows:
            try:
                ids = [int(x) for x in _json.loads(r["ep_ids"] or "[]")]
            except (TypeError, ValueError):
                ids = []
            subj = "mixed"
            if ids:
                marks = ",".join("?" for _ in ids)
                got = {
                    row[0] for row in self.conn.execute(
                        f"SELECT DISTINCT subject FROM episodes WHERE id IN ({marks})",
                        ids,
                    ).fetchall()
                }
                if got == {"human"}:
                    subj = "human"
                elif got == {"agent"}:
                    subj = "agent"
            if subj == "human":
                n_human += 1
            elif subj == "agent":
                n_agent += 1
            else:
                n_mixed += 1
            self.conn.execute(
                "UPDATE segments SET subject = ? WHERE id = ?", (subj, int(r["id"]))
            )
        self.conn.execute(
            "INSERT INTO meta(k, v) VALUES('schema_version', ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (str(contract.SCHEMA_VERSION),),
        )
        self.conn.commit()
        if self.get_meta("v4_segments_backfill") is None:
            self.set_meta(
                "v4_segments_backfill",
                f"human={n_human} agent={n_agent} mixed={n_mixed} at {_now_iso()}",
            )

    # ── 初始化 ──────────────────────────────────────────────────────────────

    def _setup_vec(self, mode: bool | None) -> bool:
        """按需加载 sqlite_vec 并建 vec0 影子表；返回最终是否启用。"""
        if mode is False:
            return False
        try:
            import sqlite_vec
        except Exception as exc:  # pragma: no cover - 环境差异分支
            if mode:
                raise RuntimeError("use_sqlite_vec=True 但无法 import sqlite_vec") from exc
            return False

        try:
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
        except Exception as exc:  # pragma: no cover - 环境差异分支
            if mode:
                raise RuntimeError("use_sqlite_vec=True 但加载 sqlite_vec 失败") from exc
            return False
        finally:
            try:
                self.conn.enable_load_extension(False)
            except Exception:  # pragma: no cover - 极老 sqlite
                pass

        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes "
                f"USING vec0(a float[{contract.EMBED_DIM}])"
            )
            # 兼容「上次以 numpy 路径写入、这次启用 vec」的库：补插缺失行。
            self.conn.execute(
                "INSERT INTO vec_episodes(rowid, a) "
                "SELECT id, embed FROM episodes "
                "WHERE embed IS NOT NULL "
                "AND id NOT IN (SELECT rowid FROM vec_episodes)"
            )
        except Exception as exc:  # pragma: no cover - 环境差异分支
            if mode:
                raise RuntimeError("use_sqlite_vec=True 但创建/同步 vec0 表失败") from exc
            return False
        return True

    def _ensure_meta(self) -> None:
        """只在缺失时写入口径版本，绝不覆盖已有 meta。"""
        name = getattr(self._embedder, "NAME", type(self._embedder).__name__)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(k, v) VALUES('embedder', ?)", (str(name),)
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(k, v) VALUES('schema_version', ?)",
            (str(contract.SCHEMA_VERSION),),
        )

    @property
    def embedder(self):
        return self._embedder

    # ── 低层工具 ────────────────────────────────────────────────────────────

    def _tokenize(self, text: str) -> list[str]:
        fn = getattr(self._embedder, "tokenize", None)
        if callable(fn):
            return list(fn(text))
        return _default_tokenize(text)

    def _invalidate_matrix(self) -> None:
        self._mat_ids = None
        self._mat = None

    def _matrix(self) -> tuple[list[int], np.ndarray]:
        """懒加载全库 embed BLOB 为 float32 矩阵（行与 id 对齐）。"""
        if self._mat_ids is not None and self._mat is not None:
            return self._mat_ids, self._mat
        need = contract.EMBED_DIM * 4
        rows = self.conn.execute(
            "SELECT id, embed FROM episodes WHERE embed IS NOT NULL ORDER BY id"
        ).fetchall()
        ids: list[int] = []
        blobs: list[bytes] = []
        for row in rows:
            blob = row["embed"]
            if blob and len(blob) == need:
                ids.append(int(row["id"]))
                blobs.append(bytes(blob))
        if blobs:
            mat = np.frombuffer(b"".join(blobs), dtype="<f4").reshape(-1, contract.EMBED_DIM)
        else:
            mat = np.zeros((0, contract.EMBED_DIM), dtype=np.float32)
        self._mat_ids = ids
        self._mat = mat
        return ids, mat

    @staticmethod
    def _sims(query: np.ndarray, mat: np.ndarray) -> np.ndarray:
        """余弦相似度；零范数向量记 0（含空查询，调用方通常提前返回）。"""
        out = np.zeros(mat.shape[0], dtype=np.float64)
        if mat.size == 0:
            return out
        qn = float(np.linalg.norm(query))
        if qn < _ZERO_NORM:
            return out
        work = mat.astype(np.float64, copy=False)
        norms = np.linalg.norm(work, axis=1)
        ok = norms >= _ZERO_NORM
        if ok.any():
            out[ok] = (work[ok] @ query) / (norms[ok] * qn)
        np.clip(out, -1.0, 1.0, out=out)
        return out

    def _rows_by_ids(self, ids: list[int]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for start in range(0, len(ids), _ROW_CHUNK):
            chunk = ids[start:start + _ROW_CHUNK]
            marks = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT * FROM episodes WHERE id IN ({marks})", chunk
            ).fetchall()
            for row in rows:
                item = dict(row)
                out[int(item["id"])] = item
        return out

    @staticmethod
    def _public_row(row: dict) -> dict:
        """检索结果不带 embed BLOB（便于上层直接 JSON 序列化）。"""
        out = dict(row)
        out.pop("embed", None)
        return out

    def _count_embedded(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM episodes WHERE embed IS NOT NULL"
        ).fetchone()
        return int(row["n"])

    def _search_vec_ids(self, query: np.ndarray, limit: int) -> list[int]:
        if limit <= 0:
            return []
        blob = contract.pack_vec([float(x) for x in query])
        rows = self.conn.execute(
            "SELECT rowid FROM vec_episodes WHERE a MATCH ? "
            "ORDER BY distance LIMIT ?",
            (blob, int(limit)),
        ).fetchall()
        return [int(row[0]) for row in rows]

    # ── episode 写入 / 读取 ─────────────────────────────────────────────────

    def add_episode(self, ep: contract.Episode) -> int:
        ep.validate()
        if ep.src_seq is not None:
            # 幂等键 = (src_seq, t)：timeline 的 seq 跨 epoch 会重复（真源 UNIQUE(seq,hash)），
            # 单看 src_seq 会把别的事件误判成重复（真数据演练踩过：184 条被吞）。
            existing = self.conn.execute(
                "SELECT id FROM episodes WHERE src_seq = ? AND t = ?",
                (ep.src_seq, ep.t),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])

        row = ep.to_db_row()
        blob = contract.pack_vec(self._embedder.embed(row["text"]))
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO episodes("
            "t, src_seq, kind, session_id, subject, screen_version, obs, action, "
            "result, outcome, verify, text, embed"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["t"],
                row["src_seq"],
                row["kind"],
                row["session_id"],
                row["subject"],
                row["screen_version"],
                row["obs"],
                row["action"],
                row["result"],
                row["outcome"],
                row["verify"],
                row["text"],
                blob,
            ),
        )
        if cur.rowcount == 0:
            # 并发下 UNIQUE(src_seq, t) 抢先落库：不重嵌、不覆盖，返回既有 id。
            existing = self.conn.execute(
                "SELECT id FROM episodes WHERE src_seq = ? AND t = ?",
                (ep.src_seq, ep.t),
            ).fetchone()
            self.conn.commit()
            if existing is None:  # pragma: no cover - 理论上不可达
                raise RuntimeError("add_episode 插入被忽略且找不到既有行")
            return int(existing["id"])

        ep_id = int(cur.lastrowid)
        if self.use_sqlite_vec:
            self.conn.execute(
                "INSERT INTO vec_episodes(rowid, a) VALUES(?, ?)", (ep_id, blob)
            )
        self.conn.commit()
        self._invalidate_matrix()
        return ep_id

    def get_episode(self, ep_id: int) -> contract.Episode | None:
        row = self.conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (int(ep_id),)
        ).fetchone()
        if row is None:
            return None
        return contract.Episode.from_db_row(dict(row))

    def iter_episode_rows(self, since_id: int = 0):
        cur = self.conn.execute(
            "SELECT * FROM episodes WHERE id > ? ORDER BY id", (int(since_id),)
        )
        for row in cur:
            yield dict(row)

    def recent_episode_ids(self, n: int = 200) -> list[int]:
        """最近 n 条 episode id（新近回退用：未入段的最新写入对检索可见——
        ZCode 本体实验实锤「写读经 promote 耦合」的治标，配合检索候选集并集）。"""
        rows = self.conn.execute(
            "SELECT id FROM episodes ORDER BY id DESC LIMIT ?", (int(n),)
        ).fetchall()
        return [int(r["id"]) for r in rows]

    def subject_ids(self, subject: str) -> set[int]:
        """某主体的全部 episode id（范围检索的过滤集）。"""
        rows = self.conn.execute(
            "SELECT id FROM episodes WHERE subject = ?", (str(subject),)
        ).fetchall()
        return {int(r["id"]) for r in rows}

    def count_episodes(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM episodes").fetchone()
        return int(row["n"])

    def delete_episode(self, ep_id: int) -> bool:
        """硬删一条 episode（含 vec 影子表行）。只用于清误入数据（DSH 验收 P1-1：
        迁移演示条目写进了生产库）——正常历史不删。返回是否删了。"""
        row = self.conn.execute(
            "SELECT id FROM episodes WHERE id = ?", (int(ep_id),)
        ).fetchone()
        if row is None:
            return False
        self.conn.execute("DELETE FROM episodes WHERE id = ?", (int(ep_id),))
        if self.use_sqlite_vec:
            try:
                self.conn.execute("DELETE FROM vec_episodes WHERE rowid = ?", (int(ep_id),))
            except Exception:  # pragma: no cover - 影子表缺行
                pass
        self.conn.commit()
        self._invalidate_matrix()
        return True

    # ── meta ────────────────────────────────────────────────────────────────

    def set_meta(self, k: str, v: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(k, v) VALUES(?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (str(k), str(v)),
        )
        self.conn.commit()

    def get_meta(self, k: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT v FROM meta WHERE k = ?", (str(k),)
        ).fetchone()
        if row is None:
            return default
        return row["v"]

    # ── 检索 ────────────────────────────────────────────────────────────────

    def search_episodes(
        self,
        query_vec: list[float],
        k: int = 8,
        candidate_ids: list[int] | None = None,
    ) -> list[tuple[dict, float]]:
        if k <= 0:
            return []
        query = np.asarray(query_vec, dtype=np.float64).reshape(-1)
        if query.size != contract.EMBED_DIM:
            raise ValueError(
                f"query dim mismatch: got {query.size}, want {contract.EMBED_DIM}"
            )
        if float(np.linalg.norm(query)) < _ZERO_NORM:
            return []
        cand = None if candidate_ids is None else {int(x) for x in candidate_ids}
        if cand is not None and not cand:
            return []

        if self.use_sqlite_vec:
            # vec0 KNN 的 LIMIT 有硬上限（0.1.x 为 4096）：全库超限时若带候选集就回落
            # numpy 精确路径；不带候选集则取 KNN 上限内的最近邻（对 k≪4096 的检索语义不变）。
            total = self._count_embedded()
            if cand is not None and len(cand) > _KNN_LIMIT:
                ranked = None
            else:
                ranked = self._search_vec_ids(query, min(total, _KNN_LIMIT))
                if cand is not None:
                    ranked = [i for i in ranked if i in cand]
            if ranked is None:
                return self._search_episodes_numpy(query, k, cand)
            if not ranked:
                return []
            rows = self._rows_by_ids(ranked)
            ids = [i for i in ranked if i in rows]
            if not ids:
                return []
            mat = np.asarray(
                [contract.unpack_vec(rows[i]["embed"]) for i in ids], dtype=np.float32
            )
            sims = self._sims(query, mat)
        else:
            return self._search_episodes_numpy(query, k, cand)

        pairs = sorted(zip(ids, sims.tolist()), key=lambda p: (-p[1], p[0]))[:k]
        rows = self._rows_by_ids([i for i, _ in pairs])
        return [
            (self._public_row(rows[i]), float(score))
            for i, score in pairs
            if i in rows
        ]

    def _search_episodes_numpy(
        self,
        query: np.ndarray,
        k: int,
        cand: set[int] | None,
    ) -> list[tuple[dict, float]]:
        """numpy 暴力余弦（精确）：全库矩阵懒加载；candidate_ids 限定候选。"""
        ids, mat = self._matrix()
        if cand is not None:
            keep = [j for j, i in enumerate(ids) if i in cand]
            ids = [ids[j] for j in keep]
            mat = mat[keep] if keep else mat[:0]
        if not ids:
            return []
        sims = self._sims(query, mat)
        pairs = sorted(zip(ids, sims.tolist()), key=lambda p: (-p[1], p[0]))[:k]
        rows = self._rows_by_ids([i for i, _ in pairs])
        return [
            (self._public_row(rows[i]), float(score))
            for i, score in pairs
            if i in rows
        ]

    def search_keyword(
        self,
        text: str,
        k: int = 8,
        candidate_ids: list[int] | None = None,
    ) -> list[tuple[dict, float]]:
        if k <= 0:
            return []
        q_tokens = set(self._tokenize(text))
        if not q_tokens:
            return []
        cand = None if candidate_ids is None else {int(x) for x in candidate_ids}
        if cand is not None and not cand:
            return []

        rows = self.conn.execute("SELECT * FROM episodes").fetchall()
        scored: list[tuple[dict, float]] = []
        for row in rows:
            item = dict(row)
            if cand is not None and int(item["id"]) not in cand:
                continue
            tokens = set(self._tokenize(item.get("text") or ""))
            hit = len(tokens & q_tokens) / len(q_tokens)
            if hit > 0.0:
                scored.append((item, hit))
        scored.sort(key=lambda p: (-p[1], int(p[0]["id"])))
        return [(self._public_row(item), score) for item, score in scored[:k]]

    # ── segments ────────────────────────────────────────────────────────────

    def iter_segments(self, live_only: bool = True, level: int | None = None):
        where: list[str] = []
        params: list = []
        if live_only:
            where.append("merged_into IS NULL")
        if level is not None:
            where.append("level = ?")
            params.append(int(level))
        sql = "SELECT * FROM segments"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY level, first_ep, id"
        cur = self.conn.execute(sql, params)
        for row in cur:
            yield dict(row)

    def add_segment(
        self,
        *,
        level: int,
        t_start: str,
        t_end: str,
        first_ep: int,
        last_ep: int,
        summary: str,
        n_ep: int,
        ep_ids: list[int] | None = None,
    ) -> int:
        """ep_ids：段的**实际成员**（按时间序）——检索候选集只认它，不再读区间
        （区间字段保留仅作展示；不传时退化为空表，等价「无候选」，宁可漏不猜）。
        subject（v4，DSH P0-1）：按成员 ep_ids 的主体一致性落库——全 human/全 agent/
        mixed；范围检索只出同主体段，mixed 不进任何单仓。"""
        blob = contract.pack_vec(
            self._embedder.embed(contract.normalize_text(summary or ""))
        )
        ids_json = json.dumps([int(x) for x in (ep_ids or [])], ensure_ascii=False)
        subj = "mixed"
        if ep_ids:
            marks = ",".join("?" for _ in ep_ids)
            got = {
                row[0] for row in self.conn.execute(
                    f"SELECT DISTINCT subject FROM episodes WHERE id IN ({marks})",
                    [int(x) for x in ep_ids],
                ).fetchall()
            }
            if got == {"human"}:
                subj = "human"
            elif got == {"agent"}:
                subj = "agent"
        cur = self.conn.execute(
            "INSERT INTO segments("
            "level, t_start, t_end, first_ep, last_ep, ep_ids, summary, embed, n_ep, subject, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(level),
                str(t_start),
                str(t_end),
                int(first_ep),
                int(last_ep),
                ids_json,
                str(summary or ""),
                blob,
                int(n_ep),
                subj,
                _now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def mark_merged(self, child_ids: list[int], parent_id: int) -> None:
        ids = [int(x) for x in child_ids]
        for start in range(0, len(ids), _ROW_CHUNK):
            chunk = ids[start:start + _ROW_CHUNK]
            marks = ",".join("?" for _ in chunk)
            self.conn.execute(
                f"UPDATE segments SET merged_into = ? WHERE id IN ({marks})",
                [int(parent_id)] + chunk,
            )
        self.conn.commit()

    # ── workflows（主控集成补齐：distill 写 / retrieve 读 的鸭型缝隙）────────

    def iter_workflows(self):
        cur = self.conn.execute(
            "SELECT * FROM workflows ORDER BY n_seen DESC, id"
        )
        for row in cur:
            yield dict(row)

    def upsert_workflow(
        self,
        *,
        name: str,
        signature: str,
        steps,
        success_ratio: float,
        last_verified: str,
        example_session: str = "",
    ) -> None:
        """signature 唯一键 upsert：n_seen 累计、success_ratio 滚动、last_verified 取新。"""
        if not isinstance(steps, str):
            steps = json.dumps(list(steps), ensure_ascii=False)
        row = self.conn.execute(
            "SELECT id, n_seen, success_ratio FROM workflows WHERE signature = ?",
            (str(signature),),
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO workflows("
                "name, signature, steps, n_seen, success_ratio, last_verified, example_session"
                ") VALUES(?,?,?,1,?,?,?)",
                (
                    str(name or "workflow"),
                    str(signature),
                    steps,
                    float(success_ratio),
                    str(last_verified),
                    str(example_session or ""),
                ),
            )
        else:
            n_seen = int(row["n_seen"]) + 1
            rolled = (
                float(row["success_ratio"]) * int(row["n_seen"]) + float(success_ratio)
            ) / n_seen
            self.conn.execute(
                "UPDATE workflows SET name=?, steps=?, n_seen=?, success_ratio=?, "
                "last_verified=MAX(last_verified, ?), example_session=? WHERE id=?",
                (
                    str(name or "workflow"),
                    steps,
                    n_seen,
                    rolled,
                    str(last_verified),
                    str(example_session or ""),
                    int(row["id"]),
                ),
            )
        self.conn.commit()

    # ── 生命周期 ────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.commit()
            except Exception:  # pragma: no cover - 已损坏连接
                pass
            self.conn.close()
            self.conn = None
        self._invalidate_matrix()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
