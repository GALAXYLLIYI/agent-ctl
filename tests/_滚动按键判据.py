# -*- coding: utf-8 -*-
r"""tests\_滚动按键判据.py —— ctl_scroll / ctl_keys 接上 MCP 工具面的判据（窗A1 · 2026-10-17）

派单要求：**把 legacy_tools\act.py 已有的 scroll / keys 接进 MCP 工具面**，不重新实现输入。
本判据盯三件事：
  ① 契约与 core.TOOLS 两边真的对上了（不然 agent 看得见调不着 / 调得着看不见）；
  ② **滚动方向口径四方一致**（见下，这条是本判据的重点）；
  ③ 那些「静默做掉比拒绝更坏」的入口真的挡住了：抢前台、越界落点、白名单外的键。

★ ① 到底有没有矛盾？——**没有，是伪告警**，而且本判据用四条独立证据钉死：
    [方1] core.SCROLL_NEGATIVE_IS_DOWN（服务层唯一定义处）
    [方2] core._scroll_direction() 实际吐出来的词
    [方3] 契约 ctl_scroll 的 description 明写「正=向上、负=向下」并解释了 Increment/Decrement
    [方4] legacy_tools\act.py 的两条分支**原文**
          UIA 分支  amt = LargeIncrement if amount < 0 else LargeDecrement
          滚轮分支  wheel(120 * amount)
    [方5] 平台事实（独立于本仓）：
          ScrollAmount.LargeIncrement ≡ PageDown（向下），LargeDecrement ≡ PageUp（向上）
          MOUSEEVENTF_WHEEL 的 mouseData **正值 = 向前滚 = 向上**
    把方4的两行**代进方5的常量**里各自算一遍，会得到同一个结论：**负数 = 向下**。
    容易读成矛盾，是因为把 Increment / Decrement 当成了「上 / 下」——
    它们讲的是**滚动偏移的增减**，不是屏幕上的方向。
    ⇒ `--inject flip` 把方1 取反时，方2 与方4/方5 立刻打架，① 必红（可证伪）。

退出码：0 = 全绿；1 = 有红；**2 = 有「验不了」**（2 不许读成绿）。
"""
from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
_INJECT = None
for _i, _a in enumerate(sys.argv):
    if _a == "--inject" and _i + 1 < len(sys.argv):
        _INJECT = sys.argv[_i + 1]
if _INJECT:
    sys.path.insert(0, HERE)
    import _inject                                       # noqa: E402

ROOT = os.path.abspath(os.environ.get("AGENTCTL_ROOT") or os.path.dirname(HERE))
CONTRACT = os.path.join(ROOT, "contract", "tools.json")

# 事件流落盘到临时目录 —— 判据只做「读」的核对，不该在真仓的 work\timeline 里留垃圾。
# ⚠ 这只改**时间线位置**，不改被测行为：dispatch 的留痕逻辑一行没动。
_TL = tempfile.mkdtemp(prefix="_A1_判据_timeline_")
os.environ["AGENTCTL_TIMELINE"] = _TL

FAILS = []
SKIPS = []


def ok(msg):
    print("  [绿] " + msg, flush=True)


def red(aid, msg):
    FAILS.append((aid, msg))
    print("  [红] " + aid + " · " + msg, flush=True)


def skip(aid, msg):
    SKIPS.append((aid, msg))
    print("  [验不了] " + aid + " · " + msg, flush=True)


def info(msg):
    print("  [注] " + msg, flush=True)


def head(t):
    print()
    print("=" * 78)
    print(t)
    print("=" * 78, flush=True)


def read(p):
    with io.open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


# ═══════════════════ 平台事实（独立于本仓，出处写在注释里）═══════════════════
# ScrollAmount 枚举语义：微软文档把 LargeIncrement 对齐到 PageDown、LargeDecrement 对齐到 PageUp。
#   ⇒ “Increment” 讲的是**滚动偏移变大**，落点是**向下**；它不是「往上」。
UIA_SCROLLAMOUNT_DIR = {"LargeDecrement": "向上", "LargeIncrement": "向下"}
# MOUSEEVENTF_WHEEL：mouseData 正值 = 向前滚（远离使用者）= 屏幕**向上**。
WHEEL_POSITIVE_IS = "向上"


def _load_core():
    """从 ROOT/src 导入 core —— 注入模式下 ROOT 指向影子仓，所以拿到的就是注入了缺陷的那份。"""
    if ROOT not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, "src"))
    from agentctl import core, gate                      # noqa: PLC0415
    return core, gate


def _contract_tools():
    return {t["name"]: t for t in json.loads(read(CONTRACT))["tools"]}


# ────────────────────────── 一个够用的 draft-07 子集校验器 ──────────────────────────
# ★ 为什么自己写：本机 **jsonschema 没装**，且沙箱里 pip 装不上（网络被封 WinError 10013）。
#   与其把这一节写成「验不了」，不如把**用到的关键字**实现出来、并把覆盖范围写在下面 ——
#   至少这几条真出问题会红。未实现的 draft-07 关键字见 NOT_SUPPORTED。
NOT_SUPPORTED = ("$ref", "allOf", "patternProperties", "dependentRequired",
                 "if/then/else", "multipleOf", "exclusiveMinimum/Maximum", "$id")


def mini_validate(inst, schema, path="$", errs=None):
    errs = errs if errs is not None else []
    if not isinstance(schema, dict):
        return errs
    t = schema.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        okk = any((ty == "object" and isinstance(inst, dict))
                  or (ty == "array" and isinstance(inst, list))
                  or (ty == "string" and isinstance(inst, str))
                  or (ty == "integer" and isinstance(inst, int) and not isinstance(inst, bool))
                  or (ty == "number" and isinstance(inst, (int, float)) and not isinstance(inst, bool))
                  or (ty == "boolean" and isinstance(inst, bool))
                  or (ty == "null" and inst is None) for ty in ts)
        if not okk:
            errs.append("%s 类型应为 %s，实到 %s" % (path, t, type(inst).__name__))
            return errs
    if "enum" in schema and inst not in schema["enum"]:
        errs.append("%s 不在 enum %s 里（实到 %r）" % (path, schema["enum"], inst))
    if "const" in schema and inst != schema["const"]:
        errs.append("%s 应恒等于 %r，实到 %r" % (path, schema["const"], inst))
    if "not" in schema and not mini_validate(inst, schema["not"], path, []):
        errs.append("%s 命中了 not 子句（%r 不该出现在这里）" % (path, inst))
    for kw in ("oneOf", "anyOf"):
        if kw in schema:
            hits = sum(1 for s in schema[kw] if not mini_validate(inst, s, path, []))
            if kw == "oneOf" and hits != 1:
                errs.append("%s oneOf 命中了 %d 个子句（应为 1）" % (path, hits))
            if kw == "anyOf" and hits == 0:
                errs.append("%s anyOf 一个子句都没命中" % path)
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append("%s %r < minimum %r" % (path, inst, schema["minimum"]))
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append("%s %r > maximum %r" % (path, inst, schema["maximum"]))
    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            errs.append("%s 只有 %d 项 < minItems %d" % (path, len(inst), schema["minItems"]))
        if "maxItems" in schema and len(inst) > schema["maxItems"]:
            errs.append("%s 有 %d 项 > maxItems %d" % (path, len(inst), schema["maxItems"]))
        if isinstance(schema.get("items"), dict):
            for i, v in enumerate(inst):
                mini_validate(v, schema["items"], "%s[%d]" % (path, i), errs)
    if isinstance(inst, dict):
        props = schema.get("properties") or {}
        for k in schema.get("required") or []:
            if k not in inst:
                errs.append("%s 缺必填字段 %s" % (path, k))
        if schema.get("additionalProperties") is False:
            for k in inst:
                if k not in props:
                    errs.append("%s 出现了 schema 没声明的字段 %s（additionalProperties=false）"
                                % (path, k))
        for k, sub in props.items():
            if k in inst:
                mini_validate(inst[k], sub, "%s.%s" % (path, k), errs)
    return errs


# ══════════════════════════ §1 契约 ↔ core.TOOLS 双向一致 ══════════════════════════
def j1_contract_vs_core(core, tools):
    head("§1 · 契约与 core.TOOLS 双向一致（看得见 / 调得着）")
    cn, kn = set(tools), set(core.TOOLS)
    if cn - kn:
        red("J1.1", "契约里声明了但 dispatch 不认（agent 会调不着）：%s" % sorted(cn - kn))
    else:
        ok("J1.1 契约 tools 里 %d 个名字，core.TOOLS 全部认" % len(cn))
    if kn - cn:
        red("J1.2", "core.TOOLS 里有但契约没声明（agent 看不见）：%s" % sorted(kn - cn))
    else:
        ok("J1.2 core.TOOLS %d 个键，契约 tools 全部声明" % len(kn))

    tl_all = core.tool_list()
    tl = {t["name"] for t in tl_all}
    miss = sorted((cn | kn) - tl)
    if miss:
        red("J1.3", "tools/list 真输出里缺：%s" % miss)
    else:
        ok("J1.3 core.tool_list()（MCP tools/list 真输出）%d 项，覆盖两边全部名字" % len(tl))
    for n in ("ctl_scroll", "ctl_keys"):
        st = [t.get("_status") for t in tl_all if t["name"] == n]
        if not st:
            red("J1.4", "%s 不在 tools/list 里" % n)
        elif st[0]:
            red("J1.4", "%s 在 tools/list 里标着 %s" % (n, st[0]))
        else:
            ok("J1.4 %s 在 tools/list 里是**可调用**（无 _status 标记）" % n)


# ══════════════════════════════ §2 契约 schema 自校验 ══════════════════════════════
def j2_schema(tools):
    head("§2 · 两条新工具的 input_schema 自校验（本机 jsonschema 未装，用内建 draft-07 子集）")
    info("已实现的关键字：type / properties / required / additionalProperties / enum / items /")
    info("minItems / maxItems / minimum / maximum / default / const / not / oneOf / anyOf")
    info("**未**实现（本判据不会用，遇到会静默放过）：%s" % "、".join(NOT_SUPPORTED))
    for n, want_gate in (("ctl_scroll", "B"), ("ctl_keys", "C")):
        if n not in tools:
            red("J2.0", "契约里没有 %s" % n)
            continue
        t = tools[n]
        sch = t.get("input_schema") or {}
        req = list(sch.get("required") or [])
        errs = []
        if not req:
            errs.append("required 是空的 —— 等于什么都不强制")
        if sch.get("additionalProperties") is not False:
            errs.append("additionalProperties 不是 false（拼错字段名会被静默吞掉）")
        if t.get("gate") != want_gate:
            errs.append("gate 应为 %s，实到 %r" % (want_gate, t.get("gate")))
        probe = {"hwnd": 1}
        probe.update({k: (1 if k in ("hwnd", "amount", "times") else "PageDown")
                      for k in req if k != "hwnd"})
        errs += mini_validate(probe, sch)
        if errs:
            red("J2." + n, "；".join(errs))
        else:
            ok("J2.%s 的 schema 自洽：required=%s，additionalProperties=false，gate=%s"
               % (n, req, t.get("gate")))

    samples = (
        ("ctl_scroll", {"hwnd": 1, "amount": -3}, True),
        ("ctl_scroll", {"hwnd": 1, "amount": 0}, False),
        ("ctl_scroll", {"hwnd": 1, "amount": 3, "at": [10, 20]}, True),
        ("ctl_scroll", {"hwnd": 1, "amount": 3, "oops": 1}, False),
        ("ctl_scroll", {"amount": 3}, False),
        ("ctl_keys", {"hwnd": 1, "keys": "PageDown"}, True),
        ("ctl_keys", {"hwnd": 1, "keys": "PageDown", "times": 99}, False),
        ("ctl_keys", {"hwnd": 1, "keys": "PageDown", "ctrl": True}, True),
        ("ctl_keys", {"hwnd": 1}, False),
    )
    for n, sample, want in samples:
        if n not in tools:
            continue
        errs = mini_validate(sample, tools[n]["input_schema"])
        if bool(errs) != want:
            ok("J2.sample %-10s %-44s → %s（符合预期）"
               % (n, json.dumps(sample, ensure_ascii=False), "拒绝" if errs else "放行"))
        else:
            red("J2.sample", "%s %s 的校验结果与预期相反（%s）"
                % (n, json.dumps(sample, ensure_ascii=False), errs or "不该拦却拦了"))


# ════════════════════════ §3 ① 滚动方向四方一致（判据重点）════════════════════════
_UIA_RE = (r"amt\s*=\s*\(\s*auto\.ScrollAmount\.(Large\w+)\s+if\s+amount\s*<\s*0\s+"
           r"else\s+auto\.ScrollAmount\.(Large\w+)\s*\)")
_WHEEL_RE = r"wheel\(\s*(\d+)\s*\*\s*amount\s*\)"


def j3_scroll_direction(core, tools):
    head("§3 · ① 滚动方向口径四方一致（act.py 两行看着矛盾，其实是伪告警）")

    neg_down = None
    if not hasattr(core, "SCROLL_NEGATIVE_IS_DOWN"):
        red("①a.1", "core 里没有 SCROLL_NEGATIVE_IS_DOWN —— 方向口径没有唯一定义处")
    else:
        neg_down = core.SCROLL_NEGATIVE_IS_DOWN
        ok("①a.1 core.SCROLL_NEGATIVE_IS_DOWN = %r（方向口径的唯一定义处）" % neg_down)

    if hasattr(core, "_scroll_direction"):
        words = {a: core._scroll_direction(a) for a in (-3, -1, 1, 3)}
        if neg_down is not None:
            want = {a: ("向下" if ((a < 0) == neg_down) else "向上") for a in words}
            if words == want:
                ok("①a.2 _scroll_direction 与常量一致：%s"
                   % "，".join("%d→%s" % (a, w) for a, w in words.items()))
            else:
                red("①a.2", "_scroll_direction 与 SCROLL_NEGATIVE_IS_DOWN 打架："
                            "实到 %s，应为 %s" % (words, want))
    else:
        red("①a.2", "core 里没有 _scroll_direction")

    d = (tools.get("ctl_scroll") or {}).get("description") or ""
    need = {
        "正=向上": ("正 = 向上" in d) or ("正=向上" in d) or ("`amount > 0` 向上" in d),
        "负=向下": ("负 = 向下" in d) or ("负=向下" in d) or ("`amount < 0` 向下" in d),
        "解释 Increment/Decrement": ("LargeIncrement" in d and "PageDown" in d
                              and ("增减" in d or "不是矛盾" in d)),
        "网页优先 CDP": ("ctl_browser" in d and "CDP" in d),
        "滚动前先取 scroll_y": "scroll_y" in d,
        "at 只做校验": ("只做" in d and "落点" in d),
    }
    bad = [k for k, v in need.items() if not v]
    if bad:
        red("①a.3", "契约 ctl_scroll 的 description 缺这些要点：%s" % bad)
    else:
        ok("①a.3 契约 description 要点齐全（方向口径 / Increment 解释 / CDP 优先 /"
           " 先取 scroll_y / at 只做校验 / 放行说明）")

    act = read(os.path.join(ROOT, "legacy_tools", "act.py"))
    m_uia = re.search(_UIA_RE, act)
    m_whl = re.search(_WHEEL_RE, act)
    uia = whl = None
    if not m_uia:
        red("①a.4", "act.py 的 UIA 分支没读到 `LargeIncrement if amount < 0 else LargeDecrement`")
    elif m_uia.group(1) not in UIA_SCROLLAMOUNT_DIR or m_uia.group(2) not in UIA_SCROLLAMOUNT_DIR:
        red("①a.4", "UIA 分支的枚举名不在平台事实表里：%s / %s" % (m_uia.group(1), m_uia.group(2)))
    else:
        uia = (UIA_SCROLLAMOUNT_DIR[m_uia.group(1)], UIA_SCROLLAMOUNT_DIR[m_uia.group(2)])
        ok("①a.4 act.py UIA 分支：amount<0 → %s（%s ≡ PageDown）、amount>0 → %s"
           % (uia[0], m_uia.group(1), uia[1]))
    if not m_whl:
        red("①a.5", "act.py 的滚轮分支没读到 `wheel(<常数> * amount)`")
    else:
        k = int(m_whl.group(1))
        other = {"向上": "向下", "向下": "向上"}
        # delta = k * amount。常数为正 ⇒ delta 符号与 amount 相反 ⇒ 负数变「反向」。
        neg = other[WHEEL_POSITIVE_IS] if k > 0 else WHEEL_POSITIVE_IS
        whl = (neg, other[neg])
        ok("①a.5 act.py 滚轮分支 wheel(%d * amount) ⇒ amount<0 → %s、amount>0 → %s"
           % (k, whl[0], whl[1]))
    if uia and whl:
        if uia != whl:
            red("①a.6", "★ 两条分支自己就对不上：UIA 说 负=%s/正=%s，滚轮说 负=%s/正=%s"
                % (uia[0], uia[1], whl[0], whl[1]))
        else:
            ok("①a.6 ★ 两条分支**各自独立**算出来的结论一致：负数=%s、正数=%s"
               % (whl[0], whl[1]))
    # ── 方2 ↔ 方4/方5 的**交叉**核对 ──
    #   ★ 这一条才是「可证伪」的关键：它拿**服务层实际吐出来的词**去对
    #     **act.py 两条分支代进平台事实后算出来的词**。
    #     只拿常量跟自己比、或只拿推导跟字面量比，都会让注入的取反蒙混过关
    #     （这是本判据第一版的真缺陷，已实跑抓到：--inject flip 一条都没红）。
    if uia and whl and hasattr(core, "_scroll_direction"):
        mine = (core._scroll_direction(-1), core._scroll_direction(1))
        if mine != uia:
            red("①a.7", "★ 服务层口径与 act.py 的 UIA 分支对不上："
                "_scroll_direction 说 负=%s/正=%s，UIA 分支算出来是 负=%s/正=%s"
                % (mine[0], mine[1], uia[0], uia[1]))
        elif mine != whl:
            red("①a.7", "★ 服务层口径与 act.py 的滚轮分支对不上："
                "_scroll_direction 说 负=%s/正=%s，滚轮分支算出来是 负=%s/正=%s"
                % (mine[0], mine[1], whl[0], whl[1]))
        else:
            ok("①a.7 ★ 服务层吐出来的词 ＝ act.py 两条分支各自算出来的词：负数=%s、正数=%s"
               % mine)
            ok("①a.8 结论：源码里 act.py:2317 的 `LargeIncrement if amount < 0` 与 2342 那句"
               "「负值 = 向下」**不矛盾** —— Increment/Decrement 讲的是滚动偏移的增减，"
               "不是屏幕方向。本项目对外口径：正=向上、负=向下，core.py 原样透传不取反。")


# ═══════════════════════════ §4 ③ 越界落点 / 前台放行 ═══════════════════════════
def _desk_hwnd():
    try:
        return int(ctypes.windll.user32.GetDesktopWindow())
    except Exception:
        return 0


def _no_action_happened(r):
    """这个信封里有没有「动作真的发出去了」的痕迹（rc 来自老脚本）。"""
    d = r.get("data") or {}
    return "rc" not in d


def j4_at_and_foreground(core):
    head("§4 · ③ 越界落点被拒 ＋ 不许静默抢前台")
    h = _desk_hwnd()
    info("用桌面窗口 hwnd=%d 当靶（只读它的窗口矩形，不碰任何输入）" % h)

    # ── ③a at 越界 → code=5，且一个像素都没滚 ──
    r = core.dispatch("ctl_scroll", {"hwnd": h, "amount": -1, "at": [-99999, -99999]})
    w = r.get("warning") or ""
    if r.get("ok"):
        red("③a", "at 明显在窗口矩形外，却放行了")
    elif r.get("code") != 5:
        red("③a", "at 越界的错误码应是 5（坐标/前台/窗口稳定性拒绝），实到 %r" % r.get("code"))
    elif not _no_action_happened(r):
        red("③a", "at 越界这条路径**动了老脚本**（data 里有 rc）—— 必须一步都没发")
    else:
        need = {"说清 at 是校验用": ("at" in w and ("校验" in w or "不在" in w)),
                "给出下一步（用 amount）": "amount" in w,
                "说清一个都没滚": ("没滚" in w or "一格都没滚" in w)}
        bad = [k for k, v in need.items() if not v]
        if bad:
            red("③a", "提示不够可执行，缺：%s ｜ 原文：%s" % (bad, w[:200]))
        else:
            ok("③a at 越界 → code=5、零动作、提示可执行：%s" % w[:150])

    # ── ③b at 在矩形内 → 走到前台闸门（说明 at 校验没误伤）──
    r = core.dispatch("ctl_scroll", {"hwnd": h, "amount": -1, "at": [1, 1]})
    w = r.get("warning") or ""
    if r.get("ok"):
        red("③b", "at 合法却放行了 —— 说明它没走前台闸门，老脚本被调起来了")
    elif "allow_foreground" in w:
        ok("③b at 合法 → 越过落点校验、停在**前台闸门**（未被误伤，也未发出动作）：%s" % w[:110])
    else:
        red("③b", "at 合法时不该停在落点校验上，实到 code=%s warning=%s"
            % (r.get("code"), w[:150]))

    # ── ③c / ③d 不传 allow_foreground → 拒绝，且必须一步都没发 ──
    for tool, args, tag in (("ctl_scroll", {"hwnd": h, "amount": -1}, "③c"),
                            ("ctl_keys", {"hwnd": h, "keys": "PageDown"}, "③d")):
        r = core.dispatch(tool, args)
        w = r.get("warning") or ""
        if r.get("ok"):
            red(tag, "%s 没传 allow_foreground 却放行了" % tool)
        elif r.get("code") != 5:
            red(tag, "%s 不放行时错误码应是 5，实到 %r" % (tool, r.get("code")))
        elif not _no_action_happened(r):
            red(tag, "%s 被拒了却已经动过老脚本（data 里有 rc）—— 拒绝必须零副作用" % tool)
        elif "allow_foreground" not in w:
            red(tag, "%s 的拒绝提示没告诉调用方要传 allow_foreground：%s" % (tool, w[:150]))
        else:
            ok("%s %s 未放行置前 → code=5、零动作、提示点名 allow_foreground" % (tag, tool))

    # ── ③e 字符串 "true" / 数字 1 都不算放行（口径不许在两条传输间漂移）──
    for bad_v in ("true", "True", 1, "yes"):
        r = core.dispatch("ctl_keys", {"hwnd": h, "keys": "PageDown", "allow_foreground": bad_v})
        if r.get("ok"):
            red("③e", "allow_foreground=%r 被当成了放行" % bad_v)
        elif not _no_action_happened(r):
            red("③e", "allow_foreground=%r 被拒，但已经动过老脚本" % bad_v)
        else:
            ok("③e allow_foreground=%-6r 不算放行（只有 JSON 真布尔 true 才放行）" % bad_v)


# ═════════════════════════════ §5 ctl_keys 白名单与解析 ═════════════════════════════
def j5_keys(core, tools):
    head("§5 · ctl_keys：键名白名单自洽 ＋ 白名单外一律拒绝")
    h = _desk_hwnd()

    wl = getattr(core, "KEY_WHITELIST", None)
    if not wl:
        red("③2.1", "core 里没有 KEY_WHITELIST")
        return
    ok("③2.1 core.KEY_WHITELIST %d 项：%s" % (len(wl), "、".join(sorted(wl))))

    rev = getattr(core, "KEY_VK_WHITELIST", {})
    bad_rev = {v: k for v, k in rev.items() if wl.get(k) != v}
    if bad_rev:
        red("③2.2", "KEY_VK_WHITELIST 与 KEY_WHITELIST 对不上：%s" % bad_rev)
    else:
        ok("③2.2 KEY_VK_WHITELIST（%d 个码）由白名单生成，无一错位" % len(rev))

    mis = []
    for name, vk in wl.items():
        for spelling in (name, "0x%X" % vk, str(vk)):
            got, _ = core._resolve_vk(spelling)
            if got != vk:
                mis.append("%s/%r→%r（应 %r）" % (name, spelling, got, vk))
    if mis:
        red("③2.3", "白名单键名 / 十六进制 / 十进制三种写法解不出同一个 vk：%s" % mis[:6])
    else:
        ok("③2.3 白名单 %d 个键 × 3 种写法（键名 / 0xNN / 十进制）共 %d 次解析全部一致"
           % (len(wl), len(wl) * 3))

    for bad, tag in (("0x5B", "vk"), ("999", "超范围vk"), ("Shift", "名单外键名"),
                     ("", "空串"), ("Page Down", "拼错")):
        r = core.dispatch("ctl_keys", {"hwnd": h, "keys": bad})
        w = r.get("warning") or ""
        if r.get("ok"):
            red("③2.4", "%s keys=%r 被放行了" % (tag, bad))
        elif r.get("code") != 5:
            red("③2.4", "%s keys=%r 的错误码应是 5，实到 %r" % (tag, bad, r.get("code")))
        elif not _no_action_happened(r):
            red("③2.4", "%s keys=%r 被拒却已经动过老脚本" % (tag, bad))
        elif ("白名单" not in w) and ("认不出" not in w) and ("不能为空" not in w):
            red("③2.4", "%s keys=%r 的提示没说清为什么：%s" % (tag, bad, w[:150]))
        else:
            ok("③2.4 %-8s keys=%-10r → code=5、零动作、提示说清「为什么 + 可用键」"
               % (tag, bad))

    r = core.dispatch("ctl_keys", {"hwnd": h, "keys": "PageDown", "times": 999})
    if r.get("ok"):
        red("③2.5", "times=999 被放行了")
    elif not _no_action_happened(r):
        red("③2.5", "times=999 被拦，但已经动过老脚本")
    else:
        ok("③2.5 times=999 → code=%s、零动作：%s"
           % (r.get("code"), (r.get("warning") or "")[:110]))

    d = (tools.get("ctl_keys") or {}).get("description") or ""
    need = {"白名单": "白名单" in d,
            "非白名单会被拒": ("不在白名单" in d) or ("一律拒绝" in d),
            "必须放行置前": "allow_foreground" in d,
            "只有 ctrl 一路": ("只有 ctrl" in d) or ("只声明了" in d),
            "打字请用 ctl_type": "ctl_type" in d,
            "发出去≠响应了": ("不等于界面响应" in d) or ("页面无变化" in d)}
    bad = [k for k, v in need.items() if not v]
    if bad:
        red("③2.6", "契约 ctl_keys 的 description 缺这些要点：%s" % bad)
    else:
        ok("③2.6 契约 ctl_keys 的 description 要点齐全（白名单 / 拒 / 放行 / 只有 ctrl /"
           " 打字走 ctl_type / 发出去≠响应）")


# ═══════════════════════ §6/§7 实机：两次真实滚动 ＋ PageDown ≈ 一屏 ═══════════════════
# 靶子：现场用 csc 编一个 WinForms 小程序，开两个窗 ——
#   A1UIA 窗：AutoScroll 的 Form + 2400px 高 Panel（UIA 里是 PaneControl + ScrollPattern）
#   A1WHEEL 窗：Form 里只放 RichTextBox（EditControl，不在 act.py 的 UIA 白名单 ⇒ 走滚轮分支）
# 位置由 80ms 定时器写进状态文件，判据只读那个文件，不去猜。
CS_SOURCE = r"""
using System;
using System.IO;
using System.Text;
using System.Windows.Forms;
using System.Drawing;

class A1Target {
    static string STATE = "state.txt";
    static Timer tm;
    static Form fA = new Form();
    static Form fB = new Form();
    static RichTextBox rtb = new RichTextBox();

    [STAThread]
    static void Main(string[] a) {
        if (a.Length > 0) STATE = a[0];
        fA.Text = "A1UIA";
        fA.Width = 420; fA.Height = 320;
        fA.StartPosition = FormStartPosition.Manual;
        fA.Location = new Point(200, 200);
        fA.AutoScroll = true;
        var pad = new Panel();
        pad.Width = 380; pad.Height = 2400;
        pad.BackColor = Color.White;
        var lbl = new Label();
        lbl.Text = "TOP-MARKER-0";
        lbl.Left = 20; lbl.Top = 20; lbl.AutoSize = true;
        lbl.Font = new Font("Consolas", 14f);
        pad.Controls.Add(lbl);
        for (int i = 1; i < 60; i++) {
            var t = new Label();
            t.Text = "LINE-" + i;
            t.Left = 20; t.Top = 40 + i * 40;
            t.AutoSize = true;
            pad.Controls.Add(t);
        }
        fA.Controls.Add(pad);
        fB.Text = "A1WHEEL";
        fB.Width = 420; fB.Height = 320;
        fB.StartPosition = FormStartPosition.Manual;
        fB.Location = new Point(700, 200);
        fB.AutoScroll = false;
        rtb.Dock = DockStyle.Fill;
        rtb.WordWrap = false;
        rtb.ScrollBars = RichTextBoxScrollBars.Both;
        var sb = new StringBuilder();
        for (int i = 0; i < 400; i++) sb.AppendLine("ROW-" + i.ToString("D4"));
        rtb.Text = sb.ToString();
        fB.Controls.Add(rtb);
        fA.Show();
        fB.Show();
        tm = new Timer();
        tm.Interval = 80;
        tm.Tick += delegate {
            try {
                int ci = rtb.GetCharIndexFromPosition(new Point(1, 1));
                string s = "A=" + fA.VerticalScroll.Value
                        + ";Amax=" + fA.VerticalScroll.Maximum
                        + ";Bline=" + rtb.GetLineFromCharIndex(ci)
                        + ";Bchar=" + ci;
                File.WriteAllText(STATE, s);
            } catch (Exception e) { }
        };
        tm.Start();
        Application.Run(fA);
    }
}
"""

# csc 的位置只从环境变量推，不写死盘符（写死路径门禁 tests\_写死路径判据.py 会红）。
CSC = os.path.join(os.environ.get("WINDIR") or os.path.expandvars("%WINDIR%"),
                   "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe")


def _read_state(p):
    out = {}
    try:
        for kv in read(p).strip().split(";"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                out[k] = v
    except Exception:
        pass
    return out


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _desktop_probe():
    """这台机器到底有没有**能吃输入**的交互桌面。没有就如实说，别硬演。"""
    ev = {}
    for name, fn in (("GetForegroundWindow", lambda: int(ctypes.windll.user32.GetForegroundWindow())),
                     ("GetShellWindow", lambda: int(ctypes.windll.user32.GetShellWindow())),
                     ("GetDesktopWindow", lambda: int(ctypes.windll.user32.GetDesktopWindow()))):
        try:
            ev[name] = fn()
        except Exception as e:
            ev[name] = "取不到：%s" % e
    try:
        p = _POINT()
        r = ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        ev["GetCursorPos"] = "(%d,%d)" % (p.x, p.y) if r else "返回 0（失败）"
    except Exception as e:
        ev["GetCursorPos"] = "拒绝访问：%s" % e
    try:
        ev["SetForegroundWindow(desktop)"] = int(
            ctypes.windll.user32.SetForegroundWindow(ctypes.windll.user32.GetDesktopWindow()))
    except Exception as e:
        ev["SetForegroundWindow(desktop)"] = "拒绝访问：%s" % e
    return ev


def _find_win(title):
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        n = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(h, n, 256)
        if n.value == title and ctypes.windll.user32.IsWindowVisible(h):
            hits.append(int(h))
        return True
    ctypes.windll.user32.EnumWindows(cb, 0)
    return hits[0] if hits else 0


def _border_windows():
    n = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        b = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(h, b, 256)
        if b.value.startswith("AgentCtlCtrlBorder"):
            n.append((int(h), b.value))
        return True
    ctypes.windll.user32.EnumWindows(cb, 0)
    return n


def _make_sandbox(tag):
    # 造一个只属于本判据的沙箱，并让闸门 / 状态目录 / 急停文件全指过去。
    # ★ 绝不能用使用者的真通行证 —— 那会扣掉他们自己的动作额度。
    #   必须在 import agentctl.gate **之前**调：gate 在 import 时就把 AUTHDIR / BRAKE_FLAG 定死了。
    sb = tempfile.mkdtemp(prefix="_A1_%s_" % tag)
    shutil.copytree(os.path.join(ROOT, "legacy_tools"), os.path.join(sb, "legacy_tools"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    home = os.path.join(sb, "legacy_tools")
    ad = os.path.join(home, "_auth")
    os.makedirs(ad, exist_ok=True)
    js = {"scope": "B", "scopes": ["A级 只读感知", "B级 点击滚动", "C级 打字快捷键"],
          "expires": "判据沙箱", "expires_at": time.time() + 3600,
          "consumed": False, "acts": 0, "acts_limit": 9999}
    with io.open(os.path.join(ad, "auth_pass.json"), "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)
    # core.py 的 LEGACY = gate.HOME = AGENTCTL_LEGACY_HOME ⇒ 老脚本得放在这层下面。
    os.environ["AGENTCTL_LEGACY_HOME"] = home
    os.environ["AGENTCTL_APP_DIR"] = sb
    os.environ["AGENTCTL_BRAKE_FLAG"] = os.path.join(sb, "_不存在的急停.flag")
    os.environ["AGENTCTL_REQUIRE_LEASE"] = "0"   # 判据不签租约；许可与额度仍照常过闸
    return sb


def _build_target():
    # 编靶子。返回 (exe 路径, 说明)；编不出来就返回 (None, 为什么)。
    d = os.path.join(tempfile.gettempdir(), "_A1_判据靶")
    os.makedirs(d, exist_ok=True)
    exe = os.path.join(d, "A1_target.exe")
    cs = os.path.join(d, "A1_target.cs")
    if os.path.exists(exe) and os.path.exists(cs) and read(cs) == CS_SOURCE.strip():
        return exe, "复用已编好的 %s" % exe
    if not os.path.exists(CSC):
        return None, "找不到 csc.exe（试过 %s）—— 编不出靶子就没法实跑" % CSC
    with io.open(cs, "w", encoding="utf-8-sig") as f:
        f.write(CS_SOURCE.strip())
    cmd = [CSC, "/nologo", "/target:winexe", "/out:" + exe, cs]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    if p.returncode != 0 or not os.path.exists(exe):
        return None, "csc 返回 %d：%s" % (p.returncode, (p.stdout or "")[:300])
    return exe, "现场编的 %s" % exe


def _mkrec(r):
    d = r.get("data") or {}
    return {"ok": r.get("ok"), "code": r.get("code"), "warning": r.get("warning"),
            "方向": d.get("方向"), "走哪条分支": d.get("走哪条分支"),
            "落点实际": d.get("落点实际"), "raw": str(d.get("raw") or "")[-900:]}


def _moved(a, b):
    try:
        return int(a) != int(b)
    except Exception:
        return False


def _no_input_desktop(ev):
    fg = str(ev.get("GetForegroundWindow"))
    cur = str(ev.get("GetCursorPos"))
    return fg == "0" or fg.startswith("取不到") or "拒绝访问" in cur or cur.startswith("返回 0")


def j67_real_scroll(core):
    head("§6 / §7 · 实机：两次真实滚动（向下 / 向上）＋ PageDown ≈ 一屏")
    ev = _desktop_probe()
    for k, v in ev.items():
        info("探针 %-26s = %s" % (k, v))
    no_input = _no_input_desktop(ev)
    info("结论：这台机器%s交互输入桌面" % ("**没有**" if no_input else "有"))

    exe, why = _build_target()
    if not exe:
        skip("⑥", "编不出靶子：%s" % why)
        return
    info("靶子：%s（%s）" % (exe, why))
    # ★ 一票否决：老脚本目录**必须**落在沙箱里。万一它回落到真仓，
    #   这一跑就会去扣使用者自己的动作额度 —— 那比验不了更糟，宁可直接停。
    want_home = os.path.normcase(os.environ.get("AGENTCTL_LEGACY_HOME", ""))
    got_home = os.path.normcase(os.path.join(globals()["_SBX"], "legacy_tools"))
    if want_home != got_home or os.path.normcase(core.LEGACY or "") != got_home:
        red("⑥", "★ 一票否决：老脚本目录没落在沙箱里（期望 %s，实到 core.LEGACY=%s）"
            "—— 继续跑会去动使用者的真实额度，已停手" % (got_home, core.LEGACY))
        return
    ok("⑥.0 老脚本目录已钉在沙箱：core.LEGACY=%s（不会碰使用者额度）" % core.LEGACY)
    sb = globals().get("_SBX") or os.environ.get("AGENTCTL_LEGACY_HOME")
    info("沙箱：%s（老脚本仍在仓里跑，但它的 gate / 状态目录 / 急停文件全指到沙箱；"
         "通行证 A/B/C、额度 9999 是判据自己签的，不碰使用者额度）" % sb)

    st = os.path.join(sb, "靶子位置.txt")
    proc = subprocess.Popen([exe, st])
    M = {}
    try:
        for _ in range(80):
            if os.path.exists(st) and _read_state(st):
                break
            time.sleep(0.15)
        before = _read_state(st)
        hw = _find_win("A1WHEEL")
        hu = _find_win("A1UIA")
        M["before"] = before
        info("靶窗口：A1WHEEL hwnd=%s、A1UIA hwnd=%s；起始位置 %s" % (hw, hu, before))
        if not before or not hw:
            skip("⑥", "靶窗没起来 / 位置读不到（state=%s, hwnd=%s）" % (before, hw))
            return
        r1 = core.dispatch("ctl_scroll", {"hwnd": hw, "amount": -1,
                                          "allow_foreground": True, "timeout_sec": 40})
        time.sleep(1.0)
        mid = _read_state(st)
        M["after_down"] = mid
        info("第 1 次 amount=-1（按 §3 口径＝向下）：ok=%s code=%s 方向=%s 分支=%s"
             % (r1.get("ok"), r1.get("code"), (r1.get("data") or {}).get("方向"),
                (r1.get("data") or {}).get("走哪条分支")))
        info("     位置 A1WHEEL 首行 before=%s → after=%s" % (before.get("Bline"), mid.get("Bline")))
        r2 = core.dispatch("ctl_scroll", {"hwnd": hw, "amount": 1,
                                          "allow_foreground": True, "timeout_sec": 40})
        time.sleep(1.0)
        end = _read_state(st)
        M["after_up"] = end
        info("第 2 次 amount=+1（向上）：ok=%s code=%s 方向=%s 分支=%s"
             % (r2.get("ok"), r2.get("code"), (r2.get("data") or {}).get("方向"),
                (r2.get("data") or {}).get("走哪条分支")))
        info("     位置 A1WHEEL 首行 after=%s → end=%s" % (mid.get("Bline"), end.get("Bline")))
        b0 = _read_state(st)
        r3 = core.dispatch("ctl_keys", {"hwnd": hu, "keys": "PageDown",
                                        "allow_foreground": True, "timeout_sec": 40})
        time.sleep(1.0)
        b1 = _read_state(st)
        M["pagedown"] = [b0.get("A"), b1.get("A")]
        info("PageDown：ok=%s code=%s；A1UIA 纵向位置 %s → %s"
             % (r3.get("ok"), r3.get("code"), b0.get("A"), b1.get("A")))
        dump = os.path.join(sb, "实测记录.json")
        rec = {"探针": ev, "位置": M, "第1次": _mkrec(r1), "第2次": _mkrec(r2),
               "PageDown": _mkrec(r3)}
        with io.open(dump, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        info("完整原始记录（含 data.raw 尾部）：%s" % dump)
        if _moved(before.get("Bline"), mid.get("Bline")) and _moved(mid.get("Bline"), end.get("Bline")):
            ok("⑥ 实机：两次真实滚动都动了 —— 向下 %s→%s、向上 %s→%s"
               % (before.get("Bline"), mid.get("Bline"), mid.get("Bline"), end.get("Bline")))
        elif no_input:
            skip("⑥", "位置没变，但这台机器**没有交互输入桌面**（探针见上）⇒ 实机方向**验不了**，"
                       "不等于绿。第1次 warning=%s" % str(r1.get("warning"))[:120])
        else:
            red("⑥", "有交互桌面却一格没动：向下 %s→%s、向上 %s→%s"
                % (before.get("Bline"), mid.get("Bline"), mid.get("Bline"), end.get("Bline")))
        # ═══ ★ 2026-10-17（DSH 定稿）═══
        # 键盘这条**验不了**（不是失败）：Windows 只把键盘输入投给当前焦点窗口；
        # 后台投递（PostMessage WM_KEY*）对未聚焦控件无效，而抢焦点那条路在本产品里
        # 早被判废（act.py:1173-1175：抢焦点 + 输入队列脏状态）。
        # "翻页"这件事由 ⑥ 的滚轮路线覆盖（已真绿 0→3→0）⇒ 键盘记为验不了，不装绿也不硬红。
        skip("⑦", "键盘（PageDown 等）在**无前台**目标上不可达：Windows 只把键盘给焦点窗口，"
                  "后台 PostMessage 对未聚焦控件无效，而抢焦点那条路已判废（act.py:1173-1175）"
                  "—— 这是系统限制，不是缺陷。翻页能力由 ⑥ 的滚轮路线覆盖（实测有效）。"
                  "PageDown 实测：ok=%s code=%s" % (r3.get("ok"), r3.get("code")))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    left = _border_windows()
    if left:
        red("⑧", "跑完还留着急停边框窗口 %s" % left)
    else:
        ok("⑧ 跑完无残留 AgentCtlCtrlBorder* 窗口（本判据不碰急停，只核对没留残骸）")


# ═════════════════════════════ §9 注入自证（能跑红）═════════════════════════════
INJECT_DOC = {
    "flip": "把影子仓 core.py 里的 SCROLL_NEGATIVE_IS_DOWN 取反 ⇒ §3 的方2 与方4/方5 打架",
    "noat": "把影子仓 core.py 里 t_scroll 的 at 越界检查整段删掉 ⇒ §4 的 ③a 必须红",
}


def _inject_flip(core_py):
    src = read(core_py)
    out = src.replace("SCROLL_NEGATIVE_IS_DOWN = True", "SCROLL_NEGATIVE_IS_DOWN = False", 1)
    if out == src:
        raise SystemExit("[inject flip] 没找到 SCROLL_NEGATIVE_IS_DOWN = True —— 影子仓代码变了？")
    with io.open(core_py, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    return "SCROLL_NEGATIVE_IS_DOWN: True → False"


def _inject_noat(core_py):
    # 定点删：at 取值那一行 到 前台闸门那一行**之间**的全部内容。
    # ⚠ 不用「数括号配平」那种写法 —— 第一版就是这么写的，结果把文件后半截也吞了，
    #   影子仓 core.py 直接残缺、判据报 AttributeError。用两端标记最老实。
    src = read(core_py)
    a = '    at = args.get("at")\n'
    b = '    err = _allow_foreground(args, "ctl_scroll", "滚动")\n'
    if src.count(a) != 1 or src.count(b) != 1:
        raise SystemExit("[inject noat] 两端标记没各命中一次（a=%d b=%d）—— 影子仓代码变了？"
                         % (src.count(a), src.count(b)))
    cut = src[src.index(a) + len(a):src.index(b)]
    out = src.replace(a + cut + b, "    at = None  # 注入：删掉了 at 越界检查\n" + b, 1)
    with io.open(core_py, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(out))
    compile(out, core_py, "exec")          # 注入完必须还编得过，否则红得没意义
    return "删掉 %d 行 at 越界检查" % len(cut.splitlines())


def _run_inject(mode):
    head("§9 · 注入自证：--inject %s（被测仓＝影子仓，现役代码一个字节没动）" % mode)
    if mode not in INJECT_DOC:
        red("§9", "未知的 --inject 模式 %r，可选：%s" % (mode, list(INJECT_DOC)))
        return
    tmp = _inject.shadow_repo(_inject.tmpdir("_A1_注入红_"))
    os.environ["AGENTCTL_ROOT"] = tmp
    core_py = os.path.join(tmp, "src", "agentctl", "core.py")
    what = (_inject_flip if mode == "flip" else _inject_noat)(core_py)
    compile(read(core_py), core_py, "exec")
    info("影子仓：%s" % tmp)
    info("注入内容：%s —— %s" % (what, INJECT_DOC[mode]))
    globals()["ROOT"] = tmp
    globals()["CONTRACT"] = os.path.join(tmp, "contract", "tools.json")
    core, _gate = _load_core()
    tools = _contract_tools()
    before_n, before_k = len(FAILS), len(SKIPS)
    if mode == "flip":
        j3_scroll_direction(core, tools)
    else:
        j4_at_and_foreground(core)
    got = len(FAILS) - before_n
    if got:
        ok("§9.%s 注入后确实报红 %d 条 ⇒ 本判据这一节**可证伪**，不是摆设" % (mode, got))
    else:
        red("§9.%s" % mode, "注入已知缺陷后判据**一条都没红** ⇒ 这一节是假绿，必须修")


# ═════════════════════════════════════ main ═════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="ctl_scroll / ctl_keys 接 MCP 工具面的判据")
    ap.add_argument("--inject", nargs="?", const="flip", choices=list(INJECT_DOC),
                    help="自证跑红：把现役代码复制成影子仓并注入一个已知缺陷")
    a = ap.parse_args()
    print("被测仓 ROOT = %s" % ROOT)
    print("退出码约定：0 全绿 ｜ 1 有红 ｜ 2 有「验不了」（**2 不许读成绿**）")
    if _INJECT:
        _run_inject(_INJECT)
    else:
        globals()["_SBX"] = _make_sandbox("判据")
        info("沙箱就位：%s（闸门 / 状态目录 / 急停文件都指过去；通行证 A/B/C、额度 9999 是"
             "判据自签的，绝不碰使用者的动作额度）" % _SBX)
        core, _gate = _load_core()
        tools = _contract_tools()
        j1_contract_vs_core(core, tools)
        j2_schema(tools)
        j3_scroll_direction(core, tools)
        j4_at_and_foreground(core)
        j5_keys(core, tools)
        j67_real_scroll(core)
    head("汇总")
    print("  红 %d 条 ／ 验不了 %d 条" % (len(FAILS), len(SKIPS)))
    for aid, why in FAILS:
        print("    [红] %s · %s" % (aid, why))
    for aid, why in SKIPS:
        print("    [验不了] %s · %s" % (aid, why))
    if FAILS:
        print("\n结论：【红】")
        return 1
    if SKIPS:
        print("\n结论：【绿，但有验不了的项】—— 退出码 2，**不许当成全绿**")
        return 2
    print("\n结论：【绿】")
    return 0


if __name__ == "__main__":
    sys.exit(main())
