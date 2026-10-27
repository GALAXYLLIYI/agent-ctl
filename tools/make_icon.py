# -*- coding: utf-8 -*-
"""tools/make_icon.py —— 原图 → 多尺寸 ICO + PNG 打包。

用法：
  python tools/make_icon.py [原图路径] [输出目录]
默认：
  原图 = work/_长任务_20261027/图标候选/selected.png
  输出 = assets/
生成：
  assets/agentctl.ico      （16/24/32/48/64/128/256 七档单文件）
  assets/agentctl_64.png   （Tk iconphoto 用）
"""
from __future__ import annotations

import os
import sys

from PIL import Image

SIZES = (16, 24, 32, 48, 64, 128, 256)


def center_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def make_icon(src: str, out_dir: str) -> dict:
    img = Image.open(src).convert("RGBA")
    img = center_square(img)

    frames = []
    for s in SIZES:
        if s == 256:
            resample = Image.LANCZOS
        else:
            resample = Image.NEAREST if s <= 32 else Image.LANCZOS
        frames.append(img.resize((s, s), resample))

    os.makedirs(out_dir, exist_ok=True)
    ico_path = os.path.join(out_dir, "agentctl.ico")
    png_path = os.path.join(out_dir, "agentctl_64.png")

    with open(ico_path, "wb") as f:
        frames[-1].save(f, format="ICO", sizes=[(s, s) for s in SIZES],
                        append_images=frames[:-1])

    frames[SIZES.index(64)].save(png_path, format="PNG")

    return {"ico": ico_path, "png": png_path, "sizes": list(SIZES)}


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    default_src = os.path.join(repo, "work", "_长任务_20261027",
                               "图标候选", "selected.png")
    default_out = os.path.join(repo, "assets")

    src = argv[1] if len(argv) > 1 else default_src
    out_dir = argv[2] if len(argv) > 2 else default_out

    if not os.path.isfile(src):
        sys.stderr.write("原图不存在: %s\n" % src)
        return 1

    r = make_icon(src, out_dir)
    print("ICO : %s  sizes=%s" % (r["ico"], r["sizes"]))
    print("PNG : %s" % r["png"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
