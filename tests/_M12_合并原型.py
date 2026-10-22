# -*- coding: utf-8 -*-
r"""M12 最小原型：**实测**「只等一次」到底省多少 ms（**不改 src/**）

做法（**同一次实验**里）：
  ① act 侧等一遍：`act._wait_effect(hwnd, base)`（画面**不变**场景 ⇒ 等满 800ms 预算）
  ② core 侧等一遍：`framecache.wait_change(hwnd, before, …)`（**同一个窗口、同一段时间**）
  ③ 「只等一次」的合并版 = **只有 ①**（core 复用 act 的结论，不再等第二遍）
  ⇒ 省 = ②（core 那一遍的实测耗时）

★ 全程只 monkeypatch / 只读，**不改 src/、不改 contract/、不改 legacy_tools/**。
★ 目标窗口 = 本脚本自己开的记事本（**不碰用户正在用的窗口**）。
★ 跑完即退；不常驻。
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import framecache                       # noqa: E402
import _M11_分段测量 as meas                         # noqa: E402


def main() -> int:
    out = {"开始": time.strftime("%Y-%m-%d %H:%M:%S")}
    print("=" * 74, flush=True)
    print("M12 合并原型：实测「只等一次」省多少 ms", flush=True)
    print("=" * 74, flush=True)

    hwnd, proc = meas.open_own_notepad()
    out["记事本hwnd"] = int(hwnd)
    try:
        out["置前方式"] = meas.bring_to_front(hwnd)
        legacy_home = os.environ.get("AGENTCTL_LEGACY_HOME") or ""
        import agentctl.core as core_mod
        legacy_home = legacy_home or getattr(core_mod, "LEGACY", "") or ""
        sys.path.insert(0, legacy_home)
        import act as legacy_act

        # ── ① act 侧等一遍（画面不变 ⇒ 等满预算）──
        base = legacy_act._poll_base(int(hwnd))
        t0 = time.perf_counter()
        eff = legacy_act._wait_effect(int(hwnd), base)
        act_ms = (time.perf_counter() - t0) * 1000
        print("① act 侧 _wait_effect      : %8.1f ms  （changed=%s 轮次=%s）"
              % (act_ms, eff.get("changed"), eff.get("轮次")), flush=True)

        # ── ② core 侧等一遍（同一个窗口、同一段画面）──
        before = framecache.capture_client(int(hwnd))
        t0 = time.perf_counter()
        chg = framecache.wait_change(int(hwnd), before,
                                      budget_ms=framecache.WAIT_BUDGET_MS,
                                      poll_ms=framecache.WAIT_POLL_MS)
        core_ms = (time.perf_counter() - t0) * 1000
        print("② core 侧 wait_change     : %8.1f ms  （changed=%s 轮次=%s）"
              % (core_ms, chg.get("changed"), chg.get("轮次")), flush=True)

        # ── ③ 合并版 = 只等一次 ──
        merged_ms = act_ms
        saved = core_ms
        print("③ 合并版（只等一次）        : %8.1f ms  ⇒ **省 %.1f ms**"
              % (merged_ms, saved), flush=True)

        out.update({
            "act_wait_effect_ms": round(act_ms, 1),
            "act_轮次": eff.get("轮次"), "act_changed": eff.get("changed"),
            "core_wait_change_ms": round(core_ms, 1),
            "core_轮次": chg.get("轮次"), "core_changed": chg.get("changed"),
            "合并版_ms": round(merged_ms, 1),
            "省_ms": round(saved, 1),
            "省pct_相对两遍之和": round(saved / (act_ms + core_ms) * 100, 1),
        })
    finally:
        try:
            proc.kill()
        except Exception:
            pass
    out["结束"] = time.strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + json.dumps(out, ensure_ascii=False, indent=2), flush=True)
    p = os.path.join(ROOT, "work", "_M12_原型输出.json")
    try:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("落盘：%s" % p, flush=True)
    except OSError as e:
        print("（落盘失败：%s）" % e, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
