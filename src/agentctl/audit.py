# -*- coding: utf-8 -*-
r"""audit —— 操作者审计导出（纯离线 · 2026-10-25 · 派单 Z2）

把**事件流 / 证据目录 / 判词**读成一份 markdown 审计报告。
纯函数、不接 CLI、只用标准库；**不 import** core / gate / action_guard
（词表与归类表在下面内置一份并注明出处）；除显式 `write_report` 外
**不写任何盘**、不碰桌面、不跑动作、不联网。

四个入口：
    collect(events_path=None, evidence_root=None) -> dict
        读事件 JSONL（缺省 %LOCALAPPDATA%\AgentCtl\state\events.jsonl；
        不存在 ⇒ 事件按空处理并记 notes）与证据目录（缺省 <仓>\work\_actions\*，
        读每个子目录的 evidence.json）。坏行/坏回执不丢：以带 "_error" 的
        占位记录保留，汇总时进「结构存疑」。
    summarize(records) -> dict
        verdict 三态分布、failure_class 四类分布、按窗口/工具计数、
        时间范围、无法复算项清单。
    render_markdown(summary, records, limit=50) -> str
        中文审计报告（含「无法复算项」一节）；空数据 ⇒ 报告写「无记录」。
    write_report(path, text) -> str
        唯一落盘口：显式调用才写，UTF-8。

口径（与 core.py 对齐但**独立内置**——改 core 不漂移本表，责任在判据）：
  ★ 判词三态 = met（生效）/ no_effect（确认没变）/ unknown（没测出来）
      —— 即产品口径 code=0 / 8 / 5（开源声明、源码地图同款）。
      折不进三态的原词（changed_outside_target 世界自己动了、denied 被拒等）
      **不许谎报**，按原词单列「三态之外」。
  ★ failure_class 四类 = rejected_env / unobservable / no_effect / action_failed
      —— 与 core.py FAILURE_CLASS_*（2026-10-25 拆标签后）同表：
      5→unobservable；8→no_effect；3/4/7/9/10/11/12/13→rejected_env；
      其余（含取不到）→action_failed（宁严）。成功不计失败类。
  ★ 无法复算 = 这份报告里没法离线重算给你看的结论：
      像素层判词（依赖前后帧与 diff 算法，需按 recompute_spec 用同一函数重算）、
      结构存疑（解析失败 / 词表外判词 / 时间戳坏）、
      依赖运行时状态的判词（denied 等，依赖租约/前台现场）。
      哈希链（prev_hash/hash）本模块不复核 —— 那是 store.py 的职责。
"""
import json
import os
from datetime import datetime
from pathlib import Path

__all__ = ["VERDICT_THREE", "FAILURE_CLASSES", "collect", "summarize",
           "render_markdown", "write_report"]

#: 三态判词（生效 / 确认没变 / 没测出来）——产品口径 code=0/8/5
VERDICT_THREE = ("met", "no_effect", "unknown")

#: 各路判词 → 三态 的折算表（词面与 core.py `_VERDICT_CANON` 对齐，折点收成三态）
VERDICT_FOLD = {
    # 因果判词（窗Z）
    "met": "met",
    "no_effect_in_region": "no_effect",
    "unknown": "unknown",
    # 闭环判词（T17）
    "satisfied": "met",
    "unsatisfied": "no_effect",
    "snapshots-incomparable": "unknown",
    # 输入判词
    "accepted": "met",
}
#: 已知但**折不进三态**的原词 —— 不谎报，按原词计数
_KNOWN_BEYOND = ("changed_outside_target", "denied")

#: 失败四类（core.py FAILURE_CLASS_*：2026-10-25 拆标签后）
FAILURE_CLASSES = ("rejected_env", "unobservable", "no_effect", "action_failed")

#: code ⇒ 失败类（core.failure_class 同表；取不到 ⇒ action_failed 宁严）
_CODE_TO_FAILURE_CLASS = {
    5: "unobservable", 8: "no_effect",
    3: "rejected_env", 4: "rejected_env", 7: "rejected_env", 9: "rejected_env",
    10: "rejected_env", 11: "rejected_env", 12: "rejected_env", 13: "rejected_env",
}

#: 无法复算项的三个类别
_UNRE_PIXEL = "像素层"
_UNRE_RUNTIME = "运行时状态"
_UNRE_STRUCT = "结构存疑"

#: 因果判词词面（用于无 canonical kind 时猜来源；猜不了按「运行时状态」保守处理）
_CAUSAL_RAW = ("met", "no_effect_in_region", "changed_outside_target", "unknown")

#: 备注/占位条目的列举上限（防大文件把报告撑爆；超出只写总数）
_NOTE_LIMIT = 10


# ---------------------------------------------------------------- 时间解析

def _parse_ts(value):
    """尽力解析时间：ISO 字符串（含 +0800 无冒号、空格分隔）或秒/毫秒 epoch。

    解析不了返回 None —— 调用方计「结构存疑」，**不许猜**。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e14:  # 毫秒 epoch
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = None
    if dt is None:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is not None:  # 统一折到本地 naive 墙钟再比，免得 aware/naive 混比报错
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------- 字段提取

def _dig(rec, *path):
    """按路径下钻 dict，任一层不是 dict 或键缺失 ⇒ None。"""
    cur = rec
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _first_str(*values):
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _verdict_raw(rec):
    """记录里能找到的判词原词（找得到词面的第一处）；找不到 ⇒ None。

    证据回执：顶层 verdict；事件流：payload.verdict / verdict_canonical.value 等。
    """
    return _first_str(
        rec.get("verdict"),
        _dig(rec, "payload", "verdict"),
        _dig(rec, "verdict_canonical", "value"),
        _dig(rec, "payload", "verdict_canonical", "value"),
        _dig(rec, "payload", "evidence", "verdict"),
    )


def _verdict_canon_kind(rec):
    return _first_str(
        _dig(rec, "verdict_canonical", "kind"),
        _dig(rec, "payload", "verdict_canonical", "kind"),
    )


def _window_of(rec):
    """窗口口径：window → payload.win.exe → payload.hwnd → hwnd →「未知窗口」。"""
    w = _first_str(
        rec.get("window"),
        _dig(rec, "payload", "win", "exe"),
    )
    if w:
        return w
    hwnd = rec.get("hwnd")
    if hwnd is None:
        hwnd = _dig(rec, "payload", "hwnd")
    if hwnd is not None:
        return "hwnd:%s" % (hwnd,)
    return "未知窗口"


def _tool_of(rec):
    """工具口径：tool 字段；证据回执的 action 就是工具名。"""
    return _first_str(rec.get("tool"), rec.get("action")) or "未知工具"


def _failure_class_of(rec):
    """事件形记录（有 ok/code 口径）的失败四类。

    返回 (shaped, ok, cls)：
      shaped=False   无失败类口径（如证据回执）⇒ 计「不适用」
      ok=True        成功 ⇒ 不计失败类
      否则 cls 总是四类之一（宁严：code 取不到 ⇒ action_failed）；
      显式标注了词表外的 failure_class ⇒ 原词上交，由调用方记「词表外」。
    """
    shaped = ("ok" in rec) or ("code" in rec)
    if not shaped:
        return (False, None, None)
    if rec.get("ok"):
        return (True, True, None)
    fc = _first_str(rec.get("failure_class"), _dig(rec, "payload", "failure_class"))
    if fc:
        return (True, False, fc)
    try:
        code = int(rec.get("code"))
    except (TypeError, ValueError):
        return (True, False, "action_failed")
    return (True, False, _CODE_TO_FAILURE_CLASS.get(code, "action_failed"))


def _is_pixel_verdict(rec, source):
    """这条判词是不是像素结论（离线不能独立重算）。"""
    if source == "evidence":
        return True  # 证据回执的判词全部来自前后帧 diff
    kind = _verdict_canon_kind(rec)
    if kind == "causal":
        return True
    if kind == "input":
        return False
    raw = _verdict_raw(rec) or ""
    return raw in ("no_effect_in_region", "changed_outside_target")  # 只有因果判词独有词面


def _ref_of(rec, source, index):
    """无法复算项里指认记录的人话引用。"""
    if not isinstance(rec, dict):
        return "记录#%d" % index
    if "_error" in rec:
        if source == "events":
            return "事件流第 %s 行" % (rec.get("_line"),)
        return "证据目录 %s" % (rec.get("_dir"),)
    if source == "events":
        if rec.get("seq") is not None:
            return "事件 seq=%s" % (rec.get("seq"),)
        return "事件#%d" % index
    return "证据 %s" % (rec.get("_dir") or ("#%d" % index),)


# ---------------------------------------------------------------- collect

def _default_events_path():
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        return None
    return Path(la) / "AgentCtl" / "state" / "events.jsonl"


def _default_evidence_root():
    # src/agentctl/audit.py → parents[2] = 仓库根
    return Path(__file__).resolve().parents[2] / "work" / "_actions"


def collect(events_path=None, evidence_root=None):
    r"""读事件 JSONL 与证据目录。只读盘，不写盘。

    - 事件缺省 %LOCALAPPDATA%\AgentCtl\state\events.jsonl；不存在 ⇒ 事件为空 + notes。
    - 证据缺省 <仓>\work\_actions\*；每个子目录读 evidence.json（缺 ⇒ notes 留痕）。
    - 坏行/坏回执以带 "_error" 的占位记录保留（汇总进「结构存疑」），不静默丢。
    返回 {"events": [...], "evidence": [...], "notes": [...], "sources": {...}}。
    """
    notes = []
    events = []
    evidence = []

    # ---- 事件流 ----
    if events_path is None:
        events_path = _default_events_path()
        if events_path is None:
            notes.append("LOCALAPPDATA 未设置 ⇒ 事件流缺省路径不可用，事件按空处理")
    if events_path is None:
        src_events = None
    else:
        src_events = Path(events_path)
        if not src_events.exists():
            notes.append("事件流不存在：%s ⇒ 事件按空处理" % src_events)
        else:
            bad = []
            n_lines = 0
            try:
                text = src_events.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                notes.append("事件流读不了：%s（%s）⇒ 事件按空处理" % (src_events, e))
                text = ""
            for i, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                n_lines += 1
                try:
                    obj = json.loads(line)
                except ValueError as e:
                    bad.append((i, str(e)))
                    continue
                if not isinstance(obj, dict):
                    bad.append((i, "行不是 JSON 对象（%s）" % type(obj).__name__))
                    continue
                obj["_source"] = "events"
                obj["_line"] = i
                events.append(obj)
            if n_lines == 0:
                notes.append("事件流为空（没有非空行）：%s" % src_events)
            for i, err in bad[:_NOTE_LIMIT]:
                events.append({"_source": "events", "_line": i, "_error": err})
                notes.append("事件流第 %d 行解析失败：%s" % (i, err))
            if len(bad) > _NOTE_LIMIT:
                notes.append("…事件流解析失败共 %d 条，备注只列前 %d 条" % (len(bad), _NOTE_LIMIT))

    # ---- 证据目录 ----
    if evidence_root is None:
        evidence_root = _default_evidence_root()
    src_evidence = Path(evidence_root)
    if not src_evidence.exists():
        notes.append("证据目录不存在：%s ⇒ 证据按空处理" % src_evidence)
    else:
        n_dirs = 0
        bad = []
        try:
            children = sorted(src_evidence.iterdir())
        except OSError as e:
            children = []
            notes.append("证据目录读不了：%s（%s）" % (src_evidence, e))
        for child in children:
            if not child.is_dir():
                continue
            n_dirs += 1
            ej = child / "evidence.json"
            if not ej.exists():
                notes.append("证据目录缺 evidence.json：%s" % child.name)
                continue
            try:
                obj = json.loads(ej.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError) as e:
                bad.append((child.name, str(e)))
                continue
            if not isinstance(obj, dict):
                bad.append((child.name, "evidence.json 不是 JSON 对象"))
                continue
            obj["_source"] = "evidence"
            obj["_dir"] = child.name
            evidence.append(obj)
        if n_dirs == 0:
            notes.append("证据目录下没有子目录：%s" % src_evidence)
        for name, err in bad[:_NOTE_LIMIT]:
            evidence.append({"_source": "evidence", "_dir": name, "_error": err})
            notes.append("证据回执解析失败：%s（%s）" % (name, err))
        if len(bad) > _NOTE_LIMIT:
            notes.append("…证据回执解析失败共 %d 条，备注只列前 %d 条" % (len(bad), _NOTE_LIMIT))

    return {
        "events": events,
        "evidence": evidence,
        "notes": notes,
        "sources": {
            "events_path": str(src_events) if src_events is not None else None,
            "evidence_root": str(src_evidence),
        },
    }


# ---------------------------------------------------------------- summarize

def _as_list(v):
    if isinstance(v, (list, tuple)):
        return list(v)
    return []


def _normalize(records):
    """summarize 的入参归一：collect() 的 dict，或一列记录，或空。"""
    if isinstance(records, dict):
        return (_as_list(records.get("events")), _as_list(records.get("evidence")),
                _as_list(records.get("notes")))
    if isinstance(records, (list, tuple)):
        return (list(records), [], [])
    return ([], [], [])


def _sorted_by_count(d):
    return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))


def summarize(records):
    """把 collect() 的产物统计成报告用的摘要 dict（全内存，不读盘）。"""
    events, evidence, notes = _normalize(records)
    pairs = ([("events", r) for r in events] + [("evidence", r) for r in evidence])

    malformed = 0
    success = 0
    three = {k: 0 for k in VERDICT_THREE}
    beyond = {}
    verdict_missing = 0
    four = {k: 0 for k in FAILURE_CLASSES}
    beyond_fc = {}
    failure_na = 0
    by_window = {}
    by_tool = {}
    tmin = tmax = None
    tmin_raw = tmax_raw = None
    time_unparsed = 0
    unre = []

    for idx, (source, rec) in enumerate(pairs):
        ref = _ref_of(rec, source, idx)
        if not isinstance(rec, dict) or "_error" in rec:
            malformed += 1
            why = rec.get("_error") if isinstance(rec, dict) else "记录不是 JSON 对象"
            unre.append({"kind": _UNRE_STRUCT, "ref": ref, "why": "解析失败：%s" % (why,)})
            continue

        # ---- 判词三态 ----
        raw = _verdict_raw(rec)
        if raw is None:
            verdict_missing += 1
        else:
            bucket = VERDICT_FOLD.get(raw)
            if bucket:
                three[bucket] += 1
            else:
                beyond[raw] = beyond.get(raw, 0) + 1
                if raw not in _KNOWN_BEYOND:
                    unre.append({"kind": _UNRE_STRUCT, "ref": ref,
                                 "why": "判词「%s」不在已知词表，三态折叠不上" % raw})
            if _is_pixel_verdict(rec, source):
                unre.append({"kind": _UNRE_PIXEL, "ref": ref,
                             "why": "像素结论（changed/判词）依赖前后帧与 diff 算法"
                                    "（阈值/分块/滤除），离线不能独立重算；"
                                    "需按 recompute_spec 用同一函数重算"})
            else:
                unre.append({"kind": _UNRE_RUNTIME, "ref": ref,
                             "why": "判词依赖租约/前台/目标窗等运行时现场，"
                                    "本报告只计数、不重判"})

        # ---- 失败四类 ----
        shaped, ok, cls = _failure_class_of(rec)
        if not shaped:
            failure_na += 1
        elif ok:
            success += 1
        elif cls in four:
            four[cls] += 1
        else:
            beyond_fc[cls] = beyond_fc.get(cls, 0) + 1
            unre.append({"kind": _UNRE_STRUCT, "ref": ref,
                         "why": "failure_class「%s」不在四类词表" % cls})

        # ---- 窗口 / 工具 ----
        w = _window_of(rec)
        by_window[w] = by_window.get(w, 0) + 1
        t = _tool_of(rec)
        by_tool[t] = by_tool.get(t, 0) + 1

        # ---- 时间范围 ----
        ts_raw = rec.get("ts")
        if ts_raw is None:
            ts_raw = rec.get("t_wall")
        if ts_raw is not None:
            dt = _parse_ts(ts_raw)
            if dt is None:
                time_unparsed += 1
                unre.append({"kind": _UNRE_STRUCT, "ref": ref,
                             "why": "时间戳解析不了：%r" % (ts_raw,)})
            else:
                if tmin is None or dt < tmin:
                    tmin, tmin_raw = dt, ts_raw
                if tmax is None or dt > tmax:
                    tmax, tmax_raw = dt, ts_raw

    return {
        "counts": {"events": len(events), "evidence": len(evidence)},
        "malformed": malformed,
        "verdict_three": three,
        "verdict_beyond": _sorted_by_count(beyond),
        "verdict_missing": verdict_missing,
        "failure_four": four,
        "failure_beyond": _sorted_by_count(beyond_fc),
        "failure_na": failure_na,
        "failure_success_skip": success,
        "by_window": _sorted_by_count(by_window),
        "by_tool": _sorted_by_count(by_tool),
        "time_min": tmin_raw,
        "time_max": tmax_raw,
        "time_unparsed": time_unparsed,
        "unrecomputable": unre,
        "notes": notes,
    }


# ---------------------------------------------------------------- 渲染

_MD_TITLE = "# 操作者审计报告（离线导出）"
_MD_HEAD_NOTE = ("> 纯离线汇总：只读不写盘、不跑动作、不复核哈希链；"
                 "本报告的结论口径见 docs/审计导出_20261025.md。")

_FAILURE_MEANING = {
    "rejected_env": "动作没发出去（没证/没租约/被挡/缺凭据）",
    "unobservable": "看不清（没测出来 / 采样退化，code=5）",
    "no_effect": "发出去了但画面没生效（code=8）",
    "action_failed": "真异常（含判不出的，宁严）",
}
_VERDICT_LABEL = {
    "met": "met（生效）",
    "no_effect": "no_effect（确认没变）",
    "unknown": "unknown（没测出来）",
}


def _table(lines, header, rows):
    lines.append("| %s |" % (" | ".join(header)))
    lines.append("|%s" % ("---|" * len(header)))
    for row in rows:
        lines.append("| %s |" % (" | ".join(str(x) for x in row)))


def _top_table(lines, title, counter, limit, note):
    lines.append("## %s" % title)
    rows = list(counter.items())[:limit]
    if rows:
        _table(lines, ["名", "条数"], rows)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("- 口径：%s" % note)


def render_markdown(summary, records=None, limit=50):
    """渲染中文审计报告。纯拼字符串，不读盘不写盘。

    summary 缺省时可用 records 现算；limit 截 Top-N 与无法复算项的行数。
    """
    try:
        limit = max(0, int(limit))
    except (TypeError, ValueError):
        limit = 50
    s = dict(summary or {})
    if not s:
        s = summarize(records)

    counts = s.get("counts") or {}
    n_events = int(counts.get("events") or 0)
    n_evidence = int(counts.get("evidence") or 0)
    malformed = int(s.get("malformed") or 0)
    unre = list(s.get("unrecomputable") or [])
    notes = list(s.get("notes") or [])

    L = [_MD_TITLE, "", _MD_HEAD_NOTE, ""]
    if n_events + n_evidence == 0:
        L.append("**无记录**：事件流与证据目录都为空（或路径不存在，见「备注」）。")
        L.append("")

    L += ["## 概览", "",
          "- 事件流记录：%d 条（其中解析失败 %d 条）" % (n_events, malformed),
          "- 证据回执：%d 条" % n_evidence,
          "- 无法复算项：%d 条（见「无法复算项」）" % len(unre), ""]

    # ---- 判词三态 ----
    three = s.get("verdict_three") or {}
    beyond = s.get("verdict_beyond") or {}
    L += ["## 判词三态分布（生效 / 确认没变 / 没测出来）", ""]
    _table(L, ["判词", "条数"],
           [(_VERDICT_LABEL.get(k, k), three.get(k, 0)) for k in VERDICT_THREE])
    L.append("")
    if beyond:
        L.append("- 三态之外（原词保留，不折不瞒）：%s"
                 % "、".join("%s × %d" % kv for kv in beyond.items()))
    else:
        L.append("- 三态之外：无")
    L.append("- 未标注（记录里找不到判词）：%d 条" % (s.get("verdict_missing") or 0))
    L.append("")

    # ---- 失败四类 ----
    four = s.get("failure_four") or {}
    beyond_fc = s.get("failure_beyond") or {}
    L += ["## 失败四类分布（口径与 core.failure_class 同表）", ""]
    _table(L, ["类", "条数", "含义"],
           [(k, four.get(k, 0), _FAILURE_MEANING[k]) for k in FAILURE_CLASSES])
    L.append("")
    if beyond_fc:
        L.append("- 词表外 failure_class（原词保留）：%s"
                 % "、".join("%s × %d" % kv for kv in beyond_fc.items()))
    L.append("- 成功不计失败类：%d 条；无 ok/code 口径（如证据回执）不适用：%d 条"
             % (s.get("failure_success_skip") or 0, s.get("failure_na") or 0))
    L.append("")

    # ---- Top-N ----
    _top_table(L, "按窗口 Top-%d" % limit, s.get("by_window") or {}, limit,
               "window 字段 → 事件 payload.win.exe → hwnd（记 hwnd:N）→「未知窗口」")
    L.append("")
    _top_table(L, "按工具 Top-%d" % limit, s.get("by_tool") or {}, limit,
               "tool 字段；证据回执取 action")
    L.append("")

    # ---- 时间范围 ----
    L += ["## 时间范围", "",
          "- 起：%s" % (s.get("time_min") or "无"),
          "- 止：%s" % (s.get("time_max") or "无"),
          "- 时间戳解析失败：%d 条" % (s.get("time_unparsed") or 0), ""]

    # ---- 无法复算项 ----
    L += ["## 无法复算项", ""]
    if unre:
        _table(L, ["类别", "对象", "原因"], [(u.get("kind"), u.get("ref"), u.get("why"))
                                             for u in unre[:limit]])
        L.append("")
        if len(unre) > limit:
            L.append("（共 %d 条，本表只列前 %d 条）" % (len(unre), limit))
    else:
        L.append("- 无：本次没有需要列出的项。")
    L += ["",
          "- 像素层判词不能独立复算：需按 recompute_spec 用**同一函数**重算"
          "（阈值/分块/滤除不在本模块）。",
          "- 哈希链（prev_hash/hash）本模块不复核 —— 那是 store.py 的职责。",
          "- denied 等依赖租约/前台现场的判词：只计数、不重判。", ""]

    # ---- 备注 ----
    L.append("## 备注")
    L.append("")
    if notes:
        for n in notes:
            L.append("- %s" % n)
    else:
        L.append("- 无")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 落盘

def write_report(path, text):
    """唯一的落盘口：显式调用才写。UTF-8、LF；父目录不存在会抛错（不静默建目录）。"""
    p = Path(path)
    p.write_text(text, encoding="utf-8", newline="\n")
    return str(p)
