# -*- coding: utf-8 -*-
"""next_question.py —— 翻到下一题，**带变更检测与自动重试**

为什么需要（2026-09-19 实测）：
  「下一题」「上一题」的坐标点击**偶发失效**（各失败过一次），
  失败时 act.py 仍返回 0，看不出问题。靠"事后读图发现题号没变"代价太高。

判据（可复核）：**题干文字区域的像素哈希**。
  不同题目的题干不同 → 哈希必然不同。
  只哈希**题干区**（不含右上角倒计时，它每秒都在变）。
"""
import hashlib
import os
import re
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import win32gui  # noqa: E402
from PIL import Image  # noqa: E402

sys.path.insert(0, HERE)
import option_locator  # noqa: E402

# 题干区（窗口图内坐标）：避开右上角倒计时（x>900 且 y<80 那块）
QBOX = (255, 105, 1150, 275)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    return r.stdout.decode("utf-8", errors="replace") + r.stderr.decode("utf-8", errors="replace")


def shot(hwnd, why):
    out = run([PY, os.path.join(HERE, "perceive.py"), "--shot", "--hwnd", str(hwnd), "--why", why])
    m = re.search(r'"文件":\s*"([^"]+)"', out)
    if not m:
        return None
    return m.group(1).replace("\\\\", "\\")


def qhash(img_path):
    im = Image.open(img_path).convert("L")
    c = im.crop(QBOX)
    return hashlib.md5(c.tobytes()).hexdigest()[:12]


def find_buttons(img_path):
    """在底部条里找**蓝色按钮**，返回按 x 从左到右排序的中心点（图内坐标）。

    为什么不能硬编码（2026-09-19 实测）：
      选择题页底部是【上一题 下一题】两个按钮；
      判断题页多出【标记本题】→ 三个按钮 → **「下一题」整体左移约 168px**。
      硬编码 (2407,1287) 会点到【标记本题】上！
    判据：按钮是**纯蓝圆角块**，颜色显著偏蓝（B 通道远高于 R/G）。按列统计蓝色像素数，
          连续有蓝的列段就是一个按钮。
    """
    im = Image.open(img_path).convert("RGB")
    w, h = im.size
    y0, y1 = max(0, h - 100), min(h, h - 45)       # 只取按钮所在的那一条（图例色块在更下面，必须排除）
    px = im.load()
    cols = []
    for x in range(w):
        cnt = 0
        for y in range(y0, y1, 2):
            r, g, b = px[x, y]
            if b > 150 and (b - r) > 60 and (b - g) > 40:
                cnt += 1
        cols.append(cnt)
    thr = max(3, int(max(cols) * 0.35)) if max(cols) else 0
    if thr == 0:
        return []
    spans = []
    start = None
    for x, c in enumerate(cols):
        if c >= thr and start is None:
            start = x
        elif c < thr and start is not None:
            if x - start > 110:                     # 按钮是宽块（约 150px）；图例小方块会远窄于这个
                spans.append((start, x))
            start = None
    if start is not None and w - start > 110:
        spans.append((start, w))
    centers = [((a + b) // 2, (y0 + y1) // 2) for a, b in spans]
    return centers


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    hwnd = int(sys.argv[1])
    which = sys.argv[2]          # next / prev
    label = "下一题" if which == "next" else "上一题"

    run([PY, os.path.join(HERE, "act.py"), "focus", "--hwnd", str(hwnd)])
    p0 = shot(hwnd, "next_q 变更前")
    if not p0:
        print("!! 取图失败")
        return 3
    h0 = qhash(p0)
    bs = find_buttons(p0)
    print("变更前 题干哈希 = %s" % h0)
    print("底部蓝色按钮 %d 个：%s" % (len(bs), [b[0] for b in bs]))
    if len(bs) < 2:
        print("!! 底部按钮少于 2 个 —— 拒绝盲点，停手")
        return 5
    # 顺序恒为 [上一题, 下一题, (标记本题)]，所以「下一题」永远是第 2 个
    gx, gy = bs[1] if which == "next" else bs[0]
    L0, T0, _, _ = win32gui.GetWindowRect(hwnd)
    bx = "%d,%d" % (L0 + gx, T0 + gy)
    print("取第 %d 个按钮当「%s」→ 图内(%d,%d) 屏幕 %s" % (2 if which == "next" else 1, label, gx, gy, bx))

    for attempt in range(1, 4):
        run([PY, os.path.join(HERE, "act.py"), "focus", "--hwnd", str(hwnd)])
        run([PY, os.path.join(HERE, "act.py"), "click", "--hwnd", str(hwnd), "--xy", bx, "--coord"])
        time.sleep(1.0)
        p1 = shot(hwnd, "next_q 变更后")
        if not p1:
            continue
        h1 = qhash(p1)
        if h1 != h0:
            print("第 %d 次点击 → 题干已变化 ✅  新哈希 = %s" % (attempt, h1))
            L, T, R, B = win32gui.GetWindowRect(hwnd)
            # 2026-09-19 统一：改用 option_locator（自适应，不依赖窗口尺寸）。
            # 旧的 radio_finder 把选区写死在 x∈[540,600]、尺寸 22–26px，换个窗口大小就废。
            blobs = option_locator.find_options(Image.open(p1))
            print("窗口 rect=(%d,%d,%d,%d)  选项 %d 个：" % (L, T, R, B, len(blobs)))
            for n, (cy, cx, bh, bw, ratio, fill) in enumerate(blobs):
                nm = "ABCDEFGH"[n] if n < 8 else "?"
                print("   %s  图内y=%d → 屏幕y=%d   占比=%.2f 填充=%.2f %s"
                      % (nm, cy, T + int(cy), ratio, fill, "★已选中" if ratio > 0.45 else "未选中"))
            print("IMG=%s" % p1)
            return 0
        print("第 %d 次点击 → 题干没变，重试…" % attempt)

    print("!! 连点 3 次都没换页 —— 停手，交给用户看")
    return 4


if __name__ == "__main__":
    sys.exit(main())
