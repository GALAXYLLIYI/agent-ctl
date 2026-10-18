# -*- coding: utf-8 -*-
"""radio_finder.py —— 自动找出选择题的单选圆圈位置（代替"肉眼估缩略图"）

为什么需要它：
  2026-09-19 实测：靠模型看缩略图估坐标，**连续两次估偏**（一次压到「上一题」上，
  一次偏 67px）。缩略图比例 1/2.53，肉眼误差约一个按钮宽 —— 正好够点错。
  而单选圈是**规则几何图形**，可以用像素判别法**确定性地**找出来，不依赖人眼。

判据（不靠"看起来像"）：
  在给定竖条区域内，找**灰度明显低于背景**的连通块；
  宽高都在 10~30px 之间、且近似正方 → 判为圆圈。
  圆圈内部若也存在深色像素占比高的区域 → 判为**已选中**。

用法：
  python radio_finder.py <图片> --x0 540 --x1 600 [--y0 300 --y1 1300]
  输出每个圆圈的 图内中心坐标 + 是否已选中
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
from PIL import Image


def find_circles(img, x0, x1, y0, y1, dark=190):
    a = np.asarray(img.convert("L"), dtype=np.uint8)
    sub = a[y0:y1, x0:x1]
    mask = sub < dark

    # 连通块标记（4 邻域，纯 python 栈，避免依赖 scipy）
    h, w = mask.shape
    lab = np.zeros((h, w), dtype=np.int32)
    cur = 0
    blobs = []
    for sy in range(h):
        for sx in range(w):
            if mask[sy, sx] and lab[sy, sx] == 0:
                cur += 1
                stack = [(sy, sx)]
                lab[sy, sx] = cur
                pix = []
                while stack:
                    cy, cx = stack.pop()
                    pix.append((cy, cx))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and lab[ny, nx] == 0:
                            lab[ny, nx] = cur
                            stack.append((ny, nx))
                ys = [p[0] for p in pix]
                xs = [p[1] for p in pix]
                bh = max(ys) - min(ys) + 1
                bw = max(xs) - min(xs) + 1
                if 22 <= bh <= 26 and 22 <= bw <= 26 and 0.88 <= bh / float(bw) <= 1.14:
                    cyc = (min(ys) + max(ys)) / 2.0
                    cxc = (min(xs) + max(xs)) / 2.0
                    # ② x 窗口判据：本页选项圈恒在 x≈570（多次实测都是 570）。
                    #    2026-09-19 踩过：D 选项第二行「来源地」的文字笔画被误判成"第 5 个选项"，
                    #    若它排在某个真选项之前，就会把 A/B/C/D 的序号整体串位 —— 必须挡掉。
                    if not (564.0 <= cxc + x0 <= 576.0):
                        continue
                    # 判定是否选中：圆圈内部中心 9x9 区域的深色占比
                    iy, ix = int(cyc), int(cxc)
                    yy0, yy1 = max(0, iy - 5), min(h, iy + 6)
                    xx0, xx1 = max(0, ix - 5), min(w, ix + 6)
                    inner = mask[yy0:yy1, xx0:xx1]
                    ratio = float(inner.mean()) if inner.size else 0.0
                    blobs.append((cyc + y0, cxc + x0, bh, bw, ratio))
    blobs.sort(key=lambda b: b[0])
    return blobs


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    path = sys.argv[1]
    args = sys.argv[2:]
    kw = {"x0": 540, "x1": 600, "y0": 300, "y1": 1350}
    i = 0
    while i < len(args):
        k = args[i].lstrip("-")
        if k in kw:
            kw[k] = int(args[i + 1])
            i += 2
        else:
            i += 1

    im = Image.open(path)
    print("图: %s  尺寸 %s" % (path, im.size))
    print("搜区域: x[%d,%d] y[%d,%d]" % (kw["x0"], kw["x1"], kw["y0"], kw["y1"]))
    blobs = find_circles(im, kw["x0"], kw["x1"], kw["y0"], kw["y1"])
    print("找到 %d 个候选圆圈：" % len(blobs))
    for n, (cy, cx, bh, bw, ratio) in enumerate(blobs, 1):
        tag = "★已选中" if ratio > 0.45 else "未选中"
        print("   #%d  图内中心=(%d, %d)   尺寸=%dx%d   内部深色占比=%.2f  %s"
              % (n, int(round(cx)), int(round(cy)), bw, bh, ratio, tag))


if __name__ == "__main__":
    main()
