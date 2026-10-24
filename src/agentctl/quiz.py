# -*- coding: utf-8 -*-
"""quiz.py —— 超星/学习通 客观题作答通道（只答不提交）

## 为什么要单独一个工具，而不是让 agent 每次自己写脚本

2026-10-20 实跑复盘：这类活（300 题客观题）真正花时间的**不是作答，是机制**：
  · 选项乱序（`randomOptions=true`）⇒ 必须按 **原始 key**（`span.num_option` 的 data）定位，
    按显示字母定位会点错题（同一题在不同标签页显示字母不同）
  · 单选**再点会取消** ⇒ 灌入必须幂等：先取消不在目标里的已选项，再选中目标里未选项
  · 点完 DOM 重建 ⇒ 每题都要重新查询元素
  · 判断题 key 是 `true`/`false`，多选 key 是字母串 ⇒ 不能用同一套拆分逻辑
  · 答案在**服务端渲染**的「作业详情」页（`mooc2/work/view`）里 ⇒ 有标准答案就别硬答

这些机制每一条都踩过一遍。做成工具 = 下次 agent 一行调用就能用，不必重踩。

## 三个传输面（CLI / MCP / GUI）共用这一份实现

  · CLI ：`python -m agentctl.cli ctl_quiz --action status`
  · MCP ：tools/call ctl_quiz（契约是唯一真源，登记即暴露）
  · GUI ：面板只给**只读**按钮（状态/校验/抽标准答案）；apply/save 属动作，
    留给 agent 走闸门，面板不碰闸门（与面板既有规矩一致）

## 红线

  · **没有 submit 动作**：交卷永远由人点。本模块只到「暂存」（saveWork）。
  · 全程 CDP（DOM 级），不发真实键鼠动作。
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata

from agentctl import core                                          # noqa: E402

WORK_DIR = os.path.join(core.ROOT, "work", "quiz")

QUIZ_PAGES = ("dowork", "work/view")


# ───────────────────────── 小工具 ─────────────────────────

def _norm(s) -> str:
    """内容归一化：全角→半角、去空白、去标点。**按内容比对，不按字母**。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[，。、；：！？,.;:!?\u2018\u2019\u201c\u201d()（）\[\]【】]", "", s)
    return s


def _key_set(keys) -> list:
    """答案表的 key 串 → key 列表。

    ★ 判断题是 ``true``/``false``（一个 key，整体）；多选是 ``ABCD``（每个字符一个 key）。
      这两种不能混用同一套拆分 —— 2026-10-20 就是 ``list('true')`` 拆成字符导致 84 道判断题没点进去。
    """
    s = str(keys or "").strip()
    if not s:
        return []
    if len(s) > 1 and all(c.isupper() and c.isalpha() for c in s):
        return list(s)
    return [s]


def _write(path: str, obj) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return path


def _read_any(src) -> dict:
    """答案表来源：文件路径 或 内联 JSON 字符串。"""
    if isinstance(src, dict):
        return src
    s = str(src or "").strip()
    if not s:
        return {}
    if os.path.isfile(s):
        with open(s, encoding="utf-8") as f:
            return json.load(f)
    return json.loads(s)


def _answers_by_qid(raw: dict) -> dict:
    """各种答案表形状 → {qid: [key...]}。

    支持三种：
      {"215422211": "A"}                       最简
      {"215422211": ["A", "B"]}               多选
      {"215422211": {"keys": "AB"}}           完整表（本工具 dump 出来的形状）
    """
    # ★ 列表形状也吃：今天 dump 出来的答案表就是列表（每条带 qid/type/keys）。
    #   只认 keys/answer，**不认 letters** —— letters 是「显示字母」，
    #   选项乱序时它和原始 key 不是一回事，拿它灌会点错题。
    if isinstance(raw, list):
        d = {}
        for it in raw:
            if isinstance(it, dict) and it.get("qid"):
                d[str(it["qid"])] = it.get("keys") or it.get("answer") or ""
        raw = d
    out = {}
    for qid, v in (raw or {}).items():
        if isinstance(v, dict):
            ks = v.get("keys") or v.get("answer") or ""
        elif isinstance(v, (list, tuple)):
            ks = "".join(str(x) for x in v)
        else:
            ks = str(v)
        out[str(qid)] = _key_set(ks)
    return out


def _eval(js: str, port: int, url_contains: str = "", timeout: int = 60):
    """在目标页跑 JS。返回解析后的值（失败给 None）。"""
    rc, val, raw = core._cdp_eval(js, port=port, timeout=timeout,
                                  url_contains=url_contains)
    if rc != 0:
        return None, (raw or "")[:400]
    # ★ 返回值不是合法 JSON 时（cdp.py 把脚本结果原样打出来），
    #   core._cdp_eval 会 json.loads 失败 → val=None ⇒ **不能装作没返回**。
    #   2026-10-20 实测踩到：saveWork() 返回 called 这种裸文本时暂存字段变 null，
    #   看起来像没暂存。这里回退成原始文本，至少把真实发生了什么带出来。
    if val is None and (raw or "").strip():
        return (raw or "").strip()[:400], None
    if isinstance(val, str):
        try:
            return json.loads(val), None
        except Exception:
            return val, None
    return val, None


def _find_page(port: int, kind: str) -> dict:
    """找作业页(dowork) 或 详情页(work/view)。找不到返回 {}。"""
    for p in core._cdp_pages(port):
        u = str(p.get("url") or "")
        if kind == "dowork" and "dowork" in u:
            return p
        if kind == "view" and "work/view" in u:
            return p
    return {}


# ───────────────────────── 页面 JS ─────────────────────────

JS_STATUS = r"""(function(){
  var qs = document.querySelectorAll('div.questionLi');
  var answered = 0, n = 0;
  for (var i=0;i<qs.length;i++){
    var qid = qs[i].getAttribute('data');
    var h = document.getElementById('answer'+qid);
    n++;
    if (h && h.value) answered++;
  }
  var hasTruth = document.body.innerText.indexOf('正确答案:') >= 0;
  return JSON.stringify({url:location.href, title:document.title, 题数:n, 已答:answered,
    详情页带标准答案:hasTruth});})()"""

JS_DUMP = r"""(function(){
  var out=[];
  document.querySelectorAll('div.questionLi').forEach(function(q){
    var qid=q.getAttribute('data');
    var h3=q.querySelector('h3.mark_name');
    var host=q.querySelector('div.stem_answer')||q;
    var opts=[];
    Array.prototype.slice.call(host.querySelectorAll('div.answerBg')).forEach(function(d){
      var sp=d.querySelector('span.num_option,span.num_option_dx');
      var ap=d.querySelector('div.answer_p');
      opts.push({letter: sp?sp.textContent.trim():'',
                 key: sp?(sp.getAttribute('data')||''):'',
                 text: ap?ap.innerText.replace(/\s+/g,' ').trim():'',
                 qtype: d.getAttribute('qtype')||''});
    });
    out.push({qid:qid, type:q.getAttribute('typename')||'',
              title: h3?h3.innerText.replace(/\s+/g,' ').trim():'', opts:opts});
  });
  return JSON.stringify(out);})()"""

JS_TRUTH = r"""(function(){
  function seg(t,tag){var i=t.indexOf(tag);if(i<0)return null;var s=t.slice(i+tag.length);
    var m=s.match(/^(.*?)(?=正确答案:|0\.\d+分|\d+分|答案解析|$)/s);return m?m[1]:s;}
  function parse(x){if(x==null)return null;x=x.replace(/\s+/g,' ').trim();
    var m=x.match(/^([A-Z]+)\s*:\s*(.*)$/);
    if(m)return {letter:m[1], texts:m[2].split(';').map(function(s){return s.trim();}).filter(Boolean)};
    if(/^(对|错|正确|错误)$/.test(x))return {letter:x,texts:[x]};
    return {letter:x,texts:[]};}
  var out=[];
  document.querySelectorAll('div.questionLi').forEach(function(q){
    var t=q.innerText||'';
    out.push({qid:q.getAttribute('data'), correct:parse(seg(t,'正确答案:')),
              mine:parse(seg(t,'我的答案:'))});
  });
  return JSON.stringify(out);})()"""

JS_READ = r"""(function(){
  var out=[];
  document.querySelectorAll('div.questionLi').forEach(function(q){
    var qid=q.getAttribute('data');
    var host=q.querySelector('div.stem_answer')||q;
    var keys=[];
    Array.prototype.slice.call(host.querySelectorAll('div.answerBg')).forEach(function(d){
      if(d.getAttribute('aria-checked')==='true'){
        var sp=d.querySelector('span.num_option,span.num_option_dx');
        if(sp)keys.push(sp.getAttribute('data'));
      }
    });
    out.push({qid:qid, keys:keys.join(''), n:keys.length});
  });
  return JSON.stringify(out);})()"""

JS_APPLY = r"""(function(batch){
  var log=[];
  batch.forEach(function(it){
    var qid=it[0], want=it[1];
    var wantSet={}; want.forEach(function(c){wantSet[c]=1;});
    var q=document.querySelector('div.questionLi[data="'+qid+'"]');
    if(!q){log.push(qid+'|NOTFOUND');return;}
    var host=q.querySelector('div.stem_answer')||q;
    var opts=Array.prototype.slice.call(host.querySelectorAll('div.answerBg')).map(function(d){
      var sp=d.querySelector('span.num_option,span.num_option_dx');
      return {d:d, key: sp?(sp.getAttribute('data')||''):'',
              checked:d.getAttribute('aria-checked')==='true'};
    });
    /* 幂等：先取消不在目标里的已选（单选再点会取消），再选中目标里未选的 */
    opts.forEach(function(o){ if(o.checked && !wantSet[o.key]) o.d.click(); });
    opts.forEach(function(o){ if(!o.checked && wantSet[o.key]) o.d.click(); });
    var h=document.getElementById('answer'+qid);
    log.push(qid+'|'+(h?h.value:''));
  });
  return JSON.stringify(log);})()"""

JS_SAVE = ("(function(){try{saveWork();return JSON.stringify('called');}"
           "catch(e){return JSON.stringify('ERR:'+e.message);}})()")


# ───────────────────────── 各动作 ─────────────────────────

def _act_status(args) -> dict:
    port = int(args.get("port") or 9222)
    out = {"端口": port, "页面": []}
    for kind, label in (("dowork", "作答页"), ("view", "详情页")):
        p = _find_page(port, kind)
        if not p:
            out["页面"].append({"类型": label, "在": False})
            continue
        v, err = _eval(JS_STATUS, port, url_contains=kind)
        item = {"类型": label, "在": True, "标题": p.get("title"), "url": p.get("url")}
        if isinstance(v, dict):
            item.update({k: v.get(k) for k in ("题数", "已答", "详情页带标准答案")})
        elif err:
            item["错误"] = err
        out["页面"].append(item)
    out["提示"] = ("没有作答页 ⇒ 先用 ctl_browser launch 起浏览器并登录；"
                    "没有标准答案 ⇒ 只能自己作答（apply）")
    return out


def _act_dump(args) -> dict:
    port = int(args.get("port") or 9222)
    v, err = _eval(JS_DUMP, port, url_contains="dowork")
    if not isinstance(v, list):
        return {"错误": err or "没抽到题面（作答页不在？）", "题数": 0}
    out = args.get("out") or os.path.join(WORK_DIR, "questions.json")
    _write(out, v)
    return {"题数": len(v), "落盘": out, "题型": sorted({x.get("type", "") for x in v})}


def _act_truth(args) -> dict:
    port = int(args.get("port") or 9222)
    v, err = _eval(JS_TRUTH, port, url_contains="work/view")
    if not isinstance(v, list):
        return {"错误": err or "没抽到标准答案（详情页不在？作业还没提交过？）", "题数": 0}
    out = args.get("out") or os.path.join(WORK_DIR, "truth.json")
    _write(out, v)
    n = sum(1 for x in v if x.get("correct"))
    return {"题数": len(v), "含标准答案": n, "落盘": out}


def _act_apply(args) -> dict:
    port = int(args.get("port") or 9222)
    raw = _read_any(args.get("answers") or os.path.join(WORK_DIR, "answers.json"))
    want = _answers_by_qid(raw)
    if not want:
        return {"错误": "没有答案表（answers 参数给路径或内联 JSON）", "改动": 0}
    cur, err = _eval(JS_READ, port, url_contains="dowork")
    if not isinstance(cur, list):
        return {"错误": err or "读不到作答页", "改动": 0}
    now = {x["qid"]: x["keys"] for x in cur}
    fix = []
    for qid, keys in want.items():
        exp = "".join(sorted(keys))
        got = now.get(qid, "")
        if got != exp:
            fix.append([qid, keys])
    if not fix:
        return {"改动": 0, "说明": "已经与答案表一致", "题数": len(want)}
    applied = 0
    for i in range(0, len(fix), 40):
        chunk = fix[i:i + 40]
        js = "var __B=" + json.dumps(chunk, ensure_ascii=False) + ";" + JS_APPLY.replace(
            "batch.forEach", "__B.forEach")
        r, e2 = _eval(js, port, url_contains="dowork", timeout=90)
        if not isinstance(r, list):
            return {"改动": applied, "错误": e2 or "灌入失败"}
        applied += len(chunk)
    return {"改动": applied, "题数": len(want), "说明": "已按 key 幂等灌入（未提交）"}


def _as_key_list(v) -> list:
    """期望/实际两边都归一成 [key...]：列表原样用；字符串走 _key_set（判断题 true/false 不拆）。"""
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v if str(x)]
    return _key_set(v)


def _verify_compare(want: dict, now: dict) -> tuple[list, int]:
    """纯函数：期望 {qid: [key...]} vs 实际 {qid: keys字符串} ⇒ (不一致明细, 顺序差异计数)。

    ★ 判等口径（2026-10-24，事件 #2341）：
      · **集合不同 ⇒ 不一致**（漏选/错选照抓，绝不放宽）；
      · **集合相同、仅顺序不同 ⇒ 不算错**，只计「顺序差异」（让人看见，但不判 FAIL）。
    为什么：答案表按 key 值排序，而页面按 DOM/显示序读回；多选只要两者顺序不同，旧口径
    （字符串比较）就必误报 —— 实测 300 题里 48 道全这样，单选/判断 0 误报。
    ⚠ 判断题 true/false 是一个整体 key，必须走 _key_set 不拆。
    """
    bad = []
    order_diff = 0
    for qid, keys in (want or {}).items():
        exp_set = set(_as_key_list(keys))
        got_raw = (now or {}).get(qid, "")
        got_set = set(_as_key_list(got_raw))
        exp_sorted = "".join(sorted(exp_set))
        if exp_set != got_set:
            bad.append({"qid": qid, "期望": exp_sorted, "实际": got_raw})
        elif got_raw != exp_sorted:
            order_diff += 1
    return bad, order_diff


def _act_verify(args) -> dict:
    port = int(args.get("port") or 9222)
    raw = _read_any(args.get("answers") or os.path.join(WORK_DIR, "answers.json"))
    want = _answers_by_qid(raw)
    cur, err = _eval(JS_READ, port, url_contains="dowork")
    if not isinstance(cur, list):
        return {"错误": err or "读不到作答页", "不一致": 0}
    now = {x["qid"]: x["keys"] for x in cur}
    bad, order_diff = _verify_compare(want, now)
    unanswered = [x["qid"] for x in cur if not x.get("keys")]
    return {"题数": len(cur), "不一致": len(bad), "未作答": len(unanswered),
            "顺序差异": order_diff,
            "明细": bad[:50], "判定": "PASS" if not bad else "FAIL"}


def _act_save(args) -> dict:
    """「暂时保存」= saveWork()。**不提交**（交卷永远由人点）。"""
    port = int(args.get("port") or 9222)
    v, err = _eval(JS_SAVE, port, url_contains="dowork", timeout=30)
    time.sleep(3)
    st, _ = _eval(JS_STATUS, port, url_contains="dowork")
    return {"暂存": v, "已答": (st or {}).get("已答") if isinstance(st, dict) else None,
            "错误": err, "说明": "草稿已存服务端；提交按钮未碰"}


def t_quiz(args: dict) -> dict:
    """工具入口（core.dispatch 调它）。"""
    from agentctl.gate import CODE_INTERNAL                            # noqa: PLC0415
    action = str(args.get("action") or "status").strip()
    fn = {"status": _act_status, "dump": _act_dump, "truth": _act_truth,
          "apply": _act_apply, "verify": _act_verify, "save": _act_save}.get(action)
    if fn is None:
        return core.env(ok=False, code=CODE_INTERNAL,
                        warning="未知 action: %s（可用 status/dump/truth/apply/verify/save）" % action)
    try:
        data = fn(args or {})
    except Exception as e:
        return core.env(ok=False, code=CODE_INTERNAL,
                        warning="%s: %s" % (type(e).__name__, e))
    return core.env(ok=True, code=0, data=data, warning=None)
