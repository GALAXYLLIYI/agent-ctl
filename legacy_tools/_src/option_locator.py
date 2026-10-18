# -*- coding: utf-8 -*-
"""option_locator.py —— **不依赖窗口尺寸**的选项定位（自适应）

为什么另写一个（2026-09-19 实测教训）：
  radio_finder 把选项圈写死在 x∈[540,600]、尺寸 22–26px —— 那是**某一具体窗口尺寸下**的标定。
  用户把页面放大/全屏后，这些常数立刻失效，**第一下就会点偏**。

自适应判据（不依赖绝对坐标，只依赖"选项排布"这一几何规律）：
  ① 选项圆圈/方框是**近方形小块**（宽高比 0.85–1.18）
  ② 尺寸按窗口高度**按比例**取值（默认 0.9%–3.2%×高度），不再写死像素
  ③ **同一题的选项左边缘对齐**（同一 x）→ 按 x 聚类
  ④ 同组内**纵向间距接近均匀**（最长间隔 / 最短间隔 ≤ 1.8）
  ⑤ 成员数 2–6（单选/多选 4–5 个，判断题 2 个）
  ⑥ 取**最靠右**的那一组 —— 因为题号网格在左边，选项在右边内容区

这几条合起来，既能随尺寸缩放，又能天然排除"题号网格里的方格列"和"底部图例的色块行"。
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
from PIL import Image


def _blobs(mask, min_px, max_px):
    """在 mask 上找近方形连通块。返回 [(cy, cx, bh, bw, ratio_inner)]（相对 mask 坐标）。"""
    h, w = mask.shape
    lab = np.zeros((h, w), dtype=np.int32)
    cur = 0
    out = []
    for sy in range(h):
        row = mask[sy]
        for sx in range(w):
            if row[sx] and lab[sy, sx] == 0:
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
                if min_px <= bw <= max_px and min_px <= bh <= max_px and 0.85 <= bh / float(bw) <= 1.18:
                    cyc = (min(ys) + max(ys)) / 2.0
                    cxc = (min(xs) + max(xs)) / 2.0
                    iy, ix = int(cyc), int(cxc)
                    r = max(2, bw // 4)
                    y0, y1 = max(0, iy - r), min(h, iy + r + 1)
                    x0, x1 = max(0, ix - r), min(w, ix + r + 1)
                    inner = mask[y0:y1, x0:x1]
                    ratio = float(inner.mean()) if inner.size else 0.0
                    # ★填充率：整个外接框里"暗像素"的占比。
                    #   实心色块（题号网格里已答的蓝格子）≈ 1.0；选项圈是**空心环**（约 0.13）
                    #   或**环+中心圆点**（约 0.4–0.6）。2026-09-19 实测：网格列尺寸一致、间距
                    #   均匀，能骗过前面所有判据，**只有填充率能把它和选项圈分开**。
                    fill = len(pix) / float(bh * bw)
                    out.append((cyc, cxc, bh, bw, ratio, fill))
    return out


def find_options(im, verbose=False):
    """返回 [(cy, cx, bh, bw, ratio)]，按 y 排序，坐标是**图像坐标**。找不到返回 []。"""
    a = np.asarray(im.convert("L"), dtype=np.uint8)
    h, w = a.shape
    y_top = int(h * 0.07)
    y_bot = int(h * 0.90)
    sub = a[y_top:y_bot, :]
    mask = sub < 190
    lo = max(10, int(h * 0.009))
    hi = max(lo + 4, int(h * 0.032))
    blobs = _blobs(mask, lo, hi)
    if verbose:
        print("  候选近方形块 %d 个（尺寸窗 %d–%d px）" % (len(blobs), lo, hi))
    if not blobs:
        return []

    # 按 x 聚类（容差 = 尺寸窗上限的一半）
    tol = max(3.0, hi * 0.45)
    blobs.sort(key=lambda b: b[1])
    groups = []
    for b in blobs:
        placed = False
        for g in groups:
            if abs(g["x"] - b[1]) <= tol:
                g["m"].append(b)
                g["x"] = sum(m[1] for m in g["m"]) / float(len(g["m"]))
                placed = True
                break
        if not placed:
            groups.append({"x": b[1], "m": [b]})

    best = None
    cands = []
    for g in groups:
        m = sorted(g["m"], key=lambda b: b[0])
        # 同一 x 上如果贴太近，说明是同一个东西被切碎 → 去重
        ded = []
        for b in m:
            if ded and abs(b[0] - ded[-1][0]) < hi * 0.6:
                continue
            ded.append(b)
        if not (2 <= len(ded) <= 6):
            continue
        # ★组内先剔除"尺寸离群"的成员：选项圈是同一个模子（尺寸相同），
        #   而**换行文字的小块会飘到同一个 x 附近混进来**，把后面的"尺寸一致"判据撑破，
        #   导致**真选项列整组被判死**（2026-09-19 实测：4 个真圈 + 1 个 18x22 混入 → 全组被丢）。
        #   做法：按尺寸分簇，取最大的那一簇留下来。
        from collections import Counter
        cnt = Counter((b[3], b[2]) for b in ded)
        modal = max(cnt.items(), key=lambda kv: kv[1])[0]
        ded = [b for b in ded if abs(b[3] - modal[0]) <= 2 and abs(b[2] - modal[1]) <= 2]
        if not (2 <= len(ded) <= 6):
            continue
        gaps = [ded[i + 1][0] - ded[i][0] for i in range(len(ded) - 1)]
        # 均匀度放宽到 2.5：**选项文字换行会让某一格的间距明显变大**（实测 52/54/86）
        if min(gaps) <= 0 or max(gaps) / min(gaps) > 2.5:
            continue
        # ★间距必须与"选项自身尺寸"成合理比例：同一题的选项间距 ≈ 2～4 倍圈径。
        #   2026-09-19 实测：左侧栏「一、单选题」这类汉字笔画会凑出 22x22、间距 400 的"伪组"
        #   —— 间距/尺寸 = 18 倍，用这一条一眼筛掉。
        if not all(1.2 <= gp / float(ded[i][2]) <= 4.2 for i, gp in enumerate(gaps)):
            continue
        # ★最强的判据：**同一题的选项是同一个模子量出来的** —— 组内宽高必须一致。
        #   文字笔画凑出来的"伪组"尺寸必有差异，这一条把它们整批筛掉。
        bws = [b[3] for b in ded]
        bhs = [b[2] for b in ded]
        if max(bws) / float(min(bws)) > 1.12 or max(bhs) / float(min(bhs)) > 1.12:
            continue
        # ★填充率判据：实心色块（题号网格里已答的格子）≈0.92~1.0，选项圈 ≤0.8
        if any(b[5] > 0.92 for b in ded):
            continue
        if verbose:
            print("  ✔候选组 x=%.0f 成员=%d 间距=%s 尺寸=%s"
                  % (g["x"], len(ded), [int(v) for v in gaps],
                     sorted(set((b[3], b[2]) for b in ded))))
        cands.append({"x": g["x"], "m": ded})

    if not cands:
        if verbose:
            print("  ✘ 没有任何组通过全部判据")
        return []
    # 排序：①成员最多 ②格子更大（选项圈比汉字笔画大）③更"空心"（环 vs 笔画）
    # 注意：**不能用"最靠右"** —— 实测右侧的题干文字会凑出更靠右的伪组。
    # 排除左侧「题号网格」面板：它在左边约 1/4 宽处，且未答格子是**白底方框**（填充率低、
    # 能骗过填充率判据）。选项圈永远在右侧内容区，所以用 x > 0.28×宽 把左面板整体排除。
    keep = [c for c in cands if c["x"] > 0.28 * w]
    if not keep:
        keep = cands
    # 排序：①**最靠左**（选项圈是每行的行首，文字笔画凑的伪组都在它右边）
    #       ②成员更多 ③格子更大 ④更空心
    best = max(keep, key=lambda c: (-c["x"], len(c["m"]),
                                    sorted(b[2] for b in c["m"])[len(c["m"]) // 2],
                                    -sum(b[5] for b in c["m"]) / len(c["m"])))
    if verbose:
        print("  ★选定 x=%.0f 成员=%d" % (best["x"], len(best["m"])))
    return [(b[0] + y_top, b[1], b[2], b[3], b[4], b[5]) for b in best["m"]]


if __name__ == "__main__":
    import glob
    import os
    _shots = os.environ.get("AGENTCTL_SHOTDIR") or os.path.join(
        os.path.expanduser("~"), "Documents", "agent-manual", "_监控", "shots")
    for p in sys.argv[1:] or sorted(glob.glob(os.path.join(_shots, "*_win2754900.png")))[-1:]:
        im = Image.open(p)
        print("=" * 66)
        print("图: %s  %s" % (os.path.basename(p), im.size))
        r = find_options(im, verbose=True)
        print("选定 %d 个选项：" % len(r))
        for n, (cy, cx, bh, bw, ratio, fill) in enumerate(r):
            nm = "ABCDEFGH"[n] if n < 8 else "?"
            print("   %s  图内(%d, %d)  %dx%d  内部占比=%.2f 填充率=%.2f %s"
                  % (nm, cx, cy, bw, bh, ratio, fill, "★已选中" if ratio > 0.45 else ""))
