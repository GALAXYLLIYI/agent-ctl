# -*- coding: utf-8 -*-
"""P4 判据：用**固定图夹具**堵掉定位器的盲区。

================================ 为什么有这条 ================================
legacy_tools\\selftest.py:95-98 **自己承认**的盲区（原话抄在此处，免得后人又绕回去）：

    if not r:
        # ★已知盲区：这里无法区分「这张图本来就没有选项」与「定位器彻底坏了」，
        #   两者都返回空。要真正覆盖，需要一张【已知含选项】的固定图当夹具。

本判据就是那张夹具 + 它缺的另一半（**确认无选项**的反向对照）。

================================ 设计 ================================
· 夹具由 tests\\fixtures\\_make_fixtures.py **确定性生成**（同脚本重跑 byte 相同）。
· 期望值**全部冻结**在下面（不每轮从定位器现算 —— 否则 f(x)==f(x) 恒真，等于没测）。
· 四条断言：① 正向（4 个、几何/填充率对得上） ② **反向（必须返回空）** ③ 确定性 ④ 夹具完整性（md5）。
· **能跑红**：把 P4_LOCATOR_DIR 指到一个"坏定位器"的目录上即可（见文件末尾的说明）。

用法：
    python -X utf8 tests\\_P4_定位器夹具判据.py
    # 红验证（坏定位器）：见 __main__ 末尾打印的命令
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
LEGACY = os.path.join(REPO, "legacy_tools")
# ★ 红验证用：把这个环境变量指到「故意改坏的定位器」所在目录（默认用真源）
LOCATOR_DIR = os.environ.get("P4_LOCATOR_DIR") or LEGACY

POS = os.path.join(FIX, "options_present.png")
NEG = os.path.join(FIX, "options_absent.png")

# ── 冻结快照（2026-10-15 用真定位器实测值，改夹具/改定位器都必须同步这里）──
FROZEN_MD5 = {
    "options_present.png": "5986b35abeefa130",   # 只比前 16 位（够用且好读）
    "options_absent.png": "22c7b53be178e9de",
}
FROZEN_N = 4
FROZEN_ROWS = [
    # (cy, cx, bh, bw, ratio, fill)  —— ratio=0.000 是因为夹具里环内是纯背景色（无中心圆点）
    (200, 600, 19, 19, 0.0, 0.410),
    (260, 600, 19, 19, 0.0, 0.410),
    (320, 600, 19, 19, 0.0, 0.410),
    (380, 600, 19, 19, 0.0, 0.410),
]
TOL_YX = 3          # 中心坐标容差（px）
TOL_WH = 1          # 宽高容差（px）
TOL_FILL = 0.05     # 填充率容差

OK, BAD, SKIP = [], [], []


def section(t):
    print("\n" + "=" * 74 + "\n" + t + "\n" + "=" * 74, flush=True)


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("绿" if cond else "红", name,
                           ("  ← %s" % detail) if detail else ""), flush=True)
    return bool(cond)


def md5_16(p):
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:16]


def main():
    section("P4 · 定位器固定图夹具判据（堵 selftest.py:95-98 那个「返回空无法区分」的盲区）")

    # ── 装载定位器（可以指向别处 ⇒ 红验证）──
    if LOCATOR_DIR not in sys.path:
        sys.path.insert(0, LOCATOR_DIR)
    import option_locator
    print("  option_locator = %s" % option_locator.__file__, flush=True)
    from PIL import Image

    if not (os.path.exists(POS) and os.path.exists(NEG)):
        SKIP.append("夹具不存在（先跑 tests\\fixtures\\_make_fixtures.py 生成）")
        print("  ⚠ 夹具不在 ⇒ 验不了，登记 SKIP")
        return

    # ── ④ 夹具完整性（防"有人偷偷换图让判据变绿"）──
    for nm, want in FROZEN_MD5.items():
        got = md5_16(os.path.join(FIX, nm))
        check("4 · 夹具 %s 的内容与冻结快照一致（md5 %s）" % (nm, want),
              got == want, "实测 %s" % got)

    # ── ① 正向：已知含选项 ⇒ 必须找到，且几何/填充率对得上 ──
    r_pos = option_locator.find_options(Image.open(POS))
    n_ok = len(r_pos) == FROZEN_N
    check("1a · 正向图（已知含 4 个选项）必须找到 %d 个" % FROZEN_N,
          n_ok, "实测 %d 个" % len(r_pos))
    if n_ok:
        bad = []
        for i, (want, got) in enumerate(zip(FROZEN_ROWS, r_pos)):
            if abs(got[0] - want[0]) > TOL_YX or abs(got[1] - want[1]) > TOL_YX \
                    or abs(got[2] - want[2]) > TOL_WH or abs(got[3] - want[3]) > TOL_WH \
                    or abs(got[5] - want[5]) > TOL_FILL:
                bad.append("#%d want=%s got=%s" % (i, want, tuple(round(v, 3) for v in got)))
        check("1b · 正向图 4 个选项的几何/填充率都对得上冻结快照"
              "（cy±%d cx±%d 宽高±%d fill±%.2f）" % (TOL_YX, TOL_YX, TOL_WH, TOL_FILL),
              not bad, " ; ".join(bad) if bad else "4/4 命中")
        # 顺带把它抓的"选错组"也钉死：cx 必须都在 600（左边缘对齐那一列）
        check("1c · 正向图选中的是同一列（4 个 cx 全 = 600）",
              all(abs(g[1] - 600) <= TOL_YX for g in r_pos),
              "cx=%s" % [g[1] for g in r_pos])

    # ── ② 反向：**确认无选项** ⇒ 必须返回空（这条正是 selftest.py 缺的那半）──
    r_neg = option_locator.find_options(Image.open(NEG))
    check("2 · ★反向图（确认无选项）必须返回空 —— "
          "这条一旦不空，就说明「返回空」不再等于「图没选项」，selftest.py 的盲区会回来",
          not r_neg, "实测 %d 个" % len(r_neg))

    # ── ③ 确定性：同一张图两次结果一致 ──
    a = option_locator.find_options(Image.open(POS))
    b = option_locator.find_options(Image.open(POS))
    check("3 · 正向图连跑两次结果一致（定位必须是确定性的）", a == b)


if __name__ == "__main__":
    try:
        main()
    finally:
        section("汇总")
        print("  绿 %d 条 / 红 %d 条 / 验不了 %d 条" % (len(OK), len(BAD), len(SKIP)), flush=True)
        for s in SKIP:
            print("  ~ %s" % s)
        if BAD:
            print("  结论：【红】%d 条不成立" % len(BAD))
            for b in BAD:
                print("     - %s" % b)
            print("\n  ★ 红验证命令（把定位器换成「返回空」的坏版本）：")
            print("    ① 造坏定位器：mkdir /s，把 legacy_tools\\option_locator.py 复制过去，"
                  "把 find_options 改成开头 `return []`")
            print("    ② set P4_LOCATOR_DIR=F:\\...\\_坏定位器目录")
            print("    ③ 再跑本判据 ⇒ 1a/1b/1c/3 必红（2 反而变绿 ⇒ 说明反向那条单独看不够）")
            sys.exit(1)
        print("  结论：【绿】夹具正向/反向/确定性/完整性 全部成立")
        sys.exit(0)
