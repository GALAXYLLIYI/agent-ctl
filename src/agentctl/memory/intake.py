# -*- coding: utf-8 -*-
r"""agentctl.memory.intake —— 经验摄入（v5 · 2026-10-29 晚）

为什么要这一层
--------------
派单背景①：真库 10k+ 条里 95% 是同一观测的循环写回（一条「30×15 复核」重复 1459 次，
day loop 每 40 秒 feed 一次同文本）。展示端去重治不了**存储**与**召回多样性**——
写一次就多一行，`recall()` 每轮只能端回同一条。

派单背景②：引擎只吃 timeline 动作流，三宿主（ZCode/Codex/DSH）的交付结论、翻车教训、
验证结论全在会话与报告文件里，没有摄入通路。本模块给它们一条标准的、带出处的入口。

两层口径
--------
1. `record_experience(...)` —— 代理经验写入的推荐入口：
   去重键 = `contract.dedup_key(subject, kind, obs, action, outcome)`。
   命中已有条目 ⇒ **不删不改历史语义**：原行 `recurrences+1`，`result`/`verify` 更新为最新
   （结论可以演进），首次出现时间保留在 `verify.cred.first_t`，旧出处挪到 `verify.cred.first_cred`。
   outcome 进键 ⇒ 同一事件先 failed 后 success 是**新信息**（两行各自成立）。

2. `ingest_lessons(store, path)` —— 教训 JSONL 适配器（格式见 `docs/lessons_jsonl_v1.md`）：
   逐行校验 → `record_experience(source='session', actor=行里的 actor)` → 游标断点续收 → promote 收尾。
   **硬规矩**：`outcome=success` 必须带 `verify.cred.file`（哪份报告/哪个脚本）；无出处的教训
   只能标 unobservable/failed。没验证过就标 unobservable —— 不许把「我觉得成了」写成成功。

注入自检开关（判据里 monkeypatch 用，不是运行时配置）
----------------------------------------------------
    DEDUP_ENABLED      —— 置 False ⇒ ⑮ 组「同 obs 写两次必须只有一行」变红
    REQUIRE_PROVENANCE —— 置 False ⇒ ⑯ 组「无出处不得标 success」变红

不做什么：不碰桌面、不发 ctl_* 动作、不写生产库（由调用方决定库路径）。
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime
from typing import Any, Optional

from agentctl.memory import contract

__all__ = [
    "DEDUP_ENABLED",
    "REQUIRE_PROVENANCE",
    "record_experience",
    "ingest_lessons",
    "parse_lesson",
    "require_provenance",
    "estimate_savings",
]

# ── 开关（默认全开；判据的注入自检会临时关掉它们）────────────────────────────
DEDUP_ENABLED = True
REQUIRE_PROVENANCE = True

# 教训行的推荐 kind（不强制：kind 是自由文本，这里只给写作者一个词表）
LESSON_KIND_HINTS = ("lesson", "delivery", "pitfall", "verify", "decision")

_CURSOR_SUFFIX = ".cursor"


def _now_iso() -> str:
    # 带本机时区偏移（不是裸 naive）：混合时区 promote 已判过这个坑（P5 ⑫）
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _resolve_subject(subject: Optional[str], actor: Optional[str]) -> tuple[str, str]:
    """subject 与 actor 的映射只允许一种答案；给不一致的组合直接拒收。"""
    actor = str(actor or contract.ACTOR_UNKNOWN)
    if actor not in contract.VALID_ACTORS:
        raise ValueError(f"actor 非法: {actor!r}，合法值 {sorted(contract.VALID_ACTORS)}")
    mapped = contract.subject_for_actor(actor)
    if subject is None or subject == "":
        return mapped, actor
    subject = str(subject)
    if subject not in contract.VALID_SUBJECTS:
        raise ValueError(
            f"subject 非法: {subject!r}，合法值 {sorted(contract.VALID_SUBJECTS)}"
        )
    if subject != mapped:
        raise ValueError(
            f"subject 与 actor 映射不一致: subject={subject!r} actor={actor!r} "
            f"（{actor} ⇒ {mapped}；分仓语义不许被归因随手改）"
        )
    return subject, actor


def record_experience(
    store: Any,
    *,
    kind: str,
    obs: str,
    outcome: str,
    result: str = "",
    verify: Optional[dict] = None,
    session_id: str = "",
    subject: Optional[str] = None,
    actor: Optional[str] = None,
    action: Optional[dict] = None,
    source: str = contract.SOURCE_MANUAL,
    t: Optional[str] = None,
    dedup: Optional[bool] = None,
) -> dict:
    """写一条经验（带去重合并）。返回 {"id", "created", "recurrences", "dedup_key"}。

    dedup=None ⇒ 跟随模块开关 DEDUP_ENABLED；显式 False 只跳过查重（键照样算并落库，
    便于之后开库自愈/离线统计）。
    """
    subject, actor = _resolve_subject(subject, actor)
    verify = dict(verify or {})
    if not verify.get("verdict"):
        # verify.verdict 只认 success/failed/unobservable（blocked 没发出去 ⇒ 不可观测）
        verify["verdict"] = (
            outcome if outcome in {contract.OUTCOME_SUCCESS, contract.OUTCOME_FAILED,
                                   contract.OUTCOME_UNOBSERVABLE}
            else contract.OUTCOME_UNOBSERVABLE
        )
    act = dict(action or {"type": str(kind), "target": ""})
    key = contract.dedup_key(subject, kind, obs, act, outcome)
    do_dedup = DEDUP_ENABLED if dedup is None else bool(dedup)

    if do_dedup:
        row = store.conn.execute(
            "SELECT id, t, result, verify, text, recurrences FROM episodes "
            "WHERE dedup_key = ? ORDER BY id LIMIT 1",
            (key,),
        ).fetchone()
        if row is not None:
            return _merge_into(store, row, kind=kind, obs=obs, act=act, result=result,
                               verify=verify, outcome=outcome, key=key)

    ep = contract.Episode(
        t=t or _now_iso(),
        kind=str(kind),
        outcome=outcome,
        obs=str(obs),
        action=act,
        result=str(result),
        verify=verify or {"verdict": outcome},
        session_id=str(session_id or ""),
        subject=subject,
        source=source,
        dedup_key=key,
        actor=actor,
    )
    ep.validate()
    ep_id = store.add_episode(ep)
    return {"id": int(ep_id), "created": True, "recurrences": int(ep.recurrences),
            "dedup_key": key, "subject": subject, "actor": actor}


def _merge_into(store, row, *, kind, obs, act, result, verify, outcome, key) -> dict:
    """命中已有条目：只累加计数 + 把结论/出处更新为最新，历史语义不动。"""
    ep_id = int(row["id"])
    old_verify = json.loads(row["verify"] or "{}")
    v_new = dict(verify)
    if not v_new.get("verdict"):
        v_new["verdict"] = outcome
    cred = dict(v_new.get("cred") or {})
    cred.setdefault("first_t", row["t"])          # 首次写入时间（出处链的锚）
    if old_verify.get("cred") and "first_cred" not in cred:
        cred["first_cred"] = old_verify["cred"]   # 首次出处留档
    v_new["cred"] = cred

    new_text = contract.episode_text(kind, obs, act, result)
    blob = None
    if new_text != (row["text"] or ""):
        blob = contract.pack_vec(store.embedder.embed(new_text))
        store.conn.execute(
            "UPDATE episodes SET recurrences = recurrences + 1, result = ?, verify = ?, "
            "text = ?, embed = ? WHERE id = ?",
            (str(result), json.dumps(v_new, ensure_ascii=False), new_text, blob, ep_id),
        )
        if getattr(store, "use_sqlite_vec", False):
            try:
                store.conn.execute("DELETE FROM vec_episodes WHERE rowid = ?", (ep_id,))
                store.conn.execute(
                    "INSERT INTO vec_episodes(rowid, a) VALUES(?, ?)", (ep_id, blob)
                )
            except Exception:  # pragma: no cover - 影子表缺行
                pass
        store._invalidate_matrix()
    else:
        store.conn.execute(
            "UPDATE episodes SET recurrences = recurrences + 1, result = ?, verify = ? "
            "WHERE id = ?",
            (str(result), json.dumps(v_new, ensure_ascii=False), ep_id),
        )
    store.conn.commit()
    n = store.conn.execute(
        "SELECT recurrences FROM episodes WHERE id = ?", (ep_id,)
    ).fetchone()[0]
    return {"id": ep_id, "created": False, "recurrences": int(n),
            "dedup_key": key, "merged": True}


# ── 教训 JSONL 适配器 ───────────────────────────────────────────────────────

def parse_lesson(raw: dict) -> dict:
    """校验一行教训，返回 record_experience 的参数；不合法直接 ValueError。

    必填：t / actor / kind / obs / outcome / result；verify.cred.file 在 success 时必填。
    """
    if not isinstance(raw, dict):
        raise ValueError("教训行必须是 JSON 对象")
    for k in ("t", "actor", "kind", "obs", "outcome", "result"):
        if not str(raw.get(k) or "").strip():
            raise ValueError(f"缺必填字段 {k!r}")
    actor = str(raw["actor"]).strip().lower()
    if actor not in contract.VALID_ACTORS:
        raise ValueError(f"actor 非法: {actor!r}（合法 {sorted(contract.VALID_ACTORS)}）")
    outcome = str(raw["outcome"]).strip().lower()
    if outcome not in contract.VALID_OUTCOMES:
        raise ValueError(f"outcome 非法: {outcome!r}（合法 {sorted(contract.VALID_OUTCOMES)}）")
    verify = raw.get("verify") or {}
    if not isinstance(verify, dict):
        raise ValueError("verify 必须是对象")
    verify = dict(verify)
    verify.setdefault("verdict", outcome if outcome in
                      {contract.OUTCOME_SUCCESS, contract.OUTCOME_FAILED,
                       contract.OUTCOME_UNOBSERVABLE} else contract.OUTCOME_UNOBSERVABLE)
    cred = dict(verify.get("cred") or {})
    if cred.get("file"):
        verify["cred"] = cred
    # 出处强制的**唯一**执行点是 record_experience（REQUIRE_PROVENANCE 开关在那边）；
    # 这里只做结构校验，避免两处各判一套（注入自检也就只需关一处）。
    return {
        "t": str(raw["t"]).strip(),
        "kind": str(raw["kind"]).strip(),
        "obs": str(raw["obs"]).strip(),
        "outcome": outcome,
        "result": str(raw["result"]).strip(),
        "verify": verify,
        "actor": actor,
        "action": {"type": str(raw["kind"]).strip(),
                   "target": str(raw.get("target") or "").strip()},
        "session_id": str(raw.get("session_id") or ""),
        "source": contract.SOURCE_SESSION,
    }


def require_provenance(lesson: dict) -> None:
    """教训行的出处闸：`outcome=success` 必须带 `verify.cred.file`。

    为什么闸放在**教训摄入**这一层而不是 record_experience：
    动作流的出处是结构化现场（session_id / src_seq / round），不靠文件路径；
    教训的出处只能靠「哪份报告第几节」，所以这条规矩只对教训生效。
    开关是 REQUIRE_PROVENANCE —— 判据 ⑯ 靠把它关掉来证明这条闸是活的。
    """
    if not REQUIRE_PROVENANCE:
        return
    if lesson.get("outcome") != contract.OUTCOME_SUCCESS:
        return
    cred = (lesson.get("verify") or {}).get("cred") or {}
    if not str(cred.get("file") or "").strip():
        raise ValueError(
            "outcome=success 但没有出处：verify.cred.file 必填"
            "（无出处的教训只能标 unobservable/failed）"
        )


def ingest_lessons(
    store: Any,
    path: str,
    *,
    promote: bool = True,
    cursor_path: Optional[str] = None,
    subject: Optional[str] = None,
) -> dict:
    """读教训 JSONL 逐条入库；断点续收（游标文件记已消费行号）；promote 收尾。

    游标语义：**按「已处理行数」推进**（含被拒的行）——重跑同文件必须零新增，
    被拒的行由 errors 明示，改完想重试就把游标文件删掉或指定新 cursor_path。
    """
    cur_path = cursor_path or (os.fspath(path) + _CURSOR_SUFFIX)
    start = 0
    if os.path.exists(cur_path):
        try:
            with io.open(cur_path, encoding="utf-8") as f:
                start = int((f.read() or "0").strip() or 0)
        except (ValueError, OSError):
            start = 0

    added = merged = rejected = 0
    errors: list[dict] = []
    consumed = start
    with io.open(path, encoding="utf-8") as f:
        lines = f.readlines()
    for idx, line in enumerate(lines, 1):
        if idx <= start:
            continue
        consumed = idx
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            args = parse_lesson(raw)
            require_provenance(args)
            if subject:
                args["subject"] = subject
            out = record_experience(store, **args)
            if out.get("created"):
                added += 1
            else:
                merged += 1
        except (ValueError, TypeError) as exc:
            rejected += 1
            errors.append({"line": idx, "error": str(exc)})

    with io.open(cur_path, "w", encoding="utf-8") as f:
        f.write(str(consumed))

    promoted = None
    if promote:
        from agentctl.memory.compact import Compactor

        promoted = Compactor(store).promote()
    return {
        "file": os.fspath(path),
        "lines": len(lines),
        "from_line": start + 1,
        "consumed_until": consumed,
        "added": added,
        "merged": merged,
        "rejected": rejected,
        "errors": errors,
        "promoted": promoted,
    }


def estimate_savings(store: Any) -> dict:
    """离线基线：按去重键统计「若当初就去重会省多少行」（只读，不改库）。"""
    rows = store.conn.execute(
        "SELECT id, subject, kind, obs, action, outcome, dedup_key FROM episodes"
    ).fetchall()
    keys: dict[str, list[int]] = {}
    for r in rows:
        key = r["dedup_key"] or contract.dedup_key(
            r["subject"] or contract.SUBJECT_AGENT, r["kind"], r["obs"],
            json.loads(r["action"] or "{}"), r["outcome"],
        )
        keys.setdefault(key, []).append(int(r["id"]))
    dup_groups = {k: v for k, v in keys.items() if len(v) > 1}
    return {
        "rows": len(rows),
        "unique_keys": len(keys),
        "saved_rows": len(rows) - len(keys),
        "dup_groups": len(dup_groups),
        "top_groups": sorted(
            ((len(v), v[0]) for v in dup_groups.values()), reverse=True
        )[:5],
    }
