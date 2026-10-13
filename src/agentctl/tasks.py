# -*- coding: utf-8 -*-
"""tasks.py —— 任务登记与状态推导

设计依据：agent-manual\框架\2026-10-06_面板任务进度.md

★ 一条硬约束决定了本模块的全部写法 ★
  timeline.db 是**只追加 + 哈希链**（store.append 里 body["hash"] = compute_hash）。
  所以任务状态**不能「更新」，只能追加事件**。
   这就是为什么 kind 不能只有 created / delivered / verified 那三种：
   没有 task_progress 事件，就没法表达「做到第 3 步了」；
   硬塞进 created 的 payload 就等于**篡改已经入链的历史**。
   同理，失败也得能表达（task_failed）——否则面板上只能看见「停在中间态」，
   分不出「失败了」还是「不停止」。
   ★ 实际 KINDS 共 6 种（见下方常量），本备注 2026-10-07 更正：
     原写「5 种」（当时 task_failed 还没加）。

★ 三态，不是两态 ★
  「进行中 / 完成」两态会自证：agent 自己写 delivered，面板显示「完成」，
  那就是执行者给自己盖章（违反 AGENT准则 铁律 3「不采信执行者的自检报告」）。
  所以中间必须有**不属于我判断**的一档：

      running   有 created，无 delivered     谁说的：我
      pending   有 delivered，无 verified    谁说的：我（只代表交出去了）
      done      有 verified                  谁说的：用户

★ 读接口一律只读 URI ★
  用普通 sqlite3.connect 打开一个不存在的库**会创建空库**（等于写盘）。
  mode=ro 是纯读，不存在就直接报错 —— 这正是我们要的降级行为。
"""
from __future__ import annotations

import sqlite3
import time

KINDS = ("task_created", "task_claimed", "task_progress",
         "task_delivered", "task_verified", "task_failed")

# 面板三态的显示样式。**颜色语义必须错开**（框架第 10 节 V10）：
# 「已交付待确认」本质上不是成功，用绿色会直接制造混淆。
STATE_NONE = {"icon": "—", "label": "无任务", "color": "dim"}
STATE_RUNNING = {"icon": "●", "label": "进行中", "color": "accent"}
STATE_PENDING = {"icon": "◆", "label": "待确认", "color": "warn"}
STATE_DONE = {"icon": "✓", "label": "已完成", "color": "good"}
STATE_FAILED = {"icon": "✗", "label": "失败", "color": "bad"}
STATE_UNREADABLE = {"icon": "？", "label": "读不到", "color": "dim"}


# ───────────── 写（唯一入口是 store.append，绕过它写库＝链断）─────────────
def start(store, name, steps=0, *, actor="", note=""):
    """登记新任务。返回事件（含 task_id，后续事件都用它对齐）。"""
    lt = time.localtime()
    tid = "t%02d%02d-%06d" % (lt.tm_mon, lt.tm_mday, int(time.time() * 1000) % 1000000)
    return store.append(
        "task_created", actor=actor, tool="ctl_task", ok=True, code=0,
        payload={"task_id": tid, "name": name, "steps": int(steps), "note": note})


def claim(store, task_id, *, actor=""):
    return store.append("task_claimed", actor=actor, tool="ctl_task", ok=True,
                        code=0, payload={"task_id": task_id})


def progress(store, task_id, current, *, note="", actor=""):
    """汇报进度。**current 是「第几步」，不是百分比**。

    为什么不存百分比：百分比是**算出来的**，而 events 是只追加的。
    存百分比就会出现「同一个任务有两个不同进度百分比」的记录，
    读的时候得猜哪个是真的；存步数就没这个问题。
    """
    return store.append("task_progress", actor=actor, tool="ctl_task", ok=True,
                        code=0,
                        payload={"task_id": task_id, "current": int(current),
                                 "note": note})


def deliver(store, task_id, *, note="", actor=""):
    return store.append("task_delivered", actor=actor, tool="ctl_task", ok=True,
                        code=0, payload={"task_id": task_id, "note": note})


def verify(store, task_id, *, actor="", note=""):
    """**只有用户能调。** 这条写进去，面板才从「待确认」变「已完成」。

    所以调用方（core.dispatch）必须走闸门 B 级，由用户在弹窗里确认，
    不接受任何「agent 自称用户」的快捷方式。
    """
    return store.append("task_verified", actor=actor, tool="ctl_task", ok=True,
                        code=0, payload={"task_id": task_id, "note": note})


def fail(store, task_id, *, why="", actor=""):
    """登记失败。**失败不发完成提示音**（框架 V9）。
    声音只用来叫人，不用来播报状态。

    ★ 为什么这里的 code 是 0 而不是 1（2026-10-14 S11 记）★
      「调用成功」和「任务失败」是**两层**：这次登记**调用本身**成功了 —— 写这行的
      同一次调用（tasks_dispatch 的 fail 分支）返回的信封也是 code=0；失败的是**任务**，
      由 kind=task_failed 承载。状态推导只读 kind（本文件 current() 里那句
      `if "task_failed" in kinds`，它连 code 列都没查）。同模块其余 5 个写入口一律 code=0，
      本行原来是唯一异类。**该列此处仅为时间线检索用。**
      ⚠️ **别把任务状态编码进 code**：那会让读 ctl_timeline 的人把它当成信封码，去 error_codes
      里查 —— 两张码表都没有它。DSH 已裁定：不收进 event_codes（13 是资源阈值，1 是任务状态，不同维）。
    """
    return store.append("task_failed", actor=actor, tool="ctl_task", ok=False,
                        code=0, payload={"task_id": task_id, "why": why})


# ───────────── 读（纯只读，降级不崩）─────────────
def _ro(db_path):
    """只读连接。文件不存在会报错而不是建空库 —— 这正是要的降级。"""
    p = str(db_path).replace("\\", "/")
    return sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=1.0)


def _blanks(state, why=""):
    return dict(state, task_id="", name="", steps=0, current=0,
                elapsed_sec=0, need_me=False, since_wall="", why=why)


def current(db_path, limit=400):
    """推导当前任务状态。**只读**，任何异常都归成 unreadable 而不是抛。

    `need_me=True` 是面板**唯一该出声**的条件，
    即「有 delivered 没有 verified」，此时需要用户确认。
    其余任何状态都不出声（框架第 10 节：声音只用来叫人）。
    """
    try:
        con = _ro(db_path)
    except Exception as e:
        return _blanks(STATE_UNREADABLE, "%s: %s" % (type(e).__name__, e))
    try:
        ph = ",".join("?" * len(KINDS))
        rows = con.execute(
            "SELECT seq,kind,t_wall,payload FROM events WHERE kind IN (%s) "
            "ORDER BY seq DESC LIMIT ?" % ph, (*KINDS, limit)).fetchall()
    except Exception as e:
        return _blanks(STATE_UNREADABLE, "查询失败 %s: %s" % (type(e).__name__, e))
    finally:
        con.close()

    import json
    evs = []
    for seq, kind, t_wall, payload in rows:
        try:
            p = json.loads(payload) if payload else {}
        except Exception:
            p = {}
        evs.append((seq, kind, t_wall, p if isinstance(p, dict) else {}))
    evs.sort(key=lambda r: r[0])

    created = None
    for e in evs:
        if e[1] == "task_created":
            created = e
    if not created:
        return _blanks(STATE_NONE)

    tid = created[3].get("task_id") or ""
    mine = [e for e in evs if e[1].startswith("task_")
            and (e[3].get("task_id") in ("", tid) or not tid)]
    kinds = [e[1] for e in mine]

    steps = int(created[3].get("steps") or 0)
    cur = 0
    for e in mine:
        if e[1] == "task_progress":
            cur = max(cur, int(e[3].get("current") or 0))
    if "task_failed" in kinds:
        state = STATE_FAILED
    elif "task_verified" in kinds:
        state = STATE_DONE
    elif "task_delivered" in kinds:
        state = STATE_PENDING
    else:
        state = STATE_RUNNING

    elapsed = 0.0
    try:
        import datetime as dt
        t0 = dt.datetime.fromisoformat(created[2])
        elapsed = (dt.datetime.now().astimezone() - t0.astimezone()).total_seconds()
    except Exception:
        elapsed = 0.0        # 时间算不出来不影响判状态，标 0 不猜

    return dict(state, task_id=tid, name=created[3].get("name") or "",
                steps=steps, current=cur, elapsed_sec=int(elapsed),
                since_wall=created[2] or "", need_me=(state is STATE_PENDING))


def history(db_path, limit=20):
    """最近的任务事件（面板第二行用）。降级返回空列表。"""
    try:
        con = _ro(db_path)
    except Exception:
        return []
    try:
        ph = ",".join("?" * len(KINDS))
        rows = con.execute(
            "SELECT seq,kind,t_wall,payload FROM events WHERE kind IN (%s) "
            "ORDER BY seq DESC LIMIT ?" % ph, (*KINDS, limit)).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    import json
    out = []
    for seq, kind, t_wall, payload in rows:
        try:
            p = json.loads(payload) if payload else {}
        except Exception:
            p = {}
        out.append({"seq": seq, "kind": kind, "t_wall": t_wall,
                    "name": (p or {}).get("name", "")})
    return out
