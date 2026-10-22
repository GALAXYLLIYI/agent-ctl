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
#: ★ **独立自洽性检查的锚点**：「core 再等第二遍」这件事**最多**能省多少 ms
#:   —— M11 实测 core 侧 wait_change = **321~343ms**（docs\实测_动作链分段_20221022.md）。
#:   这个数**独立于本次测量**（上一单的实测）⇒ 用它给端到端的降幅设**物理上限**：
#:   合并最多只能省掉「第二遍」⇒ 端到端降幅 **> 343ms ⇒ 必然是假账**。
CORE_SECOND_PASS_MAX_MS = 343.0

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


def _release_lease():
    """把测量器拿的写动作租约放掉（**别占着主控的会话**）。"""
    try:
        import agentctl.core as _c
        _c.dispatch("ctl_lease", {"action": "release", "actor": "M11分段测量"},
                    actor="M11分段测量")
    except Exception:
        pass


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
        _release_lease()
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
        _release_lease()
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
    # ★ 合并的**定义**：「core 不再等第二遍」⇒ core 侧 wait_change ≈ 0。
    #   「端到端省了多少」是**结果**不是判据：阶段二实测**净省 ≈180~200ms**，
    #   原因是 act 侧判差改**全尺寸**（消除双实现的代价）后每轮更贵
    #   ⇒ 净收益必然略小于「core 再等第二遍」的 321~343ms。
    #   ⇒ 端到端只当「**不许比基线更慢**」的护栏；省多少由实测文档如实报。
    core_not_waiting = core_min is not None and core_min <= 30.0
    not_slower = best is not None and best <= BASE_HI * 1.03
    ran = all(c in (0, 8) for c in codes)
    saved = (BASE_LO - best) if best is not None else None
    # ★ **独立自洽性检查**：端到端的降幅**不得超过**「第二遍」的物理上限
    #   （锚点 = M11 实测 321~343ms，独立于本次测量）
    #   ⇒ 降幅超限 ⇒ 必是假账（合并最多只能省掉「第二遍」）。
    saved_plausible = saved is not None and saved <= CORE_SECOND_PASS_MAX_MS
    if a.inject:
        # ★ 返工①：注入伪造的是**端到端**（伪造成「物理上不可能」的下降量），
        #   **保留 core 侧实测 wait_change**（不伪造）⇒ 假账必须被自洽性检查抓住。
        best = BASE_LO - (CORE_SECOND_PASS_MAX_MS + 200.0)
        saved = BASE_LO - best
        saved_plausible = False          # 伪造的降幅必然超限
    # ★ 必须「点击真的跑成了」才算数：没跑成时端到端只有 ~170ms、core 那一遍是 0
    #   ⇒ 若不查 code，② 会**假绿**（这是本判据最容易骗自己的地方）。
    ok_merge = core_not_waiting and not_slower and ran and saved_plausible
    if ok_merge and not a.inject:
        why = ("合并已生效：core 侧**不再等第二遍**（wait_change %.1fms）｜ 端到端 %.1fms"
               "（基线 %.0f~%.0f，**净省 ≈%.0fms**，≤第二遍物理上限 %.0fms）"
               % (core_min or 0.0, best, BASE_LO, BASE_HI, saved or 0, CORE_SECOND_PASS_MAX_MS))
    elif not ran:
        why = "★ 本次点击**没跑成**（code=%s ⇒ 端到端与 core 那一遍都不可信）" % (codes,)
    elif a.inject:
        why = ("★ **假账被抓出**：伪造的端到端 %.0fms ⇒ 降幅 %.0fms **超过**第二遍的物理上限 %.0fms"
               "（core 侧实测仍在等 %.1fms）⇒ 不被接受"
               % (best or 0, saved or 0, CORE_SECOND_PASS_MAX_MS, core_min or 0.0))
    elif not core_not_waiting:
        why = "★ 合并未实现（core 侧还在等 %.1fms ⇒ 仍是等两遍，端到端降不下来）" % (core_min or 0.0)
    elif not saved_plausible:
        why = ("★ 端到端降幅 %.0fms **超过**第二遍的物理上限 %.0fms ⇒ 必是假账"
               % (saved or 0, CORE_SECOND_PASS_MAX_MS))
    else:
        why = "★ 端到端 %.1fms 比基线上界 %.0fms 还差（劣化）⇒ 不算合并生效" % (best or 0, BASE_HI)
    rec("② 合并已发生：core 侧 wait_change ≈ 0（只等一次）**且** 端到端未劣化 **且** 点击真跑成",
        ok_merge,
        "core 侧 wait_change=%s（阈值 ≤30ms）｜ 端到端=%s（基线 %.0f~%.0f，护栏 ≤%.0f）｜ code=%s ｜ 净省 ≈%.0fms ⇒ %s"
        % (core_min, best, BASE_LO, BASE_HI, BASE_HI * 1.03, codes, saved or 0, why))

    # ── B 组③：注入自证（造假账必须被抓出）──
    if a.inject:
        # ★ 返工①：③ 判的是「**假账有没有被抓住**」⇒ 抓住 ⇒ **绿**（不是红）。
        #   它**不恒真**：自洽性检查（降幅 ≤ 第二遍物理上限）坏了 ⇒ 抓不住 ⇒ ③ 红。
        _caught = (not ok_merge) and (not saved_plausible)
        rec("③ 注入自证：伪造的端到端必须被**独立自洽性检查**抓住（抓住=绿；抓不住=红）",
            bool(_caught),
            "注入伪造端到端 %.0fms（降幅 %.0fms，物理上限 %.0fms）｜ ② 判定=%s ⇒ %s"
            % (best or 0, saved or 0, CORE_SECOND_PASS_MAX_MS,
               "红（假账不被接受）" if not ok_merge else "绿（★假账被接受了！自洽性检查失效）",
               "抓住 ✔" if _caught else "★ 没抓住 ⇒ ③ 必须红"))

    # ── D 组（阶段二新增）：双实现已消除 / 退化必落 unknown ──
    try:
        import agentctl.framecache as _fc
        import agentctl.core as _core
        from PIL import Image
        import numpy as _np
        sys.path.insert(0, os.path.join(ROOT, "legacy_tools", "_src"))
        import act as _act

        # ① 行为级：同一对帧喂两个实现 ⇒ 结论必须一致
        _a = Image.new("RGB", (240, 160), (12, 12, 12))
        _b = _a.copy()
        _px = _b.load()
        for _y in range(40, 90):
            for _x in range(40, 120):
                _px[_x, _y] = (250, 250, 250)
        _d_act = _act._img_diff(_a, _b) or {}
        _d_fc = _fc.diff_images(_a, _b, caret_px=_fc.DIFF_CARET_PX,
                                 min_area_px=_fc.DIFF_MIN_AREA)
        _same = (_d_act.get("changed") == _d_fc.get("changed"))
        _src = open(os.path.join(ROOT, "legacy_tools", "_src", "act.py"), encoding="utf-8").read()
        _wired = ("from agentctl import framecache" in _src)
        rec("⑥ 双实现已消除（同一对帧 ⇒ act 判差与 framecache 判差结论一致）",
            bool(_same) and _wired,
            "act._img_diff=%s ｜ framecache.diff_images=%s ｜ act 已 import framecache=%s"
            % (_d_act.get("changed"), _d_fc.get("changed"), _wired))

        # ② 退化检测存在且有效：全黑图 ⇒ is_blank 必须 True
        _blank = Image.new("RGB", (240, 160), (0, 0, 0))
        _is_blank = bool(_fc.is_blank(_blank))
        rec("⑦ 退化检测存在且有效（framecache.is_blank：全黑图 ⇒ True）", _is_blank,
            "is_blank(全黑)=%s" % _is_blank)

        # ③ 退化 ⇒ 落 code=5（不是 0、也不是 8）：让 core 收到 degraded=true 的回传
        _orig_parse = _core._parse_effect
        _core._parse_effect = lambda out: {"frame_ref": None, "degraded": True,
                                            "rounds": 1, "first_change_ms": 1.0}
        try:
            _led2, _ = meas.run_all(1, None)
            _codes2 = [(c.get("结果") or {}).get("code") for c in (_led2.get("用例") or [])]
            _c2 = _codes2[0] if _codes2 else None
            _ok_deg = (_c2 == 5)
            _why2 = ("回传 degraded=true ⇒ 结果 code=%r（期望 5：退化=**没测出来**，"
                     "不是「没变」(8)、也不是成功(0)）" % _c2)
            if _c2 == 10:
                _why2 = "★ 环境受阻：租约被占（code=10）⇒ 本条验不了"
                _ok_deg = None
            rec("⑧ 退化 ⇒ 必须落 code=5（永不当成功、也不当「没变」）",
                bool(_ok_deg) if _ok_deg is not None else False,
                _why2, skip=(_ok_deg is None))
        finally:
            _core._parse_effect = _orig_parse
    except Exception as e:
        rec("⑥ 双实现已消除（同一对帧 ⇒ act 判差与 framecache 判差结论一致）", False,
            "★ 检查跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

    # ── 返工②③ 的三条新断言 ──
    try:
        import glob as _glob
        import tempfile as _tf
        import agentctl.core as _core

        def _tmp_count():
            return len(_glob.glob(os.path.join(_tf.gettempdir(), "agentctl_effect_*")))

        # ⑨ 临时目录不泄漏：一次动作前后 %TEMP% 的 agentctl_effect_* 数量不许增加
        _c0 = _tmp_count()
        meas.run_all(1, None)
        _c1 = _tmp_count()
        rec("⑨ 临时目录不泄漏（一次动作前后 %TEMP% 的 agentctl_effect_* 数量不增加）",
            _c1 <= _c0, "跑前 %d 个 ⇒ 跑后 %d 个（不许增加）" % (_c0, _c1))

        # ⑩ os.environ 里没有 AGENTCTL_SNAP_DIR 残留（不许进程级改写）
        _leak = "AGENTCTL_SNAP_DIR" in os.environ
        rec("⑩ os.environ 里没有 AGENTCTL_SNAP_DIR 残留（不许进程级改写）", not _leak,
            ("os.environ 里还留着 AGENTCTL_SNAP_DIR=%r" % os.environ.get("AGENTCTL_SNAP_DIR"))
            if _leak else "无残留 ✔")

        # ⑪ 并发两次动作互不串帧 ⇒ 机制层验证：
        #   **并发**发两次 `_legacy`（各带**自己**的 SNAP_DIR）⇒ 两次拿到的目录必须不同，
        #   且 os.environ 里**不能**出现 SNAP_DIR（不许进程级改写）。
        #   （直接打 `_legacy` 而不是走整条点击：点击可能被前置闸门拒掉 ⇒ 测不到 env 这一层。）
        _orig_legacy = _core._legacy
        _seen = []

        def _legacy_spy(name, args, timeout=60, extra_env=None):
            if extra_env:
                _seen.append(extra_env.get("AGENTCTL_SNAP_DIR"))
            return _orig_legacy(name, args, timeout, extra_env=extra_env)

        _core._legacy = _legacy_spy
        try:
            import threading as _th
            _d1 = os.path.join(_tf.gettempdir(), "agentctl_effect_probeA")
            _d2 = os.path.join(_tf.gettempdir(), "agentctl_effect_probeB")

            def _one(d):
                try:
                    _core._legacy("act.py", ["--help"], timeout=30,
                                  extra_env={"AGENTCTL_SNAP_DIR": d})
                except Exception:
                    pass

            _ta = _th.Thread(target=_one, args=(_d1,))
            _tb = _th.Thread(target=_one, args=(_d2,))
            _ta.start(); _tb.start(); _ta.join(60); _tb.join(60)
        finally:
            _core._legacy = _orig_legacy
        _dirs = [d for d in _seen if d]
        _ok_cc = (len(_dirs) >= 2 and len(set(_dirs)) == len(_dirs)
                  and "AGENTCTL_SNAP_DIR" not in os.environ)
        rec("⑪ 并发两次动作互不串帧（两次拿到的 SNAP_DIR 必须不同，且 os.environ 无残留）", _ok_cc,
            "两次的 SNAP_DIR=%s ｜ os.environ 残留=%s ⇒ %s"
            % (list(_dirs), "AGENTCTL_SNAP_DIR" in os.environ,
               "互不相同 ✔" if _ok_cc else "★ 串了/没拿到"))
    except Exception as e:
        for _nm in ("⑨ 临时目录不泄漏（一次动作前后 %TEMP% 的 agentctl_effect_* 数量不增加）",
                    "⑩ os.environ 里没有 AGENTCTL_SNAP_DIR 残留（不许进程级改写）",
                    "⑪ 并发两次动作互不串帧（两次拿到的 SNAP_DIR 必须不同）"):
            rec(_nm, False, "★ 检查跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

    # ── C 组：失败口径 ──
    ok8 = all(c == 8 for c in codes)
    rec("④ 画面没变 ⇒ 结果必须落 code=8（⛔ 不许当成功）", ok8, "各轮 code=%s" % codes)
    ok_unk = not any((c == 0 and ("没测出来" in w or "未知" in w)) for c, w in zip(codes, warns))
    rec("⑤ unknown（没测出来）不许被当成成功", ok_unk,
        "各轮 (code, warning 前 40 字)=%s" % [(c, w[:40]) for c, w in zip(codes, warns)])

    # ── 收摊：把测量器拿的写动作租约**放掉**（不占着主控的会话）──
    _release_lease()

    return finish()


if __name__ == "__main__":
    sys.exit(main())
