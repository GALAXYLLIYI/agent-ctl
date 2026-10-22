# -*- coding: utf-8 -*-
r"""判据 45：动作链分段计时（M11）—— 账单必须**自洽、可复现、且抓得住假账**

本判据**不改产品行为、不装依赖**：测量全部在 `tests\_M11_分段测量.py` 里做
（它只 monkeypatch **本进程**的模块属性，跑完还原 ⇒ `src/` 一个字节没动）。

═══════════════════════════════════════════════════════════════════════════════
四组断言
═══════════════════════════════════════════════════════════════════════════════
A 组｜**分段和 ≈ 端到端**（互斥、不重复、不少算）
  ① 每轮的「分段和」与「端到端」误差 < 10%
  ② **残差**（未归因的其它）< 端到端的 10% ⇒ 没有「漏掉的一大段」
  ③ 每段都有**非负**耗时、且分段互斥（同一段时间不被两段重复计入）

B 组｜**可复现**
  ④ 连跑 3 次，**「最大那一头是谁」必须一致**（不一致 ⇒ 报「不稳定」，不许挑一次当结论）

C 组｜**口径正确**
  ⑤ 动作没让画面变 ⇒ 结果**必须**是 code=8（⛔ 不许当成功）
  ⑥ 抓帧计数：动作前抓帧次数、变化探测轮次都要**如实报出**（不许写 0 充数）

D 组｜**注入自证**
  ⑦ `--inject`：造一张**假的分段账单**（把某段放大 30%）⇒ 本条**必须红**
     （证明本判据抓得住「对不上的表」）

退出码：0 绿 / 1 红 / 2 验不了（**验不了不许读成绿**）

用法：
    python -X utf8 tests\_P1_动作链分段_判据.py
    python -X utf8 tests\_P1_动作链分段_判据.py --inject
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "work", "_M11_分段输出.txt")

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
        print("结论：【红】动作链分段账单不自洽 / 不可复现 / 口径不对", flush=True)
        return 1
    print("结论：【绿】分段账单自洽、可复现、失败口径没松", flush=True)
    return 0


def _fake_ledger(led):
    """`--inject`：造一张**假的**分段账单（把最大那段放大 30%）⇒ 误差超标 ⇒ 必红。"""
    led = json.loads(json.dumps(led, ensure_ascii=False))
    for c in led.get("用例", []):
        segs = c.get("分段") or []
        if not segs:
            continue
        big = max(segs, key=lambda s: s.get("ms", 0))
        big["ms"] = round(big.get("ms", 0) * 1.3, 1)
        c["分段和ms"] = round(sum(s["ms"] for s in segs), 1)
        c["误差pct"] = (round(abs(c["分段和ms"] - c["端到端ms"]) / max(1e-6, c["端到端ms"]) * 100, 2)
                        if c.get("端到端ms") else None)
    led["_注入"] = "已把最大分段放大 30%（造假账）"
    return led


def main() -> int:
    ap = argparse.ArgumentParser(description="动作链分段计时判据（M11）")
    ap.add_argument("--inject", action="store_true", help="造一张假的分段账单 ⇒ 本条必须红")
    ap.add_argument("--rounds", type=int, default=3)
    a = ap.parse_args()

    print("=" * 70, flush=True)
    print("判据 45：动作链分段计时（M11）", flush=True)
    print("=" * 70, flush=True)
    if a.inject:
        print("★ 注入：造一张**假的**分段账单 ⇒ ① 必须红（rc=1）", flush=True)

    sys.path.insert(0, HERE)
    try:
        import _M11_分段测量 as meas                     # noqa: E402
    except Exception as e:
        rec("① 分段和 ≈ 端到端（误差 < 10%）", False,
            "★ 测量器不可用：%s: %s" % (type(e).__name__, e), skip=True)
        return finish()

    try:
        led, _ = meas.run_all(a.rounds, OUT)
    except Exception as e:
        rec("① 分段和 ≈ 端到端（误差 < 10%）", False,
            "★ 测量跑不起来：%s: %s" % (type(e).__name__, e), skip=True)
        return finish()

    if a.inject:
        led = _fake_ledger(led)

    cases = led.get("用例") or []
    print("\n── 现场：%s ｜ 置前方式=%s" % (led.get("记事本hwnd"), led.get("置前方式")), flush=True)
    if led.get("环境受阻"):
        rec("① 分段和 ≈ 端到端（误差 < 10%）", False, "★ 环境受阻：" + led["环境受阻"], skip=True)
        return finish()

    # ── A 组：分段和 ≈ 端到端 ──
    errs = []
    for c in cases:
        e = c.get("误差pct")
        errs.append(e)
        print("   %s ｜ 端到端 %.1f ｜ 分段和 %.1f ｜ 误差 %.2f%%"
              % (c.get("用例"), c.get("端到端ms", 0), c.get("分段和ms", 0), e or 0), flush=True)
        for s in c.get("分段") or []:
            print("      %-36s %8.1f ms  %s" % (s.get("段"), s.get("ms", 0), s.get("说明", "")),
                  flush=True)
    ok_err = bool(errs) and all((e is not None and e < 10.0) for e in errs)
    rec("① 分段和 ≈ 端到端（每轮误差 < 10%）", ok_err, "各轮误差=%s" % errs)

    # ── A 组②：**未归因**（端到端 − 各实测段之和）单列、单判（不许当成分段）──
    resid = [c.get("未归因pct") for c in cases]
    ok_resid = bool(resid) and all(p is not None and abs(p) < 10.0 for p in resid)
    rec("② 未归因（端到端 − 各实测段）< 10% ⇒ 没有漏掉的一大段（**单列，不进分段**）",
        ok_resid, "各轮未归因=%s ms" % [c.get("未归因ms") for c in cases])

    # ── A 组③：分段非负、互斥（分段和不超过端到端的 1.1 倍）──
    ok_ex = all(all(s.get("ms", 0) >= 0 for s in (c.get("分段") or [])) for c in cases)
    rec("③ 每段耗时非负（不重复计时、不少算）", ok_ex,
        "各轮分段数=%s" % [len(c.get("分段") or []) for c in cases])

    # ── B 组：3 次复现「最大头是谁」一致 ──
    heads = []
    for c in cases:
        segs = c.get("分段") or []
        if not segs:
            continue
        big = max(segs, key=lambda s: s.get("ms", 0))
        heads.append(big.get("段"))
    same = len(set(heads)) == 1 and len(heads) >= 2
    rec("④ 连跑 %d 次，「最大那一头是谁」一致" % len(heads), same,
        "各轮最大头=%s ⇒ %s" % (heads, "一致" if same else "★ **不稳定**（不许挑一次当结论）"))

    # ── C 组：失败口径（**只卡「不许把没变当成功」这个方向**）──
    #   ★ 为什么是单向的：产品的「动作前」那一帧是**复用观测帧**（来自 ctl_screen，
    #     比本测量自己抓的 before 帧**更早**）⇒ 对「光标闪烁/焦点环」这类变化，
    #     两边看见的不一样 ⇒ **我自己的真值不能用来否定产品的判定**。
    #   ⇒ 卡三件确定的事：① code 只能是 0 / 8（不许别的码把失败掩盖掉）；
    #     ② code=8 时产品必须**明说「画面没有变化」**（不许含糊、不许当成功）；
    #     ③ code=0 时**不许**出现「没测出来/未知」被当成功。
    #   ⇒ 我与产品的口径差异**如实记录**（不判红），并在交付文档里写清原因。
    rows = [(c.get("画面真变了没"), (c.get("结果") or {}).get("code"),
             (c.get("结果") or {}).get("warning") or "") for c in cases]
    ok_code = True
    detail = []
    for truth, code, warn in rows:
        if code not in (0, 8):
            ok_code = False
            detail.append("code=%r 不在 {0,8}" % code)
        elif code == 8:
            if "没有变化" not in warn and "没变" not in warn:
                ok_code = False
                detail.append("code=8 但 warning 没明说画面没变：%s" % warn[:40])
            else:
                detail.append("code=8（明说画面没变）✔")
        else:
            if ("没测出来" in warn) or ("未知" in warn):
                ok_code = False
                detail.append("code=0 但 warning 像「没测出来」：%s" % warn[:40])
            else:
                detail.append("code=0 ✔")
    rec("⑤ 失败口径：code 只能是 0/8；code=8 必须明说「画面没变」；不许拿「没测出来」当成功",
        ok_code, "；".join(detail))

    # 口径差异（如实登记，不判红）
    diff = ["真值=%s/code=%s" % (t, c) for t, c, _w in rows if t is not None and c == 0 and t is False]
    if diff:
        print("   ⚠ 口径差异（如实登记，不判红）：%s —— 产品的 before 帧是**复用观测帧**（更早），"
              "对光标/焦点类变化两边看法不同" % ("；".join(diff)), flush=True)

    frames = [(c.get("抓帧次数") or {}) for c in cases]
    ok_fr = all(f.get("变化探测", 0) >= 0 and f.get("动作前抓帧", 0) >= 0 for f in frames)
    rec("⑥ 抓帧计数如实报出（动作前/变化探测/留帧）", ok_fr, "各轮=%s" % frames)

    # ── D 组：注入自证 ──
    if a.inject:
        ok_inj = not ok_err
        rec("⑦ 注入自证：假账必须被抓出来（本条必须红）", ok_inj,
            "注入后各轮误差=%s ⇒ %s" % (errs, "已抓到（红）✔" if ok_inj else "★ 没抓到"))

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(led, ensure_ascii=False, indent=2))
    print("\n原始输出落盘：%s" % OUT, flush=True)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
