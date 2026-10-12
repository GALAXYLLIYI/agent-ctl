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

# ── 交互类型白名单（照 Windows-MCP 的判据，但只用类型，没抄它整份实现）
INTERACTIVE_TYPES = frozenset({
    "ButtonControl", "EditControl", "CheckBoxControl", "RadioButtonControl",
    "ComboBoxControl", "TabItemControl", "ListItemControl", "MenuItemControl",
    "HyperlinkControl", "SliderControl", "SpinnerControl", "TreeItemControl",
    "DataItemControl",
})

# ── 有文字的类型（要读 Value/Text 的）
TEXT_TYPES = frozenset({
    "TextControl", "EditControl", "DocumentControl", "HyperlinkControl",
    "ListItemControl", "ComboBoxControl", "ButtonControl", "CheckBoxControl",
    "RadioButtonControl", "TabItemControl",
})

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


def walk(handle, *, max_nodes=DEFAULT_MAX_NODES, max_depth=DEFAULT_MAX_DEPTH,
        roles=("interactive", "text"), detail=False, drop_browser_chrome=True):
    """遍历一棵窗口的元素树，返回 (节点列表, 是否截断)。

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

    def rec(ctrl, depth, ancestors):
        if len(nodes) >= max_nodes:
            trunc[0] = True
            return
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

        txt = ""
        if not zero and (ctype in TEXT_TYPES or want_all):
            txt = _text_of(ctrl)
        interactive = ctype in INTERACTIVE_TYPES

        # want_all 时**不筛**：既不是交互类型又没有文字的节点照样要，
        # 否则 roles=("all",) 会退化成跟默认一样，扫过节点数直接对不上（实测 787 → 3）。
        keep = (not zero) and (want_all
                               or (want_int and interactive)
                               or (want_txt and bool(txt.strip())))
        if keep and drop_browser_chrome and txt.strip() in BROWSER_CHROME_NAMES:
            keep = False

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
    return nodes, trunc[0]


def window_state(handle):
    """先说清楚窗口能不能读，再去遍历。**返回的是事实，不是判断。**"""
    out = {"句柄": int(handle), "最小化": False, "前台": False,
           "根矩形": [0, 0, 0, 0], "根矩形宽": 0, "根矩形高": 0, "说明": ""}
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
        out.append("  " * min(n["depth"] - base, 6) + line)
    if truncated:
        out.append("... 还有更多节点，被 max_nodes 截断了 —— 要全的自己调大")
    return chr(10).join(out)


def find(handle, query, *, max_nodes=FIND_MAX_NODES, max_depth=DEFAULT_MAX_DEPTH,
         limit=40, detail=False):
    """按文字/类型找元素。**这是最该先用的工具** —— 比拉全树省一个数量级。

    `query` 大小写不敏感，同时匹配名字和控件类型。
    """
    t0 = time.perf_counter()
    nodes, truncated = walk(handle, max_nodes=max_nodes, max_depth=max_depth,
                           roles=("all",), detail=detail)
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
        # ★ S2b：默认 detail=False 时「命中」数组**是空的**，光靠节点上的
        #   enabled_unverified 等于没加 —— 所以在返回 dict 上留个计数，
        #   让默认路径也有留痕。计的是**实际返回给模型的那批**（被 limit 截断的不算）。
        "启用性未知": sum(1 for n in hits[:limit] if n.get("enabled") is None),
        "扫过节点": len(nodes),
        "截断": truncated,
        "耗时_ms": ms,
        "提示": "id 可以直接给 ctl_click 用，不用自己算坐标",
    }


def tree(handle, *, max_nodes=DEFAULT_MAX_NODES, max_depth=DEFAULT_MAX_DEPTH,
         detail=False, roles=("interactive", "text")):
    """拉整棵树，返回紧凑文本 + 结构化节点。"""
    t0 = time.perf_counter()
    nodes, truncated = walk(handle, max_nodes=max_nodes, max_depth=max_depth,
                           roles=roles, detail=detail)
    return {
        "句柄": int(handle),
        "节点数": len(nodes),
        "截断": truncated,
        # ★ S2b：同理 —— detail=False 时「节点」是空列表，计数才是默认路径下的可机读余痕。
        "启用性未知": sum(1 for n in nodes if n.get("enabled") is None),
        "耗时_ms": int((time.perf_counter() - t0) * 1000),
        "树": render(nodes, truncated),
        "节点": nodes if detail else [],
    }
