# -*- coding: utf-8 -*-
"""pyflakes 常驻门槛 —— 拦「会让程序崩在半路」的那一类，其余只提醒。

为什么分两类：这个门槛从 T13 起挂了三轮账，一直没落地。落地时如果简单地把
pyflakes 全量当致命，等于被存量的 unused import 卡住，门槛就名存实亡了；
如果全放行，又等于没门槛。所以按后果分：

  致命（退出码非零，拦住流程）
    undefined name / redefinition / SyntaxError
    —— 这类是「运行到那行才崩」，也正是 T15 那个死代码 bug 的成因。

  提醒（只打印，不拦）
    imported but unused / assigned to but never used
    —— 存量债，一次清完不现实，也不该卡住别人发包。

用法：
    python -X utf8 lint_gate.py src\\agentctl dist\\_legacy_for_pack
    python -X utf8 lint_gate.py --list-only src\\agentctl
"""
import os
import subprocess
import sys
import tempfile
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 致命项：命中就拦住（子串匹配 pyflakes 的输出行）
FATAL = (
    "undefined name",
    "redefinition of unused",
    "redefinition of function",
    "invalid syntax",
    "SyntaxError",
    "IndentationError",
    "unexpected indent",          # ← pyflakes 对语法错用的是这类措辞，不是 invalid syntax
    "expected an indented block",
    "unindent does not match",
    "was never closed",
)

# 提醒项：只打印
NOTICE = (
    "imported but unused",
    "assigned to but never used",
    "unable to detect undefined names",
)


def _run_pyflakes(paths):
    """一次性把所有 .py 交给 pyflakes（逐文件起进程太慢）。"""
    cmd = [sys.executable, "-X", "utf8", "-m", "pyflakes"] + list(paths)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def _pyflakes_available():
    """这个解释器到底能不能 import pyflakes —— 返回 (能不能, 探针原文)。

    ★ 为什么不看 lint_gate 自己的 returncode：pyflakes「真报了问题」与
      「模块根本不在」都是 rc=1（缺模块时 python -m 找不到模块，退出 1），
      光看 rc 分不开这两件完全相反的事。
    ★ 为什么不扫 stderr 里有没有 "No module named"：那是拿**报错文案**当判据，
      文案随 Python 版本与本地化变，判据不稳。这里跑一条最短的 import 探针，
      问的是事实；探针用的是**同一个 sys.executable**，与下面真跑的那条同源。
    """
    cmd = [sys.executable, "-X", "utf8", "-c", "import pyflakes"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.returncode == 0), ((r.stderr or r.stdout or "").strip()[:300])


def _collect(target):
    """展开成 .py 文件列表；也接受 .zip（扫包内文件）。"""
    out = []
    if os.path.isdir(target):
        for root, dirs, fs in os.walk(target):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            for f in fs:
                if f.endswith(".py"):
                    out.append(os.path.join(root, f))
    elif target.endswith(".zip") and os.path.exists(target):
        d = tempfile.mkdtemp()
        with zipfile.ZipFile(target) as z:
            for n in z.namelist():
                if n.endswith(".py") and "__pycache__" not in n:
                    p = os.path.join(d, n.replace("/", os.sep))
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "wb") as fh:
                        fh.write(z.read(n))
                    out.append(p)
    elif target.endswith(".py"):
        out.append(target)
    return out


def main(argv):
    list_only = "--list-only" in argv
    targets = [a for a in argv if not a.startswith("--")]
    if not targets:
        targets = ["src/agentctl"]

    files = []
    for t in targets:
        got = _collect(t)
        if not got:
            print("  [跳过] %s（没有 .py 或不存在）" % t)
        files.extend(got)
    if not files:
        print("  没有可扫的文件")
        return 0

    # ★ S3-B1（2026-10-13）：能力探针。
    #   「工具没跑起来」≠「没有致命项」—— 缺 pyflakes 时 python 把
    #   "No module named pyflakes" 打进 stderr，_run_pyflakes 把它并进 txt，
    #   它既不含致命词也不含提醒词 ⇒ 落 other 桶 ⇒ fatal 为空
    #   ⇒ 老代码照样打印「✔ 致命项为零 —— 门槛通过」并 return 0。
    #   任何缺 pyflakes 的机器发包（pack_dist.py 无条件 import 本模块）都会
    #   靠这道假绿灯过去 —— 所以在这里先证明工具真的跑过，再谈判定。
    #   放在 if list_only: return 0 之前 ⇒ --list-only 同样不许假装通过。
    ok_tool, why = _pyflakes_available()
    if not ok_tool:
        print("  X 门槛**未生效**：当前解释器 %s 导不进 pyflakes" % sys.executable)
        print("      ⇒ 本次判定**不成立**，绝不能当成「致命项为零」")
        print("      （那正是本条要修的假通过；探针跑的是 import 成败，不是报错文案）。")
        print("      探针原文：%s" % (why or "(无输出)"))
        print("      修法：python -m pip install pyflakes")
        return 2

    txt = _run_pyflakes(files)
    lines = [l for l in txt.splitlines() if l.strip()]
    fatal = [l for l in lines if any(k in l for k in FATAL)]
    notice = [l for l in lines if l not in fatal and any(k in l for k in NOTICE)]
    other = [l for l in lines if l not in fatal and l not in notice]

    print("  扫了 %d 个文件" % len(files))
    if fatal:
        print("  X 致命 %d 条 —— 这类会让程序崩在半路，不许过：" % len(fatal))
        for l in fatal[:20]:
            print("      %s" % l)
    if notice:
        print("  · 提醒 %d 条（存量债，不拦）：" % len(notice))
        for l in notice[:6]:
            print("      %s" % l)
        if len(notice) > 6:
            print("      … 还有 %d 条" % (len(notice) - 6))
    if other:
        print("  · 其它 %d 条：" % len(other))
        for l in other[:6]:
            print("      %s" % l)

    if list_only:
        return 0
    if not fatal:
        print("  ✔ 致命项为零 —— 门槛通过")
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
