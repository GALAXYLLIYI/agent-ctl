# -*- coding: utf-8 -*-
r"""agentctl.memory.harvest —— timeline.db 动作事件流 → episode 库（记忆层刀 · 2026-10-26）

设计依据（记忆层总单 + 上游设计稿 §5.4 / §6.6）：
    · timeline.db 是**原始事件流真源**（store.py 哈希链管）；episode 库是它的**语义视图**——
      「同一原始事件流，两个索引」。本模块只读 timeline（sqlite 只读连接），绝不回写。
    · 断点续收：meta['harvest_cursor_seq'] 记已收到的最大 seq；重跑幂等（UNIQUE(src_seq) 双保险）。
    · 会话切分：相邻动作间隔 > SESSION_GAP_SEC ⇒ 新 session_id；
      跨进程续收也能延续会话（meta 里记 last_session_id / last_event_t）。
    · 三值结局映射只认 contract.verdict_from_code —— 「看不清不判成功」同款纪律。

不做：不碰桌面、不做任何 ctl_* 动作、不写 timeline.db。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Optional

from agentctl.memory import contract
from agentctl.memory.contract import Episode

_TEXT_CAP = 120


def repo_root() -> str:
    # src/agentctl/memory/harvest.py → 向上三级 = 仓根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def default_timeline_dir() -> str:
    return os.environ.get("AGENTCTL_TIMELINE", os.path.join(repo_root(), "work", "timeline"))


def _cap(s: str, n: int = _TEXT_CAP) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _first_str(p: dict, keys: tuple[str, ...], cap: int = 60) -> str:
    for key in keys:
        v = p.get(key)
        if isinstance(v, str) and v.strip():
            return _cap(v.strip(), cap)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return ""


def _win_sensitive(exe: str, cls: str) -> tuple[bool, str]:
    """敏感窗终判（记忆层复用**闸门同款名单真源** authstate.deny_window —— 五张表老线同源）。

    hard（授权/急停弹窗 / UAC 框 / 系统设置页 / python 自家窗）与 terminal（cmd/powershell 终端族）
    都算命中：动作本身可留痕，但**窗口身份（exe/类名）不落记忆库**（设计稿 §5.2 隐私边界）。
    标题比对不适用——timeline 只存 title_hash，本来就没有明文标题（这恰好是对的）。
    判据自身出错 ⇒ **按命中处理**（隐私 fail-closed，不许静默放行）。
    """
    try:
        from agentctl import authstate
        hit, why, _kind = authstate.deny_window(exe=exe, cls=cls, title="")
        return bool(hit), why or ""
    except Exception as exc:  # noqa: BLE001
        return True, "敏感窗名单判不了（%s: %s）⇒ fail-closed 按命中过滤" % (type(exc).__name__, exc)


def _payload_brief(kind: str, p: dict) -> tuple[str, str, str, str, str]:
    """按各 kind 的真实 payload 形状容错提取 (obs, target, expect, result_ext, win_filtered)。

    实测形状（2026-10-26 真库抽样）：
        click  = {xy, hwnd, from_capture, evidence{changed,verdict,…}, win{…}}（无语义目标名）
        type   = {text, mode, …}；fill = {eid, text, …}
        keys   = {keys, ctrl, …}；scroll = {amount, …}
        browser= {action, js?, url_contains?, port}；focus = {hwnd, …}
    敏感窗（_win_sensitive 命中）⇒ obs 不带 win 身份，改由 win_filtered 说明（隐私边界）。
    """
    if not isinstance(p, dict):
        return _cap(kind), "", "", "", ""

    win = p.get("win") or {}
    win_txt = ""
    win_filtered = ""
    if isinstance(win, dict) and win:
        exe, cls = str(win.get("exe") or ""), str(win.get("cls") or "")
        hit, why = _win_sensitive(exe, cls)
        if hit:
            win_filtered = why
        else:
            win_txt = "win={}|{}".format(exe or "?", cls or "?")

    evidence = p.get("evidence") or {}
    result_ext = ""
    if isinstance(evidence, dict) and evidence.get("changed") is not None:
        result_ext = f" changed={evidence.get('changed')}"

    expect = p.get("expect") if isinstance(p.get("expect"), str) else ""
    target = ""
    if kind == "click":
        xy = p.get("xy")
        if isinstance(xy, (list, tuple)) and len(xy) >= 2:
            target = f"xy({xy[0]},{xy[1]})"
        else:
            target = _first_str(p, ("name", "selector", "eid"))
    elif kind in ("type", "fill"):
        target = _first_str(p, ("text",))
        if not target and kind == "fill":
            target = _first_str(p, ("eid",))
    elif kind == "keys":
        target = _first_str(p, ("keys",))
        if p.get("ctrl") and target:
            target = f"ctrl+{target}"
    elif kind == "scroll":
        amount = p.get("amount")
        target = f"amount={amount}" if amount is not None else ""
    elif kind == "browser":
        target = _first_str(p, ("action", "url_contains", "js"))
    elif kind == "focus":
        target = _first_str(p, ("hwnd",))

    if kind in contract.BLOCKED_KINDS:
        reason = p.get("reason") or p.get("blocked_by") or ""
        obs = _cap(f"{p.get('tool') or '?'} 被拦 {reason}", _TEXT_CAP)
        target = target or _first_str(p, ("tool",))
    else:
        parts = [x for x in (str(kind), target, win_txt) if x]
        obs = _cap("; ".join(parts), _TEXT_CAP)
    return obs, target, expect, result_ext, win_filtered


def _parse_t(t_wall: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((t_wall or "").replace("Z", "+00:00"))
    except ValueError:
        return None


class TimelineHarvester:
    """只读 timeline.db，把动作事件提炼成 episode 写入 MemoryStore。"""

    def __init__(self, store, timeline_dir: Optional[str] = None):
        self.store = store
        self.timeline_dir = timeline_dir or default_timeline_dir()
        self._conn: Optional[sqlite3.Connection] = None

    def _db_path(self) -> str:
        return os.path.join(self.timeline_dir, "timeline.db")

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            if not os.path.exists(self._db_path()):
                raise FileNotFoundError(f"timeline.db 不存在: {self._db_path()}")
            # 只读连接：mode=ro ⇒ 物理上不可能写坏真源
            self._conn = sqlite3.connect(
                f"file:{self._db_path()}?mode=ro", uri=True, timeout=5.0
            )
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def pending_count(self) -> int:
        cur = int(self.store.get_meta("harvest_cursor_seq", "0") or 0)
        con = self._connection()
        marks = ",".join("?" for _ in contract.HARVEST_KINDS)
        row = con.execute(
            f"SELECT COUNT(*) FROM events WHERE seq > ? AND kind IN ({marks})",
            (cur, *sorted(contract.HARVEST_KINDS)),
        ).fetchone()
        return int(row[0]) if row else 0

    def harvest(self, *, limit: Optional[int] = None) -> dict:
        """增量提炼。返回统计；幂等可重跑。"""
        con = self._connection()
        cursor = int(self.store.get_meta("harvest_cursor_seq", "0") or 0)
        last_session = self.store.get_meta("last_session_id", "") or ""
        last_t = self.store.get_meta("last_event_t", "") or ""
        last_dt = _parse_t(last_t)

        marks = ",".join("?" for _ in contract.HARVEST_KINDS)
        sql = (
            f"SELECT seq, kind, t_wall, ok, code, payload FROM events "
            f"WHERE seq > ? AND kind IN ({marks}) ORDER BY seq ASC"
        )
        args = [cursor, *sorted(contract.HARVEST_KINDS)]
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))

        rows = con.execute(sql, args).fetchall()
        before = self.store.count_episodes()
        by_kind: dict[str, int] = {}
        max_seq = cursor
        for seq, kind, t_wall, ok, code, payload_txt in rows:
            max_seq = max(max_seq, int(seq))
            try:
                p = json.loads(payload_txt or "{}")
            except (TypeError, ValueError):
                p = {}
            obs, target, expect, result_ext, win_filtered = _payload_brief(kind, p)
            outcome, verdict = contract.verdict_from_code(ok, code)
            if kind in contract.BLOCKED_KINDS:
                # 被拦/异常：动作没发出去，效果无从观测（contract §三值口径）
                outcome, verdict = contract.OUTCOME_BLOCKED, contract.OUTCOME_UNOBSERVABLE

            dt = _parse_t(t_wall)
            gap = None
            if dt is not None and last_dt is not None:
                gap = (dt - last_dt).total_seconds()
            if not last_session or (gap is not None and gap > contract.SESSION_GAP_SEC):
                session_id = contract.make_session_id(t_wall)
            else:
                session_id = last_session
            if dt is not None:
                last_dt, last_t = dt, t_wall
            last_session = session_id

            ep = Episode(
                t=t_wall,
                kind=kind,
                outcome=outcome,
                obs=obs,
                action={"type": kind, "target": target, "cred": {"src_seq": int(seq)}},
                result=f"ok={ok} code={code}{result_ext}",
                verify={
                    "expect": expect,
                    "verdict": verdict,
                    "cred": {"src_seq": int(seq), "ok": ok, "code": code},
                    "win_filtered": win_filtered,
                },
                session_id=session_id,
                src_seq=int(seq),
            )
            self.store.add_episode(ep)
            by_kind[kind] = by_kind.get(kind, 0) + 1

        after = self.store.count_episodes()
        self.store.set_meta("harvest_cursor_seq", str(max_seq))
        if last_session:
            self.store.set_meta("last_session_id", last_session)
        if last_t:
            self.store.set_meta("last_event_t", last_t)
        return {
            "fetched": len(rows),
            "added": after - before,
            "by_kind": by_kind,
            "cursor": max_seq,
        }
