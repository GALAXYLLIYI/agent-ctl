# -*- coding: utf-8 -*-
r"""clip.py —— 自研 MJPEG-AVI 出片器（**纯标准库，不依赖 ffmpeg**）

为什么自己写：本机**没有 ffmpeg**（实测），而 PyAV 要 28.2 MB；而 MJPEG-AVI 的容器格式很朴素，
写出来 Windows 播放器/VLC 都能直接放。详细设计 §8.1 / §16.4。

产出三样（缺一不可）：
  1. `clip.avi`        —— RIFF('AVI ') + LIST('hdrl') + LIST('movi') + 'idx1' 索引
  2. `contact_sheet.png` —— 联络表：一眼看完整段（带帧号与时间码）
  3. `manifest.json`   —— 帧清单（帧号/时间码/源文件/sha256），**可拿去做独立核验**

★ 诚实边界：自动化测试只能证明"结构合法、能被独立解析器读回、抽帧与源图逐像素一致、Windows 认它是视频"。
  **"真人用播放器看着流畅"这件事，机器证明不了** —— 那条要人看一眼，不许含糊成"已验"。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

AVIF_HASINDEX = 0x00000010
AVIIF_KEYFRAME = 0x00000010
MJPEG_FOURCC = b"MJPG"
VIDS = b"vids"


def _pad(n: int) -> int:
    """RIFF chunk 必须按偶数字节对齐。"""
    return n + (n & 1)


def sha256_file(p: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def write_mjpeg_avi(out_path: str, frames: list[str], fps: int = 8,
                    width: int = 0, height: int = 0) -> dict:
    """把一串 JPEG 写成 MJPEG-AVI。

    frames: JPEG 文件路径列表（顺序即播放顺序）
    返回：{"bytes", "frames", "duration_sec", "fps", "width", "height", "sha256"}
    """
    if not frames:
        raise ValueError("没有帧")
    blobs = [open(p, "rb").read() for p in frames]
    if not width or not height:
        from PIL import Image
        with Image.open(frames[0]) as im:
            width, height = im.size
    n = len(blobs)
    usec_per_frame = int(round(1_000_000 / max(1, fps)))
    max_frame = max(len(b) for b in blobs)

    # ── hdrl ──
    avih = struct.pack("<IIIIIIIIII",
                       usec_per_frame,          # dwMicroSecPerFrame
                       max_frame * fps,         # dwMaxBytesPerSec
                       0,                       # dwPaddingGranularity
                       AVIF_HASINDEX,           # dwFlags
                       n,                       # dwTotalFrames
                       0,                       # dwInitialFrames
                       1,                       # dwStreams
                       max_frame,               # dwSuggestedBufferSize
                       width, height)           # dwWidth, dwHeight
    avih += b"\x00" * 16                          # dwReserved[4]

    # ⚠️ rcFrame 是 RECT = 4 个 **int16**，不是 int32；写成 iiii 会 pack 失败（验收首轮就栽在这）
    strh = struct.pack("<4s4sIHHIIIIIIIIhhhh",
                       VIDS, MJPEG_FOURCC,
                       0,                       # dwFlags
                       0, 0,                    # wPriority, wLanguage
                       0,                       # dwInitialFrames
                       1, fps,                  # dwScale, dwRate  → fps
                       0, n,                    # dwStart, dwLength
                       max_frame,               # dwSuggestedBufferSize
                       0xFFFFFFFF,              # dwQuality (未知)
                       0,                       # dwSampleSize
                       0, 0, width, height)     # rcFrame (RECT: left, top, right, bottom)
    strf = struct.pack("<IiiHH4sIiiII",
                       40, width, height, 1, 24, MJPEG_FOURCC,
                       width * height * 3, 0, 0, 0, 0)

    strl = b"LIST" + struct.pack("<I", 4 + (8 + len(strh)) + (8 + len(strf))) + b"strl"
    strl += b"strh" + struct.pack("<I", len(strh)) + strh
    strl += b"strf" + struct.pack("<I", len(strf)) + strf

    hdrl_body = b"avih" + struct.pack("<I", len(avih)) + avih + strl
    hdrl = b"LIST" + struct.pack("<I", 4 + len(hdrl_body)) + b"hdrl" + hdrl_body

    # ── movi（先记每个 00dc 相对 movi 数据的偏移，供 idx1 用）──
    movi_entries = []
    movi_data = b""
    for b in blobs:
        # idx1 的 dwChunkOffset 约定：相对 'movi' LIST 里 'movi' 四字之后的位置
        off = len(movi_data) + 4          # +4 = 跳过 'movi' 这个 fourcc 本身
        movi_entries.append((off, len(b)))
        movi_data += b"00dc" + struct.pack("<I", len(b)) + b
        if len(b) & 1:
            movi_data += b"\x00"
    movi = b"LIST" + struct.pack("<I", 4 + len(movi_data)) + b"movi" + movi_data

    idx = b""
    for off, ln in movi_entries:
        idx += b"00dc" + struct.pack("<III", AVIIF_KEYFRAME, off, ln)
    idx1 = b"idx1" + struct.pack("<I", len(idx)) + idx

    body = b"AVI " + hdrl + movi + idx1
    riff = b"RIFF" + struct.pack("<I", len(body)) + body
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(riff)

    return {"path": out_path, "bytes": len(riff), "frames": n, "fps": fps,
            "duration_sec": round(n / fps, 4), "width": width, "height": height,
            "sha256": sha256_file(out_path)}


# ────────────────────────── 独立解析器（测试用；与写入器不共享代码）──────────────────────────
def parse_avi(path: str) -> dict:
    """**只按 RIFF 规范读**，不复用写入器的任何逻辑 —— 这样才叫"换个方法复核"。"""
    data = open(path, "rb").read()
    if data[:4] != b"RIFF" or data[8:12] != b"AVI ":
        raise ValueError("不是 RIFF/AVI")
    riff_size = struct.unpack("<I", data[4:8])[0]
    out = {"riff_size": riff_size, "file_size": len(data), "frames": [], "avih": {}, "strh": {}}
    pos = 12
    movi_start = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csz = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = pos + 8
        if cid == b"LIST":
            ltype = data[body:body + 4]
            if ltype == b"movi":
                movi_start = body
                # 逐个 00dc
                p2 = body + 4
                end = body + csz
                while p2 + 8 <= end:
                    c2 = data[p2:p2 + 4]
                    s2 = struct.unpack("<I", data[p2 + 4:p2 + 8])[0]
                    if c2 == b"00dc":
                        out["frames"].append({"off": p2 - movi_start - 4, "size": s2,
                                              "jpeg_magic": data[p2 + 8:p2 + 10] == b"\xff\xd8"})
                        p2 += 8 + _pad(s2)
                    else:
                        p2 += 8 + _pad(s2)
        elif cid == b"avih":
            v = struct.unpack("<IIIIIIIIII", data[body:body + 40])
            out["avih"] = {"usec_per_frame": v[0], "total_frames": v[4],
                           "streams": v[6], "width": v[8], "height": v[9]}
        elif cid == b"idx1":
            out["idx1_entries"] = csz // 16
        pos = body + _pad(csz)
    # ⚠️ avih 与 strh 都**嵌在 LIST('hdrl') 里面**，顶层循环扫不到（验收首轮就栽在这）。
    #    这里按 fourcc 直接定位 —— 简单粗暴，但对"复核结构"这个目的足够且可靠。
    i = data.find(b"avih")
    if i > 0:
        v = struct.unpack("<IIIIIIIIII", data[i + 8:i + 48])
        out["avih"] = {"usec_per_frame": v[0], "total_frames": v[4],
                       "streams": v[6], "width": v[8], "height": v[9]}
    # strh 同理
    i = data.find(b"strh")
    if i > 0:
        ln = struct.unpack("<I", data[i + 4:i + 8])[0]
        s = data[i + 8:i + 8 + ln]
        fcc, hdl = s[0:4], s[4:8]
        scale, rate, length = struct.unpack("<III", s[20:32])
        out["strh"] = {"fccType": fcc.decode("latin1"), "fccHandler": hdl.decode("latin1"),
                       "scale": scale, "rate": rate, "length": length,
                       "fps": (rate / scale) if scale else None}
    return out


# ────────────────────────── 联络表 & manifest ──────────────────────────
def contact_sheet(out_path: str, frames: list[str], *, cols: int = 4,
                  thumb_w: int = 320, labels: list[str] | None = None) -> dict:
    from PIL import Image, ImageDraw
    ims = []
    for p in frames:
        with Image.open(p) as im:
            im = im.convert("RGB")
            h = int(im.height * thumb_w / im.width)
            ims.append(im.resize((thumb_w, h), Image.BILINEAR))
    if not ims:
        raise ValueError("没有帧")
    tw, th = ims[0].size
    rows = (len(ims) + cols - 1) // cols
    pad, top = 6, 16
    W = cols * tw + (cols + 1) * pad
    H = rows * (th + top) + (rows + 1) * pad
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, im in enumerate(ims):
        r, c = divmod(i, cols)
        x = pad + c * (tw + pad)
        y = pad + r * (th + top + pad)
        sheet.paste(im, (x, y + top))
        lab = (labels[i] if labels and i < len(labels) else f"#{i}")
        d.text((x + 2, y + 2), lab[:48], fill=(230, 230, 230))
    sheet.save(out_path)
    return {"path": out_path, "cols": cols, "rows": rows,
            "thumb": [tw, th], "sheet": [W, H], "count": len(ims),
            "sha256": sha256_file(out_path)}


def build_clip(out_dir: str, items: list[dict], *, fps: int = 8,
               title: str = "clip") -> dict:
    """items: [{"path": 帧文件, "frame_id": int|None, "t_wall": str|None}, ...]

    产出 clip.avi + contact_sheet.png + manifest.json，返回三者摘要。
    """
    os.makedirs(out_dir, exist_ok=True)
    frames = [it["path"] for it in items]
    labels = [f"#{it.get('frame_id') or i} {(it.get('t_wall') or '')[-12:]}" for i, it in enumerate(items)]
    avi = write_mjpeg_avi(os.path.join(out_dir, f"{title}.avi"), frames, fps=fps)
    sheet = contact_sheet(os.path.join(out_dir, f"{title}_contact_sheet.png"), frames, labels=labels)
    man = {
        "schema_version": 1, "title": title, "fps": fps,
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "frame_count": len(items),
        "duration_sec": round(len(items) / fps, 4),
        "clip": {"file": os.path.basename(avi["path"]), "bytes": avi["bytes"], "sha256": avi["sha256"]},
        "contact_sheet": {"file": os.path.basename(sheet["path"]), "sha256": sheet["sha256"]},
        "frames": [{"i": i, "frame_id": it.get("frame_id"), "t_wall": it.get("t_wall"),
                    "src": os.path.basename(it["path"]), "sha256": sha256_file(it["path"]),
                    "bytes": os.path.getsize(it["path"])}
                   for i, it in enumerate(items)],
    }
    mp = os.path.join(out_dir, f"{title}_manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    man["manifest_path"] = mp
    return {"avi": avi, "sheet": sheet, "manifest": man, "manifest_path": mp}
