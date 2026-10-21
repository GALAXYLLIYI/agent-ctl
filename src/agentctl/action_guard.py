# -*- coding: utf-8 -*-
r"""action_guard.py —— **动作面**异常刹车（连续 N 次阈值判据）

================================ 本模块是什么 ================================
内存/资源那一侧早就有这套东西了：legacy_tools\guardian.py
    :123  _GROW_STREAK = {}      # pid → 连续超阈值的采样次数（防一次性尖峰误报）
    :216  if _GROW_STREAK[pid] < 2:   # 一次性尖峰不算：要**连续两次采样**都超才算
那边的判据是「一个数（MB/分、句柄数）连续两次采样都超线」。

**动作面当时是空的**：连点同一个坐标、连续失败、短时高频，只能靠人眼看。
本模块把那套「连续才作数」的精神**原样搬过来**，只是被盯的量从「资源」
换成「动作计数」。**没有另发明一套情绪** —— 三条判据、同一套两级响应。

================================ 三条判据（都可配）==============================
    same_target_streak  同一 actor 连续对同一 坐标/eid/name 动作 N 次（默认 5）
    fail_streak         同一 actor 连续失败 N 次（默认 6，成功一次清零）
    rate_per_min        单 actor 每分钟动作数 > M（默认 60）

★ 阈值一律从 action_guard_policy.json 读（路径可用环境变量
  AGENTCTL_ACTION_GUARD_POLICY 覆盖），**代码里没有一个写死的阈值**。

================================ 两级响应（不许一上来就全停）====================
    第一次命中  ⇒ 只写事件(kind=action_anomaly, stage=warn) + 面板出提示，
                  **一个字节都不碰闸门**；
    连续第二次命中 ⇒ 拉急停（gate.set_brake → BRAKE_FLAG），
                  事件里写明 by="action-anomaly" + 哪条阈值 + 计数 + 目标 + 时间窗。

★ 「动辄就停」是这套东西最大的失败模式：一个瞬时抖动就掐死全机器，
  用户会直接把整个系统关掉。所以：证据不足 ⇒ 不停；每一次触发都能在
  事件流里查到**阈值名、计数、目标、时间窗**四项。

★ 为什么闸门动作放在这里而不是 core 里直接调 set_brake：
  判据逻辑**只有这一处**（项目纪律）。core 只负责在动作出口调 observe()。
"""
from __future__ import annotations

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))

#: 事件 kind。写进 timeline.jsonl（store 是真相源），事件流里查得到每一次触发。
KIND = "action_anomaly"
#: ★ P0-1（2026-10-21）：环境性拒绝**只留痕不拉闸**，单独一个 kind。
#:   为什么单独一个 kind：它不是「异常」，是「这次没轮到动作上场」；
#:   混进 action_anomaly 会让「一级提示 = 有异常」这条口径失真。
KIND_BLOCKED = "action_blocked"
#: 留痕里的 by 值 —— 谁干的。判据第 4 条钉的就是这个字符串。
BY = "action-anomaly"

#: 配置写坏/读不到时的兜底值。**只在这一份文件读不到时用**，不是「默认行为口径」
#: —— 正常运行永远读 action_guard_policy.json（判据第 7 条会核这件事）。
_FALLBACK = {
    "enabled": True,
    "same_target_streak": 5,
    "fail_streak": 6,
    "rate_per_min": 60,
    "rate_window_sec": 60,
    "streak_window_sec": 300,
    "hits_to_brake": 2,
    "hint_ttl_sec": 600,
    "action_tools": ["ctl_click", "ctl_type", "ctl_fill", "ctl_focus", "ctl_browser"],
    # ★ P0-1（2026-10-21）：哪些失败性质**计入** fail_streak。
    #   默认只算「动作发出去了但没生效」与「真异常」——
    #   环境性拒绝（没证 / 没租约 / 被挡 / 缺凭据 / 前台不对）重试无意义，不该算信号。
    "fail_counted_classes": ["no_effect", "action_failed"],
    "blocked_event": True,
}


# ────────────────────────── 配置：读文件，不硬编码 ──────────────────────────
def policy_path() -> str:
    """配置在哪。优先级：环境变量 > 模块旁边的 json。

    ⚠ 为什么不从 contract\tools.json 读：那是**工具契约**（工具清单/schema），
      往里塞阈值会把「模型看得见的接口」和「运维要调的参数」搅在一起。
      项目里「给人审的阈值文件」的老规矩是 guardian_policy.json 那一种，
      这里沿用同一形状。
    """
    p = os.environ.get("AGENTCTL_ACTION_GUARD_POLICY")
    if p:
        return p
    return os.path.join(HERE, "action_guard_policy.json")


_cache = {"key": None, "pol": dict(_FALLBACK)}


def load_policy(path: str | None = None) -> dict:
    """读阈值。**按 mtime+size 缓存**：改完配置下一次动作就生效，但不会每次调用都解析一遍。

    ⚠ 单测里要换口径时走 reset_policy_cache()，别直接改这个字典。
    """
    p = path or policy_path()
    try:
        stt = os.stat(p)
        key = (p, stt.st_mtime_ns, stt.st_size)
    except OSError:
        key = None
    if key is not None and key == _cache["key"]:
        return _cache["pol"]
    pol = dict(_FALLBACK)
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k in list(pol.keys()):
                if k in raw and raw[k] is not None:
                    pol[k] = raw[k]
    except Exception:
        # 配置读不到 ⇒ 退回兜底值。**不假装「按配置运行」**：下面的 policy_source()
        # 会把「这次是兜底」这件事如实说出来。
        pol = dict(_FALLBACK)
        key = None
    _cache["key"], _cache["pol"] = key, pol
    return pol


def policy_source() -> dict:
    """现在这份阈值是哪来的（给面板与判据看的自证）。"""
    p = policy_path()
    ok = os.path.isfile(p)
    return {"path": p, "from_file": ok, "thresholds": load_policy(p)}


def reset_policy_cache() -> None:
    """测试与「刚改完配置就想立刻重读」用。"""
    _cache["key"], _cache["pol"] = None, dict(_FALLBACK)


# ────────────────────────── 目标：坐标 / eid / name ──────────────────────────
def target_key(args: dict) -> str:
    """一次动作打在哪儿 —— 判据的「同一目标」就按这个字符串比。

    ★ 为什么要显式区分而不是一律用 xy：eid 路径里坐标是**这次复核出来的**，
      同一个按钮每次复核出来的像素可能有 1px 抖动；一律用 xy 会让
      「连点同一个按钮」看起来像在换目标 ⇒ 判据永远不命中。
    ★ 没有坐标也没有 eid/name 时回落到 hwnd（ctl_focus 这类就是按窗口算）。
      真的什么都没有 ⇒ 空串：这类动作只进 rate/fail 判据，**不进同目标判据**
      （「连着对同一个未知目标动手」这个说法本身就不成立）。
    """
    a = args or {}
    if a.get("eid") is not None:
        return "eid:%s:%s" % (a.get("hwnd") or 0, a.get("eid"))
    if a.get("name"):
        return "name:%s" % a.get("name")
    xy = a.get("xy")
    if xy:
        try:
            x, y = (list(xy) + [0, 0])[:2]
            return "xy:%d,%d" % (int(float(x)), int(float(y)))
        except Exception:
            return "xy:%s" % (xy,)
    if a.get("hwnd"):
        return "hwnd:%s" % a.get("hwnd")
    return ""


def hint_path() -> str:
    """面板提示落在哪 —— 与闸门状态同目录（项目既有数据目录约定）。"""
    app = os.environ.get("AGENTCTL_APP_DIR")
    if not app:
        try:
            from agentctl import gate
            app = gate.APP_DIR
        except Exception:
            app = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "AgentCtl")
    return os.path.join(app, "_动作异常提示.json")


def _write_hint(payload: dict) -> str:
    p = hint_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = "%s.tmp.%d" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        # 提示写不出去**不许**把动作带崩 —— 它只是给人看的旁证，事件才是留痕。
        return ""
    return p


def hint_view(pol: dict | None = None) -> dict | None:
    """面板读这个：当前有没有一条**一级**提示（没有就 None）。只读文件，不计数。"""
    pol = pol or load_policy()
    p = hint_path()
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    ttl = float(pol.get("hint_ttl_sec") or 600)
    if time.time() - float(d.get("t_mono") or 0) > ttl:
        return None
    return d


# ────────────────────────── 计数器（进程内状态） ──────────────────────────
_state = {
    "same": {},     # (actor, target) -> {n 连击数, last 最近一次时间}
    "fail": {},     # actor -> {n 连续失败数, last}
    "rate": {},     # actor -> [时间戳, ...]
    "hits": {},     # (判据名, actor|目标) -> 连续命中次数（两级响应就靠它）
    "blocked": {},  # actor -> {n 环境性拒绝次数, last, class}（★ P0-1：只留痕不计数）
}


def reset_state() -> None:
    """清空所有计数。测试隔离用；生产里**没有**人调它（计数理应跨调用累计）。"""
    for k in ("same", "fail", "rate", "hits", "blocked"):
        _state[k] = {}


def _drop_stale(now: float, win: float) -> None:
    """把超过时间窗的计数丢掉 —— 否则计数只增不减，早晚误报。"""
    for k in ("same", "fail", "hits", "blocked"):
        for key, rec in list(_state[k].items()):
            if now - float(rec.get("last") or 0) > win:
                _state[k].pop(key, None)


# ────────────────────────── 判据本体 ──────────────────────────
class ActionGuard:
    r"""动作面异常刹车。observe() 是唯一入口，返回本次的命中记录（没命中返回 None）。

    ★ emit / brake 都可注入：判据 tests/_P2_动作面刹车判据.py 用它在隔离目录里
      跑**完整链路**（事件真落盘、急停旗子真写），而不碰本机真状态。
    """

    def __init__(self, *, emit=None, brake=None, path: str | None = None):
        self._emit = emit
        self._brake = brake
        self._path = path

    # ── 默认依赖（懒解析，避免与 core 的循环 import）────────────────────
    def _emit_event(self, payload: dict, kind: str | None = None) -> None:
        if self._emit is not None:
            self._emit(payload)
            return
        try:
            from agentctl import core
            core._ev(kind or KIND, actor=payload.get("actor") or "",
                     tool=payload.get("tool") or "", payload=payload,
                     ok=(payload.get("stage") == "warn"),
                     code=0 if payload.get("stage") == "warn" else 11)
        except Exception:
            pass        # 留痕失败不许把动作带崩（动作已经发出去了）

    def _pull_brake(self, payload: dict) -> tuple[bool, str]:
        if self._brake is not None:
            return self._brake(payload)
        try:
            from agentctl import gate
            return gate.set_brake(
                "动作面异常（by=%s）：%s 连续命中 %s 次 —— 阈值 %s=%s，目标 %s"
                % (BY, payload.get("threshold_name"), payload.get("count"),
                   payload.get("threshold_name"), payload.get("threshold_value"),
                   payload.get("target")))
        except Exception as e:
            return False, "拉急停失败：%s: %s" % (type(e).__name__, e)

    # ── 主入口 ──────────────────────────────────────────────────────────
    def observe(self, *, actor: str = "", tool: str = "", args: dict | None = None,
                ok: bool = True, failure_class: str | None = None) -> dict | None:
        """一次动作调一次。返回本次触发的证据（没触发 ⇒ None，**不影响动作本身**）。

        ★ P0-1（2026-10-21）新增 failure_class：
          「失败」不是一种东西 —— 环境性拒绝（没证/没租约/被挡/缺凭据/前台不对）
          **重试没有意义**，不该算进「agent 在乱点」的信号；真正该急停的是
          「动作发出去了但没生效」(no_effect) 与「真异常」(action_failed)。
          failure_class=None ⇒ **按旧口径计数**（向后兼容：老调用方不传这个参数时，
          行为与改前完全一致）。
        """
        pol = load_policy(self._path)
        if not pol.get("enabled", True):
            return None
        if tool not in (pol.get("action_tools") or []):
            return None

        now = time.time()
        win = float(pol.get("streak_window_sec") or 300)
        _drop_stale(now, win)

        actor = actor or "unknown"
        args = args or {}
        tgt = target_key(args)

        # ── ★ P0-1（2026-10-21）：这次失败**算不算数**，先算清楚 ──
        #   counted = True  ⇒ 动作**真的发出去了**（成功 / 发出去了没生效 / 真异常）
        #                    ⇒ 进 fail_streak，也进同目标连击
        #   counted = False ⇒ 环境性拒绝（没证 / 没租约 / 被挡 / 缺凭据 / 前台不对）
        #                    ⇒ **一个像素都没发出去**：重试它没有意义，
        #                      更不该被算成「agent 在乱点」的信号
        #   failure_class=None ⇒ 调用方没给性质 ⇒ **按旧口径**（counted=True，向后兼容）
        counted_classes = pol.get("fail_counted_classes")
        if not isinstance(counted_classes, (list, tuple)):
            counted_classes = _FALLBACK.get("fail_counted_classes")
        if failure_class is None:
            counted = True
        else:
            counted = str(failure_class) in [str(x) for x in (counted_classes or [])]

        # ── 判据 1：同目标连击 ──
        hit_tgt = ""
        if tgt:
            key = (actor, tgt)
            rec = _state["same"].get(key)
            # ★ 中间断过别的目标就算「不连续」：同一个 actor 换了目标再换回来，
            #   不许把两段连击拼成一段（拼接＝误报，而这正是最招人烦的那种停机）。
            prev = _state["same"].get((actor, "#last"))
            if prev and prev.get("t") != tgt:
                for k in [k for k in _state["same"]
                          if k[0] == actor and k != (actor, "#last")]:
                    _state["same"].pop(k, None)
            # ★ P0-1：动作**没发出去**的尝试不算「连点同一处」——
            #   连点的危险信号是「agent 在同一个点上反复戳」，
            #   而被闸门当场拦下（压根没戳）恰恰是闸门在正常工作。
            if counted:
                n = ((rec.get("n") or 0) + 1) if rec else 1
                _state["same"][key] = {"n": n, "last": now}
            else:
                n = (rec.get("n") or 0) if rec else 0
            _state["same"][(actor, "#last")] = {"n": 0, "last": now, "t": tgt}
            if counted and n >= int(pol.get("same_target_streak") or 5):
                hit_tgt = tgt

        # ── 判据 2：连续失败（成功一次即清零）──
        #   ★ P0-1（2026-10-21）：**按失败性质分流**。
        #     counted  = 真失败（动作发出去了但没生效 / 真异常）⇒ 计进 fail_streak
        #     别的     = 环境性拒绝（没证 / 没租约 / 被挡 / 缺凭据 / 前台不对）
        #                ⇒ 不加、也不清零（它既不是成功、也不是「乱点」的证据）
        #     None    = 调用方没传性质 ⇒ **按旧口径计数**（向后兼容）
        rec = _state["fail"].get(actor)
        if ok:
            fail_n = 0
        elif counted:
            fail_n = ((rec.get("n") or 0) + 1) if rec else 1
        else:
            fail_n = (rec.get("n") or 0) if rec else 0
        _state["fail"][actor] = {"n": fail_n, "last": now}
        hit_fail = fail_n >= int(pol.get("fail_streak") or 6)

        # ── 环境性拒绝：只留痕，不进任何计数器（更不拉闸）──
        #   为什么留痕：它仍然是「这次没成」的事实，事后要能查到；
        #   为什么不拉闸：拉闸的理由是「agent 在乱点」，而它不是。
        if (not ok) and (not counted) and failure_class:
            _state["blocked"][actor] = {"n": (_state["blocked"].get(actor, {}).get("n") or 0) + 1,
                                        "last": now, "class": failure_class}
            if pol.get("blocked_event", True):
                try:
                    self._emit_event({
                        "by": BY, "stage": "blocked", "threshold_name": "failure_class",
                        "failure_class": failure_class, "actor": actor, "tool": tool,
                        "target": tgt or "（无具体目标）",
                        "count": _state["blocked"][actor]["n"],
                        "note": "环境性拒绝：动作一个像素都没发出去 ⇒ 不计入 fail_streak",
                    }, kind=KIND_BLOCKED)
                except Exception:
                    pass

        # ── 判据 3：短时高频 ──
        rw = float(pol.get("rate_window_sec") or 60)
        q = [t for t in _state["rate"].get(actor, []) if now - t <= rw]
        q.append(now)
        _state["rate"][actor] = q
        rate_n = len(q)
        hit_rate = rate_n > int(pol.get("rate_per_min") or 60)

        # ── 三条都命中时，按「证据最硬」的那条报（同目标 > 连续失败 > 高频）──
        #   为什么排序：同目标与连续失败是**行为特征**；高频可能只是任务本身碎。
        if hit_tgt:
            name, val = "same_target_streak", pol.get("same_target_streak")
            cnt = _state["same"][(actor, tgt)]["n"]
            target, window = tgt, win
        elif hit_fail:
            name, val, cnt = "fail_streak", pol.get("fail_streak"), fail_n
            target, window = (tgt or "（无具体目标）"), win
        elif hit_rate:
            name, val, cnt = "rate_per_min", pol.get("rate_per_min"), rate_n
            target, window = "（全部目标）", rw
        else:
            return None

        # ★ 两级响应：连续第 1 次命中只提示，第 2 次才拉闸。
        #   ⚠ 同目标判据的「命中计数」**按目标分组**，另两条按 actor 分组 ——
        #     为什么：同目标的第二次命中必须是「同一个点又连点了 5 下」，
        #     换了目标就重新算（否则两次不相干的连点会被拼成「连续两次」⇒ 误停）；
        #     而连续失败/高频的证据本来就是「这个 actor 整体在崩/在刷」，
        #     目标每次都变，若也按目标分组就永远凑不出第二次 ⇒ **两级形同虚设**
        #     （这正是改前想修的缺口，不能自己再破一次）。
        hkey = (name, actor, target if name == "same_target_streak" else "*")
        hits = (_state["hits"].get(hkey, {}).get("n") or 0) + 1
        _state["hits"][hkey] = {"n": hits, "last": now}
        need = max(2, int(pol.get("hits_to_brake") or 2))
        stage = "warn" if hits < need else "brake"

        payload = {
            "by": BY,
            "stage": stage,
            "hits": hits,
            "hits_to_brake": need,
            "threshold_name": name,
            "threshold_value": val,
            "count": cnt,
            "actor": actor,
            "tool": tool,
            "target": target,
            "window_sec": int(window),
            "t_wall": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        payload["why"] = (
            "%s：%s 连续命中第 %d 次（阈值 %s=%s，实际 %s，目标 %s，窗口 %ds）—— %s"
            % (BY, name, hits, name, val, cnt, target, int(window),
               "已写事件并在面板提示，未动闸门" if stage == "warn"
               else "第二次连续命中，已拉急停 BRAKE_FLAG"))

        if stage == "brake":
            bok, bmsg = self._pull_brake(payload)
            payload["brake_ok"] = bool(bok)
            payload["brake_msg"] = bmsg
            if not bok:
                # 拉不下急停是**事实**，不许粉饰成「已停」。
                payload["stage"] = "brake_failed"
        self._emit_event(payload)
        _write_hint({
            **payload,
            "t_mono": time.time(),
            "text": ("动作面异常（%s）：%s 连续 %d 次 / 阈值 %s=%s\n目标 %s · %s · 窗口 %ds\n%s"
                     % ("未拉闸" if payload["stage"] == "warn" else "已拉急停",
                        name, cnt, name, val, target, tool, int(window),
                        "只记事件、未动闸门；再连续命中一次才会停。"
                        if payload["stage"] == "warn"
                        else "已拉下急停，确认安全后手动解除。")),
        })
        return payload


_guard = None


def guard() -> ActionGuard:
    """进程内单例。core 的动作出口调它 —— 判据逻辑只有一处就在这里。"""
    global _guard
    if _guard is None:
        _guard = ActionGuard()
    return _guard
