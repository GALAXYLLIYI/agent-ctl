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
"""
import argparse
import ctypes
import datetime
import json
import os
import sys

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

# ★ 默认写进 %LOCALAPPDATA%\AgentCtl\shots，不写死本机路径。
#   平台进程会用 AGENTCTL_SHOTS_DIR（或直接注入 SHOTDIR）指过来，
#   这里只是「独立跑这个脚本时」的兜底，指向同一个地方。
_APP_DIR = os.environ.get("AGENTCTL_APP_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl")
SHOTDIR = os.environ.get("AGENTCTL_SHOTS_DIR") or os.path.join(_APP_DIR, "shots")
MAX_NODES = 400          # 控件树上限，防炸
MAX_DEPTH = 6
# 用户在这个标题的窗口里输密钥 —— 见到它就一律不看屏幕（比"记得别看"可靠）
BLOCKED_TITLES = ("DSH电脑控制授权",)


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
                "可点击": bool(getattr(ctrl, "IsEnabled", False)),
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
                  "ExpandCollapse", "Scroll", "Text", "LegacyIAccessible")


def _patterns(ctrl):
    """能用的交互模式 —— 决定「该用 UIA 点」还是「只能 SendInput 点」。"""
    out = []
    for name in _PATTERN_NAMES:
        fn = getattr(ctrl, "Get" + name + "Pattern", None)
        if fn is None:
            continue
        try:
            if fn() is not None:
                out.append(name)
        except Exception:
            pass
    return out


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
    a = ap.parse_args()

    # ── 过闸：碰屏幕之前先问闸门。不过闸就什么都不做。（持 B/C/D 证也能看：感知是各级底座）
    try:
        got = gate.require_any(gate.PERCEIVE_SCOPES, a.why or "只读感知")
    except gate.GateDenied as e:
        print("⛔ 未获授权，不做任何采集：" + str(e))
        print("   授权方式：python auth_popup.py --request \"" + gate.A + "\"")
        return 9
    print("🔓 " + got, file=sys.stdout, flush=True)

    res = {}
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
        res["控件树"] = tree(a.tree)
    if a.tree and a.nodes:
        t = res["控件树"]
        if "拒绝" in t:
            print("⛔ " + t["拒绝"])
            return 4
        print(f"句柄 {t['句柄']}  节点数 {t['节点数']}  截断 {t['截断']}")
        for n in t["节点"][:a.nodes]:
            print("  " * n.get("层级", 0) + "· {} | {} | {} | {}".format(
                n.get("类型", "?"), (n.get("名称") or "")[:34],
                n.get("矩形"), ",".join(n.get("模式") or []) or "-"))
        return 0
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
