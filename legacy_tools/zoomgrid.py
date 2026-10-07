# -*- coding: utf-8 -*-
"""zoomgrid.py —— 坐标标定工具：**放大裁剪 + 画 x/y 刻度 + 画十字**，一次看清"目标在图内哪儿"

为什么必须固化成工具（2026-09-19 血泪）：
  今晚我在**同一个坑上摔了 4 次** —— 靠肉眼看缩略图估坐标，偏差 100~170px：
    · 证件号码输入框 → 点到了"人员状态"那一行
    · 左侧"综合所得申报" → 实际点成"非居民所得申报"（读成 y=589，真值 465）
    · "添加"按钮 → 点空（信息横幅变两行把按钮顶上去了）
  **唯一救回的办法每次都是同一个：裁剪 + 放大 + 画刻度 + 看清楚。**
  既然如此，就别再每次临时写脚本了 —— 做成工具。

用法：
  # 看整张图（超大图会自动缩到合适倍率）
  python zoomgrid.py 图.png

  # 只看某个区域，放大 4 倍，并在图内坐标 (1220,528) 画红十字
  python zoomgrid.py 图.png --box 950,420,1400,620 --scale 4 --cross 1220,528

  # 一次画多个十字（对比几个候选点）
  python zoomgrid.py 图.png --box 0,300,340,820 --cross 100,465 --cross 100,615

输出里会打印 **图内坐标 → 屏幕坐标** 的换算（给 --origin 时）。
"""
import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image, ImageDraw

MAX_OUT = 1600          # 输出图最长边上限（避免再被下游缩得看不清）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--box", help="裁剪区 x0,y0,x1,y1（图内坐标）")
    ap.add_argument("--scale", type=float, default=0,
                    help="放大倍数；不填则自动（把裁剪区放大到约 %d 像素）" % MAX_OUT)
    ap.add_argument("--cross", action="append", default=[],
                    help="画红十字的图内坐标 x,y（可重复）")
    ap.add_argument("--origin", help="窗口左上角的屏幕坐标 x,y —— 给了就打印屏幕坐标换算")
    ap.add_argument("--out", help="输出文件；默认在原图旁生成 *_zoom.png")
    ap.add_argument("--step", type=int, default=0, help="刻度间隔（图内像素）；默认自动")
    a = ap.parse_args()

    im = Image.open(a.image).convert("RGB")
    W, H = im.size
    if a.box:
        x0, y0, x1, y1 = [int(v) for v in a.box.split(",")]
    else:
        x0, y0, x1, y1 = 0, 0, W, H
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x1), min(H, y1)
    cw, ch = x1 - x0, y1 - y0
    if cw <= 0 or ch <= 0:
        print("!! 裁剪区无效")
        return 2

    scale = a.scale if a.scale > 0 else max(1.0, MAX_OUT / float(max(cw, ch)))
    c = im.crop((x0, y0, x1, y1)).resize((int(cw * scale), int(ch * scale)), Image.LANCZOS)
    d = ImageDraw.Draw(c)

    step = a.step or max(10, int(round(25 / scale)) * 5)
    for gy in range(y0 - y0 % step, y1 + 1, step):
        y = (gy - y0) * scale
        if 0 <= y < c.height:
            major = (gy % (step * 4) == 0)
            col = (220, 0, 0) if major else (90, 150, 220)
            d.line([(0, y), (16 if major else 10, y)], fill=col, width=2)
            if major:
                d.text((20, y - 6), str(gy), fill=(220, 0, 0))
    for gx in range(x0 - x0 % step, x1 + 1, step):
        x = (gx - x0) * scale
        if 0 <= x < c.width:
            major = (gx % (step * 4) == 0)
            col = (0, 150, 0) if major else (150, 200, 150)
            d.line([(x, 0), (x, 16 if major else 10)], fill=col, width=2)
            if major:
                d.text((x + 3, 18), str(gx), fill=(0, 120, 0))

    origin = None
    if a.origin:
        origin = tuple(int(v) for v in a.origin.split(","))

    for spec in a.cross:
        cx, cy = [int(v) for v in spec.split(",")]
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            px = (cx - x0) * scale
            py = (cy - y0) * scale
            d.line([(px - 60, py), (px + 60, py)], fill=(255, 0, 0), width=3)
            d.line([(px, py - 60), (px, py + 60)], fill=(255, 0, 0), width=3)
            msg = "红十字 = 图内(%d,%d)" % (cx, cy)
            if origin:
                msg += " → 屏幕(%d,%d)" % (origin[0] + cx, origin[1] + cy)
            print(msg)
        else:
            print("⚠ 十字 (%d,%d) 不在裁剪区内，没画" % (cx, cy))

    out = a.out or os.path.splitext(a.image)[0] + "_zoom.png"
    c.save(out)
    print("源图 %dx%d   裁剪 (%d,%d)-(%d,%d)   放大 %.2f 倍   刻度 %dpx"
          % (W, H, x0, y0, x1, y1, scale, step))
    print("已存 %s  (%dx%d)" % (out, c.width, c.height))
    if origin:
        print("★ 换算：屏幕坐标 = 图内坐标 + (%d, %d)" % origin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
