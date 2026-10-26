# -*- coding: utf-8 -*-
r"""agentctl.memory.contract —— 记忆层 v0 的**唯一契约真源**（记忆层刀 · 2026-10-26）

谁读它：
    store.py   / harvest.py / compact.py / retrieve.py / distill.py / __main__.py
    tests\_P5_记忆层判据.py
    （以及两个执行者交回的模块 —— 全部从这里 import，不许各自复刻常量。）

管什么（对应《连续视觉「视网膜」与记忆层》§2.2 episode 数据契约 + §6.6 第 2 步）：
    1. episode 三态结局口径：success / failed / unobservable / blocked
       —— 继承本仓铁律「看不清不判成功，要判 unobservable」（core.py 同款三态）。
    2. timeline 事件 (ok, code) → (outcome, verify.verdict) 的**唯一映射函数**。
    3. sqlite schema（episodes / segments / workflows / meta）—— DDL 只此一份。
    4. 向量 BLOB 打包口径：float32 小端，定长 EMBED_DIM。
    5. 巩固阈值的数值真源（MERGE_SIM / SPLIT_SIM / ANCHOR_N / SEG_GAP_SEC）。
    6. 文本归一化与检索文本拼接口径（embed 的输入必须全仓同源，否则向量不可比）。

不做：不碰桌面、不碰 core.py、不发任何 ctl_* 动作。纯数据契约。
"""
from __future__ import annotations

import re
import struct
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

# ── 版本 ────────────────────────────────────────────────────────────────────
SCHEMA_VERSION = 1
MEMORY_VERSION = "0.1.0"

# ── 结局三态+一（§2.2 / §6.6 第 2 步）───────────────────────────────────────
OUTCOME_SUCCESS = "success"            # 效果已发生且被证实（ok=1, code=0）
OUTCOME_FAILED = "failed"             # 可观测地没发生（含 code=8「确认没变」）
OUTCOME_UNOBSERVABLE = "unobservable"  # 没测出来（code=5）/ 动作没发出去
OUTCOME_BLOCKED = "blocked"            # 被闸门/急停拦下（action_blocked / action_anomaly）
VALID_OUTCOMES = frozenset(
    {OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_UNOBSERVABLE, OUTCOME_BLOCKED}
)

# ── timeline 事件映射 ───────────────────────────────────────────────────────
# 这些 kind 才构成 episode（动作事件）；观测类（shot/tree/find/screen/windows）v0 不收。
ACTION_KINDS = frozenset(
    {"click", "type", "fill", "keys", "scroll", "focus", "browser", "quiz"}
)
# 被拦/异常事件：单独成 episode，outcome=blocked（经验价值：哪里总被拦要能查到）
BLOCKED_KINDS = frozenset({"action_blocked", "action_anomaly"})
HARVEST_KINDS = ACTION_KINDS | BLOCKED_KINDS

# 时间线事件序号 → 结局。判定只认 (ok, code)，文本只是给人看的。
def verdict_from_code(ok: Optional[int], code: Optional[int]) -> tuple[str, str]:
    """返回 (outcome, verify_verdict)。

    口径（与 core.py 三态判词同源）：
        ok=1, code=0            → success   / success
        code=8（确认没变）       → failed    / failed      —— 可观测地没发生
        code=5（没测出来）       → unobservable / unobservable
        其它非零 code / ok=0    → failed    / failed
        blocked 类事件          → blocked   / unobservable（动作没发出去，效果无从观测）
    """
    if ok is None and code is None:
        return OUTCOME_UNOBSERVABLE, OUTCOME_UNOBSERVABLE
    if int(ok or 0) == 1 and int(code or 0) == 0:
        return OUTCOME_SUCCESS, OUTCOME_SUCCESS
    if int(code or 0) == 8:
        return OUTCOME_FAILED, OUTCOME_FAILED
    if int(code or 0) == 5:
        return OUTCOME_UNOBSERVABLE, OUTCOME_UNOBSERVABLE
    return OUTCOME_FAILED, OUTCOME_FAILED


# ── 会话切分 ────────────────────────────────────────────────────────────────
SESSION_GAP_SEC = 1800.0   # 相邻动作间隔超过 30 分钟 ⇒ 新 session

def make_session_id(t_iso: str) -> str:
    """会话 id：s-YYYYMMDD-HHMMSS（取会话首个动作时间）。"""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", t_iso or "")
    if not m:
        return "s-unknown"
    d, tt = "".join(m.groups()[:3]), "".join(m.groups()[3:])
    return f"s-{d}-{tt}"

# ── sqlite schema（DDL 真源，store.py 只执行不修改）────────────────────────
SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS episodes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  t TEXT NOT NULL,
  src_seq INTEGER,
  kind TEXT NOT NULL,
  session_id TEXT NOT NULL DEFAULT '',
  screen_version INTEGER,
  obs TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '{}',
  result TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL,
  verify TEXT NOT NULL DEFAULT '{}',
  text TEXT NOT NULL DEFAULT '',
  embed BLOB,
  UNIQUE(src_seq, t));

CREATE TABLE IF NOT EXISTS segments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level INTEGER NOT NULL DEFAULT 0,
  t_start TEXT NOT NULL,
  t_end TEXT NOT NULL,
  first_ep INTEGER NOT NULL,
  last_ep INTEGER NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  embed BLOB,
  n_ep INTEGER NOT NULL DEFAULT 0,
  merged_into INTEGER,
  created_at TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS workflows(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  signature TEXT NOT NULL UNIQUE,
  steps TEXT NOT NULL DEFAULT '[]',
  n_seen INTEGER NOT NULL DEFAULT 1,
  success_ratio REAL NOT NULL DEFAULT 1.0,
  last_verified TEXT NOT NULL DEFAULT '',
  example_session TEXT);

CREATE INDEX IF NOT EXISTS ix_ep_t ON episodes(t);
CREATE INDEX IF NOT EXISTS ix_ep_sess ON episodes(session_id);
CREATE INDEX IF NOT EXISTS ix_seg_live ON segments(level, merged_into);
"""

# ── 向量 BLOB 口径 ──────────────────────────────────────────────────────────
EMBED_DIM = 256

def pack_vec(values: list[float]) -> bytes:
    """float32 小端打包；长度必须等于 EMBED_DIM。"""
    if len(values) != EMBED_DIM:
        raise ValueError(f"embed dim mismatch: got {len(values)}, want {EMBED_DIM}")
    return struct.pack(f"<{EMBED_DIM}f", *values)

def unpack_vec(blob: Optional[bytes]) -> list[float]:
    if not blob:
        return []
    return list(struct.unpack(f"<{EMBED_DIM}f", blob))

# ── 巩固阈值真源（§2.3 双阈值滞回 + 周期锚点）──────────────────────────────
MERGE_SIM = 0.85    # 相邻段相似度 > 0.85 才合并（防抖动：中间带维持现状）
SPLIT_SIM = 0.65    # 相似度 < 0.65 才切分新段
ANCHOR_N = 20       # 周期性强制锚点：每 N 条 episode 必留一个细粒度段边界
SEG_GAP_SEC = 600.0 # 时间间隔超过 10 分钟 ⇒ 强制段边界
SUMMARY_MAX_CHARS = 1200

# ── 文本口径（embed 输入全仓同源）──────────────────────────────────────────
_TEXT_WS = re.compile(r"\s+")

def normalize_text(s: str) -> str:
    """NFKC → 去零宽 → 空白折叠 → 小写。检索与 embed 的**唯一**文本归一化。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    s = _TEXT_WS.sub(" ", s).strip().lower()
    return s

_EMBED_PARTS_CAP = 2000

def episode_text(kind: str, obs: str, action: dict, result: str) -> str:
    """检索文本拼接口径：kind + obs + action.target + result。

    ⚠ 嵌入向量与关键词检索都必须喂这一条的产物；
      改口径 ⇒ 旧向量全体作废（meta.k='embedder' 里记着口径版本）。
    """
    target = ""
    if isinstance(action, dict):
        target = str(action.get("target", "") or "")
    joined = " ".join([str(kind or ""), str(obs or ""), target, str(result or "")])
    joined = normalize_text(joined)
    return joined[:_EMBED_PARTS_CAP]

# ── Episode 数据契约 ────────────────────────────────────────────────────────

@dataclass
class Episode:
    """一条 episode = 观测 + 动作 + 结局（§2.2）。

    verify 字段（v0.3 增补）：episode 必须能回答「凭什么说它成功了」——
        verify = {"expect": str, "verdict": success|failed|unobservable, "cred": {...}}
    """
    t: str
    kind: str
    outcome: str
    obs: str = ""
    action: dict = field(default_factory=dict)
    result: str = ""
    verify: dict = field(default_factory=dict)
    session_id: str = ""
    src_seq: Optional[int] = None
    screen_version: Optional[int] = None
    id: Optional[int] = None

    def validate(self) -> None:
        if not self.t:
            raise ValueError("episode.t 必填（ISO8601）")
        if not self.kind:
            raise ValueError("episode.kind 必填")
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"episode.outcome 非法: {self.outcome!r}，合法值 {sorted(VALID_OUTCOMES)}"
            )
        if not isinstance(self.action, dict):
            raise ValueError("episode.action 必须是 dict")
        v = self.verify or {}
        if v and v.get("verdict") not in {OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_UNOBSERVABLE}:
            raise ValueError(f"verify.verdict 非法: {v.get('verdict')!r}")

    def text_for_embedding(self) -> str:
        return episode_text(self.kind, self.obs, self.action, self.result)

    def to_db_row(self) -> dict:
        import json as _json
        return {
            "t": self.t,
            "src_seq": self.src_seq,
            "kind": self.kind,
            "session_id": self.session_id,
            "screen_version": self.screen_version,
            "obs": self.obs,
            "action": _json.dumps(self.action, ensure_ascii=False),
            "result": self.result,
            "outcome": self.outcome,
            "verify": _json.dumps(self.verify, ensure_ascii=False),
            "text": self.text_for_embedding(),
        }

    @classmethod
    def from_db_row(cls, row: dict) -> "Episode":
        import json as _json
        return cls(
            id=row.get("id"),
            t=row["t"],
            src_seq=row.get("src_seq"),
            kind=row["kind"],
            session_id=row.get("session_id") or "",
            screen_version=row.get("screen_version"),
            obs=row.get("obs") or "",
            action=_json.loads(row.get("action") or "{}"),
            result=row.get("result") or "",
            outcome=row["outcome"],
            verify=_json.loads(row.get("verify") or "{}"),
        )
