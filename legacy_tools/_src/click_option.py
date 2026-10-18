# -*- coding: utf-8 -*-
"""click_option.py —— 原子点选：**取图→找圈→点击→自检→不中就重试**

两个实测教训（2026-09-19）：
  ① **布局会漂移**："先定位、后点击"两步之间页面会移位（实测差 60px），4 次点击全打偏。
     ⇒ 必须把"取图→找圈→点击"压进同一次执行。
  ② **点击会偶发失效**：act.py 返回 0 但页面没变（"下一题"失败过，"选 B"也失败过）。
     ⇒ 必须点完立刻自检，**没点上就重试**，不能返回 0 就当成功。

用法：
  python click_option.py <hwnd> <选项字母，如 A / ABD> [--dry]
"""
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
import option_locator  # noqa: E402   ← 改用**不依赖窗口尺寸**的自适应定位

LETTERS = "ABCDEFGH"
MAXROUND = 3


def run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    return r.stdout.decode("utf-8", errors="replace") + r.stderr.decode("utf-8", errors="replace")


def grab(hwnd, why):
    out = run([PY, os.path.join(HERE, "perceive.py"), "--shot", "--hwnd", str(hwnd), "--why", why])
    m = re.search(r'"文件":\s*"([^"]+)"', out)
    if not m:
        return None, []
    p = m.group(1).replace("\\\\", "\\")
    blobs = option_locator.find_options(Image.open(p))
    return p, blobs


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    hwnd = int(sys.argv[1])
    letters = sys.argv[2].upper()
    dry = "--dry" in sys.argv
    want = [LETTERS.index(c) for c in letters if c in LETTERS]

    L, T, R, B = win32gui.GetWindowRect(hwnd)
    print("窗口 rect = (%d, %d, %d, %d)   目标 = %s" % (L, T, R, B, letters))

    done = set()
    for rnd in range(1, MAXROUND + 1):
        run([PY, os.path.join(HERE, "act.py"), "focus", "--hwnd", str(hwnd)])
        img, blobs = grab(hwnd, "click_option 取图 第%d轮" % rnd)
        if not blobs:
            print("!! 第 %d 轮取图/找圈失败" % rnd)
            return 3
        if max(want) >= len(blobs):
            print("!! 只找到 %d 个选项，少于请求的 %d" % (len(blobs), max(want) + 1))
            return 4

        print("第 %d 轮  图=%s  找到 %d 个选项" % (rnd, os.path.basename(img), len(blobs)))
        for idx in want:
            cy, cx, bh, bw, ratio, fill = blobs[idx]
            ch = LETTERS[idx]
            if ratio > 0.45:
                print("   %s → 已经是选中态，跳过" % ch)
                continue
            sx, sy = L + int(round(cx)), T + int(round(cy))
            print("   %s → 屏幕(%d,%d) 点击" % (ch, sx, sy))
            if dry:
                continue
            run([PY, os.path.join(HERE, "act.py"), "click", "--hwnd", str(hwnd),
                 "--xy", "%d,%d" % (sx, sy), "--coord"])
            time.sleep(0.35)

        if dry:
            print("（演练模式，没有点击）")
            return 0

        time.sleep(0.5)
        _, chk = grab(hwnd, "click_option 自检 第%d轮" % rnd)
        state = []
        allok = True
        for n, (cy, cx, bh, bw, ratio, fill) in enumerate(chk):
            nm = LETTERS[n] if n < len(LETTERS) else "?"
            sel = ratio > 0.45
            state.append("%s=%s" % (nm, "选" if sel else "-"))
            if n in want and not sel:
                allok = False
        print("   自检: " + " ".join(state))
        if allok:
            print("✅ 第 %d 轮全部到位" % rnd)
            return 0
        print("⚠ 第 %d 轮有选项没点上，重试…" % rnd)

    print("!! 连点 %d 轮仍没全部点上 —— 停手，交给用户看" % MAXROUND)
    return 5


if __name__ == "__main__":
    sys.exit(main())
