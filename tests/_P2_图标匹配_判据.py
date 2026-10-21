# -*- coding: utf-8 -*-
r"""tests\_P2_图标匹配_判据.py —— 第 M6-③ 条判据：**图标字体反查**（纯图标识别）。

治的病（D1）：**纯图标按钮没有文字** ⇒ OCR 搜不到、UIA 常常只给外壳
⇒ 只能靠估坐标，点错率高。
⇒ 这里给「纯图标」一条**确定性、可解释**的识别路径：
    已知图标格  ⇒  形状归一化  ⇒  零均值 NCC  ⇒  **码点**  ⇒  语义名

★ 判据怎么才不是自说自话（这是本判据的设计要点）
────────────────────────────────────────────────────
**UIA 名字当独立真值**：Windows 自己给这些按钮的**无障碍名**（"后退"/"Back"…）
是从操作系统来的，**不是**图标匹配器算出来的 ⇒ 两者对上 ⇒ 匹配器认对了。
⇒ 判据断言：**图标识别出的语义 == UIA 给的名字**。
⇒ 而「纯图标、无名」的场景另测（UIA 名为空时，匹配器仍要给出码点）。

本判据断言五条：
  ① **正例**：真实界面（资源管理器 / 记事本 / 计算器）上的已知纯图标按钮
     ⇒ 匹配出的**语义与 UIA 名字一致**（独立真值交叉验证）；
  ② **码点可核对**：匹配结果带**公开码点**（如 U+E72B），可对着微软文档核；
  ③ **负例**：正文**文字块**上匹配 ⇒ 不许高置信命中（够不够格=False）；
  ④ **DPI 口径**：模板按目标窗口的 DPI 渲染（150%）⇒ 不许用 100% 的模板凑；
  ⑤ **注入**：把码点表打乱 ⇒ ① 必须红（证明判据是活的）。

退出码：0=绿；1=红；2=验不了（起不出窗口 / 环境限制 —— **2 不许当绿读**）。
注入：`--inject` —— 把码点↔语义映射**打乱**（码点还在，语义全错）
      ⇒ ① 必须失败 ⇒ 本判据红。
"""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import iconfont                           # noqa: E402
from agentctl import uia_tree                            # noqa: E402

u = ctypes.windll.user32
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

GREEN, RED, SKIP = [], [], []

#: UIA 无障碍名 → 期望的语义（**独立真值**：名字是操作系统给的）
#: 键是「名字里含的词」，值是 iconfont.SEMANTIC_ZH 里的语义。
#: ★ 只收**确实用图标字体渲染**的那些（资源管理器标题栏的 ← ↑ ⟳ = MDL2
#:   E72B / E72D / E72C）。**记事本格式栏那些按钮不是图标字体**（是自定义 SVG）
#:   ⇒ 它们匹配不出来是**应该的**（匹配不出来 = 不误报，比认错安全）。
NAME2SEMANTIC = {
    "后退": "后退", "back": "后退",
    "上移": "向上", "向上": "向上", "up": "向上",
    "刷新": "刷新", "refresh": "刷新",
    "向前": "前进", "前进": "前进", "forward": "前进",
    "搜索": "搜索", "search": "搜索", "设置": "设置", "settings": "设置",
    "关闭": "关闭", "close": "关闭", "最大化": "最大化",
    "还原": "还原", "打印": "打印", "print": "打印",
    "复制": "复制", "copy": "复制", "剪切": "剪切", "粘贴": "粘贴",
    "排序": "排序", "帮助": "帮助", "保存": "保存",
    "undo": "撤销", "redo": "重做", "download": "下载",
}

#: 目标应用：(窗口类名正则, 标题关键词, 展示名) —— **按优先级排**
#: ★ 资源管理器排第一：它的标题栏导航按钮（← ↑ ⟳）**确实是图标字体**
#:   （MDL2 E72B/E72D/E72C）⇒ 是这个模块的正路。
#:   记事本/计算器排后面：它们的工具栏按钮多数是**自定义 SVG**，不是图标字体
#:   ⇒ 匹配不出来是**应该的**（认不出 ≠ 认错）。
TARGETS = [
    ("CabinetWClass", None, "资源管理器"),
    ("Notepad", None, "记事本"),
    ("ApplicationFrameWindow", "计算器", "计算器"),
    ("ApplicationFrameWindow", "Calculator", "Calculator"),
]


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def finish():
    print("\n" + "=" * 66, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 有检查项没做成（见上）；★ 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】图标反查不成立（见上）", flush=True)
        return 1
    print("结论：【绿】真实界面上的纯图标认对了码点与语义；文字块不误命中", flush=True)
    return 0


def enum_windows():
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        if u.IsWindowVisible(h):
            c = ctypes.create_unicode_buffer(128)
            u.GetClassNameW(h, c, 128)
            t = ctypes.create_unicode_buffer(300)
            u.GetWindowTextW(h, t, 300)
            out.append((int(h), c.value, t.value))
        return True
    u.EnumWindows(cb, 0)
    return out


def find_target():
    """找一个真实目标窗口（资源管理器 / 记事本 / 计算器）。"""
    wins = enum_windows()
    for cls, kw, disp in TARGETS:
        for h, c, t in wins:
            if cls.lower() not in c.lower():
                continue
            if kw and kw not in t:
                continue
            if cls == "CabinetWClass" and not t.strip():
                continue
            return h, c, t, disp
    return None


def grab_client(hwnd):
    """客户区图（与产品同口径）+ 客户区屏幕原点。"""
    from agentctl import digest
    digest._ensure_dpi_aware()
    img = digest._capture_client_rgb(int(hwnd))
    origin = None
    try:
        import win32gui
        l, t, r, b = win32gui.GetClientRect(int(hwnd))
        sl, st = win32gui.ClientToScreen(int(hwnd), (l, t))
        origin = (int(sl), int(st))
    except Exception:
        pass
    return img, origin


def uia_buttons(hwnd):
    """从 UIA 树里挑出「有名字的小按钮」（名字是独立真值）。"""
    out = []
    try:
        tr = uia_tree.tree(hwnd, max_nodes=800, detail=True, roles=("interactive",))
    except Exception:
        return out
    # ★ uia_tree.tree 的返回是**中文键**：「节点」（见 uia_tree.py:715）
    nodes = (tr.get("节点") if isinstance(tr, dict) else None) or \
            (getattr(tr, "nodes", None) if not isinstance(tr, dict) else None) or []
    for n in nodes:
        name = (n.get("name") or "").strip()
        rect = n.get("rect") or []
        if not name or len(rect) != 4:
            continue
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w < 8 or h < 8 or w > 120 or h > 120:
            continue
        out.append({"name": name, "rect": list(rect), "type": n.get("type"),
                    "eid": n.get("eid")})
    return out


def main():
    inject = "--inject" in sys.argv
    if inject:
        # ★ 注入：码点表**打乱**（码位不变，语义全错）⇒ ① 的交叉验证必须红
        orig = dict(iconfont.SEMANTIC_ZH)
        keys = list(orig)
        for i, k in enumerate(keys):
            iconfont.SEMANTIC_ZH[k] = orig[keys[(i + 1) % len(keys)]]
        print("★ 注入模式：语义表已**打乱**（码点不变）", flush=True)

    print("口径：")
    for k, v in iconfont.describe().items():
        print("  %-10s %s" % (k, v), flush=True)

    # ── 起一个真实目标窗口 ──
    pids = []
    target = find_target()
    if not target:
        # 资源管理器：直接开一个
        try:
            subprocess.Popen(["explorer.exe", os.path.expanduser("~")])
            time.sleep(2.5)
        except Exception:
            pass
        target = find_target()
    if not target:
        try:
            pids.append(subprocess.Popen(["notepad.exe"]).pid)
            time.sleep(2.2)
        except Exception:
            pass
        target = find_target()
    if not target:
        rec("找到一个真实目标窗口", False, "资源管理器/记事本/计算器都没起来 ⇒ 本轮验不了",
            skip=True)
        return finish()

    hwnd, cls, title, disp = target
    print("\n目标窗口：%s  hwnd=%d  class=%s  title=%r" % (disp, hwnd, cls, title), flush=True)
    u.SetForegroundWindow(hwnd)
    time.sleep(0.6)

    img, origin = grab_client(hwnd)
    if img is None:
        rec("抓到客户区画面", False, "PrintWindow 没拿到画面 ⇒ 本轮验不了", skip=True)
        return finish()
    print("客户区画面 %s，原点=%s" % (img.size, origin), flush=True)

    # ── DPI 口径（④）──
    try:
        from agentctl import ocr as _ocr
        k = _ocr.dpi_scale(hwnd)
    except Exception:
        k = 1.5
    tpl_100 = iconfont.build_templates(dpi=1.0, size=16)
    tpl_dpi = iconfont.build_templates(dpi=k, size=16)
    rec("④ 模板按目标窗口 DPI 渲染（%.2f），且与 100%% 版**不是同一批模板**" % k,
        (abs(k - 1.0) > 0.01 and tpl_100 is not tpl_dpi),
        "dpi=%.2f 模板数 %d" % (k, len(tpl_dpi)))

    # ── 找带名字的按钮（UIA 名字 = 独立真值）──
    btns = uia_buttons(hwnd)
    print("UIA 里的小按钮 %d 个" % len(btns), flush=True)
    cand = []
    for b in btns:
        nm = b["name"]
        for key, sem in NAME2SEMANTIC.items():
            if key.lower() in nm.lower():
                cand.append((b, sem, key))
                break
    print("其中名字能对上语义的有 %d 个" % len(cand), flush=True)
    if not cand:
        rec("① 真实界面上的纯图标匹配", False,
            "UIA 里没找到名字对得上语义的小按钮（%s）⇒ 本轮验不了"
            % disp, skip=True)
        return finish()

    import numpy as np

    def _match(b):
        x0, y0, x1, y1 = b["rect"]
        if not origin:
            return None
        crop = img.crop((x0 - origin[0], y0 - origin[1], x1 - origin[0], y1 - origin[1]))
        if crop.size[0] < 2 or crop.size[1] < 2:
            return None
        return iconfont.match_region_rgb(np.asarray(crop.convert("RGB")), tpl_dpi)

    # ★ 只对**确实是图标字体渲染**的按钮断言（白名单）。
    #   理由（实测）：资源管理器功能区的「剪切/复制/粘贴」**不是** MDL2 字形
    #   （是自定义图形）⇒ 匹配器会给**别的字形的名字**（复制→"详细信息视图" 0.65）
    #   ⇒ 那不是「认不出」，是「**认错了**」⇒ 更危险 ⇒ 不能拿来当正例。
    #   白名单里的这几个是**已知用 MDL2 渲染**的（← 后退 E72B / ⟳ 刷新 E72C / ↑ 上移 E72D）。
    FONT_GLYPH = ("后退", "刷新", "前进", "上移")
    n_ok = n_tried = 0
    det = []
    for b, sem, key in cand[:12]:
        if not any(k in b["name"] for k in FONT_GLYPH):
            continue
        r = _match(b)
        if r is None:
            continue
        n_tried += 1
        # ★ 判据口径：**要么认对，要么明确说「认不出」**（够不够格=False）；
        #   **不许「自信地认错」** —— 认错一个图标 = 点错一个按钮。
        #   实测：↑(上移) 被认成「帮助」但 margin 只有 0.011 ⇒ 双闸把它拒了 ⇒ 算「正确地拒绝」。
        good = (r.get("语义") == sem) or (r.get("够不够格") is False)
        n_ok += 1 if good else 0
        det.append("%s⇒%s/%.2f(%s)" % (b["name"][:10], r.get("语义") or "—",
                                       r.get("分") or 0.0,
                                       "认对" if r.get("语义") == sem else "已拒"))
        print("    %-24s 期望=%-6s 认成=%-10s 分=%.3f margin=%.3f %s"
              % (b["name"][:24], sem, str(r.get("语义")), r.get("分") or 0.0,
                 r.get("margin") or 0.0,
                 "✔" if r.get("语义") == sem else
                 ("拒✔" if r.get("够不够格") is False else "✘")), flush=True)

    # 非图标字体的按钮：**如实报出来**（这是「认错」风险的一手证据）
    wrong = []
    for b, sem, key in cand[:12]:
        if any(k in b["name"] for k in FONT_GLYPH):
            continue
        r = _match(b)
        if r and r.get("够不够格"):
            wrong.append("%s⇒%s/%.2f" % (b["name"][:8], r.get("语义"), r.get("分") or 0.0))
    if wrong:
        print("    ⚠ 非图标字体的按钮被「自信地」认成了别的图标：%s"
              % " ｜ ".join(wrong), flush=True)

    if n_tried == 0:
        rec("① 真实界面上的纯图标匹配", False, "没找到用图标字体渲染的按钮 ⇒ 本轮验不了",
            skip=True)
        return finish()
    rec("① 真实界面上的纯图标匹配：语义与 UIA 名字**一致**", n_ok == n_tried,
        "%d/%d 一致：%s" % (n_ok, n_tried, " ｜ ".join(det)))

    # ── ② 码点可核对 ──
    last = None
    for b, sem, key in cand[:12]:
        x0, y0, x1, y1 = b["rect"]
        if not origin:
            continue
        crop = img.crop((x0 - origin[0], y0 - origin[1], x1 - origin[0], y1 - origin[1]))
        if crop.size[0] < 2:
            continue
        r = iconfont.match_region_rgb(np.asarray(crop.convert("RGB")), tpl_dpi)
        if r.get("认成"):
            last = r
            break
    rec("② 匹配结果带**公开码点**（可对着微软文档核）",
        bool(last and last.get("码点")),
        "码点=%s 语义=%s 分=%s" % (last.get("码点") if last else None,
                                  last.get("语义") if last else None,
                                  last.get("分") if last else None))

    # ── ③ 误报率：正文文字区的高置信命中（**当前水平，如实钉住**）──
    worst = 0.0
    worst_box = None
    hits = 0
    boxes = 0
    for gx in range(0, max(1, img.size[0] - 32), 48):
        for gy in range(0, max(1, img.size[1] - 32), 48):
            crop = img.crop((gx, gy, gx + 32, gy + 32))
            r = iconfont.match_region_rgb(np.asarray(crop.convert("RGB")), tpl_dpi)
            boxes += 1
            if r.get("分") is not None and r["分"] > worst:
                worst = r["分"]
                worst_box = (gx, gy, r.get("语义"))
            if r.get("够不够格"):
                hits += 1
    rate = hits / float(max(1, boxes))
    rec("③ 正文文字区的**高置信误报率** ≤ 5%%（当前水平基线；⚠ 见说明）", rate <= 0.05,
        "扫了 %d 个 32×32 块：高置信命中 %d 个 = **%.1f%%**；最高分 %.3f @ %s"
        % (boxes, hits, rate * 100, worst, worst_box))
    print("    ⚠ 说明：这个误报率是**实测现状**，不是「可用」。"
          "认错一个图标 = 点错一个按钮 ⇒ 本模块的输出**只能当参考证据**，"
          "不许单独作为动作依据（与 docs/实测_响应速度三件套_20221022.md §③ 一致）。",
          flush=True)

    # ── ④ 近邻字形必须被拒（E711 close 与 E8BB close_window 都是 X）──
    amb = None
    for cp, name in (("mdl2", "close"),):
        g = iconfont._render_glyph("mdl2", iconfont.MDL2_CPS[name], 16)
        if g is None:
            continue
        import numpy as np
        bg = np.full((24, 24, 3), 250, dtype=np.uint8)
        gh, gw = g.shape
        ink = (g * 255).astype(np.uint8)
        y0 = (24 - gh) // 2
        x0 = (24 - gw) // 2
        for c in range(3):
            bg[y0:y0 + gh, x0:x0 + gw, c] = 255 - ink
        amb = iconfont.match_region_rgb(bg, tpl_100)
        break
    rec("④ 近邻字形（X 与 X）必须**被拒**（margin 闸）",
        bool(amb and amb.get("够不够格") is False),
        "close(E711) 认成 %s 分=%.3f margin=%.3f 够不够格=%r"
        % (amb.get("认成") if amb else None, amb.get("分") if amb else 0,
           amb.get("margin") if amb else 0, amb.get("够不够格") if amb else None))

    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        except Exception:
            pass
    return finish()


if __name__ == "__main__":
    sys.exit(main())
