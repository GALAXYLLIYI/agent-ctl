# -*- coding: utf-8 -*-
"""tasks_dispatch.py —— ctl_task 的处理逻辑（2026-10-06）

设计依据：agent-manual\框架\2026-10-06_面板任务进度.md

★ 为什么单独一个模块 ★
  core.py 已经 900 多行，而且是三态同源的**唯一入口**（HTTP/MCP/CLI 都调它），
  在里面再塞 70 行任务逻辑会让那个文件更难读。
  所以这里只放处理逻辑，依赖（store / env / gate / timeline 路径）由 core 注入。

★ 只有 verify 过闸门 ★
  verify 的含义是「用户确认这个任务真的完成了」。必须由用户在弹窗里确认，
  否则 agent 就能自己给自己盖章 —— 那正是 AGENT准则 铁律 3
  「不采信执行者的自检报告」要防的事。
  其余动作不消耗动作次数：登记任务不是「操作这台电脑」。

★ 全部走同一份 tasks.py ★
  面板和这里共用状态推导 —— 不然两边各写一套，迟早各说各话
  （2026-10-05 面板热键假告警就是那个病的另一个症状）。
"""
from __future__ import annotations

import os

from . import tasks


def handle(args, *, store, env, gate, timeline_dir, actor=""):
    args = args or {}
    action = (args.get("action") or "status").lower()
    db = os.path.join(timeline_dir, "timeline.db")
    who = args.get("actor") or actor
    note = str(args.get("note") or "")

    def _cur():
        return tasks.current(db)

    def _tid():
        """没给 task_id 就用「当前任务」的。**不许静默新建** ——
        新建会让「交付」落到一个用户从没听过的任务上。"""
        cur = _cur()
        if not cur.get("task_id"):
            return None, "当前没有任务，请先 ctl_task action=start"
        if cur.get("state") in (tasks.STATE_DONE, tasks.STATE_FAILED):
            return None, "当前任务已%s，请先 start 下一个" % cur.get("label")
        return cur["task_id"], ""

    if action == "start":
        name = str(args.get("name") or "").strip()
        if not name:
            return env(code=gate.CODE_NO_CTRL, warning="start 必须给 name")
        tasks.start(store(), name, int(args.get("steps") or 0), actor=who, note=note)
        return env(ok=True, code=0, data=_cur())

    if action == "progress":
        tid, err = _tid()
        if err:
            return env(code=gate.CODE_NO_CTRL, warning=err)
        tasks.progress(store(), tid, int(args.get("current") or 0), note=note, actor=who)
        return env(ok=True, code=0, data=_cur())

    if action == "deliver":
        tid, err = _tid()
        if err:
            return env(code=gate.CODE_NO_CTRL, warning=err)
        tasks.deliver(store(), tid, note=note, actor=who)
        return env(ok=True, code=0, data=_cur(),
                   warning="已交付。这不等于完成 —— 面板会显示「待确认」，"
                           "需要用户确认后才变「已完成」。")

    if action == "fail":
        tid, err = _tid()
        if err:
            return env(code=gate.CODE_NO_CTRL, warning=err)
        tasks.fail(store(), tid, why=note, actor=who)
        return env(ok=True, code=0, data=_cur(),
                   warning="已登记失败。失败不播提示音（声音只用来叫人）。")

    if action == "verify":
        try:
            # ⚠ 不能写 gate.B —— gate 模块里**没有 B 这个常量**，只有 SCOPES dict。
            #   写 gate.B 会 AttributeError，而 dispatch 会把它包成「内部错误」,
            #   表面上 ok=False 像是「闸门拒绝了」，实际是**代码崩了** ——
            #   这种假拒最难查（我 2026-10-06 就栽在这，还是自己写的判据太松没抓住）。
            gate.require(gate.SCOPES["B"], "确认任务已完成（ctl_task verify）")
        except gate.GateDenied as e:
            return env(code=e.code, warning="没确认：%s" % e.why)
        tid, err = _tid()
        if err:
            return env(code=gate.CODE_NO_CTRL, warning=err)
        tasks.verify(store(), tid, actor=who, note=note)
        return env(ok=True, code=0, data=_cur())

    if action == "status":
        return env(ok=True, code=0, data=_cur())

    if action == "history":
        n = int(args.get("steps") or 20)
        return env(ok=True, code=0, data={"history": tasks.history(db, n)})

    return env(code=gate.CODE_NO_CTRL, warning="未知 action: %s" % action)
