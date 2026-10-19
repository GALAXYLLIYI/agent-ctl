# -*- coding: utf-8 -*-
r"""tests\_通行证不封顶_判据.py —— 第 37 条：**次数上限可以取消，但老语义不许被顺手改掉**。

背景（用户 2026-10-19 定）："通行证一个任务一次签，取消点击次数限制；我看到问题会急停。"
所以：
  · acts_limit ≤ 0 或未写 ⇒ **不封顶**（放行，理由要写明"急停兜底"）
  · acts_limit > 0     ⇒ **老语义不变**（用尽即拒）
  · 有效期过期 / 范围不符 / 已被消费 ⇒ 仍然拒（安全边界一条都没松）

做法：直接打桩 `legacy_tools\gate.py` 的账本读取（`_load_checked`），
      **不伪造签发、不碰真账本、不需要通行证** —— 纯逻辑判据，谁跑都一样。
`--inject`：把"不封顶"分支打成不生效（当 limit≤0 时仍按 20 卡）⇒ ① 必然失败 ⇒ 判据红。

退出码：0 绿 / 1 红 / 2 验不了（legacy gate.py 读不到）
"""
import importlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "legacy_tools"))

GREEN, RED, SKIP = [], [], []
INJECT = False


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def finish():
    print("\n" + "=" * 60, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】次数上限语义不符合 2026-10-19 口径", flush=True)
        return 1
    print("结论：【绿】acts_limit≤0 不封顶、>0 老语义不变、过期/范围/已用仍拒", flush=True)
    return 0


def main():
    try:
        gate = importlib.import_module("gate")          # legacy_tools\gate.py
    except Exception as e:
        rec("legacy gate.py 可导入", False, "%s ⇒ 验不了" % e, skip=True)
        return finish()

    # ★ 打桩点：check() 读的是 `_load_checked(PASSFILE) -> (obj, 错因)`
    orig = gate._load_checked
    future = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 3600))

    def stub(p):
        gate._load_checked = lambda *_a, **_k: (dict(p), "")

    def reset():
        gate._load_checked = orig

    base = {"scope": "B级 点击滚动", "expires": future,
            "expires_at": time.time() + 3600, "acts": 999}

    try:
        # ① 不封顶
        stub({**base, "acts_limit": 0})
        if INJECT:
            # 注入：把"不封顶"打成不生效（模拟有人把分支改回"按 20 卡"）
            stub({**base, "acts_limit": 20})
        ok, why = gate.check("B级 点击滚动")
        rec("① acts_limit=0 ⇒ 不封顶放行", ok is True, "ok=%r why=%s" % (ok, str(why)[:60]))

        # ② 老语义：上限 20 + 已用 999 ⇒ 必拒
        reset(); stub({**base, "acts_limit": 20})
        ok2, why2 = gate.check("B级 点击滚动")
        rec("② acts_limit=20 且已用 999 ⇒ 用尽即拒（老语义不变）",
            ok2 is False and "次数已用尽" in str(why2), "ok=%r why=%s" % (ok2, str(why2)[:60]))

        # ③ 不封顶不等于放行一切：过期仍拒
        reset(); stub({"scope": "B级 点击滚动", "expires": future,
                       "expires_at": time.time() - 10, "acts": 0, "acts_limit": 0})
        ok3, why3 = gate.check("B级 点击滚动")
        rec("③ 不封顶但已过期 ⇒ 仍然拒", ok3 is False and "过期" in str(why3),
            "ok=%r why=%s" % (ok3, str(why3)[:60]))

        # ④ 不封顶不等于放行一切：范围不符仍拒
        reset(); stub({**base, "acts_limit": 0, "scopes": ["C级 打字快捷键"]})
        ok4, why4 = gate.check("B级 点击滚动")
        rec("④ 不封顶但范围不符 ⇒ 仍然拒", ok4 is False and "范围不符" in str(why4),
            "ok=%r why=%s" % (ok4, str(why4)[:60]))
    finally:
        reset()
    return finish()


if __name__ == "__main__":
    INJECT = "--inject" in sys.argv
    sys.exit(main())
