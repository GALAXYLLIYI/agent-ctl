# -*- coding: utf-8 -*-
r"""证据复核器 · 判据（O1，2026-10-25 重写）

为什么重写：窗口B 交来的版本只是“跑 verifier 并把它的 rc 当自己的 rc”
（正常跑输出 1、注入跑 97）——那不是判据，是转发。本版改成**显式断言**：
  · 合法记录  ⇒ verifier rc=0
  · 矛盾记录  ⇒ rc=1
  · 不可复算  ⇒ rc=2 且报告含「不可独立复算」
  · --inject  ⇒ 故意把矛盾记录期望成 0，判据必须红

跑法：python -X utf8 tests\_P3_证据复核_判据.py [--inject]
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VERIFIER = os.path.join(ROOT, "tools", "verify_evidence.py")

sys.path.insert(0, os.path.join(ROOT, "src"))
from agentctl import evidence                                    # noqa: E402


def _rec(**kw):
    base = dict(actor="judge", target={"hwnd": 1}, frame_hash="aa11",
                action={"tool": "ctl_click"}, wait_ms=100, verdict="met",
                reasons=["ok"], evidence_refs=[],
                recompute_spec={"DIFF_STEP": 4, "HYSTERESIS": 2, "need": 1},
                recomputable=True)
    base.update(kw)
    return evidence.make_record(**base)


def run_verifier(recs):
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    out = p + ".md"
    try:
        json.dump(recs, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        r = subprocess.run([sys.executable, "-X", "utf8", VERIFIER,
                            "--evidence", p, "--out", out],
                           cwd=ROOT, capture_output=True, text=True)
        rep = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
        return r.returncode, rep
    finally:
        for x in (p, out):
            try:
                os.unlink(x)
            except OSError:
                pass


def main():
    inject = "--inject" in sys.argv
    fails = []

    def check(name, cond, detail=""):
        print(("  OK  " if cond else "  X   ") + name + (("  —— " + detail) if not cond else ""))
        if not cond:
            fails.append(name)

    rc0, _ = run_verifier([_rec()])
    rc1, _ = run_verifier([_rec(verdict="not_met", reasons=["ok"])])
    rc2, rep2 = run_verifier([_rec(recomputable=False)])

    if inject:
        # ★ 注入：故意把“矛盾记录”期望成 rc=0 —— 判据必须红
        check("注入·矛盾记录被期望成 0（应当红）", rc1 == 0, "actual rc=%s" % rc1)
    else:
        check("合法记录 ⇒ rc=0", rc0 == 0, "actual %s" % rc0)
        check("矛盾记录 ⇒ rc=1", rc1 == 1, "actual %s" % rc1)
        check("不可复算 ⇒ rc=2", rc2 == 2, "actual %s" % rc2)
        check("报告含「不可独立复算」", "不可独立复算" in rep2)

    print()
    if fails:
        print("【红】%d 项失败：%s" % (len(fails), fails))
        raise SystemExit(1)
    print("【绿】全部通过：三态退出码与报告措辞都对得上，且注入必红。")


if __name__ == "__main__":
    main()
