# -*- coding: utf-8 -*-
r"""判据 46：M12「等待合并」验收（**阶段一：合并未实现 ⇒ 本条现在就该红**）

M11 证明 core 与 act **等的是同一件事**（等了两遍）⇒ 合并可省 200~340ms/次。
本判据是**阶段二**（实现合并）的验收尺；**阶段一**（只做设计）它必须是**红的**。

═══════════════════════════════════════════════════════════════════════════════
断言
═══════════════════════════════════════════════════════════════════════════════
A 组｜测量（不改 src/，只读）
  ① 测出**当前**（未合并）的端到端，并与 M11 基线 1,682~1,718ms 对照
     —— 偏离基线太远 ⇒ 报「环境变了」而不是「产品变了」

B 组｜**合并是否真的发生了**（★ 现在红）
  ② 端到端 ≤ 基线下界 − 200ms（看起来合并了）**且** core 侧 `wait_change` 实测 ≈ 0
     ⇒ 才算合并成功。
     ★ **现在必红**：core 侧还在等 300ms（实测 321~343ms）⇒ 端到端下不来。
  ③ **自相矛盾检查**：`--inject` 会把端到端伪造成「已合并」的数，
     但 core 侧实测仍在等 ⇒ 前后对不上 ⇒ **必须红**（证明本判据抓得住假账）

C 组｜失败口径（**不许为了变绿而放松**）
  ④ 画面没变 ⇒ 结果**必须**落 `code=8`（⛔ 不许当成功）
  ⑤ `unknown`（没测出来）**不许**被当成成功（code=0 且 warning 说「没测出来」⇒ 红）

退出码：0 绿 / 1 红 / 2 验不了（**验不了不许读成绿**）

用法：
    python -X utf8 tests\_P2_M12_等待合并_判据.py            # 现在应当红（合并未实现）
    python -X utf8 tests\_P2_M12_等待合并_判据.py --inject   # 造假账 ⇒ 也必须红
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

#: M11 基线（docs\实测_动作链分段_20221022.md，主控已复核）
BASE_LO = 1682.0
BASE_HI = 1718.0
#: 合并的可接受收益下限（主控裁：200~340ms/次）
MERGE_SAVE_MIN = 200.0

GREEN, RED, SKIP = [], [], []


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def finish():
    print("\n" + "=" * 66, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】等待合并**尚未实现**（阶段一的预期结果）", flush=True)
        return 1
    print("结论：【绿】等待已合并成「只等一次」，且失败口径没松", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="M12 等待合并判据")
    ap.add_argument("--inject", action="store_true", help="造假账（端到端伪造成已合并）⇒ 必须红")
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()

    print("=" * 70, flush=True)
    print("判据 46：M12 等待合并验收", flush=True)
    print("=" * 70, flush=True)
    print("★ 阶段一（合并未实现）本条必须红；阶段二实现后应转绿。", flush=True)

    try:
        import _M11_分段测量 as meas                    # noqa: E402
    except Exception as e:
        rec("① 测出当前端到端并与基线对照", False,
            "★ 测量器不可用：%s: %s" % (type(e).__name__, e), skip=True)
        return finish()

    # ── 写动作前提：租约。**不自己持**（会和测量器的租约打架 ⇒ 点击 code=10）；
    #    改为「事后看 code」：code=10 ⇒ 租约被别人占 ⇒ 环境受阻 ⇒ 验不了。
    import time as _time
    try:
        led, _ = meas.run_all(a.rounds, None)
    except Exception as e:
        rec("① 测出当前端到端并与基线对照", False,
            "★ 测量跑不起来：%s: %s" % (type(e).__name__, e), skip=True)
        return finish()

    cases = led.get("用例") or []
    if led.get("环境受阻"):
        rec("① 测出当前端到端并与基线对照", False, "★ 环境受阻：" + led["环境受阻"], skip=True)
        return finish()

    ends = [c.get("端到端ms") for c in cases]
    core_waits = []
    for c in cases:
        w = 0.0
        for s in (c.get("分段") or []):
            if s.get("段", "").startswith("③"):
                w = s.get("ms", 0.0)
        core_waits.append(w)
    print("\n── 现场测量 ─────────────────────────────────────────────", flush=True)
    for i, c in enumerate(cases):
        print("   第%d轮：端到端 %.1f ms ｜ core 侧 wait_change %.1f ms ｜ code=%s"
              % (i + 1, c.get("端到端ms", 0), core_waits[i],
                 (c.get("结果") or {}).get("code")), flush=True)

    # ── code 统计**必须先算**（② 要用它判「点击真跑成」）──
    codes = [(c.get("结果") or {}).get("code") for c in cases]
    warns = [(c.get("结果") or {}).get("warning") or "" for c in cases]

    # ── 租约被别人占（code=10）⇒ 环境受阻 ⇒ 验不了（**不许拿「没跑成」当测量结果**）──
    if any(c == 10 for c in codes):
        rec("① 测出当前端到端并与基线对照", False,
            "★ **环境受阻**：写动作租约被别的 actor 持有（code=10）⇒ 点击跑不成 ⇒ 本次无法测量",
            skip=True)
        rec("② 合并是否真的发生了", False, "★ 环境受阻，未测量", skip=True)
        rec("④ 画面没变 ⇒ 结果必须落 code=8", False, "★ 环境受阻，未测量", skip=True)
        rec("⑤ unknown 不许被当成成功", False, "★ 环境受阻，未测量", skip=True)
        return finish()

    # ── A 组①：与基线对照 ──
    near = [e for e in ends if BASE_LO * 0.85 <= e <= BASE_HI * 1.15]
    rec("① 测出当前端到端，并与 M11 基线 %.0f~%.0fms 对照" % (BASE_LO, BASE_HI),
        bool(near), "各轮端到端=%s（基线区间外 ⇒ 环境变了，不是产品变了）" % ends)

    # ── B 组②：合并是否真的发生 ──
    best = min(ends) if ends else None
    core_min = min(core_waits) if core_waits else None
    merged_looking = best is not None and best <= BASE_LO - MERGE_SAVE_MIN
    core_not_waiting = core_min is not None and core_min <= 30.0
    ran = all(c in (0, 8) for c in codes)
    if a.inject:
        # 造假账：把端到端伪造成「已合并」的数，core 那一遍却还在等 ⇒ 前后对不上
        merged_looking = True
    # ★ 必须「点击真的跑成了」才算数：没跑成时端到端只有 ~170ms、core 那一遍是 0
    #   ⇒ 若不查 code，② 会**假绿**（这是本判据最容易骗自己的地方）。
    ok_merge = merged_looking and core_not_waiting and ran
    thr = BASE_LO - MERGE_SAVE_MIN
    if ok_merge:
        why = "合并已生效"
    elif not ran:
        why = "★ 本次点击**没跑成**（code=%s ⇒ 端到端与 core 那一遍都不可信）" % (codes,)
    elif a.inject:
        why = ("★ **自相矛盾**：端到端被伪造成 %.0fms（≤阈值），但 core 侧实测仍在等 %.1fms"
               % (thr, core_min or 0.0))
    else:
        why = "★ 合并未实现（core 侧还在等 %.1fms ⇒ 端到端下不来）" % (core_min or 0.0)
    rec("② 端到端 ≤ 基线下界−%.0fms（看起来合并了）**且** core 侧 wait_change ≈ 0 **且** 点击真跑成"
        % MERGE_SAVE_MIN,
        ok_merge,
        "端到端=%s（阈值 %.0f）｜ core 侧 wait_change=%s（阈值 ≤30ms）｜ code=%s ⇒ %s"
        % (best, thr, core_min, codes, why))

    # ── B 组③：注入自证（造假账必须被抓出）──
    if a.inject:
        rec("③ 注入自证：假账必须被抓出（本条必须红）", not ok_merge,
            "注入后端到端被伪造成 %.0fms（≤阈值），但 core 侧实测仍在等 %.0fms ⇒ 矛盾被抓出"
            % (BASE_LO - MERGE_SAVE_MIN, core_min or 0))

    # ── C 组：失败口径 ──
    ok8 = all(c == 8 for c in codes)
    rec("④ 画面没变 ⇒ 结果必须落 code=8（⛔ 不许当成功）", ok8, "各轮 code=%s" % codes)
    ok_unk = not any((c == 0 and ("没测出来" in w or "未知" in w)) for c, w in zip(codes, warns))
    rec("⑤ unknown（没测出来）不许被当成成功", ok_unk,
        "各轮 (code, warning 前 40 字)=%s" % [(c, w[:40]) for c, w in zip(codes, warns)])

    return finish()


if __name__ == "__main__":
    sys.exit(main())
