# -*- coding: utf-8 -*-
r"""tests\_元判据_判据接线.py —— 元判据②：**写了判据就必须接线，或者明写「不接」**
                               （窗V · 2026-10-17 · 已接进 run_all.CRITERIA 的「元2」）

治的病（派单 §一）：「**判据存在 ≠ 判据在跑**」。
    gen_legacy 里写了 CHK-A 却没调用；tests\ 下写了十几条判据，run_all 只接了一部分
    —— 没接的那些既没人跑，也没人发现。本条把这件事**立成判据**。

断三件事：
    ① run_all.CRITERIA 里每一条的脚本文件**必须存在**（缺 ⇒ 红，fail-closed）；
    ② tests\ 下每个「看起来是判据」的文件（tests\*.py 且含 def main( 或含 check( 调用）
       —— 要么在 CRITERIA 里，要么在**显式白名单** NOT_WIRED 里且写了理由；
       两边都没有 ⇒ **红，并点名**（这就是病本身）；
    ③ **文档一致**：work\_判据有效性清单_20261017.md 里写的条数、框架进度文档 §〇 表里写的条数，
       与 len(CRITERIA) **必须相等**；不等 ⇒ 红（点名两边的数）。

不做：不建窗、不动鼠标、不跑任何别的判据、不写 git。

退出码：
    0 = 三件事都过 ｜ 1 = 有问题（逐条点名） ｜ 2 = 环境不满足（例如框架进度文档
        不在本机可读范围内 ⇒ 那一条只能判「验不了」，带原因打印；rc=2 **不许当绿读**）。

用法：
    python -X utf8 tests\_元判据_判据接线.py
    python -X utf8 tests\_元判据_判据接线.py --inject   # 自证：在副本里放一个
        # 「有 def main() 但没接进 CRITERIA」的假判据 ⇒ 本条必须变红并点名它

★ 框架进度文档的定位（**不写死任何盘符路径**，这是 tests\_M_可移植性判据.py 的红线）：
    ① 环境变量 AGENT_MANUAL_ROOT（指向手册根目录，里面再有「框架」子目录）；
    ② 本仓的**兄弟目录** <仓根的上一级>/agent-manual/框架/；
    两条都找不到或读不到 ⇒ 该条判「验不了」并打印试过的路径（rc=2 带原因）。
    想让这一条也能自动断，请在框架文档 §〇 表里保留一行
        | 判据条数 | **N 条** |
    本条只认带「判据条数」标签的那一行（避免把文档里历史批次的 "13 条" 误当成现状）。
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FRAMEWORK_DOC = "2026-10-16_安全补强四窗_进度.md"
INVENTORY_DOC = os.path.join("work", "_判据有效性清单_20261017.md")

# ══════════════════════════════════════════════════════════════════════════
# NOT_WIRED：**明确不接 run_all** 的文件。键 = 文件名（支持尾部 * 通配），值 = 理由。
#   标【照抄】的 = 派单 §一「已知要进白名单的」原文照抄；
#   标【窗V 补登记】的 = 派单成文时新出现 / 本条枚举到的文件，理由都写了；
#   DSH 若不认某一条，删掉那一行即可（删完本条会红，红了就红，不许悄悄放过）。
# ══════════════════════════════════════════════════════════════════════════
NOT_WIRED = {
    "_滚动按键判据.py": "【DSH 登记】滚轮路线已真绿（0→3→0），但含 1 项**验不了**：键盘（PageDown）在无前台目标上不可达（Windows 只把键盘给焦点窗口，抢焦点那条路已判废）⇒ 整条 rc=2，按纪律**不接 run_all**；详见 work\\_A1判据_定稿2.txt。",
    # —— 派单【照抄】——
    "_inject.py": "【照抄】注入工具，被 wA / wB / wF / 接缝判据 import；"
                  "它本身不含断言（这条铁律写在它自己文件头），不是判据。",
    "_急停端到端判据.py": "【照抄】要真通行证 + 真桌面，人工执行；跑它会建窗、"
                          "会点真鼠标。判据有效性清单 §二.2 已登记它验不了（rc=2）。",
    "s2_*": "【照抄】历史验收脚本，夹具（见证图 / 截图 / 老线真源）在**仓外**，"
            "换台机器跑不了；见 work\\_写死路径白名单_20261017.md §四.E。",
    "s3_*": "【照抄】同上（历史验收脚本，夹具在仓外）。",
    "s4_*": "【照抄】同上（历史验收脚本，夹具在仓外）。",
    "s5_*": "【照抄】同上（历史验收脚本，夹具在仓外）。",
    "feasibility_http.py": "【照抄】历史验收脚本，夹具在仓外。",
    "frozen_selftest.py": "【照抄】历史验收脚本，夹具在仓外。",
    # —— 窗V 补登记（派单给的名单里没有，但本条需要有个说法）——
    "run_all.py": "【窗V 补登记】它**就是总入口本身**（调度器，不是被调度的那一条）；"
                  "枚举规则会命中它的 def main(，故登记在此。",
    "_wS_靶标进程.py": "【窗V 补登记】窗S 端到端判据的**靶窗宿主子进程**，由 "
                       "_急停端到端判据.py 用 subprocess 拉起、拿它的 HWND。"
                       "它自己会建窗 ⇒ 更不能进 run_all（进一次就抢一次前台）。"
                       "派单成文时它刚被窗S 从 _急停端到端判据.py 里拆出来，"
                       "派单给的名单里还没有它。",
    "_gate_worker.py": "【窗V 补登记】S3 验收的闸门并发 worker，被 s3_accept_gate.py "
                       "subprocess 反复拉起；无 def main、无 check(，本来也枚举不到，"
                       "登记在此只为「tests\\ 下每个 _*.py 都有说法」。",
    "http_click_demo.py": "【窗V 补登记】演示脚本（HTTP 点击 demo），无断言、非判据。",
    "quiz_demo_click.py": "【窗V 补登记】演示脚本，无断言、非判据。",
    "discriminate_input.py": "【窗V 补登记】输入判别小工具，无 def main( / 无 check(，"
                             "本来也枚举不到，登记在此只为说清楚。",
}

RC_GREEN, RC_RED, RC_ENV = 0, 1, 2


def section(title):
    print("\n" + "=" * 78, flush=True)
    print(title, flush=True)
    print("=" * 78, flush=True)


def read_text(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def not_wired_reason(name):
    for key, reason in NOT_WIRED.items():
        if key.endswith("*"):
            if fnmatch.fnmatch(name, key):
                return reason
        elif key == name:
            return reason
    return None


def read_criteria(repo):
    """用 ast 取 run_all.py 的 CRITERIA 字面量（**不执行** run_all，不起任何判据）。"""
    path = os.path.join(repo, "tests", "run_all.py")
    if not os.path.isfile(path):
        return None, "总入口不在：%s" % path
    try:
        tree = ast.parse(read_text(path))
    except SyntaxError as e:
        return None, "run_all.py 语法错（应当红，不许绿）：%s" % e
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "CRITERIA" for t in node.targets):
            try:
                return list(ast.literal_eval(node.value)), None
            except ValueError as e:
                return None, "CRITERIA 不是字面量（取不到）：%s" % e
    return None, "run_all.py 里找不到 CRITERIA 赋值"


def looks_like_criterion(text):
    if re.search(r"^def\s+main\s*\(", text, re.M):
        return "含 def main("
    if re.search(r"(?<![\w.])check\s*\(", text):
        return "含 check( 调用"
    return None


def parse_count(text, label="判据条数"):
    """只认带 label 的那一行。返回 (数字 or None, 原文行 or None)。"""
    for ln in text.splitlines():
        if label in ln:
            m = re.search(r"(\d+)\s*条", ln)
            return (int(m.group(1)) if m else None), ln.strip()
    return None, None


def framework_candidates(repo):
    """找框架进度文档。**不写死盘符**：环境变量 → 本仓的兄弟目录。"""
    out = []
    env = os.environ.get("AGENT_MANUAL_ROOT")
    if env:
        out.append(os.path.join(env, "框架", FRAMEWORK_DOC))
    out.append(os.path.join(os.path.dirname(os.path.abspath(repo)),
                           "agent-manual", "框架", FRAMEWORK_DOC))
    return out


def run_checks(repo, tag="现役"):
    reds = []
    unverifiable = []
    section("元判据② · 判据必须接线（或明写不接）· %s · 仓 %s" % (tag, repo))

    # ── ① CRITERIA 里每条脚本必须存在 ────────────────────────────────
    print("[①] run_all.CRITERIA 里每条脚本文件必须存在（fail-closed）", flush=True)
    criteria, err = read_criteria(repo)
    if err:
        print("  [红] %s" % err, flush=True)
        return RC_ENV, [], [err]
    print("  CRITERIA 共 **%d 条**" % len(criteria), flush=True)
    wired = {}
    for row in criteria:
        key, script = row[0], row[1]
        wired[script] = key
        if os.path.isfile(os.path.join(repo, "tests", script)):
            print("  · %-4s %-28s 在" % (key, script), flush=True)
        else:
            print("  [红] %-4s %-28s **文件不在**" % (key, script), flush=True)
            reds.append("CRITERIA 里的 %s（键 %s）文件不存在" % (script, key))

    # ── ② 每个判据样文件：要么在 CRITERIA，要么在 NOT_WIRED ──────────
    print("\n" + "-" * 78, flush=True)
    print("[②] tests 下「看起来是判据」的文件：要么在 CRITERIA，要么在 NOT_WIRED",
          flush=True)
    print("  枚举规则：tests 下的 *.py，且（含 def main( 或含 check( 调用）", flush=True)
    tdir = os.path.join(repo, "tests")
    names = sorted(n for n in os.listdir(tdir) if n.endswith(".py")) \
        if os.path.isdir(tdir) else []
    cand = []
    for name in names:
        why = looks_like_criterion(read_text(os.path.join(tdir, name)))
        if why:
            cand.append((name, why))
    print("  枚举到 %d 个判据样文件：" % len(cand), flush=True)
    for name, why in cand:
        if name in wired:
            print("  · %-26s %s ⇒ **已接线**（键 %s）" % (name, why, wired[name]),
                  flush=True)
            continue
        reason = not_wired_reason(name)
        if reason:
            print("  · %-26s %s ⇒ NOT_WIRED：%s" % (name, why, reason), flush=True)
        else:
            print("  [红] %-26s %s ⇒ **既不在 CRITERIA、也不在 NOT_WIRED**"
                  % (name, why), flush=True)
            print("        ★ 这就是「判据存在 ≠ 判据在跑」本体：要么接进 run_all.CRITERIA，"
                  "要么在 NOT_WIRED 里写明不接的理由。", flush=True)
            reds.append("%s 既没接进 CRITERIA，也没在 NOT_WIRED 里登记" % name)
    print("  附：NOT_WIRED 共登记 %d 条（含通配）" % len(NOT_WIRED), flush=True)
    for k, v in NOT_WIRED.items():
        print("    · %s：%s" % (k, v), flush=True)

    # ── ③ 文档一致：清单 + 框架进度文档 ────────────────────────────────
    print("\n" + "-" * 78, flush=True)
    print("[③] 文档写的条数必须 == len(CRITERIA) = %d" % len(criteria), flush=True)
    inv = os.path.join(repo, INVENTORY_DOC)
    if not os.path.isfile(inv):
        print("  [红] 判据有效性清单不在：%s" % inv, flush=True)
        reds.append("判据有效性清单不在：%s" % INVENTORY_DOC)
    else:
        n, line = parse_count(read_text(inv))
        if n is None:
            print("  [红] 清单里找不到可解析的「判据条数」声明（读到 %r）" % line,
                  flush=True)
            reds.append("清单里没有可解析的「判据条数」声明")
        elif n != len(criteria):
            print("  [红] 清单写 **%d 条** vs len(CRITERIA)=**%d** ⇒ 文档漂了"
                  % (n, len(criteria)), flush=True)
            print("        原文：%s" % line, flush=True)
            reds.append("清单条数 %d ≠ len(CRITERIA) %d" % (n, len(criteria)))
        else:
            print("  · 清单：%d 条 == len(CRITERIA) ⇒ 一致（%s）" % (n, line), flush=True)

    fw_path = None
    tried = []
    for c in framework_candidates(repo):
        if os.path.isfile(c):
            fw_path = c
            break
        tried.append(c)
    if fw_path is None:
        msg = ("框架进度文档（%s）不在本机可读范围内，试过：%s"
               % (FRAMEWORK_DOC, " ｜ ".join(tried)))
        print("  [验不了] %s" % msg, flush=True)
        print("        ⇒ 这一项判「验不了」：可用环境变量 AGENT_MANUAL_ROOT "
              "指向手册根目录后重跑。", flush=True)
        unverifiable.append(msg)
    else:
        n, line = parse_count(read_text(fw_path))
        if n is None:
            msg = ("框架进度文档 %s 里找不到带「判据条数」标签的可解析声明（读到 %r）"
                   % (fw_path, line))
            print("  [验不了] %s" % msg, flush=True)
            print("        ⇒ 请在该文档 §〇 表保留一行「| 判据条数 | **N 条** |」，"
                  "本条才认得。", flush=True)
            unverifiable.append(msg)
        elif n != len(criteria):
            print("  [红] 框架文档写 **%d 条** vs len(CRITERIA)=**%d** ⇒ 文档漂了"
                  % (n, len(criteria)), flush=True)
            print("        原文：%s" % line, flush=True)
            reds.append("框架文档条数 %d ≠ len(CRITERIA) %d" % (n, len(criteria)))
        else:
            print("  · 框架文档：%d 条 == len(CRITERIA) ⇒ 一致（%s）" % (n, line),
                  flush=True)

    rc = RC_RED if reds else (RC_ENV if unverifiable else RC_GREEN)
    return rc, reds, unverifiable


# ══════════════════════════════════════════════════════════════════════════
# --inject：自证「本条能跑红」。注入只发生在 %TEMP% 的副本上。
# ══════════════════════════════════════════════════════════════════════════
def do_inject(repo):
    section("跑红自证（--inject）· 注入只发生在 %TEMP% 的副本上，现役一个字节不动")
    base = tempfile.mkdtemp(prefix="agentctl_wV_meta2_")
    shadow = os.path.join(base, "repo")
    os.makedirs(shadow)
    shutil.copytree(os.path.join(repo, "tests"), os.path.join(shadow, "tests"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    os.makedirs(os.path.join(shadow, "work"))
    shutil.copy2(os.path.join(repo, INVENTORY_DOC), os.path.join(shadow, INVENTORY_DOC))
    # 框架进度文档：真读得到就搬一份；读不到就在副本侧放一个**明确登记的替身**——
    # 否则这一项会判「验不了」（rc=2），自证就看不出「红」是注入造成的。
    real_fw = None
    for c in framework_candidates(repo):
        if os.path.isfile(c):
            real_fw = c
            break
    fw_dir = os.path.join(base, "agent-manual", "框架")
    os.makedirs(fw_dir)
    if real_fw:
        shutil.copy2(real_fw, os.path.join(fw_dir, FRAMEWORK_DOC))
        print("框架文档：搬了真件 %s" % real_fw, flush=True)
    else:
        crit, _ = read_criteria(shadow)
        n = len(crit) if crit else 0
        with io.open(os.path.join(fw_dir, FRAMEWORK_DOC), "w",
                     encoding="utf-8", newline="\n") as f:
            f.write("# 注入自证用的**替身**（真框架文档本机读不到，"
                    "否则 rc=2 会盖掉注入的红）\n")
            f.write("| 判据条数 | **%d 条** |\n" % n)
        print("框架文档：真件读不到，已在副本侧放**替身**（条数按副本 CRITERIA 写 %d）"
              % n, flush=True)
    fake = "_假判据_注入用.py"
    with io.open(os.path.join(shadow, "tests", fake), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("# -*- coding: utf-8 -*-\n")
        f.write('"""注入自证用的假判据：有 def main()，但没接进 run_all.CRITERIA。"""\n')
        f.write("\n\ndef main():\n    print(\"我是没接线的假判据\")\n    return 0\n\n")
        f.write('if __name__ == "__main__":\n    raise SystemExit(main())\n')
    print("副本：%s" % shadow, flush=True)
    print("注入：在副本里放了 %s（有 def main()，**故意不接** CRITERIA）" % fake,
          flush=True)
    rc, reds, unver = run_checks(shadow, tag="注入后的副本")
    print("", flush=True)
    if rc == RC_RED and any(fake in r for r in reds):
        print("结论：【绿】注入确实把本条打红了（副本 rc=%d，红 %d 条，其中点名了 %s）"
              "—— 判据是活的。" % (rc, len(reds), fake), flush=True)
        print("副本留在：%s（可自行复核）" % shadow, flush=True)
        return RC_GREEN
    print("结论：【红】注入后本条 rc=%d（红 %d 条 / 验不了 %d 条），没有点名 %s ⇒ 判据抓不住。"
          % (rc, len(reds), len(unver), fake), flush=True)
    for r in reds:
        print("    红：%s" % r, flush=True)
    for u in unver:
        print("    验不了：%s" % u, flush=True)
    return RC_RED


def main(argv=None):
    ap = argparse.ArgumentParser(description="tests 判据必须接线（元判据②）")
    ap.add_argument("--inject", action="store_true",
                    help="自证：在副本里放一个没接线的假判据 ⇒ 本条必须变红并点名")
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

    rc, reds, unver = run_checks(a.repo, tag="现役")
    section("汇总")
    print("红 %d 条 ｜ 验不了 %d 条" % (len(reds), len(unver)), flush=True)
    for r in reds:
        print("  [红] %s" % r, flush=True)
    for u in unver:
        print("  [验不了] %s" % u, flush=True)
    if reds:
        print("结论：【红】—— 上面每一条都要处理（接上，或明写不接的理由）。", flush=True)
        return RC_RED
    if unver:
        print("结论：【验不了】—— 有检查项在本机没法给结论（原因见上）。", flush=True)
        print("★ rc=2 不许读成绿：它只说明「这项没验成」。", flush=True)
        return RC_ENV
    print("结论：【绿】判据都在跑（接线齐、文档条数与 CRITERIA 相等）。", flush=True)
    return RC_GREEN


if __name__ == "__main__":
    sys.exit(main())

