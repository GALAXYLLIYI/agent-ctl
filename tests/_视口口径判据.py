# -*- coding: utf-8 -*-
r"""判据 · F3 视口与文本口径（窗A2 · 2026-10-17）

派单：work\dispatch_A2_viewport_text.md
被测：src\agentctl\uia_tree.py

判两个病（都是实机踩出来的，不是推演的）：
    病1 walk() 只滤零矩形、**不裁视口** ⇒ 长文档里 y=17838 的节点被原样返回，
         agent 拿它当屏幕坐标点下去会落到别处。**这不是 agent 用错，是接口没给区分。**
    病2 TEXT_TYPES 不含 PaneControl，而 Chromium 的裸 div 文本常挂在 Pane 上
         ⇒ 题干「读不到」有一部分是**被口径筛掉的**，不是 Chromium 不给。

六条断言（任一红 ⇒ 退出码 1；判据自身不可信 ⇒ 退出码 2）：
    ① 默认口径返回里 in_view=false 的节点数 == 0；
       include_offscreen=True 时它们**在**、带 in_view=false，差集恰好是屏外那批。
    ② 裸 div 题干（挂在 PaneControl 上的文字）读得到；**兜底路径**（非白名单类型上
       ≥2 字的文字）也读得到；单字符不进 texts。
    ③ 滚动取不到时是 **null + scroll_unknown=true**，不是 0（0 会被读成「已滚到顶」）。
    ④ 几何口径（纯函数）：相交面积>0 算看得见；相切不算；负坐标副屏照常参与运算；
       客户区取不到 ⇒ None ⇒ **不裁剪**。
    ⑤ 回归：零矩形节点照旧被丢、浏览器外壳名照旧被丢、eid 连续、
       因 max_nodes 上限丢弃的计数 > 0（"丢在哪"必须可机读）。
    ⑥ 每个返回节点都带 in_view / scroll_y / doc_height / scroll_unknown 四个字段。

跑法：
    python -X utf8 tests\_视口口径判据.py                  # 合成夹具（无桌面也能跑）
    python -X utf8 tests\_视口口径判据.py --live           # 真开 msedge 长页（需交互桌面）
    python -X utf8 tests\_视口口径判据.py --inject in_view # 必红①（in_view 恒置 True）
    python -X utf8 tests\_视口口径判据.py --inject text    # 必红②（TEXT_TYPES 退回补前）
    python -X utf8 tests\_视口口径判据.py --inject scroll  # 必红③（null 改成 0）
    python -X utf8 tests\_视口口径判据.py --inject all     # 三种一起注，逐条报红

★ 为什么默认跑合成夹具而不是真开浏览器：
  本条判据要能在**任何**会话里复现（包括 CI、没有交互桌面的沙箱）。
  合成夹具只替换**COM 边界**（_lib 取窗口根、_client_rect 取客户区），
  被测的 walk() 口径逻辑是**现役文件里的真代码**，一行没改；
  真窗口那条路是 --live，开的是自己的 msedge 窗口，用完立刻关。
★ 本判据**只读**：不发任何点击/输入，不碰用户的窗口，不拉急停。
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UIA = os.path.join(ROOT, "src", "agentctl", "uia_tree.py")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
MARK = "A2视口判据靶页"

CLIENT = [0, 0, 1280, 900]          # 合成夹具的客户区
DOC_HEIGHT = 27900                  # 合成夹具的整篇文档高（≈31 屏，远超派单要求的 3 屏）


# ══════════════════════════════════════════════════════════════════════
# 合成夹具：一棵**照 Chromium 实测形状**搭的假 UIA 树
#   照抄的是 uia_tree.py 文件头里已写明的事实：同矩形多层透明 Pane、零尺寸幽灵 Button、
#   裸 div 文本挂在 Pane 上、整篇 DOM 挂在 DocumentControl 下。
# ══════════════════════════════════════════════════════════════════════
class _Rect(object):
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b


class _Pattern(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Ctrl(object):
    """够 walk() 用的最小控件替身（只实现 walk 真会问的那几个属性）。"""

    def __init__(self, ctype, rect, name="", children=(), rid=None, scroll=None):
        self.ctype = ctype
        self.rect = list(rect)
        self.name = name
        self.children = list(children)
        self.rid = rid or [abs(hash((ctype, tuple(rect), name))) % 100000, 1]
        self.scroll = scroll

    @property
    def BoundingRectangle(self):
        return _Rect(*self.rect)

    @property
    def ControlTypeName(self):
        return self.ctype

    @property
    def Name(self):
        return self.name

    @property
    def AutomationId(self):
        return ""

    @property
    def IsEnabled(self):
        return True

    def GetRuntimeId(self):
        return list(self.rid)

    def GetChildren(self):
        return list(self.children)

    def GetInvokePattern(self):
        return None

    def GetTogglePattern(self):
        return None

    def GetSelectionItemPattern(self):
        return None

    def GetValuePattern(self):
        return None

    def GetExpandCollapsePattern(self):
        return None

    def GetScrollPattern(self):
        return _Pattern(**self.scroll) if self.scroll else None

    def GetTextPattern(self):
        return None

    def GetLegacyIAccessiblePattern(self):
        return None


class _FakeAuto(object):
    """替身 uiautomation 模块：只提供 walk() 用到的两个入口。"""

    def __init__(self, root):
        self.root = root

    def ControlFromHandle(self, handle):
        return self.root

    def SetGlobalSearchTimeout(self, t):
        return None

    def InitializeUIAutomationInCurrentThread(self):
        return None


def long_page_tree():
    """一页 27900px 的长文档（≈31 屏）。返回 (根控件, 客户区)。"""
    kids = []
    # Chromium 套的 5 层同矩形透明 Pane（实测它们矩形与类型完全一样）
    shell = [_Ctrl("PaneControl", [0, 0, 1280, 900], rid=[900 + i, 1])
             for i in range(5)]
    doc = _Ctrl("DocumentControl", [0, 0, 1280, DOC_HEIGHT], rid=[800, 1],
                scroll={"VerticalScrollPercent": 0.0,
                        "VerticalViewSize": round(100.0 * CLIENT[3] / DOC_HEIGHT, 4)})
    content = []
    # 题干与选项：Chromium 的**裸 div**，文字挂在 PaneControl 上
    content.append(_Ctrl("PaneControl", [20, 100, 1260, 160],
                         "题干裸div_题目标记XYZ 第1题：下列说法正确的是"))
    content.append(_Ctrl("PaneControl", [20, 170, 1260, 230], "选项A裸div：先有鸡"))
    content.append(_Ctrl("PaneControl", [20, 240, 1260, 300], "选项B裸div：先有蛋"))
    # 视口内按钮（可点）
    content.append(_Ctrl("ButtonControl", [20, 320, 200, 380], "提交"))
    # 单字符文字：按 TEXT_MIN_LEN=2 不该进 texts（图标名/刻度这类噪音）
    content.append(_Ctrl("PaneControl", [300, 320, 320, 380], "x"))
    # 无名 Pane：不该进 texts
    content.append(_Ctrl("PaneControl", [400, 320, 600, 380], ""))
    # 非白名单类型上带字（GroupControl）⇒ 只有**兜底**能捞到
    content.append(_Ctrl("GroupControl", [620, 320, 900, 380], "兜底文字丙丙丙"))
    # 部分可见（跨过客户区下沿但有面积）⇒ 算看得见
    content.append(_Ctrl("PaneControl", [20, 860, 1260, 940], "跨下沿部分可见段段段"))
    # 与客户区下沿**相切**（面积 0）⇒ 算看不见
    content.append(_Ctrl("PaneControl", [20, 900, 1260, 980], "刚好在视口下沿外段段段"))
    # 视口外的一堆填充段 + 一个视口外按钮（以前会被当可点的原样返回）
    for i in range(60):
        y = 1000 + i * 280
        content.append(_Ctrl("PaneControl", [20, y, 1260, y + 60], "填充段%02d" % i))
    content.append(_Ctrl("ButtonControl", [20, 15000, 240, 15070], "下一题"))
    content.append(_Ctrl("PaneControl", [20, DOC_HEIGHT - 160, 1260, DOC_HEIGHT - 100],
                         "末尾裸div_页尾标记QQQ"))
    doc.children = content
    kids.append(doc)
    kids.extend(shell)
    # 零尺寸幽灵节点：点不到、但可能有孩子 ⇒ 照旧丢掉自己、继续往下走
    kids.append(_Ctrl("ButtonControl", [0, 0, 0, 0], "幽灵按钮甲",
                      children=[_Ctrl("PaneControl", [20, 400, 400, 460],
                                       "藏在幽灵下的真文字丙")],
                      rid=[700, 1]))
    kids.append(_Ctrl("ButtonControl", [0, 0, 0, 0], "关闭", rid=[701, 1]))
    root = _Ctrl("PaneControl", [0, 0, 1280, 940], "", children=kids, rid=[1, 1])
    return root, list(CLIENT)


def short_window_tree():
    """一屏装得下、**又没有滚动信息**的窗口（记事本那种）⇒ scroll_y 必须是 null。"""
    root = _Ctrl("PaneControl", [0, 0, 800, 500], "", rid=[2, 1], children=[
        _Ctrl("TextControl", [10, 10, 700, 300], "记事本里的一段文字甲乙丙"),
        _Ctrl("ButtonControl", [10, 320, 120, 380], "确定"),
    ])
    return root, [0, 0, 800, 500]


def clipped_doc_window_tree(client_h=900, view=10.0, pct=50.0):
    """**最险的那种浏览器**：Document 矩形被裁到可见那一屏，可页面明明能滚。

    这时候若按「短页」处理，会报 scroll_y=0 / doc_height=一屏高 ——
    等于谎报「已经滚到顶、整篇都在这一屏」，正是本条最危险的错。
    ScrollPattern 说它可滚 ⇒ 必须走百分比法给出真值。
    """
    doc = _Ctrl("DocumentControl", [0, 0, 1280, client_h], rid=[801, 1],
                scroll={"VerticalScrollPercent": pct, "VerticalViewSize": view})
    doc.children = [
        _Ctrl("PaneControl", [20, 40, 1260, 100], "被裁窗口里的题干文字丁戊"),
        _Ctrl("PaneControl", [20, 200, 1260, 260], "被裁窗口里的选项文字己庚"),
    ]
    root = _Ctrl("PaneControl", [0, 0, 1280, 940], "", children=[doc], rid=[3, 1])
    return root, [0, 0, 1280, client_h]

def install_stubs(mod, root, client):
    """只替换**COM 边界**：_lib（取窗口根）与 _client_rect（取客户区）。

    被测的 walk() 本身是**现役文件里的真代码**，一行没改 ——
    换掉的只是「怎么拿到根控件 / 怎么拿客户区」这两个 I/O 入口。
    """
    mod._lib = lambda: _FakeAuto(root)
    mod._client_rect = lambda handle: list(client)


def load_module(path, alias):
    """按路径加载一份 uia_tree（好让 --inject 指注入了缺陷的副本去跑红）。"""
    path = os.path.abspath(path)
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise SystemExit("加载失败：%s" % path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m, path


def node_key(n):
    return (n["type"], (n["name"] or "")[:40], tuple(n["rect"]))


# ══════════════════════════════════════════════════════════════════════
# 注入器（跑红自证）：复制一份到 %TEMP% → 在副本上注入一个已知缺陷 → 同一套断言跑它
#   产品代码一个字节都不改；副本路径打印出来，谁都能复核「改的是副本不是现役」。
# ══════════════════════════════════════════════════════════════════════
INJECT_IN_VIEW = """

# ===== 以下为 视口口径判据 注入段（跑红自证专用，勿入产品代码）=====
def _in_view(rect, client):          # noqa: F811  故意覆盖真版
    \"\"\"注入的缺陷：不做视口相交判断，恒说「看得见」。\"\"\"
    return True
"""

INJECT_SCROLL = """

# ===== 以下为 视口口径判据 注入段（跑红自证专用，勿入产品代码）=====
def _viewport_fields(in_view, scroll):   # noqa: F811  故意覆盖真版
    \"\"\"注入的缺陷：不知道滚动位置时填 0，而不是 null + scroll_unknown。\"\"\"
    return {"in_view": in_view,
            "scroll_y": scroll.get("scroll_y") or 0,
            "doc_height": scroll.get("doc_height") or 0,
            "scroll_unknown": bool(scroll.get("scroll_unknown", True))}
"""

INJECT = {
    "in_view": ("把 in_view 恒置 True（病1 原样：分不出屏内屏外，全当看得见）",
                "append", INJECT_IN_VIEW),
    "scroll": ("把 scroll 取不到时的 null 改成 0（谎报「已滚到顶」）",
               "append", INJECT_SCROLL),
    "text": ("把 TEXT_TYPES 退回补前（去掉 PaneControl）并关掉兜底", "edit", None),
}

EDIT_TEXT = [
    ("TEXT_FALLBACK = True", "TEXT_FALLBACK = False"),
    ('"RadioButtonControl", "TabItemControl", "PaneControl",',
     '"RadioButtonControl", "TabItemControl",'),
]


def build_injected(tmp, kinds):
    """造一份注入了缺陷的 uia_tree 副本，返回 (路径, 说明)。锚点没命中 ⇒ None。"""
    with io.open(UIA, "r", encoding="utf-8") as f:
        src = f.read()
    notes = []
    for k in kinds:
        desc, mode, snippet = INJECT[k]
        if mode == "append":
            src = src + snippet
        else:
            for old, new in EDIT_TEXT:
                if old not in src:
                    return None, ("注入锚点没命中 ⇒ 判据自身不可信：%r "
                                  "（产品文件已改版？先更新本判据的注入锚点）" % old)
                src = src.replace(old, new, 1)
        notes.append("%s=%s" % (k, desc))
    dst = os.path.join(tmp, "uia_tree_注入缺陷_%s.py" % "_".join(kinds))
    with io.open(dst, "w", encoding="utf-8") as f:
        f.write(src)
    return dst, ("；".join(notes))


# ══════════════════════════════════════════════════════════════════════
# 断言组
# ══════════════════════════════════════════════════════════════════════
def check_viewport(mod):
    """断言①：默认只回视口内；全量口径下屏外节点在、且带 in_view=false。"""
    root, client = long_page_tree()
    install_stubs(mod, root, client)
    reds = []
    st_def = {}
    view_nodes, _ = mod.walk(424242, roles=("interactive", "text"),
                             max_nodes=2000, stats=st_def)
    all_nodes, _ = mod.walk(424242, roles=("interactive", "text"),
                            max_nodes=2000, include_offscreen=True)
    bad = [n for n in view_nodes if n.get("in_view") is False]
    if bad:
        reds.append("① 默认口径仍回了 %d 个 in_view=false 的节点，例：%s"
                    % (len(bad), (bad[0]["name"] or bad[0]["type"])[:30]))
    offscreen = [n for n in all_nodes if n.get("in_view") is False]
    if not offscreen:
        reds.append("① include_offscreen=True 一个屏外节点都没回 ⇒ 夹具或口径失效，"
                    "这条断言没有判别力")
    d = set(node_key(n) for n in view_nodes)
    a = set(node_key(n) for n in all_nodes)
    if a - d != set(node_key(n) for n in offscreen):
        reds.append("① 全量比默认多出的 %d 个节点与「屏外集合」对不上（多半是别的节点被多丢了）"
                    % len(a - d))
    if d - a:
        reds.append("① 全量口径反而少了 %d 个节点（include_offscreen=True 不许更少）"
                    % len(d - a))
    # 视口外的可点按钮必须**只在**全量里出现，且带 in_view=false（本题的靶子）
    far_btn = [n for n in all_nodes if n["name"] == "下一题"]
    if not far_btn:
        reds.append("① 视口外的「下一题」按钮没在全量口径里出现")
    elif far_btn[0].get("in_view") is not False:
        reds.append("① 视口外的「下一题」按钮 in_view 不是 False（=%r）"
                    % far_btn[0].get("in_view"))
    if any(n["name"] == "下一题" for n in view_nodes):
        reds.append("① 视口外的「下一题」按钮出现在默认口径里（病1 没治好）")
    if not (st_def.get("因视口丢弃") or 0) > 0:
        reds.append("① stats 因视口丢弃 = %r，必须 >0（丢在哪要可机读）"
                    % st_def.get("因视口丢弃"))
    return {
        "红": reds,
        "默认节点数": len(view_nodes),
        "全量节点数": len(all_nodes),
        "屏外数": len(offscreen),
        "因视口丢弃": st_def.get("因视口丢弃", 0),
        "远端按钮": far_btn[0] if far_btn else None,
        "样例_屏外": offscreen[:2],
        "样例_视口内": view_nodes[:2],
    }


def check_text(mod):
    """断言②：裸 div 题干读得到（PaneControl 白名单补齐 + 非白名单兜底两条路）。"""
    root, client = long_page_tree()
    install_stubs(mod, root, client)
    reds = []
    nodes, _ = mod.walk(424242, roles=("interactive", "text"), max_nodes=2000)
    texts = [n for n in nodes if n.get("role") == "text"]

    def seen(key):
        return [t for t in texts if key in (t["name"] or "")]

    xyz, group = seen("XYZ"), seen("丙丙丙")
    if not xyz:
        reds.append("② 挂在 PaneControl 上的裸 div 题干读不到（病2：白名单漏了 PaneControl）")
    if not group:
        reds.append("② 非白名单类型（GroupControl）上 ≥2 字的文字读不到（兜底没生效）")
    if [t for t in texts if (t["name"] or "") == "x"]:
        reds.append("② 单字符节点进了 texts（TEXT_MIN_LEN 口径失效，噪音会淹题干）")
    all_nodes, _ = mod.walk(424242, roles=("interactive", "text"),
                            max_nodes=2000, include_offscreen=True)
    if not [n for n in all_nodes if "QQQ" in (n["name"] or "")]:
        reds.append("② 全量口径下读不到屏外的页尾文字（屏外不等于坏节点）")
    return {
        "红": reds,
        "读到题干": [t["name"] for t in xyz],
        "读到兜底": [t["name"] for t in group],
        "texts数": len(texts),
        "单字符进了": bool([t for t in texts if (t["name"] or "") == "x"]),
    }


def check_scroll(mod):
    """断言③：滚动取不到时是 null + scroll_unknown=true，**不是 0**。"""
    reds = []
    root, client = long_page_tree()
    install_stubs(mod, root, client)
    long_nodes, _ = mod.walk(424242, roles=("interactive", "text"), max_nodes=2000)
    if not long_nodes:
        reds.append("③ 长页夹具一个节点都没扫出来 ⇒ 夹具坏了")
    doc_h = long_nodes[0].get("doc_height") if long_nodes else None
    if not (isinstance(doc_h, int) and doc_h > 3 * CLIENT[3]):
        reds.append("③ 长页 doc_height=%r，应 > 3 屏（>%d）才能验『屏外节点』这件事"
                    % (doc_h, 3 * CLIENT[3]))
    # 短窗：没有 Document、没有 ScrollPattern ⇒ 必须 null
    root2, client2 = short_window_tree()
    install_stubs(mod, root2, client2)
    short_nodes, _ = mod.walk(424242, roles=("interactive", "text"), max_nodes=200)
    if not short_nodes:
        reds.append("③ 短窗夹具一个节点都没扫出来 ⇒ 夹具坏了")
    for n in short_nodes:
        if n.get("scroll_y") is not None:
            reds.append("③ 滚动未知时 scroll_y=%r，应该是 null（填 0 等于谎报「已滚到顶」）"
                        % (n.get("scroll_y"),))
            break
        if n.get("doc_height") is not None:
            reds.append("③ 滚动未知时 doc_height=%r，应该是 null" % (n.get("doc_height"),))
            break
        if n.get("scroll_unknown") is not True:
            reds.append("③ 滚动未知时 scroll_unknown=%r，必须是 True（要让 agent 看见"
                        "「不知道」，而不是替它猜）" % (n.get("scroll_unknown"),))
            break
    # 「Document 矩形被裁到一屏、但页面能滚」：绝不许报 scroll_y=0
    root3, client3 = clipped_doc_window_tree()
    install_stubs(mod, root3, client3)
    clip_nodes, _ = mod.walk(424242, roles=("interactive", "text"), max_nodes=200)
    if not clip_nodes:
        reds.append("③ 裁剪矩形夹具一个节点都没扫出来 ⇒ 夹具坏了")
    clip = clip_nodes[0] if clip_nodes else {}
    if clip.get("scroll_y") == 0 or clip.get("doc_height", 0) <= client3[3]:
        reds.append("③ Document 矩形被裁成一屏时仍报 scroll_y=%r / doc_height=%r "
                    "（谎报「已滚到顶」—— 该走 ScrollPattern 百分比法）"
                    % (clip.get("scroll_y"), clip.get("doc_height")))
    if clip.get("scroll_unknown") is not False:
        reds.append("③ 裁剪矩形夹具里 ScrollPattern 明明给了值，scroll_unknown=%r"
                    % (clip.get("scroll_unknown"),))
    return {
        "红": reds,
        "长页": ({"doc_height": doc_h,
                  "scroll_y": long_nodes[0].get("scroll_y") if long_nodes else None,
                  "scroll_unknown": long_nodes[0].get("scroll_unknown") if long_nodes else None}
                 if long_nodes else {}),
        "短窗": ({"scroll_y": short_nodes[0].get("scroll_y"),
                  "doc_height": short_nodes[0].get("doc_height"),
                  "scroll_unknown": short_nodes[0].get("scroll_unknown")}
                 if short_nodes else {}),
        "矩形被裁的可滚窗": {"scroll_y": clip.get("scroll_y"),
                            "doc_height": clip.get("doc_height"),
                            "scroll_unknown": clip.get("scroll_unknown")},
    }


def check_geometry(mod):
    """断言④：几何口径纯函数单测（客户区可能在负坐标副屏上）。"""
    reds = []
    cases = [
        ([0, 0, 100, 100], [0, 0, 800, 600], True, "完全在视口内"),
        ([700, 500, 900, 700], [0, 0, 800, 600], True, "右下角露出一条"),
        ([0, 600, 800, 700], [0, 0, 800, 600], False, "与客户区下沿相切（面积 0）"),
        ([0, 700, 800, 900], [0, 0, 800, 600], False, "整块在视口下方"),
        ([-1900, 100, -1800, 200], [-1920, 0, -1000, 1080], True, "左副屏（客户区 x 为负）"),
        ([-300, 100, -100, 200], [-1920, 0, -1000, 1080], False,
         "客户区左侧之外（负坐标不等于全都不算）"),
        ([0, 0, 100, 100], None, None, "客户区取不到 ⇒ None（未知，不裁剪）"),
    ]
    for rect, client, want, why in cases:
        got = mod._in_view(list(rect), None if client is None else list(client))
        if got != want:
            reds.append("④ %s：_in_view(%s, %s)=%r，期望 %r" % (why, rect, client, got, want))
    return {"红": reds, "用例数": len(cases)}


def check_regress(mod):
    """断言⑤⑥：老口径不许回退 + 每个节点都带视口四字段。"""
    reds = []
    root, client = long_page_tree()
    install_stubs(mod, root, client)
    nodes, _ = mod.walk(424242, roles=("interactive", "text"), max_nodes=2000)
    if [n for n in nodes if n["name"] == "幽灵按钮甲"]:
        reds.append("⑤ 零矩形节点没被丢掉（预算会被幽灵吃光）")
    if [n for n in nodes if (n["name"] or "") == "关闭"]:
        reds.append("⑤ 浏览器外壳名没被滤掉")
    ids = [int(n["eid"][1:]) for n in nodes]
    if ids != list(range(len(ids))):
        reds.append("⑤ eid 不连续（回退到哪儿了？）")
    # ★ 下面两条走 roles=("all",)：它们验的是**遍历/预算**口径，
    #   必须与文本口径解耦 —— 否则「退回补前文本口径」会把树缩到几个节点，
    #   预算压根撞不上，⑤ 就在一个跟被注入维度无关的地方误报红。
    all_nodes, _ = mod.walk(424242, roles=("all",), max_nodes=2000)
    if not [n for n in all_nodes if "藏在幽灵下的真文字丙" in (n["name"] or "")]:
        reds.append("⑤ 零矩形节点的孩子被一起丢了（丢掉自己 ≠ 砍掉子树）")
    st2 = {}
    few, trunc = mod.walk(424242, roles=("all",), max_nodes=5, stats=st2)
    if len(few) > 5:
        reds.append("⑤ max_nodes=5 却回了 %d 个节点（上限没生效）" % len(few))
    if not (st2.get("因上限丢弃") or 0) > 0:
        reds.append("⑤ stats 因上限丢弃=%r，撞了预算却没记账（截断多少必须可机读）"
                    % (st2.get("因上限丢弃"),))
    if not trunc:
        reds.append("⑤ 撞了上限但 truncated=False")
    for n in nodes[:20]:
        for k in ("in_view", "scroll_y", "doc_height", "scroll_unknown"):
            if k not in n:
                reds.append("⑥ 节点缺字段 %s（缺字段同样是「没说」）" % k)
                break
    return {"红": reds, "节点数": len(nodes),
            "小预算丢弃": st2.get("因上限丢弃", 0)}


# ══════════════════════════════════════════════════════════════════════
# 真窗口（--live）：开自己的 msedge 长页，量完立刻关
# ══════════════════════════════════════════════════════════════════════
def make_long_html(tmp, blocks=140):
    parts = ['<!doctype html><html><head><meta charset="utf-8"><title>' + MARK + "</title>",
             "<style>body{margin:0;font:16px/1.6 sans-serif}"
             "div{padding:14px 20px;border-bottom:1px solid #eee}"
             "div.t{background:#eef;padding:24px 20px;font-size:22px}</style></head><body>",
             '<div class="t">题干裸div_题目标记XYZ 第1题：下列说法正确的是</div>',
             '<div class="t">选项A裸div：先有鸡</div>',
             '<div class="t">选项B裸div：先有蛋</div>',
             "<button>提交</button>",
             "<button>下一题</button>"]
    for i in range(blocks):
        parts.append("<div>填充块 %d —— 纯文本无 aria</div>" % i)
    parts.append('<div class="t">末尾裸div_页尾标记QQQ</div>')
    parts.append("</body></html>")
    p = os.path.join(tmp, "long.html")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return p


def find_own_window(mark, timeout=25.0):
    import win32gui
    t0 = time.time()
    while time.time() - t0 < timeout:
        hits = []

        def cb(h, _):
            if mark in win32gui.GetWindowText(h) and win32gui.IsWindowVisible(h):
                hits.append(h)
        win32gui.EnumWindows(cb, None)
        if hits:
            return hits[0]
        time.sleep(0.5)
    return None


def count_border_windows():
    """退出前自检：AgentCtlCtrlBorder* 窗必须 = 0（判据自己不许留边框窗）。"""
    try:
        import win32gui
        n = [0]

        def cb(h, _):
            if win32gui.GetWindowText(h).startswith("AgentCtlCtrlBorder"):
                n[0] += 1
        win32gui.EnumWindows(cb, None)
        return n[0]
    except Exception as e:
        return "读不到（%s: %s）" % (type(e).__name__, e)


def run_live(mod):
    """真开 msedge 长页跑同一套口径断言。返回 (红, 证据, 环境是否被挡)。"""
    reds = []
    ev = {}
    tmp = tempfile.mkdtemp(prefix="_A2判据_实机_")
    page = make_long_html(tmp)
    url = "file:///" + page.replace("\\", "/")
    proc = None
    try:
        if not os.path.exists(EDGE):
            return (["--live：找不到 msedge（%s）" % EDGE], {"夹具": page}, False)
        proc = subprocess.Popen([EDGE, "--new-window", "--window-size=1280,900", url],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        hwnd = find_own_window(MARK)
        if not hwnd:
            why = ("--live：起了 msedge，但按标题找不到自己的窗口"
                   "（这个会话多半没有交互桌面 —— EnumWindows 看不到任何顶层窗口）")
            return ([why], {"夹具": page, "退出前边框窗": count_border_windows()}, True)
        ev["hwnd"] = hwnd
        time.sleep(1.5)
        st_def = {}
        view_nodes, _ = mod.walk(hwnd, roles=("interactive", "text"),
                                 max_nodes=2000, stats=st_def)
        all_nodes, _ = mod.walk(hwnd, roles=("interactive", "text"),
                                max_nodes=2000, include_offscreen=True)
        ev["客户区"] = st_def.get("视口")
        ev["滚动"] = st_def.get("滚动")
        ev["默认节点数"] = len(view_nodes)
        ev["全量节点数"] = len(all_nodes)
        ev["因视口丢弃"] = st_def.get("因视口丢弃", 0)
        doc_h = (st_def.get("滚动") or {}).get("doc_height")
        ev["doc_height"] = doc_h
        if not (isinstance(doc_h, int) and doc_h > 0):
            reds.append("--live：滚动真值取不到（scroll_unknown=%r）—— 真窗口上必须取得到，"
                        "否则 scroll_y/doc_height 只是装饰"
                        % (st_def.get("滚动") or {}).get("scroll_unknown"))
        if [n for n in view_nodes if n.get("in_view") is False]:
            reds.append("--live：默认口径仍回了屏外节点")
        off = [n for n in all_nodes if n.get("in_view") is False]
        if not off:
            reds.append("--live：全量口径里没有屏外节点（页面不够长？doc_height=%r）" % (doc_h,))
        ev["屏外样例"] = [{k: n[k] for k in ("eid", "type", "name", "rect", "in_view",
                                         "scroll_y", "doc_height", "scroll_unknown")}
                        for n in (off[:2] + view_nodes[:1])]
        names = [n["name"] or "" for n in all_nodes]
        ev["题干样例"] = [x for x in names if "XYZ" in x][:2]
        if not ev["题干样例"]:
            reds.append("--live：读不到裸 div 题干")
    finally:
        try:
            import win32gui

            def cb(h, _):
                if MARK in win32gui.GetWindowText(h):
                    win32gui.PostMessage(h, 0x0010, 0, 0)
            win32gui.EnumWindows(cb, None)
        except Exception:
            pass
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:
                pass
        ev["退出前 AgentCtlCtrlBorder* 窗"] = count_border_windows()
    return reds, ev, False


def main():
    ap = argparse.ArgumentParser(description="F3 视口与文本口径判据（窗A2）")
    ap.add_argument("--live", action="store_true",
                    help="真开 msedge 长页跑（需要有交互桌面）")
    ap.add_argument("--inject", choices=sorted(INJECT) + ["all"],
                    help="自证跑红：把现役 uia_tree.py 复制到 %%TEMP%% 注入缺陷再跑同一套断言")
    args = ap.parse_args()

    kinds = ["in_view", "scroll", "text"] if args.inject == "all" else (
        [args.inject] if args.inject else [])

    tmp = tempfile.mkdtemp(prefix="_A2判据_")
    target, notes = UIA, "现役文件（未注入）"
    if kinds:
        target, notes = build_injected(tmp, kinds)
        if target is None:
            print("判据自身不可信：%s" % notes)
            return 2
    mod, path = load_module(target, "_A2_uia_under_test")

    print("=" * 108)
    print("窗A2 · F3 视口与文本口径判据")
    print("被测 uia_tree : %s" % path)
    print("模式          : %s" % notes)
    print("夹具          : 合成长页（%d px ≈ %.1f 屏）+ 短窗（无滚动信息）"
          % (DOC_HEIGHT, DOC_HEIGHT / float(CLIENT[3])))
    print("=" * 108)

    res = {}
    for name, fn in (("① 视口", check_viewport), ("② 题干文本", check_text),
                     ("③ 滚动口径", check_scroll), ("④ 几何口径", check_geometry),
                     ("⑤⑥ 回归与字段", check_regress)):
        r = fn(mod)
        res[name] = r
        print("")
        print("── %s ── %s" % (name, "红" if r["红"] else "绿"))
        for line in r["红"]:
            print("   ★★红 · %s" % line)
        for k, v in r.items():
            if k == "红":
                continue
            print("   %s = %s" % (k, json.dumps(v, ensure_ascii=False)[:700]))

    print("")
    print("── 「补前 vs 补后」题干可读性（两次真实输出）──")
    after = res["② 题干文本"]
    if kinds != ["text"]:
        dst, _ = build_injected(tmp, ["text"])
        bmod, bpath = load_module(dst, "_A2_uia_before")
        before = check_text(bmod)
        print("   补前（%s）：读到题干=%s · 读到兜底=%s · texts数=%d"
              % (os.path.basename(bpath), before["读到题干"] or "【读不到】",
                 before["读到兜底"] or "【读不到】", before["texts数"]))
    else:
        print("   本次就是补前注入：读到题干=%s · 读到兜底=%s · texts数=%d"
              % (after["读到题干"] or "【读不到】",
                 after["读到兜底"] or "【读不到】", after["texts数"]))
    print("   补后（现役）  ：读到题干=%s · 读到兜底=%s · texts数=%d"
          % (after["读到题干"] or "【读不到】",
             after["读到兜底"] or "【读不到】", after["texts数"]))

    total = sum(len(r["红"]) for r in res.values())

    if args.live:
        print("")
        print("── --live 真窗口 ──")
        reds, ev, blocked = run_live(mod)
        for k, v in ev.items():
            print("   %s = %s" % (k, json.dumps(v, ensure_ascii=False)[:700]))
        for line in reds:
            print("   ★★红 · %s" % line)
        if blocked:
            print("")
            print("   [环境限制] 本会话没有交互桌面（EnumWindows 看不到任何顶层窗口），"
                  "真窗口那半段跑不了。")
            print("            **这不算红也不算绿** —— 宁可跑不起来，也不要半个结论。")
            print("            有桌面的会话请重跑：python -X utf8 tests\\_视口口径判据.py --live")
        else:
            total += len(reds)
        if blocked:
            print("")
            print("=" * 108)
            print("退出前 AgentCtlCtrlBorder* 窗 = %s" % count_border_windows())
            print("=" * 108)
            shutil.rmtree(tmp, ignore_errors=True)
            return 2

    print("")
    print("=" * 108)
    if total:
        print("结论：RED —— 红 %d 条%s"
              % (total, ("（注入模式：注入的定义就是必红，红了正说明判据有判别力）"
                         if kinds else "")))
    else:
        print("结论：GREEN —— ①默认只回视口内/全量带 in_view；②裸 div 题干与兜底文字都读得到；"
              "③滚动未知是 null 不是 0；④几何口径（含负坐标副屏）逐条相符；"
              "⑤零矩形/外壳名/eid/上限计数无回退；⑥四字段齐全。")
    print("退出前 AgentCtlCtrlBorder* 窗 = %s" % count_border_windows())
    print("=" * 108)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
