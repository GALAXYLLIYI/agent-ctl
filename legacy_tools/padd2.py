# -*- coding: utf-8 -*-
"""padd2.py —— 通用人员录入（数据驱动）

★ 与 _padd.py 的区别：**人员数据不写死在代码里**，从 `case_data.json` 读。
  这样"换一道题"只是换数据，不是改代码 —— 这是向"本地模型能用"迈的一步。

用法：
  python padd2.py --case 案例5_分类所得偶然所得          # 录该案例全部人员
  python padd2.py --case 案例5_分类所得偶然所得 --only 汤晴
  python padd2.py --case 案例5_分类所得偶然所得 --dry     # 只填不提交

要点（都是踩出来的）：
  · 切对子标签（境内人员 / 境外人员）—— 两边的表单字段**不一样**
  · 填值必须在【目标表单范围内】查找（同名元素有 3 个）
  · 下拉按 option **文本**匹配，并同步自绘层
  · 提交按 name 序列化，**响应 200 ≠ 落库，要重载后看列表**
"""
import argparse
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import cdp as C

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "case_data.json")

WRAP = """(function(){
var f=Array.from(document.querySelectorAll('iframe')).filter(function(x){
  return (x.src||'').indexOf('personalTaxPerson')>=0})[0];
if(!f) return 'no_frame'; var d=f.contentDocument; if(!d) return 'null_doc';
%s })()"""


def load(case):
    with io.open(DATA, encoding="utf-8") as f:
        d = json.load(f)
    if case not in d:
        raise SystemExit("case_data.json 里没有 %s（有：%s）"
                         % (case, [k for k in d if not k.startswith("_")]))
    return d[case]


def conn():
    """连到【案例】的实训系统页（多题同时开时必须挑对）。"""
    import _session as S
    lg = S.find_logid()            # 从页面 URL 抓 logid
    p = None
    for t in C.http_get("/json/list"):
        if t.get("type") != "page":
            continue
        u = t.get("url") or ""
        if "personinfo/homepage" in u and lg in u:
            p = t
            break
    if not p:                      # 退一步：只要 personinfo 页
        p = C.pick_page("personinfo")
    if not p:
        raise SystemExit("找不到实训系统页（先跑 case_open.py 进案例）")
    c = C.CDP(p["webSocketDebuggerUrl"])
    c.call("Runtime.enable")
    c.need_action()
    return c


def ev(c, e, aw=False):
    try:
        return c.js(WRAP % e, await_promise=aw)
    except Exception as x:
        return "ERR:" + str(x)[:200]


FILL = """
var fm=d.querySelector('#personalTaxPersonEdit'); if(!fm) return 'no_form';
var byName=function(nm,v){
  var es=fm.querySelectorAll('[name="'+nm+'"]'); if(!es.length) return nm+'=无';
  var e=es[0];
  if(e.tagName==='SELECT'){
    var val=null; for(var i=0;i<e.options.length;i++){ if((e.options[i].text||'').trim()===v){ val=e.options[i].value; break; } }
    if(val===null) return nm+'=缺选项:'+v;
    e.value=val;
  } else { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,v); }
  e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));
  try{ if(window.layui&&layui.form) layui.form.render('select'); }catch(x){}
  var host=e.parentElement;
  for(var u=0;u<3&&host;u++){ var ins=host.querySelectorAll('input');
    if(ins.length){ var t=(e.tagName==='SELECT')?((e.options[e.selectedIndex]||{}).text||''):'';
      if(t){ for(var k=0;k<ins.length;k++){ if(ins[k]!==e) ins[k].value=t; } } break; }
    host=host.parentElement; }
  return nm+'='+String(e.value).slice(0,14);
};
var o=[];
%s
return JSON.stringify(o);
"""


def fill_person(c, p):
    """按 JSON 字段填；缺的字段跳过（境内人员没有 entry/leave）。"""
    pairs = [("name", p["name"]), ("idType", p["idType"]), ("idNumber", p["idNumber"]),
             ("sex", p.get("sex")), ("birthday", p.get("birthday")),
             ("employedType", p.get("employedType")), ("employedDate", p.get("employedDate")),
             ("phoneNumber", p.get("phone")), ("jobNumber", p.get("job"))]
    if p.get("nat"):
        pairs.insert(3, ("nationalitySelect", p["nat"]))
    if p.get("entry"):
        pairs.append(("dateOfEntry", p["entry"]))
    if p.get("leave"):
        pairs.append(("departureDate", p["leave"]))
    body = "".join("o.push(byName(%r,%r));\n" % (k, str(v)) for k, v in pairs if v)
    return ev(c, FILL % body)


def verify(c, p):
    """★ 按 name 核验将要提交的值（缺字段就不该提交）。"""
    need = ["logid", "idType", "idNumber", "name", "sex", "birthday", "employedDate"]
    js = """
    var fm=d.querySelector('#personalTaxPersonEdit'); if(!fm) return 'no_form';
    var got={}, miss=[];
    fm.querySelectorAll('[name]').forEach(function(e){ if(got[e.name]===undefined) got[e.name]=e.value; });
    %r.forEach(function(n){ if(!got[n]) miss.push(n); });
    return JSON.stringify({缺:miss, 有:(%d-miss.length)+'/'+%d});
    """ % (need, len(need), len(need))
    return ev(c, js)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--only", help="只录某个人（按姓名）")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    data = load(a.case)
    tab = data.get("tab", "境外人员")
    base = os.path.join(HERE, "_session.py")
    if os.path.exists(base):
        import _session as S
        S.ensure_login(verbose=True)          # ★ 登录自愈

    c = conn()
    print("== 案例：%s（子标签=%s）==" % (a.case, tab))
    print("① 切「%s」子标签:" % tab,
          ev(c, """var h=null; d.querySelectorAll('li').forEach(function(e){
              if((e.innerText||'').trim()===%r && e.children.length===0){ e.click(); h='ok'; }});
              return h||'no';""" % tab))
    time.sleep(2)

    for p in data["people"]:
        if a.only and p["name"] != a.only:
            continue
        print("-- %s --" % p["name"])
        print("   点添加:", ev(c, """var h=null; d.querySelectorAll('*').forEach(function(e){
            if((e.innerText||'').trim()==='添加' && e.children.length===0 && e.tagName!=='TD'){ e.click(); h='ok'; }});
            return h||'no';"""))
        time.sleep(3.5)
        print("   填:", fill_person(c, p))
        time.sleep(1)
        v = verify(c, p)
        print("   ★按name核验:", v)
        if a.dry:
            print("   (--dry 不提交)")
            continue
        print("   提交:", ev(c, """
        var fm=d.querySelector('#personalTaxPersonEdit'); if(!fm) return 'no_form';
        var body=''; var n=0;
        fm.querySelectorAll('input[name],select[name],textarea[name]').forEach(function(e){
          if(!e.name||e.name==='-') return;
          if((e.type==='checkbox'||e.type==='radio') && !e.checked) return;
          body += (n?'&':'')+encodeURIComponent(e.name)+'='+encodeURIComponent(e.value||''); n++; });
        return (async function(){ try{
          var r=await fetch('/personalTaxPerson/personalTaxPersonSave',{method:'POST',credentials:'same-origin',
            headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
            body:body});
          var t=await r.text();
          var has=function(k){ return t.indexOf(k)>=0; };
          return 'fields='+n+' st='+r.status+' 含'+%r+'='+has(%r);
        }catch(e){return 'ERR:'+e.message;} })();""" % (p["name"], p["name"]), aw=True))
        time.sleep(3)

    # 重载核验（★ 不看清旧 DOM）
    print("② 重载核验:", ev(c, """var f=Array.from(d.querySelectorAll('iframe')).filter(function(x){
        return (x.src||'').indexOf('personalTaxPerson')>=0})[0];
        f.contentWindow.location.reload(); return 'reloading';"""))
    time.sleep(9)
    print("   总人数:", ev(c, "var t=(d.body.innerText||''); var i=t.indexOf('总人数'); return t.slice(i,i+34).replace(/\\s+/g,' ');"))
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
