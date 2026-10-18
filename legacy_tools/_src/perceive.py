# -*- coding: utf-8 -*-
"""perceive.py —— 电脑控制的第 1 步：只读感知（不看不动）

铁律 11 的第 1 档：本脚本**只读**，不模拟任何输入。
产出三样东西，供用户核对：
  1) 窗口清单（句柄/标题/类名/矩形/PID/是否可见/是否最小化）
  2) 截图（全屏或指定窗口），存到 _监控\\shots\\
  3) 指定窗口的 UIA 控件树（名称/类型/矩形/是否可点击），截断在安全上限内

用法：
  python perceive.py --windows              # 列窗口
  python perceive.py --shot                 # 全屏截图
  python perceive.py --shot --hwnd 1234     # 指定窗口截图
  python perceive.py --tree 1234            # 控件树
  python perceive.py --screen               # 分辨率与 DPI 信息

── 以下两项为 2026-10-08 工单 T2 新增，**默认全关**；不开时输出与旧版逐字节一致 ──
  python perceive.py --tree 1234 --tree-diff      # 树差量：full / diff / no_change 三态
  python perceive.py --tree 1234 --tree-diff --diff-force full   # 强制全量（调试/自测）
  python perceive.py --windows --events           # 感知期间订阅窗口事件，采完即注销
"""
import argparse
import ctypes
import datetime
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate                                                # noqa: E402  # 唯一闸门

# ── DPI：必须先设置，否则截图坐标（物理像素）和 UIA 矩形（逻辑像素）对不上
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import win32gui                                            # noqa: E402
import win32process                                        # noqa: E402
import win32ui                                             # noqa: E402
from PIL import Image, ImageGrab, ImageStat                # noqa: E402

_MON = (__import__("os").environ.get("AGENTCTL_MONITOR_DIR")
        or __import__("os").path.join(__import__("os").path.expanduser("~"),
                                     "Documents", "agent-manual", "_监控"))
SHOTDIR = __import__("os").path.join(_MON, "shots")
MAX_NODES = 400          # 控件树上限，防炸
MAX_DEPTH = 6
# 用户在这个标题的窗口里输密钥 —— 见到它就一律不看屏幕（比"记得别看"可靠）
BLOCKED_TITLES = ("DSH电脑控制授权",)

# ── 2026-10-08 工单 T2-A：树差量 ────────────────────────────────────────────
# 缓存目录：与截图同门（_监控\），只存**规范化后的控件树**，不存屏幕内容。
DIFF_CACHE_DIR = __import__("os").path.join(_MON, "perceive_cache")
DIFF_CACHE_VER = 1       # 规范化口径一变就升版本，老缓存自动作废（按 full，不瞎比）
DIFF_MAX_ITEMS = 200     # 差量明细条数上限，防炸

# ── 2026-10-08 工单 T2-B：事件订阅（即用即注销）────────────────────────────
# 只订阅"可能改变控件树"的事件，见 WinEventProbe。
# 事件范围（SetWinEventHook 的 min/max 对）：
#   0x0003–0x0020 系统事件（前台/移动缩放结束/最小化/桌面切换…）
#   0x8000–0x800C 对象事件（创建/销毁/显示/隐藏/重排/焦点/状态/位置/改名）
WINEVENT_RANGES = ((0x0003, 0x0020), (0x8000, 0x800C))
WINEVENT_WATCHED = {
    0x0003: "前台窗口变化",
    0x000B: "窗口移动缩放结束",
    0x0016: "窗口最小化",
    0x0017: "窗口还原",
    0x0020: "桌面切换",
    0x8000: "控件创建",
    0x8001: "控件销毁",
    0x8002: "控件显示",
    0x8003: "控件隐藏",
    0x8004: "控件顺序变化",
    0x8005: "控件焦点变化",
    0x800A: "控件状态变化",
    0x800B: "控件位置/尺寸变化",
    0x800C: "控件改名",
}
WINEVENT_OUTOFCONTEXT = 0x0000    # 回调在**安装钩子的线程**上、由该线程取消息时触发
WINEVENT_SKIPOWNPROCESS = 0x0002  # 不理会本进程自己发出的事件
WINEVENT_SAMPLE_CAP = 40          # 样本条数上限


def blocked_window():
    """返回当前存在的敏感窗口标题；无则 None。"""
    found = []

    def cb(hwnd, _):
        t = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and any(b in t for b in BLOCKED_TITLES):
            found.append(t)
        return True

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def _pid(hwnd):
    try:
        return win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return 0


def windows(only_visible=True):
    out = []

    def cb(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if only_visible and not win32gui.IsWindowVisible(hwnd):
            return True
        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 1 or h <= 1:
            return True
        out.append({
            "hwnd": hwnd,
            "pid": _pid(hwnd),
            "class": win32gui.GetClassName(hwnd),
            "title": "[已屏蔽：敏感窗口]" if any(x in title for x in BLOCKED_TITLES) else title,
            "屏蔽": any(x in title for x in BLOCKED_TITLES),
            "rect": list(rect),          # 物理像素 (l, t, r, b)
            "size": [w, h],
            "minimized": bool(win32gui.IsIconic(hwnd)),
            "foreground": hwnd == win32gui.GetForegroundWindow(),
            "process": _proc_name(_pid(hwnd)),
        })
        return True

    win32gui.EnumWindows(cb, None)
    return out


_proc_cache = {}


def _proc_name(pid):
    if pid in _proc_cache:
        return _proc_cache[pid]
    name = ""
    try:
        import psutil
        name = psutil.Process(pid).name()
    except Exception:
        pass
    _proc_cache[pid] = name
    return name


def screen_info():
    u = ctypes.windll.user32
    u.SetProcessDPIAware()
    phys = (u.GetSystemMetrics(0), u.GetSystemMetrics(1))
    g = ctypes.windll.gdi32
    hdc = u.GetDC(0)
    LOGPIXELSX = 88
    dpi = g.GetDeviceCaps(hdc, LOGPIXELSX)
    u.ReleaseDC(0, hdc)
    virt = (u.GetSystemMetrics(78), u.GetSystemMetrics(79))   # SM_CXVIRTUALSCREEN/Y
    return {"物理分辨率": phys, "DPI": dpi, "缩放%": round(dpi / 96 * 100),
            "虚拟桌面": virt, "截图坐标系": "物理像素（已 SetProcessDPIAware）"}


def _print_window(hwnd):
    """让窗口**自己画一遍**（PrintWindow + PW_RENDERFULLCONTENT=2），即使被别的窗口盖住
    也能拿到它自己的内容。返回 (PIL图, 是否成功)。

    为什么不能用 ImageGrab 抓窗口矩形：那抓的是**屏幕上那块像素**。窗口被盖住时，
    你拿到的其实是压在上面那个窗口的像素 —— 却会当成目标窗口的证据。
    """
    rect = win32gui.GetWindowRect(hwnd)
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if w <= 0 or h <= 0:
        return None, False
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        ok = bool(ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2))
        img = Image.frombuffer("RGBA", (w, h), bmp.GetBitmapBits(True),
                               "raw", "BGRA", 0, 1).convert("RGB")
    except Exception:
        return None, False
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
    return img, ok


def _looks_blank(img):
    """整块画面"没有内容"的判据：用灰度标准差，而不是"是不是全黑"。

    为什么不能只看"是不是全黑"：实测踩过——窗口最小化时 `GetWindowRect` 返回哨兵矩形
    `(-32000,…)`，于是抓到一块 237×39 的碎片；它既不黑也不空，还带着"范围(84,232) 标准差15"
    这类看起来正常的统计量，于是被当成"证据"记进日志。**空白/碎片都必须能被识别出来。**
    """
    g = img.convert("L")
    st = ImageStat.Stat(g)
    return st.stddev[0] < 3.0          # 纯色/近纯色 = 没有信息


def shot(hwnd=None, path=None, allow_screen_fallback=False):
    b = blocked_window()
    if b:
        return {"拒绝": f"检测到敏感窗口「{b}」——用户在输密钥，按规矩不看屏幕。"
                        "请先关闭授权弹窗再采集。"}
    os.makedirs(SHOTDIR, exist_ok=True)
    tag = f"win{hwnd}" if hwnd else "full"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = path or os.path.join(SHOTDIR, f"{stamp}_{tag}.png")

    if not hwnd:                                   # 全屏：本来就是抓屏幕，ImageGrab 正确
        rect = (0, 0) + (ctypes.windll.user32.GetSystemMetrics(0),
                         ctypes.windll.user32.GetSystemMetrics(1))
        img = ImageGrab.grab(bbox=rect, all_screens=True)
        img.save(path)
        return {"文件": path, "类型": "全屏", "区域": list(rect), "尺寸": list(img.size)}

    # 指定窗口：一律走 PrintWindow，拿到的是**窗口自身内容**
    fg = win32gui.GetForegroundWindow() == hwnd
    title = win32gui.GetWindowText(hwnd)
    if win32gui.IsIconic(hwnd):
        return {"拒绝": f"窗口「{title}」已最小化 —— 取不到内容。"
                        f"（最小化时 GetWindowRect 返回哨兵坐标，会抓到一小块垃圾像素）"
                        f"请先恢复它；恢复窗口属于动作，需要 B 级授权。"}
    rect = win32gui.GetWindowRect(hwnd)
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if w < 80 or h < 80 or rect[0] < -30000 or rect[1] < -30000:
        return {"拒绝": f"窗口矩形异常 {list(rect)}（尺寸 {w}×{h}）—— 拒绝出图，"
                        f"免得把碎片当证据。"}
    img, ok = _print_window(hwnd)
    if img is not None and not _looks_blank(img):
        img.save(path)
        return {"文件": path, "类型": "PrintWindow（窗口自身内容）", "窗口标题": title,
                "是否前台": fg, "尺寸": list(img.size),
                "区域": list(rect),
                "说明": "被遮挡也能取到窗口自身" if not fg else "前台窗口"}
    # PrintWindow 拿不到内容（全黑/纯色，部分 WinUI/DWM 窗口会这样）→ 绝不悄悄退回屏幕抓取
    if (fg or allow_screen_fallback) and not win32gui.IsIconic(hwnd):
        img2 = ImageGrab.grab(bbox=rect, all_screens=True)
        if not _looks_blank(img2):
            img2.save(path)
            return {"文件": path, "类型": "屏幕抓取（PrintWindow 无内容，退路）", "窗口标题": title,
                    "是否前台": fg, "尺寸": list(img2.size), "区域": list(rect),
                    "警告": "该窗口不支持 PrintWindow，退路只在窗口处于前台时才可信"}
    return {"拒绝": f"窗口「{title}」PrintWindow 拿不到内容（空白/纯色），且它不是前台窗口；"
                    f"此时抓屏幕会拍到别的窗口 —— 拒绝给出错误的图。"}


def tree(hwnd, max_nodes=MAX_NODES, max_depth=MAX_DEPTH):
    """UIA 控件树。只读属性读取，不 Invoke。"""
    if any(x in (win32gui.GetWindowText(hwnd) or "") for x in BLOCKED_TITLES):
        return {"拒绝": "敏感窗口，不读控件树"}
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(2)
    try:
        auto.InitializeUIAutomationInCurrentThread()
    except Exception:
        pass
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return {"错误": f"句柄 {hwnd} 取不到 UIA 根"}
    nodes, truncated = [], [False]

    def walk(ctrl, depth):
        if len(nodes) >= max_nodes:
            truncated[0] = True
            return
        try:
            r = ctrl.BoundingRectangle
            rect = [r.left, r.top, r.right, r.bottom]
            nodes.append({
                "层级": depth,
                "类型": ctrl.ControlTypeName,
                "名称": (ctrl.Name or "")[:80],
                "自动化ID": ctrl.AutomationId or "",
                "类名": ctrl.ClassName or "",
                "矩形": rect,
                # ★ 2026-10-04 修：原来这里写的是 "可点击": IsEnabled ——
                #   IsEnabled 的语义是「元素没被禁用/只读」，**跟"看得见""点得着"无关**。
                #   实测 Windows Terminal 34 个控件 34/34 全为 true（含 TextControl 纯文字标签），
                #   信号价值为零且会骗人。改按设计 §21.8 的契约：enabled + patterns，并补 IsOffscreen。
                "启用": bool(getattr(ctrl, "IsEnabled", False)),
                "离屏": bool(getattr(ctrl, "IsOffscreen", False)),
                "可键盘聚焦": bool(getattr(ctrl, "IsKeyboardFocusable", False)),
                "模式": _patterns(ctrl),
            })
        except Exception as e:
            nodes.append({"层级": depth, "类型": "?", "读取失败": str(e)[:60]})
            return
        if depth >= max_depth:
            return
        try:
            for c in ctrl.GetChildren():
                walk(c, depth + 1)
                if truncated[0]:
                    return
        except Exception:
            pass

    walk(root, 0)
    return {"句柄": hwnd, "节点数": len(nodes), "截断": truncated[0], "节点": nodes}


_PATTERN_NAMES = ("Invoke", "Toggle", "SelectionItem", "Value",
                  "ExpandCollapse", "Scroll", "Text", "RangeValue",
                  "Grid", "GridItem", "Window", "Selection",
                  "LegacyIAccessible")


def _patterns(ctrl):
    """能用的交互模式 —— 决定「该用 UIA 点」还是「只能 SendInput 点」。

    ★ 2026-10-04 修 BUG：原来写的是 getattr(ctrl, "Get" + name + "Pattern")，
      但 uiautomation 库里**没有这些方法**（只有 GetPattern / GetCachedPattern /
      GetPatternAs / GetLegacyIAccessiblePattern）。getattr 拿到 None 就 `continue`，
      **不报错、不告警** —— 于是「模式」永远返回 []，
      而设计 §21.8 的剪枝第一条判据「有 patterns → 必留」从来没生效过。

      正确用法：GetPattern(**整数 PatternId**)，属性名带 Pattern 后缀
      （uiautomation.PatternId.InvokePattern = 10000，经 import uiautomation as _ua 访问）。
      传字符串会抛 COMError，被库里的 `except comtypes.COMError` 吞掉，同样静默失败。
    """
    # 注意：模块顶部的 import 列表里没有 uiautomation（它在 build_tree 内部才 import），
    # 所以这里必须自己导一次 —— 否则会 NameError，被下面的 except 吞掉，又变成静默返回 []。
    import uiautomation as _ua

    out = []
    for name in _PATTERN_NAMES:
        pid = getattr(_ua.PatternId, name + "Pattern", None)
        if pid is None:
            continue
        try:
            if ctrl.GetPattern(pid) is not None:
                out.append(name)
        except Exception:
            pass
    return out


# ══════════════════════════════════════════════════════════════════════════
# 2026-10-08 工单 T2-A：树差量
#
# 目标（照工单）：对 UIA 控件树做**规范化哈希**并缓存上次结果，输出
#   full / diff / no_change 三态。**默认行为不变** —— 不开 --tree-diff 时
#   下面的函数一个都不会被执行，tree() 本身**一个字都没改**。
#
# 规范化的三条规则（同时写进输出的「规范化」字段，供审核逐条对照）：
#   1. 矩形改成**相对根窗口左上角**的偏移。窗口被整体挪一下不该算"树变了"；
#      要绝对坐标就加 --diff-abs-rect。
#   2. 只取 9 个字段参与签名：类型／名称／自动化ID／类名／矩形／启用／离屏／
#      可键盘聚焦／模式。读取失败的节点只取「读取失败」文本。
#   3. 名称沿用采集时的 80 字截断口径，不在这里二次加工。
#
# 配对规则（决定 diff 的粒度）：兄弟节点先按强身份键 (类型, 自动化ID, 类名) 唯一配对，
#   再按加名称的全键唯一配对，最后剩下的按位置兜底。这样"中间插进来一个控件"只会
#   报 1 条新增，而"改了个名字"只会报 1 条变更 —— 不会放大成整片增删。
# ══════════════════════════════════════════════════════════════════════════


def _node_sig(n, base, abs_rect=False):
    """节点签名（只含稳定字段）。base = 根窗口矩形左上角 (left, top)。"""
    if "读取失败" in n:
        return ["?", str(n.get("读取失败", ""))[:60]]
    r = list(n.get("矩形") or [0, 0, 0, 0])
    if not abs_rect:
        r = [r[0] - base[0], r[1] - base[1], r[2] - base[0], r[3] - base[1]]
    return [str(n.get("类型", "?")),
            str(n.get("名称") or ""),
            str(n.get("自动化ID") or ""),
            str(n.get("类名") or ""),
            r,
            bool(n.get("启用")),
            bool(n.get("离屏")),
            bool(n.get("可键盘聚焦")),
            [str(x) for x in (n.get("模式") or [])]]


def _nest(nodes, base, abs_rect=False):
    """把 tree() 给的**扁平前序列表**还原成父子结构（JSON 可存，便于增量比对）。

    节点字段：层级 / sig / 类型 / 名称 / 自动化ID / 类名 / 矩形 / 模式 / children。
    ★ 身份键**不落盘**，用时由 sig 现算（见 _pair 的 key_of）——
      免得 JSON 往返把 tuple 变成 list，fresh 与 cached 两边的键类型对不上。
    """
    root, stack = None, []
    for n in nodes:
        d = n.get("层级", 0)
        if not isinstance(d, int) or d < 0:
            d = 0
        sig = _node_sig(n, base, abs_rect)
        node = {"层级": d, "sig": sig,
                "类型": n.get("类型", "?"), "名称": n.get("名称", ""),
                "自动化ID": n.get("自动化ID", ""), "类名": n.get("类名", ""),
                "矩形": n.get("矩形"), "模式": list(n.get("模式") or []),
                "children": []}
        del stack[d:]
        if d == 0 or not stack:
            if root is None:
                root = node
            else:
                # 理论上不该出现第二个根；出现就挂到前一个根下，宁可多留不可丢
                root["children"].append(node)
        else:
            stack[d - 1]["children"].append(node)
        stack.append(node)
    return root


def _canon(node):
    """只留签名的最小规范形 —— 哈希只吃这个，不吃任何描述性字段。"""
    if node is None:
        return None
    return {"s": node["sig"], "c": [_canon(x) for x in node["children"]]}


def _tree_digest(nested):
    """规范化哈希：sha256(规范 JSON 前 32 位)。键序固定、分隔符固定 → 结果稳定。"""
    blob = json.dumps(_canon(nested), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def _mark(bucket, path, kind, node, old):
    """记一条差异。bucket = {"新增"/"变更"/"删除": 计数, "项": [...], "截断": bool}"""
    bucket[kind] = bucket.get(kind, 0) + 1
    if len(bucket["项"]) >= DIFF_MAX_ITEMS:
        bucket["截断"] = True
        return
    src = node if node is not None else old
    rec = {"层级": len(path),
           "路径": "/".join(str(i) for i in path) or "根",
           "变化": kind,
           "类型": src.get("类型", "?"),
           "名称": (src.get("名称") or "")[:80],
           "自动化ID": src.get("自动化ID", ""),
           "类名": src.get("类名", ""),
           "矩形": src.get("矩形"),
           "模式": src.get("模式")}
    if kind == "变更" and old is not None:
        rec["变更前"] = {"类型": old.get("类型", "?"),
                         "名称": (old.get("名称") or "")[:80],
                         "矩形": old.get("矩形"), "模式": old.get("模式")}
    bucket["项"].append(rec)


def _diff_nodes(old, new, path, bucket):
    if old is None and new is None:
        return
    if old is None:
        return _mark(bucket, path, "新增", new, None)
    if new is None:
        return _mark(bucket, path, "删除", None, old)
    if old["sig"] != new["sig"]:
        _mark(bucket, path, "变更", new, old)
    _pair(old["children"], new["children"], path, bucket)


def _pair(oc, nc, path, bucket):
    """兄弟配对，三趟 —— 这一步决定 diff 的粒度。

      第 1 趟 · 强身份键 (类型, 自动化ID, 类名)，**只在两边都唯一时才配**。
                这一趟让"名字变了"报成 1 条「变更」，而不是「删一个 + 加一个」。
      第 2 趟 · 全身份键（再加 名称），同样要求两边唯一 —— 收第 1 趟撞车的匿名兄弟。
      第 3 趟 · 剩下的按位置对齐 —— 兜底：宁可报变更，也不假装成整片增删。

    ★ 全程要求"两边都唯一"：一个键对应多个节点时宁可不配，也不乱配 ——
      乱配会造出看起来很像真的、其实指错对象的差量。
    """
    taken_o, taken_n, pairs = set(), set(), []

    def key_of(n, idxs):
        s = n["sig"]
        return tuple(s[k] if k < len(s) else "" for k in idxs)

    def match(tag, idxs):
        omap, nmap = {}, {}
        for i, o in enumerate(oc):
            if i not in taken_o:
                omap.setdefault((tag,) + key_of(o, idxs), []).append(i)
        for j, c in enumerate(nc):
            if j not in taken_n:
                nmap.setdefault((tag,) + key_of(c, idxs), []).append(j)
        for k, oi in omap.items():
            nj = nmap.get(k)
            if len(oi) == 1 and nj is not None and len(nj) == 1:
                taken_o.add(oi[0])
                taken_n.add(nj[0])
                pairs.append((oi[0], nj[0]))

    match("s", (0, 2, 3))          # 类型 / 自动化ID / 类名
    match("f", (0, 1, 2, 3))       # 再加 名称
    rest_o = [i for i in range(len(oc)) if i not in taken_o]
    rest_n = [j for j in range(len(nc)) if j not in taken_n]
    for oi, nj in zip(rest_o, rest_n):
        taken_o.add(oi)
        taken_n.add(nj)
        pairs.append((oi, nj))
    for oi in rest_o:
        if oi not in taken_o:
            pairs.append((oi, None))
    for nj in rest_n:
        if nj not in taken_n:
            pairs.append((None, nj))

    for oi, nj in pairs:
        _diff_nodes(oc[oi] if oi is not None else None,
                    nc[nj] if nj is not None else None,
                    path + [nj if nj is not None else oi], bucket)


def _cache_key(k):
    """缓存键清洗：只留字母数字与 . _ -，挡掉路径穿越。"""
    safe = "".join(ch for ch in str(k) if ch.isalnum() or ch in "._-")
    return safe[:64] or "default"


def tree_with_diff(hwnd, key="", cache_dir="", abs_rect=False, force=None, reset=False):
    """树差量三态。**只在 --tree-diff 打开时被调用**；内部采集仍走原样未改的 tree()。

    full      —— 没有可用缓存（首次/缓存坏了/参数变了/被强制），给全量节点
    no_change —— 规范化哈希与缓存一致，不给节点（省令牌），只给哈希
    diff      —— 哈希不同，只给变化节点 + 计数（明细上限 DIFF_MAX_ITEMS）

    fail-closed：缓存读不了、版本对不上、参数对不上、键被换成了别的窗口 ——
    一律**退回 full 并把原因原样写进「差量原因」**，绝不拿脏基线做差量。
    """
    base = tree(hwnd)
    if "拒绝" in base or "错误" in base:
        return base

    try:
        root_rect = list(win32gui.GetWindowRect(hwnd))
    except Exception:
        root_rect = [0, 0, 0, 0]
    nested = _nest(base["节点"], (root_rect[0], root_rect[1]), abs_rect)
    digest = _tree_digest(nested)
    ck = _cache_key(key or ("hwnd%d" % hwnd))
    cfile = os.path.join(cache_dir or DIFF_CACHE_DIR, ck + ".json")

    if reset:
        try:
            if os.path.exists(cfile):
                os.remove(cfile)
        except Exception:
            pass

    cached, why_none = None, ""
    try:
        with open(cfile, encoding="utf-8") as f:
            cached = json.load(f)
    except FileNotFoundError:
        why_none = "首次采集（无缓存）"
    except Exception as e:
        why_none = "缓存不可读（%s，按全量，原样上报）" % type(e).__name__
    if cached is not None:
        if cached.get("版本") != DIFF_CACHE_VER:
            cached, why_none = None, "缓存版本不符（按全量，原样上报）"
        elif bool(cached.get("绝对矩形")) != bool(abs_rect):
            cached, why_none = None, "规范化参数与缓存不一致（按全量，原样上报）"
        elif str(cached.get("句柄", "")) != str(hwnd):
            cached, why_none = None, "同一缓存键换成了别的窗口（按全量，原样上报）"
        elif "嵌套" not in cached:
            cached, why_none = None, "缓存缺树体（按全量，原样上报）"
    old = cached.get("嵌套") if cached is not None else None

    if force:
        state, reason = force, "强制 " + force
        if state == "diff" and cached is None:
            state, reason = "full", "强制 diff 但无基线可比 —— 退回全量"
    elif cached is None:
        state, reason = "full", why_none
    elif cached.get("树哈希") == digest:
        state, reason = "no_change", "与上次缓存逐字段一致"
    else:
        state, reason = "diff", "与上次缓存不同"

    out = {"句柄": hwnd, "节点数": base["节点数"], "截断": base["截断"],
           "差量状态": state, "差量原因": reason, "树哈希": digest,
           "哈希算法": "sha256(规范 JSON)前32位",
           "规范化": ("矩形取绝对坐标" if abs_rect else
                      "矩形相对根窗口左上角；名称沿用 80 字截断口径"),
           "缓存键": ck, "缓存文件": cfile, "根矩形": root_rect}

    if state == "diff":
        bucket = {"新增": 0, "变更": 0, "删除": 0, "项": [], "截断": False}
        _diff_nodes(old, nested, [], bucket)
        out["变更明细"] = bucket["项"]
        out["差异计数"] = {"新增": bucket["新增"], "变更": bucket["变更"],
                       "删除": bucket["删除"]}
        if bucket["截断"]:
            out["变更明细截断"] = True
    elif state == "no_change":
        out["差异计数"] = {"新增": 0, "变更": 0, "删除": 0}
        out["说明"] = "树与上次缓存一致，省略节点明细（省令牌）"
    else:
        out["节点"] = base["节点"]
        out["差异计数"] = {"新增": 0, "变更": 0, "删除": 0}

    try:
        os.makedirs(os.path.dirname(cfile), exist_ok=True)
        tmp = cfile + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"版本": DIFF_CACHE_VER, "句柄": hwnd,
                       "绝对矩形": bool(abs_rect), "树哈希": digest,
                       "根矩形": root_rect, "节点数": base["节点数"],
                       "时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "嵌套": nested}, f, ensure_ascii=False)
        os.replace(tmp, cfile)
    except Exception as e:
        # 缓存写不进去不该把这次感知变成失败 —— 但也不许悄悄吞掉，原样写在输出里
        out["缓存写入失败"] = "%s: %s" % (type(e).__name__, e)
    return out


def _diff_brief(t):
    """--tree-diff 配 --nodes 时的差量摘要行。"""
    st = t.get("差量状态", "?")
    c = t.get("差异计数") or {}
    if st == "diff":
        return "差量 {}  树哈希 {}  新增 {} 变更 {} 删除 {}".format(
            st, t.get("树哈希", ""), c.get("新增", 0), c.get("变更", 0), c.get("删除", 0))
    if st == "no_change":
        return "差量 {}  树哈希 {}  （与上次一致，未打印节点）".format(st, t.get("树哈希", ""))
    return "差量 {}  树哈希 {}  原因 {}  全量 {} 节点".format(
        st, t.get("树哈希", ""), t.get("差量原因", ""), t.get("节点数", 0))


# ══════════════════════════════════════════════════════════════════════════
# 2026-10-08 工单 T2-B：感知期间的事件订阅（即用即注销）
#
# 工单原文三条红线，逐条对应到实现：
#   · 禁止常驻线程 —— 本类**一行 threading 都没有**，回调全部由本线程自己的
#     短时 PeekMessage 泵驱动（OUTOFCONTEXT 的送达机制本来就是这样）。
#   · 禁止动 guardian —— 本文件不 import、不读写 guardian 的任何东西。
#   · 事件只用于标注"树可能已变"，**不改变过闸逻辑、不自动触发任何动作**。
#
# 另外两条自加的安全约束：
#   · 回调里**不调用任何 Win32/UIA 取属性**（读标题、读矩形都可能死锁），
#     只记 事件号 + hwnd + idObject/idChild —— 因此也**天然不会泄露敏感窗口标题**。
#   · 回调体整体套 try/except：回调里抛异常会把整个消息泵带崩。
#
# 为什么必须 OUTOFCONTEXT：INCONTEXT 要求回调函数在 DLL 里（要跨进程注入），
# Python 做不到。OUTOFCONTEXT 的回调在**安装钩子的那个线程**上被调用，且只有
# 该线程调用 PeekMessage/GetMessage 时才会送达 —— 所以**必须有 pump()**，
# 否则钩子装了也收不到事件（而且系统会在超时后把钩子摘掉）。
# ══════════════════════════════════════════════════════════════════════════


class WinEventProbe:
    """装上 → 采完 → 立刻注销。生命周期由 main() 用 try/finally 兜住。"""

    _PROTO = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint,
                                ctypes.c_void_p, ctypes.c_long,
                                ctypes.c_long, ctypes.c_ulong, ctypes.c_ulong)

    def __init__(self):
        self.hooks = []
        self.counts = {}
        self.samples = []
        self.error = ""
        self._cb = None            # ★ 必须留引用：一被 GC 回收，回调就是野指针
        self._u = ctypes.windll.user32
        try:
            self._u.SetWinEventHook.restype = ctypes.c_void_p
            self._u.SetWinEventHook.argtypes = [
                ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, self._PROTO,
                ctypes.c_ulong, ctypes.c_ulong, ctypes.c_uint]
            self._u.UnhookWinEvent.restype = ctypes.c_int
            self._u.UnhookWinEvent.argtypes = [ctypes.c_void_p]
        except Exception:
            pass

    def start(self):
        try:
            self._cb = self._PROTO(self._on_event)
            for lo, hi in WINEVENT_RANGES:
                h = self._u.SetWinEventHook(lo, hi, None, self._cb, 0, 0,
                                            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
                if h:
                    self.hooks.append(h)
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
        if not self.hooks and not self.error:
            self.error = "SetWinEventHook 全部返回 0（订阅失败）"
        return self

    def _on_event(self, _hook, event, hwnd, idobj, idchild, _thread, _when):
        try:
            name = WINEVENT_WATCHED.get(event)
            if name is None:
                return
            self.counts[name] = self.counts.get(name, 0) + 1
            if len(self.samples) < WINEVENT_SAMPLE_CAP:
                self.samples.append({"事件": name, "hwnd": int(hwnd or 0),
                                     "idObject": int(idobj), "idChild": int(idchild)})
        except Exception:
            pass

    def pump(self, seconds=0.6):
        """最长 seconds 秒的非阻塞消息泵。事件在这段时间里被真正送达回调。

        ★ 这里 peek 到的是**本线程**的消息（我们不创建任何窗口，所以除了系统给
          钩子用的内部消息，不会有别人的消息），Translate/Dispatch 不会去动别人的窗口。
        """
        try:
            from ctypes import wintypes
            msg = wintypes.MSG()
            PM_REMOVE = 0x0001
            u = self._u
            end = time.monotonic() + max(0.0, float(seconds))
            while True:
                while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    u.TranslateMessage(ctypes.byref(msg))
                    u.DispatchMessageW(ctypes.byref(msg))
                    if time.monotonic() >= end:
                        break
                if time.monotonic() >= end:
                    break
                time.sleep(0.005)
        except Exception as e:
            self.error = self.error or ("pump: %s: %s" % (type(e).__name__, e))
        return self

    def stop(self):
        """注销。**必须真的调到** —— main() 把它放在 finally 里。"""
        n = len(self.hooks)
        for h in self.hooks:
            try:
                self._u.UnhookWinEvent(ctypes.c_void_p(h))
            except Exception:
                pass
        self.hooks = []
        self._cb = None
        return n

    def summary(self):
        total = sum(self.counts.values())
        s = {"订阅": "已注销" if not self.hooks else "未注销（异常，需上报）",
             "订阅范围": ["0x%04X-0x%04X" % r for r in WINEVENT_RANGES],
             "事件总数": total,
             "事件种类": dict(sorted(self.counts.items(), key=lambda kv: -kv[1])),
             "样本": self.samples}
        if self.error:
            s["订阅告警"] = self.error
        s["提示"] = ("树可能已变（感知期间收到 %d 个相关事件，差量结果请自行复核）" % total
                     if total else "感知期间无相关事件（树应未变）")
        s["说明"] = "事件只用于标注，不参与过闸、不触发任何动作；回调只记事件号与句柄，不读窗口文字"
        return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", action="store_true")
    ap.add_argument("--screen", action="store_true")
    ap.add_argument("--shot", action="store_true")
    ap.add_argument("--tree", type=int)
    ap.add_argument("--hwnd", type=int)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--nodes", type=int, default=0,
                    help="配合 --tree：只打印前 N 个节点（紧凑文本，省得刷屏）")
    ap.add_argument("--why", default="", help="过闸日志里记的动作说明")
    # ── 以下为 2026-10-08 工单 T2 新增，默认全关 ──
    ap.add_argument("--tree-diff", action="store_true",
                    help="配合 --tree：输出树差量（full / diff / no_change 三态）")
    ap.add_argument("--diff-key", default="", help="差量缓存键（默认按句柄 hwndN）")
    ap.add_argument("--diff-cache", default="", help="差量缓存目录（默认 _监控\\perceive_cache）")
    ap.add_argument("--diff-abs-rect", action="store_true",
                    help="哈希用绝对矩形（默认相对根窗口左上角）")
    ap.add_argument("--diff-force", choices=("full", "diff", "no_change"), default=None,
                    help="强制某一态（调试/自测用）")
    ap.add_argument("--diff-reset", action="store_true", help="先清掉该键缓存再采集")
    ap.add_argument("--events", action="store_true",
                    help="感知期间订阅窗口事件（即用即注销，无常驻线程）")
    ap.add_argument("--events-wait", type=float, default=0.6,
                    help="采集后收集事件的最长秒数（默认 0.6）")
    a = ap.parse_args()

    # ── 用法自检：参数配错了就地拒绝，别浪费一张证（fail-closed）──
    if a.tree_diff and not a.tree:
        print("⛔ --tree-diff 必须配合 --tree <句柄>：不知道该对哪棵树上差量。")
        return 4
    if (a.diff_key or a.diff_reset or a.diff_abs_rect or a.diff_force) and not a.tree_diff:
        print("⛔ --diff-key / --diff-reset / --diff-abs-rect / --diff-force 只在 --tree-diff 下生效。")
        return 4
    if a.events_wait < 0:
        print("⛔ --events-wait 不能为负。")
        return 4

    # ── 过闸：碰屏幕之前先问闸门。不过闸就什么都不做。（持 B/C/D 证也能看：感知是各级底座）
    try:
        # ★ D18·A3-F1：读操作**不扣动作额度**（对齐 act.py:_need_perceive 与本仓新线 core.py
        #   的 count=False 口径）。改前这里漏了 count=False ⇒ 「看一眼屏幕」也在计数，
        #   额度用尽后只读感知会被误杀，用户想让我看一眼都得重新授权。
        got = gate.require_any(gate.PERCEIVE_SCOPES, a.why or "只读感知", count=False)
    except gate.GateDenied as e:
        print("⛔ 未获授权，不做任何采集：" + str(e))
        print("   授权方式：python auth_popup.py --request \"" + gate.A + "\"")
        return 9
    print("🔓 " + got, file=sys.stdout, flush=True)

    res = {}
    probe = WinEventProbe().start() if a.events else None
    try:
        if a.screen or not any([a.windows, a.shot, a.tree]):
            res["屏幕"] = screen_info()
        if a.windows or not any([a.windows, a.shot, a.tree]):
            ws = windows()
            ws.sort(key=lambda x: (not x["foreground"], x["minimized"], -x["size"][0]))
            res["窗口数"] = len(ws)
            res["窗口"] = ws[:a.limit]
        if a.shot:
            res["截图"] = shot(a.hwnd)
        if a.tree:
            if a.tree_diff:
                res["控件树"] = tree_with_diff(a.tree, key=a.diff_key, cache_dir=a.diff_cache,
                                               abs_rect=a.diff_abs_rect, force=a.diff_force,
                                               reset=a.diff_reset)
            else:
                res["控件树"] = tree(a.tree)
        if probe is not None:
            probe.pump(a.events_wait)
    finally:
        # ★ 即用即注销：无论采集是否抛异常，钩子一定被摘掉（不留常驻订阅）
        if probe is not None:
            probe.stop()
    if probe is not None:
        res["事件"] = probe.summary()

    if a.tree and a.nodes:
        t = res["控件树"]
        if a.tree_diff and "错误" in t:
            print("⛔ " + t["错误"])
            return 4
        if "拒绝" in t:
            print("⛔ " + t["拒绝"])
            return 4
        print(f"句柄 {t['句柄']}  节点数 {t['节点数']}  截断 {t['截断']}")
        if a.tree_diff:
            print(_diff_brief(t))
        for n in (t.get("节点") or [])[:a.nodes]:
            print("  " * n.get("层级", 0) + "· {} | {} | {} | {}".format(
                n.get("类型", "?"), (n.get("名称") or "")[:34],
                n.get("矩形"), ",".join(n.get("模式") or []) or "-"))
        return 0
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
