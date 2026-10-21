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

## 两条断言（修好后）
  (1) **主断言**：凡 if <像素/指纹比较结果>: 分支，3 行内出现 return 0 ⇒ 红。
      即「画面变了」在代码里**不许被直接等同于成功**。
  (2) **自述一致性**：同一文件里既然写了「判不出来 / 别当成功 / 不可区分」，
      就不许再有「用像素判生效」的实现 —— 两句话不能并存。
      ★ 计数口径**故意不对称**：自述(DISCLAIM)连注释一起数，
      实现(PIXEL_CLAIM)**排除注释行** —— 因为 act.py:2008 那句
      「日志逐行写着✔后台按键生效」是**历史证据记录**，不是实现，
      算进去会把「真跑留过这种日志」误判成「现在还这么判」。
  ★ **注入即红**：注入一处 if _kch: return 0，(1) 必须变红。

★ 诚实边界：
  · 验的是**判定依据的性质**，不验「这次动作到底生不生效」——后者只能靠
    latent state 内层溯源（铁律 11(9) 的验动作.py），不是本条口径。
  · **纯静态**，不碰闸门、不碰屏幕、不需要通行证 => 用户不在场也能跑。
  · (1) 是形状匹配，可能漏报「换个变量名」的同类写法；宁可漏报不可误报 ——
    误报会让真源被无谓改动，比漏报一个变体更贵。

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

_INJ = LF.join([
    "    if _kch:",
        "        return 0",
])

# ★ P0-3（2026-10-21）：把「UIA.Invoke 路径不算 changed」注入回去
#   ⇒ 第 (3) 条必须变红（证明这一条真有判别力，不是恒绿）。
_INJ3_OLD = '            changed = None if (fp0 is None or fp1 is None) else (fp1 != fp0)'
_INJ3_NEW = '            changed = None  # ★注入：Invoke 路径不算 changed（回到真 bug）'


def main():
    inject = "--inject" in sys.argv
    ap = _find_act()
    if not ap:
        rec("前置·找到真源 act.py", False, "四个候选目录都没找到", skip=True)
        return 2
    try:
        text = open(ap, encoding="utf-8").read()
    except OSError as e:
        rec("前置·读真源 act.py", False, e, skip=True)
        return 2
    if inject:
        text = text + LF + _INJ
        print(("（--inject：已注入 if _kch: return 0 => 期望 (1) 红）"), flush=True)
        if _INJ3_OLD in text:
            text = text.replace(_INJ3_OLD, _INJ3_NEW, 1)
            print("（--inject：已把 UIA.Invoke 路径的 changed 计算去掉 => 期望 (3) 红）", flush=True)
        else:
            print("（--inject：★没找到 Invoke 路径那一行（真源已变），第 (3) 条只能验不了）", flush=True)
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

    # ── (3) ★P0-3（2026-10-21）：**UIA.Invoke 路径必须算 changed** ──
    #   治的病（ZCode 评审逮到的真 bug）：do_click 里 UIA.Invoke / SelectionItem
    #   那条分支**从不计算 changed** ⇒ 末尾 changed is True 恒不成立 ⇒ 恒返回 rc=8
    #   ——「name 点击即便 Invoke 成功、界面真的变了，回执也说画面没变化」。
    #   ⇒ 桌面窗口的 name 点击路径实际上一直不可用。
    #   判据形态：找到「else: after = {} if dry else evidence(hwnd, "after")」那一支，
    #   往后 12 行内必须出现 changed 的赋值。
    anchor = 'after = {} if dry else evidence(hwnd, "after")'
    idx = text.find(anchor)
    bad3 = []
    if idx < 0:
        bad3.append("act.py 里找不到 UIA.Invoke 那条分支的锚点（真源结构变了）")
    else:
        seg = text[idx:idx + 1800]
        if "changed = None if (fp0 is None or fp1 is None) else (fp1 != fp0)" not in seg:
            bad3.append("UIA.Invoke 分支后面没有算 changed ⇒ 这条路径恒返回 rc=8")
        if "判不了" not in seg:
            bad3.append("Invoke 分支没说「判不了」（None 被说成「画面没变」= 文案与数据不一致）")
    if "def do_click(hwnd, name=None, xy=None, dry=False, force_coord=False, retry=0" not in text:
        bad3.append("do_click 的 retry 默认值不是 0（无变化时又自动重点一次）")
    rec("(3) ★P0-3：UIA.Invoke 路径必须算 changed + None 说「判不了」 + retry 默认 0",
        not bad3, ("；".join(bad3)) if bad3 else
        "Invoke 路径算 changed；None 说判不了；retry 默认 0")

    if RED:
        print((LF + "红 %d：%s" % (len(RED), "；".join(RED))), flush=True)
        return 1
    print((LF + "全绿 %d" % len(GREEN)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
