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

import hashlib
import re
import struct
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

# ── 版本 ────────────────────────────────────────────────────────────────────
# v2（2026-10-27）：segments 增加 ep_ids 显式成员列。起因（Kimi 独立复核 P1-1）：
#   旧段用 first_ep..last_ep 区间当候选集，但 episode 按时间排序成段后 ID 会倒挂
#   （多写者下「新 id 旧时间」），区间覆盖错 episode ⇒ 检索漏检。
#   真实库实锤：46 段 n_ep≠区间长、2 段反向。ep_ids 存实际成员，区间字段保留仅作展示。
SCHEMA_VERSION = 6
MEMORY_VERSION = "0.1.0"

# v6（2026-10-30 晨，用户拍板「换生产嵌入器」）：EMBED_DIM 256→512 + 嵌入器口径
#   hash-ngram-zh-v1 → bge-small-zh-v1.5。依据（离线评估 embedder_eval.py，12 条真实
#   教训金标准 + 同义改写查询，独立 agent 审计通过）：哈希 hit@1=6/12 MRR=0.592，
#   bge hit@1=11/12 MRR=0.925——同义改写是词面哈希的路线性硬伤（它量字面重合，
#   经验检索恰恰是『换个说法找同件事』）。
#   纪律：episode_text 拼接口径**仍一字不动**（旧向量全体作废=预期内，reembed 全量重算）；
#   meta['embedder'] 记录库的编码口径，open_store 按 meta 自动匹配嵌入器——
#   库用什么编码，连接就用什么编码，杜绝混写。

# v5（2026-10-29 晚，DSH 执行 / ZCode 派单「记忆层 v5 扩展」Phase 1）：
#   episodes 增加 recurrences / source / dedup_key 三列，治「同一观测的循环写回堆行」——
#   真库 10k+ 条里 95% 是同一观测重复（一条「30×15 复核」重复 1459 次），展示端去重治不了
#   存储与召回多样性。写入侧由 intake.record_experience 统一去重：同 (subject, kind, obs,
#   action.target, outcome) 重复命中 ⇒ 不新增行，原行 recurrences+1 并更新结论。
#   硬约束：这三列**都不进** episode_text（改口径=全库 10k 向量作废）。

# v4（2026-10-29 下午）：segments 加 subject 列。起因（DSH 独立验收 P0-1）：v3 只在
#   episode 检索层过滤了主体，段摘要一路完全没过闸——agent 范围查询照样端上人的记忆摘要
#   （真库 85% 存活段是纯 human）。修法：段在落库时按成员 ep_ids 的主体一致性定 subject
#   （全 agent / 全 human / mixed），范围查询只出同主体段，mixed 段不进任何单仓。

# v3（2026-10-29）：episodes 增加 subject 主体分仓列。起因（ZCode 本体实验 E4 + 用户拍板
#   「宪法条款要么进结构要么不存在」）：桌面时间线 harvest 是**人的**情景记忆，代理经验批
#   （feed/nightloop/zcode 写回）是**代理的**情景记忆——两种记忆同库混存会互相污染
#   （330 条知识库映射 MM 子概念零覆盖=理论独有贡献）。harvest 写 'human'，其余默认 'agent'。
SUBJECT_AGENT = "agent"
SUBJECT_HUMAN = "human"
VALID_SUBJECTS = frozenset({SUBJECT_AGENT, SUBJECT_HUMAN})

# ── v5 来源流（source）与主体归因（actor）────────────────────────────────────
# source = 这条 episode 从哪条流进来（timeline 桌面动作 / manual 手工经验批 /
#          session 会话教训摄入）；subject = 谁的记忆（分仓）。两个字段语义分开。
SOURCE_TIMELINE = "timeline"
SOURCE_MANUAL = "manual"
SOURCE_SESSION = "session"
VALID_SOURCES = frozenset({SOURCE_TIMELINE, SOURCE_MANUAL, SOURCE_SESSION})

# actor = 精确到宿主/循环的归因（分仓仍是二元，human 仓的语义不因归因而放宽）。
ACTOR_HUMAN = "human"
ACTOR_ZCODE = "zcode"
ACTOR_CODEX = "codex"
ACTOR_DSH = "dsh"
ACTOR_DAYLOOP = "dayloop"
ACTOR_NIGHTLOOP = "nightloop"
ACTOR_UNKNOWN = "unknown"
VALID_ACTORS = frozenset(
    {ACTOR_HUMAN, ACTOR_ZCODE, ACTOR_CODEX, ACTOR_DSH, ACTOR_DAYLOOP,
     ACTOR_NIGHTLOOP, ACTOR_UNKNOWN}
)

# actor → subject 映射：只有 human 进 human 仓，其余（含 unknown）一律 agent 仓。
# 「人的记忆不可被代理改写」这条不因归因而松动：映射是单向的。
ACTOR_SUBJECT = {
    ACTOR_HUMAN: SUBJECT_HUMAN,
    ACTOR_ZCODE: SUBJECT_AGENT,
    ACTOR_CODEX: SUBJECT_AGENT,
    ACTOR_DSH: SUBJECT_AGENT,
    ACTOR_DAYLOOP: SUBJECT_AGENT,
    ACTOR_NIGHTLOOP: SUBJECT_AGENT,
    ACTOR_UNKNOWN: SUBJECT_AGENT,
}


def subject_for_actor(actor: str) -> str:
    """actor → subject（唯一口径，intake/harvest 共用；单独成函数是为了可注入自检）。"""
    return ACTOR_SUBJECT.get(str(actor or ACTOR_UNKNOWN), SUBJECT_AGENT)


# 存量回填口径（宁可 unknown 不许猜）：session_id 里出现以下子串才认归因。
# 只认**明确写过宿主名**的会话；其余一律 unknown。
ACTOR_SESSION_HINTS = (
    (ACTOR_DAYLOOP, "dayloop"),
    (ACTOR_NIGHTLOOP, "nightloop"),
    (ACTOR_ZCODE, "zcode"),
    (ACTOR_CODEX, "codex"),
    (ACTOR_DSH, "dsh"),
)


def actor_from_session(session_id: str) -> str:
    """按 session_id 推断 actor（保守：认不出就 unknown）。"""
    s = str(session_id or "").lower()
    for actor, hint in ACTOR_SESSION_HINTS:
        if hint in s:
            return actor
    return ACTOR_UNKNOWN

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
  subject TEXT NOT NULL DEFAULT 'agent',
  screen_version INTEGER,
  obs TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '{}',
  result TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL,
  verify TEXT NOT NULL DEFAULT '{}',
  text TEXT NOT NULL DEFAULT '',
  embed BLOB,
  recurrences INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'manual',
  dedup_key TEXT NOT NULL DEFAULT '',
  actor TEXT NOT NULL DEFAULT 'unknown',
  UNIQUE(src_seq, t));

CREATE TABLE IF NOT EXISTS segments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level INTEGER NOT NULL DEFAULT 0,
  t_start TEXT NOT NULL,
  t_end TEXT NOT NULL,
  first_ep INTEGER NOT NULL,
  last_ep INTEGER NOT NULL,
  ep_ids TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '',
  embed BLOB,
  n_ep INTEGER NOT NULL DEFAULT 0,
  subject TEXT NOT NULL DEFAULT 'mixed',
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
# v6：256→512（bge-small-zh-v1.5 输出 512 维）。旧 256 维 BLOB 在新口径下按长度判废。
EMBED_DIM = 512

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


# ── v5 去重键（intake 与迁移回填共用同一份口径，不许各自复刻）──────────────

def dedup_key(subject: str, kind: str, obs: str, action: Any, outcome: str) -> str:
    """循环写回的去重键：sha1(主体 ␟ kind ␟ 归一 obs ␟ 归一 target ␟ outcome)[:32]。

    口径（为什么这样切）：
      · subject 进键 —— 分仓之间永不合并（人的记忆不许被代理那条副本吃掉）；
      · outcome 进键 —— 同一事件先 failed 后 success 是**新信息**（两行各自成立）；
      · obs / target 都走 normalize_text —— 空白、全半角、大小写差异不算新观测；
      · 键不进 episode_text（见上方硬约束），只是写入侧的账。
    """
    target = ""
    if isinstance(action, dict):
        target = str(action.get("target", "") or "")
    raw = "\x1f".join([
        str(subject or ""),
        str(kind or ""),
        normalize_text(obs or ""),
        normalize_text(target),
        str(outcome or ""),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]

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
    subject: str = SUBJECT_AGENT
    src_seq: Optional[int] = None
    screen_version: Optional[int] = None
    recurrences: int = 1
    source: str = SOURCE_MANUAL
    dedup_key: str = ""
    actor: str = ACTOR_UNKNOWN
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
        if self.subject not in VALID_SUBJECTS:
            raise ValueError(
                f"episode.subject 非法: {self.subject!r}，合法值 {sorted(VALID_SUBJECTS)}"
            )
        if self.source not in VALID_SOURCES:
            raise ValueError(
                f"episode.source 非法: {self.source!r}，合法值 {sorted(VALID_SOURCES)}"
            )
        if self.actor not in VALID_ACTORS:
            raise ValueError(
                f"episode.actor 非法: {self.actor!r}，合法值 {sorted(VALID_ACTORS)}"
            )
        if not isinstance(self.recurrences, int) or self.recurrences < 1:
            raise ValueError(f"episode.recurrences 必须是 >=1 的整数，得到 {self.recurrences!r}")
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
            "subject": self.subject,
            "screen_version": self.screen_version,
            "obs": self.obs,
            "action": _json.dumps(self.action, ensure_ascii=False),
            "result": self.result,
            "outcome": self.outcome,
            "verify": _json.dumps(self.verify, ensure_ascii=False),
            "text": self.text_for_embedding(),
            "recurrences": int(self.recurrences),
            "source": self.source,
            "dedup_key": self.dedup_key,
            "actor": self.actor,
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
            subject=row.get("subject") or SUBJECT_AGENT,
            screen_version=row.get("screen_version"),
            obs=row.get("obs") or "",
            action=_json.loads(row.get("action") or "{}"),
            result=row.get("result") or "",
            outcome=row["outcome"],
            verify=_json.loads(row.get("verify") or "{}"),
            recurrences=int(row.get("recurrences") or 1),
            source=row.get("source") or SOURCE_MANUAL,
            dedup_key=row.get("dedup_key") or "",
            actor=row.get("actor") or ACTOR_UNKNOWN,
        )
