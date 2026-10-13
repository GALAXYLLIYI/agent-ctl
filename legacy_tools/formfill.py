# -*- coding: utf-8 -*-
"""formfill.py —— 通用表单层（给"接本地模型"用）

把这三轮踩出来的东西固化成四个原语，任何页面只要**描述清楚**就能填：

    open_form(page_kw, opener_js, marker_js)   # 开表单：直接调注入函数（不是死磕点击）
    fill(page_kw, values)                      # 填值：按 name；下拉走"原生+自绘同步"
    verify(page_kw, required)                  # ★ 按 name 核验"将要提交的值"
    submit(page_kw, api)                       # 直接 POST（带强制 logid）

设计依据（每条都是踩出来的，别再犯）：
 ① **按钮是壳，函数才是门**：页面「添加」常是 JS 把 HTML 字符串模板注入进去，
    点 <a>/<li> 没用 → 先搜 `function 名字(` 找注入函数，直接调用。
 ② **动作后必须有到达条件**（配合 card.do_until）：不认"我以为点成功了"。
 ③ **判据要跟提交路径一致**：读原生 select 有值 ≠ 表单按 name 提交时有值。
 ④ **提交要带 logid**，且 logid 会因「清除重做」而变（用 _session.find_logid()）。
 ⑤ **落库核验必须重载后读列表**，不能读手上那份 DOM。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp as C  # noqa: E402


def _conn(page_kw):
    p = C.pick_page(page_kw)
    if not p:
        raise RuntimeError("找不到页面: %s" % page_kw)
    c = C.CDP(p["webSocketDebuggerUrl"])
    c.call("Runtime.enable")
    return c


def ev_of(c):
    def ev(js, aw=False):
        try:
            return c.js("(function(){ %s })()" % js, await_promise=aw)
        except Exception as x:
            return "ERR:" + str(x)[:200]
    return ev


# ── 填值：下拉用「原生 select + 自绘同步」，文本按 name ─────────
# ★★ 2026-09-20 轮 13 修：**同名元素可能有多个**（实测 nameform/jobNumberform/idNumberform 各 3 个），
#    getElementsByName(...)[0] 抓到的常常是页面顶部搜索区的那个，不是表单里的那个 ⇒ 设了等于没设。
#    **规矩：填值必须在【目标表单】范围内找元素。** 默认用 #gzform，可用 form_sel 指定。
FILLER_HEAD = """
var o=[];
var SCOPE = document.querySelector(%(scope)r) || document;
var byName=function(nm,v){
  var es=SCOPE.querySelectorAll('[name="'+nm+'"]');
  if(!es.length){ o.push(nm+'=无'); return; }
  var e=es[0];
  if(e.tagName==='SELECT'){
    var val=null;
    for(var i=0;i<e.options.length;i++){ if((e.options[i].text||'').trim()===v){ val=e.options[i].value; break; } }
    if(val===null){ val=v; }           /* 允许直接给 value */
    e.value=val;
  } else {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,v);
  }
  e.dispatchEvent(new Event('input',{bubbles:true}));
  e.dispatchEvent(new Event('change',{bubbles:true}));
  try{ if(window.layui&&layui.form) layui.form.render('select'); }catch(x){}
  /* 自绘层同步（校验/人眼都看它） */
  var host=e.parentElement;
  for(var u=0;u<3&&host;u++){
    var ins=host.querySelectorAll('input');
    if(ins.length){
      var t=(e.tagName==='SELECT')?((e.options[e.selectedIndex]||{}).text||''):'';
      if(t){ for(var k=0;k<ins.length;k++){ if(ins[k]!==e) ins[k].value=t; } }
      break;
    }
    host=host.parentElement;
  }
  o.push(nm+'='+String(e.value).slice(0,14));
};
"""


def fill(page_kw, values, form_sel="#gzform"):
    """values: {name: value}。**在 form_sel 指定的表单范围内**查找元素。

    form_sel=None 时回退到全局（但强烈不建议——同名元素会撞车）。
    """
    c = _conn(page_kw)
    c.need_action()   # ★ 写操作必须过 B+C 级闸门（2026-09-20 轮 20 补）
    ev = ev_of(c)
    scope = form_sel if form_sel else "document"
    head = FILLER_HEAD % {"scope": scope}
    body = head + "".join("byName(%r,%r);\n" % (k, str(v))
                          for k, v in values.items()) + "return JSON.stringify(o);"
    r = ev(body)
    c.close()
    return r


def verify(page_kw, required):
    """★ 按 name 核验【将要提交的值】——这才是判据。"""
    c = _conn(page_kw)
    ev = ev_of(c)
    js = """
    var need=%r, got={}, miss=[];
    document.querySelectorAll('input[name],select[name],textarea[name]').forEach(function(e){
      if(got[e.name]===undefined) got[e.name]=e.value; });
    need.forEach(function(n){ if(!got[n]) miss.push(n); });
    return JSON.stringify({缺:miss, 有:(need.length-miss.length)+'/'+need.length});
    """ % list(required)
    r = ev(js)
    c.close()
    return r


def submit(page_kw, api, logid):
    """按 form 序列化后 POST；强制带上 logid。"""
    c = _conn(page_kw)
    c.need_action()   # ★ 写操作必须过 B+C 级闸门（2026-09-20 轮 20 补）
    ev = ev_of(c)
    js = """
    var a=document.getElementsByName('currentIncome')[0] || document.querySelector('form input[name]');
    if(!a) return 'no_anchor';
    var fm=a.closest('form')||document.querySelector('form'); if(!fm) return 'no_form';
    var body='logid=%s&logId=%s'; var n=0;
    fm.querySelectorAll('input[name],select[name],textarea[name]').forEach(function(e){
      if(!e.name||e.name==='-') return;
      if(e.name==='logid'||e.name==='logId') return;
      if((e.type==='checkbox'||e.type==='radio')&&!e.checked) return;
      body+='&'+encodeURIComponent(e.name)+'='+encodeURIComponent(e.value||''); n++; });
    return (async function(){ try{
      var r=await fetch('%s',{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
        body:body});
      var t=await r.text();
      return JSON.stringify({fields:n,status:r.status,len:t.length});
    }catch(e){return 'ERR:'+e.message;} })();
    """ % (logid, logid, api)
    r = ev(js, aw=True)
    c.close()
    return r


def open_form(page_kw, opener_js, marker_js, tries=3, gap=3):
    """开表单：调用注入函数 → 用到达条件核验。"""
    c = _conn(page_kw)
    c.need_action()   # ★ 写操作必须过 B+C 级闸门（2026-09-20 轮 20 补）
    ev = ev_of(c)
    for i in range(1, tries + 1):
        r = ev(opener_js)
        time.sleep(gap)
        if ev(marker_js) is True:
            c.close()
            return True, r, i
    c.close()
    return False, r, tries


def link_hidden(page_kw, src_name, dst_name, pick_text=None, form_sel="#gzform"):
    """把"选人下拉的 UUID"写进隐藏关联字段。

    ★ 为什么需要（2026-09-20 实测）：改了自绘 `<select>` 的值 + 派发 change，
      **页面绑在自绘项上的处理器不会跑** ⇒ 页面本该派生的隐藏字段（如 `personId`）仍是空，
      提交后服务端收到 `personId:""`、`person:null`，**记录不关联人**（返 200 但数据是废的）。
      ⇒ 手动补写这个字段，是绕过页面 JS 的**正当做法**（不是作弊）。

    用法：
        formfill.link_hidden("fjmgongzixinjin", "nameform", "personId", pick_text="张*[0001]")
    返回 {"src": src值, "dst": dst值} 的 JSON 字符串。
    """
    c = _conn(page_kw)
    c.need_action()   # ★ 写操作必须过 B+C 级闸门（2026-09-20 轮 20 补）
    ev = ev_of(c)
    js = """
    var SCOPE = document.querySelector(%(form)r) || document;
    var s = SCOPE.querySelector('[name="%(src)s"]');
    if(!s) return 'no_src';
    if(%(txt)r){                       /* 按文本选中（可选） */
      for(var i=0;i<s.options.length;i++){
        if((s.options[i].text||'').trim()===%(txt)r){ s.value=s.options[i].value; break; }
      }
      s.dispatchEvent(new Event('change',{bubbles:true}));
      try{ if(window.layui&&layui.form) layui.form.render('select'); }catch(e){}
      var host=s.parentElement;
      for(var u=0;u<3&&host;u++){ var ins=host.querySelectorAll('input');
        if(ins.length){ var t=(s.options[s.selectedIndex]||{}).text||'';
          if(t){ for(var k=0;k<ins.length;k++){ if(ins[k]!==s) ins[k].value=t; } } break; }
        host=host.parentElement; }
    }
    var d = SCOPE.querySelector('[name="%(dst)s"]');
    if(!d) return 'no_dst';
    d.value = s.value;
    d.dispatchEvent(new Event('input',{bubbles:true}));
    d.dispatchEvent(new Event('change',{bubbles:true}));
    return JSON.stringify({src:s.value, dst:d.value, ok:(d.value===s.value && !!d.value)});
    """ % {"form": form_sel, "src": src_name, "dst": dst_name, "txt": pick_text or ""}
    r = ev(js)
    c.close()
    return r


if __name__ == "__main__":
    print("formfill.py —— 通用表单层：open_form / fill / verify / submit / link_hidden")
    print("用法见文件头部注释。")
