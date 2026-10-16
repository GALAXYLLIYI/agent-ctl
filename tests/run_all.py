# -*- coding: utf-8 -*-
r"""tests\run_all.py —— 判据总入口（窗C · 2026-10-15）

一条命令跑齐 tests\ 下沉淀的全部判据：

    python -X utf8 tests\run_all.py

退出码：
    0 = 全部判据**全部**绿
    1 = 至少一条红 / 判据文件缺失 / 判据崩了 / 超时

设计约束（派单 §三「只搬运 + 接线，不改判据逻辑」）：
  · 本文件**只做调度**：起子进程、透传输出、收退出码、汇总。
    任何一条判据的断言一个字都没改；本文件也不复刻任何一条断言。
  · 输出**原样透传**，不吞不改 —— 红/绿留档里能看到每条判据的原始输出。
  · **fail-closed**：判据文件不见了、抛异常了，一律算红。
    「少一条判据」绝不能被读成「全绿」（这正是退回单
    work\_T1_退回_W2_判据退出码.md 记的那个教训）。

判据清单（原散落处 → 现居处）：
    work\_T1_W2_判据.py         → tests\_T1_W2_判据.py
    docs\证据\check_t1_w1.py    → tests\check_t1_w1.py
    work\_T1_窗3_自证.py        → tests\_T1_窗3_自证.py
    work\_codex_接缝判据.py     → tests\_codex_接缝判据.py
    work\_2h_wA_档位口径判据.py → tests\_2h_wA_档位口径判据.py
    work\_2h_wB_D级绊线判据.py  → tests\_2h_wB_D级绊线判据.py
    （窗F 新增，就地落在 tests）    → tests\_2h_wF_配色冻结判据.py
    （P4 新增，就地落在 tests）     → tests\_P4_定位器夹具判据.py（配 tests\fixtures\ 两张固定图）

跑红（不需要改任何判据、不需要 git 写）：
  · 少一条：把 tests\ 下任一判据临时挪走再跑 ⇒ 该条报「判据文件不在」⇒ 退 1。
  · 指向陈旧副本：wB 那条自带 AGENTCTL_ROOT 覆盖口（它自己的用法里写了），
    指向归档的红基线仓就能让整条入口变红：
        $env:AGENTCTL_ROOT="$PWD\work\_wD_红基线_repo"; python -X utf8 tests\run_all.py

参数：
    --list      只打印判据清单，不跑
    --timeout N 单条判据的秒数上限（默认 900；超时算红）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (键, 判据脚本, 固定参数, 这条判据查的是什么)
CRITERIA = (
    ("w1", "check_t1_w1.py", (),
     "T1 窗1：docs/AGENT接口.md §3-bis 与契约条目逐条对上"),
    ("接缝", "_codex_接缝判据.py", (),
     "契约（唯一真相源）与代码实际行为是否脱节（J1-J4）"),
    ("w2", "_T1_W2_判据.py", ("--new",),
     "T1 窗2：eid 绑签发它的那次快照 + bench b11 回归守（跑现役 src）"),
    ("w3", "_T1_窗3_自证.py", ("all",),
     "T1 窗3：uia_tree 坐标系标注 + ctrl_border --follow 接线自证"),
    ("wA", "_2h_wA_档位口径判据.py", (),
     "窗A·P3：边框判档口径 vs 闸门判档口径（默认 --target 现役 ctrl_border.py）"),
    ("wB", "_2h_wB_D级绊线判据.py", (),
     "窗B·P6/P5：D 级有意不解锁的机械绊线 + 发证上限口径"),
    ("wF", "_2h_wF_配色冻结判据.py", (),
     "窗F：边框配色/按档厚度/1px 反色描边**冻结钉子**（五个 RGB 写死）+ 契约颜色名同步"),
    ("P4", "_P4_定位器夹具判据.py", (),
     "P4：定位器**固定图夹具**（正向必须找到 4 个 + ★反向必须返回空）——堵 selftest.py:95-98 的盲区"),
)


def out(msg=""):
    print(msg, flush=True)


def run_one(key, script, args, timeout):
    path = os.path.join(HERE, script)
    out("\n" + "#" * 78)
    out("# 判据 %s · %s %s" % (key, script, " ".join(args)))
    out("#" * 78)
    if not os.path.isfile(path):
        out("[红] %s 判据文件不在：%s" % (key, path))
        out("     ⇒ 少一条判据不能当全绿（fail-closed），这条记红。")
        return 1, 0.0, "判据文件不在"
    cmd = [sys.executable, "-X", "utf8", path] + list(args)
    t0 = time.monotonic()
    try:
        rc = subprocess.call(cmd, cwd=ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        out("[红] %s %s 超过 %ss 没跑完，按红记（fail-closed）" % (key, script, timeout))
        return 1, time.monotonic() - t0, "超时"
    except Exception as e:
        out("[红] %s 判据起不来：%s: %s" % (key, type(e).__name__, e))
        return 1, time.monotonic() - t0, "起不来"
    dt = time.monotonic() - t0
    if rc == 0:
        out("[绿] %s %s（rc=0，%.1fs）" % (key, script, dt))
        note = "绿"
    else:
        out("[红] %s %s（rc=%s，%.1fs）—— 细节看它自己打的红条目"
            % (key, script, rc, dt))
        note = "红（rc=%s）" % rc
    return rc, dt, note


def main(argv=None):
    ap = argparse.ArgumentParser(description="tests\\ 判据总入口")
    ap.add_argument("--list", action="store_true", help="只打印清单，不跑")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="单条判据秒数上限（默认 900；超时算红）")
    a = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    out("=" * 78)
    out("判据总入口 · 仓 %s" % ROOT)
    out("=" * 78)
    out("清单（%d 条）：" % len(CRITERIA))
    for key, script, args, desc in CRITERIA:
        out("  %-4s %-24s %-8s %s" % (key, script, " ".join(args) or "（无参）", desc))
    if a.list:
        return 0
    out("")

    results = []
    for key, script, args, _desc in CRITERIA:
        rc, dt, note = run_one(key, script, args, a.timeout)
        results.append((key, script, rc, dt, note))

    out("")
    out("=" * 78)
    out("汇总")
    out("=" * 78)
    for key, script, rc, dt, note in results:
        out("  [%s] %-4s %-24s rc=%-4s %6.1fs  %s"
            % ("绿" if rc == 0 else "红", key, script, rc, dt, note))
    reds = [r for r in results if r[2] != 0]
    out("-" * 78)
    out("判据 %d 条：绿 %d，红 %d ；总用时 %.1fs"
        % (len(results), len(results) - len(reds), len(reds),
           sum(r[3] for r in results)))
    if reds:
        out("结论：【红】—— %s" % "、".join(r[0] for r in reds))
        out("（退出码 1；每条判据的红条目细节都在它自己那段原始输出里）")
        return 1
    out("结论：【绿】全部判据全绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
