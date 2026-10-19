# -*- coding: utf-8 -*-
"""第 34 条判据：unknown 永不当成功（铁律 11(9)）。

治的病（2026-10-19 16:28 扫 act.py 扫出来的，不是推演）：
    四处 return 0 if changed is not False else 8
    changed 为 **None**（指纹取不到 / 判不出来 / unknown）时，
    changed is not False **同样成立** ⇒ 直接 return 0 **判成功**。
    铁律 11(9) 明写：unknown 永不当成功，且「没写期望谓词就只能报 unknown」。

    更隐蔽的是它连原因都不报：那几处回执文案是「changed 为 None 时 ev 是空字符串」，
    即「判不出来」这件事在回执里**一个字都不说**，直接以成功收场。
    这是判据 31 治的「失败态长得像什么都没拿到」的升级版：连「判不出来」都被吞了。

与判据 32 的分工（形状不同，互补）：
    判据 32：True（像素有变化）当成功 —— 已堵
    判据 34：**None（判不出来）当成功** —— 本条

★ 写之前先在真源上验证正则，结果直接改了设计：
    初版正则含 is not (False|None)，在 act.py 上命中 **5** 处，其中 2943 是**误报**：
        return 0 if (dry or (sel is not None and len(sel) > 0)) else 8
    —— 那是**二元**语义（没找到/找到了），不是三值 unknown。
    ⇒ 只留 is not False。这是不写判据先跑样本的实际收益。

两条断言：
  (1) 主断言：真源里不许出现「三值变量 is not False ⇒ 直接 return 0」，命中必须为 0。
  (2) 注入即红：注入一处 return 0 if changed is not False else 8，必须变红。

★ 诚实边界：
  · 只管「非假即真 ⇒ 判成功」这一种写法。若写成显式三分支、把 None 报成
    独立返回码（判不出 ≠ 没变化），本条抓不到 —— 那属于「写对了」，不是漏报。
  · 纯静态，不需要通行证，用户不在场也能跑。只扫 act.py。

退出码：0=绿；1=红；2=验不了（找不到真源 act.py）。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NL = chr(10)

# 只抓「三值变量 is not False ⇒ 直接 return 0」。
# 刻意不含 is not None：那是二元语义（没找到/找到了），不是 unknown。
TRAP = re.compile(r"return\s+0\s+if\b[^\n]*\bis\s+not\s+False\b")

GREEN, RED, SKIP = [], [], []


def rec(name, ok, detail="", skip=False):
    print(("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"),
                              name, str(detail)[:240])), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def _find_act():
    cands = []
    env = os.environ.get("AGENTCTL_LEGACY_HOME")
    if env:
        cands.append(env)
    cands.append(os.path.join(ROOT, "legacy_tools", "_src"))
    cands.append(os.path.join(ROOT, "legacy_tools"))
    cands.append(os.path.join(os.path.dirname(ROOT), "dsh-viewer-plugin", "tools"))
    for c in cands:
        q = os.path.join(c, "act.py") if c else ""
        if q and os.path.isfile(q):
            return q
    return ""


def main():
    inject = "--inject" in sys.argv
    ap = _find_act()
    if not ap:
        rec("前置·找到真源 act.py", False, "四个候选都没找到", skip=True)
        return 2
    text = io.open(ap, "r", encoding="utf-8", errors="replace").read()
    if inject:
        text = text + NL + "        return 0 if changed is not False else 8"
        print("（--inject：已注入一处 is not False 判成功 => 期望 (1) 红）", flush=True)
    hits = []
    for i, ln in enumerate(text.split(NL)):
        if TRAP.search(ln) and not ln.lstrip().startswith("#"):
            hits.append((i + 1, ln.strip()[:56]))
    rec("(1) 三值变量 is not False 不许直接映射成成功（unknown 永不当成功）", not hits,
        ("命中 %d 处：%s" % (len(hits), hits[:6])) if hits
        else "0 处；没有把 None 当成功的返回码映射")

    if RED:
        print((NL + "红 %d：%s" % (len(RED), "；".join(RED))), flush=True)
        return 1
    print((NL + "全绿 %d" % len(GREEN)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())