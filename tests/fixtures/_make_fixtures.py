# -*- coding: utf-8 -*-
"""P4 固定图夹具生成器 —— 生成两张**确定性**的图（同一脚本重跑 = 同样字节）。

为什么要它（原话，别改口径）：
    legacy_tools\\selftest.py:95-98 自认盲区 —— find_options() 返回空时，
    分不清「这张图本来就没有选项」与「定位器彻底坏了」，只能 skip。
    要真覆盖，需要一张【已知含选项】的固定图当夹具（外加一张**确认无选项**的反向对照）。

设计依据（照 legacy_tools\\option_locator.py 的判据反推，2026-10-15 实读）：
    · 灰度 <190 算"暗像素"；找**近方形**连通块，边长必须落在 [int(h*0.009), int(h*0.032)] 内
    · 同一题的选项**左边缘对齐**（按 x 聚类）、**纵向间距均匀**、间距/尺寸 ∈ [1.2, 4.2]
    · 组内宽高差 ≤1.12 倍（"同一个模子"）
    · **填充率 ≤0.92**（这一条把网格里的实心色块排除掉；空心环约 0.4）
    · 分组须 2–6 个成员；取 x > 0.28×宽 的最靠左那组

用法：
    python -X utf8 tests\\fixtures\\_make_fixtures.py          # 生成 + 自校准（用真定位器验一遍）
"""
import hashlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))            # agent-ctl
LEGACY = os.path.join(REPO, "legacy_tools")

W, H = 900, 800
BG = 255
INK = 40
R = 9                    # 环外半径 ⇒ bbox 18×18（尺寸窗 10–25 内）
THICK = 3
XS = 600                 # 环中心 x（> 0.28*900 = 252）
YS = (200, 260, 320, 380)   # 间距 60 ⇒ 60/18 = 3.33 ∈ [1.2, 4.2]


def _ring(dr, cx, cy, r=R, t=THICK, ink=INK, bg=BG):
    """画一个空心环（外接框 (2r+1)×(2r+1)，填充率 ≈ 0.39 ⇒ 远低于 0.92 阈值）。

    做法：先画**实心外圆**（墨色），再在中心画一个**背景色内圆** ⇒ 得到一个圆环。
    """
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ink)
    dr.ellipse([cx - r + t, cy - r + t, cx + r - t, cy + r - t], fill=bg)


def make_present():
    from PIL import Image, ImageDraw
    im = Image.new("L", (W, H), BG)
    dr = ImageDraw.Draw(im)
    for y in YS:
        _ring(dr, XS, y)
    # 左侧仿"题号网格"：一排**实心**方块（填充率≈1.0 ⇒ 会被定位器按填充率判据排除，
    # 用来验证"实心块不算选项"这条判据真的在起作用）
    for i in range(5):
        x0, y0 = 60, 150 + i * 90
        dr.rectangle([x0, y0, x0 + 40, y0 + 40], fill=INK)
    return im


def make_absent():
    """**确认无选项**：只画横向长条与超大实心块 —— 近方形且尺寸合规的块一个都没有。"""
    from PIL import Image, ImageDraw
    im = Image.new("L", (W, H), BG)
    dr = ImageDraw.Draw(im)
    for i in range(8):
        y = 120 + i * 70
        dr.rectangle([80, y, 820, y + 6], fill=INK)          # 长条：宽高比 0.008 ⇒ 被"近方形"挡掉
    dr.rectangle([300, 560, 700, 700], fill=INK)             # 巨型实心块：边长 400 ⇒ 超尺寸窗
    return im


def save(im, name):
    p = os.path.join(HERE, name)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=False)               # 确定性：不用随机、不写时间戳
    data = buf.getvalue()
    with open(p, "wb") as f:
        f.write(data)
    print("  写出 %-22s %6d B  md5=%s" % (name, len(data), hashlib.md5(data).hexdigest()[:16]))
    return p


if __name__ == "__main__":
    print("生成 P4 夹具（确定性）：")
    p1 = save(make_present(), "options_present.png")
    p2 = save(make_absent(), "options_absent.png")

    print("\n自校准（用**真定位器**验一遍，数值就是判据里要冻结的快照）：")
    if LEGACY not in sys.path:
        sys.path.insert(0, LEGACY)
    from PIL import Image
    import option_locator
    print("  option_locator = %s" % option_locator.__file__)
    for tag, p in (("present", p1), ("absent", p2)):
        r = option_locator.find_options(Image.open(p))
        print("  [%s] 找到 %d 个选项" % (tag, len(r)))
        for i, t in enumerate(r):
            print("        #%d  cy=%d cx=%d bh=%d bw=%d ratio=%.3f fill=%.3f"
                  % (i, t[0], t[1], t[2], t[3], t[4], t[5]))
