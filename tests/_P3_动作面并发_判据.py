# -*- coding: utf-8 -*-
r"""动作面并发 · 判据（2026-10-25，第 61 条）

钉的病：`action_guard.observe()` 的「读计数 → +1 → 写回」不是原子操作，
而 `mcp_http` 是 ThreadingHTTPServer（每请求一线程）⇒ 并发会**丢更新**，
两级刹车对计数精度敏感（少计=该拉不拉，多计=误拉）。

本判据：8 线程 × 250 次失败动作（action_failed），断言计数**精确等于 2000**。

跑法：python -X utf8 tests\_P3_动作面并发_判据.py [--inject]
"""
import copy
import json
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import action_guard                          # noqa: E402

THREADS, PER = 8, 250


def _policy():
    src = os.path.join(ROOT, "src", "agentctl", "action_guard_policy.json")
    pol = json.loads(open(src, encoding="utf-8").read())
    # 阈值放大：本判据只想量“计数准不准”，不想真触发刹车
    pol.update({"enabled": True, "same_target_streak": 10 ** 6, "fail_streak": 10 ** 6,
                "rate_per_min": 10 ** 9, "hits_to_brake": 2, "blocked_event": False})
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    open(p, "w", encoding="utf-8").write(json.dumps(pol, ensure_ascii=False))
    return p


def main():
    inject = "--inject" in sys.argv
    actor = "conc-judge-%d" % os.getpid()
    polp = _policy()
    guard = action_guard.ActionGuard(emit=lambda payload: None, brake=lambda p: (True, "ok"), path=polp)

    def worker():
        for _ in range(PER):
            guard.observe(actor=actor, tool="ctl_click", args={"xy": [1, 2]},
                          ok=False, failure_class="action_failed")

    ts = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    got = ((action_guard._state["fail"].get(actor) or {}).get("n"))
    want = THREADS * PER
    os.remove(polp)

    fails = []

    def check(name, cond, detail=""):
        print(("  OK  " if cond else "  X   ") + name + (("  —— " + detail) if not cond else ""))
        if not cond:
            fails.append(name)

    if inject:
        # 注入：故意要求「少 1」（模拟丢更新）——判据必须红
        check("注入·期望 1999（丢一次更新，应当红）", got == want - 1,
              "actual=%r want=%r" % (got, want))
    else:
        check("并发 %d 线程 × %d 次后计数精确等于 %d" % (THREADS, PER, want),
              got == want, "actual=%r" % (got,))
        check("成功一次应清零", (guard.observe(actor=actor, tool="ctl_click",
                                             args={"xy": [1, 2]}, ok=True) is None
                                 or True))
        after = ((action_guard._state["fail"].get(actor) or {}).get("n"))
        check("清零后计数 == 0", after == 0, "actual=%r" % (after,))

    print()
    if fails:
        print("【红】%d 项失败：%s" % (len(fails), fails))
        raise SystemExit(1)
    print("【绿】并发计数无丢更新。")


if __name__ == "__main__":
    main()
