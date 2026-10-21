# -*- coding: utf-8 -*-
r"""framecache.py —— 帧复用 + 变化即停 + 只 diff 变化区（治 D2/D4，**零新依赖**）

★ 这个模块为什么存在
────────────────────
`screen_version` + TTL 早就签发了，但**调用方基本不复用** —— 每步都重扫。
本机实测（见 `docs/实测_响应速度三件套_20221022.md`）：
    UIA 控件树        191 ms / 85 节点
    整窗 mss 抓帧     77.6 ms（2560×1600）
    客户区 PrintWindow 抓帧 ≈ 24 ms（1632×1191 实测中位）
⇒ 一次「观测 → 动作」里，光重复抓帧 + 重扫就是几百毫秒。

★ 四条规矩（全部来自实测，不是设计偏好）
──────────────────────────────────────────
1. **判变化不许用整屏 hash**。实测整屏 hash 变化率 **90.6%**（屏有动画）⇒
   等于「永远在变」，拿它当判据等于没有判据。
2. **判变化不许用「一像素不同就算变」**。实测区域 hash 全等判变化 **100%**
   （记事本光标闪 5~7 px = 0.01% 也算变）⇒ 比整屏 hash 还差。
   ⇒ 判据必须是「差异像素**占比**超阈值**，且**形状**对得上**。
3. **不许拿整窗（带标题栏）当同一张图的判据**。实测同一窗口连抓 3 次：
   整窗 flag=2 三次 sha256 **各不相同**（标题栏 DWM 亚像素噪声，2634 px = 3.43%）；
   客户区 flag=3 三次 sha256 **完全相同**、逐像素 **0 差异**
   （`digest.py` 模块开头口径①，本模块沿用同一口径）。
   ⇒ 本模块的帧**一律取客户区**；本机 12 次连抓实测：相邻两帧瓦片差 **全为 0.000**。
4. **退化画面不许复用**。PrintWindow 对某些硬件加速窗口会返回全黑 ⇒
   全黑指纹恒等 ⇒ 会「永远判没变」⇒ 必须检测退化并**放弃复用（fail open）**。

★ 三值判据（本模块的核心）
────────────────────────────
`changed` 是**三值**，不是布尔：
    True  = 真变了
    False = 确认没变
    None  = **没测出来**（拿不到画面 / 画面退化）
⛔ `None` 永不当 `False` ——「我没看见」与「我看见了没变」是两件事。

★ 两级判据（派单补充 §2 的方案，落到代码）
────────────────────────────────────────────
  **第一级 · 召回**：按目标窗口**客户区**切 64×64 瓦片（**整屏不参与**），
    每瓦片灰度降采样 8×；`|Δ| ≥ ACT_DIFF_STEP` 的采样点**只要有**就列候选
    （阈值取 0 ⇒ 只管召回，不管精度）。
  **第二级 · 精度**：**只在候选瓦片里**做全分辨率求差 → 切连通块 →
    按「光标形状」与「最小面积」两条规则判真假。
    ⇒ 4.1 MP 的整幅求差变成几十块 64×64，实测省一个数量级。
  **滞回**：候选瓦片**连续 2 帧**都超阈才上报 ⇒ 光标闪烁只闪一帧就过去，不误报。
  **代价不对称**：误报多花一次裁剪 OCR（42–58 ms）+ 一次 UIA（191 ms），**可吃**；
    漏报会让动作变 unknown 或点错窗，**不可吃** ⇒ 阈值宁可偏敏感。

★ 坐标系口径
────────────
本模块的坐标一律是**客户区像素**（与 `digest.py` 同一口径：PrintWindow flag=3）。
本机是 **150% DPI**：unaware 进程拿的是逻辑坐标、钩子/截图给的是物理像素，差 1.5 倍。
⇒ 本模块**自己**保证 DPI 感知（`_ensure_dpi_aware`），并且**绝不乘 dpi_scale**
  （客户区在感知进程里本来就是物理像素，再乘一次就是重复缩放）。

★ 不引新依赖
────────────
分块用**纯 numpy + Python**（行内连续段 + 并查集），
**不用 scipy**（scipy 不在 requirements.txt 里，进了产品就是给安装包加体积）。
"""
from __future__ import annotations

import time

# ── 判据口径常量（**唯一真相源**：core.py 的 `_act_diff_of` 与 act.py 的就绪轮询共用）──
#
# ★ 这几个数字是**复用现成的**（裁决 B2「主线走复用路线」）：
#   它们原本住在 `core.py` 的 `ACT_DIFF_STEP` / `ACT_DIFF_IGNORE_DEFAULT`，
#   本模块把它们**搬到这里**并让 core 反过来引用 ⇒ 全仓只有**一份**定义，
#   不会出现「core.py 说没变、act.py 说变了」的自相矛盾。
DIFF_STEP = 2             # 阈值 2/255（= core.ACT_DIFF_STEP）
DIFF_CARET_PX = 8         # 窄边 ≤ 此值 ⇒ **可能**是光标（还要看长宽比）
DIFF_MIN_AREA = 24        # 真变化的最小面积 px²（= core.ACT_DIFF_IGNORE_DEFAULT.min_area_px）
DIFF_CARET_ASPECT = 2.0   # 窄边 ≤ DIFF_CARET_PX **且** 长宽比 ≥ 此值 ⇒ 当光标丢掉
HYSTERESIS = 2            # **连续 N 帧**同一结论才认（= 派单补充 §2 的「时间滞回」）

# ── 判据参数（全部可被调用方覆盖）────────────────────────────────────────
TILE = 64                  # 瓦片边长（客户区像素）——**阶段二增强**才用
TILE_SCALE = 8             # 每瓦片降采样倍数 → 64×64 瓦片变 8×8 = 64 个采样点
TILE_STEP = DIFF_STEP      # 单采样点灰度差阈值（= 主线判据的阈值 2/255）
CARET_PX = DIFF_CARET_PX   # 窄边上限（= 老口径 caret_px=8）
CARET_ASPECT = DIFF_CARET_ASPECT   # 窄边 ≤ CARET_PX 且长宽比 ≥ 此值 ⇒ 当光标丢掉
MIN_AREA_PX = DIFF_MIN_AREA        # 真变化的最小面积（与 core.ACT_DIFF_IGNORE_DEFAULT 同值）
NOISE_FRAMES = 10          # 估噪声地板的采样帧数
NOISE_WINDOW_S = 1.0       # 估噪声地板的时间窗（秒）

#: 画面退化判定：灰度标准差低于此值 ⇒ 「没有画面」（与 digest._BLANK_STDDEV 同一条规矩）
BLANK_STDDEV = 3.0

#: 帧复用 TTL（秒）。与 `gate.CAPTURE_TTL` 对齐 —— 凭据 15 秒过期，复用不得比它更久。
FRAME_TTL_SEC = 15.0

#: 「动作前」那一帧允许的**最大年龄**（秒）。
#: 比凭据 TTL（15 s）短得多：这一帧要代表「动作前那一瞬」，
#: 太老 ⇒ 求差会把「动作之前就发生的变化」算成「我这次动作造成的」⇒ 判词失真。
#: ⚠ 这只影响**证据与判词**的质量，不影响安全性（安全性由闸门的凭据校验管）。
FRAME_REUSE_MAX_AGE_SEC = 5.0

#: 变化即停的默认预算（毫秒）。⛔ **不是把等待砍成 0**：
#: 固定等待要么白等（界面早变了）、要么不够（界面还没变）；
#: 这里改成「轮询指纹，变了就停，最多等这么久」。
WAIT_BUDGET_MS = 300
WAIT_POLL_MS = 25

#: 第一探测**之前**的宽限期（毫秒）。
#: ★ 为什么要有：动作发出后，界面往往还要再重绘一次（实测记事本：
#:   点击后菜单栏的「文件」二字**晚约 40 ms** 才重画完）。
#: 不留宽限期 ⇒ 第一探测落在重绘之前 ⇒ 必然「没变」⇒ 白白多探一轮。
#: 留 50 ms ⇒ 第一探测就落在重绘之后 ⇒ 常态下**只探一次**。
#: ⚠ 这**不是**把固定 settle 砍成 0：宽限期后仍然是**自适应轮询**（变了就停）。
WAIT_GRACE_MS = 50

_dpi_ready = False

#: 计数器（判据要用它数「这次动作抓了几次帧」）
#: ★ 三种计数**不许混**：证据帧（留作 diff/落盘的）、指纹探测帧（变化即停的探针）、
#:   观测帧（看屏时拍的）。派单补充 §2 说的「指纹 6.45ms/次」与「抓帧」是两种东西。
STATS = {"客户区抓帧": 0, "整窗抓帧": 0, "复用命中": 0, "重扫": 0,
         "动作前抓帧": 0, "动作后留帧": 0, "指纹探测": 0}


def reset_stats():
    for k in STATS:
        STATS[k] = 0
    return dict(STATS)


# ── DPI 感知（口径与 digest.py 完全一致）────────────────────────────────
def _ensure_dpi_aware() -> None:
    """尽力把进程设成 PER_MONITOR_AWARE ⇒ 客户区尺寸是**物理像素**。

    ★ 与 `digest._ensure_dpi_aware` **同一套调用**（shcore.SetProcessDpiAwareness(2)
      → 退回 user32.SetProcessDPIAware()）：两处各设各的会让坐标系打架。
    ★ 只在进程启动早期设一次有效；已经建过窗口后再设会失败 —— 那就沿用现状，
      不报错、**不假装成功**。
    """
    global _dpi_ready
    if _dpi_ready:
        return
    _dpi_ready = True
    try:
        import ctypes                                     # noqa: PLC0415
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except Exception:
        pass
    try:
        import ctypes                                     # noqa: PLC0415
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ── 画面抓取（**唯一来源** = digest.py，不另开一条）─────────────────────
def capture_client(hwnd: int):
    """抓目标窗**客户区** → PIL RGB 图。拿不到返回 None（**绝不返回空白图冒充**）。

    ★ 口径与 `digest.window_digest` **完全一致**（PrintWindow flag=3、无 ImageGrab 退路）
      ⇒ 同一窗口连抓两次逐像素相同（实测）⇒ 抓来的图可以直接当「同一张图」用。
    ★ 退化画面（全黑/纯色）在这里就被识别出来 —— 指纹恒等的退化图**绝不能**进复用。
    """
    try:
        from agentctl import digest                       # noqa: PLC0415
    except Exception:
        return None
    _ensure_dpi_aware()
    try:
        img = digest._capture_client_rgb(int(hwnd))
    except Exception:
        return None
    if img is None or is_blank(img):
        return None
    STATS["客户区抓帧"] = int(STATS.get("客户区抓帧") or 0) + 1
    return img


def is_blank(img) -> bool:
    """整块画面「没有内容」：灰度标准差 < BLANK_STDDEV。

    ★ 与 `digest._looks_blank` 同一条规矩、同一阈值 —— 两处各判各的会让
      「这张图能不能进复用」出现两套答案。
    """
    if img is None:
        return True
    try:
        from PIL import ImageStat                        # noqa: PLC0415
        return float(ImageStat.Stat(img.convert("L")).stddev[0]) < BLANK_STDDEV
    except Exception:
        return True


def client_origin(hwnd: int):
    """客户区原点（**屏幕物理像素**）。拿不到返回 None。

    ★ 只在进程已 DPI 感知时才是物理像素 —— 本模块 `_ensure_dpi_aware()` 保证了这一点。
      **绝不乘 dpi_scale**：感知进程里 GetClientRect 本来就给物理像素。
    """
    try:
        import win32gui                                  # noqa: PLC0415
        _ensure_dpi_aware()
        hwnd = int(hwnd)
        l, t, r, b = win32gui.GetClientRect(hwnd)
        if r - l <= 0 or b - t <= 0:
            return None
        sl, st = win32gui.ClientToScreen(hwnd, (l, t))
        return (int(sl), int(st))
    except Exception:
        return None


# ── 瓦片指纹（**向量化**：整幅降采样一次，再切成瓦片）──────────────────
def _downsample_rgb(img, scale=TILE_SCALE):
    """整幅图 → 块均值（BOX）后的 (h, w, 3) int16 数组。

    ★ 为什么保留**三通道**而不是先转灰度：
      老口径（core._act_diff_of）比的是 `ImageChops.difference(RGB).convert("L")`
      —— 那是「差值的亮度」，不是「亮度的差值」。
      两者的差别是**颜色变化**：红(255,0,0) → 蓝(0,0,255) 在老口径下差值亮度 ≈76
      （判为变了），在「先转灰度再相减」下差值 = |76-76| = 0（**漏掉**）。
      ⇒ 本模块按**三通道最大差**判，与老口径一致：颜色变化不会被漏掉。
    ★ 为什么用 PIL 的 **BOX**（而不是 numpy reshape-mean）：
      实测 1632×1191 上 reshape-mean 要 **8.0 ms**、PIL BOX 只要 **3.0 ms**；
      而这一步每个动作要跑两次（动作前 + 动作后）⇒ 差 10 ms。
      BOX 与 reshape-mean 都是「块均值」，口径一致。
    ★ 边缘用 **edge 复制** 补齐 ⇒ 两次同尺寸的图补出来的内容完全一致
      （补法不同会让「没变」判成「变了」）。
    """
    import numpy as np
    if img is None:
        return None
    try:
        from PIL import Image                           # noqa: PLC0415
        w, h = img.size
        if w < 1 or h < 1:
            return None
        small = img.resize((max(1, w // scale), max(1, h // scale)), Image.BOX)
        arr = np.asarray(small, dtype=np.int16)
    except Exception:
        return None
    if arr.size == 0:
        return None
    if arr.ndim == 2:
        arr = arr[:, :, None]
    return arr


def tiles_array(img, tile=TILE, scale=TILE_SCALE, channels=True):
    """整幅客户区 → 形状 (瓦片行, 瓦片列, ts, ts[, 3]) 的 int16 数组。

    ★ 降采样不是「优化」，是**去噪**：1px 的抗锯齿变化不该被判成「这块变了」。
    """
    import numpy as np
    arr = _downsample_rgb(img, scale)
    if arr is None:
        return None
    ts = max(1, tile // scale)
    qh, qw = (-arr.shape[0]) % ts, (-arr.shape[1]) % ts
    if qh or qw:
        arr = np.pad(arr, ((0, qh), (0, qw), (0, 0)), mode="edge")
    hs, ws = arr.shape[0], arr.shape[1]
    n_r, n_c = hs // ts, ws // ts
    if not channels:
        arr = arr.mean(axis=2)
    blocks = arr.reshape(n_r, ts, n_c, ts, arr.shape[2]).transpose(0, 2, 1, 3, 4)
    return blocks.astype(np.int16)


def tiles_of(img, tile=TILE, scale=TILE_SCALE):
    """整幅客户区 → {瓦片键: 降采样**灰度**矩阵}（`tiles_array` 的字典版）。

    ★ 键 = (瓦片行, 瓦片列)。像素坐标换算见 `tile_box`。
    ★ 灰度 = 三通道均值（只给「看看像不像」的场合用；
      **判变化一律走三通道最大差**，见 `diff_images`）。
    """
    arr = tiles_array(img, tile, scale, channels=False)
    if arr is None:
        return {}
    n_r, n_c = arr.shape[0], arr.shape[1]
    return {(r, c): arr[r, c] for r in range(n_r) for c in range(n_c)}


def tile_box(key, tile=TILE, scale=TILE_SCALE):
    """瓦片键 → 客户区像素框 (x0, y0, x1, y1)（右开）。"""
    r, c = key
    return (c * tile, r * tile, c * tile + tile, r * tile + tile)


# ── 全分辨率求差 + 分块（第二级：判「真变了」的精度闸）──────────────────
def blocks_from_mask(mask, ox=0, oy=0):
    """二值 mask（numpy bool）→ [(宽, 高, 面积, (x0,y0,x1,y1))]（**闭区间**）。

    ★ 纯 numpy + Python，**不引 scipy**（scipy 不在 requirements.txt 里，
      进了产品就是给安装包加体积 —— 本单边界：不许引新依赖）。
    ★ 行列都按**闭区间**（含末端像素），所以宽高要 +1 ——
      与 core._act_diff_blocks 同一口径（那处是 K4b 实测踩出来的）。
    """
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


def merge_blocks(blocks, gap=1):
    """把**跨瓦片边界**被切开的块接回去。

    ★ 为什么必须接：分块是在**候选瓦片里**做的（64×64），
      一根跨越瓦片边界的光标条会被切成两段 ——
      上面那截 7×4（长宽比 1.75 < 2 ⇒ **不像光标**）⇒ 逃过光标规则
      ⇒ 实测：7×24 的光标条被判成「变了」（判据 A2 红）。
      ⇒ 把**相邻（间隙 ≤ gap）**的块并回一个，再判光标/面积。
    """
    if len(blocks) <= 1:
        return list(blocks)
    items = [[int(b[0]), int(b[1]), int(b[2]),
              int(b[3][0]), int(b[3][1]), int(b[3][2]), int(b[3][3])] for b in blocks]
    changed = True
    while changed and len(items) > 1:
        changed = False
        out = []
        used = [False] * len(items)
        for i in range(len(items)):
            if used[i]:
                continue
            a = items[i]
            used[i] = True
            acc = list(a)
            for j in range(i + 1, len(items)):
                if used[j]:
                    continue
                b = items[j]
                if (a[3] - gap <= b[5] and b[3] - gap <= a[5]
                        and a[4] - gap <= b[6] and b[4] - gap <= a[6]):
                    used[j] = True
                    changed = True
                    acc[0] = max(acc[0], b[0])
                    acc[1] = max(acc[1], b[1])
                    acc[2] = acc[2] + b[2]
                    acc[3] = min(acc[3], b[3])
                    acc[4] = min(acc[4], b[4])
                    acc[5] = max(acc[5], b[5])
                    acc[6] = max(acc[6], b[6])
                    a = acc
            out.append((acc[0], acc[1], acc[2], (acc[3], acc[4], acc[5], acc[6])))
        # out 里是 (w,h,area,bbox) 四元组 ⇒ 摊平成 7 元组再继续
        items = [[o[0], o[1], o[2], o[3][0], o[3][1], o[3][2], o[3][3]] for o in out]
    return [(o[0], o[1], o[2], (o[3], o[4], o[5], o[6])) for o in items]


def is_caret_like(w, h, caret_px=CARET_PX, aspect=CARET_ASPECT):
    """这块变化是不是「光标类」（细长条）？

    ★ 与老口径（core._act_diff_of：`min(w,h) > caret_px` ⇒ 窄边 ≤8 一律丢）的差别：
      老口径连**8×8 的方块**也一起丢掉了（窄边=8 ⇒ 不 >8 ⇒ 丢），
      而派单补充 §2 明写「塞一个 8px 方块**必须**报变」。
      ⇒ 改成**形状**判据：光标是**细长条**（窄边 ≤ caret_px 且长宽比 ≥ aspect），
        方块（长宽比 ≈ 1）不算光标。
        这条与 eye.py `diff_box_features` 记的是同一件事：
        「ratio 只差 3.5 倍，单阈值分不开；但**形状**没被压掉」。
    """
    mn, mx = min(w, h), max(w, h)
    if mn <= 0:
        return True
    if mn > caret_px:
        return False
    return (mx / float(mn)) >= aspect


def diff_images(before, after, caret_px=DIFF_CARET_PX, min_area_px=DIFF_MIN_AREA,
                step=DIFF_STEP):
    """**主线判据**：两幅客户区图求差 ⇒ 三值结果。

    ★★★ 这就是 `core._act_diff_of` 的那套判据（**复用现成**，裁决 B2）★★★：
      · 阈值 2/255（`ImageChops.difference` → 亮度 → 阈值）
      · **分块**（行内连续段 + 并查集）——不是只看包围盒
      · **窄边 caret 滤除**
      · **最小面积 24px²**
      · 返回**三值** `changed`（True / False / **None=没测出来**）
      ⇒ 它已经从「动作后验」被提成「帧复用的脏区判定器」。

    ★ 与老口径的**唯一**差别：caret 滤除从「窄边 ≤8 一律丢」改成
      「窄边 ≤8 **且** 长宽比 ≥2.0 才丢」——
      因为老口径连 **8×8 的方块**也丢了，而派单补充 §2 明写「塞一个 8px 方块必须报变」。
      光标条（5×16 / 7×24）长宽比 ≥2 ⇒ 照样被丢掉 ✓（与 eye.py `diff_box_features`
      记的是同一件事：「ratio 分不开，但**形状**分得开」）。

    返回 {changed, ratio, bbox, blocks, 说明}
      changed ∈ True / False / None（None = 没测出来，**永不当 False**）
      bbox    = **客户区像素**闭区间框 (x0, y0, x1, y1)；None = 没变
    """
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
        from PIL import ImageChops                        # noqa: PLC0415
        _d = ImageChops.difference(before, after).convert("L")
        _mask = _d.point(lambda v: 255 if v >= step else 0)
    except Exception as e:
        return {"changed": None, "ratio": 0.0, "bbox": None, "blocks": [],
                "说明": "求差失败：%s" % e}
    if not _mask.getbbox():
        return {"changed": False, "ratio": 0.0, "bbox": None, "blocks": [],
                "说明": "逐像素比对无变化（阈值 %d/255）" % step}
    m = np.asarray(_mask) > 0
    blocks = blocks_from_mask(m)
    live = [b for b in blocks if not is_caret_like(b[0], b[1], caret_px)]
    dropped = len(blocks) - len(live)
    total = sum(b[2] for b in live)
    if (not live) or (min_area_px and total < min_area_px):
        why = ("共 %d 块变化（其中 %d 块窄边≤%d 且长宽比≥%.1f 判为光标类），"
               "剩余面积 %d px2 ≤ min_area_px=%d ⇒ 判为没变"
               % (len(blocks), dropped, caret_px, DIFF_CARET_ASPECT, total, min_area_px)
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


def diff_images_tiled(before, after, caret_px=DIFF_CARET_PX, min_area_px=DIFF_MIN_AREA,
                      step=DIFF_STEP, tile=TILE, scale=TILE_SCALE, area=None):
    """**阶段二增强**（不在主线上）：64×64 瓦片 + 降采样 8× 的两级判据。

    ★ 为什么它只是「增强」而不是主线（裁决 B2）：
      主线是**复用现成**的 `_act_diff_of`（本机铁律：最小代价）。
      本函数的价值在**超大窗口**：先在降采样层把「明显没变」的那些**快速排除**，
      只在候选瓦片里做全分辨率分块 ⇒ 4.1 MP 的整幅求差（实测 19~22 ms）可以省掉。
    ★ 口径与主线**完全一致**（同一组常量、同一套 caret 规则、同一套三值）⇒
      两条路给出的结论不会互相打架。
    ★ 实测：本机 1632×1191 端到端 7.6 ms（主线 9.1 ms）⇒
      收益**有限**（1.2×）⇒ 主线够用，本函数按需启用。
    """
    import numpy as np
    if before is None or after is None:
        return {"changed": None, "ratio": 0.0, "bbox": None, "blocks": [],
                "候选瓦片": 0,
                "说明": "取不到画面（窗口不可读 / 截图失败）⇒ 没测出来，不等于没变"}
    if before.size != after.size:
        w, h = max(before.size[0], after.size[0]), max(before.size[1], after.size[1])
        return {"changed": True, "ratio": 1.0, "bbox": (0, 0, w - 1, h - 1),
                "blocks": [], "候选瓦片": 0,
                "说明": "动作前后画面尺寸变了（%s → %s）" % (before.size, after.size)}
    ta, tb = tiles_array(before, tile, scale), tiles_array(after, tile, scale)
    if ta is None or tb is None or ta.shape != tb.shape:
        return {"changed": True, "ratio": 1.0, "bbox": (0, 0, before.size[0] - 1,
                                                         before.size[1] - 1),
                "blocks": [], "候选瓦片": 0,
                "说明": "指纹尺寸变了 ⇒ 坐标系变了，不能拿旧坐标去点"}
    d = np.abs(ta.astype(np.int32) - tb.astype(np.int32)).max(axis=4)
    hit = (d >= step).sum(axis=(2, 3)) > 0
    cand = np.argwhere(hit)
    if cand.shape[0] == 0:
        return {"changed": False, "ratio": 0.0, "bbox": None, "blocks": [],
                "候选瓦片": 0,
                "说明": "逐瓦片比对无变化（三通道最大差阈值 |Δ|≥%d）" % step}
    bw, bh = before.size
    H, W = bh, bw
    blocks = []
    for r, c in cand:
        y0, x0 = int(r) * tile, int(c) * tile
        y1, x1 = min(H, y0 + tile), min(W, x0 + tile)
        if y1 <= y0 or x1 <= x0:
            continue
        try:
            ga = np.asarray(before.crop((x0, y0, x1, y1)), dtype=np.int16)
            gb = np.asarray(after.crop((x0, y0, x1, y1)), dtype=np.int16)
        except Exception:
            continue
        if ga.shape != gb.shape:
            continue
        sub = (np.abs(ga - gb).max(axis=2) >= step)
        if not sub.any():
            continue
        blocks.extend(blocks_from_mask(sub, ox=x0, oy=y0))
    blocks = merge_blocks(blocks)
    live = [b for b in blocks if not is_caret_like(b[0], b[1], caret_px)]
    dropped = len(blocks) - len(live)
    total = sum(b[2] for b in live)
    if area is None:
        area = float(H * W)
    if (not live) or (min_area_px and total < min_area_px):
        why = ("变化面积 %d px2 ≤ min_area_px=%d ⇒ 判为没变" % (total, min_area_px)
               if live else
               ("共 %d 块变化，全部是光标类（窄边≤%d 且长宽比≥%.1f）⇒ 判为没变"
                % (len(blocks), caret_px, DIFF_CARET_ASPECT)))
        return {"changed": False, "ratio": 0.0, "bbox": None, "blocks": blocks,
                "候选瓦片": int(cand.shape[0]), "忽略为": True, "说明": why,
                "丢弃光标块": dropped}
    x0 = min(b[3][0] for b in live)
    y0 = min(b[3][1] for b in live)
    x1 = max(b[3][2] for b in live)
    y1 = max(b[3][3] for b in live)
    return {"changed": True, "ratio": round(total / float(area or 1), 6),
            "bbox": (x0, y0, x1, y1), "blocks": blocks, "候选瓦片": int(cand.shape[0]),
            "丢弃光标块": dropped,
            "说明": "变化区（客户区 %d,%d 起 %dx%d；%d 块合计 %d px2）"
                    % (x0, y0, x1 - x0 + 1, y1 - y0 + 1, len(live), total)}


class ChangeTracker:
    """连续帧判变化（滞回）：**连续 N 帧**都超阈才报「真变了」。

    ★ 为什么要有滞回：光标闪烁只闪一帧就过去 ⇒ 单帧判据会把它报成变化。
      连续 2 帧都超阈 ⇒ 真变化（界面重绘）才会连续命中。
    """

    def __init__(self, need=HYSTERESIS):
        self.need = max(1, int(need))
        self.streak = 0

    def feed(self, before_img, after_img, **kw):
        """喂一帧（前图、后图），返回本帧的判定结果（`changed` 含滞回）。"""
        d = diff_images(before_img, after_img, **kw)
        raw = d.get("changed")
        if raw is True:
            self.streak += 1
        else:
            self.streak = 0
        out = dict(d)
        out["raw_changed"] = raw
        out["streak"] = self.streak
        if raw is True and self.streak >= self.need:
            out["changed"] = True
            out["滞回"] = "连续 %d 帧超阈才上报" % self.need
        else:
            out["changed"] = False if raw is True else raw
            if raw is True:
                out["说明"] = ("候选瓦片 %d 块，但只连续命中 %d 帧（要 %d 帧）⇒ 本帧不报变"
                               % (d.get("候选瓦片", 0), self.streak, self.need))
        return out

    def reset(self):
        self.streak = 0


def wait_stable(hwnd: int, baseline=None, budget_ms=600, poll_ms=30,
                 need=HYSTERESIS, img=None, capture=None, **kw):
    """轮询画面，直到**连续 `need` 帧都判「没变」** ⇒ 界面稳定/就绪。

    ★ 这就是「**连续 2 帧滞回**」那条铁律的实现（裁决 B2 / 派单补充 §2）：
      · 一次读数只能说明「这一瞬间」；**连续两次一致**才说明「稳了」。
      · `act.py:2329-2331` 早就有这个雏形（「隔 0.25 秒两次读数完全一致才动手」），
        本函数把它系统化，并**换掉整文件 md5**（那是「整屏 hash 90.6% 恒变」的成因）：
        判据用**主线那套**（`diff_images`：阈值 2/255 + 窄边 caret 滤除 + 最小面积 24px²）。
    ★ `capture` 可注入（判据用合成图驱动；默认取客户区抓帧）。
    ★ 返回 {稳定, 帧数, 耗时ms, 图, 说明}
      稳定 ∈ True（稳了）/ False（预算内没稳）
      ⛔ 没稳**不等于**「变了」，也**不等于**「没变」⇒ 调用方必须自己决定
        报「没测出来」(None) 还是继续等。
    """
    t0 = time.perf_counter()
    if baseline is None:
        baseline = img
    if baseline is None:
        return {"稳定": False, "帧数": 0, "耗时ms": 0.0, "图": None,
                "说明": "没有基线帧 ⇒ 没测出来，不等于稳了"}
    grab = capture or (lambda _h: capture_client(_h))
    streak = 0
    rounds = 0
    last = None
    deadline = t0 + max(0.0, float(budget_ms) / 1000.0)
    while True:
        rounds += 1
        cur = grab(hwnd)
        if cur is None:
            return {"稳定": False, "帧数": rounds, "耗时ms": (time.perf_counter() - t0) * 1000.0,
                    "图": last,
                    "说明": "抓不到客户区画面 ⇒ 没测出来，不等于稳了"}
        last = cur
        d = diff_images(baseline, cur, **kw)
        if d.get("changed") is False:
            streak += 1
        else:
            streak = 0
        if streak >= max(1, int(need)):
            return {"稳定": True, "帧数": rounds, "耗时ms": (time.perf_counter() - t0) * 1000.0,
                    "图": last, "diff": d,
                    "说明": "连续 %d 帧判「没变」⇒ 稳定（第 %d 轮，%.1f ms）"
                            % (streak, rounds, (time.perf_counter() - t0) * 1000.0)}
        if time.perf_counter() >= deadline:
            return {"稳定": False, "帧数": rounds, "耗时ms": (time.perf_counter() - t0) * 1000.0,
                    "图": last, "diff": d,
                    "说明": "%.0f ms 内没有连续 %d 帧一致 ⇒ **没测出来**（不是「变了」，"
                            "也不是「没变」）" % (budget_ms, max(1, int(need)))}
        time.sleep(max(0.0, float(poll_ms) / 1000.0))


# ── 变化即停（替代「固定等待」；⛔ 不是把等待砍成 0）──────────────────────
def wait_change(hwnd: int, before_img, budget_ms=WAIT_BUDGET_MS,
                poll_ms=WAIT_POLL_MS, grace_ms=WAIT_GRACE_MS, tracker=None, **kw):
    """轮询画面，**变了就停**。返回 {changed, 耗时ms, 轮次, 图, 说明}。

    ★ 这不是「把固定 sleep 砍成 0」：固定等待要么白等（界面早变了）、
      要么不够（界面还没变）。这里是「等到真的变了，最多等 budget_ms」。
    ★ **第一轮抓的那张图就是「动作后」那张** ⇒ 界面已经变了时，
      整条动作只多抓 **1** 次帧（而不是「固定等 450ms 再抓」）。
    ★ `before_img` 为空 ⇒ 没法判「变没变」⇒ 直接返回 changed=None（不假装）。
    """
    t0 = time.perf_counter()
    tr = tracker or ChangeTracker()
    rounds = 0
    if before_img is None:
        return {"changed": None, "耗时ms": 0.0, "轮次": 0, "图": None,
                "说明": "没有「动作前」那一帧 ⇒ 没测出来，不等于没变"}
    deadline = t0 + max(0.0, float(budget_ms) / 1000.0)
    last = None
    if grace_ms > 0:
        # ★ 宽限期：让界面把动作后的重绘落完（见 WAIT_GRACE_MS 的理由）。
        time.sleep(max(0.0, float(grace_ms) / 1000.0))
    while True:
        rounds += 1
        # 探测帧是**指纹探测**，不是「证据帧」—— 最后一轮那张才留下来当「动作后」。
        STATS["指纹探测"] = int(STATS.get("指纹探测") or 0) + 1
        cur = capture_client(hwnd)
        if cur is None:
            return {"changed": None, "耗时ms": (time.perf_counter() - t0) * 1000.0,
                    "轮次": rounds, "图": last,
                    "说明": "抓不到客户区画面 ⇒ 没测出来，不等于没变"}
        last = cur
        d = tr.feed(before_img, cur, **kw)
        if d.get("changed") is True:
            return {"changed": True, "耗时ms": (time.perf_counter() - t0) * 1000.0,
                    "轮次": rounds, "图": last, "diff": d,
                    "说明": "第 %d 轮检测到变化即停（%.1f ms）"
                            % (rounds, (time.perf_counter() - t0) * 1000.0)}
        if time.perf_counter() >= deadline:
            return {"changed": False, "耗时ms": (time.perf_counter() - t0) * 1000.0,
                    "轮次": rounds, "图": last, "diff": d,
                    "说明": "轮询 %d 轮、%.1f ms 内未见超阈变化 ⇒ 判为没变"
                            % (rounds, (time.perf_counter() - t0) * 1000.0)}
        time.sleep(max(0.0, float(poll_ms) / 1000.0))


# ── 帧仓库（按凭据 token 存「那张观测帧」）──────────────────────────────
class FrameStore:
    """观测帧仓库：token → {hwnd, 图, 时间, 客户区原点, 来源}。

    ★ 为什么按 token 存而不是按 hwnd 存：按 hwnd 存会把**不同观测**混成一张，
      「拿旧截图去点新画面」那类事故就是这么来的。按 token 存 ⇒ 帧与凭据**一一对应**，
      复用时还能顺带核 hwnd / 客户区原点是否一致。
    ★ TTL = `FRAME_TTL_SEC`（15 s，与 `gate.CAPTURE_TTL` 对齐）——
      凭据 15 秒就过期，帧比它活得久就是危险。
    """

    def __init__(self, ttl_sec=FRAME_TTL_SEC, max_items=64):
        self.ttl = float(ttl_sec)
        self.max_items = int(max_items)
        self._d = {}

    def put(self, token, hwnd, img, origin=None, src=""):
        if not token or img is None:
            return False
        self._d[str(token)] = {
            "hwnd": int(hwnd), "img": img, "t": time.time(),
            "origin": origin, "src": src,
        }
        if len(self._d) > self.max_items:
            for k in sorted(self._d, key=lambda x: self._d[x]["t"])[:len(self._d) - self.max_items]:
                self._d.pop(k, None)
        return True

    def get(self, token, hwnd=None):
        if not token:
            return None
        rec = self._d.get(str(token))
        if not rec:
            return None
        if time.time() - float(rec.get("t") or 0) > self.ttl:
            self._d.pop(str(token), None)
            return None
        if hwnd is not None and int(rec["hwnd"]) != int(hwnd):
            return None
        return rec

    def age_sec(self, token):
        rec = self._d.get(str(token)) or {}
        return time.time() - float(rec.get("t") or 0)

    def drop(self, token):
        self._d.pop(str(token), None)

    def clear(self):
        self._d.clear()

    def stats(self):
        return {"条数": len(self._d),
                "最老秒": round(max([time.time() - float(v.get("t") or 0)
                                     for v in self._d.values()] or [0]), 2)}


#: 进程级帧仓库（与 `core._SCREEN_SEEN` 同生命周期：进程内、重启即失）。
STORE = FrameStore()


def describe() -> dict:
    """口径说明（给回执/文档/排查用）。"""
    return {
        "画面口径": "客户区 PrintWindow(PW_CLIENTONLY|PW_RENDERFULLCONTENT=3)，"
                    "无 ImageGrab/mss 退路（与 digest.py 同一来源）",
        "坐标系": "客户区像素；本机 150% DPI，进程强制 PER_MONITOR_AWARE ⇒ 物理像素，"
                  "**不乘 dpi_scale**",
        "两级判据": "第一级 64×64 瓦片降采样 %d×（召回，阈值 0）；"
                    "第二级 只在候选瓦片里全分辨率分块（精度）" % TILE_SCALE,
        "光标规则": "窄边 ≤ %d **且** 长宽比 ≥ %.1f ⇒ 当光标丢掉；"
                    "方块（比≈1）不算光标 ⇒ 8×8 的方块仍报变" % (CARET_PX, CARET_ASPECT),
        "最小面积": "%d px2（与 core.ACT_DIFF_IGNORE_DEFAULT 同值）" % MIN_AREA_PX,
        "滞回": "连续 %d 帧都超阈才上报（压光标闪烁）" % HYSTERESIS,
        "三值": "changed ∈ True/False/None；None = 没测出来，**永不当 False**",
        "退化": "灰度标准差 < %.1f ⇒ 判「没画面」⇒ 不进复用（fail open）" % BLANK_STDDEV,
        "复用TTL": "%.0f 秒（与 gate.CAPTURE_TTL 对齐）" % FRAME_TTL_SEC,
        "变化即停": "轮询指纹，变了就停，最多 %d ms（**不是把等待砍成 0**）" % WAIT_BUDGET_MS,
    }
