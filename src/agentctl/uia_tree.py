# -*- coding: utf-8 -*-
"""UIA 元素树 —— 不截图的识别通道（2026-10-07 建）

## 为什么有它

原来只有 `ctl_shot`：拿一张 PNG 丢给模型，模型自己在像素里找「哪个是提交按钮」。
慢、贵（PNG 无损编码实测 191 ms），而且**没有身份** —— 同一帧里两个长得一样的按钮，
模型只能猜。这就是以前「老是点回去做好的题」的根因：选择的依据是像素，不是元素。

UIA（UI Automation）是 Windows 自带的 COM 接口，读屏软件用的就是它。
每个控件天生带名字、类型、矩形、可用状态和交互能力（Invoke/Toggle/Value...），
**这些是结构化数据，不是图像**。所以：

* 认题、认选项、认按钮 —— 读 `Name`/`ValuePattern`/`TextPattern`，**不 OCR、不编码 PNG**
* 元素**有身份** → 返回短 id（`e12`），agent 下一步说「点 e42」，而不是自己算坐标
* 不受主题/颜色/字体/动画影响

## 实测基线（本机，Edge，6 个标签页）

* 787 节点 / **393 ms** / 纯 COM 往返
* 页面 DOM 全穿透：`DocumentControl` 下所有文字与按钮都带名字和物理坐标
* 对比：`ctl_shot` 单帧 65 ms（内存）+ 120 ms（PNG 编码）

## 边界（必须如实说）

UIA 看不见的一律瞎：**Canvas 自绘、游戏画面、PDF 内嵌渲染、没开无障碍的 Electron**。
本机 Codex 桌面版（Electron）只能拿到 22 个节点的外壳，页面全黑。
浏览器可用是因为 Chromium 在检测到无障碍客户端接入后才把 DOM 灌进树里。
所以这条线是**补充**不是**替代**，`ctl_shot` 永远留着兜底。
"""

import time

# 元素预算：一次最多吐多少个节点。防 token 爆，也防遍历失控。
DEFAULT_MAX_NODES = 600
# find() 是「找东西」，不该被一半的预算卡在浏览器外壳上。
FIND_MAX_NODES = 3000
DEFAULT_MAX_DEPTH = 25
SEARCH_TIMEOUT_SEC = 2.0

# ★ T1/W3-A（2026-10-14）：**只标注，不新增第二套坐标。**
#   本文件早先有人把树里的 rect 当成「屏幕坐标」、把 --xy 当成「窗口局部坐标」，
#   于是去「减窗口原点换算」—— 换算后点仍可能落在窗口矩形内 ⇒ 不被 rc=5 拦住
#   ⇒ **静默点错**，而且被写成 SOP 就制度化了。
#   DSH 已读码裁定：--xy 与 ctl_tree 的 rect **同一套 = 屏幕物理像素**
#   （contract.conventions.coordinates / act.py 的 SetCursorPos / PER_MONITOR_DPI_AWARE）。
#   所以这里**只加一句标注**，措辞照契约原文；**绝不加 rect_local / center_local /
#   窗口原点 这类换算字段** —— 多摆一套坐标，agent 总有选错的那个。
COORD_SYS = ("屏幕物理像素（与 contract.conventions.coordinates 同一套）；"
             "窗口 rect 由 ctl_windows 实时提供，不许缓存")

# ── 交互类型白名单（照 Windows-MCP 的判据，但只用类型，没抄它整份实现）
INTERACTIVE_TYPES = frozenset({
    "ButtonControl", "EditControl", "CheckBoxControl", "RadioButtonControl",
    "ComboBoxControl", "TabItemControl", "ListItemControl", "MenuItemControl",
    "HyperlinkControl", "SliderControl", "SpinnerControl", "TreeItemControl",
    "DataItemControl",
})

# ── 有文字的类型（要读 Value/Text 的）
# ★ F3 补（窗A2 · 2026-10-17）：补 PaneControl —— Chromium 的**裸 div**（无 role/aria）
#   文本常挂在 Pane 上，过去不在白名单里 ⇒ 题干"读不到"有一部分是**被口径筛掉的**，
#   不是 Chromium 不给。加它只是放宽一档，误收的代价（多几行文本）远小于漏题干。
TEXT_TYPES = frozenset({
    "TextControl", "EditControl", "DocumentControl", "HyperlinkControl",
    "ListItemControl", "ComboBoxControl", "ButtonControl", "CheckBoxControl",
    "RadioButtonControl", "TabItemControl", "PaneControl",
})

# ★ F3 补·兜底（窗A2）：TEXT_TYPES 是白名单，白名单天生漏 —— 网页里自定义控件、
#   自绘元素、Electron 内嵌页都可能挂在**任何** ControlType 上。所以再加一条
#   兜底：**任何类型只要文字非空且长度 ≥ TEXT_MIN_LEN 就进 texts**。
#   这两个开关是给"跑红自证"留的注入口（把 TEXT_FALLBACK 置 False 即退回补前口径）。
TEXT_FALLBACK = True
TEXT_MIN_LEN = 2          # 单字符不算文字（图标名/刻度这类噪音不进题干）

# ── 视口口径（F3 · 窗A2 · 2026-10-17）────────────────────────────────────────
#   病：walk() 过去**只滤零矩形、不裁剪视口** ⇒ 超星那种长页面里 y=17838 的节点
#       被原样返回，agent 拿它当屏幕坐标点下去会落到别处。
#       **这不是 agent 用错，是接口没给区分。**
#   规矩：**只标注，不裁信息**。每个节点都带 in_view / scroll_y / doc_height /
#       scroll_unknown；include_offscreen=False（默认）才只回视口内的，
#       True 时全回且 in_view 照标 —— 屏外节点是"现在看不见"，不是坏节点。
VIEWPORT_SYS = (
    "in_view = 节点屏幕矩形 ∩ 窗口客户区，面积>0 才算看得见（None = 客户区取不到，未知不裁剪）；"
    "in_view=false 只是「现在不在这一屏」，不是坏节点 —— 要它就 include_offscreen=True "
    "重拉，或先滚动页面；scroll_y/doc_height 取不到时是 null 且 scroll_unknown=true，"
    "不是 0（0 会被读成「已滚到顶」）"
)

# Document 的矩形比客户区高这么多才当成「整篇文档」（否则它可能只是可见那一屏）
DOC_TALL_FACTOR = 1.05
# ── 浏览器外壳：识别「我其实在网页里」时该滤掉的东西（= Windows-MCP 的 use_dom 思路）
BROWSER_CHROME_NAMES = frozenset({
    "最小化", "最大化", "还原", "关闭", "返回", "前进", "刷新", "主页",
    "地址和搜索栏", "搜索标签页", "新建标签页", "关闭标签页", "收藏夹",
    "扩展", "设置及其他 (Alt+F)", "个人 个人资料", "查看站点信息",
    "将此页面添加到收藏夹(Ctrl+D)", "进入阅读模式(F9)", "工作区按钮",
    "标签页组", "包含隐藏的收藏夹的菜单", "管理密码",
})


class UiaUnavailable(RuntimeError):
    """uiautomation 没装 / 拿不到根 / COM 初始化失败。"""


def _lib():
    try:
        import uiautomation as auto        # noqa: WPS433（延迟导入，缺了要降级而不是崩）
    except Exception as e:
        raise UiaUnavailable("uiautomation 不可用：%s" % e)
    _dpi_awareness()
    try:
        auto.SetGlobalSearchTimeout(SEARCH_TIMEOUT_SEC)
        auto.InitializeUIAutomationInCurrentThread()
    except Exception:
        pass                                 # 老版本没这俩，不致命
    return auto


_dpi_done = False


def _dpi_awareness():
    """UIA 给的是**物理像素**。不声明 DPI 感知，坐标在高缩放屏上会整体偏移。

    只做一次 —— 进程 DPI 感知是进程级设置，设第二次没意义。
    """
    global _dpi_done
    if _dpi_done:
        return
    _dpi_done = True
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _rect(r):
    try:
        return [int(r.left), int(r.top), int(r.right), int(r.bottom)]
    except Exception:
        return [0, 0, 0, 0]


def _center(rect):
    return [(rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2]


def _text_of(ctrl):
    """拿一个控件的文字。**只读，不 Invoke。**

    顺序有讲究：`Name` 最便宜也最可靠，先问它；空了才去问 ValuePattern（输入框内容）、
    TextPattern（长段落）、LegacyIAccessibleValue（老应用）。
    """
    try:
        n = ctrl.Name
        if n and n.strip():
            return n
    except Exception:
        pass
    for getter in ("GetValuePattern", "GetTextPattern"):
        try:
            p = getattr(ctrl, getter, None)
            if p is None:
                continue
            obj = p()
            if obj is None:
                continue
            if getter == "GetValuePattern":
                v = getattr(obj, "Value", "")
            else:
                rng = getattr(obj, "DocumentRange", None)
                v = getattr(rng, "GetText", lambda: "")() if rng else ""
            if v and str(v).strip():
                return str(v)
        except Exception:
            continue
    try:
        p = ctrl.GetLegacyIAccessiblePattern()
        v = getattr(p, "Value", "") if p else ""
        if v and str(v).strip():
            return str(v)
    except Exception:
        pass
    return ""


def _patterns(ctrl):
    """这个控件支持哪些交互能力。**要花钱，只在 detail=True 时问。**

    每个 GetXxxPattern 都是一次 COM 往返。787 节点 × 8 个 = 6000+ 次往返，
    这就是老 `perceive.py --tree` 慢的真正原因 —— 不是树大，是每个节点都穷问一遍。
    """
    out = []
    for name in ("Invoke", "Toggle", "SelectionItem", "Value",
                 "ExpandCollapse", "Scroll", "Text", "LegacyIAccessible"):
        fn = getattr(ctrl, "Get" + name + "Pattern", None)
        if fn is None:
            continue
        try:
            if fn() is not None:
                out.append(name)
        except Exception:
            pass
    return out


def _ident(ctrl, rect):
    """元素的**真身份**，用来断环。

    ★ 2026-10-07 实测踩了三个坑，一个比一个隐蔽：

    1. **不能用全局 seen 集合去重。** Edge 一个页面里就有 91 个零尺寸 ButtonControl、
       50 个零尺寸 PaneControl，矩形全是 (0,0,0,0)，全局去重会全并成一个；
       更要命的是命中重复就 `return`，**连子树一起丢** —— 787 个节点直接塌成 3 个。
    2. **矩形+类型也不够。** Chromium 会套多层透明 Pane，父子矩形和类型**完全一样**
       （实测 Edge 有 5 层同矩形 PaneControl），照样误判成环。
    3. 正确做法：**只跟当前祖先链比**（这才是「环」的定义），且身份用官方 RuntimeId。
       实测那 5 个同矩形 Pane 的 RuntimeId 分别是 ...180 / ...182 / ...183 / ...1 / ...796，
       唯一。

    代价：每节点多一次 COM 往返，实测 787 节点整树 ~279 ms，可以接受。
    老版本取不到 RuntimeId 时退回矩形+类型+自动化 id，宁可少断环也别错杀。
    """
    try:
        rid = ctrl.GetRuntimeId()
        if rid:
            return ("rid",) + tuple(rid)
    except Exception:
        pass
    try:
        return ("fb", str(ctrl.ControlTypeName), str(ctrl.AutomationId or ""),
                tuple(rect))
    except Exception:
        return ("fb", tuple(rect))


def _client_rect(handle):
    """窗口**客户区**的屏幕矩形 [l,t,r,b]（物理像素，与 rect 同一坐标系）；取不到返回 None。

    ★ 为什么不用根节点矩形当视口：根矩形含标题栏/边框/标签栏，
      拿它当视口会把标题栏那一圈也算成"看得见"。
    ★ DPI：进程在 _dpi_awareness() 里已经声明 PER_MONITOR_DPI_AWARE，
      所以 GetClientRect/ClientToScreen 与 UIA 的 BoundingRectangle **同一套物理像素**，
      不需要再乘缩放比（早期版本乘过 1.25，那是双乘，反而错位）。
    ★ 多显示器：ClientToScreen 出来可能是负数，原样留着，不做 clamp。
    """
    try:
        import ctypes
        from ctypes import wintypes
        h = wintypes.HWND(int(handle))
        rc = wintypes.RECT()
        if not ctypes.windll.user32.GetClientRect(h, ctypes.byref(rc)):
            return None
        org = wintypes.POINT(0, 0)
        if not ctypes.windll.user32.ClientToScreen(h, ctypes.byref(org)):
            return None
        return [int(org.x), int(org.y),
                int(org.x) + int(rc.right - rc.left),
                int(org.y) + int(rc.bottom - rc.top)]
    except Exception:
        pass
    try:                                   # 老环境没 ctypes.wintypes 时退回 pywin32
        import win32gui
        l, t, r, b = win32gui.GetClientRect(int(handle))
        ox, oy = win32gui.ClientToScreen(int(handle), (0, 0))
        return [int(ox), int(oy), int(ox) + int(r - l), int(oy) + int(b - t)]
    except Exception:
        return None


def _in_view(rect, client):
    """节点屏幕矩形 ∩ 窗口客户区，**面积 > 0** 才算看得见。

    返回 True / False / **None**（客户区取不到 ⇒ 未知）。
    ★ None 时上层一律**不裁剪** —— 判不出来就当看得见，宁可多给不许少给：
      漏给 = agent 说"界面上没有这个东西"，那是更危险的错。
    ★ 相交面积为 0（刚好在客户区下沿之外）判 False；
      客户区在负坐标屏上（左副屏）时照常参与运算，不做 clamp。
    """
    if client is None:
        return None
    w = min(rect[2], client[2]) - max(rect[0], client[0])
    h = min(rect[3], client[3]) - max(rect[1], client[1])
    return bool(w > 0 and h > 0)


def _find_document(root, max_nodes=400, max_depth=14):
    """BFS 找 DocumentControl（Chromium 的整篇 DOM 都挂在它下面）。找不到返回 None。

    深度/节点数都限量：这是**每次遍历多出来的一趟**，不能拿整树的代价换。
    实测 Edge 里 Document 在第 4~6 层，几十个节点内就能碰到。
    """
    q = [(root, 0)]
    seen = 0
    while q and seen < max_nodes:
        ctrl, depth = q.pop(0)
        seen += 1
        try:
            if ctrl.ControlTypeName == "DocumentControl":
                return ctrl
        except Exception:
            continue
        if depth >= max_depth:
            continue
        try:
            for ch in ctrl.GetChildren():
                q.append((ch, depth + 1))
        except Exception:
            continue
    return None


def _scroll_info(root, client):
    """取窗口级滚动真值：scroll_y / doc_height / scroll_unknown / scroll_src。

    ★ **取不到必须 null + scroll_unknown=True，不许填 0。**
      填 0 等于谎报「已经滚到顶」，agent 会据此认为整篇都看过了 —— 比 null 危险得多。
    取值两条路，按精度排：
      ① DocumentControl.BoundingRectangle 是**整篇文档**时（高度明显超过客户区）：
         scroll_y = 客户区上沿 - 文档上沿（像素精确，滚多少就上移多少）。
      ② ScrollPattern 的 VerticalViewSize/VerticalScrollPercent 换算：
         doc_height = 客户区高 × 100 ÷ VerticalViewSize；scroll_y = doc_height × 百分比。
         百分比只有 1% 的刻度 ⇒ 误差约 ±1% 文档高（27900px 的页就有 ±279px），
         所以只当 ① 不成立时的退路，scroll_src 里照实写明。
    都不成立 ⇒ 不知道就是不知道。
    """
    info = {"scroll_y": None, "doc_height": None, "scroll_unknown": True,
            "scroll_src": "取不到（既没 DocumentControl 也没 ScrollPattern）"}
    if client is None:
        info["scroll_src"] = "客户区取不到"
        return info
    ch = client[3] - client[1]
    if ch <= 0:
        info["scroll_src"] = "客户区高度非正"
        return info
    doc = _find_document(root)
    doc_rect = None
    pct = view = None
    if doc is not None:
        try:
            doc_rect = _rect(doc.BoundingRectangle)
        except Exception:
            doc_rect = None
        try:
            sp = doc.GetScrollPattern()
            pct = float(sp.VerticalScrollPercent)
            view = float(sp.VerticalViewSize)
        except Exception:
            pass

    # ① 像素法：文档矩形明显高于客户区 ⇒ 它就是整篇文档
    if doc_rect and (doc_rect[3] - doc_rect[1]) > ch * DOC_TALL_FACTOR:
        info.update(scroll_y=max(0, int(client[1] - doc_rect[1])),
                    doc_height=int(doc_rect[3] - doc_rect[1]),
                    scroll_unknown=False,
                    scroll_src="DocumentControl.BoundingRectangle（像素，精确）")
        return info
    # ①' 短页：文档矩形整个落在客户区内 ⇒ 一屏装得下，滚动量就是 0
    #   ★ 但**必须先确认它真的不滚动**：有些浏览器把 Document 矩形裁到可见那一屏，
    #   页面明明能滚、矩形却只有一屏高。这时若照短页处理，就会谎报 scroll_y=0
    #   （正是本条最危险的错）。ScrollPattern 说它可滚 ⇒ 不许走这条，改走 ②。
    if (doc_rect and doc_rect[3] > doc_rect[1]
            and doc_rect[0] >= client[0] and doc_rect[2] <= client[2]
            and doc_rect[1] >= client[1] and doc_rect[3] <= client[3]
            and (pct is None or view is None
                 or (view >= 99.5 and abs(pct) <= 0.5))):
        info.update(scroll_y=0, doc_height=int(doc_rect[3] - doc_rect[1]),
                    scroll_unknown=False,
                    scroll_src="DocumentControl 矩形完整落在客户区内（一屏装得下）")
        return info
    # ② 百分比法
    if pct is not None and view is not None and 0 < view <= 100.0:
        dh = int(round(ch * 100.0 / view))
        info.update(scroll_y=int(round(dh * pct / 100.0)), doc_height=dh,
                    scroll_unknown=False,
                    scroll_src="ScrollPattern 百分比换算（VerticalViewSize=%.2f%%，"
                               "误差约 ±1%% 文档高）" % view)
    return info


def _viewport_fields(in_view, scroll):
    r"""节点上那三个视口字段（in_view / scroll_y / doc_height / scroll_unknown）。

    单独抽成函数是为了让"跑红自证"能整体覆盖它 —— 把 null 改成 0 的那个缺陷
    就注在这里，一改就红（判据 tests\_视口口径判据.py --inject scroll）。
    """
    return {
        "in_view": in_view,
        "scroll_y": scroll.get("scroll_y"),
        "doc_height": scroll.get("doc_height"),
        "scroll_unknown": bool(scroll.get("scroll_unknown", True)),
    }


def walk(handle, *, max_nodes=DEFAULT_MAX_NODES, max_depth=DEFAULT_MAX_DEPTH,
        roles=("interactive", "text"), detail=False, drop_browser_chrome=True,
        include_offscreen=False, stats=None):
    """遍历一棵窗口的元素树，返回 (节点列表, 是否截断)。

    ★ include_offscreen（F3 · 窗A2 · 2026-10-17，默认 False）= 是否连**视口外**的节点一起回。
      默认只回看得见的；True 时全回，但每个节点都保留 in_view 标记 ——
      **屏外节点不是坏节点，只是「现在看不见」**，要它得先滚动或显式要全量。
    ★ stats（可选 dict，出参）= 填「因上限丢弃 / 因视口丢弃」两个计数，
      让"丢在哪"成为可机读的数字，而不是只留一句 truncated。
    `roles` 决定收哪些：interactive=能点的、text=有字的、all=全要。
    **只读属性，不点不动。**
    """
    auto = _lib()
    root = auto.ControlFromHandle(int(handle))
    if root is None:
        raise UiaUnavailable("句柄 %s 取不到 UIA 根" % handle)

    # ★ 2026-10-07 实测踩的坑（差点当成自己代码写错了）：
    #   窗口最小化时 UIA 仍返回根节点，但 **根的矩形是 (0,0,0,0)**，
    #   子节点坐标是 [-31989,-32000] 这种负数，整棵树是塌的。
    #   此时遍历只会走出个位数节点 —— **看起来像断环逻辑有 bug，其实不是。**
    #   所以先判状态，把话说明白，别让 agent 拿着 3 个节点去推断整个界面。
    st = window_state(int(handle))
    if st["最小化"] or st["根矩形宽"] <= 0 or st["根矩形高"] <= 0:
        raise UiaUnavailable(
            "窗口 %s 不可读：%s。先把它叫醒（SW_RESTORE）再拉树。" % (handle, st["说明"]),
        )

    want_all = "all" in roles
    want_int = want_all or "interactive" in roles
    want_txt = want_all or "text" in roles

    nodes = []
    trunc = [False]

    # ── F3（窗A2）：视口口径**每窗算一次**，逐节点只做一次相交判断 ──────────
    #   客户区用 GetClientRect/ClientToScreen（不是根矩形 —— 根矩形含标题栏/标签栏，
    #   拿它当视口会把窗口边框那一圈也算成「看得见」）。
    #   滚动真值走 _scroll_info：取不到就是 null + scroll_unknown，**绝不填 0**。
    client = _client_rect(int(handle))
    scroll = _scroll_info(root, client)
    # 计数（给 stats 出参用）：丢在哪一步、各丢多少，都是可机读的数字
    cut_cap = [0]        # 因 max_nodes 预算被丢
    cut_view = [0]       # 因不在视口被丢（include_offscreen=False 时）
    seen_any = [0]       # 扫过的节点数（含没保留的）

    def rec(ctrl, depth, ancestors):
        if len(nodes) >= max_nodes:
            trunc[0] = True
            cut_cap[0] += 1
            return
        seen_any[0] += 1
        try:
            rect = _rect(ctrl.BoundingRectangle)
        except Exception:
            return
        ctype = ctrl.ControlTypeName
        key = _ident(ctrl, rect)
        if key in ancestors:
            return
        ancestors = ancestors | {key}

        # ★ 零矩形 = 点不到的东西 = 噪音。Chromium 的树里塞满了这种幽灵节点
        #   （本机实测一个页面就 91 个零尺寸 ButtonControl），不滤掉的话
        #   60 个节点的预算会被 40 行 `[0,0 0x0]` 白吃掉，真正要用的按钮反而排不进来。
        #   **但不 return —— 幽灵节点可能有孩子（真元素藏在下面），要继续往下走。**
        zero = (rect[2] <= rect[0]) or (rect[3] <= rect[1])

        # ★ F3·兜底（窗A2）：TEXT_TYPES 是白名单，白名单天生漏 —— Chromium 的**裸 div**
        #   文本、自定义控件、自绘元素都可能挂在任何 ControlType 上。
        #   所以除白名单外，只要**这一轮要文本**（roles 含 text），就把所有非零矩形节点
        #   都问一遍文字，读到 ≥TEXT_MIN_LEN 字的，一样进 texts。
        #   ★ 要不要多问这一遍由 TEXT_FALLBACK 说了算：置 False 就**逐字退回补前口径**
        #     （只问白名单），顺带把那次多花的 COM 往返也省掉。
        #   ★ 只在 want_txt 时兜底：roles=("interactive",) 这种纯点击用途不受影响。
        txt = ""
        if not zero:
            probe = want_all or ctype in TEXT_TYPES or (TEXT_FALLBACK and want_txt)
            if probe:
                txt = _text_of(ctrl)
        # 单字符不进 texts：图标名/刻度这类噪音会把题干淹掉。
        txt_ok = bool(txt.strip()) and len(txt.strip()) >= TEXT_MIN_LEN
        interactive = ctype in INTERACTIVE_TYPES

        # want_all 时**不筛**：既不是交互类型又没有文字的节点照样要，
        # 否则 roles=("all",) 会退化成跟默认一样，扫过节点数直接对不上（实测 787 → 3）。
        keep = (not zero) and (want_all
                               or (want_int and interactive)
                               or (want_txt and (txt_ok if TEXT_FALLBACK
                                                 else bool(txt.strip()))))
        if keep and drop_browser_chrome and txt.strip() in BROWSER_CHROME_NAMES:
            keep = False

        # ★ F3：视口判定**只标注；只在默认口径下过滤**。
        #   in_view 为 None（客户区取不到）时**不过滤** —— 判不出来就当看得见：
        #   宁可多给，漏给会让 agent 断言「界面上没有这个东西」，那比多给危险得多。
        in_view = None if zero else _in_view(rect, client)
        if keep and in_view is False and not include_offscreen:
            keep = False
            cut_view[0] += 1

        if keep:
            # ★ S2b（2026-10-13）：**读不到 ≠ 启用**。原来 except → True，等于把
            #   「UIA 读不出 IsEnabled」静默当成「可以点」，「别点禁用控件」这道守卫
            #   就自动失效了。但也**不能**默认 False：读不到的控件面积庞大（Canvas /
            #   Electron / 没开无障碍），一律判禁用会变成「什么都点不了」，比原来更糟。
            #   所以显式三态：True=启用 / False=禁用 / None=读不到（未知），未知由 core.t_click 暴露。
            try:
                enabled = bool(ctrl.IsEnabled)
            except Exception:
                enabled = None
            node = {
                "eid": "e%d" % len(nodes),
                "depth": depth,
                "type": ctype,
                "name": txt[:120],
                "rect": rect,
                "center": _center(rect),
                "enabled": enabled,          # True / False / None(未知)
            }
            # ★ F3：视口四字段。**只标注，不改 rect、不换算坐标** ——
            #   坐标系仍是屏幕物理像素（见 COORD_SYS），这里只回答「这一屏看得见吗」。
            node.update(_viewport_fields(in_view, scroll))
            if enabled is None:
                # 机读标记：让「未知」在**数据层**就露出来，不靠渲染文本（模型可能不看那行）。
                # 约定：**只在未知时出现**；键不存在 = 验证过。
                node["enabled_unverified"] = True
            if want_int and interactive:
                node["role"] = "interactive"
            elif want_txt:
                node["role"] = "text"
            if detail:
                node["patterns"] = _patterns(ctrl)
                try:
                    node["aid"] = ctrl.AutomationId or ""
                except Exception:
                    node["aid"] = ""
            nodes.append(node)

        if depth >= max_depth:
            return
        try:
            for ch in ctrl.GetChildren():
                rec(ch, depth + 1, ancestors)
                if trunc[0]:
                    return
        except Exception:
            pass

    rec(root, 0, frozenset())
    if isinstance(stats, dict):
        # 「因上限丢弃」是**下界**：撞到预算就停，不再往下走，树里还剩多少没人知道。
        stats.update({
            "扫过节点": seen_any[0],
            "返回节点": len(nodes),
            "因上限丢弃": cut_cap[0],
            "因视口丢弃": cut_view[0],
            "视口": list(client) if client else None,
            "滚动": dict(scroll),
            "include_offscreen": bool(include_offscreen),
        })
    return nodes, trunc[0]


def window_state(handle):
    """先说清楚窗口能不能读，再去遍历。**返回的是事实，不是判断。**"""
    out = {"句柄": int(handle), "最小化": False, "前台": False,
           "根矩形": [0, 0, 0, 0], "根矩形宽": 0, "根矩形高": 0, "说明": "",
           "坐标系": COORD_SYS}          # ★ W3-A：既有键一个不动，只多这一句标注
    try:
        import ctypes
        h = int(handle)
        out["最小化"] = bool(ctypes.windll.user32.IsIconic(h))
        try:
            import win32gui
            out["标题"] = win32gui.GetWindowText(h)
        except Exception:
            out["标题"] = ""
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            out["前台"] = (int(fg) == h)
        except Exception:
            pass
    except Exception as e:
        out["说明"] = "读窗口状态失败：%s" % e
        return out

    try:
        auto = _lib()
        root = auto.ControlFromHandle(int(handle))
        if root is not None:
            rect = _rect(root.BoundingRectangle)
            out["根矩形"] = rect
            out["根矩形宽"] = rect[2] - rect[0]
            out["根矩形高"] = rect[3] - rect[1]
    except Exception:
        pass

    if out["最小化"]:
        out["说明"] = "窗口已最小化，UIA 树是塌的"
    elif out["根矩形宽"] <= 0 or out["根矩形高"] <= 0:
        out["说明"] = "根矩形为零宽高（%s），多半是最小化或未真正显示" % out["根矩形"]
    else:
        out["说明"] = "可读"
    return out


def render(nodes, truncated=False, *, width=64):
    """紧凑文本树 —— 这是给模型看的默认格式，不是 JSON。

    一个节点一行：`e12  Button  提交  [2350,523 144x48]`。
    JSON 版每个节点 9 个字段，787 个节点就是几万 token —— **刷屏等于没信息**。

    缩进按**相对深度**算：Chromium 会在最上面套 4~5 层透明壳，
    用绝对深度的话每行前面都是十几个空格，纯占地方。
    """
    base = min((n["depth"] for n in nodes), default=0)
    out = []
    for n in nodes:
        r = n["rect"]
        line = "%-5s %-16s %-*s [%5d,%4d %4dx%-4d]" % (
            n["eid"], n["type"][:16], width,
            (n["name"] or "")[:width], r[0], r[1], r[2] - r[0], r[3] - r[1])
        # ★ S2b：缺省也改成 None —— **缺字段同样是「没验证过」**，不许默认 True。
        #   三态在这里全部呈现：禁用→「(禁用)」，未知→「(启用性未知)」（不得当成启用）。
        _en = n.get("enabled", None)
        if _en is False:
            line += " (禁用)"
        elif _en is None:
            line += " (启用性未知)"
        # ★ F3：屏外节点照回时必须**看得见地标出来** —— 它有真实坐标，
        #   agent 拿去点会点到别处。默认口径下不会有这一行（屏外已经被滤掉）。
        if n.get("in_view") is False:
            line += " (屏外·先滚动再点)"
        out.append("  " * min(n["depth"] - base, 6) + line)
    if truncated:
        out.append("... 还有更多节点，被 max_nodes 截断了 —— 要全的自己调大")
    return chr(10).join(out)


def find(handle, query, *, max_nodes=FIND_MAX_NODES, max_depth=DEFAULT_MAX_DEPTH,
         limit=40, detail=False, include_offscreen=True):
    """按文字/类型找元素。**这是最该先用的工具** —— 比拉全树省一个数量级。

    `query` 大小写不敏感，同时匹配名字和控件类型。
    """
    t0 = time.perf_counter()
    # ★ F3：find() 默认**把屏外也带上**（与 walk 的默认相反，这是有意的）：
    #   「找东西」的目标常常正是屏外那一段（长文档里后面的题）。
    #   若这里也按视口裁剪，agent 会得到「找不到」⇒ 误判成「界面上没有这个东西」。
    #   屏外命中照样带 in_view=false，并由「屏外命中」计数显式告诉 agent 要先滚。
    st = {}
    nodes, truncated = walk(handle, max_nodes=max_nodes, max_depth=max_depth,
                           roles=("all",), detail=detail,
                           include_offscreen=include_offscreen, stats=st)
    q = (query or "").lower()
    hits = [
        n for n in nodes
        if (not q)
        or (q in (n["name"] or "").lower())
        or (q in n["type"].lower())
        or (q in (n.get("aid") or "").lower())
    ]
    ms = int((time.perf_counter() - t0) * 1000)
    return {
        "命中": hits[:limit],
        "命中数": len(hits),
        "坐标系": COORD_SYS,              # ★ W3-A：同上，纯增量
        # ★ S2b：默认 detail=False 时「命中」数组**是空的**，光靠节点上的
        #   enabled_unverified 等于没加 —— 所以在返回 dict 上留个计数，
        #   让默认路径也有留痕。计的是**实际返回给模型的那批**（被 limit 截断的不算）。
        "启用性未知": sum(1 for n in hits[:limit] if n.get("enabled") is None),
        # ★ F3：屏外命中数 + 窗口滚动真值（取不到时是 null 且 scroll_unknown=true）
        "屏外命中": sum(1 for n in hits[:limit] if n.get("in_view") is False),
        "滚动": st.get("滚动"),
        "视口口径": VIEWPORT_SYS,          # ★ F3：口径原文随结果走，别让 agent 猜
        "扫过节点": len(nodes),
        "截断": truncated,
        "因上限丢弃": st.get("因上限丢弃", 0),
        "耗时_ms": ms,
        "提示": "id 可以直接给 ctl_click 用，不用自己算坐标",
    }


def tree(handle, *, max_nodes=DEFAULT_MAX_NODES, max_depth=DEFAULT_MAX_DEPTH,
         detail=False, roles=("interactive", "text"), include_offscreen=False):
    """拉整棵树，返回紧凑文本 + 结构化节点。

    ★ F3：include_offscreen 默认 **False**（只回这一屏看得见的）。
      要全量就显式传 True，屏外节点带 in_view=false 一起回 ——
      **屏外节点不是坏节点，别丢，标出来就行。**
    """
    t0 = time.perf_counter()
    st = {}
    nodes, truncated = walk(handle, max_nodes=max_nodes, max_depth=max_depth,
                           roles=roles, detail=detail,
                           include_offscreen=include_offscreen, stats=st)
    return {
        "句柄": int(handle),
        "节点数": len(nodes),
        "截断": truncated,
        # ★ F3：滚动真值 + 口径原文 + 丢在哪（视口丢多少、预算丢多少）
        "滚动": st.get("滚动"),
        "视口": st.get("视口"),
        "视口口径": VIEWPORT_SYS,
        "include_offscreen": bool(include_offscreen),
        "因视口丢弃": st.get("因视口丢弃", 0),
        "因上限丢弃": st.get("因上限丢弃", 0),
        "坐标系": COORD_SYS,              # ★ W3-A：同上，纯增量
        # ★ S2b：同理 —— detail=False 时「节点」是空列表，计数才是默认路径下的可机读余痕。
        "启用性未知": sum(1 for n in nodes if n.get("enabled") is None),
        "耗时_ms": int((time.perf_counter() - t0) * 1000),
        "树": render(nodes, truncated),
        "节点": nodes if detail else [],
    }
