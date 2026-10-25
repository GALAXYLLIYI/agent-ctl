# -*- coding: utf-8 -*-
r"""审计导出 · 判据（2026-10-25 · 派单 Z2）

被测：src/agentctl/audit.py（纯离线：collect / summarize / render_markdown）。

本判据**只钉行为、不改行为**，fixtures 全部自建在临时目录（离线，不碰真数据）：
  ① 空输入 ⇒ 不崩、collect 记 notes、报告含「无记录」（且含「无法复算」小节）；
  ② 3 条事件 + 2 个证据回执 ⇒ 三态（met/no_effect/unknown）与四类
     （rejected_env/unobservable/no_effect/action_failed）计数逐格正确，
     折不进三态的原词进「三态之外」、不被谎报；
  ③ Top-N（窗口/工具）与时间范围（起止原样字符串）正确；
  ④ markdown：含「无法复算」小节、含时间范围起止、Top-N 里看得到窗口与工具；
     无法复算项覆盖 2 个证据目录（像素层）+ 2 条事件 causal 判词（像素层）。

跑法：python -X utf8 tests\_P3_审计导出_判据.py
      python -X utf8 tests\_P3_审计导出_判据.py --inject   # 必红
      --inject 干的事：把 failure_class=no_effect 故意计成 action_failed（坏汇总），
      再用**同一组断言**重跑 —— 汇总写死了/自证了就必须抓到并报红。
退出码：0 绿；1 红。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import audit                                      # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  X   ") + name + (("  —— " + detail) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


def make_fixtures(tmp):
    """临时目录里造 3 条事件 + 2 个证据目录（口径见 audit.py 文档字符串）。"""
    state = os.path.join(tmp, "state")
    os.makedirs(state)
    ev1 = {"seq": 1, "kind": "click", "ok": True, "code": 0,
           "t_wall": "2026-10-24T10:00:00+08:00", "tool": "ctl_click",
           "payload": {"win": {"exe": "notepad.exe", "hwnd": 111},
                       "verdict": "met",
                       "verdict_canonical": {"value": "met", "kind": "causal"}}}
    ev2 = {"seq": 2, "kind": "click", "ok": False, "code": 8,
           "t_wall": "2026-10-24T10:01:00+08:00", "tool": "ctl_click",
           "payload": {"win": {"exe": "notepad.exe", "hwnd": 111},
                       "verdict": "no_effect_in_region",
                       "verdict_canonical": {"value": "no_effect", "kind": "causal"}}}
    ev3 = {"seq": 3, "kind": "action_blocked", "ok": False, "code": 3,
           "t_wall": "2026-10-24T10:02:00+08:00", "tool": "ctl_click",
           "payload": {"failure_class": "rejected_env", "note": "环境性拒绝"}}
    with open(os.path.join(state, "events.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for ev in (ev1, ev2, ev3):
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    acts = os.path.join(tmp, "_actions")
    for name, rec in (
        ("click_1792826567704398600",
         {"action": "click", "hwnd": 111, "verdict": "met",
          "verdict_canonical": {"value": "met", "kind": "causal"},
          "ts": "2026-10-24T15:22:47+0800"}),
        ("keys_1792827910753128400",
         {"action": "keys", "hwnd": 222, "verdict": "no_effect_in_region",
          "verdict_canonical": {"value": "no_effect", "kind": "causal"},
          "ts": "2026-10-24T15:23:00+0800"}),
    ):
        d = os.path.join(acts, name)
        os.makedirs(d)
        with open(os.path.join(d, "evidence.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, ensure_ascii=False)
    return os.path.join(state, "events.jsonl"), acts


def run_all(summarize_fn, records, tag=""):
    """同一组断言，既验现役 summarize，也验 --inject 的坏实现。"""
    s = summarize_fn(records)

    check(tag + "collect 读到 3 事件 + 2 证据回执，无解析失败",
          s.get("counts") == {"events": 3, "evidence": 2} and s.get("malformed") == 0,
          "实际 counts=%r malformed=%r" % (s.get("counts"), s.get("malformed")))

    # ② 三态：met = 事件1 + 证据click；no_effect = 事件2 + 证据keys；unknown = 0；
    #   事件3（action_blocked）没有判词 ⇒ 未标注 1 条；三态之外应为空。
    three = s.get("verdict_three") or {}
    check(tag + "三态计数：met=2 / no_effect=2 / unknown=0",
          three.get("met") == 2 and three.get("no_effect") == 2 and three.get("unknown") == 0,
          "实际 %r" % (three,))
    check(tag + "三态之外为空（fixtures 没有折不进的原词）",
          (s.get("verdict_beyond") or {}) == {}, "实际 %r" % (s.get("verdict_beyond"),))
    check(tag + "未标注判词 = 1（事件3）", s.get("verdict_missing") == 1,
          "实际 %r" % (s.get("verdict_missing"),))

    # ② 四类：事件3 显式 rejected_env；事件2 code=8 ⇒ no_effect（从 code 推，不依赖标注）；
    #   事件1 成功不计；证据回执无 ok/code 口径 ⇒ 不适用 2 条。
    four = s.get("failure_four") or {}
    check(tag + "四类计数：rejected_env=1 / unobservable=0 / no_effect=1 / action_failed=0",
          four.get("rejected_env") == 1 and four.get("unobservable") == 0
          and four.get("no_effect") == 1 and four.get("action_failed") == 0,
          "实际 %r" % (four,))
    check(tag + "成功不计失败类 = 1；不适用（证据回执）= 2",
          s.get("failure_success_skip") == 1 and s.get("failure_na") == 2,
          "实际 success=%r na=%r" % (s.get("failure_success_skip"), s.get("failure_na")))
    check(tag + "词表外 failure_class 为空", (s.get("failure_beyond") or {}) == {},
          "实际 %r" % (s.get("failure_beyond"),))

    # ③ Top-N：窗口 = notepad.exe×2（win.exe）+ hwnd:111×1 + hwnd:222×1 + 未知窗口×1；
    #   工具 = ctl_click×3（事件流 tool）+ click×1 + keys×1（回执 action）。
    by_window = s.get("by_window") or {}
    by_tool = s.get("by_tool") or {}
    check(tag + "窗口计数：notepad.exe=2、hwnd:111=1、hwnd:222=1、未知窗口=1",
          by_window.get("notepad.exe") == 2 and by_window.get("hwnd:111") == 1
          and by_window.get("hwnd:222") == 1 and by_window.get("未知窗口") == 1,
          "实际 %r" % (by_window,))
    check(tag + "工具计数：ctl_click=3、click=1、keys=1",
          by_tool.get("ctl_click") == 3 and by_tool.get("click") == 1 and by_tool.get("keys") == 1,
          "实际 %r" % (by_tool,))

    # ③ 时间范围：起 = 事件1 的 10:00，止 = 证据 keys 的 15:23（原样字符串，不重排格式）。
    check(tag + "时间范围：起含 10:00、止含 15:23，无解析失败",
          "10:00" in str(s.get("time_min") or "") and "15:23" in str(s.get("time_max") or "")
          and s.get("time_unparsed") == 0,
          "实际 min=%r max=%r bad=%r" % (s.get("time_min"), s.get("time_max"), s.get("time_unparsed")))

    # ④ 无法复算：4 条像素层（2 条事件 causal 判词 + 2 个证据回执），引用含两个证据目录名。
    unre = s.get("unrecomputable") or []
    pixels = [u for u in unre if u.get("kind") == "像素层"]
    refs = " ".join(str(u.get("ref")) for u in unre)
    check(tag + "无法复算项共 4 条且全是像素层",
          len(unre) == 4 and len(pixels) == 4, "实际 %r" % (unre[:2],))
    check(tag + "无法复算引用含两个证据目录名",
          "click_1792826567704398600" in refs and "keys_1792827910753128400" in refs,
          "实际 refs=%r" % (refs,))

    # ④ markdown：含「无法复算」小节、时间范围起止、窗口/工具、三态表头词。
    md = audit.render_markdown(s, [])
    check(tag + "markdown 含「无法复算」小节", "无法复算" in md)
    check(tag + "markdown 含时间范围起止", "10:00" in md and "15:23" in md)
    check(tag + "markdown Top-N 看得到窗口与工具", "notepad.exe" in md and "ctl_click" in md)
    check(tag + "markdown 三态表写的是人话标签", "met（生效）" in md and "unknown（没测出来）" in md)
    check(tag + "markdown 以报告标题开头", md.startswith("# 操作者审计报告"))
    return s


print("== ① 空输入 ⇒ 不崩、记 notes、报告含「无记录」 ==")
tmp_empty = tempfile.mkdtemp(prefix="audit_empty_")
try:
    rec_empty = audit.collect(events_path=os.path.join(tmp_empty, "nope", "events.jsonl"),
                              evidence_root=os.path.join(tmp_empty, "nope", "_actions"))
    check("collect 空输入返回 dict 且事件/证据为空", isinstance(rec_empty, dict)
          and rec_empty.get("events") == [] and rec_empty.get("evidence") == [])
    check("collect 空输入记了 notes（说明缺什么）",
          any("不存在" in str(n) for n in (rec_empty.get("notes") or [])),
          "实际 %r" % (rec_empty.get("notes"),))
    s_empty = audit.summarize(rec_empty)
    check("summarize 空输入不崩且全零",
          s_empty.get("counts") == {"events": 0, "evidence": 0}
          and (s_empty.get("verdict_three") or {}).get("met") == 0)
    md_empty = audit.render_markdown(s_empty, rec_empty)
    check("空输入报告含「无记录」", "无记录" in md_empty)
    check("空输入报告也含「无法复算」小节", "无法复算" in md_empty)
finally:
    shutil.rmtree(tmp_empty, ignore_errors=True)

print("== ②③④ fixtures：3 事件 + 2 证据目录 ⇒ 计数与 markdown ==")
tmp = tempfile.mkdtemp(prefix="audit_fixtures_")
try:
    events_path, acts = make_fixtures(tmp)
    records = audit.collect(events_path=events_path, evidence_root=acts)
    check("collect sources 指向 fixtures",
          str(records.get("sources", {}).get("events_path", "")).endswith("events.jsonl")
          and str(records.get("sources", {}).get("evidence_root", "")).endswith("_actions"))
    run_all(audit.summarize, records)

    if "--inject" in sys.argv:
        print("== 注入：把 no_effect 故意计成 action_failed（坏汇总）—— 上面的断言必须抓到 ==")
        _moved = {"n": 0}

        def _bad_summarize(records_):
            s = audit.summarize(records_)
            moved = (s.get("failure_four") or {}).get("no_effect", 0)
            _moved["n"] = moved
            s["failure_four"]["no_effect"] = 0
            s["failure_four"]["action_failed"] = s["failure_four"].get("action_failed", 0) + moved
            return s

        run_all(_bad_summarize, records, tag="注入·")
        check("注入：坏汇总确实把 no_effect 挪进了 action_failed（否则注入无效）",
              _moved["n"] == 1, "实际挪了 %r" % (_moved["n"],))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAILS:
    print("【红】%d 项失败：%s" % (len(FAILS), FAILS))
    raise SystemExit(1)
if "--inject" in sys.argv:
    print("【红】注入模式必须红：坏汇总没被同一组断言抓到 ⇒ 判据自证了，别信这次的绿")
    raise SystemExit(1)
print("【绿】全部通过：空输入不崩且报告含「无记录」；三态/四类计数逐格正确；"
      "Top-N 与时间范围正确；markdown 含「无法复算」小节；--inject 的坏汇总被同一组断言抓到。")
