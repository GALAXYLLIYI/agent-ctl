# -*- coding: utf-8 -*-
r"""_authstate注入.py —— 证明 _authstate自证.py **能红**（判据没空转）

★ 为什么单独一个文件：全绿只说明「现在没坏」，不说明「坏了会红」。
  本脚本把 authstate.py 复制一份，逐个注入 dispatch_L §二 点名的四类缺陷
  （外加一类锁的），用**同一条**自证判据跑被污染的副本，
  确认对应断言真的会红 —— 这才叫判据有证明力。

★ 只读 + 临时目录：注入都在 %TEMP% 下的副本里改，**真 src\agentctl\authstate.py 一个字节都不动**。

用法：
    python -X utf8 tests\_authstate注入.py
    python -X utf8 tests\_authstate注入.py --keep
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOOD_PKG = os.path.join(ROOT, "src", "agentctl")
SELFTEST = os.path.join(HERE, "_authstate自证.py")
TARGET = os.path.join(GOOD_PKG, "authstate.py")


def _legacy_home():
    """真源的 legacy home（gate_lock.py 随它走）。

    ★ 副本在 %TEMP% 下，上级没有 legacy_tools\\ ⇒ 副本里的 agentctl.gate
      解析不出 HOME ⇒ authstate._gate_lock() fail-closed ⇒ 自证在第 0 节就崩，
      看起来像「注入没红」其实是「压根没跑到判据」。
      所以显式用 AGENTCTL_LEGACY_HOME 指过去（gate.py 的第 1 候选）。
    """
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from agentctl import gate as g
    if not g.HOME:
        raise SystemExit("真源都解析不出 legacy home，先修环境")
    return g.HOME


LEGACY_HOME = _legacy_home()

# 每个注入：(名字, [(锚点, 替换), …], 期望红的断言关键词)
#   一个注入可以打**多个点** —— 有些缺陷是双重复核，只打一处未必复现
#   （父过期在 check() 与 _live_windows() 各判一次，真实的「父亡不判子亡」
#     要两处同时坏才现形；只坏一处会被另一层挡住，这是**好事**，但测不出来）。
#   锚点必须在文件里**唯一**出现，否则宁可不注入也不瞎改。
INJECTS = [
    (
        "ack_clobber（窗口放行把 scopes 覆盖成 C-only —— dispatch_L 的已确诊 bug）",
        [('        windows = st.setdefault("windows", [])',
          '        task["scopes"] = ["C"]   # ← 注入：覆盖\n'
          '        windows = st.setdefault("windows", [])')],
        "B 档仍可用",
    ),
    (
        "ack_escalate（窗口放行把 scopes 抬到 D）",
        [('        windows = st.setdefault("windows", [])',
          '        task["scopes"] = norm_scopes(list(task["scopes"]) + ["D"])  # ← 注入：抬权\n'
          '        windows = st.setdefault("windows", [])')],
        "D 档仍被拒",
    ),
    (
        "acts_reset（窗口放行重置 acts 总账）",
        [('        windows = st.setdefault("windows", [])',
          '        task["acts_used"] = 0  # ← 注入：重置\n'
          '        windows = st.setdefault("windows", [])')],
        "acts_used 不变",
    ),
    (
        "parent_expired_ignored（父过期不判子亡）",
        [("    if not _parent_alive(task, now):\n"
          "        out[\"reason\"] = \"任务授权已过期（子授权同时失效）\"\n"
          "        return out",
          "    pass  # ← 注入：check() 不再看父过期"),
         ("    if not _parent_alive(task, now):\n        return []",
          "    pass  # ← 注入：_live_windows 也不再看父过期")],
        "父亡一票否决",
    ),
    (
        "no_lock（去掉跨进程锁，锁外读-改-写）",
        [("        self._lock = _exclusive(self.authdir, self.timeout)",
          "        import contextlib  # ← 注入：不起锁\n"
          "        self._lock = contextlib.nullcontext()"),
         ("            if exc_type is None:\n"
          "                _write_locked(self.path, self.state)",
          "            if exc_type is None:\n"
          "                import time as _t; _t.sleep(0.08)  # ← 注入：写前拖慢，逼出交错\n"
          "                _write_locked(self.path, self.state)")],
        "没有丢更新",
    ),
    (
        "created_unnormalized（身份键不归一，同窗拆成两条）",
        [("    if v is None or isinstance(v, float):\n        return v",
          "    return v  # ← 注入：原样返回，created 不归一")],
        "没有丢更新",
    ),
    (
        "deny_window_bypass（动作时敏感窗终判被旁路 —— 接线单 §二 5 的头号防线）",
        [('        hit, why, kind = deny_window(window.get("exe"), window.get("cls"),\n'
          '                                     window.get("title"))',
          '        hit, why, kind = False, "", ""  # ← 注入 deny_window_bypass：终判旁路')],
        "永久拒：python.exe",
    ),
]


def make_bad_pkg(dst, name, points):
    """复制整包到 dst，只改坏 authstate.py。返回 (ok, 说明)。"""
    pkg = os.path.join(dst, "agentctl")
    shutil.copytree(GOOD_PKG, pkg)
    bad = os.path.join(pkg, "authstate.py")
    with open(bad, encoding="utf-8") as f:
        src = f.read()
    notes = []
    for anchor, repl in points:
        n = src.count(anchor)
        if n != 1:
            return False, "锚点出现 %d 次（要求唯一），注入失效" % n
        src = src.replace(anchor, repl, 1)
        notes.append("点%d" % len(notes))
    with open(bad, "w", encoding="utf-8") as f:
        f.write(src)
    return True, "注入 %d 处（%s）" % (len(points), "、".join(notes))


def run_selftest(src_dir):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["AGENTCTL_LEGACY_HOME"] = LEGACY_HOME
    p = subprocess.run(
        [sys.executable, "-X", "utf8", SELFTEST, "--src", src_dir],
        env=env, capture_output=True, timeout=300)
    txt = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    reds = re.findall(r"\[FAIL\]\s*(.+?)(?:\s{2}·|$)", txt, re.M)
    under = ""
    m = re.search(r"被测模块：(.+)", txt)
    if m:
        under = m.group(1).strip()
    return p.returncode, reds, txt, under


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    print("真源（不会被动）：%s" % TARGET)
    print("注入副本：%TEMP% 下的包副本\n")

    base = tempfile.mkdtemp(prefix="authstate_inject_%d_" % os.getpid())
    # 先跑一次未污染的，确认基线是绿的
    rc0, reds0, _, under0 = run_selftest(os.path.join(ROOT, "src"))
    print("【基线】未注入的版本：退出码 %d，红 %d 条 —— %s"
          % (rc0, len(reds0), "绿（符合预期）" if rc0 == 0 else "异常！"))
    print("       被测：%s" % under0)
    if rc0 != 0:
        print("  基线不绿，后面没法判。基线红项：%s" % reds0)
        return 2

    results = []
    try:
        for i, (name, points, expect) in enumerate(INJECTS):
            d = os.path.join(base, "inj%d" % i)
            os.makedirs(d, exist_ok=True)
            made, why = make_bad_pkg(d, name, points)
            if not made:
                print("【%d】%s\n     注入失败：%s\n" % (i, name, why))
                results.append((name, False, "注入失败"))
                continue
            rc, reds, _, under = run_selftest(d)
            hit = (expect is None) or any(expect in r for r in reds)
            results.append((name, rc != 0 and hit, "%d 条红 %s" % (len(reds), reds[:4])))
            print("【%d】%s" % (i, name))
            print("     注入：%s" % why)
            print("     被测：%s" % under)
            print("     判据：%s" % ("红 ✔" if (rc != 0 and hit) else "没红 ✘"))
            print("     红了 %d 条：%s" % (len(reds), "；".join(reds[:6]) or "（无）"))
            if expect:
                print("     期望包含：%s —— %s"
                      % (expect, "命中" if any(expect in r for r in reds) else "没命中"))
            print()
    finally:
        if args.keep:
            print("（--keep 保留 %s）" % base)
        else:
            shutil.rmtree(base, ignore_errors=True)

    bad = [n for n, okk, _ in results if not okk]
    print("=" * 70)
    if bad:
        print("注入结果：%d/%d 条注入**没被抓到** —— %s"
              % (len(bad), len(results), "；".join(bad)))
        return 1
    print("注入结果：%d/%d 条注入全被抓到（判据有证明力）" % (len(results), len(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

