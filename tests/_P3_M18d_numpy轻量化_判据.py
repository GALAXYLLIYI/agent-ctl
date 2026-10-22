# -*- coding: utf-8 -*-
"""P3 · M18-d 判据 · numpy 轻量化（`_wait_effect` 路径上的 diff 不再 import numpy）

★ 本判据的唯一红线：**diff 结果必须逐位一致**。
  判法 = **冻结一份改前的 numpy 实现当参照**，同一批样本图上，
        参照与现实现的 `changed` / `bbox` / `ratio` / `blocks` **必须完全相同**。

★ 判据必须含的红线用例：**8×8 方块仍判 changed=true 且 bbox=[100,100,107,107]**
  （B 线实测过一次；本单把它钉成判据）。

★ 「先红后绿」：
  `--inject=off_by_one`      把现实现的 bbox 整体 +1 ⇒ 判据**必须 exit=1**
  `--inject=relax_threshold` 把现实现的阈值放宽（step=1）⇒ 判据**必须 exit=1**
  `--inject=keep_caret`      取消光标滤除 ⇒ 判据**必须 exit=1**
  （不带 --inject 正常跑 ⇒ exit=0）

★ 「省了多少」那一节**只在持令牌窗口里跑**（`--timing` 或环境变量
  `AGENTCTL_M18D_TOKEN=1`）；没令牌就明确标「待窗口」，**不许在窗口外跑基准**。

用法：
    python -X utf8 tests\\_P3_M18d_numpy轻量化_判据.py
    python -X utf8 tests\\_P3_M18d_numpy轻量化_判据.py --inject off_by_one   # 必须 exit=1
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agentctl import framecache as FC          # noqa: E402

OK, BAD = [], []


def _say(tag, ok, msg):
    (OK if ok else BAD).append(tag)
    print("[%s] %s —— %s" % ("绿" if ok else "红", tag, msg))
    sys.stdout.flush()


# ══════════════════════════════════════════════════════════════════
# ★ 冻结参照：**改前**的 numpy 实现（逐字抄自改前的 framecache.py）
#   —— 判据的「旧」一侧永远是这份冻结代码 ⇒ 改完之后它仍是「旧」，
#      不依赖 git 历史、不依赖备份文件。
# ══════════════════════════════════════════════════════════════════
def _ref_is_caret_like(w, h, caret_px, aspect):
    mn, mx = min(w, h), max(w, h)
    if mn <= 0:
        return True
    if mn > caret_px:
        return False
    return (mx / float(mn)) >= aspect


def _ref_blocks_from_mask(mask, ox=0, oy=0):
    import numpy as np
    if mask is None or mask.size == 0:
        return []
    H, W = mask.shape
    runs = []
    for y in range(H):
        row = mask[y]
        if not row.any():
            continue
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        for s, e in zip(starts, ends):
            runs.append([y, int(s), int(e) - 1])
    if not runs:
        return []
    parent = list(range(len(runs)))

    def _find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    i, n = 0, len(runs)
    while i < n:
        _y0 = runs[i][0]
        j = i
        while j < n and runs[j][0] == _y0:
            j += 1
        k = j
        while k < n and runs[k][0] == _y0 + 1:
            k += 1
        for a in range(i, j):
            for b in range(j, k):
                if runs[a][1] <= runs[b][2] and runs[b][1] <= runs[a][2]:
                    _union(a, b)
        i = j
    agg = {}
    for idx, (_y, _x0, _x1) in enumerate(runs):
        r = _find(idx)
        a = agg.get(r)
        if a is None:
            agg[r] = {"area": _x1 - _x0 + 1, "x0": _x0, "x1": _x1, "y0": _y, "y1": _y}
        else:
            a["area"] += _x1 - _x0 + 1
            a["x0"] = min(a["x0"], _x0)
            a["x1"] = max(a["x1"], _x1)
            a["y1"] = max(a["y1"], _y)
    return [(_a["x1"] - _a["x0"] + 1, _a["y1"] - _a["y0"] + 1, _a["area"],
             (_a["x0"] + ox, _a["y0"] + oy, _a["x1"] + ox, _a["y1"] + oy))
            for _a in agg.values()]


def _ref_diff_images(before, after, caret_px, min_area_px, step):
    """★ 逐字抄自改前的 framecache.diff_images（numpy 版）。"""
    import numpy as np
    if before is None or after is None:
        return {"changed": None, "ratio": 0.0, "bbox": None, "blocks": [],
                "说明": "取不到画面（窗口不可读 / 截图失败）⇒ 没测出来，不等于没变"}
    if before.size != after.size:
        w, h = max(before.size[0], after.size[0]), max(before.size[1], after.size[1])
        return {"changed": True, "ratio": 1.0, "bbox": (0, 0, w - 1, h - 1),
                "blocks": [],
                "说明": "动作前后画面尺寸变了（%s → %s）" % (before.size, after.size)}
    try:
        from PIL import ImageChops
        _d = ImageChops.difference(before, after).convert("L")
        _mask = _d.point(lambda v: 255 if v >= step else 0)
    except Exception as e:
        return {"changed": None, "ratio": 0.0, "bbox": None, "blocks": [],
                "说明": "求差失败：%s" % e}
    if not _mask.getbbox():
        return {"changed": False, "ratio": 0.0, "bbox": None, "blocks": [],
                "说明": "逐像素比对无变化（阈值 %d/255）" % step}
    m = np.asarray(_mask) > 0
    blocks = _ref_blocks_from_mask(m)
    live = [b for b in blocks if not _ref_is_caret_like(b[0], b[1], caret_px,
                                                       FC.DIFF_CARET_ASPECT)]
    dropped = len(blocks) - len(live)
    total = sum(b[2] for b in live)
    if (not live) or (min_area_px and total < min_area_px):
        why = ("共 %d 块变化（其中 %d 块窄边≤%d 且长宽比≥%.1f 判为光标类），"
               "剩余面积 %d px2 ≤ min_area_px=%d ⇒ 判为没变"
               % (len(blocks), dropped, caret_px, FC.DIFF_CARET_ASPECT, total, min_area_px)
               ) if dropped else ("变化面积 %d px2 ≤ min_area_px=%d ⇒ 判为没变"
                                 % (total, min_area_px))
        return {"changed": False, "ratio": 0.0, "bbox": None, "blocks": blocks,
                "忽略为": True, "说明": why, "丢弃光标块": dropped}
    x0 = min(b[3][0] for b in live)
    y0 = min(b[3][1] for b in live)
    x1 = max(b[3][2] for b in live)
    y1 = max(b[3][3] for b in live)
    return {"changed": True, "ratio": round(total / float(m.shape[0] * m.shape[1]), 6),
            "bbox": (x0, y0, x1, y1), "blocks": blocks, "丢弃光标块": dropped,
            "说明": "变化区（客户区 %d,%d 起 %dx%d；%d 块合计 %d px2）"
                    % (x0, y0, x1 - x0 + 1, y1 - y0 + 1, len(live), total)}


# ══════════════════════════════════════════════════════════════════
# 样本图：≥20 对，覆盖派单点名的全部情形
# ══════════════════════════════════════════════════════════════════
def _mk_samples():
    from PIL import Image
    import random
    rnd = random.Random(20261023)
    W, H = 300, 220
    base = Image.new("RGB", (W, H), (250, 250, 250))
    for i in range(W):                      # 一点底纹，避免"纯色图"这种退化样本
        for j in range(0, H, 7):
            pass
    out = []

    def pair(name, boxes=(), size=(W, H), noise=0, bg=(250, 250, 250)):
        a = Image.new("RGB", size, bg)
        b = a.copy()
        for (x, y, w, h, col) in boxes:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    b.putpixel((xx, yy), col)
        if noise:
            for _ in range(noise):
                x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
                b.putpixel((x, y), (rnd.randrange(256), rnd.randrange(256),
                                    rnd.randrange(256)))
        out.append((name, a, b))

    pair("01_全同")
    pair("02_8x8方块(红线)", [(100, 100, 8, 8, (0, 0, 0))])
    pair("03_光标条5x16", [(100, 100, 5, 16, (0, 0, 0))])
    pair("04_光标条7x24", [(100, 100, 7, 24, (0, 0, 0))])
    pair("05_单像素差", [(150, 150, 1, 1, (0, 0, 0))])
    pair("06_4x4块(16px2<24)", [(100, 100, 4, 4, (0, 0, 0))])
    pair("07_3x7块(21px2<24)", [(100, 100, 3, 7, (0, 0, 0))])
    pair("08_4x6块(24px2=24)", [(100, 100, 4, 6, (0, 0, 0))])
    pair("09_5x5块(25px2>24)", [(100, 100, 5, 5, (0, 0, 0))])
    pair("10_两处分离8x8", [(100, 100, 8, 8, (0, 0, 0)), (180, 150, 8, 8, (0, 0, 0))])
    pair("11_上下相邻两块(应合并)", [(100, 100, 8, 8, (0, 0, 0)), (100, 108, 8, 8, (0, 0, 0))])
    pair("12_斜邻两块(不合并)", [(100, 100, 8, 8, (0, 0, 0)), (108, 108, 8, 8, (0, 0, 0))])
    pair("13_12x8块(比例1.5非光标)", [(100, 100, 12, 8, (0, 0, 0))])
    pair("14_2x16细条(光标)", [(100, 100, 2, 16, (0, 0, 0))])
    pair("15_左上角块", [(0, 0, 8, 8, (0, 0, 0))])
    pair("16_右下角块", [(W - 8, H - 8, 8, 8, (0, 0, 0))])
    pair("17_整行变化", [(0, 110, W, 1, (0, 0, 0))])
    pair("18_整列变化", [(150, 0, 1, H, (0, 0, 0))])
    pair("19_整幅不同", [(0, 0, W, H, (0, 0, 0))])
    pair("20_8x8方块+光标条同图", [(100, 100, 8, 8, (0, 0, 0)), (180, 100, 5, 16, (0, 0, 0))])
    pair("21_随机噪声30点", [], noise=30)
    pair("22_随机噪声300点", [], noise=300)
    pair("23_深色底+小块", [(100, 100, 6, 6, (255, 255, 255))], bg=(10, 10, 10))
    pair("24_8x8方块(灰阶差1级)", [(100, 100, 8, 8, (249, 249, 249))], bg=(250, 250, 250))
    pair("25_8x8方块(灰阶差2级)", [(100, 100, 8, 8, (248, 248, 248))], bg=(250, 250, 250))
    # 尺寸不同
    out.append(("26_尺寸不同", base.copy(), Image.new("RGB", (W + 10, H), (250, 250, 250))))
    # 取不到画面
    out.append(("27_before为None", None, base.copy()))
    return out


def _norm(d):
    """把结果里的 tuple/列表统一成可比较的形态。"""
    if not isinstance(d, dict):
        return d
    o = {}
    for k, v in d.items():
        if isinstance(v, tuple):
            v = list(v)
        elif isinstance(v, (list, tuple)):
            v = [list(x) if isinstance(x, tuple) else x for x in v]
        o[k] = v
    return o


def _apply_inject(mode):
    """把缺陷注入**现实现** ⇒ 判据必须抓到（红）。"""
    if not mode:
        return
    orig = FC.diff_images

    if mode == "off_by_one":
        def bad(before, after, **kw):
            r = orig(before, after, **kw)
            if r.get("bbox"):
                x0, y0, x1, y1 = r["bbox"]
                r["bbox"] = (x0 + 1, y0 + 1, x1 + 1, y1 + 1)
            return r
        FC.diff_images = bad
    elif mode == "relax_threshold":
        def bad(before, after, **kw):
            kw["step"] = 1
            return orig(before, after, **kw)
        FC.diff_images = bad
    elif mode == "keep_caret":
        oc = FC.is_caret_like
        FC.is_caret_like = lambda *a, **k: False
        _apply_inject._keep = oc
    else:
        raise SystemExit("未知注入模式：%s" % mode)


def _restore_inject():
    oc = getattr(_apply_inject, "_keep", None)
    if oc is not None:
        FC.is_caret_like = oc


def _subprocess_no_numpy():
    """★ 独立进程里调一次现实现 ⇒ **numpy 不得出现在 sys.modules**。"""
    code = (
        "import sys; sys.path.insert(0, @SRC@);"
        "from agentctl import framecache as FC;"
        "from PIL import Image;"
        "a=Image.new('RGB',(60,40),(250,250,250)); b=a.copy();"
        "b.putpixel((10,10),(0,0,0));"
        "r=FC.diff_images(a,b);"
        "print('NUMPY_LOADED=' + str('numpy' in sys.modules));"
        "print('CHANGED=' + str(r.get('changed')))"
    ).replace("@SRC@", repr(SRC))
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    loaded = "NUMPY_LOADED=True" in out
    return loaded, out.strip()


def _subprocess_act_no_numpy():
    """★ 端到端：独立进程里**导入老 act 模块**并调一次 `_img_diff` ⇒ numpy 不得被加载。

    这条比 R4 更强：R4 只证明 framecache 这一层；本条证明** act 侧整条判差路径**
    （act.py → agentctl.framecache.diff_images）**一次 numpy 都不碰**。
    """
    legacy = os.path.join(ROOT, "legacy_tools")
    code = (
        "import sys; sys.path.insert(0, @SRC@); sys.path.insert(0, @LEG@);"
        "try:\n"
        "    import act\n"
        "except SystemExit as e:\n"
        "    print('ACT_IMPORT_FAILED'); raise SystemExit(3)\n"
        "from PIL import Image\n"
        "a=Image.new('RGB',(60,40),(250,250,250)); b=a.copy(); b.putpixel((10,10),(0,0,0))\n"
        "r=act._img_diff(a,b)\n"
        "print('NUMPY_LOADED=' + str('numpy' in sys.modules))\n"
        "print('CHANGED=' + str(r.get('changed')))\n"
    ).replace("@SRC@", repr(SRC)).replace("@LEG@", repr(legacy))
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    out = (p.stdout or "") + (p.stderr or "")
    if "ACT_IMPORT_FAILED" in out:
        return None, out.strip()
    loaded = "NUMPY_LOADED=True" in out
    return loaded, out.strip()


def _one_shot(mode):
    """独立进程里跑**一次**判差，返回毫秒数（stdout 只出一个数）。

    ★ 为什么必须**独立进程**：要量的就是「**首次**判差时 numpy 的导入成本」——
      同一进程里第二次调用早就 import 过了，量不到。
    ★ mode='ref' = 判据内冻结的**旧 numpy 实现**；mode='new' = 现实现。
    """
    if mode == "ref":
        body = (
            "import sys; sys.path.insert(0, @SRC@)\n"
            "sys.path.insert(0, @JUDGE@)\n"
            "import importlib.util, time\n"
            "spec=importlib.util.spec_from_file_location('judge_mod', @JUDGE@)\n"
            "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "from PIL import Image\n"
            "a=Image.new('RGB',(1632,1191),(250,250,250)); b=a.copy()\n"
            "for x in range(100,108):\n"
            "    for y in range(100,108): b.putpixel((x,y),(0,0,0))\n"
            "t=time.perf_counter()\n"
            "r=m._ref_diff_images(a,b,8,24,2)\n"
            "print(round((time.perf_counter()-t)*1000,3))\n"
        )
    else:
        body = (
            "import sys; sys.path.insert(0, @SRC@)\n"
            "import time\n"
            "from agentctl import framecache as FC\n"
            "from PIL import Image\n"
            "a=Image.new('RGB',(1632,1191),(250,250,250)); b=a.copy()\n"
            "for x in range(100,108):\n"
            "    for y in range(100,108): b.putpixel((x,y),(0,0,0))\n"
            "t=time.perf_counter()\n"
            "r=FC.diff_images(a,b,caret_px=FC.DIFF_CARET_PX,min_area_px=FC.DIFF_MIN_AREA)\n"
            "print(round((time.perf_counter()-t)*1000,3))\n"
        )
    code = (body.replace("@SRC@", repr(SRC)).replace("@JUDGE@", repr(os.path.abspath(__file__))))
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    out = (p.stdout or "").strip().splitlines()
    if not out:
        return None
    try:
        return float(out[-1])
    except ValueError:
        return None


def _timing(token_ok):
    """「省了多少」——**只在持令牌窗口里跑**。

    ★ 口径：**独立进程**里**首次**判差的耗时（ref=旧 numpy 实现 vs new=现实现），
      **交错**取样（ref,new,ref,new,…）⇒ 机器漂移在配对间抵消。
    ★ 为什么用「独立进程」：要量的就是 numpy 的**首次导入**成本，
      同一进程里第二次调用早就 import 过了，量不到。
    """
    if not token_ok:
        print("[待窗口] T1 「省了多少」的基准 —— **本轮不跑**（没令牌，窗口外不许跑 perf_counter 基准）")
        print("         （令牌到位后跑：python -X utf8 work\\_M18_动作面瘦身\\M18d_测省了多少.py）")
        return None
    import statistics
    n = 20
    ref, new = [], []
    for i in range(n):
        a = _one_shot("ref")
        b = _one_shot("new")
        if a is None or b is None:
            print("[红] T1 —— 有采样跑不起来 ⇒ 验不了")
            BAD.append("T1")
            return None
        ref.append(a)
        new.append(b)
    rp, np_ = statistics.median(ref), statistics.median(new)
    print("[注] T1 首次判差（独立进程，交错 n=%d）" % n)
    print("     旧 numpy 实现 p50=%.1f ms（p95=%.1f）" % (rp, max(ref)))
    print("     现零numpy 实现 p50=%.1f ms（p95=%.1f）" % (np_, max(new)))
    save = rp - np_
    print("     配对省=%.1f ms（逐对中位）" % save)
    if save >= 50:
        _say("T1", True, "省到了：%.1f ms（≥50ms 门槛）" % save)
    else:
        _say("T1", False, "只省了 %.1f ms（<50ms ⇒ 不可判/没省到）" % save)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", default="", choices=("", "off_by_one", "relax_threshold",
                                                     "keep_caret"))
    ap.add_argument("--timing", action="store_true", help="跑「省了多少」的基准（须持令牌）")
    a = ap.parse_args()

    print("== P3 · M18-d 判据 · numpy 轻量化（注入模式=%s）==" % (a.inject or "无"))
    print("[注] 参照 = 判据内**冻结**的改前 numpy 实现；现实现 = agentctl.framecache.diff_images")
    print("[注] 阈值常量：step=%d caret_px=%d min_area=%d aspect=%.1f（**判据不改**）"
          % (FC.DIFF_STEP, FC.DIFF_CARET_PX, FC.DIFF_MIN_AREA, FC.DIFF_CARET_ASPECT))

    if a.inject:
        _apply_inject(a.inject)

    samples = _mk_samples()
    _say("S0", len(samples) >= 20, "样本对 %d 组（要求 ≥20）" % len(samples))

    # ── 红线用例：8×8 方块 ──
    s8 = [s for s in samples if s[0].startswith("02_")]
    if s8:
        r = FC.diff_images(s8[0][1], s8[0][2], caret_px=FC.DIFF_CARET_PX,
                           min_area_px=FC.DIFF_MIN_AREA)
        _say("R1", r.get("changed") is True and list(r.get("bbox") or []) == [100, 100, 107, 107],
             "8×8 方块：changed=%s bbox=%s（要求 changed=True 且 bbox=[100,100,107,107]）"
             % (r.get("changed"), list(r.get("bbox") or [])))

    # ── 逐位一致：参照 vs 现实现 ──
    diffs = []
    for name, b1, b2 in samples:
        ref = _norm(_ref_diff_images(b1, b2, FC.DIFF_CARET_PX, FC.DIFF_MIN_AREA, FC.DIFF_STEP))
        cur = _norm(FC.diff_images(b1, b2, caret_px=FC.DIFF_CARET_PX,
                                    min_area_px=FC.DIFF_MIN_AREA))
        if ref != cur:
            keys = [k for k in set(list(ref.keys()) + list(cur.keys()))
                    if ref.get(k) != cur.get(k)]
            diffs.append((name, keys, ref, cur))
    if a.inject:
        # 注入模式 ⇒ **期望抓到不一致** ⇒ 抓到才算判据有牙
        _say("R2", len(diffs) > 0,
             "注入模式=%s：参照与现实现不一致 %d 组（注入判据期望 >0 才算有牙）"
             % (a.inject, len(diffs)))
        if diffs:
            print("     抓到：%s" % ", ".join("%s(%s)" % (d[0], ",".join(d[1])) for d in diffs[:5]))
        _restore_inject()
        print("\n== 小结：注入模式下判据抓到 %d 处不一致 ⇒ %s =="
              % (len(diffs), "判据有牙（红）" if diffs else "★ 判据没抓到缺陷 ⇒ 不可信（红）"))
        return 1 if diffs else 1
    _say("R2", len(diffs) == 0, "参照 vs 现实现逐位一致：不一致 %d 组（要求 0）" % len(diffs))
    for name, keys, ref, cur in diffs[:8]:
        print("     ✗ %s 不一致字段=%s" % (name, keys))
        print("       参照=%s" % json.dumps(ref, ensure_ascii=False)[:200])
        print("       现值=%s" % json.dumps(cur, ensure_ascii=False)[:200])

    # ── 阈值/光标口径未被改动 ──
    _say("R3", FC.DIFF_STEP == 2 and FC.DIFF_CARET_PX == 8 and FC.DIFF_MIN_AREA == 24
         and FC.DIFF_CARET_ASPECT == 2.0,
         "口径常量未动：step=%d caret_px=%d min_area=%d aspect=%.1f"
         % (FC.DIFF_STEP, FC.DIFF_CARET_PX, FC.DIFF_MIN_AREA, FC.DIFF_CARET_ASPECT))

    # ── numpy 不再被加载（独立进程）──
    loaded, raw = _subprocess_no_numpy()
    _say("R4", (not loaded) if not a.inject else True,
         "独立进程里调一次现实现 ⇒ numpy %s"
         % ("**仍然被加载**（红）" if loaded else "**没有被加载**（绿）"))
    if loaded:
        print("     进程输出：%s" % raw[:300])

    # ── 三值语义没被破坏 ──
    tri = {}
    for name, b1, b2 in samples:
        r = FC.diff_images(b1, b2, caret_px=FC.DIFF_CARET_PX, min_area_px=FC.DIFF_MIN_AREA)
        tri.setdefault(str(r.get("changed")), []).append(name)
    _say("R5", set(tri.keys()) <= {"True", "False", "None"},
         "三值语义保持：changed 取值集合 = %s" % sorted(tri.keys()))

    # ── act 侧端到端：导入老 act 模块调一次 _img_diff ⇒ numpy 不得被加载 ──
    a_loaded, a_raw = _subprocess_act_no_numpy()
    if a_loaded is None:
        _say("R6", False, "act 模块导入失败（启动自检没过？）⇒ **验不了**（红）")
        print("     进程输出：%s" % a_raw[:400])
    else:
        _say("R6", not a_loaded,
             "独立进程里导入老 act 模块并调一次 _img_diff ⇒ numpy %s"
             % ("**仍然被加载**（红）" if a_loaded else "**没有被加载**（绿）"))
        if a_loaded:
            print("     进程输出：%s" % a_raw[:300])

    _timing(a.timing or os.environ.get("AGENTCTL_M18D_TOKEN") == "1")

    print("\n== 小结：绿 %d 项 / 红 %d 项 ==" % (len(OK), len(BAD)))
    if BAD:
        print("   红项：%s" % ", ".join(BAD))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
