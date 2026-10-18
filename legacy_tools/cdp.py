# -*- coding: utf-8 -*-
"""cdp.py —— 用 Chrome DevTools Protocol 直接操作网页（**不靠坐标、不抢鼠标**）

为什么换这条路（2026-09-20 实测教训）：
  前几天一直用「截图 + 坐标点击」操作练习/考试站点。它有三个死穴：
    ① **窗口会跑** —— 我点了一下"最大化"，紧接着那一击就落到任务视图上了；
    ② **坐标要反复标定** —— 一晚上在同一类偏差上摔了 4 次（点击差 3px 就出界）；
    ③ **抢用户的鼠标** —— 每次点击都 SetCursorPos，用户正在用电脑就被打断。
  而练习站点 `tax.caidao8.com` 是**普通网页**，浏览器自带 **CDP**（需带
  `--remote-debugging-port` 启动）：
    · 按 **CSS 选择器**取元素、点元素、设输入值 → **不依赖任何屏幕坐标**
    · 直接读 **DOM / 可见文字 / 元素几何** → 判断依据来自**页面本身**，不是像素
    · **完全不动鼠标** → 用户可以同时在用电脑
  这是质的升级：从"看像素"变成"看结构"。

用法（默认连 127.0.0.1:9222；实例在别的端口就加 `--port <端口>`）：
  python cdp.py pages [--port 9222]        # 列出标签页
  python cdp.py text  [--url 关键字]        # 页面可见文字
  python cdp.py html  [--url 关键字] [--sel CSS]   # 页面/元素 HTML
  python cdp.py find  <选择器>              # 列出匹配到的元素（文字+矩形+可点性）
  python cdp.py click <选择器|--text 文字>   # 点元素（派发真实鼠标事件）
  python cdp.py fill  <选择器> <值>          # 给输入框设值（带 input/change 事件）
  python cdp.py eval  <JS>                  # 执行任意 JS 并回传结果
  python cdp.py shot  <文件名>               # 用 CDP 截图（不碰鼠标、不调 PrintWindow）
  python cdp.py goto  <URL>                 # 当前标签页跳转
"""
import argparse
import base64
import json
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import websocket  # websocket-client

# ── 过闸门（2026-09-20 补）────────────────────────────────
# 为什么必须补：cdp.py 能读页面、能点、能填 —— **它就是在控制用户的电脑**，
# 只不过换了个通道（CDP 而不是鼠标）。旧规矩（刹车＋通行证＋范围匹配）不能因为
# "换了条路"就不走，否则就是我自己给自己开后门。
#   · 只读类（pages/text/html/find/eval/shot/cookies）→ A级 只读感知
#   · 动作类（click/fill/goto）                       → B级 点击滚动 ＋ C级 打字快捷键
READONLY_CMDS = {"pages", "text", "html", "find", "eval", "shot", "cookies"}
ACTION_CMDS = {"click", "fill", "goto", "open"}


def gate_check(cmd):
    """过闸门。返回 None＝放行，否则返回错误说明字符串。"""
    import os
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    gate = os.path.join(here, "gate.py")
    if not os.path.exists(gate):
        return None                       # 没装闸门就放行，不让工具互相锁死
    scopes = ["A级 只读感知"] if cmd in READONLY_CMDS else ["B级 点击滚动", "C级 打字快捷键"]
    why = "cdp.py %s（网页结构化操作）" % cmd
    for sc in scopes:
        p = subprocess.run([sys.executable, gate, "--check", sc, "--why", why], capture_output=True)
        if p.returncode != 0:
            msg = (p.stdout + p.stderr).decode("utf-8", errors="replace").strip()
            last = msg.splitlines()[-1] if msg else "无说明"
            return "⛔ 闸门不放行（%s）：%s" % (sc, last)
    return None



# ★ 2026-10-18（窗B）：调试端口不再是常量 —— `--port` 落在下面这个 PORT 上（默认 9222，向后兼容）。
#   为什么用模块级变量、而不给每个 cmd_* 加一个 port 参数：这条路径上 pick_page / open_target /
#   cmd_* 有十来处都要透传端口，**漏一处就又是「非 9222 不可用」**（G1 如实报过的那句）。
#   收在一个地方，main() 解析完 --port 写进来，其余全部自动跟着走。
#   没经过 main()（比如别的脚本 import cdp 直接调）就还是 9222 —— 与改前行为一致。
PORT = 9222


def http_get(path, host="127.0.0.1", port=None):
    import urllib.request
    port = int(port if port is not None else PORT)
    with urllib.request.urlopen("http://%s:%d%s" % (host, port, path), timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_page(keyword=None, kind="page"):
    tl = http_get("/json/list")
    pages = [t for t in tl if t.get("type") == kind]
    if keyword:
        pages = [p for p in pages if keyword.lower() in (p.get("url", "") + p.get("title", "")).lower()]
    if not pages:
        return None
    return pages[0]


class CDP(object):
    def __init__(self, ws_url):
        # ★★ 2026-09-20 轮 19 补：**把过闸门挪进类里**
        #   以前 gate 检查只在 main() 里 —— 于是任何 `import cdp` 的脚本（person_add/fjm_*/formfill…）
        #   都**绕开了闸门**。这等于"换了条路就不用拿钥匙"，是我最该堵的洞。
        #   现在：**建立 CDP 连接本身就要 A级只读感知**（最低门槛）；
        #   要点击/填表，脚本必须再显式调 need_action()（B+C级）。
        _err = gate_check("eval")          # 按"只读"口径过闸
        if _err:
            raise PermissionError(_err)
        self.ws = websocket.create_connection(ws_url, timeout=30, max_size=64 * 1024 * 1024)
        self.i = 0

    def need_action(self):
        """做点击/填表/提交前必须调一次（要 B级点击滚动 + C级打字快捷键）。"""
        err = gate_check("click")
        if err:
            raise PermissionError(err)
        return True

    def call(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                if "error" in msg:
                    raise RuntimeError("CDP %s 出错: %s" % (method, msg["error"]))
                return msg.get("result", {})

    def js(self, expr, await_promise=False):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=await_promise, userGesture=True)
        if r.get("exceptionDetails"):
            raise RuntimeError("JS 异常: %s" % json.dumps(r["exceptionDetails"], ensure_ascii=False)[:300])
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def open_target(keyword=None):
    p = pick_page(keyword)
    if not p:
        print("没有匹配的标签页（关键字=%s）。用 pages 看看有哪些。" % keyword)
        sys.exit(1)
    c = CDP(p["webSocketDebuggerUrl"])
    c.call("Runtime.enable")
    return c, p


# ── 子命令 ────────────────────────────────────────────────
def cmd_pages(a):
    tl = http_get("/json/list")
    for t in tl:
        if t.get("type") == "page":
            print("  [%s] %s\n        %s\n        id=%s" % (t.get("type"), t.get("title"), t.get("url"), t.get("id")))


def cmd_text(a):
    c, p = open_target(a.url)
    print("页面: %s\n%s" % (p.get("title"), p.get("url")))
    txt = c.js("document.body ? document.body.innerText : ''")
    c.close()
    print("=" * 70)
    print(txt)


def cmd_html(a):
    c, p = open_target(a.url)
    expr = "document.querySelector('%s').outerHTML" % a.sel if a.sel else "document.documentElement.outerHTML"
    h = c.js(expr)
    c.close()
    print(h if h else "(空)")


def cmd_find(a):
    c, p = open_target(a.url)
    expr = """
    (function(sel){
      var out=[], els=document.querySelectorAll(sel);
      for (var i=0;i<els.length && i<40;i++){
        var e=els[i], r=e.getBoundingClientRect(), cs=getComputedStyle(e);
        out.push({i:i, tag:e.tagName, text:(e.innerText||e.value||'').trim().slice(0,60),
                  x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height),
                  vis:(cs.display!=='none'&&cs.visibility!=='hidden'&&r.width>0&&r.height>0),
                  cls:e.className && e.className.toString ? e.className.toString().slice(0,50) : ''});
      }
      return JSON.stringify(out);
    })('%s')
    """ % a.sel
    out = json.loads(c.js(expr) or "[]")
    c.close()
    print("匹配 %d 个（最多显示 40）:" % len(out))
    for o in out:
        print("   #%-2d %-8s vis=%-5s rect=(%d,%d %dx%d) text=%s | cls=%s"
              % (o["i"], o["tag"], o["vis"], o["x"], o["y"], o["w"], o["h"], o["text"], o["cls"]))


def _click_js(sel=None, text=None):
    if text:
        return """
        (function(t){
          var all=document.querySelectorAll('a,button,div,span,li,td,label,input');
          for (var i=0;i<all.length;i++){
            var e=all[i], s=(e.innerText||e.value||'').trim();
            if (s===t || (s.indexOf(t)>=0 && s.length<t.length+8)){
              var r=e.getBoundingClientRect();
              if (r.width>0 && r.height>0){
                ['mousedown','mouseup','click'].forEach(function(ev){
                  e.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window,
                    clientX:r.x+r.width/2, clientY:r.y+r.height/2}));
                });
                return 'clicked: '+e.tagName+' '+s.slice(0,40);
              }
            }
          }
          return 'NOT_FOUND';
        })('%s')
        """ % text.replace("\\", "\\\\").replace("'", "\\'")
    return """
    (function(sel){
      var e=document.querySelector(sel);
      if(!e) return 'NOT_FOUND';
      var r=e.getBoundingClientRect();
      ['mousedown','mouseup','click'].forEach(function(ev){
        e.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window,
          clientX:r.x+r.width/2, clientY:r.y+r.height/2}));
      });
      return 'clicked: '+e.tagName+' '+(e.innerText||e.value||'').trim().slice(0,40);
    })('%s')
    """ % sel.replace("\\", "\\\\").replace("'", "\\'")


def cmd_click(a):
    c, p = open_target(a.url)
    print(c.js(_click_js(a.sel, a.text)))
    time.sleep(a.wait)
    c.close()


def cmd_fill(a):
    c, p = open_target(a.url)
    expr = """
    (function(sel,val){
      var e=document.querySelector(sel);
      if(!e) return 'NOT_FOUND';
      e.focus();
      var proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
      var setter=Object.getOwnPropertyDescriptor(proto,'value').set;
      setter.call(e,val);
      e.dispatchEvent(new Event('input',{bubbles:true}));
      e.dispatchEvent(new Event('change',{bubbles:true}));
      return 'filled: '+sel+' = '+val;
    })('%s','%s')
    """ % (a.sel.replace("'", "\\'"), a.value.replace("\\", "\\\\").replace("'", "\\'"))
    print(c.js(expr))
    c.close()


def cmd_eval(a):
    c, p = open_target(a.url)
    v = c.js(a.js)
    c.close()
    print(json.dumps(v, ensure_ascii=False, indent=1) if not isinstance(v, str) else v)


def cmd_shot(a):
    c, p = open_target(a.url)
    c.call("Page.enable")
    r = c.call("Page.captureScreenshot", format="png")
    c.close()
    data = base64.b64decode(r["data"])
    with open(a.file, "wb") as f:
        f.write(data)
    print("已存 %s  (%d 字节)  —— 用 CDP 截的，没碰窗口也没碰鼠标" % (a.file, len(data)))


def cmd_cookies(a):
    """导出当前标签页的 cookies（含 HttpOnly）——给"用 Python 带会话取文件"用。

    为什么要它：PDF/附件的接口（如 `/student/viewpdf`）**不带会话就返回登录页**，
    直接下载会拿到一坨 HTML。从 CDP 拿真 cookie 再下载，才是"以登录态取件"。
    """
    p = pick_page(a.url)
    if not p:
        print("没有匹配的标签页")
        return 1
    c = CDP(p["webSocketDebuggerUrl"])
    c.call("Network.enable")
    r = c.call("Network.getCookies", urls=[p["url"]])
    c.close()
    cks = r.get("cookies", [])
    if a.json:
        print(json.dumps(cks, ensure_ascii=False, indent=1))
    else:
        print("; ".join("%s=%s" % (k["name"], k["value"]) for k in cks))
    return 0


def cmd_open(a):
    """开（或复用）一个标签页到指定 URL。

    ★ 与手工 Start-Process 的区别：**优先复用已在跑的 CDP 实例**，用 CDP 自己开标签，
      不会每开一次就多一个浏览器进程（今天为此堆了 15 个进程）。
    """
    import os
    import urllib.request
    # ★ T14 阶段 0-2：本函数用到 os / subprocess，却只 import 了 urllib.request ——
    #   走到「复用失败 → 拉新窗口」那条分支（subprocess.Popen）就 NameError。
    #   上面 gate_check() 里也是函数内 import，照它的写法补，风格一致。
    #   （os 不在这里补：本函数当前用的是硬编码路径串；打包净化把它换成
    #     os.path.join 时，gen_legacy.py 会在同处注入 import os。）
    import subprocess
    url = a.target or a.arg
    if not url:
        print("要开哪个 URL？ 例：cdp.py open http://example.com")
        return 1
    # 先试复用：在本浏览器里新开标签（Target.createTarget）
    try:
        tl = http_get("/json/list")
        page = next(t for t in tl if t.get("type") == "page")
        c = CDP(page["webSocketDebuggerUrl"])
        r = c.call("Target.createTarget", url=url)
        c.close()
        print("✅ 已在本浏览器新开标签: %s" % r.get("targetId"))
        return 0
    except Exception as e:
        print("  （复用失败，退回拉新窗口）:", str(e)[:110])
    # 端口跟着 --port 走：复用失败要拉新窗口时，新实例必须开在**同一个**端口上，
    #   否则会出现「在 9333 上开的页、下一条命令却又去 9222 找」的错位。
    subprocess.Popen([a.exe or "msedge.exe",
                      "--remote-debugging-port=%d" % int(getattr(a, "port", None) or PORT),
                      "--remote-allow-origins=*",
                      "--user-data-dir=" + os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl", "_edge_cdp"),
                      "--new-window", url])
    print("✅ 已拉起浏览器窗口:", url)
    return 0


def cmd_wait(a):
    """等条件成立：轮询页面 JS，直到返回真值或超时。

    ★ 等待必须【从外部轮询】—— 绝不能在页面里写忙等循环（会锁死渲染，今天踩过）。
    """
    c, p = open_target(a.url)
    js = a.js or a.arg
    if not js:
        print("要等什么条件？ 例：cdp.py wait --js 后面接一个返回真值的 JS 表达式")
        c.close()
        return 1
    t0 = time.time()
    while True:
        try:
            v = c.js(js)
        except Exception as e:
            v = None
            err = str(e)[:120]
        if v:
            print("✅ 条件成立（%.1fs）: %s" % (time.time() - t0, json.dumps(v, ensure_ascii=False)[:120]))
            c.close()
            return 0
        if time.time() - t0 > (a.timeout or 30):
            print("✗ 超时（%.0fs）条件仍未成立: %s" % (time.time() - t0, js[:100]))
            c.close()
            return 3
        time.sleep(a.gap or 0.6)


def cmd_goto(a):
    c, p = open_target(a.url2)
    print(c.js("location.href='%s'; 'navigating'" % a.target))
    time.sleep(a.wait)
    print("现在:", c.js("location.href"))
    c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pages", "text", "html", "find", "click", "fill", "eval", "shot", "goto", "cookies", "open", "wait"])
    ap.add_argument("arg", nargs="?", help="选择器 / JS / 文件名 / 文本")
    ap.add_argument("value", nargs="?", help="fill 的值")
    ap.add_argument("--url", help="按 URL/标题关键字挑标签页")
    ap.add_argument("--sel", help="html：只取该选择器的 HTML")
    ap.add_argument("--text", help="click：按可见文字点（不必写选择器）")
    ap.add_argument("--js", help="eval：要执行的 JS")
    ap.add_argument("--file", help="shot：输出文件名")
    ap.add_argument("--url2", help="goto：目标标签页关键字")
    ap.add_argument("--target", help="goto：要跳到的 URL")
    ap.add_argument("--wait", type=float, default=1.5)
    ap.add_argument("--json", action="store_true", help="cookies：输出 JSON")
    ap.add_argument("--exe", help="open：浏览器可执行文件名")
    ap.add_argument("--timeout", type=float, default=30, help="wait：超时秒数")
    ap.add_argument("--gap", type=float, default=0.6, help="wait：轮询间隔")
    ap.add_argument("--port", type=int, default=9222,
                    help="CDP 调试端口（默认 9222；实例开在别的端口时用它指过去）")
    a = ap.parse_args()

    # ★ 窗B：解析完就写进 PORT，**闸门检查之前** —— 这样连报错路径也指着同一个端口。
    global PORT
    PORT = int(a.port)
    a.port = PORT

    # ★ 过闸门：所有命令都要先拿证（只读 A 级；点击/填表 B+C 级）
    err = gate_check(a.cmd)
    if err:
        print(err)
        return 9

    if a.cmd == "pages":
        cmd_pages(a)
    elif a.cmd == "text":
        cmd_text(a)
    elif a.cmd == "html":
        a.sel = a.sel or a.arg
        cmd_html(a)
    elif a.cmd == "find":
        a.sel = a.sel or a.arg
        cmd_find(a)
    elif a.cmd == "click":
        a.sel = a.sel or (None if a.text else a.arg)
        cmd_click(a)
    elif a.cmd == "fill":
        a.sel = a.sel or a.arg
        a.value = a.value if a.value is not None else ""
        cmd_fill(a)
    elif a.cmd == "eval":
        a.js = a.js or a.arg
        cmd_eval(a)
    elif a.cmd == "shot":
        a.file = a.file or a.arg
        cmd_shot(a)
    elif a.cmd == "cookies":
        return cmd_cookies(a)
    elif a.cmd == "open":
        return cmd_open(a)
    elif a.cmd == "wait":
        return cmd_wait(a)
    elif a.cmd == "goto":
        a.url2 = a.url2 or a.url
        a.target = a.target or a.arg
        cmd_goto(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
