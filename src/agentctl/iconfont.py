# -*- coding: utf-8 -*-
r"""iconfont.py —— 图标字体反查（Segoe Fluent Icons / Segoe MDL2 Assets）

治的病（派单 §③ · D1）：**纯图标按钮没有文字** ⇒ OCR 搜不到、UIA 常常只给外壳
⇒ 只能靠估坐标，点错率高。
⇒ 这里给「纯图标」一条**确定性、可解释**的识别路径：
    屏幕上的图标块  ⇒  归一化  ⇒  零均值 NCC  ⇒  码点  ⇒  语义名
**不用任何模型、不用 GPU、不引任何新依赖**（纯 numpy + PIL + 系统字体）。

★ 三条硬规矩（都是踩过坑才定下来的，不是设计偏好）
──────────────────────────────────────────────────
1. **必须按目标窗口的 DPI 渲染模板**。
   本机是 **150% DPI**：同一个码点在 100% 下是 16 px 字形、在 150% 下是 24 px。
   模板按错尺寸渲染 ⇒ 笔画粘连/断裂 ⇒ NCC 必掉分（派单补充 §3 明写这条是缺口）。
   ⇒ 模板按 `size × dpi_scale` 渲染，再**形状归一化**到同一张画布。
2. **必须关 ClearType（子像素抗锯齿）**。
   ClearType 会把笔画边缘染成 RGB 三色 ⇒ 灰度化后边缘出现「毛刺」⇒ NCC 掉分。
   ⇒ PIL/FreeType 默认走 `FT_RENDER_MODE_NORMAL`（**灰度抗锯齿**，无子像素），
     本模块据此判定「已关 ClearType」；并且比对时用**三通道最大差**做二值化，
     不依赖灰度边缘。
3. **候选框必须是「已知的图标格」**，不是满屏乱扫。
   原型实测（`work/iconmatch/README.md` §3.5）：满屏乱扫时高置信命中密度 0.6%，
   而且 top5 全是弧线类字形（wifi/download/volume/forward）⇒ **系统性误判**。
   ⇒ 本模块只对**调用方给定的框**工作（框从哪来是另一件事，见 `describe_icons`）。

★ 输出是「多一条证据」，**不替代 UIA/OCR**
─────────────────────────────────────────────
认错一个图标 = 点错一个按钮 ⇒ 置信度必须与来源一起给出：
`{码点, 语义名, 分, margin, 阈值, 够不够格}`。
`margin`（第一名与第二名的分差）比绝对分更管用：真图标往往**孤峰**，
误命中往往**一片糊**。

★ 不引新依赖
────────────
连通块用**纯 Python 洪水填充**（候选框都很小，≤64×64），
**不用 scipy**（scipy 不在 requirements.txt 里，进产品就是给安装包加体积）。
"""
from __future__ import annotations

import os

# ── 字体（系统自带，不需要装）─────────────────────────────────────────────
FONTS = {
    "mdl2": r"C:\Windows\Fonts\segmdl2.ttf",        # Segoe MDL2 Assets
    "fluent": r"C:\Windows\Fonts\SegoeIcons.ttf",   # Segoe Fluent Icons
}

#: 码点表（**公开**的，不是猜的）。名字 = 语义，值 = Unicode 码点。
#: ★ 覆盖 Windows 11 界面最常出现的一批纯图标。
#: ⚠ **同一个码点只能出现一次** —— 重复的码点会让两个模板一模一样 ⇒
#:   margin 恒为 0 ⇒ 双闸把它判成「认不出」（这是双闸在干活，不是 bug）。
#:   2026-10-22 逐个渲染核对（`work/_M6_三件套_20221022/证据_图标/码点对照_*.png`）
#:   后删掉了这些重复/无效项：global_nav(E700)、chevron_down(E70D)、
#:   share(E72D)、new_folder(E8B7)、copy_link(E8C8)、money(E7BF)、
#:   rocket(E135 —— **Segoe Fluent Icons 里没有这个码点**，渲染出来是个 .notdef 黑方块)、
#:   gift(E8D7 —— 渲染出来不是礼物)。
MDL2_CPS = {
    "back": 0xE72B, "forward": 0xE72A, "refresh": 0xE72C, "up": 0xE72D,
    "down": 0xE70D, "chevron_up": 0xE70E,
    "home": 0xE80F, "settings": 0xE713, "search": 0xE721, "close": 0xE711,
    "delete": 0xE74D, "save": 0xE74E, "edit": 0xE70F, "copy": 0xE8C8,
    "folder": 0xE8B7, "more": 0xE712, "check": 0xE73E, "open": 0xE8E0,
    "print": 0xE749, "undo": 0xE7A7, "redo": 0xE7A8,
    "hamburger": 0xE700, "download": 0xE896,
    "view_list": 0xE8A0, "view_details": 0xE8A5, "view_grid": 0xE8A4,
    "sort": 0xE8A1, "filter": 0xE71C, "info": 0xE946,
    "warning": 0xE7BA, "error": 0xE783, "help": 0xE897, "pin": 0xE718,
    "wifi": 0xE701, "volume": 0xE767, "battery": 0xE7F4, "camera": 0xE722,
    "mail": 0xE715, "cloud": 0xE753, "globe": 0xE774, "calendar": 0xE787,
    "open_in_new": 0xE8A7, "minimize": 0xE921, "maximize": 0xE922,
    "restore": 0xE923, "close_window": 0xE8BB,
    "cut": 0xE8C1, "paste": 0xE8BD, "zoom": 0xE71E, "sync": 0xE895,
}

FLUENT_CPS = {
    "open_in_new": 0xE8A7, "pin": 0xE718, "globe": 0xE774, "calendar": 0xE787,
    "mail": 0xE715, "cloud": 0xE753, "download": 0xE896, "print": 0xE749,
    "camera": 0xE722, "wifi": 0xE701, "battery": 0xE7F4, "volume": 0xE767,
    "emoji": 0xE899, "reading": 0xE7B3, "weather": 0xE823,
    "shopping": 0xE7BF, "news": 0xE71D, "music": 0xE8E6, "games": 0xE7FC,
    "people": 0xE77B, "key": 0xE8CE, "shield": 0xEA18, "lock": 0xE72E,
    "flash": 0xE945, "coffee": 0xE7F8,
}

#: 语义名（中文）—— 给模型/人看的那一层。
SEMANTIC_ZH = {
    "back": "后退", "forward": "前进", "refresh": "刷新", "up": "向上",
    "down": "向下", "chevron_up": "上拉箭头",
    "home": "主页", "settings": "设置", "search": "搜索", "close": "关闭",
    "delete": "删除", "save": "保存", "edit": "编辑", "copy": "复制",
    "folder": "文件夹", "more": "更多", "check": "勾选", "open": "打开",
    "print": "打印", "undo": "撤销", "redo": "重做",
    "hamburger": "汉堡菜单", "download": "下载",
    "view_list": "列表视图", "view_details": "详细信息视图", "view_grid": "平铺视图",
    "sort": "排序", "filter": "筛选", "info": "信息",
    "warning": "警告", "error": "错误", "help": "帮助", "pin": "固定",
    "wifi": "无线网络", "volume": "音量", "battery": "电池", "camera": "相机",
    "mail": "邮件", "cloud": "云", "globe": "全球/网页", "calendar": "日历",
    "open_in_new": "在新窗口打开", "minimize": "最小化", "maximize": "最大化",
    "restore": "还原", "close_window": "关闭窗口",
    "cut": "剪切", "paste": "粘贴", "zoom": "缩放", "sync": "同步",
    "rocket": "启动", "emoji": "表情", "reading": "阅读", "weather": "天气",
    "shopping": "购物", "news": "资讯", "music": "音乐", "games": "游戏",
    "people": "联系人", "key": "密钥", "shield": "防护", "lock": "锁定",
    "flash": "闪电", "coffee": "咖啡",
}

#: 判据参数（阈值都是**保守**取法：宁可认不出，也不认错 —— 认错 = 点错按钮）
NORM = 32           # 归一化画布边长
DEFAULT_MIN_SCORE = 0.60     # 第一名的最低分
DEFAULT_MIN_MARGIN = 0.15   # 第一名与第二名的最小分差
DEFAULT_DPI = 1.5            # 本机 150%（调用方应传目标窗口的真实 DPI）
MIN_PIXELS = 6              # 连通块少于这么多像素 ⇒ 不是图标
MAX_AREA_RATIO = 0.5        # 连通块超过候选区一半 ⇒ 多半是背景
#: 候选块的长宽比上下限（**排除「一条线」**）。
#: ★ 为什么要有：像 E921「最小化」这种**纯横线**字形，形状归一化之后
#:   就是一条横杠 ⇒ 文字里的每一行下划线、每一个分隔线都会跟它「一模一样」
#:   （实测：正文文字区扫 850 块，37 块高置信命中，最高分 1.000 的就是「最小化」）。
#:   ⇒ 候选的长宽比超出 [1/6, 6] ⇒ 不是图标形状，丢掉。
MIN_ASPECT = 1.0 / 6.0
MAX_ASPECT = 6.0

_TPL_CACHE = {}


# ── 模板渲染 ────────────────────────────────────────────────────────────
def _render_glyph(font_key: str, cp: int, size: int):
    """渲染一个码点 → 0..1 的灰度图（白字黑底，裁到外接框）。

    ★ **PIL 默认就是关 ClearType 的**：PIL 走 FreeType 的
      `FT_RENDER_MODE_NORMAL`（灰度抗锯齿），**不做子像素渲染** ⇒ 不存在
      ClearType 的 RGB 毛刺。派单补充 §3 要求「关 ClearType」——
      本模块靠「用 PIL 而不是 GDI/DirectWrite 画字」来满足这一条。
    """
    path = FONTS.get(font_key)
    if not path or not os.path.exists(path):
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(path, int(size))
        canvas = int(size) * 2
        im = Image.new("L", (canvas, canvas), 0)
        d = ImageDraw.Draw(im)
        d.text((canvas // 4, canvas // 4), chr(int(cp)), fill=255, font=font)
    except Exception:
        return None
    import numpy as np
    a = np.asarray(im, dtype=np.float32) / 255.0
    ys, xs = np.nonzero(a > 0.15)
    if len(xs) == 0:
        return None
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _norm_shape(mask, out=NORM):
    """二值掩码 → 保持长宽比的 out×out 画布（0..1）。"""
    import numpy as np
    from PIL import Image
    sub = np.asarray(mask, dtype=np.float32)
    h, w = sub.shape
    s = max(h, w)
    if s <= 0:
        return None
    sc = float(out) / s
    nh, nw = max(1, int(round(h * sc))), max(1, int(round(w * sc)))
    img = Image.fromarray((np.clip(sub, 0, 1) * 255).astype("uint8")).resize(
        (nw, nh), Image.LANCZOS)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    canvas = np.zeros((out, out), dtype=np.float32)
    y0, x0 = (out - nh) // 2, (out - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = arr
    return canvas


def build_templates(dpi=DEFAULT_DPI, size=16, cps=None):
    """渲染模板集 → {键: [归一化画布, ...]}（每个码点**多个切值**）。

    ★ **按 DPI 渲染**（规矩①）：名义 16 px 的字形在 150% 屏上是 24 px
      ⇒ 模板按 `size × dpi` 渲染再归一化，笔画结构才与屏幕上的一致。
    ★ 键 = "字体:名字"。
    ★ **每个码点存两个切值**（alpha ≥ 0.15 / 0.35）：
      模板与候选的二值化切值只要差一点，边缘就差一圈 ⇒ NCC 掉分（实测踩过：
      地球仪被认成「列表视图」0.62）。⇒ 两边都扫一遍切值，取最高分。
    """
    key = (round(float(dpi), 3), int(size))
    if key in _TPL_CACHE:
        return _TPL_CACHE[key]
    px = max(8, int(round(float(size) * float(dpi))))
    out = {}
    for font_key, table in (("mdl2", MDL2_CPS), ("fluent", FLUENT_CPS)):
        for name, cp in (cps or table).items():
            g = _render_glyph(font_key, cp, px)
            if g is None:
                continue
            canv = []
            for cut in (0.15, 0.35):
                m = g >= cut
                if not m.any():
                    continue
                n = _norm_shape(m)
                if n is not None:
                    canv.append(n)
            if canv:
                out["%s:%s" % (font_key, name)] = canv
    _TPL_CACHE[key] = out
    return out


def template_meta():
    """模板键 → {码点, 语义}（供回执/文档用）。"""
    meta = {}
    for font_key, table in (("mdl2", MDL2_CPS), ("fluent", FLUENT_CPS)):
        for name, cp in table.items():
            meta["%s:%s" % (font_key, name)] = {
                "码点": "U+%04X" % int(cp), "码点十进制": int(cp),
                "语义": SEMANTIC_ZH.get(name, name),
            }
    return meta


# ── 连通块（纯 Python 洪水填充；**不用 scipy**）──────────────────────────
def _components(mask):
    """二值 mask（numpy bool）→ [[(y,x), ...], ...]（8 邻域）。"""
    import numpy as np
    H, W = mask.shape
    seen = np.zeros((H, W), dtype=bool)
    out = []
    for y in range(H):
        row = mask[y]
        for x in range(W):
            if not row[x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            pix = []
            while stack:
                cy, cx = stack.pop()
                pix.append((cy, cx))
                for ny in range(max(0, cy - 1), min(H, cy + 2)):
                    for nx in range(max(0, cx - 1), min(W, cx + 2)):
                        if mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
            out.append(pix)
    return out


def _otsu(gray) -> float:
    """Otsu 阈值（直方图法，纯 numpy）。"""
    import numpy as np
    hist, edges = np.histogram(gray, bins=256, range=(0.0, 1.0))
    total = float(gray.size)
    centers = (edges[:-1] + edges[1:]) / 2.0
    sum_all = float(np.dot(centers, hist))
    w_b = sum_b = 0.0
    best_var, thr = -1.0, 0.5
    for i in range(256):
        w_b += hist[i]
        if w_b <= 0:
            continue
        w_f = total - w_b
        if w_f <= 0:
            break
        sum_b += hist[i] * centers[i]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best_var:
            best_var, thr = var, float(centers[i])
    return thr


def shape_candidates(gray, max_cand=8):
    """从候选区里挑出**可能的图标连通块** → {极性: [归一化画布, ...]}。

    ★★★ 极性必须**挑**，不能盲试 ★★★
      图标要么比背景**深**（黑图标），要么比背景**浅**（白图标）。
      盲试两个极性 ⇒ 其中一个极性把**背景**当成墨水 ⇒ 抗锯齿边缘碎成
      一堆横条 ⇒ 认成「列表视图」（实测：地球仪被认成 mdl2:view_list 0.62）。
      ⇒ 两个极性**各算一遍，取整体分更高的那个**（见 `match_region`）。

    ★★★ 口径必须与模板**完全一致**（本模块第一版最大的坑）★★★
      模板走的是「墨水 alpha ≥ 切值」的掩码；第一版候选走的是「原灰度 < Otsu」。
      两个切法不同 ⇒ 同一个地球仪，模板带**柔和边缘**、候选只剩**硬芯** ⇒
      实测 NCC 掉到 0.02~0.14（该认的认不回来，只能认成别的字形）。

    ★ 为什么不能只取「最大连通块」：真实截图里最大的一块往往是**背景面板**
      ⇒ 归一化后是一块实心方块 ⇒ NCC 恒为 0.000。
    ★ 为什么**多个阈值**：图标常和背景亮度接近 ⇒ 单一阈值会并进背景或切碎。
    ★ 为什么还要**整块并集**：有些字形**自带内部笔画**（地球仪的经纬线、齿轮的齿）
      ⇒ 二值化后被切成好几块 ⇒ 只看单块就只拿到「一个圆圈」⇒ 认成别的字形。
      ⇒ 除了每个连通块，还把「这一阈值下所有像图标的块**并起来**」当一个候选。
    ★ 筛法：面积在 [MIN_PIXELS, 区域 50%] 之间 **且** 不贴区域边界。
    """
    import numpy as np
    gray = np.asarray(gray, dtype=np.float32)
    if gray.size == 0:
        return {}
    if gray.max() > 1.5:                 # 传进来的是 0..255 ⇒ 归一化
        gray = gray / 255.0
    res = {}
    for pol, alpha in (("亮图标", gray), ("暗图标", 1.0 - gray)):
        out = []
        cuts = {0.15, 0.35, 0.55, 0.75}
        try:
            cuts.add(_otsu(alpha))
        except Exception:
            pass
        for t in sorted(cuts):
            mask = alpha >= t
            if not mask.any() or mask.all():
                continue
            keep = []
            for pix in _components(mask):
                area = len(pix)
                if area < MIN_PIXELS or area > MAX_AREA_RATIO * mask.size:
                    continue
                ys = [p[0] for p in pix]
                xs = [p[1] for p in pix]
                y0, y1, x0, x1 = min(ys), max(ys), min(xs), max(xs)
                # ★ 长宽比闸：排除「一条线」（见 MIN_ASPECT 的理由）
                bw = x1 - x0 + 1
                bh = y1 - y0 + 1
                asp = bw / float(bh) if bh else 0.0
                if asp < MIN_ASPECT or asp > MAX_ASPECT:
                    continue
                border = sum(1 for (yy, xx) in pix
                             if yy in (y0, y1) and xx in (x0, x1))
                if border / float(area) > 0.6:
                    continue
                keep.append(pix)
            # ★ ① 每个块单独一个候选
            for pix in keep:
                ys = [p[0] for p in pix]
                xs = [p[1] for p in pix]
                sub = _crop_mask(pix, min(ys), max(ys), min(xs), max(xs))
                n = _norm_shape(sub)
                if n is not None:
                    out.append(n)
            # ★ ② 所有块**并起来**一个候选（带内部笔画的字形靠它认回来）
            #   ⚠ 加闸：并集外接框 ≤ 最大单块外接框 × 3 ——
            #   否则「几行文字」会被并成大块 ⇒ 认成「最小化」那种横条。
            if len(keep) > 1:
                allp = [p for pix in keep for p in pix]
                ys = [p[0] for p in allp]
                xs = [p[1] for p in allp]
                uy0, uy1, ux0, ux1 = min(ys), max(ys), min(xs), max(xs)
                biggest = 0
                for pix in keep:
                    py = [p[0] for p in pix]
                    px_ = [p[1] for p in pix]
                    a = (max(py) - min(py) + 1) * (max(px_) - min(px_) + 1)
                    biggest = max(biggest, a)
                uarea = (uy1 - uy0 + 1) * (ux1 - ux0 + 1)
                if biggest > 0 and uarea <= biggest * 3:
                    sub = _crop_mask(allp, uy0, uy1, ux0, ux1)
                    n = _norm_shape(sub)
                    if n is not None:
                        out.append(n)
            if len(out) >= max_cand:
                break
        if out:
            res[pol] = out[:max_cand]
    return res


def _crop_mask(pix, y0, y1, x0, x1):
    """把一组像素点画成一张二值图（裁到外接框）。"""
    import numpy as np
    sub = np.zeros((y1 - y0 + 1, x1 - x0 + 1), dtype=np.float32)
    for (yy, xx) in pix:
        sub[yy - y0, xx - x0] = 1.0
    return sub


def ncc(a, b) -> float:
    """归一化互相关（零均值、单位范数）→ [-1, 1]。"""
    import numpy as np
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a, b = a[:h, :w], b[:h, :w]
    a = a - a.mean()
    b = b - b.mean()
    na = float(np.sqrt((a * a).sum()))
    nb = float(np.sqrt((b * b).sum()))
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    return float((a * b).sum() / (na * nb))


def match_region(gray, templates, min_score=DEFAULT_MIN_SCORE,
                 min_margin=DEFAULT_MIN_MARGIN):
    """候选区反查图标 → 判词。

    返回 {认成, 码点, 语义, 分, margin, 够不够格, 前三名}
    ★ `够不够格` = 分 ≥ min_score **且** margin ≥ min_margin（双闸）。
    ★ **margin 比绝对分更管用**：真图标往往孤峰，误命中往往一片糊
      （原型实测 N3/N5：文字碎片被当成弧线图标，分 0.41~0.46、margin 0.06~0.19）。
    """
    import numpy as np
    by_pol = shape_candidates(gray)
    if not by_pol:
        return {"认成": None, "码点": None, "语义": None, "分": 0.0, "margin": 0.0,
                "够不够格": False, "前三名": [], "说明": "候选区里没找到像图标的连通块"}
    # ★ 两个极性**各算一遍，取整体分更高的那个**（暗图标 vs 亮图标）。
    best_all = {}
    for pol, cands in by_pol.items():
        best = {}
        for name, tpl in templates.items():
            # 模板是**一串**画布（不同切值）⇒ 取最好的一次
            best[name] = max(max(ncc(c, t) for c in cands) for t in tpl)
        for k, v in best.items():
            if v > best_all.get(k, -2.0):
                best_all[k] = v
    per = sorted(((v, k) for k, v in best_all.items()), reverse=True)
    if not per:
        return {"认成": None, "码点": None, "语义": None, "分": 0.0, "margin": 0.0,
                "够不够格": False, "前三名": [], "说明": "模板集是空的"}
    top_score, top_name = per[0]
    # ★ margin 必须在**不同的字形**之间算 ——
    #   同一个码点在两个字体里都有（地球仪 E774 在 mdl2 与 fluent 里都有，
    #   两张模板几乎一样，NCC≈0.997）⇒ 它们是**同一个图标**，不是竞争者
    #   ⇒ 若拿它们算 margin，正确识别反而 margin=0 被双闸否掉（实测踩到）。
    #   ⇒ 第二名只从「与第一名**不是同一个字形**」的模板里取。
    top_tpl = templates.get(top_name) or []
    rival = 0.0
    for s, name in per[1:]:
        tpl = templates.get(name) or []
        same = any(ncc(a, b) > 0.97 for a in top_tpl for b in tpl)
        if same:
            continue
        rival = s
        break
    margin = top_score - rival
    meta = template_meta().get(top_name) or {}
    ok = bool(top_score >= min_score and margin >= min_margin)
    return {"认成": top_name, "码点": meta.get("码点"), "语义": meta.get("语义"),
            "分": round(top_score, 4), "margin": round(margin, 4),
            "够不够格": ok, "阈值": {"分": min_score, "margin": min_margin},
            "前三名": [(n, round(s, 4)) for s, n in per[:3]],
            "说明": ("双闸通过（分 ≥ %.2f 且 margin ≥ %.2f）" % (min_score, min_margin)
                     if ok else
                     "没过双闸（分 %.3f / margin %.3f）⇒ **不许当识别结果用**"
                     % (top_score, margin))}


def match_region_rgb(pixels, templates, min_score=DEFAULT_MIN_SCORE,
                     min_margin=DEFAULT_MIN_MARGIN):
    """同 `match_region`，但输入是 RGB 像素块（np.uint8, H×W×3）。

    ★ 用**三通道最大差**做二值化（不是先转灰度）——
      彩色图标（Windows 11 大量彩色字形）先转灰度会把颜色信息丢掉。
    """
    import numpy as np
    a = np.asarray(pixels)
    if a.ndim == 3:
        g = a.astype(np.float32).max(axis=2) / 255.0
    else:
        g = a.astype(np.float32) / 255.0
    return match_region(g, templates, min_score, min_margin)


def describe_icons(img, boxes, dpi=DEFAULT_DPI, size=16,
                   min_score=DEFAULT_MIN_SCORE, min_margin=DEFAULT_MIN_MARGIN,
                   templates=None):
    """对**已知框**逐个反查图标。

    `boxes` = [(x, y, w, h), ...]（**图像内**坐标，不是屏幕坐标）。
    返回 [{框, 码点, 语义, 分, margin, 够不够格, ...}, ...]

    ★ 这是**「多一条证据」**：不替代 UIA/OCR，也不单独作为动作依据
      （认错一个图标 = 点错一个按钮）。调用方应把它和 UIA 的名字合起来看。
    """
    # ★ 2026-10-23（发版前置 · A 单）：本函数用了 np.asarray，但**函数内没有 numpy**
    #   ⇒ pyflakes 判「undefined name 'np'」（致命类：运行到这行才崩）⇒ 补回。
    #   补法照**本文件房规**：numpy 一律**函数内 import**（本文件另 9 处都这么写），
    #   不动模块级依赖口径 —— numpy 早已在 requirements.txt（>=1.24）里，
    #   本模块的定位就是「纯 numpy + PIL + 系统字体」⇒ **不新增任何依赖**。
    #   （M18d 的 numpy 轻量化是 act.py 判差那条路径，与本模块无关。）
    import numpy as np
    tpl = templates if templates is not None else build_templates(dpi=dpi, size=size)
    out = []
    for (x, y, w, h) in boxes:
        item = {"框": [int(x), int(y), int(w), int(h)]}
        try:
            crop = img.crop((int(x), int(y), int(x) + int(w), int(y) + int(h)))
        except Exception as e:
            item["说明"] = "裁剪失败：%s" % e
            out.append(item)
            continue
        r = match_region_rgb(np.asarray(crop.convert("RGB")), tpl, min_score, min_margin)
        item.update(r)
        out.append(item)
    return out


def describe() -> dict:
    """口径说明（给回执/文档/排查用）。"""
    return {
        "字体": "Segoe MDL2 Assets（segmdl2.ttf）+ Segoe Fluent Icons（SegoeIcons.ttf）—— 系统自带，不装",
        "码点来源": "微软公开文档（Segoe Fluent Icons 图标字体页）",
        "渲染DPI": "按目标窗口 DPI 渲染（默认 %.2f = 150%%）⇒ 笔画结构与屏幕一致" % DEFAULT_DPI,
        "ClearType": "**已关**：PIL/FreeType 默认 FT_RENDER_MODE_NORMAL（灰度抗锯齿，无子像素）",
        "比对": "形状归一化到 %d×%d 画布 → 零均值 NCC（亮度/对比度不变）" % (NORM, NORM),
        "双闸": "分 ≥ %.2f **且** margin ≥ %.2f（margin = 第一名与第二名的分差）"
                % (DEFAULT_MIN_SCORE, DEFAULT_MIN_MARGIN),
        "候选框": "**必须已知**（调用方给框）；满屏乱扫的误判率见 work/iconmatch/README.md §3.5",
        "定位": "本模块**不做定位**（「框在哪」是另一件事）",
        "依赖": "纯 numpy + PIL + 系统字体；**不引新依赖**（不用 scipy / cv2 / 任何模型）",
        "定位边界": "输出是「多一条证据」，**不替代 UIA/OCR**，不单独作为动作依据",
    }
