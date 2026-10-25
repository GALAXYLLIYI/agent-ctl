# -*- coding: utf-8 -*-
r"""skills.py —— 动作库：**技能的结构、校验、回放引擎**（执行器可注入）。

★ 定位：「动作库 = 手脚的记忆」
  一个 **技能** 是一段**可回放的记忆**：在某个应用、某个窗口上，按顺序做几步动作，
  每步都要「找得到」且「做完符合预期」。本模块管的是这段记忆的**形状**与**回放纪律**，
  真正的手脚（点、敲、读）由**注入进来的 executor** 做。

★ 三条硬边界（本单写死，别越界）
  ① **不接真 UIA**：本模块**不 import core / gate**，不发输入、不截图、不碰桌面。
     引擎只认 executor 的三个方法（`locate` / `perform` /（可选）`fingerprint`）——
     换谁进去都行（真 UIA 执行器、假执行器、录像回放器）。
  ② **零 IO**：本模块**不读也不写任何文件**；要存盘自己调 `save_skill(skill, path)`，
     **路径由调用方给**（本模块不认仓内外、不猜路径）。
  ③ **只用标准库**。

★ 回放纪律（本模块的全部价值所在）
  · **定位失败 ⇒ 立即停**：找不到元素还往下走 = 在乱点（仓库铁律：看不清/没测出来
    不许当成"没问题"）。停下时如实报「停在哪一步、为什么」。
  · **expect 不满足 ⇒ 停**：`expect` 是纯数据（元素属性 → 期望值），
    判定由本模块做（不接 UIA ⇒ 判定只做字典比较）。
  · **「没测出来」≠ 成功**：`expect` 要求的属性在元素里**取不到**时，
    判词是「没测出来」而不是「不满足」，更不是「成功」——两种都 `ok=False`。
  · **闸门先行**：`guard` 是可选回调（模拟真实闸门），**在定位之前**调用；
    返回 False ⇒ 一步都不做就停。
  · **窗口指纹对不上 ⇒ 停**（可选）：executor 若提供 `fingerprint()`，
    就必须与技能的 `window_fingerprint` 一致——记忆得用在对的地方。
  · **版本要对得上**：技能带 `version`；回放结果原样回显，便于对账。

★ 已知动作词表 `KNOWN_ACTIONS`
  与 `src/agentctl/action_guard_policy.json` 的 `action_tools`（写动作）**对齐**。
  ★ 这里**刻意不读那个 JSON**：动作库要能离线单测、要能当纯数据存进别的进程，
    运行时去读仓内文件会把它拖回"依赖仓布局"。代价是**词表要靠人同步**——
    改 policy 的 `action_tools` 时这里也得跟着改（见 docs 与交卷的"注意到但没动"）。

用法（调用方先引入本模块 `agentctl.skills`）：
    sk = skills.make_skill(name="提交表单", app="chrome",
                           window_fingerprint="w-1",
                           steps=[{"action": "ctl_click",
                                   "selector": {"name": "提交", "type": "Button"},
                                   "expect": {"visible": True}}])
    skills.validate(sk)                       # 合格返回 None
    r = skills.replay(sk, my_executor)        # r["ok"] / r["steps_done"] / r["total_ms"]
"""
from __future__ import annotations

import json
import os
import time

#: 技能字段顺序（序列化顺序就按这个来；改顺序 = 改格式）。
SKILL_FIELDS = (
    "name",
    "app",
    "window_fingerprint",
    "steps",
    "stats",
    "version",
)

#: 调用方**必须**给的字段（`stats` / `version` 有默认值，`make_skill` 会补）。
REQUIRED_FIELDS = (
    "name",
    "app",
    "window_fingerprint",
    "steps",
    "stats",
    "version",
)

#: 已知动作词表（写动作）。与 action_guard_policy.json 的 `action_tools` 对齐。
KNOWN_ACTIONS = (
    "ctl_click",
    "ctl_type",
    "ctl_fill",
    "ctl_focus",
    "ctl_browser",
)

#: 步骤字段顺序。
STEP_FIELDS = ("action", "selector", "expect", "args")

#: 步骤里必填的字段（`expect` 可为 `None`/空 dict = 只定位不判期望）。
STEP_REQUIRED = ("action", "selector")

#: selector 里至少要有一个非空项（`name` 或 `type`）——两个都空 = 没指名道姓 = 定位不到。
SELECTOR_KEYS = ("name", "type")

#: 技能版本（当前结构版本；改结构就 +1，回放时与旧技能对不上时要能一眼看出）。
SKILL_VERSION = 1

#: 判词全集（回放结果里的 `verdict` 只会是这些值之一）。
VERDICTS = (
    "完成",
    "定位失败",
    "动作失败",
    "期望不满足",
    "没测出来",
    "闸门拒绝",
    "窗口指纹不匹配",
)

#: `expect` 里比对的元素属性没有取到时用的哨兵（区别于「取到了但是 None」）。
_MISSING = object()


def _typename(value) -> str:
    """给报错用的类型名（中文报错里要让人一眼看出给的是什么）。"""
    return type(value).__name__


def _is_int(value) -> bool:
    """`bool` 是 `int` 的子类，统计类字段要单独排掉它。"""
    return isinstance(value, int) and not isinstance(value, bool)


# ══════════════════════════════════════════════════════════════════════
# 校验
# ══════════════════════════════════════════════════════════════════════
def _check_selector(where: str, selector) -> None:
    """selector 必须是 dict，且 `name` / `type` 至少有一个非空。"""
    if not isinstance(selector, dict):
        raise ValueError("%s 的 selector 必须是 dict，给的是 %s" % (where, _typename(selector)))
    unknown = [k for k in selector if k not in SELECTOR_KEYS]
    if unknown:
        raise ValueError("%s 的 selector 有不认识的字段：%s（可写：%s）"
                         % (where, "、".join(sorted(unknown)), " / ".join(SELECTOR_KEYS)))
    if not any(str(selector.get(k) or "").strip() for k in SELECTOR_KEYS):
        raise ValueError("%s 的 selector 是空的（name / type 至少给一个）" % where)


def _check_step(where: str, step) -> None:
    """一步的形状：action 必填且须是已知动作；selector 必填且非空；expect 可空。"""
    if not isinstance(step, dict):
        raise ValueError("%s 必须是 dict，给的是 %s" % (where, _typename(step)))
    missing = [k for k in STEP_REQUIRED if k not in step]
    if missing:
        raise ValueError("%s 缺必填字段：%s" % (where, "、".join(missing)))
    action = step["action"]
    if not isinstance(action, str) or not action.strip():
        raise ValueError("%s 的 action 必须是非空 str，给的是 %s" % (where, _typename(action)))
    if action not in KNOWN_ACTIONS:
        raise ValueError("%s 的 action 未知：%r（已知动作：%s）"
                         % (where, action, "、".join(KNOWN_ACTIONS)))
    _check_selector(where, step["selector"])
    if "expect" in step and step["expect"] is not None:
        if not isinstance(step["expect"], dict):
            raise ValueError("%s 的 expect 必须是 dict 或 None，给的是 %s"
                             % (where, _typename(step["expect"])))
    # ★ 2026-10-25：可选 args（动作参数，如 ctl_type 的 text）。缺省 = 该动作没有参数。
    #   为什么要加：结构里原先只有 action/selector/expect，"打字打什么"没处放
    #   ⇒ 技能永远回放不了输入类动作（只测了点击）。
    if "args" in step and step["args"] is not None:
        if not isinstance(step["args"], dict):
            raise ValueError("%s 的 args 必须是 dict 或 None，给的是 %s"
                             % (where, _typename(step["args"])))


def _check_stats(where: str, stats) -> None:
    """统计块：runs / ok / avg_ms 都要是数（bool 不算数）。"""
    if not isinstance(stats, dict):
        raise ValueError("%s 必须是 dict，给的是 %s" % (where, _typename(stats)))
    for k in ("runs", "ok", "avg_ms"):
        if k not in stats:
            raise ValueError("%s 缺字段：%s" % (where, k))
        v = stats[k]
        if k == "avg_ms":
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        else:
            ok = _is_int(v)
        if not ok:
            raise ValueError("%s 的 %s 必须是数字，给的是 %s" % (where, k, _typename(v)))
    if stats["runs"] < 0 or stats["ok"] < 0:
        raise ValueError("%s 的 runs / ok 不许为负" % where)
    if stats["ok"] > stats["runs"]:
        raise ValueError("%s 的 ok（%r）不许大于 runs（%r）" % (where, stats["ok"], stats["runs"]))


def validate(skill) -> None:
    """校验一个技能：合格返回 `None`，不合格抛 `ValueError`（中文、指明字段名）。

    检查顺序：是不是 dict → 有没有未知字段 → 缺不缺必填 → 字段类型/值域 → 每一步。
    """
    if not isinstance(skill, dict):
        raise ValueError("技能必须是 dict，给的是 %s" % _typename(skill))
    unknown = [k for k in skill if k not in SKILL_FIELDS]
    if unknown:
        raise ValueError("技能里有不认识的字段：%s（可写字段见 skills.SKILL_FIELDS）"
                         % "、".join(sorted(unknown)))
    missing = [k for k in REQUIRED_FIELDS if k not in skill]
    if missing:
        raise ValueError("技能缺必填字段：%s" % "、".join(missing))
    for k in ("name", "app"):
        v = skill[k]
        if not isinstance(v, str) or not v.strip():
            raise ValueError("技能字段 %s 必须是非空 str，给的是 %s" % (k, _typename(v)))
    fp = skill["window_fingerprint"]
    if not isinstance(fp, str):
        raise ValueError("技能字段 window_fingerprint 必须是 str，给的是 %s" % _typename(fp))
    if not _is_int(skill["version"]):
        raise ValueError("技能字段 version 必须是 int，给的是 %s" % _typename(skill["version"]))
    if skill["version"] < 1:
        raise ValueError("技能字段 version 必须 >= 1，给的是 %r" % (skill["version"],))
    steps = skill["steps"]
    if not isinstance(steps, list):
        raise ValueError("技能字段 steps 必须是 list，给的是 %s" % _typename(steps))
    if not steps:
        raise ValueError("技能字段 steps 不许为空（没有步骤就不是技能）")
    for i, step in enumerate(steps):
        _check_step("技能第 %d 步" % (i + 1), step)
    _check_stats("技能字段 stats", skill["stats"])


def make_skill(*, name: str, app: str, window_fingerprint: str = "",
               steps=None, version: int = SKILL_VERSION) -> dict:
    """造一个技能（**唯一**的合法入口）→ 返回按 `SKILL_FIELDS` 排好序的 dict。

    `stats` 默认 `{runs:0, ok:0, avg_ms:0}`（新技能还没跑过；**不许默认吹牛**）。
    造完一律过 `validate()`。
    """
    if steps is None:
        steps = []
    skill = {
        "name": name,
        "app": app,
        "window_fingerprint": window_fingerprint,
        "steps": list(steps),
        "stats": {"runs": 0, "ok": 0, "avg_ms": 0},
        "version": version,
    }
    validate(skill)
    return {k: skill[k] for k in SKILL_FIELDS}


# ══════════════════════════════════════════════════════════════════════
# 回放
# ══════════════════════════════════════════════════════════════════════
def _call_locate(executor, selector) -> dict:
    """让 executor 定位元素；返回规整后的 `{ok, element, reason}`。"""
    out = executor.locate(selector)
    if not isinstance(out, dict):
        raise ValueError("executor.locate 必须返回 dict（{ok, element, reason}），给的是 %s"
                         % _typename(out))
    ok = bool(out.get("ok"))
    element = out.get("element")
    reason = str(out.get("reason") or "")
    if not ok and not reason:
        reason = "定位失败（executor 没给原因）"
    return {"ok": ok, "element": element, "reason": reason}


def _call_perform(executor, action, element, args=None) -> dict:
    """让 executor 执行动作；返回规整后的 `{ok, reason}`。

    ★ 2026-10-25：executor 可以只接受 (action, element)（老签名），
      也可以用 (action, element, args) 接收动作参数（如 ctl_type 的 text）。
      这里按签名自适应，**不要求 executor 改签名**。
    """
    try:
        import inspect
        n = len(inspect.signature(executor.perform).parameters)
    except (TypeError, ValueError):
        n = 2
    out = executor.perform(action, element, args) if n >= 3 else executor.perform(action, element)
    if not isinstance(out, dict):
        raise ValueError("executor.perform 必须返回 dict（{ok, reason}），给的是 %s"
                         % _typename(out))
    ok = bool(out.get("ok"))
    reason = str(out.get("reason") or "")
    if not ok and not reason:
        reason = "动作失败（executor 没给原因）"
    return {"ok": ok, "element": out.get("element", element), "reason": reason}


def _check_expect(expect, element) -> tuple:
    """比对期望 → 返回 (是否满足, 判词, 原因)。

    ★ 「没测出来」与「不满足」**分开**：
      · 属性**取不到**（元素里没这个键）⇒ 判词「没测出来」；
      · 属性**取到了但对不上** ⇒ 判词「期望不满足」。
      两种都 `ok=False` —— **绝不把「没测出来」当成功**。
    """
    if not expect:
        return True, "完成", ""
    if not isinstance(element, dict):
        return False, "没测出来", "元素不是属性字典（拿不到 expect 要的字段）"
    for key, want in expect.items():
        got = element.get(key, _MISSING)
        if got is _MISSING:
            return False, "没测出来", "元素里没有属性 %r（没测出来，不是没变）" % (key,)
        if got != want:
            return False, "期望不满足", "属性 %r 期望 %r，实测 %r" % (key, want, got)
    return True, "完成", ""


def _stop(result, verdict, reason, index, t0):
    """收尾一个失败结果（总耗时必带）。"""
    result["ok"] = False
    result["verdict"] = verdict
    result["reason"] = reason
    result["step"] = index
    result["step_no"] = None if index is None else index + 1
    result["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    return result


def replay(skill, executor, guard=None) -> dict:
    """回放一个技能 → 返回结果 dict。

    结果字段：
      · `ok`          —— 全过才是 True
      · `verdict`     —— 判词（见 `VERDICTS`）
      · `reason`      —— 中文原因（停在哪、为什么）
      · `step`        —— 停住的那一步的索引（**0 基**；全过时为 None）
      · `step_no`     —— 同上但 **1 基**（给人读）
      · `steps_done`  —— 已经跑完（定位+执行+期望全过）的步数
      · `total_ms`    —— 总耗时（毫秒）
      · `steps`       —— 每步明细

    流程（每一步都按这个顺序，**先定位后动手**）：
      1. `guard(skill, step)`（可选）—— 返回 False ⇒ 停（一步都不做）
      2. `executor.locate(selector)` —— 失败 ⇒ **立即停**
      3. `executor.perform(action, element)` —— 失败 ⇒ 停
      4. 比对 `expect` —— 不满足 / 没测出来 ⇒ 停
    """
    validate(skill)
    if executor is None:
        raise ValueError("replay 必须给 executor（没有执行器就没有回放）")
    for meth in ("locate", "perform"):
        if not hasattr(executor, meth):
            raise ValueError("executor 缺方法 %s（回放至少要能定位、能执行）" % meth)

    t0 = time.perf_counter()
    result = {
        "ok": False, "verdict": "完成", "reason": "", "step": None, "step_no": None,
        "steps_done": 0, "total_ms": 0.0, "steps": [],
        "skill": {"name": skill["name"], "app": skill["app"],
                  "window_fingerprint": skill["window_fingerprint"],
                  "version": skill["version"]},
    }

    # ── 窗口指纹对不上 ⇒ 停（executor 支持 fingerprint() 时才查）──
    if hasattr(executor, "fingerprint"):
        try:
            cur_fp = executor.fingerprint()
        except Exception as e:                                   # pragma: no cover
            return _stop(result, "窗口指纹不匹配", "取窗口指纹失败：%s: %s"
                         % (type(e).__name__, e), None, t0)
        want_fp = skill["window_fingerprint"]
        if want_fp and str(cur_fp) != str(want_fp):
            return _stop(result, "窗口指纹不匹配",
                         "窗口指纹对不上：技能记的是 %r，现场是 %r（记忆用错了地方）"
                         % (want_fp, cur_fp), None, t0)

    steps = skill["steps"]
    for i, step in enumerate(steps):
        detail = {"index": i, "action": step["action"], "selector": step["selector"],
                  "expect": step.get("expect"), "ok": False, "reason": "", "ms": 0.0}
        ts = time.perf_counter()

        # ① 过闸（可选）：拒绝 ⇒ 一步都不做
        if guard is not None:
            try:
                allowed = guard(skill, step)
            except Exception as e:
                detail["reason"] = "闸门回调抛异常：%s: %s" % (type(e).__name__, e)
                detail["ms"] = round((time.perf_counter() - ts) * 1000.0, 2)
                result["steps"].append(detail)
                return _stop(result, "闸门拒绝", detail["reason"], i, t0)
            if not allowed:
                detail["reason"] = "闸门拒绝（guard 返回 False）"
                detail["ms"] = round((time.perf_counter() - ts) * 1000.0, 2)
                result["steps"].append(detail)
                return _stop(result, "闸门拒绝", detail["reason"], i, t0)

        # ② 定位：失败 ⇒ 立即停（不许在没找着的东西上动手）
        try:
            loc = _call_locate(executor, step["selector"])
        except Exception as e:
            detail["reason"] = "定位调用出错：%s: %s" % (type(e).__name__, e)
            detail["ms"] = round((time.perf_counter() - ts) * 1000.0, 2)
            result["steps"].append(detail)
            return _stop(result, "定位失败", detail["reason"], i, t0)
        if not loc["ok"]:
            detail["reason"] = loc["reason"]
            detail["ms"] = round((time.perf_counter() - ts) * 1000.0, 2)
            result["steps"].append(detail)
            return _stop(result, "定位失败",
                         "第 %d 步定位失败：%s（已停，不再往下走）" % (i + 1, loc["reason"]), i, t0)

        # ③ 执行
        try:
            perf = _call_perform(executor, step["action"], loc["element"], step.get("args"))
        except Exception as e:
            detail["reason"] = "执行调用出错：%s: %s" % (type(e).__name__, e)
            detail["ms"] = round((time.perf_counter() - ts) * 1000.0, 2)
            result["steps"].append(detail)
            return _stop(result, "动作失败", detail["reason"], i, t0)
        if not perf["ok"]:
            detail["reason"] = perf["reason"]
            detail["ms"] = round((time.perf_counter() - ts) * 1000.0, 2)
            result["steps"].append(detail)
            return _stop(result, "动作失败",
                         "第 %d 步动作失败：%s" % (i + 1, perf["reason"]), i, t0)

        # ④ 期望：没测出来 / 不满足 ⇒ 停（都不许当成功）
        ok_exp, verdict, why = _check_expect(step.get("expect"), perf["element"])
        detail["ms"] = round((time.perf_counter() - ts) * 1000.0, 2)
        detail["ok"] = ok_exp
        detail["reason"] = why
        result["steps"].append(detail)
        if not ok_exp:
            return _stop(result, verdict,
                         "第 %d 步%s：%s" % (i + 1, verdict, why), i, t0)
        result["steps_done"] += 1

    result["ok"] = True
    result["verdict"] = "完成"
    result["reason"] = "全部 %d 步完成" % len(steps)
    result["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    return result


# ══════════════════════════════════════════════════════════════════════
# 从动作日志提炼技能
# ══════════════════════════════════════════════════════════════════════
def record_from(steps_log, name: str, app: str, fingerprint: str = "") -> dict:
    """从**动作日志**提炼一个技能（纯数据，不碰界面）。

    `steps_log` 是执行器写出来的动作流水，每项形如：
        {"action": "ctl_click",
         "selector": {"name": "提交", "type": "Button"},
         "expect": {"visible": True},   # 可选
         "ok": True,                    # 可选，缺省按 True（成功的动作才留）
         "ms": 120}                     # 可选，用于算 avg_ms

    规则：
      · **只留成功的步骤**（`ok=False` 的不写进技能——没做成的动作不是「记忆」）；
      · 保持原顺序（动作的顺序就是记忆的顺序）；
      · `stats` = `{runs:1, ok:1, avg_ms:平均每步耗时}` ——
        ★ `ok` 的语义是「**成功跑过几次**」（≤ `runs`），**不是**「记了几步」；
          步数在 `len(skill["steps"])` 里，不许塞进 `stats`（塞进去就和 `ok<=runs` 打架）；
      · 提炼出来的技能**必须过 `validate()`**（日志里有未知 action ⇒ 直接报错，
        不许悄悄攒出一个跑不起来的技能）。

    用法：
        sk = record_from(log, name="提交表单", app="chrome", fingerprint="w-1")
    """
    if not isinstance(steps_log, list):
        raise ValueError("动作日志必须是 list，给的是 %s" % _typename(steps_log))
    kept = []
    total_ms = 0.0
    for i, entry in enumerate(steps_log):
        where = "动作日志第 %d 项" % (i + 1)
        if not isinstance(entry, dict):
            raise ValueError("%s 必须是 dict，给的是 %s" % (where, _typename(entry)))
        if not entry.get("ok", True):
            continue                      # 失败的动作不进技能
        step = {"action": entry.get("action"),
                "selector": entry.get("selector"),
                "expect": entry.get("expect") or {}}
        ms = entry.get("ms")
        if isinstance(ms, (int, float)) and not isinstance(ms, bool):
            total_ms += float(ms)
        kept.append(step)
    if not kept:
        raise ValueError("动作日志里没有成功的动作，提炼不出技能")
    avg_ms = round(total_ms / len(kept), 2) if kept else 0.0
    skill = {
        "name": name,
        "app": app,
        "window_fingerprint": fingerprint,
        "steps": kept,
        # ★ `ok` = 成功**跑过几次**（≤ runs）；「记了几步」看 len(steps)，不塞进 stats
        "stats": {"runs": 1, "ok": 1, "avg_ms": avg_ms},
        "version": SKILL_VERSION,
    }
    validate(skill)
    return {k: skill[k] for k in SKILL_FIELDS}


# ══════════════════════════════════════════════════════════════════════
# 存盘（路径由调用方给；本模块不猜路径、不读仓内布局）
# ══════════════════════════════════════════════════════════════════════
def save_skill(skill, path: str) -> str:
    """把技能写成 JSON 到 `path`（**路径由调用方给**）→ 返回该路径。

    写之前一定过 `validate()`：不合格的技能不许落盘。
    """
    validate(skill)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({k: skill[k] for k in SKILL_FIELDS}, fh, ensure_ascii=False, indent=2)
    return path


def load_skill(path: str) -> dict:
    """从 `path` 读回一个技能（读回来一定过 `validate()`）→ 返回技能 dict。"""
    with open(path, "r", encoding="utf-8") as fh:
        skill = json.load(fh)
    validate(skill)
    return {k: skill[k] for k in SKILL_FIELDS}
