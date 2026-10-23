# -*- coding: utf-8 -*-
r"""tests\_authdir一致_判据.py —— J1 主判据：三方对「账本在哪」必须给出**同一个答案**

★ 判什么（工单 §7.4 J1）
  设 `AGENTCTL_AUTHDIR=%TEMP%\x` ⇒ **签发方 / 老线闸门 / 新线闸门** 三方解析出的账本
  绝对路径**全等**；清空该变量 ⇒ 三方对默认路径再全等，且等于
  `%LOCALAPPDATA%\AgentCtl\_auth`。
  ★ 改前（实测 2026-10-23）：老线+签发方读 `agent-ctl\legacy_tools\_auth`，
    新线读 `dsh-viewer-plugin\tools\_auth` ⇒ **两本账**；env 覆盖只覆盖 1/3。
    ⇒ 本判据对着改前的代码跑**必须红**（先红），改后必须绿。

★ 实现口径（为什么不 import 到一起量）
  三个探针各起**独立子进程**：每个进程自己 import 自己那一侧、各自解析自己的路径。
  ⇒ 互不污染（不会出现「A 侧 import 之后 B 侧拿到 A 的模块缓存」这种假一致）。

★ 探针
  · 新线闸门：`from agentctl import gate` → `gate.AUTHDIR`
  · 老线闸门：`import gate`（legacy_tools 目录）→ `gate.AUTHDIR`
  · 签发方  ：`import auth_popup`（legacy_tools 目录）→ `auth_popup.AUTHDIR`
  · 共享模块：`from agentctl import authdir` → `authdir.resolve()`（规则正文本身）

★ 三档用例
  ① env 覆盖   ：AGENTCTL_AUTHDIR=<沙箱绝对路径> ⇒ 三方全等且 == 该路径
  ② 默认路径   ：不设 AGENTCTL_AUTHDIR ⇒ 三方全等且 == %LOCALAPPDATA%\AgentCtl\_auth
  ③ 应用目录覆盖：AGENTCTL_APP_DIR=<沙箱> （账本与急停同家的那个 _APP_DIR）⇒ 同上口径

★ 注入自证（--inject）：把**签发方**的解析改回写死 `BASE/_auth`（= 当前病灶）
  —— 改在**临时副本**上，现役一个字节不动 ⇒ 必须**转红**并点名签发方那一条。
  ⇒ 这证明 J1 不是「恒绿」的死判据。

用法：
    python -X utf8 tests\_authdir一致_判据.py             # 正跑（期望 exit=0）
    python -X utf8 tests\_authdir一致_判据.py --inject    # 注入自证（期望 exit=1）

退出码：
    0 = 全绿 ｜ 1 = 有红（三方不一致 / 注入没让行为退化）｜ 2 = 环境不满足（找不到仓内文件）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

# ★ 判据要 import 仓内模块 ⇒ 顺手会在它们旁边生成 __pycache__\*.pyc。
#   ⇒ 落笔前先关掉字节码写入 ⇒ 判据**零足迹**（跑完不留任何副产物）。
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
LEGACY = os.path.join(ROOT, "legacy_tools")

# 老线那侧要 import 的伴生模块（缺一个就 import 失败 —— 这正是「伴生模块」的含义）
LEGACY_COMPANIONS = ("gate.py", "gate_lock.py", "auth_popup.py", "authdir.py")

# ── 四个探针（各起独立子进程）────────────────────────────────────────────
_PROBE_SRC = {
    "新线闸门": ("import sys,json; sys.path.insert(0, %r); "
                 "from agentctl import gate; print(json.dumps(gate.AUTHDIR, ensure_ascii=False))" % SRC),
    "老线闸门": ("import sys,json; sys.path.insert(0, %r); "
                 "import gate; print(json.dumps(gate.AUTHDIR, ensure_ascii=False))"),
    "签发方": ("import sys,json; sys.path.insert(0, %r); "
               "import auth_popup; print(json.dumps(auth_popup.AUTHDIR, ensure_ascii=False))"),
    "共享模块": ("import sys,json; sys.path.insert(0, %r); "
                 "from agentctl import authdir; print(json.dumps(authdir.resolve(), ensure_ascii=False))" % SRC),
}
PROBE_ORDER = ("新线闸门", "老线闸门", "签发方", "共享模块")


def _norm(p):
    """比较用口径：绝对化 + Windows 下大小写归一（Windows 路径本来不分大小写）。"""
    if p is None:
        return None
    return os.path.normcase(os.path.abspath(p))


def child_env(**over):
    """给子进程准备环境。值给 None = **删掉**这个变量（不许继承外界的）。"""
    e = os.environ.copy()
    e["PYTHONDONTWRITEBYTECODE"] = "1"      # 不许在仓里留 __pycache__
    for k, v in over.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


def probe_one(name, env, legacy_dir):
    """跑一个探针。返回 (路径 or None, 失败原因 or '')。"""
    code = _PROBE_SRC[name]
    if name in ("老线闸门", "签发方"):
        code = code % legacy_dir
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT, env=env, timeout=120)
    except Exception as e:
        return None, "探针起不来：%s: %s" % (type(e).__name__, e)
    if p.returncode != 0:
        tail = ((p.stderr or p.stdout or "").strip().splitlines() or [""])[-1]
        return None, "探针退出码 %d：%s" % (p.returncode, tail[:160])
    out = (p.stdout or "").strip().splitlines()
    if not out:
        return None, "探针没有输出"
    try:
        return json.loads(out[-1]), ""
    except Exception as e:
        return None, "探针输出不是 JSON：%r（%s）" % (out[-1][:120], e)


def probe_all(env, legacy_dir=LEGACY):
    """四个探针各跑一次。返回 {名字: (路径 or None, 原因)}。"""
    return {n: probe_one(n, env, legacy_dir) for n in PROBE_ORDER}


def default_authdir(app_dir=None):
    """判据自己算一遍默认账本路径（不 import 产品代码 ⇒ 独立口径）。"""
    base = app_dir or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl")
    return os.path.abspath(os.path.join(base, "_auth"))


def _row(out, label, want, got, ok, note=""):
    out.append((label, want, got, bool(ok), note))


def run_cases(legacy_dir=LEGACY, src_dir=SRC, tag="现役", out=None):
    """跑三档用例。返回 (rows, 是否全绿)。

    ★ 期望值由判据**自己**算（不 import 产品代码）⇒ 独立口径，不自证。
    """
    rows = [] if out is None else out
    tmp = tempfile.mkdtemp(prefix="_J1_authdir_")
    try:
        # ① env 覆盖
        sandbox = os.path.join(tmp, "sandbox")
        env = child_env(AGENTCTL_AUTHDIR=sandbox, AGENTCTL_APP_DIR=None)
        got = probe_all(env, legacy_dir)
        want = os.path.abspath(sandbox)
        for n in PROBE_ORDER:
            p, why = got[n]
            _row(rows, "① 设 AGENTCTL_AUTHDIR ⇒ %s" % n, want,
                 p if p else ("失败：" + why), _norm(p) == _norm(want))
        # ② 默认路径
        env = child_env(AGENTCTL_AUTHDIR=None, AGENTCTL_APP_DIR=None)
        got = probe_all(env, legacy_dir)
        want = default_authdir()
        for n in PROBE_ORDER:
            p, why = got[n]
            _row(rows, "② 不设 AGENTCTL_AUTHDIR ⇒ %s" % n, want,
                 p if p else ("失败：" + why), _norm(p) == _norm(want))
        # ③ 应用目录覆盖（账本与急停同家的那个 _APP_DIR）
        appdir = os.path.join(tmp, "appdir")
        env = child_env(AGENTCTL_AUTHDIR=None, AGENTCTL_APP_DIR=appdir)
        got = probe_all(env, legacy_dir)
        want = default_authdir(appdir)
        for n in PROBE_ORDER:
            p, why = got[n]
            _row(rows, "③ 设 AGENTCTL_APP_DIR ⇒ %s" % n, want,
                 p if p else ("失败：" + why), _norm(p) == _norm(want))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rows, all(r[3] for r in rows)


# ── 注入：把签发方改回写死 BASE/_auth（= 当前病灶）────────────────────────
_HARDCODED = 'AUTHDIR = os.path.join(BASE, "_auth")'
_AUTHDIR_LINE = re.compile(r"^AUTHDIR\s*=.*$", re.M)


def _copy_writable(src, dst):
    """拷一份并**清掉只读位**（产物是 gen_legacy 设的只读；副本要能改）。"""
    shutil.copy2(src, dst)
    try:
        os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def copy_legacy(legacy_dir, dst):
    """把老线那几份（闸门/锁/签发方/共享模块）拷到 dst（副本可写）。"""
    os.makedirs(dst, exist_ok=True)
    for f in LEGACY_COMPANIONS:
        s = os.path.join(legacy_dir, f)
        if os.path.isfile(s):
            _copy_writable(s, os.path.join(dst, f))
    return dst


def patch_popup(dst):
    """把**副本**里签发方的 AUTHDIR 改回写死 BASE/_auth。

    返回 (是否真的改到了, 说明)。★ 改不到 = 注入没下上 ⇒ 判据必须红。
    """
    pop = os.path.join(dst, "auth_popup.py")
    if not os.path.isfile(pop):
        return False, "签发方不在（%s）" % dst
    with open(pop, "r", encoding="utf-8") as f:
        txt = f.read()
    if _HARDCODED in txt:
        # 已经是写死形态 ⇒ 病灶**本来就在**⇒ 注入无处可下
        return False, "签发方**已经是**写死 BASE/_auth 形态（病灶本来就在）⇒ 注入无处可下"
    new, n = _AUTHDIR_LINE.subn(lambda m: _HARDCODED, txt, count=1)
    if n != 1:
        return False, "签发方里没找到 AUTHDIR 赋值行（形态变了？）⇒ 注入无处可下"
    with open(pop, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    return True, "已把签发方改回写死 BASE/_auth"


def inject_popup(legacy_dir, dst):
    """拷一份老线树到 dst，并把**签发方**的 AUTHDIR 改回写死 BASE/_auth（= 当前病灶）。"""
    copy_legacy(legacy_dir, dst)
    return patch_popup(dst)


def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for label, want, got, ok, note in rows:
        print("  %-52s 期望=%-46s 实得=%-46s %s%s"
              % (label, str(want)[:46], str(got)[:46],
                 "PASS" if ok else "FAIL", ("  · " + note) if note else ""),
              flush=True)


def do_normal():
    print("== J1 判据 · AUTHDIR 三方一致 ==", flush=True)
    print("[口径] 三方 = 老线闸门 / 新线闸门 / 签发方（+ 共享模块本身）", flush=True)
    print("[口径] 期望路径由判据自己算（不 import 产品代码）⇒ 独立口径", flush=True)
    rows, ok = run_cases()
    show_rows("三档用例", rows)
    print()
    print("=" * 78, flush=True)
    if ok:
        print("【绿】三方对「账本在哪」给出同一个答案（%d 条断言）" % len(rows), flush=True)
        return 0
    bad = [r for r in rows if not r[3]]
    print("【红】%d 条不一致：" % len(bad), flush=True)
    for r in bad:
        print("   · %s：期望 %s，实得 %s" % (r[0], r[1], r[2]), flush=True)
    return 1


def do_inject():
    """注入自证：签发方改回写死 BASE/_auth ⇒ 必须转红（点名签发方那一条）。"""
    print("== J1 判据 · 注入自证（签发方改回写死 BASE/_auth）==", flush=True)
    tmp = tempfile.mkdtemp(prefix="_J1_inject_")
    dst = os.path.join(tmp, "legacy")
    try:
        # ① 先量「未注入的副本」：必须与现役一致（否则注入对照就没有可比性）
        j1_copy = copy_legacy(LEGACY, dst)
        base_rows, base_ok = run_cases(legacy_dir=j1_copy)
        show_rows("未注入的副本（基线）", base_rows)
        applied, why = patch_popup(dst)
        if not applied:
            print()
            print("=" * 78, flush=True)
            print("【红】注入无处可下：%s" % why, flush=True)
            print("★ 判据此刻抓不到病灶（要么病灶已修、要么形态变了）⇒ 判红。", flush=True)
            return 1
        print("\n注入内容：签发方 AUTHDIR ⇒ %s（改在临时副本上，现役不动）" % _HARDCODED,
              flush=True)
        inj_rows, inj_ok = run_cases(legacy_dir=dst)
        show_rows("注入后的副本", inj_rows)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("=" * 78, flush=True)
    if not base_ok:
        print("【红】注入前的副本就不一致 ⇒ 注入对照不成立（判据口径可能变了）", flush=True)
        return 1
    if inj_ok:
        print("【✗崩溃】注入后仍然全绿 ⇒ J1 抓不住这个病 ⇒ 判据没牙", flush=True)
        return 2
    hit = [r for r in inj_rows if not r[3] and "签发方" in r[0]]
    if not hit:
        print("【红】注入后有红，但不是「签发方」那几条 ⇒ 判据口径可能变了", flush=True)
        return 1
    print("【红】注入被逮到（%s）⇒ exit=1" % "；".join(r[0] for r in hit), flush=True)
    print("★ 判据有证明力：把签发方改回写死 BASE/_auth，它当场就红。", flush=True)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红）")
    args = ap.parse_args()
    for need in (os.path.join(SRC, "agentctl", "gate.py"),
                 os.path.join(SRC, "agentctl", "authdir.py"),
                 os.path.join(LEGACY, "gate.py"),
                 os.path.join(LEGACY, "auth_popup.py")):
        if not os.path.isfile(need):
            print("[验不了] 仓内文件不在：%s" % need, flush=True)
            return 2
    try:
        return do_inject() if args.inject else do_normal()
    finally:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
