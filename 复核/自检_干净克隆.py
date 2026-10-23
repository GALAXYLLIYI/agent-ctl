# -*- coding: utf-8 -*-
r"""自检_干净克隆.py —— 「主控复核护栏」仓内副本的**干净克隆自检**（2026-10-23）

★ 为什么有这份脚本：这套护栏原本在 `work\_M19_语义修复_20221023\主控复核\` 下面，
  而 `work\` 整个被 .gitignore 排除 ⇒ 别人克隆下来**没有**这套护栏
  ⇒「形状白名单 + 数量账本」双保险只在本机生效。
  本脚本是那份「复制进仓」的**验收器**：一条命令回答三个问题 ——
    ① 护栏文件齐全吗？
    ② 它**进得了仓**吗（会不会被 .gitignore 挡掉 ⇒ 干净克隆上就没有）？
    ③ 在一份**干净克隆**上跑得起来吗（复核脚本 exit=0、八组全绿）？

用法：
    python -X utf8 复核\自检_干净克隆.py             # 自检（快；不动 git 历史）
    python -X utf8 复核\自检_干净克隆.py --clone     # 加做一次真·干净克隆演练

★ 一条**必须说清**的前置事实（不是本单的锅，但会决定干净克隆能不能绿）：
  本仓**没有** .gitattributes ⇒ 签出时行尾由**每台机器自己的** git 配置决定。
  入库的 blob 是 LF（逐字节护栏就是按这个算的）；但全局 core.autocrlf=true 的机器上，
  普通 clone 会把 LF **改成 CRLF** ⇒ ②③④⑤ 组的逐字节比对必红。
  ⇒ ⑦ 的克隆演练用 `git -c core.autocrlf=false clone` 复现**入库字节**；
    仓级修法（加 .gitattributes）不在本单范围，此处只如实报。

退出码：0 = 全绿；1 = 有红（逐条打印为什么红）；2 = 验不了（环境不满足：拿不到仓 / git / 解释器）

★ 口径：① 走仓内 git（`git check-ignore`）⇒ 护栏**必须不在** ignore 名单里；
  ② ③ 走**子进程**跑真脚本 ⇒ 验的是「别人拿到仓之后会看到什么」，不是本进程内存里的状态；
  ④ ⑤ 全程**只读**本仓：只 clone（到 %TEMP%）、只复制护栏目录、只删自己造的 %TEMP% 现场。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))          # 复核\
ROOT = os.environ.get("AGENTCTL_ROOT") or os.path.dirname(HERE)   # 仓根（复核\ 的上一级）
M19 = os.path.join(HERE, "M19")

# 护栏清单：相对**复核\** 的路径（★ 用 / 写，跨平台）
GUARD_FILES = (
    "M19/复核_M19_20221023.py",
    "M19/approved_code_lines.json",
    "M19/账本工具.py",
    "M19/baseline_core_pre_M20.py",
    "backup_before/act_1_src__legacy_tools__act.py",
    "自检_干净克隆.py",
)
# 要扫「不许有盘符绝对路径」的源文件（基线快照是**冻结的历史产物**，按设计含旧路径，不扫）
PORTABLE_SOURCES = (
    "M19/复核_M19_20221023.py",
    "M19/账本工具.py",
    "自检_干净克隆.py",
)
# 盘符绝对路径判定：与仓内 tests\_写死路径判据.py 同一口径（正则照抄）
DRIVE_PAT = re.compile(r"[A-Za-z]:\\[^\s\"'<>|，。；）)]{3,}")

RESULTS = []


def rec(key, ok, detail=""):
    RESULTS.append((key, bool(ok), detail))
    print("  [%s] %-46s %s" % ("绿" if ok else "红", key, detail), flush=True)
    return ok


def head(t):
    print("\n== %s ==" % t, flush=True)


def run(cmd, cwd=None, env=None, timeout=900):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        for k, v in env.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=e, timeout=timeout)


def git(args, check_rc=None):
    r = run(["git", "-C", ROOT] + list(args))
    return r


def check_files():
    head("① 护栏文件齐全（相对 复核\\）")
    for rel in GUARD_FILES:
        p = os.path.join(HERE, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            rec("文件存在 %s" % rel, False, "缺件")
            continue
        n = os.path.getsize(p)
        rec("文件存在 %s" % rel, n > 0, "%d 字节" % n)


def check_trackable():
    head("② 护栏进得了仓（git check-ignore：必须**不在** ignore 名单里）")
    r = git(["rev-parse", "--show-toplevel"])
    if r.returncode != 0:
        rec("拿得到仓（git rev-parse）", False, "不是 git 仓：%s" % (r.stderr or "").strip()[:120])
        return False
    rec("拿得到仓（git rev-parse）", True, r.stdout.strip())
    ok = True
    bundle_rel = os.path.basename(HERE)          # 护栏目录名（相对仓根）
    for rel in GUARD_FILES:
        rr = git(["check-ignore", "-q", "--", "%s/%s" % (bundle_rel, rel)])
        # rc=0 ⇒ 被 ignore（危险）；rc=1 ⇒ 没被 ignore（好）；其它 ⇒ 查不动（也算红）
        if rr.returncode == 0:
            ok = False
            rec("未被 .gitignore 排除 %s" % rel, False, "★被排除 ⇒ 干净克隆上会缺件")
        elif rr.returncode == 1:
            rec("未被 .gitignore 排除 %s" % rel, True, "")
        else:
            ok = False
            rec("未被 .gitignore 排除 %s" % rel, False,
                "check-ignore rc=%d：%s" % (rr.returncode, (rr.stderr or "").strip()[:100]))
    return ok


def check_ledger():
    head("③ 账本校验（段校验值 + 整本指纹）")
    tool = os.path.join(M19, "账本工具.py")
    r = run([sys.executable, "-X", "utf8", tool, "check"], cwd=HERE)
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    last = tail[-1] if tail else ""
    rec("账本工具.py check", r.returncode == 0, "rc=%d ｜ %s" % (r.returncode, last[:100]))
    return r.returncode == 0


def check_review(script=None, cwd=None, env=None,
                 label="④ 复核脚本 exit=0（八组全绿）"):
    head(label)
    script = script or os.path.join(M19, "复核_M19_20221023.py")
    r = run([sys.executable, "-X", "utf8", script], cwd=cwd or ROOT, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    green = "【绿】全部通过" in out
    rec("复核脚本 exit=0", r.returncode == 0, "rc=%d" % r.returncode)
    rec("复核脚本八组全绿", green, "看到「【绿】全部通过」" if green else "★没看到")
    fails = [ln for ln in out.splitlines() if ln.startswith("【红】")]
    if fails:
        print("     %s" % fails[0][:160])
    return r.returncode == 0 and green


def check_portable():
    head("⑤ 路径可移植（不许再出现盘符绝对路径；仓根必须从 __file__ 推）")
    for rel in PORTABLE_SOURCES:
        p = os.path.join(HERE, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            rec("无盘符绝对路径 %s" % rel, False, "缺件")
            continue
        src = open(p, encoding="utf-8").read()
        hits = DRIVE_PAT.findall(src)
        rec("无盘符绝对路径 %s" % rel, not hits,
            "命中 %d 处" % len(hits) if hits else "")
    script = os.path.join(M19, "复核_M19_20221023.py")
    src = open(script, encoding="utf-8").read() if os.path.isfile(script) else ""
    rec("仓根从 __file__ 推", "__file__" in src, "")
    rec("有 AGENTCTL_ROOT 覆盖口", "AGENTCTL_ROOT" in src, "")


def check_autocrlf():
    head("⑥ 入库字节 vs 签出字节（逐字节护栏的前提：签出不得改字节）")
    loc = git(["config", "--get", "core.autocrlf"]).stdout.strip() or "(未设)"
    glo = run(["git", "config", "--global", "--get", "core.autocrlf"]).stdout.strip() or "(未设)"
    sysc = run(["git", "config", "--system", "--get", "core.autocrlf"]).stdout.strip() or "(未设)"
    # 仓里入库的 blob 本身必须就是 LF（这是逐字节护栏成立的前提）
    bad = []
    for rel in ("src/agentctl/core.py", "legacy_tools/_src/act.py"):
        r = git(["show", "HEAD:" + rel])
        if r.returncode == 0 and "\r\n" in r.stdout:
            bad.append(rel)
    rec("入库 blob 是 LF（不含 CRLF）", not bad,
        "入库内容自带 CRLF：%s" % bad if bad else "")
    print("     ★ 提示（不是红）：本仓 **没有** .gitattributes ⇒ 签出时行尾由**每台机器自己的**")
    print("       git 配置决定：本仓本地=%s ／ 全局=%s ／ 系统=%s。" % (loc, glo, sysc))
    print("       ⇒ 新克隆没有本地配置 ⇒ 吃**全局/系统**那份；若那边是 true，")
    print("         普通 clone 会把入库的 LF **改成 CRLF**")
    print("       ⇒ 逐字节护栏（②③④⑤ 组）在那种克隆上必红 —— 与护栏是否进仓无关。")
    print("       ⇒ 下面的克隆演练用 `git -c core.autocrlf=false clone` 复现**入库字节**。")
    print("       ⇒ 仓级修法（加 .gitattributes）**不在本单范围**，此处只如实报。")


def do_clone():
    head("⑦ 真·干净克隆演练（git clone 到 %TEMP% → 放护栏 → 从**克隆副本**里跑）")
    dest = os.path.join(tempfile.gettempdir(),
                        "agentctl_review_clone_%s" % time.strftime("%Y%m%d_%H%M%S"))
    clone = os.path.join(dest, "clone")
    try:
        # ★ -c core.autocrlf=false：复现**入库字节**（本仓没有 .gitattributes，
        #   签出行尾由每台机器的 git 配置决定，见 ⑥）
        r = run(["git", "-c", "core.autocrlf=false", "clone", "--quiet", ROOT, clone])
        if r.returncode != 0:
            rec("git clone", False, (r.stderr or "").strip()[:160])
            return False
        rec("git clone", True, clone)
        # 把护栏目录复制进克隆（= 假装它已经进仓）
        bundle = os.path.basename(HERE)
        shutil.copytree(HERE, os.path.join(clone, bundle))
        rec("护栏已放进克隆", True, bundle)
        # ★ 干净克隆的纪律：不许继承本仓的任何绝对路径 ⇒ AGENTCTL_ROOT / AGENTCTL_LIVE_ACT 全清
        env = {"AGENTCTL_ROOT": None, "AGENTCTL_LIVE_ACT": None}
        # ★★ 必须跑**克隆里那一份**脚本：跑原仓那份 = 验的是原仓工作树 = 假绿灯
        script = os.path.join(clone, bundle, "M19", "复核_M19_20221023.py")
        rec("跑的是克隆里的那一份脚本", os.path.isfile(script),
            "仓根≠本仓：%s" % clone)
        ok = check_review(script=script, cwd=clone, env=env,
                          label="⑦b 从**克隆副本**里跑复核脚本（仓根≠本仓）")
        return ok
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def main():
    print("=" * 78)
    print("主控复核护栏 · 干净克隆自检 · 仓 %s" % ROOT)
    print("=" * 78)
    if not os.path.isdir(ROOT):
        print("★ 拿不到仓根（%s）" % ROOT)
        return 2
    check_files()
    check_trackable()
    check_ledger()
    check_review()
    check_portable()
    check_autocrlf()
    if "--clone" in sys.argv:
        do_clone()
    g = sum(1 for _, ok, _ in RESULTS if ok)
    r = sum(1 for _, ok, _ in RESULTS if not ok)
    print("\n汇总：绿 %d / 红 %d" % (g, r))
    if r:
        for k, ok, d in RESULTS:
            if not ok:
                print("  ★红：%s %s" % (k, d))
        print("结论：【红】（rc=1）")
        return 1
    print("结论：【绿】（rc=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
