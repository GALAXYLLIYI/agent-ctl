# -*- coding: utf-8 -*-
"""_session.py —— 会话参数自动发现（不要把 logid 写死在脚本里）

由来：平台「清除重做」后同一案例会换新 logid，写死的脚本会全部失效（2026-09-20 实测）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp as C  # noqa: E402


def find_logid(hint=""):
    """从当前任一标签页 URL 里抓 logid。抓不到就抛错（附提示）。"""
    pat = re.compile(r"[?&]logid=([0-9a-f]{16,})")
    tl = C.http_get("/json/list")
    for t in tl:
        if t.get("type") != "page":
            continue
        url = t.get("url") or ""
        if hint and hint not in url:
            continue
        m = pat.search(url)
        if m:
            return m.group(1)
    # 退一步：不带 hint 再扫一遍
    for t in tl:
        m = pat.search(t.get("url") or "")
        if m:
            return m.group(1)
    raise RuntimeError("抓不到 logid —— 先在浏览器里打开任一个税务业务页（如人员信息采集）再来")


def find_caseid():
    pat = re.compile(r"[?&]caseid=([0-9a-f]{16,})")
    for t in C.http_get("/json/list"):
        m = pat.search(t.get("url") or "")
        if m:
            return m.group(1)
    return ""


if __name__ == "__main__":
    print("logid =", find_logid())
    print("caseid =", find_caseid())


# ── 登录态自愈（2026-09-20 轮 21 补）───────────────────────────
# 会话过期后访问业务页会被重定向到 /login。**别重开窗口，重登就行。**
LOGIN_URL = "http://tax.caidao8.com/login"
# ★ S3-A（2026-10-13）：**凭据不许写死在会进包的文件里**。
#   原来这里硬编码了实训系统的 schoolCode / accountCode（含手机号）/
#   accountPwd（明文口令）—— 而 _session.py 是 padd2 的运行期依赖、会被打进发布包
#   ⇒ 等于把凭据随包分发。现改为**环境变量注入**，
#   口径沿用本目录既有写法（os.environ.get + AGENTCTL_ 前缀，见 up.py:138 / gate.py:69）。
#   三项缺任何一项 ⇒ 那一项不进 CRED，并在 stderr **明确提示**——
#   不让“没配凭据”退化成“静默填了半截、登录失败却不知道为什么”。
_CRED_ENV = (("#schoolCode", "AGENTCTL_PADD_SCHOOL"),
             ("#accountCode", "AGENTCTL_PADD_ACCOUNT"),
             ("#accountPwd", "AGENTCTL_PADD_PWD"))
CRED = {}
CRED_MISSING = []
for _sel, _env in _CRED_ENV:
    _val = (os.environ.get(_env) or "").strip()
    if _val:
        CRED[_sel] = _val
    else:
        CRED_MISSING.append(_env)
if CRED_MISSING:
    sys.stderr.write("[_session] 未配置实训系统凭据：%s"
                     "—— 请在环境变量里设好后再用 padd2，"
                     "否则登录字段不会被填。\n" % "、".join(CRED_MISSING))


def is_login_page(page_kw="caidao8"):
    """当前匹配页是不是停在登录页。"""
    try:
        for t in C.http_get("/json/list"):
            if t.get("type") != "page":
                continue
            u = t.get("url") or ""
            if page_kw not in u:
                continue
            return u.rstrip("/").endswith("/login")
    except Exception:
        pass
    return False


def ensure_login(page_kw="caidao8", verbose=True):
    """确保处于登录态：被踢回 /login 就自动重登。返回 True=已登录。"""
    if not is_login_page(page_kw):
        return True
    if verbose:
        print("  ⚠ 检测到登录态过期，自动重登…")
    try:
        for t in C.http_get("/json/list"):
            if t.get("type") != "page" or page_kw not in (t.get("url") or "") :
                continue
            c = C.CDP(t["webSocketDebuggerUrl"])
            c.call("Runtime.enable")
            c.need_action()          # 填表要过闸
            for sel, val in CRED.items():
                c.js("(function(){var e=document.querySelector(%r); if(!e) return 'no';"
                     "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,%r);"
                     "e.dispatchEvent(new Event('input',{bubbles:true}));"
                     "e.dispatchEvent(new Event('change',{bubbles:true})); return 'ok';})()" % (sel, val))
                import time as _t
                _t.sleep(0.4)
            c.js("(function(){var a=document.querySelector('.dl'); if(!a) return 'no'; a.click(); return 'ok';})()")
            import time as _t
            _t.sleep(6)
            c.close()
            ok = "/home" in (C.http_get("/json/list")[0].get("url") or "") or not is_login_page(page_kw)
            if verbose:
                print("  ✅ 重登" + ("成功" if ok else "后仍未离开登录页，请人工确认"))
            return ok
    except Exception as e:
        if verbose:
            print("  ✗ 自动重登失败:", str(e)[:150])
    return False


def pick_page_by_logid(substr="", logid=None, caseid=None, page_kw=None):
    """按 logid / caseid【精确】挑页面；page_kw 再叠加一层关键字过滤。

    返回 raw target dict（含 webSocketDebuggerUrl），找不到返回 None。
    """
    tl = C.http_get("/json/list")
    best = None
    for t in tl:
        if t.get("type") != "page":
            continue
        u = t.get("url") or ""
        if logid and logid not in u:
            continue
        if caseid and caseid not in u:
            continue
        if substr and substr not in u:
            continue
        if page_kw and page_kw not in u:
            continue
        best = t
        break
    return best


def pick_case_page(logid=None, caseid=None, page_kw="personinfo"):
    """连到指定案例的业务页（多题同开也准）。返回 (CDP连接, target)。

    用法：
        from _session import pick_case_page, find_logid
        lg = find_logid()
        c, t = pick_case_page(logid=lg, page_kw="personinfo")
    """
    if logid is None:
        logid = find_logid()
    t = pick_page_by_logid(logid=logid, caseid=caseid, page_kw=page_kw)
    if not t:
        raise RuntimeError("按 logid=%s / page_kw=%s 找不到页面 —— 可能没进这道题" % (logid, page_kw))
    c = C.CDP(t["webSocketDebuggerUrl"])
    c.call("Runtime.enable")
    return c, t
