# -*- coding: utf-8 -*-
r"""tests\_元判据_断言弱化.py —— 元判据①：**tests\ 里的断言不许被悄悄删弱**
                              （窗V · 2026-10-17 · 已接进 run_all.CRITERIA 的「元1」）

治的病（派单 §一「判据存在 ≠ 判据在跑」的同族病）：
    有人改判据文件时，把几条断言删了 / 改成不较真的写法，**没人发现**，
    于是「全绿」变成一条假成绩。⇒ 把「有没有人删弱断言」本身**立成判据**。

扫什么（两路都过才算绿）：
    【路 1】已跟踪的 tests\*.py —— git diff HEAD -- tests/ 的**删除行**里，命中
            check( / attempt( / assert / unverifiable( 的行（另单列 def check(）。
            一行都不许少，除非它**显式在白名单里且写了理由**。
    【路 2】未跟踪的 tests\*.py（git ls-files 查不到 = 不在仓里，git diff 根本看不见它们）
            —— 拿它的备份副本比 check( + attempt( 的**条数**；少了 ⇒ 红，点名到条数。
            备份候选：tests\<name>.py.bak* / work\_bak_<name>_*.py /
                    work\_bak_<name>.py* / work\<name>.py.bak*

不做（边界写死在这条判据里）：
    · 只**读** git（rev-parse / diff / ls-files）——不 add、不 commit、不 stash、不 checkout；
    · 不建窗、不抢前台、不动鼠标、不碰急停、不跑任何别的判据；
    · 不改 tests\ 下任何一个字节（本文件只读它们）。

退出码：
    0 = 没有弱化 ｜ 1 = 有弱化（点名到文件 + 删除原文） ｜ 2 = 环境不满足
        （git 不在 / 不是 git 仓库 / 拿不到 HEAD）——★ rc=2 必须带原因，**不许当绿读**。

用法：
    python -X utf8 tests\_元判据_断言弱化.py
    python -X utf8 tests\_元判据_断言弱化.py --inject   # 自证：把仓复制到 %TEMP%，
        # 在**副本**里删掉一行 check( ⇒ 本条必须变红（注入只发生在副本，现役一个字节不动）

★ 本条与「元2」都**不登记进 run_all 的 INJECTABLE**：注入自证走各自的 --inject，
  不去动总入口「哪些判据支持注入」那一栏的语义（派单 §二 ⚠️）。
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ══════════════════════════════════════════════════════════════════════════
# 白名单：**只有显式写在这里、且写了理由的删除才放过**。
#        键 = tests\ 下的相对路径或文件名；支持尾部 * 通配。
#
# 现状（2026-10-17 窗V 落地时）：**空**。
#   不是「懒得写」，是**当场确实一条都不需要**：本文件落地那一刻，
#   git diff HEAD -- tests/ 的删除行里命中下面五个模式的行数 = 0
#   （当时唯一在改的 tests\_急停端到端判据.py，那 19 行删除全是
#     CreateWindowExW / pump / DestroyWindow / bmp.DeleteObject 之类实现代码，
#     没有一行是断言）。证据：work\_2h_wV_元1_绿_20261017.txt 与本仓回执
#     work\_2h_wV_receipt.md §五。
#
# ★ 加白名单的规矩（别把它当成「弱化的许可证」）：
#   1. 理由必须写清「为什么这次删除是必要的」+「少了它这条判据会误报什么」；
#   2. 理由不许超过三行 ⇒ 逼你写短话；写不出短话说明这次删除本来就不该放行；
#   3. 加完必须重跑本判据确认它绿，并在回执里点名这次加了什么。
# ══════════════════════════════════════════════════════════════════════════
WHITELIST = {
    # 格式示范（**当前不生效，别照抄**）：
    # "_某判据.py": "理由：把三条重复断言合并成一条等价断言，覆盖面不变，"
    #              "依据 work\xxx_合并前后.txt；不是删除覆盖面。",
}

# 删除行里要抓的五个形态：前四个是「断言调用」，第五个是「断言函数本身被删」。
PATTERNS = (
    ("check(", r"(?<![\w.])check\s*\("),
    ("attempt(", r"(?<![\w.])attempt\s*\("),
    ("assert ", r"(?<![\w.])assert\s+"),
    ("unverifiable(", r"unverifiable\s*\("),
)
DEF_CHECK_RE = re.compile(r"^\s*def\s+check\s*\(")
PAT_RE = tuple((k, re.compile(v)) for k, v in PATTERNS)

# 未跟踪判据文件的备份候选（相对仓根；{n} = 文件名去掉 .py）
BACKUP_GLOBS = (
    "tests/{n}.py.bak*",
    "work/_bak_{n}_*.py",
    "work/_bak_{n}.py*",
    "work/{n}.py.bak*",
)

RC_GREEN, RC_RED, RC_ENV = 0, 1, 2


def section(title):
    print("\n" + "=" * 78, flush=True)
    print(title, flush=True)
    print("=" * 78, flush=True)


def git(repo, *args):
    """只读地调 git。git 不在 PATH ⇒ 抛 FileNotFoundError（由 env_precheck 收）。"""
    p = subprocess.run(["git", "-C", repo] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def read_text(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def whitelist_reason(name):
    """白名单查表：精确名 / 文件名 / 尾部通配。命中返回理由，否则 None。"""
    norm = name.replace("\\", "/")
    for key, reason in WHITELIST.items():
        k = key.replace("\\", "/")
        if k.endswith("*"):
            if fnmatch.fnmatch(norm, k):
                return reason
        elif k == norm or os.path.basename(norm) == os.path.basename(k):
            return reason
    return None


def marker_counts(text):
    """数一条判据文件里的断言标记。core = check( + attempt(（派单 §一路 2 的口径）"""
    c = {}
    for name, rx in PAT_RE:
        c[name] = len(rx.findall(text))
    c["def check("] = len(DEF_CHECK_RE.findall(text))
    c["core"] = c["check("] + c["attempt("]
    c["total"] = c["core"] + c["assert "] + c["unverifiable("]
    return c


def _fmt_counts(c):
    return ("check(=%d attempt(=%d assert=%d unverifiable(=%d core=%d 总=%d）"
            % (c["check("], c["attempt("], c["assert "],
               c["unverifiable("], c["core"], c["total"]))


# ══════════════════════════════════════════════════════════════════════════
# 路 1：已跟踪文件的 git diff 删除行
# ══════════════════════════════════════════════════════════════════════════
def scan_tracked(repo):
    """返回 (改动文件列表, {相对名: [(形态, 删除原文), ...]}, 红条目列表, 错误)。"""
    rc, diff, err = git(repo, "diff", "HEAD", "--", "tests/")
    if rc != 0:
        return [], {}, [], "git diff HEAD -- tests/ 失败（rc=%s）：%s" % (rc, err.strip())
    per_file = []      # [[相对名, +行数, -行数], ...]
    hits = {}          # 相对名 -> [(形态列表, 删除行原文), ...]
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            per_file.append([line[6:].strip(), 0, 0])
        elif line.startswith("-") and not line.startswith("---"):
            if per_file:
                per_file[-1][2] += 1
            body = line[1:]
            kinds = [k for k, rx in PAT_RE if rx.search(body)]
            if DEF_CHECK_RE.match(body):
                kinds.append("def check(")
            if kinds and per_file:
                hits.setdefault(per_file[-1][0], []).append((kinds, body))
        elif line.startswith("+") and not line.startswith("+++"):
            if per_file:
                per_file[-1][1] += 1
    reds = []
    for name, _p, _m in per_file:
        got = hits.get(name) or []
        if not got:
            print("  · %s（+%d / -%d）：删除行里命中断言模式的 **0 行** ⇒ 无弱化"
                  % (name, _p, _m), flush=True)
            continue
        tally = {}
        for kinds, _body in got:
            for k in kinds:
                tally[k] = tally.get(k, 0) + 1
        detail = "、".join("%s×%d" % (k, tally[k]) for k in sorted(tally))
        why = whitelist_reason(name)
        if why:
            print("  · %s（+%d / -%d）：命中 %d 行（%s）—— ★ **白名单放过**：%s"
                  % (name, _p, _m, len(got), detail, why), flush=True)
            continue
        print("  [红] %s：删除行里命中断言模式 **%d 行**（%s），且不在白名单里"
              % (name, len(got), detail), flush=True)
        for i, (kinds, body) in enumerate(got, 1):
            print("        #%d %s ｜ %s" % (i, "/".join(kinds), body.rstrip()),
                  flush=True)
        reds.append((name, len(got), detail))
    return per_file, hits, reds, None


# ══════════════════════════════════════════════════════════════════════════
# 路 2：未跟踪的 tests\*.py 与备份比条数
# ══════════════════════════════════════════════════════════════════════════
def scan_untracked(repo):
    """返回 (未跟踪文件列表, 红条目列表, 错误)。"""
    rc, out, err = git(repo, "ls-files", "--", "tests/")
    if rc != 0:
        return [], [], "git ls-files -- tests/ 失败（rc=%s）：%s" % (rc, err.strip())
    tracked = set(x.strip().replace("\\", "/") for x in out.splitlines() if x.strip())
    tdir = os.path.join(repo, "tests")
    if not os.path.isdir(tdir):
        return [], [], "tests 目录不在：%s" % tdir
    untracked = []
    for name in sorted(os.listdir(tdir)):
        if name.endswith(".py") and ("tests/" + name) not in tracked:
            untracked.append(name)
    reds = []
    for name in untracked:
        stem = name[:-3]
        backups = []
        for g in BACKUP_GLOBS:
            backups.extend(glob.glob(os.path.join(repo, g.format(n=stem))))
        cur = marker_counts(read_text(os.path.join(tdir, name)))
        if not backups:
            print("  · %-28s 未跟踪，无备份副本可比对（只登记，不判）：%s"
                  % (name, _fmt_counts(cur)), flush=True)
            continue
        for bak in sorted(set(backups)):
            rel = os.path.relpath(bak, repo).replace("\\", "/")
            old = marker_counts(read_text(bak))
            bad = []
            if cur["core"] < old["core"]:
                bad.append("check(+attempt( 由 %d 降到 %d" % (old["core"], cur["core"]))
            if cur["total"] < old["total"]:
                bad.append("断言标记总数 由 %d 降到 %d" % (old["total"], cur["total"]))
            if bad:
                print("  [红] %s vs 备份 %s：**条数减少** —— %s"
                      % (name, rel, "；".join(bad)), flush=True)
                print("        备份 %s" % _fmt_counts(old), flush=True)
                print("        现役 %s" % _fmt_counts(cur), flush=True)
                reds.append((name, rel, "；".join(bad)))
            else:
                print("  · %s vs 备份 %s：条数未减（core %d→%d，总数 %d→%d）"
                      % (name, rel, old["core"], cur["core"],
                         old["total"], cur["total"]), flush=True)
    return untracked, reds, None


def env_precheck(repo):
    """环境预检。None = 可跑；字符串 = 环境不满足的原因（⇒ rc=2）。"""
    try:
        rc, out, _ = git(repo, "rev-parse", "--is-inside-work-tree")
    except (FileNotFoundError, OSError):
        return ("git 不在 PATH 上（或不可执行）—— 本判据靠 git diff HEAD 取基线，"
                "取不到就只能 rc=2")
    if rc != 0 or out.strip() != "true":
        return "不是 git 仓库（或拿不到工作树）：%s" % repo
    rc, _out, err = git(repo, "rev-parse", "--verify", "HEAD")
    if rc != 0:
        return "拿不到 HEAD（空仓 / 无提交）：%s" % err.strip()
    return None


def run_checks(repo, tag="现役"):
    """跑两路检查，返回 (rc, 红条目)。"""
    bad = env_precheck(repo)
    if bad:
        section("元判据① · 断言不许被悄悄削弱 · %s · 仓 %s" % (tag, repo))
        print("[验不了] %s" % bad, flush=True)
        return RC_ENV, []
    section("元判据① · 断言不许被悄悄削弱 · %s · 仓 %s" % (tag, repo))
    print("口径：只读 git（rev-parse/diff/ls-files），不写 git；"
          "不建窗、不动鼠标、不跑别的判据。", flush=True)
    print("白名单（删弱放行的例外）：%d 条" % len(WHITELIST), flush=True)
    for k, v in WHITELIST.items():
        print("    · %s：%s" % (k, v), flush=True)

    print("\n" + "-" * 78, flush=True)
    print("[路 1] 已跟踪文件：git diff HEAD -- tests/", flush=True)
    per_file, hits, reds1, err = scan_tracked(repo)
    if err:
        print("[红] %s" % err, flush=True)
        return RC_ENV, []
    if not per_file:
        print("  · tests\\ 下相对 HEAD 没有任何改动 ⇒ 无弱化", flush=True)
    print("  小结：改动文件 %d 个，删除行里命中断言模式的共 **%d 行**，红 %d 个文件"
          % (len(per_file), sum(len(v) for v in hits.values()), len(reds1)), flush=True)

    print("\n" + "-" * 78, flush=True)
    print("[路 2] 未跟踪的 tests\\*.py 与备份副本比条数", flush=True)
    untracked, reds2, err2 = scan_untracked(repo)
    if err2:
        print("[红] %s" % err2, flush=True)
        return RC_ENV, []
    print("  小结：未跟踪判据文件 %d 个，比出弱化的 %d 个"
          % (len(untracked), len(reds2)), flush=True)

    reds = list(reds1) + list(reds2)
    return (RC_RED if reds else RC_GREEN), reds


# ══════════════════════════════════════════════════════════════════════════
# --inject：自证「本判据能跑红」。注入只发生在 %TEMP% 的副本上。
# ══════════════════════════════════════════════════════════════════════════
def copy_repo_for_inject(repo):
    """把本判据需要的那几样搬进 %TEMP%：.git（git diff 要）、tests\\、work\\ 里的备份候选。
    ★ 故意**不**搬 dist/ build/ baseline/ vendor/（几百 MB，与本判据无关）。"""
    dst = tempfile.mkdtemp(prefix="agentctl_wV_meta1_")
    shutil.copytree(os.path.join(repo, ".git"), os.path.join(dst, ".git"))
    src_tests = os.path.join(repo, "tests")
    if os.path.isdir(src_tests):
        shutil.copytree(src_tests, os.path.join(dst, "tests"),
                        ignore=shutil.ignore_patterns("__pycache__"))
    wsrc, wdst = os.path.join(repo, "work"), os.path.join(dst, "work")
    os.makedirs(wdst, exist_ok=True)
    if os.path.isdir(wsrc):
        for n in os.listdir(wsrc):
            if n.startswith("_bak_") or ".bak" in n:
                try:
                    shutil.copy2(os.path.join(wsrc, n), os.path.join(wdst, n))
                except OSError:
                    pass
    return dst


def _balanced(line):
    return line.count("(") == line.count(")")


def inject_delete_one_check_line(repo):
    """在副本里删掉**一整行**独立的 check( 语句。
    返回 (相对路径, 行号, 原文) 或 None（没找到可安全删的行）。"""
    tdir = os.path.join(repo, "tests")
    me = os.path.basename(os.path.abspath(__file__))
    for name in sorted(os.listdir(tdir)):
        if not name.endswith(".py") or name in (me, "run_all.py"):
            continue
        p = os.path.join(tdir, name)
        if not os.path.isfile(p):
            continue
        lines = read_text(p).splitlines(True)
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith("check(") and re.search(r"(?<![\w.])check\s*\(", s) \
                    and _balanced(s):
                del lines[i - 1]
                with io.open(p, "w", encoding="utf-8", newline="") as f:
                    f.write("".join(lines))
                return "tests/" + name, i, s
    return None


def do_inject(repo):
    section("跑红自证（--inject）· 注入只发生在 %TEMP% 的副本上，现役一个字节不动")
    dst = copy_repo_for_inject(repo)
    print("副本：%s" % dst, flush=True)
    got = inject_delete_one_check_line(dst)
    if got is None:
        print("[红] 副本里找不到可安全注入的独立 check( 语句 ⇒ 注入没做成，自证不算数",
              flush=True)
        return RC_RED
    rel, lineno, line = got
    print("注入：删掉副本里 %s 第 %d 行的一整条断言 —— 原文：%s" % (rel, lineno, line),
          flush=True)
    rc, reds = run_checks(dst, tag="注入后的副本")
    print("", flush=True)
    if rc == RC_RED:
        print("结论：【绿】注入确实把本判据打红了（副本 rc=%d，红条目 %d 个）—— 判据是活的。"
              % (rc, len(reds)), flush=True)
        print("副本留在：%s（可自行 diff 复核）" % dst, flush=True)
        return RC_GREEN
    print("结论：【红】注入后本判据仍 rc=%d ⇒ 它抓不住这类弱化，判据是死的。" % rc,
          flush=True)
    return RC_RED


def main(argv=None):
    ap = argparse.ArgumentParser(description="tests\\ 断言不许被悄悄删弱（元判据①）")
    ap.add_argument("--inject", action="store_true",
                    help="自证：把仓复制到 %TEMP%，在副本里删一行 check( ⇒ 本条必须变红")
    ap.add_argument("--repo", default=ROOT, help="验哪份仓（默认：本文件所在仓）")
    a = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    if a.inject:
        return do_inject(a.repo)

    rc, reds = run_checks(a.repo, tag="现役")
    section("汇总")
    print("红条目 %d 个" % len(reds), flush=True)
    for r in reds:
        print("  [红] %s" % (r,), flush=True)
    if reds:
        print("结论：【红】—— 有人删弱了 tests\\ 里的断言（上面已逐行点名）。", flush=True)
        print("要放过某一次删除：把它连同理由写进本文件顶部的 WHITELIST，再重跑。",
              flush=True)
        return RC_RED
    if rc == RC_ENV:
        print("结论：【验不了】—— 环境不满足（见上面 [验不了] 的原因）。", flush=True)
        print("★ rc=2 不许读成绿：它只说明「本判据没跑成」，不说明「没弱化」。",
              flush=True)
        return RC_ENV
    print("结论：【绿】tests\\ 里的断言一条没被删弱。", flush=True)
    return RC_GREEN


if __name__ == "__main__":
    sys.exit(main())

