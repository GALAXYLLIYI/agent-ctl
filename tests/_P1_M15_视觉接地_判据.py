# -*- coding: utf-8 -*-
r"""判据 47：M15 视觉接地（OCR 词框 → 词 → 可点目标）

═══════════════════════════════════════════════════════════════════════════════
A 批：口径统一（物理像素） + 字框合并
═══════════════════════════════════════════════════════════════════════════════
A①  字框合并：把 Windows OCR 的**逐字**框合成**词**框
    · 同行（垂直中心差 ≤ 0.5×min(h)）+ 紧邻（水平 gap ≤ 0.5×min(h)）+ 同号（|Δh| ≤ 25%）
    · 按 x 排序贪心；**标点并入左词**；空格（gap > 0.5×h）断词
    · **尺度基准用 h 不用 w**（标点框 w 只有半字，用 w 会误断）
    · 词框 = 成员框并集；conf = min(成员 conf)
    ⇒ 断言：与 GT 的 **IoU ≥ 0.8**、**召回 100%**；「文 + 件」必须合成一框（gap=0、Δh=6%）
A②  口径一致：两条抓图路径（PrintWindow / ImageGrab 回退）**同一区域的词框坐标差 ≤ 1px**
    ★ 比的是**同一个词的框坐标**，不是只比图的尺寸（只比尺寸放过「1.5 倍」那个洞）
A③  声明字段：返回值必须带 `coord_space == "client_physical"`
A④  口径物理性（★ A 批修正后新增）：客户区尺寸/原点必须与 **uia_tree 的客户区屏幕矩形**
    （**另一套实现**）逐像素对上，且进程 DPI 感知级别 = 2（PER_MONITOR_AWARE）
    ⇒ 这条卡的是「无条件乘 dpi_scale ⇒ 感知进程里双乘 1.5」那个洞
A⑤  帧绑定原点：抓图期间窗口没动 ⇒ `画面稳定=True`（B 批对齐的前提）

═══════════════════════════════════════════════════════════════════════════════
退出码：0 绿 / 1 红 / 2 验不了（**验不了不许读成绿**）
用法：
    python -X utf8 tests\_P1_M15_视觉接地_判据.py
    python -X utf8 tests\_P1_M15_视觉接地_判据.py --inject   # 造假账 ⇒ 必须红
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

GREEN, RED, SKIP = [], [], []
INJECT = False


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def finish():
    print("\n" + "=" * 66, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】视觉接地的字框合并 / 口径统一没过", flush=True)
        return 1
    print("结论：【绿】字框已合并成词、口径已统一为客户区物理像素", flush=True)
    return 0


def _iou(a, b):
    """两个 (x,y,w,h) 的 IoU。"""
    ax0, ay0, ax1, ay1 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx0, by0, bx1, by1 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    ua = max(0, a["w"]) * max(0, a["h"]) + max(0, b["w"]) * max(0, b["h"]) - inter
    return (inter / ua) if ua > 0 else 0.0


#: 造三个已知词条（模拟 Windows OCR 的**逐字**输出：中文被切成一字一框，首尾相接）
def _gt_cases():
    return [
        # (词, [(字, x, y, w, h), ...], 期望合并后的框)
        ("文件", [("文", 31, 114, 32, 31), ("件", 63, 117, 32, 29)], (31, 114, 64, 32)),
        ("保存全部", [("保", 100, 50, 30, 30), ("存", 130, 50, 30, 30),
                    ("全", 160, 50, 30, 30), ("部", 190, 50, 30, 30)], (100, 50, 120, 30)),
        ("确定", [("确", 400, 200, 28, 28), ("定", 428, 200, 28, 28)], (400, 200, 56, 28)),
    ]


def _open_own_notepad():
    """开**自己的**记事本（不碰用户正在用的窗口），返回 (proc, hwnd, prev_foreground)。"""
    import win32gui
    prev_fg = win32gui.GetForegroundWindow()
    p = subprocess.Popen(["notepad.exe"])
    hwnd = 0
    for _ in range(40):
        time.sleep(0.25)
        found = []

        def _cb(h, _l, out=found):
            if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == "Notepad":
                out.append(h)
            return True
        win32gui.EnumWindows(_cb, None)
        if found:
            hwnd = found[0]
            break
    return p, hwnd, prev_fg


def main() -> int:
    global INJECT
    ap = argparse.ArgumentParser(description="M15 视觉接地判据")
    ap.add_argument("--inject", action="store_true",
                    help="造假账（合并结果被替换成逐字框 + 客户区尺寸双乘 1.5）⇒ 必须红")
    a = ap.parse_args()
    INJECT = a.inject

    print("=" * 70, flush=True)
    print("判据 47：M15 视觉接地（OCR 词框 → 词）", flush=True)
    print("=" * 70, flush=True)
    if INJECT:
        print("★ 注入：「合并结果」换成逐字原框 + 客户区尺寸双乘 1.5 ⇒ A①/A④ 必须红", flush=True)

    try:
        from agentctl import ocr as ocr_mod
    except Exception as e:
        rec("A① 字框合并：与 GT IoU ≥ 0.8、召回 100%", False,
            "★ agentctl.ocr 导入失败：%s: %s" % (type(e).__name__, e), skip=True)
        return finish()

    # ── A① 字框合并 ──
    has_merge = hasattr(ocr_mod, "merge_word_boxes")
    if not has_merge:
        rec("A① 字框合并：与 GT IoU ≥ 0.8、召回 100%", False,
            "★ agentctl.ocr 里**没有** merge_word_boxes（合并未实现）")
        rec("A①b 「文+件」必须合成一框（gap=0、Δh=6%）", False, "★ 合并未实现")
    else:
        merge = ocr_mod.merge_word_boxes
        ious, recall, details = [], [], []
        for word, chars, gt in _gt_cases():
            words = [{"t": t, "x": x, "y": y, "w": w, "h": h, "conf": 0.55}
                     for (t, x, y, w, h) in chars]
            out = merge(list(words))
            if INJECT:
                out = list(words)          # ★ 注入：假装「没合并」
            got = None
            for o in out:
                if o.get("t") == word:
                    got = o
                    break
            if got is None:
                details.append("%s: 没合成出整词（得到 %s）" % (word, [o.get("t") for o in out]))
                ious.append(0.0)
                recall.append(0)
                continue
            gtbox = {"x": gt[0], "y": gt[1], "w": gt[2], "h": gt[3]}
            iou = _iou(got, gtbox)
            ious.append(iou)
            recall.append(1)
            details.append("%s: IoU=%.3f 框=%s" % (word, iou,
                                                (got.get("x"), got.get("y"), got.get("w"), got.get("h"))))
        rec("A① 字框合并：与 GT IoU ≥ 0.8", bool(ious) and all(i >= 0.8 for i in ious),
            "；".join(details))
        rec("A①b 字框合并：召回 100%（三个词条全中）", bool(recall) and all(recall),
            "召回=%d/%d" % (sum(recall), len(recall)))
        # 「文 + 件」必须合成一框（gap=0、h 差 6%）
        _w = [{"t": "文", "x": 31, "y": 114, "w": 32, "h": 31, "conf": 0.55},
              {"t": "件", "x": 63, "y": 117, "w": 32, "h": 29, "conf": 0.55}]
        _o = merge(list(_w))
        if INJECT:
            _o = list(_w)              # ★ 注入：假装「没合并」
        _joined = any(o.get("t") == "文件" for o in _o)
        rec("A①c 「文+件」必须合成一框（gap=0、Δh=6%）", _joined,
            "合并结果=%s" % [(o.get("t"), o.get("x"), o.get("y"), o.get("w"), o.get("h")) for o in _o])

        # ★ A①d **多行交错**：另一行的字 x 夹在两个字中间
        #   （本机真机实测：记事本状态栏的「行」x=34 夹在「文」x=21 与「件」x=42 之间
        #   ⇒ 旧的「按 x 排序 + 单一开放组」把「文件」拆成了两个词框）。
        #   期望：「文」「件」仍然合成「文件」；「行」**自己一组**。
        _mw = [{"t": "文", "x": 21, "y": 76, "w": 21, "h": 20, "conf": 0.55},
               {"t": "行", "x": 34, "y": 1488, "w": 18, "h": 18, "conf": 0.55},
               {"t": "件", "x": 42, "y": 78, "w": 21, "h": 20, "conf": 0.55}]
        _mo = merge(list(_mw))
        if INJECT:
            _mo = list(_mw)
        _wf = next((o for o in _mo if o.get("t") == "文件"), None)
        _hang = next((o for o in _mo if o.get("t") == "行"), None)
        _d_ok = (_wf is not None and _hang is not None
                 and abs(int(_wf.get("x")) - 21) <= 1
                 and abs(int(_wf.get("w")) - 42) <= 1)
        rec("A①d 多行交错时仍能合成「文件」（另一行的字夹在中间）", _d_ok,
            "合并结果=%s" % [(o.get("t"), o.get("x"), o.get("y"), o.get("w"), o.get("h"))
                             for o in _mo])

    # ── A② / A④ / A⑤：真机（自己开的记事本）──
    proc = None
    hwnd = 0
    prev_fg = 0
    try:
        try:
            import win32gui
            proc, hwnd, prev_fg = _open_own_notepad()
        except Exception as e:
            rec("A② 两条抓图路径同区域坐标差 ≤ 1px", False,
                "★ 开不出自己的记事本：%s: %s" % (type(e).__name__, e), skip=True)
            rec("A④ 客户区口径与 uia_tree 交叉核对（物理像素）", False, "★ 没窗口", skip=True)
            rec("A⑤ 帧绑定原点（抓图期间窗口没动）", False, "★ 没窗口", skip=True)
            return finish()

        if not hwnd:
            rec("A② 两条抓图路径同区域坐标差 ≤ 1px", False, "★ 开不出自己的记事本", skip=True)
            rec("A④ 客户区口径与 uia_tree 交叉核对（物理像素）", False, "★ 没窗口", skip=True)
            rec("A⑤ 帧绑定原点（抓图期间窗口没动）", False, "★ 没窗口", skip=True)
            return finish()

        # 把**自己的**记事本顶到最前（ImageGrab 抓的是屏幕上真正显示的东西）。
        # ★ Windows 的前台锁常常让 SetForegroundWindow 失效 ⇒ 改用 **TOPMOST** 兜底：
        #   窗口盖在最上层 ⇒ 屏幕上那块区域显示的就是它（测完直接杀掉，不留痕迹）。
        #   记下原来的前台，测完**还回去** —— 不抢用户正在用的窗口。
        try:
            import win32con
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
            time.sleep(0.8)
        except Exception:
            pass

        geom = ocr_mod.client_rect_screen(int(hwnd))
        if INJECT:
            # ★ 注入：把客户区尺寸双乘 1.5（= 感知进程里无条件乘 dpi_scale 的老洞）
            geom = {**geom, "width": int(geom.get("width") or 0) * 3 // 2,
                    "height": int(geom.get("height") or 0) * 3 // 2}

        # ── A④ 口径物理性：与 **uia_tree** 的客户区屏幕矩形（另一套实现）逐像素对上 ──
        try:
            from agentctl import uia_tree
            urect = uia_tree._client_rect(int(hwnd))       # [l,t,r,b]，屏幕物理像素
            if not urect:
                rec("A④ 客户区口径与 uia_tree 交叉核对（物理像素）", False,
                    "★ uia_tree 取不到客户区屏幕矩形", skip=True)
            else:
                uw, uh = int(urect[2]) - int(urect[0]), int(urect[3]) - int(urect[1])
                same_size = (int(geom.get("width") or 0) == uw and int(geom.get("height") or 0) == uh)
                same_org = (int(geom.get("left") or 0) == int(urect[0])
                            and int(geom.get("top") or 0) == int(urect[1]))
                aware_ok = int(geom.get("dpi_aware") or 0) == 2
                rec("A④ 客户区口径与 uia_tree 交叉核对（物理像素）",
                    same_size and same_org and aware_ok,
                    "ocr 客户区=%sx%s@(%s,%s) ｜ uia_tree 客户区=%dx%d@(%s,%s) ｜ dpi_aware=%s "
                    "⇒ 尺寸%s 原点%s 感知%s"
                    % (geom.get("width"), geom.get("height"), geom.get("left"), geom.get("top"),
                       uw, uh, urect[0], urect[1], geom.get("dpi_aware"),
                       "一致" if same_size else "★不一致",
                       "一致" if same_org else "★不一致",
                       "PER_MONITOR" if aware_ok else "★非 PER_MONITOR"))
        except Exception as e:
            rec("A④ 客户区口径与 uia_tree 交叉核对（物理像素）", False,
                "★ 跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

        # ── A② 两条抓图路径：同一区域的词框坐标差 ≤ 1px ──
        try:
            from PIL import ImageGrab
            img_pw = ocr_mod.grab_client(int(hwnd), geom)            # PrintWindow 路径
            img_ig = ImageGrab.grab(bbox=(int(geom["left"]), int(geom["top"]),
                                          int(geom["left"]) + int(geom["width"]),
                                          int(geom["top"]) + int(geom["height"])),
                                    all_screens=True)                # ImageGrab 退路
            if img_pw.size != img_ig.size:
                rec("A② 两条抓图路径同区域坐标差 ≤ 1px", False,
                    "★ 两条路径的图**尺寸就不一样**：PrintWindow=%s ｜ ImageGrab=%s"
                    % (img_pw.size, img_ig.size))
            else:
                # ★★ 先找一块**屏幕上确实显示着这个窗口**的子区域。
                #   本机前台锁很硬（SetForegroundWindow 会被 Windows 拒），
                #   用户的窗口常常压住整块客户区 ⇒ 整块区域两条路径天然不可比。
                #   两条路径的坐标偏移是**常数**（都在客户区相对系里），
                #   所以在**任一块可见区域**上量这个偏移，量的就是同一件事。
                #   找不到可见区域 ⇒ **验不了**（环境受阻），不是产品红。
                from PIL import ImageChops, ImageStat
                W, H = img_pw.size
                cands = []
                for (cw, ch) in ((700, 150), (500, 110), (900, 220), (300, 90)):
                    for (cx, cy) in ((0, 0), (0, max(0, H - ch)), (max(0, W - cw), 0),
                                     (max(0, W - cw), max(0, H - ch))):
                        cands.append((cx, cy, cw, ch))
                patch = None
                for (cx, cy, cw, ch) in cands:
                    if cx + cw > W or cy + ch > H:
                        continue
                    try:
                        _d = ImageStat.Stat(ImageChops.difference(
                            img_pw.crop((cx, cy, cx + cw, cy + ch)).convert("RGB"),
                            img_ig.crop((cx, cy, cx + cw, cy + ch)).convert("RGB"))).mean
                        mad = sum(_d) / 3.0
                    except Exception:
                        continue
                    if mad <= 12.0:
                        patch = (cx, cy, cw, ch, mad)
                        break
                if patch is None:
                    rec("A② 两条抓图路径同区域坐标差 ≤ 1px", False,
                        "★ 验不了：整块客户区都被别的窗口挡住了（试了 %d 块候选区域，"
                        "屏幕上显示的都不是这个窗口）⇒ 两条路径拿不到同一块画面，没法比坐标"
                        % len(cands), skip=True)
                else:
                    cx, cy, cw, ch, mad = patch
                    rec_pw = ocr_mod.recognize_image(img_pw.crop((cx, cy, cx + cw, cy + ch)))
                    rec_ig = ocr_mod.recognize_image(img_ig.crop((cx, cy, cx + cw, cy + ch)))
                    wp = [(w["t"], w["x"], w["y"]) for w in rec_pw.get("words") or []]
                    wi = [(w["t"], w["x"], w["y"]) for w in rec_ig.get("words") or []]
                    m_pw, m_ig = {}, {}
                    for t, x, y in wp:
                        m_pw.setdefault(t, []).append((x, y))
                    for t, x, y in wi:
                        m_ig.setdefault(t, []).append((x, y))
                    deltas, common = [], 0
                    for t, lst in m_pw.items():
                        if t not in m_ig or not lst or not m_ig[t]:
                            continue
                        for (x1, y1), (x2, y2) in zip(lst, m_ig[t]):
                            deltas.append(max(abs(x1 - x2), abs(y1 - y2)))
                            common += 1
                    if not common:
                        rec("A② 两条抓图路径同区域坐标差 ≤ 1px", False,
                            "★ 可见区域里两条路径**没有共同的词**（PrintWindow %d 词 / ImageGrab %d 词，"
                            "区域 %s）⇒ 没测出来，不等于对" % (len(wp), len(wi), (cx, cy, cw, ch)),
                            skip=True)
                    else:
                        worst = max(deltas)
                        rec("A② 两条抓图路径同区域坐标差 ≤ 1px", worst <= 1,
                            "可见区域(客户区内 %s，两图平均差 %.1f/255) 共同词 %d 个，"
                            "最大坐标差 = %d px（PrintWindow %d 词 / ImageGrab %d 词，客户区 %s）"
                            % ((cx, cy, cw, ch), mad, common, worst, len(wp), len(wi), img_pw.size))
        except Exception as e:
            rec("A② 两条抓图路径同区域坐标差 ≤ 1px", False,
                "★ 跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

        # ── A⑤ 帧绑定原点：抓图期间窗口没动 ──
        try:
            r = ocr_mod.ocr_window(int(hwnd))
            g2 = r.get("客户区") or {}
            rec("A⑤ 帧绑定原点（抓图期间窗口没动 ⇒ 画面稳定）",
                bool(g2.get("画面稳定")) and bool(g2.get("口径可靠")),
                "画面稳定=%s 口径可靠=%s dpi_aware=%s origin=(%s,%s) 客户区=%sx%s"
                % (g2.get("画面稳定"), g2.get("口径可靠"), g2.get("dpi_aware"),
                   g2.get("left"), g2.get("top"), g2.get("width"), g2.get("height")))
        except Exception as e:
            rec("A⑤ 帧绑定原点（抓图期间窗口没动 ⇒ 画面稳定）", False,
                "★ 跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

        # ══ B / C 批（真机）══
        try:
            from agentctl import core as core_mod
        except Exception as e:
            rec("B④ 对齐：命中对的 IoU ≥ 0.6、错的 < 0.6", False,
                "★ agentctl.core 导入失败：%s: %s" % (type(e).__name__, e), skip=True)
            rec("C⑤ R3 命中 ⇒ objects[] 出现带 eid 的词元素且能定位", False, "★ 没 core", skip=True)
            return finish()

        # ── B④-a 合成一例：阈值**对错各一例**（确定性，不依赖 GUI/OCR）──
        try:
            # ★ 注入：把对齐阈值改成 0.0 ⇒ 「错的一例」也会被判命中 ⇒ B④/B④b 必须红
            #   （证明这两条**不是恒真**）。
            iou_min = 0.0 if INJECT else core_mod.SCREEN_OCR_IOU_MIN
            objs = [{"uid": "u1", "eid": "e1", "role": "TextControl",
                     "rect": [100, 100, 200, 200]}]
            hit_words = [{"t": "文件", "x": 120, "y": 100, "w": 80, "h": 100}]
            miss_words = [{"t": "文件", "x": 500, "y": 500, "w": 80, "h": 100}]
            r_hit = core_mod._ocr_align_words(hit_words, (0, 0), objs, iou_min=iou_min)
            r_miss = core_mod._ocr_align_words(miss_words, (0, 0), objs, iou_min=iou_min)
            i_hit = float((r_hit[0] or {}).get("IoU") or 0.0)
            i_miss = float((r_miss[0] or {}).get("IoU") or 0.0)
            ok_hit = bool((r_hit[0] or {}).get("命中UIA")) and i_hit >= 0.6
            ok_miss = (not (r_miss[0] or {}).get("命中UIA")) and i_miss < 0.6
            rec("B④ 对齐：命中对的 IoU ≥ 0.6、错的 < 0.6", ok_hit and ok_miss,
                "对的一例 IoU=%.3f 判命中=%s ｜ 错的一例 IoU=%.3f 判命中=%s（阈值 %.2f）"
                % (i_hit, (r_hit[0] or {}).get("命中UIA"), i_miss,
                   (r_miss[0] or {}).get("命中UIA"), iou_min))
        except Exception as e:
            rec("B④ 对齐：命中对的 IoU ≥ 0.6、错的 < 0.6", False,
                "★ 跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

        # ── B④-b 真机：OCR 词框 + 帧绑定 origin ⇒ 与 UIA 物体求 IoU ──
        try:
            from agentctl import uia_tree
            nodes, _tr = uia_tree.walk(int(hwnd), roles=("all",), max_nodes=400,
                                       max_depth=12, include_offscreen=False)
            uobjs = [{"uid": "n%d" % i, "eid": n.get("eid"), "role": n.get("type"),
                      "rect": list(n.get("rect") or ())} for i, n in enumerate(nodes)]
            r = ocr_mod.ocr_window(int(hwnd))
            cg = r.get("客户区") or {}
            if not (cg.get("口径可靠") and cg.get("画面稳定")):
                rec("B④b 真机：词框→屏幕物理→与 UIA 物体对齐", False,
                    "★ 这一帧的 origin 不可靠（口径可靠=%s 画面稳定=%s）⇒ 对齐无从谈起"
                    % (cg.get("口径可靠"), cg.get("画面稳定")), skip=True)
            else:
                rows = core_mod._ocr_align_words(r.get("词框") or [],
                                                 (cg.get("left"), cg.get("top")), uobjs,
                                                 iou_min=(0.0 if INJECT
                                                          else core_mod.SCREEN_OCR_IOU_MIN))
                hits = [x for x in rows if x.get("命中UIA")]
                best = max([float(x.get("IoU") or 0) for x in rows] or [0.0])
                # 命中数不是硬指标（OCR 认出的字未必都有 UIA 对应物），
                # 但**至少要有一次真正的命中**，且命中的 IoU 必须 ≥ 阈值。
                rec("B④b 真机：词框→屏幕物理→与 UIA 物体对齐",
                    bool(hits) and all(float(x.get("IoU") or 0) >= 0.6 for x in hits),
                    "词 %d 个，命中 UIA %d 个，最大 IoU=%.3f（UIA 节点 %d 个）"
                    % (len(rows), len(hits), best, len(uobjs)))
        except Exception as e:
            rec("B④b 真机：词框→屏幕物理→与 UIA 物体对齐", False,
                "★ 跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

        # ── C⑤ R3 命中 ⇒ objects[] 出现带 eid 的词元素 ⇒ 能定位到点击点 ──
        try:
            import tempfile
            _sess = os.path.join(tempfile.gettempdir(), "agentctl_m15_eid_%d.json" % os.getpid())
            os.environ["AGENTCTL_EID_SESSIONS"] = _sess
            scr = core_mod._screen_build(int(hwnd), {"query": "文件", "ocr": True,
                                                     "include_offscreen": False})
            ocr_seg = scr.get("ocr") or {}
            objs_out = scr.get("objects") or []
            words_out = ocr_seg.get("词元素") or []
            al = ocr_seg.get("对齐") or []
            # 找「带 eid 的词元素」：要么是新造的词元素（role=OcrWordControl），
            # 要么是对上了 UIA 且挂了 ocr 标注的 UIA 物体。
            cand = [o for o in objs_out
                    if (o.get("role") == core_mod.SCREEN_OCR_WORD_ROLE and o.get("eid"))
                    or (o.get("ocr") and o.get("eid"))]
            note = ocr_seg.get("词元素说明") or {}
            cap = scr.get("capture_id")
            if not cand:
                rec("C⑤ R3 命中 ⇒ objects[] 出现带 eid 的词元素", False,
                    "★ objects[] 里没有带 eid 的词元素（命中=%s 命中词=%s 对上UIA=%s 新造=%s 原因=%s）"
                    % (ocr_seg.get("hit"), note.get("命中词"), note.get("对上UIA"),
                       note.get("新造词元素"), note.get("原因")))
            else:
                # 旁表里取回来 + 复核 ⇒ 必须能算出**点击点**
                eid = cand[0].get("eid")
                feat, entry, how = core_mod._eid_lookup(cap, eid)
                if feat is None:
                    rec("C⑤ R3 命中 ⇒ objects[] 出现带 eid 的词元素", False,
                        "★ 旁表里取不回 eid=%s（how=%s）⇒ 点不了" % (eid, how))
                else:
                    if feat.get("ocr"):
                        cur, why, rctx = core_mod._ocr_word_recheck(int(hwnd), feat)
                    else:
                        cur, why, rctx = core_mod._eid_recheck(int(hwnd), feat,
                                                               (entry or {}).get("walk"))
                    if cur is None:
                        rec("C⑤ R3 命中 ⇒ objects[] 出现带 eid 的词元素", False,
                            "★ 复核失败（%s）⇒ 点不了" % why)
                    else:
                        cx, cy = cur.get("center") or (0, 0)
                        rct = cand[0].get("rect") or []
                        inside = (len(rct) == 4
                                  and rct[0] <= cx <= rct[2] and rct[1] <= cy <= rct[3])
                        rec("C⑤ R3 命中 ⇒ objects[] 出现带 eid 的词元素且能定位",
                            bool(inside),
                            "词元素 label=%r eid=%s uid=%s rect=%s ⇒ 复核后点击点=(%s,%s)%s"
                            % (cand[0].get("label"), eid, cand[0].get("uid"), rct,
                               cx, cy, "（落在框内）" if inside else "★不在框内"))
            try:
                os.remove(_sess)
            except Exception:
                pass
        except Exception as e:
            rec("C⑤ R3 命中 ⇒ objects[] 出现带 eid 的词元素", False,
                "★ 跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

        # ── C⑤b「UIA 瞎了」那条路：真词 + 空 UIA ⇒ 必须造出带 eid 的词元素 ──
        try:
            r = ocr_mod.ocr_window(int(hwnd))
            cg = r.get("客户区") or {}
            boxes = r.get("词框") or []
            tgt = next((b for b in boxes if str(b.get("t") or "") == "文件"), None)
            if tgt is None or not (cg.get("口径可靠") and cg.get("画面稳定")):
                rec("C⑤b UIA 瞎了 ⇒ 造出带 eid 的词元素并能定位", False,
                    "★ 这一屏没认出「文件」或 origin 不可靠 ⇒ 这条验不了", skip=True)
            else:
                fake = {"hit": True, "origin": (int(cg.get("left")), int(cg.get("top"))),
                        "词框": [dict(tgt)], "res": {}}
                objs2, nodes2, note2 = core_mod._screen_ocr_word_objects(
                    fake, [], "文件")          # ← cands 传空 = 假装 UIA 什么都没认出来
                if not objs2:
                    rec("C⑤b UIA 瞎了 ⇒ 造出带 eid 的词元素并能定位", False,
                        "★ 没造出词元素（说明=%s 命中词=%s）" % (note2.get("原因"),
                                                          note2.get("命中词")))
                else:
                    w = objs2[0]
                    _tok = "m15-test-%d" % os.getpid()
                    core_mod._eid_sessions_put(_tok, int(hwnd), nodes2, {"tool": "m15"})
                    feat, entry, how = core_mod._eid_lookup(_tok, w.get("eid"))
                    if feat is None:
                        rec("C⑤b UIA 瞎了 ⇒ 造出带 eid 的词元素并能定位", False,
                            "★ 旁表取不回 eid=%s（how=%s）" % (w.get("eid"), how))
                    else:
                        cur, why, rctx = core_mod._ocr_word_recheck(int(hwnd), feat)
                        if cur is None:
                            rec("C⑤b UIA 瞎了 ⇒ 造出带 eid 的词元素并能定位", False,
                                "★ OCR 复核失败（%s）" % why)
                        else:
                            cx, cy = cur.get("center") or (0, 0)
                            rct = w.get("rect") or []
                            inside = (len(rct) == 4 and rct[0] <= cx <= rct[2]
                                      and rct[1] <= cy <= rct[3])
                            rec("C⑤b UIA 瞎了 ⇒ 造出带 eid 的词元素并能定位",
                                bool(inside) and bool(feat.get("ocr")),
                                "词元素 label=%r eid=%s uid=%s rect=%s ⇒ 复核后点击点=(%s,%s)%s"
                                % (w.get("label"), w.get("eid"), w.get("uid"), rct, cx, cy,
                                   "（落在框内）" if inside else "★不在框内"))
        except Exception as e:
            rec("C⑤b UIA 瞎了 ⇒ 造出带 eid 的词元素并能定位", False,
                "★ 跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

        # ── C⑤c 窗口代际**第二道闸**：代际对不上 ⇒ 拒（一个像素都不点）──
        try:
            err, blind = core_mod._ownership_precheck({"window_gen": "999999"},
                                                       int(hwnd), "ctl_click")
            bad_ok = (err is not None) and int((err or {}).get("code") or 0) == 12
            rec("C⑤c 窗口代际第二道闸：代际对不上 ⇒ code=12 拒", bad_ok,
                "代际给 999999 ⇒ %s" % (("code=%s 拒：%s" % ((err or {}).get("code"),
                                                            str((err or {}).get("warning"))[:90]))
                                        if err else "★没拒"))
        except Exception as e:
            rec("C⑤c 窗口代际第二道闸：代际对不上 ⇒ code=12 拒", False,
                "★ 跑不起来：%s: %s" % (type(e).__name__, e), skip=True)
    finally:
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass
        try:
            import win32gui
            if prev_fg and win32gui.IsWindow(prev_fg):
                win32gui.SetForegroundWindow(prev_fg)      # 把前台还给用户
        except Exception:
            pass

    # ── A③ coord_space 声明 ──
    try:
        _r = ocr_mod.ocr_window(0)     # 坏 hwnd ⇒ 走失败路径也要带声明
        _cs = _r.get("coord_space")
        rec("A③ 返回值带 coord_space 声明（= client_physical）",
            _cs == "client_physical", "coord_space=%r" % _cs)
    except Exception as e:
        rec("A③ 返回值带 coord_space 声明（= client_physical）", False,
            "★ 跑不起来：%s: %s" % (type(e).__name__, e), skip=True)

    return finish()


if __name__ == "__main__":
    sys.exit(main())
