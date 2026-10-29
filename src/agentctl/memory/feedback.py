# -*- coding: utf-8 -*-
r"""agentctl.memory.feedback —— 反哺引擎（2026-10-30 晨 · 「适配 agentctl 的引擎」）

把这两天在量化循环里验证过的模式泛化成**三行接线**的引擎级组件：
    fb = FeedbackEngine(store)
    ctx = fb.before_task(task, query_text)      # 循环头：结构化召回（义务，非号召）
    fb.after_task(task, kind=..., obs=..., outcome=...)   # 任务尾：去重回灌（promote 收尾）

内嵌的口径全部来自已验证的实验与验收：
    · 召回：memory.recall.recall——分仓（默认 agent 仓）+ 分数地板 + 去重（含兄弟结局）
      + 出处强制 + 饥饿豁免（shown_before）+ 召回失败不拦业务（E1/E2/E4、DSH P2-2/P2-3）
    · 回灌：intake.record_experience——dedup_key 合并（subject+outcome 入键，先败后成算
      新信息）+ 口径自愈（v5/v6）
    · 巩固：promote_every 自动收尾（写读耦合规则，ZCode 本体实验实锤）
    · A/B：轮次级交替（ab_cycle，默认 2 对照 / 2 召回），RECALL_OFF 环境变量总开关——
      「先查记忆」是结构不是号召（harness 设计律）

谁在用：night_loop_v2（量化循环）为第一个适配客户端；任何 agentctl 宿主照 docs/feedback_engine_v1.md
三行接入。fail-open 纪律：引擎的任何失败都降级为空结果/标记，绝不拦业务。
"""
from __future__ import annotations

import os
from collections import deque
from datetime import datetime
from typing import Any, Optional

from agentctl.memory import contract

__all__ = ["FeedbackEngine"]

_DEFAULT_SESSION = "s-agent-feedback"


def _mode_for_round(round_no: int, ab_cycle: tuple[int, int]) -> str:
    """A/B 轮次级交替：ab_cycle=(对照轮数, 召回轮数)。round 从 1 计。"""
    off_n, on_n = ab_cycle
    period = max(1, off_n + on_n)
    idx = (round_no - 1) % period
    return "control" if idx < off_n else "recall"


class FeedbackEngine:
    """反哺引擎：循环头召回 + 任务尾回灌 + A/B 交替 + 自动巩固。"""

    def __init__(
        self,
        store: Any,
        *,
        subject: str = contract.SUBJECT_AGENT,
        actor: Optional[str] = None,
        session_id: str = "",
        k: int = 3,
        floor: float = 0.10,
        shown_max: int = 60,
        ab_cycle: tuple[int, int] = (2, 2),
        promote_every: int = 10,
        disabled: Optional[bool] = None,
    ):
        self.store = store
        self.subject = subject
        self.actor = actor
        self.session_id = session_id or _DEFAULT_SESSION
        self.k = int(k)
        self.floor = float(floor)
        self.ab_cycle = (int(ab_cycle[0]), int(ab_cycle[1]))
        self.promote_every = int(promote_every)
        if disabled is None:
            disabled = os.environ.get("RECALL_OFF", "").strip() not in ("", "0")
        self.disabled = bool(disabled)
        self._shown: deque[int] = deque(maxlen=int(shown_max))
        self._round = 0
        self._writes = 0
        self.history: list[dict] = []   # [{round, task, mode, n, written}]

    # ── 循环头 ──────────────────────────────────────────────────────────────

    def begin_round(self) -> str:
        """推进轮号并返回本轮 A/B 模式（"control" / "recall"）。"""
        self._round += 1
        return _mode_for_round(self._round, self.ab_cycle)

    def before_task(self, task_name: str, query_text: str,
                    k: Optional[int] = None, round_tag: str = "") -> dict:
        """任务执行前调用。ctx = {"mode","recalled","n","hits","block","round"}；
        绝不抛异常（召回失败不拦业务）。"""
        try:
            mode = _mode_for_round(self._round or 1, self.ab_cycle)
            if self.disabled or self._round == 0:
                # 未 begin_round ⇒ 视为 control（义务未建立就别给记忆）
                mode = "control"
            if self.disabled or mode == "control":
                ctx = {"mode": mode, "recalled": False, "n": 0, "hits": [],
                       "block": "", "round": self._round}
            else:
                from agentctl.memory.recall import recall as _recall
                rec = _recall(self.store, query_text,
                              k=k or self.k, subject=self.subject, floor=self.floor,
                              exclude_ids=list(self._shown))
                for h in rec.get("hits", []):
                    hid = h.get("id")
                    if hid is not None and int(hid) not in self._shown:
                        self._shown.append(int(hid))
                ctx = {"mode": mode, "recalled": rec.get("recalled", False),
                       "n": rec.get("n", 0), "hits": rec.get("hits", []),
                       "block": rec.get("block", ""), "round": self._round}
            self.history.append({"round": self._round, "task": task_name,
                                 "mode": ctx["mode"], "n": ctx["n"], "written": None})
            return ctx
        except Exception as exc:  # noqa: BLE001 —— fail-open
            return {"mode": "error", "recalled": False, "n": 0, "hits": [],
                    "block": "", "round": self._round, "error": repr(exc)}

    # ── 任务尾 ──────────────────────────────────────────────────────────────

    def after_task(
        self,
        task_name: str,
        *,
        kind: str,
        obs: str,
        outcome: str,
        result: str = "",
        verify_cred: Optional[dict] = None,
        target: Optional[str] = None,
        actor: Optional[str] = None,
        dedup: Optional[bool] = None,
    ) -> dict:
        """任务执行后调用（义务回灌）。返回 {"written", "id"/"error", ...}；绝不抛异常。"""
        try:
            from agentctl.memory.intake import record_experience
            cred = {"session": self.session_id, "task": task_name,
                    "round": self._round}
            cred.update(verify_cred or {})
            res = record_experience(
                self.store,
                kind=kind,
                obs=obs,
                outcome=outcome,
                result=result,
                verify={"cred": cred},
                session_id=self.session_id,
                subject=self.subject,
                actor=actor or self.actor,
                action={"type": str(kind), "target": target or task_name},
                t=datetime.now().astimezone().isoformat(timespec="seconds"),
                dedup=dedup,
            )
            self._writes += 1
            promoted = None
            if self.promote_every > 0 and self._writes % self.promote_every == 0:
                from agentctl.memory.compact import Compactor
                promoted = Compactor(self.store).promote()
            if self.history:
                self.history[-1]["written"] = bool(res.get("created"))
            out = {"written": True, **res}
            if promoted is not None:
                out["promoted_until"] = promoted.get("promoted_until")
            return out
        except Exception as exc:  # noqa: BLE001 —— fail-open
            return {"written": False, "error": repr(exc)}

    # ── 账本 ────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """运行账本（供日志/对账/验收）。"""
        n_recall = sum(1 for h in self.history if h["mode"] == "recall")
        n_control = sum(1 for h in self.history if h["mode"] == "control")
        n_written = sum(1 for h in self.history if h.get("written"))
        return {"round": self._round, "recall_rounds": n_recall,
                "control_rounds": n_control, "writes": self._writes,
                "written_tasks": n_written, "shown_buffer": len(self._shown),
                "disabled": self.disabled}
