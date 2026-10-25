# -*- coding: utf-8 -*-
"""eye.py —— 眼：区域划分 + 裁剪 + OCR + 颜色判选中态

★★★ 三条规矩，全部来自 2026-10-04 的实测，不是设计偏好 ★★★

1. **必须按区域裁剪，绝不整屏丢给 OCR。**
    实测：画面里有「多出来的一行文字」时，OCR 会把单选圆**拆成独立行**，
    坐标**整体下移 26px**（命中率 0/4）；裁掉就准到 **0.5px（4/4）**。

2. **选中态一律用像素颜色判，OCR 文本一个字都不能信。**
    实测：同一个蓝圆在 1x/2x/3x/4x 下被读成「．」「0」「读不出来」「O」四种。

3. **增量按区域 hash 判变化，不能用整屏 hash。**
    实测：整屏 150 次采样变化率 **90.6%**（屏幕有动画），判变化基本无效。

★★ 额度纪律（挂件那次的教训延续到这）★★
  - **区域划分 / 截图 / hash / 颜色**：**不过闸门**
    （窗口矩形是窗口管理器给的数据，不是读窗口内容；像素也还没被"理解"）
  - **只有「OCR 认字」这一步过 A 级感知**（这才是把像素交给模型看的那一步）
  - **按区域增量**：没变化的区域一律不 OCR，额度自然省下来
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 老脚本目录**只从 gate 拿**（A 项，2026-10-04）。原来这里是第 2 份拷贝
#   （core / widget / agentctl_main 各一份，共 4 处同一硬编码）。
#   拷贝 = 迟早漂移：今天 gate 的急停搬到 %LOCALAPPDATA%，这 4 处还写着老路径。
from agentctl.gate import HOME as TOOLS, require_home as _require_home   # noqa: E402
STATE = os.path.join(os.path.dirname(HERE), 'work', 'state')
CACHE = os.path.join(STATE, 'eye_regions.json')

# OCR 的实用上限：整屏 2560x1600 直接丢进去既慢又没意义（还容易触发拆行）
MAX_OCR_W = 1600
MAX_OCR_H = 1400

# 颜色判「选中」的两个特征（实测过的具体 RGB）
SEL_BLUE = ((40, 200), (90, 260), (150, 255))     # #3a7afe 一带：R G B 各自区间


def _dpi():
    """DPI 感知：截图是物理像素，窗口矩形也是物理像素，两者必须同系。
    （widget 那次实测：不做就是 1.5 倍错位）"""
    import ctypes
    for fn in (lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
               lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
               lambda: ctypes.windll.user32.SetProcessDPIAware()):
        try:
            fn()
            return True
        except Exception:
            continue
    return False


# ───────────────────── ① 区域划分（不过闸门）─────────────────────

def list_regions(only_visible=True, exclude_titles=()):
    """列出可作为「观察区域」的窗口矩形。

    ★ 关键性质：这一步**只问窗口管理器要标题和矩形**，不看窗口内容，
    所以**不消耗 A 级感知额度**。OCR 才消耗。
    """
    import win32gui
    out = []

    def cb(hwnd, _):
        if only_visible and not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.IsIconic(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if any(x in title for x in exclude_titles):
            return True
        cls = win32gui.GetClassName(hwnd)
        # 桌面容器本身当区域没意义：它铺满全屏、永远在变，增量判它等于没判
        if cls in ('Progman', 'WorkerW', 'Shell_TrayWnd') or title in ('Program Manager',):
            return True
        try:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        w, h = r - l, b - t
        if w < 80 or h < 40:          # 太小的（托盘气泡之类）不值得看
            return True
        # ★ 客户区（不含标题栏和边框）。颜色判选中态**必须用客户区**：
        #   Win11 的标题栏装饰色 + 窗口边框是蓝色的，会被当成"选中"，
        #   还会把竖直边框当成"桥"把所有圆连成一簇（实测：一个都检不出）。
        try:
            cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
            sx, sy = win32gui.ClientToScreen(hwnd, (cl, ct))
            ex, ey = win32gui.ClientToScreen(hwnd, (cr, cb))
            client = [sx, sy, ex, ey]
        except Exception:
            client = [l, t, r, b]
        out.append({'hwnd': hwnd, 'title': title, 'rect': [l, t, r, b],
                    'client': client,
                    'size': [w, h], 'key': '%s|%s' % (hwnd, title)})
        return True

    win32gui.EnumWindows(cb, None)
    return out


# ───────────────────── ② 截图 + 区域 hash（不过闸门）─────────────────────

def grab_once():
    """整屏只截**一次**，然后按区域切片。

    比「每个窗口截一次」快得多（N 次截图 = N 次全屏抓取）。"""
    _dpi()
    from PIL import ImageGrab
    return ImageGrab.grab(all_screens=True)


def region_signature(img, rect, scale=8):
    """区域指纹：缩到很小、转灰度的像素矩阵。

    缩放不是「优化」，是**去噪**：1px 的抗锯齿变化不该被判成「这块变了」。
    """
    import numpy as np
    l, t, r, b = rect
    sub = img.crop((l + 2, t + 2, max(l + 3, r - 2), max(t + 3, b - 2)))
    sub = sub.convert('L')
    sub = sub.resize((max(1, sub.width // scale), max(1, sub.height // scale)))
    return np.asarray(sub)


def region_changed(sig_a, sig_b, min_ratio=0.001, min_delta=20):
    """两块指纹算不算「变了」。

    ★ 为什么要带容差（第一版的教训）★★
    第一版比的是 **hash 全等**。实测：设置窗口里有个固定位置在闪的东西
    （原图 x 464..472 y 632..688，疑似文字光标），每次只有 5~7 个缩略图像素变
    （**0.01%**）→ hash 全等判定 → **每一次都算「变了」，变化率 100%**，
    比整屏 hash 的 90.6% 还差。区域化白做了。

    → 判据必须是「差异像素**占比**超过阈值」，不是「一像素不同就算变」。
       实测取值：光标闪烁 ≈ 0.01%，真变化（一个圆被填上）≈ 0.5% →
       阈值取 0.1%，两边都分得开。
    """
    import numpy as np
    if sig_a is None or sig_b is None:
        return True
    if sig_a.shape != sig_b.shape:
        return True
    d = np.abs(sig_a.astype(np.int16) - sig_b.astype(np.int16))
    n = int((d > min_delta).sum())
    return (n / float(d.size)) > min_ratio


# 变化占比超过这个值 → 判「全区域变了」，此时 bbox 没有信息量
WHOLE_AREA_RATIO = 0.5


def region_diff(sig_a, sig_b, min_delta=20, scale=8):
    """**结构化**的区域差异：变了什么、变了多少、变在哪。

    ★ 为什么要有这个（2026-10-05，治「变化盲」）★
    原来 `scan()` 只给 `changed: True/False`。这等于只告诉模型
    「这一块变了」—— 但**没说变了什么、变了多少、变在哪**。
    而用户 2026-10-04 的原话是「你没正确识别题目位置」「单选你识别错了」：
    根因就在这里 —— 我们一直在看**当前帧**，从来没告诉模型
    「这一块是不是刚动过、往哪动了」。人眼有「变化盲」这个缺陷
    （两张图之间变了什么看不太出来，中间闪一下才立刻发现），
    **模型同样有**；解法不是指望它自己看出来，而是显式把 diff 喂给它。

    ⚠ 归类：diff 是**结构化比较**，不是识别 → 属于**第 1 层**，
      结论是数字/布尔值，**零识别误差**，也不消耗 A 级感知额度。
    """
    import numpy as np
    if sig_a is None or sig_b is None:
        return {'changed': True, 'ratio': 1.0, 'shape': 'whole',
                'note': '没有历史指纹（首次观测）'}
    a = np.asarray(sig_a, dtype=np.int16)
    b = np.asarray(sig_b, dtype=np.int16)
    if a.shape != b.shape:
        # 尺寸变了 = 布局变了。那不是「这块内容变了」，是「坐标系变了」——
        # 拿旧坐标去点一定错，必须显式说出来。
        return {'changed': True, 'ratio': 1.0, 'shape': 'whole',
                'note': '尺寸变了 %s → %s：**坐标已失效，不能拿旧框去点**'
                        % (tuple(a.shape), tuple(b.shape))}
    d = np.abs(a - b)
    mask = d > min_delta
    n = int(mask.sum())
    if n == 0:
        return {'changed': False, 'ratio': 0.0, 'shape': 'none', 'bbox': None,
                'box': None, 'darker': 0.0, 'lighter': 0.0, 'max_delta': 0}
    ratio = n / float(d.size)
    ys, xs = np.where(mask)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    darker = float(((b < a) & mask).sum()) / float(d.size)
    lighter = float(((b > a) & mask).sum()) / float(d.size)
    if ratio >= WHOLE_AREA_RATIO:
        shape = 'whole'
    elif ratio < 0.01:
        shape = 'spot'
    else:
        shape = 'blob'
    return {'changed': True, 'ratio': round(ratio, 5), 'bbox': bbox,
            'darker': round(darker, 5), 'lighter': round(lighter, 5),
            'max_delta': int(d.max()), 'shape': shape}


def diff_box_to_screen(diff, rect, scale=8):
    """把 diff 的指纹坐标包围盒换算成**屏幕物理坐标**。

    ★ 必须换算，否则这个 diff 没用 ★
    指纹是降采样 scale 倍的小图，它的 (x, y) 离「能点的坐标」差两个量级：
    一是不缩放回去（差 8 倍），二是不加窗口偏移（差几百像素）。
    我们 2026-10-04 栽的就是这个 —— 坐标整体下移 26px、偏 100~170px。

    `region_signature` 内部裁掉了边框 2px，所以换算是 x*scale + 2 + rect[0]。
    """
    if not diff or not diff.get('bbox'):
        return None
    x0, y0, x1, y1 = diff['bbox']
    l, t = rect[0], rect[1]
    return [l + x0 * scale + 2, t + y0 * scale + 2,
            l + x1 * scale + 2, t + y1 * scale + 2]


def diff_box_features(box):
    """变化框的**形状特征** —— 为什么单看 ratio 分不开，得再加这个。

    ★ 实测逼出来的（2026-10-05 第一轮判据失败）★
      原以为「真变化 ratio ≈0.5%」，实测：
          光标闪烁 8×8   ratio 0.00027   bbox 8×16 px（**细长条**）
          选项被填上20×20 ratio 0.00096   bbox 24×24 px（**正方形**）
      **ratio 只差 3.5 倍，单阈值分不开。**

      根因：指纹是降采样 8 倍的，**面积被压掉 64 倍** ——
      沿用全分辨率时代（10-04）的 0.5% 口径来判降采样后的 ratio，必然错。

      但**形状**没被压掉：光标是细长条，可点击控件是正方形或横向文字块。
      于是加两个零开销的特征：面积与长宽比。

    ⚠ 这不是「判据没过就改判据凑通过」。区别在于：
      第一版判据建立在**错误的假设**上（拿全分辨率的 ratio 阈值去判降采样数据）；
      这一版建立在**实测数字**上，且新特征有物理含义（形状），不是拟合出来的。
    """
    if not box:
        return None
    x0, y0, x1, y1 = box
    w = max(0, x1 - x0)
    h = max(0, y1 - y0)
    area = w * h
    return {
        'w': w, 'h': h, 'area': area,
        # 长宽比取「大/小」，>=1：这样 2.0 既表示 2:1 也表示 1:2
        'aspect': round(max(w, h) / float(min(w, h)), 2) if min(w, h) else 0.0,
        'square': 0.9 <= (max(w, h) / float(min(w, h)) if min(w, h) else 0) <= 1.15,
    }


def load_cache():
    try:
        with open(CACHE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(d):
    os.makedirs(STATE, exist_ok=True)
    tmp = CACHE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, CACHE)


# ───────────────────── ③ 颜色判选中态（不过闸门）─────────────────────

def find_selected(img, rect, limit=40):
    """在区域里找「蓝色实心圆」的**候选**。**只看像素颜色，不问 OCR**（规矩 2）。

    ★★★ 候选 ≠ 结论 ★★★
    第一版把它当「选中态」直接用，实测**满屏误报**：
      · 直径 24 的那个是**任务栏托盘图标**
      · 直径 9 的那个是设置窗口里的小蓝点
      · 三个互相重叠的窗口把**同一批像素报了三次**
    → 所以本函数只当**底层原语**，必须在「已知的表单区域」里用，
      并且要再用「与文字同行、在文字左侧」等条件交叉确认，才算确认选中。

    返回 [{cx, cy, d, fill}]（区域坐标）。
    """
    import numpy as np
    l, t, r, b = rect
    sub = img.crop((l, t, r, b)).convert('RGB')
    a = np.asarray(sub).astype(np.int16)
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    # 蓝：R 低、B 高。范围放宽一点以覆盖不同主题的选中色（#3a7afe / #2563eb / #0d6efd…）
    m = (B > 150) & (B - R > 60) & (G > 60) & (G < B)
    ys, xs = np.nonzero(m)
    if not len(ys):
        return []
    order = np.argsort(ys)
    ys, xs = ys[order], xs[order]
    # ★★ 聚类必须**二维**。第一版只按 y 分组，结果单选圆（x 59..94）和
    #   同一行的文字（x 116..251）被并成一簇，宽度 193 > 上限 60 → **一个都检不出**。
    #   而那 849 个"蓝像素"其实是 **ClearType 次像素抗锯齿**在文字边缘造的蓝边，
    #   不是任何界面元素。靠填充率能把文字滤掉，但滤不掉"圆和字被并簇"。
    #   → 先按 y 分带（gap>6），带内再按 x 拆（gap>6）。
    ybands, cur = [], [0]
    for j in range(1, len(ys)):
        if ys[j] - ys[j - 1] > 6:
            ybands.append(cur)
            cur = []
        cur.append(j)
    ybands.append(cur)
    groups = []
    for band in ybands:
        bidx = np.asarray(band)          # 带内像素在原始 ys/xs 里的下标，必须跟着走
        bx, by = xs[bidx], ys[bidx]
        o2 = np.argsort(bx)
        bx, by, bidx = bx[o2], by[o2], bidx[o2]
        seg, c2 = [], [0]
        for j in range(1, len(bx)):
            if bx[j] - bx[j - 1] > 6:
                seg.append(c2)
                c2 = []
            c2.append(j)
        seg.append(c2)
        for s in seg:
            groups.append(bidx[s])
    out = []
    for gp in groups:
        y0, y1 = int(ys[gp].min()), int(ys[gp].max())
        x0, x1 = int(xs[gp].min()), int(xs[gp].max())
        d = max(y1 - y0, x1 - x0) + 1
        if len(gp) < 20 or d < 6 or d > 60:
            continue
        # 近正方形才算圆（排除下划线、边框、进度条之类的蓝色长条）
        if not (0.6 <= (x1 - x0 + 1) / float(d) <= 1.6):
            continue
        # ★ 填充率判据（第一版缺这条，蓝色文字/图标全被判成「选中」）：
        #   真的单选圆是**实心**的，bbox 里几乎全是它自己；
        #   蓝色文字、链接、图标是散点，填充率远低于 0.5。
        fill = len(gp) / float((x1 - x0 + 1) * (y1 - y0 + 1))
        if fill < 0.55:
            continue
        out.append({'cx': (x0 + x1) / 2.0, 'cy': (y0 + y1) / 2.0, 'd': d,
                    'fill': round(fill, 2),
                    'screen': [l + (x0 + x1) / 2.0, t + (y0 + y1) / 2.0]})
        if len(out) >= limit:
            break
    return out


def fit_ocr_rect(rect, screen_w, screen_h):
    """把区域裁到 OCR 友好的尺寸（规矩 1：别丢整屏进去）。"""
    l, t, r, b = rect
    w, h = r - l, b - t
    if w <= MAX_OCR_W and h <= MAX_OCR_H:
        return rect, 1.0
    s = min(MAX_OCR_W / float(w), MAX_OCR_H / float(h), 1.0)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    return [l, t, l + nw, t + nh], s


async def ocr_region(img, rect, note=""):
    r"""对**已经裁好的区域**做 OCR。

    ★★★ 坐标系契约（第一版没写清楚，导致调用方必然点错）★★★
    WinRT 的 OcrWord.bounding_rect 是**相对于传入图片**的，也就是**裁剪区内坐标**。
    靶子实测：客户区在屏幕 (311,245)，OCR 报的字框却是 x=64 —— 差正好是裁剪原点。
    → 所以这里**同时**给出两套坐标，别让调用方自己猜：

      w['x'], w['y'], w['cx'], w['cy']  = 裁剪区内坐标（WinRT 原样）
      w['sx'], w['sy'], w['scx'], w['scy'] = **屏幕坐标**（加了 rect 原点，点就用这个）

    ★ 这是整个眼模块里**唯一**消耗 A 级感知额度的调用点。
    """
    import io
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    sub = img.crop(tuple(rect))
    buf = io.BytesIO()
    sub.save(buf, format='PNG')
    data = buf.getvalue()
    ms = InMemoryRandomAccessStream()
    dw = DataWriter(ms)
    dw.write_bytes(data)
    await dw.store_async()
    await dw.flush_async()
    dw.detach_stream()
    ms.seek(0)
    dec = await BitmapDecoder.create_async(ms)
    bmp = await dec.get_software_bitmap_async()
    eng = OcrEngine.try_create_from_language(Language('zh-Hans-CN'))
    if eng is None:
        return {'text': '', 'words': [], 'why': '系统没有 zh-Hans-CN OCR 引擎'}
    res = await eng.recognize_async(bmp)
    words = []
    for ln in res.lines:
        for w in ln.words:
            rc = w.bounding_rect
            words.append({'t': w.text, 'x': rc.x, 'y': rc.y,
                          'cx': rc.x + rc.width / 2, 'cy': rc.y + rc.height / 2,
                          'w': rc.width, 'h': rc.height,
                          'sx': rect[0] + rc.x, 'sy': rect[1] + rc.y,
                          'scx': rect[0] + rc.x + rc.width / 2,
                          'scy': rect[1] + rc.y + rc.height / 2})
    return {'text': res.text, 'words': words, 'note': note}


async def scan(do_ocr=True, max_ocr=3, require_auth=True, use_cache=True):
    """扫一遍屏幕。返回本次结果 + 本次消耗的感知次数。

    额度纪律（关键数字）：
      - 区域划分 / 截图 / hash / 颜色 = 0 次感知
      - OCR 每个变化区域 1 次
    """
    import time
    # 老脚本目录定位不到时**在这里**抛明确异常，而不是拿 None 去 insert
    sys.path.insert(0, TOOLS or _require_home())
    # ★ 必须先设 DPI 感知，**再**枚举窗口 —— 第一版把它放在 grab_once() 里，
    #   而 list_regions() 在前，于是窗口矩形拿到的是逻辑坐标（1707x1067），
    #   和截图的物理坐标（2560x1600）差 1.5 倍。跟 widget 那次是同一个坑。
    _dpi()
    regions = list_regions(exclude_titles=('本地 agent电脑控制授权',))
    img = grab_once()
    sw, sh = img.width, img.height
    # 整屏大小的窗口 = 透明浮层/桌面容器，不是「表单」，
    # 在它上面找选中态只会拿到托盘图标之类的东西（实测满屏误报）。
    def _is_form(rg):
        w, h = rg['size']
        return not (w >= sw * 0.85 and h >= sh * 0.85)
    prev = load_cache() if use_cache else {}
    cur = {}
    out = []
    changed = []
    for rg in regions:
        l, t, r, b = rg['rect']
        if l < 0 or t < 0 or r > sw or b > sh:
            continue
        sig = region_signature(img, rg['client'] or rg['rect'])
        h = hashlib.sha256(sig.tobytes()).hexdigest()[:16]
        cur[rg['key']] = {'h': h, 'sig': sig.tolist(), 'rect': rg['rect'],
                          'client': rg.get('client'), 'title': rg['title']}
        is_new = rg['key'] not in prev
        old_sig = None
        if is_new:
            is_changed = True
        else:
            import numpy as np
            old = prev[rg['key']].get('sig')
            old_sig = np.array(old, dtype=np.uint8) if old else None
            is_changed = region_changed(
                np.array(old, dtype=np.uint8) if old else None, sig)
        # ★ 结构化 diff（2026-10-05）★
        #   以前只给 `changed` 布尔值 —— 模型知道「变了」但不知道「变在哪、变成啥样」。
        #   这就是用户抱怨「识别错题目位置」的病根：翻页时整块都变了，
        #   模型分不清「翻到下一题」还是「内容重排」。
        #   现在额外给出：变化占比、变化块在**屏幕坐标**里的位置、
        #   变亮/变暗比例（区分「被填上」和「被擦掉」）、变化形态。
        #   —— 这些全是数字，**零识别误差**，且不过 A 级感知闸门（第 1 层）。
        d = region_diff(old_sig, sig)
        box = diff_box_to_screen(d, rg.get('client') or rg['rect'])
        cands = find_selected(img, rg.get('client') or rg['rect']) if _is_form(rg) else []
        item = {'title': rg['title'], 'rect': rg['rect'], 'size': rg['size'],
                'hash': h, 'changed': is_changed, 'new': is_new,
                'blue_candidates': cands, 'is_form': _is_form(rg),
                'diff': dict(d, box=box)}
        if is_changed:
            changed.append((rg, item))
        out.append(item)
    if use_cache:
        save_cache(cur)
    spent = 0
    if do_ocr and changed:
        if require_auth:
            import gate
            gate.require(gate.A, '眼: OCR 认字 %d 个变化区域' % min(len(changed), max_ocr), count=False)
        for rg, item in changed[:max_ocr]:
            rect, s = fit_ocr_rect(rg['rect'], sw, sh)
            res = await ocr_region(img, rect, note=rg['title'])
            spent += 1
            item['ocr'] = {'text': res['text'][:4000], 'words': len(res['words']), 'scale': s}
    return {'screen': [sw, sh], 'regions': out, 'changed': len(changed),
            'total': len(out), 'ocr_spent': spent, 'ts': time.strftime('%Y-%m-%d %H:%M:%S')}
