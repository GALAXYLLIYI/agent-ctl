# -*- coding: utf-8 -*-
r"""tests\_像素不判成功_判据.py —— 第 32 条判据：**像素有变化 ≠ 动作生效**。

治的病（2026-10-19 16:08 真跑撞出来的，不是推演）：
    在 QQ 上发消息，回车那步的日志写着「后台按键生效（像素证据有变化）」。
    **这一条当时真的成功了**，但成功不能证明这条路可靠 —— 同一种判据在
    今天上午微信那轮里，把打出来的**字面量 v**（vvaaav）读成「后台按键生效」并 return 0。

★ 立论依据是**代码自相矛盾**，不是我的推测（同一个文件里四处打架）：
    act.py:2026  log("后台按键生效（像素证据有变化）")         <- 用像素判生效
    act.py:2042  「那种乱字符**和成功在像素上长得一样**，判不出来」 <- 承认判不出
    act.py:2395  「投递成功 != 生效…判不出来就报未验」         <- 自己定的规矩
    act.py:2849  「页面无变化——别当成功，去核验」             <- 又一次承认不够

## ★ 本判据的第一版是**坏的**，已修（留在这里当判例）
    v1 的断言(1)是「生效判词上方 14 行内有没有三值处理」。实测：
      · **误报 4 处** —— 抓到的是提示文本「动作发出去了但没生效」、
        「本次放行**照常生效**」（指放行不是动作）、「终端临时放行生效」、
        甚至 FOREGROUND_SWITCH_TIMEOUT 这行根本没有「生效」二字的常量行；
      · **漏报唯一那个真病灶（2026 行）** —— 因为它上面有 _kch = None if (...) 这一行，
        关键词「is None」被满足了，于是被判成「有三值处理」。
    ⇒ **判据被被测对象骗过了**（真源自己就写着 _kch 能算出 None，看起来很讲究）。
      这正是「判据必须比被测对象更严」那条的复现。
    修法：断言(1) 不再看「有没有三值」，改为**盯住真正的病灶形状**：
      **以「指纹/像素有变化」为条件的分支，其分支体内不许 return 0。**

## ★ 第 (3) 条（P0-3）也腐烂过一次 —— **判据与产品结构漂移**（2026-10-23 修）
    病灶（2026-10-21 P0-3 立的）：do_click 里 UIA.Invoke / SelectionItem 那条分支
    **从不计算 changed** ⇒ 末尾 changed is True 恒不成立 ⇒ 恒返回 rc=8
    （「name 点击即便 Invoke 成功、界面真的变了，回执也说画面没变化」）。
    ⇒ 当时判据(3) 的形态是「在锚点后面找**指纹式**那一行
      `changed = None if (fp0 is None or fp1 is None) else (fp1 != fp0)`」。
    ★ **后来产品变了（M12 阶段三 A）**：那条分支改用**等生效轮询**的三态
      `changed = _eff.get("生效")` ⇒ **changed 确实算了**；
      指纹式那一行则留在了**别的分支**（do_keys）⇒
      **判据的锚点过期 ⇒ 本条长期红着（红得没道理：产品没那个病）。**
    ⇒ 本次修法（**只改判据，不碰产品**）：
      · (3) 的「有没有算 changed」改成**结构化**判定：
        **先按锚点定位到 UIA.Invoke 那条 else 分支**，再在**该分支体内**
        用正则认 `changed = …` 的赋值语句（排除注释行），
        **任一**下列形态出现即算「算了 changed」：
          ① `changed = _eff.get("生效")`（等生效轮询三态，现役形态）
          ② 指纹式 `changed = None if (fp0 is None or fp1 is None) else (fp1 != fp0)`
          ③ 其它**等价**的三态赋值（右值不是哨兵 `None` 的任何表达式）
        ★ 右值是**裸 `None`** 的（`changed = None`）**不算「算了」** ——
          那只是把「判不了」的哨兵摆上去，正是那个真 bug 的落点。
      · 注入定位修好：先定位 UIA.Invoke 分支，再在**该分支体内**改那一行；
        **绝不用** `text.replace(…, 1)`（那会打到全文件第一处 = do_keys 分支 ⇒ 打错分支 = 自证无效）。
      · 三个子条件**(a) 算 changed /(b) None 说「判不了」/(c) retry 默认 0** 一个没少、没放宽。

## 三条断言
  (1) **主断言**：凡 if <像素/指纹比较结果>: 分支，3 行内出现 return 0 ⇒ 红。
      即「画面变了」在代码里**不许被直接等同于成功**。
  (2) **自述一致性**：同一文件里既然写了「判不出来 / 别当成功 / 不可区分」，
      就不许再有「用像素判生效」的实现 —— 两句话不能并存。
      ★ 计数口径**故意不对称**：自述(DISCLAIM)连注释一起数，
      实现(PIXEL_CLAIM)**排除注释行** —— 因为 act.py:2008 那句
      「日志逐行写着✔后台按键生效」是**历史证据记录**，不是实现，
      算进去会把「真跑留过这种日志」误判成「现在还这么判」。
  (3) **P0-3**：UIA.Invoke 路径必须算 changed + None 说「判不了」 + retry 默认 0。
  ★ **注入即红**：--inject 把 UIA.Invoke 分支的 changed 计算打成「不算」⇒ (3) 必须变红。

## ★ 跑法
    python -X utf8 tests\_像素不判成功_判据.py              # 正跑：期望 exit=0（三条全绿）
    python -X utf8 tests\_像素不判成功_判据.py --inject     # 注入 P0-3 那个病：期望 exit=1，**只红 (3)**
    python -X utf8 tests\_像素不判成功_判据.py --inject1    # 注入 (1) 那个病：期望 exit=1，**只红 (1)**
    python -X utf8 tests\_像素不判成功_判据.py --inject-all # 两个病一起注：期望 (1)(3) 都红
    ★ --inject 的**注入点打印在输出里**（打在第几行、替换前后的原文）⇒ 人可核。

★ 诚实边界：
  · 验的是**判定依据的性质**，不验「这次动作到底生不生效」——后者只能靠
    latent state 内层溯源（铁律 11(9) 的验动作.py），不是本条口径。
  · **纯静态**，不碰闸门、不碰屏幕、不需要通行证 => 用户不在场也能跑。
  · (1) 是形状匹配，可能漏报「换个变量名」的同类写法；宁可漏报不可误报 ——
    误报会让真源被无谓改动，比漏报一个变体更贵。
  · (3) 判的是**代码形状**（分支体内有没有真算 changed、None 有没有被单独说成
    「判不了」、retry 默认值），不验**运行时**那条路真的变了没有 —— 后者是 core 的尺子。

退出码：0=绿；1=红；2=验不了（找不到真源 act.py）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LF = chr(10)

GREEN, RED, SKIP = [], [], []


def rec(name, ok, detail="", skip=False):
    print(("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"),
                              name, str(detail)[:230])), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def _find_act():
    r"""定位真源 act.py。**不许把盘符写死**（tests\_M_可移植性判据.py 的红线）。"""
    cands = []
    env = os.environ.get("AGENTCTL_LEGACY_HOME")
    if env:
        cands.append(env)
    cands.append(os.path.join(ROOT, "legacy_tools", "_src"))
    cands.append(os.path.join(ROOT, "legacy_tools"))
    parent = os.path.dirname(ROOT)
    cands.append(os.path.join(parent, "dsh-viewer-plugin", "tools"))
    for c in cands:
        p = os.path.join(c, "act.py") if c else ""
        if p and os.path.isfile(p):
            return p
    return ""


BRANCH = re.compile(r"^\s*if\s+[A-Za-z_]*(kch|chg|changed|diff)\w*\s*:")
DISCLAIM = re.compile(r"判不出|别当成功|不可区分|投递成功\s*(!=|≠)\s*生效")
PIXEL_CLAIM = re.compile(r"生效（像素证据|像素证据有变化")

# (1) 的注入：像素/指纹比较分支里塞一个 return 0 ⇒ (1) 必须红。
_INJ = LF.join([
    "    if _kch:",
    "        return 0",
])

# ── (3) 的结构化定位零件 ─────────────────────────────────────────────
# ★ UIA.Invoke 分支的锚点（**整行**相等才算命中，避免被更长的一行误匹配）。
ANCHOR = 'after = {} if dry else evidence(hwnd, "after")'
# `changed = …` 赋值（结构化，不认注释行；右值里可以带行尾注释）。
_CHANGED_ASSIGN = re.compile(r"^(?P<ind>\s*)changed\s*=\s*(?P<rhs>.*)$")
# `changed is None` 的处理分支（None 必须被单独说成「判不了」）。
_NONE_HANDLER = re.compile(r"^\s*(?:if|elif)\b[^\n]*\bchanged\s+is\s+None\b")
# 顶层函数头。
_DEF = re.compile(r"^def\s+(\w+)\s*\(")


def _is_blank_or_comment(line):
    s = line.strip()
    return (not s) or s.startswith("#")


def _indent(line):
    s = line.rstrip()
    return len(s) - len(s.lstrip())


def _strip_comment(code):
    """去掉行尾注释（引号里的 # 不算注释）。"""
    out, q, i = [], "", 0
    while i < len(code):
        ch = code[i]
        if q:
            out.append(ch)
            if ch == "\\" and i + 1 < len(code):
                out.append(code[i + 1])
                i += 2
                continue
            if ch == q:
                q = ""
        elif ch in "\"'":
            q = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _unbalanced(s):
    """括号还差几个（>0 说明赋值跨行了）。"""
    return (s.count("(") - s.count(")")
            + s.count("[") - s.count("]")
            + s.count("{") - s.count("}"))


def _locate_invoke_branch(lines):
    """**结构化**定位 do_click 里 UIA.Invoke / SelectionItem 那条 else 分支。

    ★ 为什么不能再用「全文件找那条指纹式字符串」当锚点（2026-10-23 修）：
      指纹式那一行现在已经移到 do_keys 分支去了 ⇒ 拿它当锚点 = 判到了别的分支。
    ⇒ 现在的口径：**先按锚点定位分支，再在分支体内判定**。

    返回 dict：ok=False 时 why 说明为什么定位不到；
    成功后含 anchor(锚点行号0基) / hdr(else: 头行号) / blk(分支体行号区间, 含端点)
    / fn(所属函数行号区间) / fn_name。
    """
    hits = [i for i, ln in enumerate(lines) if ln.strip() == ANCHOR]
    if not hits:                      # 退一步：只要含这句就算（换行/加尾巴的情况）
        hits = [i for i, ln in enumerate(lines) if ANCHOR in ln]
    if not hits:
        return {"ok": False,
                "why": "act.py 里找不到 UIA.Invoke 分支的锚点 `%s`（真源结构变了）" % ANCHOR}
    if len(hits) > 1:
        return {"ok": False,
                "why": "锚点 `%s` 在 act.py 里出现 %d 次 ⇒ 判不准是哪条分支" % (ANCHOR, len(hits))}
    a = hits[0]
    a_ind = _indent(lines[a])

    # 往上找分支头：最近的、缩进比锚点更浅的非空非注释行 ⇒ 必须是 else:
    hdr = None
    for i in range(a - 1, -1, -1):
        if _is_blank_or_comment(lines[i]):
            continue
        if _indent(lines[i]) < a_ind:
            hdr = i
            break
    if hdr is None:
        return {"ok": False, "why": "锚点上方找不到分支头（UIA.Invoke 路径结构变了）"}
    if not re.match(r"^\s*else\s*:", lines[hdr]):
        return {"ok": False,
                "why": "锚点已经不在 else 分支里了（分支头是 `%s`）⇒ UIA.Invoke 路径结构变了"
                       % lines[hdr].strip()}

    # 往下找分支体结束：第一条缩进比锚点更浅的非空非注释行
    end = len(lines)
    for i in range(a + 1, len(lines)):
        if _is_blank_or_comment(lines[i]):
            continue
        if _indent(lines[i]) < a_ind:
            end = i
            break

    # 所属函数（往上找顶层 def），并确认它就是 do_click
    fn = None
    for i in range(hdr, -1, -1):
        m = _DEF.match(lines[i])
        if m:
            fn = i
            break
    if fn is None:
        return {"ok": False, "why": "锚点所在的函数找不到了"}
    fn_end = len(lines)
    for i in range(hdr + 1, len(lines)):
        if _DEF.match(lines[i]):
            fn_end = i
            break
    fn_name = _DEF.match(lines[fn]).group(1)
    if fn_name != "do_click":
        return {"ok": False,
                "why": "锚点不在 do_click 里（当前在 %s）⇒ UIA.Invoke 路径结构变了" % fn_name}
    return {"ok": True, "anchor": a, "hdr": hdr, "blk": (hdr, end - 1),
            "fn": (fn, fn_end), "fn_name": fn_name, "a_ind": a_ind}


def _collect_changed_assigns(lines, lo, hi):
    """收集 [lo, hi] 行区间内所有 `changed = …` 赋值（结构化；排除注释行）。

    返回 [(行号0基, 缩进, 右值代码部分)]，按源码顺序。
    """
    out = []
    i = lo
    while i <= hi:
        ln = lines[i]
        if _is_blank_or_comment(ln):
            i += 1
            continue
        m = _CHANGED_ASSIGN.match(ln)
        if not m:
            i += 1
            continue
        rhs = _strip_comment(m.group("rhs")).strip()
        j = i
        while _unbalanced(rhs) and j + 1 <= hi:      # 右值跨行 ⇒ 并进来
            j += 1
            rhs = (rhs + " " + _strip_comment(lines[j]).strip()).strip()
        out.append((i, _indent(lines[i]), rhs))
        i = j + 1
    return out


def _is_computed(rhs):
    """changed 的右值**是不是真算出来的**。

    · 右值是**裸 `None`** ⇒ 只是把「判不了」的哨兵摆上去，**不算「算了」** ——
      这正是 P0-3 那个真 bug 的落点（该分支恒返回 rc=8）。
    · 其余右值 ⇒ 真算了：
        ① `changed = _eff.get("生效")`（等生效轮询三态，现役形态）
        ② 指纹式 `changed = None if (fp0 is None or fp1 is None) else (fp1 != fp0)`
        ③ 其它等价的三态赋值（任何不是裸 None 的表达式）
    """
    r = rhs.strip()
    if not r:
        return False
    return r != "None"


def _def_params(lines, fn_idx):
    """取 def 那一行的完整形参表（可能跨行，按括号配平取）。"""
    buf = ""
    for i in range(fn_idx, min(fn_idx + 12, len(lines))):
        buf += lines[i]
        if buf.count("(") <= buf.count(")"):
            break
    m = re.search(r"\((.*)\)", buf, re.S)
    return m.group(1) if m else ""


def _inject_invoke_bug(lines, loc):
    """把 UIA.Invoke 分支里「算 changed」的那一行，换成「不算 changed」的那一行。

    ★ 为什么不能 `text.replace(_INJ3_OLD, _INJ3_NEW, 1)`（2026-10-23 修）：
      那是**全文件第一处**替换 ⇒ 命中的是 do_keys 分支（第 2547 行），
      **压根没碰 UIA.Invoke 分支** ⇒ 注入自证「打错分支 = 无效」。
    ★ 现在：先按锚点定位 UIA.Invoke 分支 → 再在**该分支体内**找 changed 赋值
      → **只改那一行**（分支体里最后一次赋值才是最终生效的那个）。

    返回 (被改行号0基, 原文, 新文) 或 (None, 原因)。
    """
    lo, hi = loc["blk"]
    assigns = _collect_changed_assigns(lines, lo, hi)
    if not assigns:
        return None, "★UIA.Invoke 分支（第 %d–%d 行）里没有 changed 赋值可注入" % (lo + 1, hi + 1)
    i, ind, _rhs = assigns[-1]
    old = lines[i]
    new = " " * ind + "changed = None  # ★注入：Invoke 路径不算 changed（回到真 bug）"
    lines[i] = new
    return (i, old, new), None


def _check_p03(lines):
    """(3) ★P0-3 的三个子条件：(a) 算 changed /(b) None 说「判不了」/(c) retry 默认 0。

    返回 (ok, detail, fails, sub)。fails 是失败子条件的说明；sub 是逐条的判定依据。
    """
    fails, sub = [], []
    loc = _locate_invoke_branch(lines)
    if not loc.get("ok"):
        fails.append("(a) " + loc["why"])
        return False, loc["why"], fails, sub
    lo, hi = loc["blk"]
    anchor = loc["anchor"]

    # ── (a) UIA.Invoke 分支必须算 changed ──
    assigns = _collect_changed_assigns(lines, lo, hi)
    if not assigns:
        fails.append("(a) UIA.Invoke 分支（第 %d–%d 行）里**完全没有** `changed` 赋值 ⇒ 这条路径恒返回 rc=8"
                     % (lo + 1, hi + 1))
    elif not _is_computed(assigns[-1][2]):
        fails.append("(a) UIA.Invoke 分支（第 %d 行）把 changed 摆成哨兵 `None`（`%s`）⇒ 没真算 ⇒ 恒返回 rc=8"
                     % (assigns[-1][0] + 1, lines[assigns[-1][0]].strip()[:44]))
    else:
        sub.append("(a)✔ 算了 changed：第 %d 行 `%s`（分支体 第 %d–%d 行）"
                   % (assigns[-1][0] + 1, lines[assigns[-1][0]].strip()[:40], lo + 1, hi + 1))

    # ── (b) 该分支必须说「判不了」 ──
    fn_lo, fn_hi = loc["fn"]
    handlers = []
    for i in range(anchor, fn_hi):
        if _is_blank_or_comment(lines[i]) or not _NONE_HANDLER.match(lines[i]):
            continue
        ind = _indent(lines[i])
        end = fn_hi
        for j in range(i + 1, fn_hi):
            if _is_blank_or_comment(lines[j]):
                continue
            # ★ 分支体 = **缩进比这一行更深**的那些行（`<= ind` 就是分支外了）
            if _indent(lines[j]) <= ind:
                end = j
                break
        handlers.append((i, end))
    hit = None
    for (i, end) in handlers:
        if "判不了" in LF.join(lines[i:end]):
            hit = (i, end)
            break
    if not handlers:
        fails.append("(b) do_click 里找不到 `changed is None` 的处理分支 ⇒ None 没被单独对待")
    elif hit is None:
        fails.append("(b) `changed is None` 分支（第 %d 行起）里没有「判不了」⇒ None 被说成「画面没变」"
                     % (handlers[0][0] + 1))
    else:
        ks = [j for j in range(hit[0], hit[1]) if "判不了" in lines[j]]
        sub.append("(b)✔ None 说「判不了」：`changed is None` 分支（第 %d 行）体内第 %s 行有「判不了」（分支体 第 %d–%d 行）"
                   % (hit[0] + 1, "、".join(str(j + 1) for j in ks), hit[0] + 2, hit[1]))

    # ── (c) retry 默认 0 ──
    params = _def_params(lines, fn_lo)
    r = re.search(r"\bretry\s*=\s*([^,\s)]+)", params)
    if r is None:
        fails.append("(c) do_click 的签名里找不到 `retry` 参数 ⇒ 「无变化时又自动重点一次」没被钉住")
    elif r.group(1) != "0":
        fails.append("(c) do_click 的 retry 默认值是 `%s`（必须是 0 ⇒ 无变化时又自动重点一次）" % r.group(1))
    else:
        sub.append("(c)✔ retry 默认 0（do_click 签名 retry=0）")

    ok = not fails
    detail = ("；".join(sub) if ok else "；".join(fails))
    return ok, detail, fails, sub


def main():
    inject = "--inject" in sys.argv
    inject1 = "--inject1" in sys.argv
    inject_all = "--inject-all" in sys.argv
    ap = _find_act()
    if not ap:
        rec("前置·找到真源 act.py", False, "四个候选目录都没找到", skip=True)
        return 2
    try:
        text = open(ap, encoding="utf-8").read()
    except OSError as e:
        rec("前置·读真源 act.py", False, e, skip=True)
        return 2

    # ★ 注入一律只发生在**内存里的这份文本**上（现役 act.py 一个字节不动）。
    if inject1 or inject_all:
        text = text + LF + _INJ
        print(("（--inject1：已注入 if _kch: return 0 => 期望 (1) 红）"), flush=True)
    if inject or inject_all:
        # ★ P0-3 的注入：**先定位 UIA.Invoke 分支**，再在**该分支体内**改「算 changed」那一行。
        #   绝不用 replace(...,1)（那会打到全文件第一处 = do_keys 分支 ⇒ 打错分支 = 自证无效）。
        lines0 = text.split(LF)
        loc0 = _locate_invoke_branch(lines0)
        if not loc0.get("ok"):
            print(("（--inject：★定位不到 UIA.Invoke 分支（%s）⇒ 第 (3) 条只能红/验不了）" % loc0["why"]),
                  flush=True)
        else:
            res, err = _inject_invoke_bug(lines0, loc0)
            if err:
                print(("（--inject：%s）" % err), flush=True)
            else:
                i, old, new = res
                text = LF.join(lines0)
                print(("（--inject：★注入打在 **UIA.Invoke 分支** —— 分支头第 %d 行 `%s`，锚点第 %d 行，"
                       "分支体 第 %d–%d 行）"
                       % (loc0["hdr"] + 1, lines0[loc0["hdr"]].strip(), loc0["anchor"] + 1,
                          loc0["blk"][0] + 1, loc0["blk"][1] + 1)), flush=True)
                print(("（--inject：★替换发生在**第 %d 行**：原 `%s`  ==>  新 `%s`）"
                       % (i + 1, old.strip(), new.strip())), flush=True)
                print(("（--inject：=> 期望 (3) 红（且只红 (3)））"), flush=True)
    lines = text.split(LF)

    bad = []
    for i, ln in enumerate(lines):
        if not BRANCH.search(ln):
            continue
        body = LF.join(lines[i + 1:i + 4])
        if re.search(r"return\s+0\b", body):
            bad.append((i + 1, ln.strip()[:52]))
    rec("(1) 像素/指纹分支里不许 return 0（画面变了 != 生效）", not bad,
        ("二值当成功 %d 处：%s" % (len(bad), bad[:6])) if bad
        else "0 处；没有任何「像素有变化 ⇒ 直接 return 0」的分支")

    n_dis = len(DISCLAIM.findall(text))          # 自述：注释里的告诫**也算**（注释就是写给人看的）
    pix_lines = [i + 1 for i, ln in enumerate(lines)   # 实现：**排除注释行**，
                 if PIXEL_CLAIM.search(ln) and not ln.lstrip().startswith("#")]
    n_pix = len(pix_lines)
    rec("(2) 既已自述像素判不出真相，就不许再用像素判生效", not (n_dis and n_pix),
        ("自述告诫 %d 处 与 像素判生效 %d 处 **并存** => 自相矛盾" % (n_dis, n_pix))
        if (n_dis and n_pix) else ("自述 %d 处 / 像素判生效 %d 处" % (n_dis, n_pix)))

    # ── (3) ★P0-3（2026-10-21 立 / 2026-10-23 修锚点漂移）：
    #   治的病：do_click 里 UIA.Invoke / SelectionItem 那条分支**从不计算 changed**
    #   ⇒ 末尾 changed is True 恒不成立 ⇒ 恒返回 rc=8
    #   ——「name 点击即便 Invoke 成功、界面真的变了，回执也说画面没变化」。
    #   ⇒ 桌面窗口的 name 点击路径实际上一直不可用。
    #   判据形态（**结构化**）：先按锚点定位到那条 else 分支，再在**分支体内**判定 ——
    #     (a) 分支体内有真算 changed 的赋值（右值不是哨兵 None）；
    #     (b) 同一函数里 `changed is None` 的分支说了「判不了」；
    #     (c) do_click 的 retry 默认值是 0。
    ok3, detail3, _fails3, _sub3 = _check_p03(lines)
    rec("(3) ★P0-3：UIA.Invoke 路径必须算 changed + None 说「判不了」 + retry 默认 0",
        ok3, detail3)

    if RED:
        print((LF + "红 %d：%s" % (len(RED), "；".join(RED))), flush=True)
        return 1
    print((LF + "全绿 %d" % len(GREEN)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
