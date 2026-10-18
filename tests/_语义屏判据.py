# -*- coding: utf-8 -*-
r"""判据 · F4 语义屏 ctl_screen（窗B1 · 2026-10-17）

派单：work\dispatch_B1_ctl_screen.md
被测：src\agentctl\core.py 的 ctl_screen / _screen_* + contract\tools.json 的 ctl_screen 条目

判六件事（任一红 ⇒ 退出码 1；判据自身不可信 ⇒ 退出码 2）：
    ① 默认 top=40：返回 ≤40，且**全部视口内可交互节点都在**；omitted 诚实报数。
    ② uid 连续三次不变；改窗口标题后 screen_version 变；screen_version **服务端签发**
       （回传 since / 传 version 都改不了它）。
    ③ volatile 豁免：计时器节点 volatile=true 且两次调用 uid 不变（不触发 STALE 风暴）；
       **同时**证明非 volatile 节点改了字 uid **会**变 —— 两半合起来才说明
       「text_key 参与比对」这件事真的在跑（只判前半截的话，
       把 text_key 整个删掉也能过，注入就白注了）。
    ④ source_confidence 多来源**取 min**（造一条 OCR 证据混进 UIA 里验）。
    ⑤ channels 段：must_vlm = not R1 and not R2 and not R3 逐格对上；
       name_trust 对 Chromium 窗口给 low；返回里**没有裸 confidence 字段**。
    ⑥ 契约与接线：tools.json 有 ctl_screen、JSON 合法、TOOLS 表里有它。
    ⑦ 属性级置信度：每条证据按 {bbox,text,existence} 分档；对象上三档都在，
       聚合 = min(existence,text)，且 **conf_bbox 不被 conf_text 拖低**
       （造一条「UIA 坐标精确 + OCR 字很弱」的证据来验）。
    ⑧ uid 三档：改文本 ⇒ TEXT_CHANGED（动作核验期待这一档）；挪结构 ⇒ STALE；
       都不变 ⇒ OK；没有基线 ⇒ STALE。
    ⑨ must_vlm 的 R2 加 name_trust 门槛：offset-suspect ⇒ **R2 不成立**、
       must_vlm 转 True；对照组：low 不拦（别把网页一刀切）、原生窗口不报错位。
    ⑩ 给了 since：since_echo 原样回传 + version_match 正确 + 带回**新** screen_version。
    S1~S3 任务声明制 watch_text（窗D1 · Z3 §4）：
       S1 声明过的 volatile 节点改了 ⇒ uid_state=TEXT_CHANGED ∧ delta 记新旧 ∧
          screen_version 进位且**恰好**归因到它（声明压过一切豁免源）；
       S2 **未声明**的倒计时改了 ⇒ uid 恒 skey@~ ∧ version 不进位（豁免没失守 ⇒
          STALE 风暴没回流）；
       S3 声明一个不存在的串 ⇒ rc≠0 且 watch_校验.unmatched 逐条列明（不许静默）；
          watch_pending=true 才允许挂起；条数上限 32。
    S11 cdp.py 的 `--port`（窗B 2026-10-18）：`--port 9333` 真去连 :9333、不给参数仍连 :9222，
       真源与产物都如此；＋ 契约写明「结果包在 `data.结果` 里（objects 在 `data.结果.objects`）」
       与「ctl_browser 的 port 与 `cdp.py --port` 同步」。

跑法：
    python -X utf8 tests\_语义屏判据.py                # 合成夹具（无桌面也能跑）
    python -X utf8 tests\_语义屏判据.py --live         # 真开 tkinter 窗口跑（需交互桌面）
    python -X utf8 tests\_语义屏判据.py --inject guarantee  # 必红①（拿掉必进保证）
    python -X utf8 tests\_语义屏判据.py --inject text_key    # 必红③（uid 去掉 text_key）
    python -X utf8 tests\_语义屏判据.py --inject version      # 必红②（screen_version 钉死）
    python -X utf8 tests\_语义屏判据.py --inject all          # 三条一起注，逐条报红
    python -X utf8 tests\_语义屏判据.py --inject conf        # 必红⑦（退回对象级一个数 ⇒ bbox 被 text 拖低）
    python -X utf8 tests\_语义屏判据.py --inject uid_state     # 必红⑧（三档塌两档：内容变了也报 STALE）
    python -X utf8 tests\_语义屏判据.py --inject r2          # 必红⑨（R2 不看 name_trust ⇒ 错位页面假通过）
    python -X utf8 tests\_语义屏判据.py --inject version_reply  # 必红⑩（since 的版本应答不回）
    python -X utf8 tests\_语义屏判据.py --inject cdp_port      # 必红⑪（cdp.py 把 --port 丢掉）
    python -X utf8 tests\_语义屏判据.py --inject contract_env  # 必红⑪（契约删掉「data.结果」那一句）
★ 名字说明：--inject version 是**修正②**那条（screen_version 钉死），它现在会同时红 (2) 与 (10) —— 两处盯的都是「版本必须是新签发的」，如实报。

★ 换掉的只有 I/O 边界：uia_tree._lib（怎么拿窗口根）、uia_tree._client_rect（怎么拿客户区）、
  core._screen_window_info（标题/类名从 win32gui 读）。
  **walk()、_in_view()、_scroll_info() 与本单新增的全部 _screen_* 都是现役真代码**，一行没改。
★ 本判据**只读**：不发点击/输入，不碰用户的窗口，不拉急停。闸门、事件流、旁表、
  capture 全部重定向到临时沙箱 —— 真 auth_pass.json 与真事件流一个字节都不碰。
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "src", "agentctl", "core.py")
CONTRACT = os.path.join(ROOT, "contract", "tools.json")

# ★ 窗B：cdp.py 的真源与产物。判据要同时盯这两份 —— 只盯产物，产物漂了没人知道；
#   只盯真源，装出去的那份（= 包里真正跑的那份）就没人管了。
CDP_SRC = os.path.join(os.path.dirname(ROOT), "dsh-viewer-plugin", "tools", "cdp.py")
CDP_PROD = os.path.join(ROOT, "legacy_tools", "cdp.py")

# 契约句①的原文（窗B 加的）。--inject contract_env 就是把它从影子件里删掉 ⇒ 跑红。
CONTRACT_ENV_SENTENCE = (
    "★ **返回信封（2026-10-18 真跑踩过）**：经 MCP/CLI 拿到的不是裸结果 —— 工具结果被包在 "
    "**`data.结果`** 里，`objects` 在 **`data.结果.objects`**（**不是** `data.objects`）。"
    "**这是信封结构、不是空屏** —— 取错层读到 undefined，看起来像「这一屏什么都没有」，"
    "其实只是少剥了一层信封；别据此判「界面上没有」。")

# 注入时指向影子件；默认（未注入）就是现役路径。main() 按注入情况改写它们。
CDP_UNDER_TEST = CDP_PROD
CONTRACT_UNDER_TEST = CONTRACT

CLIENT = [0, 0, 1280, 900]          # 夹具客户区
FAKE_HWND = 424242                  # 合成句柄：界面上没有这个窗口，所以读不到也无所谓

# 夹具规模。**故意造得对「必进保证」不利**：
#   3 块 query 命中的大文本各占客户区 88% ⇒ 打分 0.638，压过小按钮的 0.601。
#   于是「纯按分取前 40」会必然裁掉至少一个视口内可交互按钮 ——
#   去掉必进保证就一定报红，这才叫这条断言有判别力。
N_BTN = 38                          # 视口内可交互按钮
N_QTEXT = 3                         # 视口内 query 命中的大文本
N_TEXT = 8                          # 视口内普通文本（★ 窗E1：10→8 —— 「备注段」恰好命中
                                    #   8 个节点，做 S5 宽度上限的边界放行例）
N_OFF = 10                          # 视口外按钮
QUERY = "题干"
EXPECT_TOTAL = N_BTN + N_QTEXT + N_TEXT + 3 + N_OFF      # 62（+3 = 计时器 + 状态 + T2 任务信息）
EXPECT_IN_VIEW = N_BTN + N_QTEXT + N_TEXT + 3             # 52（★ 窗D1 加了 T2；窗E1 跟 N_TEXT）

REQUIRED_OBJ_FIELDS = ("id", "uid", "label", "role", "rect", "in_view", "interactive",
                       "actions", "source", "source_confidence",
                       "conf_bbox", "conf_text", "conf_existence",   # ★ 窗C1 属性级三档
                       "name_trust", "volatile", "score")


# ══════════════════════════════════════════════════════════════════════
# 合成夹具：一棵够 walk() 用的假 UIA 树（形状照 A2 判据那套现役替身）
# ══════════════════════════════════════════════════════════════════════
class _Rect(object):
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b


class _Pattern(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Ctrl(object):
    """够 walk() 用的最小控件替身。**只实现 walk 真会问的那几个属性。**"""

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
    """替身 uiautomation 模块：只提供 walk() 用到的入口。"""

    def __init__(self, root):
        self.root = root

    def ControlFromHandle(self, handle):
        return self.root

    def SetGlobalSearchTimeout(self, t):
        return None

    def InitializeUIAutomationInCurrentThread(self):
        return None

# ======================================================================
# 沙箱 + 加载被测 core（真仓，或注入了缺陷的副本）
# ======================================================================
SB = {"dir": None}


def sandbox(tmp):
    """闸门/事件流/旁表/capture 全指进临时目录。**真凭证与真事件流一个字节都不碰。**"""
    os.environ["AGENTCTL_APP_DIR"] = os.path.join(tmp, "app")
    os.environ["AGENTCTL_TIMELINE"] = os.path.join(tmp, "timeline")
    os.environ["AGENTCTL_EID_SESSIONS"] = os.path.join(tmp, "eid_sessions.json")
    os.environ["AGENTCTL_SHOTS_DIR"] = os.path.join(tmp, "shots")
    os.environ["AGENTCTL_REQUIRE_LEASE"] = "0"
    os.environ["AGENTCTL_BORDER_LOG"] = os.path.join(tmp, "ctrl_border.log")
    os.environ["AGENTCTL_BORDER_MUTEX"] = "Local" + chr(92) + "_B1_%d" % os.getpid()
    auth = os.path.join(tmp, "_auth")
    os.makedirs(auth, exist_ok=True)
    # 铺一张只读感知用的临时通行证（A 级不计数、不消耗）
    with io.open(os.path.join(auth, "auth_pass.json"), "w", encoding="utf-8") as f:
        json.dump({"scopes": ["A", "B", "C", "D"], "acts": 0, "acts_limit": 99,
                   "expires_at": time.time() + 3600}, f, ensure_ascii=False)
    SB["dir"] = tmp
    return auth


def load_core(path, alias):
    """按路径加载一份 core —— 好让 --inject 指注入了缺陷的副本去跑红。"""
    path = os.path.abspath(path)
    # core.py 只把**包目录**塞进 sys.path；包本身要靠调用方把 src\ 放进去
    # （现役入口 agentctl_main.py 就是这么干的，判据跟它一样）
    if os.path.join(ROOT, "src") not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, "src"))
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise SystemExit("加载失败：%s" % path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # gate 的落盘点在 import 时就定了 —— 换成沙箱里的（真闸门状态不碰）
    from agentctl import gate
    auth = os.path.join(SB["dir"], "_auth")
    gate.PASSFILE = os.path.join(auth, "auth_pass.json")
    gate.AUTHDIR = auth
    gate.LOCKFILE = os.path.join(auth, "gate.lock")
    gate.CAPTURE_DB = os.path.join(auth, "capture.json")
    gate.LEASE_DB = os.path.join(auth, "lease.json")
    gate.LOG = os.path.join(auth, "judge.log")
    gate.BRAKE_FLAG = os.path.join(SB["dir"], "_急停_判据专用_不存在.flag")
    if os.path.exists(gate.BRAKE_FLAG):
        os.remove(gate.BRAKE_FLAG)
    return m, path

# ======================================================================
# 夹具窗口：可改标题、可推进文本内容
# ======================================================================
TIMER_LABELS = ["剩余 00:59", "剩余 00:58", "剩余 00:57", "剩余 00:56"]
STATUS_LABELS = ["当前状态：甲", "当前状态：乙", "当前状态：丙", "当前状态：丁"]


class Fixture(object):
    """自造的一屏：按钮、题干大文本、备注、计时器、状态行，外加一屏外的按钮。"""

    def __init__(self, title="B1语义屏判据靶窗", cls="TkTopLevel", pid=0):
        self.title = title
        self.cls = cls
        self.pid = pid
        self.tick = 0
        # ★ 窗C1 新增两个旋钮（默认关 ⇒ (1)~(6) 组完全不受影响）
        #   n_extra_btn >0：在按钮队列**最前面**插 N 个同角色按钮 ⇒ 后面每个按钮的
        #     struct_key 整体后移（uid 变）⇒ 造「可能压根不是同一对象」那一档。
        #   web_options："" / "normal" / "scramble" —— 网页选项字母正序 / 乱序。
        self.n_extra_btn = 0
        self.web_options = ""
        # ★ 窗D1 再加两个（同样默认无害）：
        #   timer_tick：**独立于 tick 的计时器刻度** —— S2 要的是「只动倒计时、别的都不动」，
        #     所以必须能把计时器单独推一格。
        #   quota：T2「剩余可选 N 项 · 第 7/20 步」那个数 —— 承载任务信息的伪 volatile。
        self.timer_tick = 0
        self.quota = 3
        # ★ 窗F2：冷窗口期（0 = 不冷）—— 见 start_cold/shell_root。
        self._cold_until = 0.0
        # ★ 窗F2b：半热窗口期（0 = 不半热）—— 见 start_half_cold/half_root。
        self._half_until = 0.0
        # ★ 窗G3：假点击器按什么脚本改这棵树 ——
        #   none=不动 / advance=计时器与状态行换字（文字变） / insert=插一个按钮（结构变）
        self.g3_click_effect = "none"
        # ★ 窗G3：滚动量（像素）。「滚出视口」那组要靠它把按钮推到看不见的地方。
        self.scroll_y = 0

    def scroll(self, y):
        """滚到 scroll_y（像素），返回 (scroll_percent, scroll_y)。

        ★ 判据要造的是「签发在视口内、点之前滚了一下」—— 那就把 y 调大，
          让原本可见的按钮真的落到视口外；**节点仍在树里**（in_view=False）。
        """
        self.scroll_y = int(y or 0)
        return int(round(float(self.scroll_y) / 10.0)), int(self.scroll_y)

    def set_title(self, t):
        self.title = t

    def advance(self):
        """推进一格：计时器与状态行**同时**换字（一个 volatile、一个不 volatile）。"""
        self.tick += 1
        self.timer_tick += 1

    def advance_timer_only(self):
        """**只**推计时器一格（状态行与别的都不动）—— S2 用它造「只有倒计时在变」。"""
        self.timer_tick += 1

    def set_quota(self, n):
        """改 T2 那个「剩余可选 N 项」—— 声明制盯的典型任务信息。"""
        self.quota = int(n)

    @property
    def timer_label(self):
        # ★ 算出来而不是查表：查表会「走到末尾就不动了」，S2 连推两格就测不出变化。
        return "剩余 00:%02d" % max(0, 59 - self.timer_tick)

    @property
    def status_label(self):
        return STATUS_LABELS[min(self.tick, len(STATUS_LABELS) - 1)]

    def button_labels(self):
        return ["选项格%02d" % (i + 1) for i in range(N_BTN)]

    def start_cold(self, ms=300):
        """让采集器在接下来 ms 毫秒里只给**外壳**（模拟 Chromium 无障碍树懒启用）。"""
        self._cold_until = time.time() + (float(ms) / 1000.0)

    def is_cold(self):
        return bool(getattr(self, "_cold_until", 0.0)) and time.time() < self._cold_until

    def shell_root(self):
        """冷启动外壳：**6 个节点、0 个可交互**（真页实测外壳约 18 个，量级相同）。"""
        rid = [900, 1]
        inner = []
        for i in range(5):
            rid[0] += 1
            inner.append(_Ctrl("PaneControl", [20, 20 + 30 * i, 900, 46 + 30 * i],
                               "外壳块%d" % (i + 1), rid=list(rid)))
        return _Ctrl("PaneControl", [0, 0, 1280, 200], "外壳窗格",
                     rid=[899, 1], children=inner)

    def start_half_cold(self, ms=150):
        """接下来 ms 毫秒里 walk 只拿到「**半热**」树（真页冷扫的形状，见 half_root）。

        ★ 为什么按**时间**门控、而不是「接下来 n 次」：一次 ctl_screen 里
          `_lib()` 不止被 walk 调（取窗口状态那条路也会要一次 root），
          按趟数计数会被那些调用吃掉 —— 实测踩过：计数被窗口状态先扣掉，
          等 walk 真正跑起来时已经是「热」的了。start_cold 当初也是按时间，稳妥。
        """
        self._half_until = time.time() + (float(ms) / 1000.0)

    def is_half_cold(self):
        return bool(getattr(self, "_half_until", 0.0)) and time.time() < self._half_until

    def half_root(self):
        """真页冷扫形状：**约 60 个原始节点，但可报对象只有 18 个**。

        ★ 为什么要单独造一棵：`shell_root`（6 节点 / 0 可交互）三条依据能撞中②和③，
          但**真页冷扫一个都撞不中** —— 它有可交互外壳（②不中）、耗时不短、节点也不少（③不中）。
        ★ 比例照 2026-10-18 真页实测抄：冷扫 18 个可报对象 / 60 个原始节点（热扫 49 / 111）。
          大半原始节点是**空壳 Pane**（没 label、也不可交互）—— 这正是「原始节点数」
          骗人、必须按「可报对象数」卡门槛的原因。
        """
        rid = [800, 1]
        inner = []
        # (a) 12 段有 label 的文本块 —— 计入「可报对象」
        for i in range(12):
            rid[0] += 1
            inner.append(_Ctrl("PaneControl", [20, 20 + 60 * i, 1200, 70 + 60 * i],
                               "半热文本段%02d" % (i + 1), rid=list(rid)))
        # (b) 6 个能点的按钮 —— 也计入「可报对象」
        for i in range(6):
            rid[0] += 1
            inner.append(_Ctrl("ButtonControl", [100, 60 * i, 200, 40 + 60 * i],
                               "半热按钮%02d" % (i + 1), rid=list(rid)))
        # (c) 41 个空壳 Pane —— 不可交互也没 label，**只涨原始节点数**
        for i in range(41):
            rid[0] += 1
            inner.append(_Ctrl("PaneControl", [400, 20 + 12 * i, 900, 30 + 12 * i],
                               "", rid=list(rid)))
        return _Ctrl("PaneControl", [0, 0, 1280, 900], "半热网页根",
                     rid=[799, 1], children=inner)

    def root(self):
        # ★ 窗F2：冷窗口期只给外壳（walk() 仍是现役真代码，换的只是 _lib 这个 I/O 边界）。
        if self.is_cold():
            return self.shell_root()
        # ★ 窗F2b：半热窗口期只给「半热」树（真页冷扫的形状）。
        if self.is_half_cold():
            return self.half_root()
        inner = []
        rid = [100, 1]
        # (1) query 命中的**大**文本（面积占客户区 88%）：打分 0.638，压过小按钮的 0.601
        for i in range(N_QTEXT):
            rid[0] += 1
            inner.append(_Ctrl("PaneControl", [20, 40, 1260, 860],
                               "题干第%d段：下列说法正确的是" % (i + 1), rid=list(rid)))
        # (2) 视口内可交互按钮（小）。★ 插在最前面的额外按钮动的是**同角色兄弟编号**，
        #     后面每个按钮的 struct_key 都会变 —— 这就是 STALE 那一档。
        for i in range(int(self.n_extra_btn or 0)):
            rid[0] += 1
            inner.append(_Ctrl("ButtonControl", [100, 40 + 20 * i, 180, 58 + 20 * i],
                               "插入按钮%02d" % (i + 1), rid=list(rid)))
        for i, lab in enumerate(self.button_labels()):
            rid[0] += 1
            inner.append(_Ctrl("ButtonControl", [100, 100 + 20 * i, 180, 118 + 20 * i],
                               lab, rid=list(rid)))
        # (3) 视口内普通文本（不含 query）
        for i in range(N_TEXT):
            rid[0] += 1
            inner.append(_Ctrl("PaneControl", [400, 100 + 20 * i, 700, 118 + 20 * i],
                               "备注段%02d" % (i + 1), rid=list(rid)))
        # (4) 计时器（volatile）与状态行（不 volatile）—— (3) 号判据就盯这俩
        rid[0] += 1
        inner.append(_Ctrl("TextControl", [20, 870, 220, 890], self.timer_label, rid=list(rid)))
        rid[0] += 1
        inner.append(_Ctrl("TextControl", [300, 870, 600, 890], self.status_label, rid=list(rid)))
        # (4b) ★ 窗D1 · T2：靶场 lab_volatile.html 的 e49 那一行 ——
        #     「长得像倒计时、其实承载任务信息」。它**当前不被 volatile 规则命中**
        #     （Z3 已把裸关键词「剩余」删了），所以 uid 本来就实算；
        #     声明它验的是**另一层价值**：把要盯的串钉住（无视口过滤 + 必进）。
        rid[0] += 1
        inner.append(_Ctrl("TextControl", [620, 870, 1000, 890],
                           "剩余可选 %d 项 · 第 7/20 步" % self.quota, rid=list(rid)))

        # (5) ★ 窗C1：网页选项（RadioButtonControl）。字母正序 = 没问题；
        #     乱序 = 「控件自称的字母与它在屏幕上的位置对不上」= 疑似错位。
        if self.web_options:
            order = [0, 1, 2, 3] if self.web_options == "normal" else [3, 2, 1, 0]
            for k, i in enumerate(order):
                rid[0] += 1
                inner.append(_Ctrl("RadioButtonControl", [100, 300 + 100 * k, 700, 330 + 100 * k],
                                   "%s. 选项%s" % ("ABCD"[i], "甲乙丙丁"[i]), rid=list(rid)))

        offscreen = []
        for i in range(N_OFF):
            rid[0] += 1
            offscreen.append(_Ctrl("ButtonControl", [100, 1200 + 60 * i, 220, 1240 + 60 * i],
                                   "屏外按钮%02d" % (i + 1), rid=list(rid)))

        pane = _Ctrl("PaneControl", [0, 0, 1280, 900], "", rid=[2, 1], children=inner)
        off_pane = _Ctrl("PaneControl", [0, 1100, 1280, 3400], "", rid=[3, 1],
                         children=offscreen)
        return _Ctrl("PaneControl", [0, 0, 1280, 940], "", rid=[1, 1],
                     children=[pane, off_pane])


ORIG = {}


class FakeCdp(object):
    '''**假 cdp**（窗G1）—— 只换两个接缝：`_cdp_http` 与 `_cdp_eval`。

    ★ 为什么不用真浏览器：判据要能在**任何时候、任何机器**上跑，且不许碰用户正在用的
      Edge 实例。接缝之下全是真代码（JSON 解析、裁剪、接线、判定式），之上
      （screen_channels、盲点文案）也是真代码 —— 只有「网络 I/O」这一层是假的。
    ★ `_cdp_spawn` 也被换掉：launch 一旦被放行就会真的起浏览器，判据里绝不能发生。
      浏览器路径也换成 sys.executable —— 省得真去 Program Files 里找 Edge。
    '''

    def __init__(self):
        self.reset()

    def reset(self):
        self.up = False                 # 端口上有没有 CDP 实例
        self.taken_by_other = False     # 端口被**别人**占着（不是我们起的）
        self.dom_objects = []           # 页面里能取到的 DOM 元素
        self.page_url = 'file:///靶场/lab_cdp.html'
        self.page_title = 'CDP 靶页'
        self.spawned = []               # 记下每一次「试图起浏览器」
        self.killed = []                # 记下每一次 taskkill
        self.http_hits = []             # 记下被问过的端点
        self.alive = set()              # 假装活着的 pid
        self.answer_after_spawn = True  # 起完之后端口是否应答

    def make_dom(self, n, hit_text=''):
        out = []
        for i in range(n):
            name = ('%s' % hit_text) if (i == 0 and hit_text) else ('DOM 元素 %03d' % (i + 1))
            out.append({'role': 'button' if i % 3 == 0 else 'div',
                        'name': name,
                        'rect': [10 + i, 20, 120, 30],
                        'selector': 'body > div:nth-of-type(%d)' % (i + 1)})
        return out

    def install(self, core):
        '''把接缝换掉（真身存进 ORIG，--live 那半段要还原）。'''
        if '_cdp_http' not in ORIG:
            for k in ('_cdp_http', '_cdp_eval', '_cdp_spawn', '_cdp_kill_tree',
                      '_pid_alive', 'CDP_BROWSER_CANDIDATES'):
                ORIG[k] = core.__dict__.get(k)
        me = self

        def fake_http(path, port=9222, timeout=3.0):
            me.http_hits.append((path, int(port)))
            if path == '/json/version':
                if me.up or me.taken_by_other:
                    who = '别人家的' if me.taken_by_other else '我们起的'
                    return 200, json.dumps({'Browser': 'FakeCDP/1.0（%s）' % who,
                                            'webSocketDebuggerUrl':
                                            'ws://127.0.0.1:%d/devtools/browser/fake' % int(port)})
                return 0, 'ConnectionRefusedError: 端口 %s 上没有实例' % int(port)
            if path == '/json/list':
                if not (me.up or me.taken_by_other):
                    return 0, 'ConnectionRefusedError'
                return 200, json.dumps([
                    {'id': 'T1', 'type': 'page', 'title': me.page_title, 'url': me.page_url},
                    {'id': 'T2', 'type': 'service_worker', 'title': 'sw', 'url': 'about:blank'},
                ])
            return 0, '未知端点 %s' % path

        def fake_eval(js, port=9222, timeout=25, url_contains=''):
            if int(port) != 9222:
                return 124, None, 'cdp.py 的调试端口写死 9222'
            val = {'url': me.page_url, 'title': me.page_title,
                   'scanned': len(me.dom_objects) + 7, 'objects': list(me.dom_objects)}
            return 0, val, json.dumps(val, ensure_ascii=False)

        def fake_spawn(argv):
            me.spawned.append(list(argv))
            me.alive.add(999999)
            me.up = me.answer_after_spawn      # 起完之后端口才应答（真浏览器就是这样）
            return 999999

        def fake_kill(pid):
            me.killed.append(int(pid))
            me.alive.discard(int(pid))
            me.up = False
            return 0, '成功: 已终止 PID=%d' % int(pid)

        def fake_alive(pid):
            return int(pid) in me.alive

        core._cdp_http = fake_http
        core._cdp_eval = fake_eval
        core._cdp_spawn = fake_spawn
        core._cdp_kill_tree = fake_kill
        core._pid_alive = fake_alive
        core.CDP_BROWSER_CANDIDATES = (sys.executable,)   # 绝不去 Program Files 里找真 Edge
        try:
            core._CDP_PROC.clear()
        except Exception:
            pass


CDP = FakeCdp()


class FakeOcr(object):
    '''**假 OCR**（窗G2）—— 只换 `agentctl.ocr` 模块上的四个 **I/O 叶子**：
    `engine_ok` / `client_rect_screen` / `grab_client` / `_recognize`（外加 `dpi_scale` 这个查询）。

    ★ **判据里绝不真跑 OCR**：真跑要截图 + 调 WinRT，慢、要桌面、还可能和用户抢前台。
    ★ ★ **为什么不整个替换 `ocr_window`**：换掉它 = 把「守护线程 + join(max_ms) + 超时即返回」
      那段真代码也换掉了 —— 超时断言就变成在测假件自己。踩过一次：假件睡 2 秒无视 max_ms，
      判据红了但红得没意义。只换叶子，ocr_window 内部（上限、坐标换算、超时、原因）全是真代码。
    ★ core 是 `from agentctl import ocr` 按模块取的，所以替换**模块属性**就够。
    '''

    def __init__(self):
        self.reset()

    def reset(self):
        self.engine_ok_flag = True
        self.engine_raises = False
        self.words = []
        self.text = ''
        self.geom = {'left': 0, 'top': 0, 'width': 1280, 'height': 900,
                     'logical_width': 1280, 'logical_height': 900}
        self.dpi_scale = 1.25
        self.calls = []
        self.slow_ms = 0          # >0 ⇒ ocr_window 会睡这么久（用来造超时）

    def words_made(self, n, hit_text='', inside=True):
        out = []
        for i in range(n):
            name = hit_text if (i == 0 and hit_text) else ('词%03d' % (i + 1))
            # ★ 这些是**放大后位图里**的坐标（真实 OCR 给的就是这个尺度），
            #   换算 ÷4 ×dpi_scale 由 ocr.py 真代码做，别在造数据时就替它算掉。
            x, y, w, h = 400 + 120 * i, 800, 240, 96
            if not inside:
                x, y = 20000, 16000        # ÷4×1.25 后仍是 6250/5000 ⇒ 客户区 1280×900 之外
            out.append({'t': name, 'x': x, 'y': y, 'w': w, 'h': h, 'conf': 0.55})
        return out

    def install(self, core):
        try:
            from agentctl import ocr as m
        except Exception:
            return None
        me = self

        def fake_engine_ok(lang='zh-Hans-CN', refresh=False):
            if me.engine_raises:
                raise RuntimeError('假 OCR：引擎导入炸了')
            if not me.engine_ok_flag:
                return {'ok': False, 'lang': '', 'langs': [],
                        '原因': 'Windows.Media.Ocr 起不来（本机没装对应语言）', '上限': 10000}
            return {'ok': True, 'lang': lang, 'langs': [lang], '原因': '', '上限': 10000}

        def fake_geom(hwnd):
            g = dict(me.geom)
            g['ok'] = True            # 真函数会算这个标志，假件也得给 —— 不然 ocr.py 会说「拿不到客户区」
            return g

        def fake_grab(hwnd, box):
            me.calls.append({'hwnd': int(hwnd), 'grab': True})
            from PIL import Image
            return Image.new('RGB', (int(box.get('logical_width') or 1280),
                                     int(box.get('logical_height') or 900)), 'white')

        def fake_recognize(png_bytes, lang):
            me.calls.append({'recognize': True, 'bytes': len(png_bytes or b'')})
            if me.slow_ms:
                time.sleep(float(me.slow_ms) / 1000.0)
            # ★ 200 条全给，让 ocr.py 真代码去裁、去换算、去报 omitted
            return {'text': me.text or ' '.join(x['t'] for x in me.words),
                    'words': [dict(x) for x in me.words]}

        def fake_dpi(hwnd):
            return float(me.dpi_scale)

        m.engine_ok = fake_engine_ok
        m.client_rect_screen = fake_geom
        m.grab_client = fake_grab
        m._recognize = fake_recognize
        m.dpi_scale = fake_dpi
        return m


OCRSTUB = FakeOcr()


def install_stubs(core, fx):
    """只换 I/O 边界。**walk / _in_view / _scroll_info / 全部 _screen_* 都是真代码。**"""
    uia = core.uia_tree
    if not ORIG:
        # 存真身：--live 那半段要把它们还原回去，走真 COM、真 win32gui
        ORIG["_lib"] = uia.__dict__["_lib"]
        ORIG["_client_rect"] = uia.__dict__["_client_rect"]
        ORIG["_screen_window_info"] = core.__dict__["_screen_window_info"]
    uia._lib = lambda: _FakeAuto(fx.root())
    uia._client_rect = lambda handle: list(CLIENT)
    core._screen_window_info = lambda h: {"hwnd": FAKE_HWND, "title": fx.title,
                                          "cls": fx.cls, "pid": fx.pid}
    # ★ 窗G1：**默认把 CDP 通道钉成「不可达」** ——
    #   判据全程不许碰 9222 端口（那儿可能是用户正在用的浏览器），更不许顺手起一个。
    #   要测 CDP 就由 S8 显式摆假 cdp。
    CDP.reset()
    CDP.install(core)
    # ★ 窗G2：同理，**默认把 OCR 钉成「引擎不可用」** —— 判据全程不截图、不调 WinRT。
    OCRSTUB.reset()
    OCRSTUB.engine_ok_flag = False
    OCRSTUB.install(core)


def call(core, **kw):
    """走**真正的 dispatch**（不是直接调内部函数）—— 顺带证明 TOOLS 表接上了。"""
    args = {"hwnd": FAKE_HWND}
    args.update(kw)
    r = core.dispatch("ctl_screen", args, actor="判据B1")
    data = r.get("data") or {}
    return r, data.get("结果") or {}


def by_label(objs, needle):
    return [o for o in objs if str(o.get("label") or "").startswith(needle)]


def uids_of(objs):
    return sorted(o["uid"] for o in objs)

# ======================================================================
# 注入器（跑红自证）：复制一份 core.py 到 %TEMP% → 在副本上注入一个已知缺陷
#   → 同一套断言跑那份副本。**产品代码一个字节都不改**，副本路径打印出来。
# ======================================================================
INJECT_GUARANTEE = """

# ===== 以下为 窗B1 注入段（跑红自证专用，勿入产品代码）=====
_real_screen_select = _screen_select


def _screen_select(cands, top, guarantee=True):        # noqa: F811  故意覆盖真版
    \"\"\"注入的缺陷：拿掉「必进保证」，纯按分取前 top —— 视口内可交互按钮会被裁掉。\"\"\"
    return _real_screen_select(cands, top, guarantee=False)
"""

INJECT_TEXT_KEY = """

# ===== 以下为 窗B1 注入段（跑红自证专用，勿入产品代码）=====
_real_screen_text_key = _screen_text_key


def _screen_text_key(label, volatile):                 # noqa: F811  故意覆盖真版
    \"\"\"注入的缺陷：uid 里去掉 text_key（只剩结构路径）⇒ 文字变了 uid 也不变。\"\"\"
    return "-"
"""

INJECT_VERSION = """

# ===== 以下为 窗B1 注入段（跑红自证专用，勿入产品代码）=====
_real_screen_build = _screen_build


def _screen_build(hwnd, args):                         # noqa: F811  故意覆盖真版
    \"\"\"注入的缺陷：screen_version 钉死 ⇒ 内容或标题变了它也不变。\"\"\"
    out = _real_screen_build(hwnd, args)
    out["screen_version"] = "sv1-PINNED00000000"
    return out
"""

# ===== 以下为 窗C1 注入段（跑红自证专用，勿入产品代码）=====================
INJECT_CONF = """

# ===== 以下为 窗C1 注入段（跑红自证专用，勿入产品代码）=====
_real_conf_attrs = _agg_source_conf_attrs


def _agg_source_conf_attrs(items):                       # noqa: F811  故意覆盖真版
    # 注入的缺陷：退回**对象级一个数**（三档一起取 min）
    #   ⇒ bbox 被 text 一起拖低，正是 Z2 指出的「误伤定位」。
    a = _real_conf_attrs(items)
    m = min([a["bbox"], a["text"], a["existence"], a["agg"]])
    out = {k: m for k in SCREEN_CONF_ATTRS}
    out["agg"] = m
    out["sources"] = a["sources"]
    return out
"""

INJECT_UID_STATE = """

# ===== 以下为 窗C1 注入段（跑红自证专用，勿入产品代码）=====
_real_uid_state = screen_uid_state


def screen_uid_state(prev_uid, cur_uid):                 # noqa: F811  故意覆盖真版
    # 注入的缺陷：三档塌成两档 —— 同对象改了字也报 STALE
    #   ⇒ 「动作成功了」被判成「过期」，agent 只好反复重扫。
    s = _real_uid_state(prev_uid, cur_uid)
    return "STALE" if s == "TEXT_CHANGED" else s
"""

INJECT_R2 = """

# ===== 以下为 窗C1 注入段（跑红自证专用，勿入产品代码）=====
_real_channels_c1 = screen_channels


def screen_channels(*, interactive_count, target_hit, name_trust="high",  # noqa: F811
                    **kw):
    # 注入的缺陷：R2 不看 name_trust（退回修正⑤的老口径）
    #   ⇒ 网页选项错位页面「错位命中」，R2 假通过。
    return _real_channels_c1(interactive_count=interactive_count,
                             target_hit=target_hit, name_trust="high", **kw)
"""

INJECT_VERSION_REPLY = """

# ===== 以下为 窗C1 注入段（跑红自证专用，勿入产品代码）=====
_real_build_reply = _screen_build


def _screen_build(hwnd, args):                            # noqa: F811  故意覆盖真版
    # 注入的缺陷：since 的版本应答三件套**不回**（还把 version_match 写死成 True）
    #   ⇒ agent 拿不到「这屏到底变没变」的凭据，只能盲扫。
    out = _real_build_reply(hwnd, args)
    if args.get("since"):
        for k in ("since_echo", "version_match", "screen_version_新值"):
            out.pop(k, None)
        out["版本口径"] = ""
        d = out.get("delta")
        if isinstance(d, dict):
            for k in ("since_echo", "version_match", "screen_version_新值"):
                d.pop(k, None)
            d["version_match"] = True
    return out
"""

# ===== 以下为 窗D1 注入段（跑红自证专用，勿入产品代码）=====================
INJECT_WATCH_EXEMPT = """

# ===== 以下为 窗D1 注入段（跑红自证专用，勿入产品代码）=====
_real_build_exempt = _screen_build


def _screen_build(hwnd, args):                            # noqa: F811  故意覆盖真版
    # 注入的缺陷：**声明仍然被豁免** —— watched ∧ volatile 的节点照样把 text_key
    # 压成 ~，换多少字 uid 都不变 ⇒ 「我声明要盯它」等于没声明（这就是 J2 那种假绿）。
    out = _real_build_exempt(hwnd, args)
    hit = False
    for o in (out.get("objects") or []):
        if o.get("watched") and o.get("volatile"):
            hit = True
            o["uid"] = str(o.get("uid") or "").split("@")[0] + "@~"
            o["exempted"] = True
            if o.get("uid_state"):
                o["uid_state"] = "OK"
    if hit:
        # 把这趟快照也换成「豁免后的样子」，否则下一趟仍会拿真 uid 算出 TEXT_CHANGED
        _screen_forget(int(hwnd))
    return out
"""

INJECT_WATCH_LEAK = """

# ===== 以下为 窗D1 注入段（跑红自证专用，勿入产品代码）=====
_real_build_leak = _screen_build


def _screen_build(hwnd, args):                            # noqa: F811  故意覆盖真版
    # 注入的缺陷：**未声明**的 volatile 节点也不再豁免 —— 等于把豁免整个拆了，
    # 每秒跳一次的倒计时会把 version 顶得到处都是（框架 §⑯ 量的 A 套：13 次假 STALE）。
    out = _real_build_leak(hwnd, args)
    import hashlib as _hl
    hit = False
    for o in (out.get("objects") or []):
        if o.get("exempted"):
            hit = True
            o["exempted"] = False
            o["uid"] = (str(o.get("uid") or "").split("@")[0] + "@t"
                       + _hl.sha1(str(o.get("label") or "").encode("utf-8")).hexdigest()[:10])
            if o.get("uid_state"):
                o["uid_state"] = "TEXT_CHANGED"
    if hit:
        _screen_forget(int(hwnd))
    return out
"""

INJECT_WATCH_SILENT = """

# ===== 以下为 窗D1 注入段（跑红自证专用，勿入产品代码）=====
_real_build_silent = _screen_build


def _screen_build(hwnd, args):                            # noqa: F811  故意覆盖真版
    # 注入的缺陷：首扫 unmatched **静默吞掉** —— 照常返回 rc=0，
    # 声明「要盯的东西」不存在也不吭声 ⇒ agent 以为在盯，其实盯的是空气。
    out = _real_build_silent(hwnd, args)
    rej = out.pop("_watch_拒绝", None)
    if rej:
        ck = rej["watch_校验"]
        ck["unmatched"] = []
        ck["pending"] = []
        out["watch_校验"] = ck
    return out
"""

INJECT_WATCH_SHADOW = """

# ===== 以下为 窗E1 注入段（跑红自证专用，勿入产品代码）=====
_real_matches_shadow = _screen_watch_matches


def _screen_watch_matches(label, decls):                  # noqa: F811  故意覆盖真版
    # 注入的缺陷：退回「只记第一条」—— 第二条重叠声明被互蔽成 unmatched
    # ⇒ 整调用误拒（Z4 F1 的病灶复原）。
    return _real_matches_shadow(label, decls)[:1]
"""

INJECT_WATCH_WIDTH = """

# ===== 以下为 窗E1 注入段（跑红自证专用，勿入产品代码）=====
# 注入的缺陷：宽度上限名存实亡（改成天文数字）—— 一条宽串吞半屏也放行（Z4 F2 复原）。
WATCH_TEXT_WIDTH_MAX = 10 ** 9
"""

INJECT_WATCH_CTX = """

# ===== 以下为 窗E1 注入段（跑红自证专用，勿入产品代码）=====
_real_build_ctx = _screen_build


def _screen_build(hwnd, args):                            # noqa: F811  故意覆盖真版
    # 注入的缺陷：拒绝信封抠掉屏上下文（screen_version/window/对象总数/性质）——
    # agent 分不清「声明写错 / 白屏 / 错窗 / 预算没扫到」（Z4 F3 复原）。
    out = _real_build_ctx(hwnd, args)
    rej = out.get("_watch_拒绝")
    if isinstance(rej, dict):
        for k in ("screen_version", "window", "对象总数", "性质"):
            rej.pop(k, None)
    return out
"""

# ===== 以下为 窗F2 注入段（跑红自证专用，勿入产品代码）=====================
INJECT_WARMUP = """

_real_ready_f2 = _screen_ready


def _screen_ready(nodes, stats, *, elapsed_ms, prev_nodes=0, is_web=False):   # noqa: F811  故意覆盖真版
    # 注入的缺陷：**没有就绪判定也没有暖机重试** —— 首扫拿到什么就交什么，
    # 并且对外谎称已热（= 治「首扫不可信」之前的样子）。
    return True, ["注入：永远当已热"]


_real_build_f2 = _screen_build


def _screen_build(hwnd, args):                            # noqa: F811  故意覆盖真版
    _a = dict(args)
    _a["warmup"] = False            # 连重试也不许发生
    out = _real_build_f2(hwnd, _a)
    out["screen_ready"] = True      # 且对外谎称已热
    out["ready_证据"] = {"注入": "永远当已热"}
    return out
"""

# ===== 以下为 窗F2b 注入段（跑红自证专用，勿入产品代码）=====================
INJECT_WARMUP_FIRST = """

# ===== 以下为 窗F2b 注入段（跑红自证专用，勿入产品代码）=====
_real_minprobes_f2b = _screen_min_probes
_real_ready_f2b = _screen_ready        # ★ 必须在覆盖**之前**绑定，否则绑到自己 ⇒ 无限递归


def _screen_min_probes(*, warmup, is_web, first_scan):   # noqa: F811  故意覆盖真版
    # 注入的缺陷：**网页类首扫不强制探第二次**（把「至少两趟」这个下限抹成 1）——
    # 真页上冷扫 18 个对象会被原样交出去，agent 拿着半屏当真屏。
    return 1


def _screen_ready(nodes, stats, *, elapsed_ms, prev_nodes=0, is_web=False):   # noqa: F811  故意覆盖真版
    # 顺带把网页类门槛也去掉：真页冷扫形状（原始节点多、可报对象少）就没人判了。
    return _real_ready_f2b(nodes, stats, elapsed_ms=elapsed_ms,
                           prev_nodes=prev_nodes, is_web=False)
"""

INJECT_WARMUP_NB = """

# ===== 以下为 窗F2b 注入段（跑红自证专用，勿入产品代码）=====
_real_minprobes_f2b_nb = _screen_min_probes   # ★ 必须在覆盖**之前**绑定，否则绑到自己 ⇒ 无限递归


def _screen_min_probes(*, warmup, is_web, first_scan):   # noqa: F811  故意覆盖真版
    # 注入的缺陷：**忘了判窗口类**（把 `is_web and` 那个条件删掉）——
    # 于是记事本、记事本、任何原生窗口的首扫也被迫多走一趟、白等 400ms。
    if is_web:
        return _real_minprobes_f2b_nb(warmup=warmup, is_web=True, first_scan=first_scan)
    return 2 if (warmup and first_scan) else 1
"""

# ===== 以下为 窗G2 注入段（跑红自证专用，勿入产品代码）=====================
INJECT_OCR_WIRED = '''

# ===== 以下为 窗G2 注入段（跑红自证专用，勿入产品代码）=====
_real_channels_g2 = screen_channels


def screen_channels(*, interactive_count, target_hit, name_trust='high',   # noqa: F811
                    cdp_wired=False, cdp_reachable=False, dom_hit=False,
                    ocr_wired=False, ocr_hit=False, ocr_bbox_usable=False, **kw):
    # 注入的缺陷：**R3 根本没接线**（回到 G2 之前的样子）—— OCR 三个要素一律不接，
    # 于是像素里明明认出了字，R3 也不成立，must_vlm 照样为 true（白白多叫一次 VLM）。
    return _real_channels_g2(interactive_count=interactive_count,
                             target_hit=target_hit, name_trust=name_trust,
                             cdp_wired=cdp_wired, cdp_reachable=cdp_reachable,
                             dom_hit=dom_hit)
'''

INJECT_OCR_CAP = '''

# ===== 以下为 窗G2 注入段（跑红自证专用，勿入产品代码）=====
_real_ocr_g2 = _screen_ocr


def _screen_ocr(hwnd, *, query='', rect=None, lang='zh-Hans-CN', max_ms=1500):   # noqa: F811
    # 注入的缺陷：**去掉 80 词上限** —— 一屏几百个词全塞回信封，
    # token 被一条屏吃光，agent 反而更看不见目标。
    out = dict(_real_ocr_g2(hwnd, query=query, rect=rect, lang=lang, max_ms=max_ms))
    res = dict(out.get('res') or {})
    res['words'] = list(res.get('words') or [])
    res['omitted'] = {'total': len(res['words']), 'returned': len(res['words']), 'cap': None}
    out['res'] = res
    return out
'''

INJECT_OCR_BBOX = '''

# ===== 以下为 窗G2 注入段（跑红自证专用，勿入产品代码）=====
_real_ocr_g2b = _screen_ocr


def _screen_ocr(hwnd, *, query='', rect=None, lang='zh-Hans-CN', max_ms=1500):   # noqa: F811
    # 注入的缺陷：**越界的框也算 usable** —— OCR 的框飘了也照当锚，
    # 顺着它去点就会点到别的地方去。
    out = dict(_real_ocr_g2b(hwnd, query=query, rect=rect, lang=lang, max_ms=max_ms))
    out['usable'] = bool(out.get('hit'))
    return out
'''

INJECT_OCR_TIMEOUT = '''

# ===== 以下为 窗G2 注入段（跑红自证专用，勿入产品代码）=====
_real_ocr_g2t = _screen_ocr


def _screen_ocr(hwnd, *, query='', rect=None, lang='zh-Hans-CN', max_ms=1500):   # noqa: F811
    # 注入的缺陷：**超时不认** —— 没跑完也照样当「引擎可用」，
    # 拿半截 words 当完整证据交出去（比明确失败坏得多）。
    out = dict(_real_ocr_g2t(hwnd, query=query, rect=rect, lang=lang, max_ms=max_ms))
    if not out.get('ok') and '超时' in str(out.get('原因') or ''):
        out['wired'] = True
        out['hit'] = bool(query)
        out['usable'] = bool(query)
    return out
'''

# ===== 以下为 窗G1 注入段（跑红自证专用，勿入产品代码）=====================
INJECT_CDP_WIRED = '''

# ===== 以下为 窗G1 注入段（跑红自证专用，勿入产品代码）=====
_real_channels_g1 = screen_channels


def screen_channels(*, interactive_count, target_hit, name_trust='high',   # noqa: F811
                    cdp_wired=False, cdp_reachable=False, dom_hit=False,
                    ocr_wired=False, ocr_hit=False, ocr_bbox_usable=False, **kw):
    # 注入的缺陷：**R1 根本没接线**（回到 G1 之前的样子）—— CDP 三要素一律不接，
    # 于是 DOM 明明命中了，R1 也不成立，must_vlm 照样为 true（白白多叫一次 VLM）。
    # ★ ocr_* **原样透传**：这个注入只该拆 CDP 那条通道。顺手把 OCR 也吞掉的话，
    #   红线会从 S8 溢到 S9（实测踩过：3 条 → 6 条、两个组都红），注入的爆炸半径就废了。
    return _real_channels_g1(interactive_count=interactive_count,
                             target_hit=target_hit, name_trust=name_trust,
                             ocr_wired=ocr_wired, ocr_hit=ocr_hit,
                             ocr_bbox_usable=ocr_bbox_usable)
'''

INJECT_CDP_CAP = '''

# ===== 以下为 窗G1 注入段（跑红自证专用，勿入产品代码）=====
def _screen_dom(*, url_contains='', port=CDP_DEFAULT_PORT,              # noqa: F811
                timeout=CDP_EVAL_TIMEOUT_SEC):
    # 注入的缺陷：**去掉 60 条上限** —— 一屏几百个 DOM 元素全塞回信封，
    # token 被一条屏吃光，agent 反而更看不见目标。
    js = SCREEN_DOM_JS.replace('__ASK__', str(int(SCREEN_DOM_ASK_OBJECTS)))
    rc, val, raw = _cdp_eval(js, port, timeout, url_contains)
    got = [o for o in ((val or {}).get('objects') or []) if isinstance(o, dict)]
    return {'wired': rc == 0 and isinstance(val, dict), 'page_url': str((val or {}).get('url') or ''),
            'page_title': str((val or {}).get('title') or ''),
            'objects': [{'role': str(o.get('role') or ''), 'name': str(o.get('name') or ''),
                         'rect': list(o.get('rect') or [0, 0, 0, 0]),
                         'selector': str(o.get('selector') or ''), 'source': 'cdp',
                         'source_confidence': dict(SCREEN_SOURCE_CONF['cdp'])} for o in got],
            'omitted': {'total': len(got), 'returned': len(got), 'cap': None}}
'''

INJECT_CDP_STEAL = '''

# ===== 以下为 窗G1 注入段（跑红自证专用，勿入产品代码）=====
_real_version_g1 = _cdp_version
_real_launch_g1 = _cdp_launch
_in_launch_g1 = {'on': False, 'n': 0}


def _cdp_launch(args):                            # noqa: F811  故意覆盖真版
    _in_launch_g1['n'] = 0
    _in_launch_g1['on'] = True
    try:
        return _real_launch_g1(args)
    finally:
        _in_launch_g1['on'] = False


def _cdp_version(port=CDP_DEFAULT_PORT):              # noqa: F811  故意覆盖真版
    # 注入的缺陷：**探测时假装端口是空的** —— 于是 launch 认定「没人占」，
    # 直接另起一个实例占上去。真实后果：用户那个浏览器从此再也连不上调试端口。
    # ★ 只在 **launch 自己的那一次占用探测** 里撒谎（第 1 次），第 2 次起交回真身，
    #   好让 launch 起完之后那次「端口起来了吗」的收尾探测能过。
    #   只骗 launch 内的探测 ⇒ S8 的 ①②③（R1 判定）不受影响，红线只落在 ④ 上。
    if _in_launch_g1['on']:
        _in_launch_g1['n'] += 1
        if _in_launch_g1['n'] == 1:
            return {}
    return _real_version_g1(port)
'''



INJECT_EID_LINK = '''

# ===== 以下为 窗G3 注入段（跑红自证专用，勿入产品代码）=====
_real_screen_build_g3 = _screen_build


def _screen_build(hwnd, args):                          # noqa: F811  故意覆盖真版
    # 注入的缺陷：**eid 换成自造号**（形如 o<hash>）——
    #   看着还是"每个对象有个 id"，但它压根不在旁表里。
    #   真实后果：agent 拿 ctl_screen 看到的号去点，旁表查无此号 ⇒ 每次都被拒，
    #   或者更坏：哪天真有实现拿它当坐标兜底，就变成"点自己猜的位置"（D1 那条病）。
    out = _real_screen_build_g3(hwnd, args)
    for o in (out.get("objects") or []):
        o["eid"] = "x%s" % str(o.get("id") or "")[1:]
    return out
'''

INJECT_EXPECT_UID = '''

# ===== 以下为 窗G3 注入段（跑红自证专用，勿入产品代码）=====
_real_t_click_g3 = t_click


def t_click(args):                                       # noqa: F811  故意覆盖真版
    # 注入的缺陷：**expect_uid 收下但不校验** —— 对不上也照点。
    #   真实后果：agent 拿一屏前的 uid 去点，界面早变了，点下去的是**另一个对象**，
    #   而且回执还写着 ok=true —— 这正是 STALE_SCREEN 语义要挡的那一刀。
    return _real_t_click_g3({**args, "expect_uid": ""})


# ★ TOOLS 是 import 时按函数对象建的表（core.py:4496），覆盖模块名不覆盖表里的引用 ——
#   不补这一行，dispatch 走的老实身 ⇒ 这个注入会假绿（实测踩过）。
TOOLS["ctl_click"] = t_click
'''

INJECT_AFTER_STATE = '''

# ===== 以下为 窗G3 注入段（跑红自证专用，勿入产品代码）=====
_real_click_after_state_g3 = _click_after_state


def _click_after_state(hwnd, hit, pre_ctx=None):         # noqa: F811  故意覆盖真版
    # 注入的缺陷：**恒报 OK** —— 不管结构变了没变、对象还在不在，一律"没变化"。
    #   真实后果：点错了对象、点了之后布局整个重排，回执都说"一切如常"；
    #   "点下去了"被当成"点对了"，这正是本单要治的最后一层。
    out = _real_click_after_state_g3(hwnd, hit, pre_ctx)
    out["uid_state"] = "OK"
    return out
'''

INJECT_CAP_DETAIL = '''

# ===== 以下为 窗G3b 注入段（跑红自证专用，勿入产品代码）=====
_real_eid_put_g3b = _eid_sessions_put


def _eid_sessions_put(tok, hwnd, nodes, walk=None, 无明细=False):   # noqa: F811  故意覆盖真版
    # 注入的缺陷：**capture 一律不写元素明细** ——
    #   屏幕照样交出 eid，旁表却空空如也，点下去永远 code=12。
    #   真实后果：这就是 G3b 单要治的那条真门路缺口（DSH 2026-10-18 真 Edge 实跑撞到），
    #   光看返回信封完全看不出来 —— objects[].eid 明明在那儿，一喂就断。
    return _real_eid_put_g3b(tok, hwnd, [], walk, 无明细=True)
'''


INJECT_AFTER_BASELINE = '''

# ===== 以下为 窗G3c 注入段（跑红自证专用，勿入产品代码）=====
_real_snap_put_g3c = _screen_snap_put_ex


def _screen_snap_put_ex(hwnd, version, uids, meta, uids_all=None,      # noqa: F811  故意覆盖真版
                        nodes_n=0, caller_seen=True):
    # 注入的缺陷：**动作后回读照旧推进调用方的基线**（无视 caller_seen=False）。
    #   真实后果：点击内部自己扫过一屏，把「调用方上次看到的那一版」顶掉了。
    #   于是调用方随后带 since 再比时，动作造成的变化已经被点击自己吃掉 ——
    #   真窗口实测：version_match=false、changed_objects 为空、明明换了字的
    #   控件还报 uid_state=OK。点击看上去成功了，而「发生了什么」没人说得清。
    return _real_snap_put_g3c(hwnd, version, uids, meta, uids_all, nodes_n, True)
'''

# ★ 窗B 的两个注入。
#   ⚠ 这两个**不是**接在 core.py 上（`build_injected` 的 append 方式），而是改影子件：
#     一个改 cdp.py 的影子件，一个改契约的影子件。注入的东西一样是「跑红自证专用」。

INJECT_CDP_PORT = '''

# ===== 以下为 窗B 注入段（跑红自证专用，勿入产品代码）=====
# 注入的缺陷：**把 --port 丢掉**，无论命令行给多少都硬连 9222
#   —— 也就是把 cdp.py 改回 2026-10-18 之前的样子。
# 真实后果：ctl_browser launch 起在 9333 的实例，ctl_screen 的 DOM 段连不上，
#   R1 恒为 false，channels.must_vlm 只好退回 VLM —— 而屏幕明明看得见。
_real_http_get_B = http_get


def _inj_http_get_B(path, host="127.0.0.1", port=None):      # noqa: F811  故意覆盖真版
    return _real_http_get_B(path, host, 9222)


http_get = _inj_http_get_B
'''

INJECT_CONTRACT_ENV = '''
★ 窗B：把契约里「data.结果」那一句**删掉**（不是改错、是真的没有）⇒ 判据必须跑红。
'''

INJECT = {
    # ★ 窗G2 的四个新注入（R3 那条通道的四根骨头各断一次）
    "ocr_wired": ("R3 根本没接线（OCR 三要素一律不接）", "append", INJECT_OCR_WIRED),
    "ocr_cap": ("ocr 段去掉 80 词上限", "append", INJECT_OCR_CAP),
    "ocr_bbox": ("bbox 越界也算可用（会点偏）", "append", INJECT_OCR_BBOX),
    "ocr_timeout": ("OCR 超时当没事（拿半截结果当证据）", "append", INJECT_OCR_TIMEOUT),
    "guarantee": ("拿掉必进保证（纯按分取前 top）", "append", INJECT_GUARANTEE),
    "text_key": ("uid 里去掉 text_key（只剩结构路径）", "append", INJECT_TEXT_KEY),
    "version": ("screen_version 钉死不变", "append", INJECT_VERSION),
    # ★ 窗C1 的四个新注入（每个只红自己那一组）
    "conf": ("置信度退回对象级一个数（bbox 被 text 拖低）", "append", INJECT_CONF),
    "uid_state": ("uid 三档塌成两档（内容变了也报 STALE）", "append", INJECT_UID_STATE),
    "r2": ("R2 不看 name_trust（错位页面假通过）", "append", INJECT_R2),
    "version_reply": ("since 的版本应答三件套不回", "append", INJECT_VERSION_REPLY),
    # ★ 窗D1 的三个新注入（每个只红自己那一组）
    "watch_exempt": ("声明仍然被豁免（text_key 照压成 ~）", "append", INJECT_WATCH_EXEMPT),
    "watch_leak": ("未声明的 volatile 节点也进位（风暴回流）", "append", INJECT_WATCH_LEAK),
    "watch_silent": ("unmatched 静默吞掉（rc=0）", "append", INJECT_WATCH_SILENT),
    # ★ 窗E1 的三个新注入（每个只红自己那一组）
    "watch_shadow": ("退回只记第一条（重叠声明互蔽 ⇒ 误拒）", "append", INJECT_WATCH_SHADOW),
    "watch_width": ("宽度上限名存实亡（宽串放行）", "append", INJECT_WATCH_WIDTH),
    "watch_ctx": ("拒绝信封抠掉屏上下文（分不清校验失败与白屏）", "append", INJECT_WATCH_CTX),
    # ★ 窗F2 的新注入：去掉就绪判定与暖机重试
    "warmup": ("没有就绪判定也没有暖机（首扫直接当成品）", "append", INJECT_WARMUP),
    # ★ 窗F2b 的两个新注入
    "warmup_first": ("网页类首扫不探第二次（冷扫当热屏交出去）", "append", INJECT_WARMUP_FIRST),
    "warmup_nb": ("首扫强制探测忘了判窗口类（非网页也白等 400ms）", "append", INJECT_WARMUP_NB),
    # ★ 窗G1 的三个新注入
    "cdp_wired": ("R1 没接线（CDP 三要素一律不接）", "append", INJECT_CDP_WIRED),
    "cdp_cap": ("dom 段去掉 60 条上限", "append", INJECT_CDP_CAP),
    "cdp_steal": ("端口被占也照样抢（另起实例顶上去）", "append", INJECT_CDP_STEAL),
    # ★ 窗G3 的三个新注入（eid 旁表 / expect_uid 闸 / after 归因各断一次）
    "eid_link": ("ctl_screen 的 eid 换成自造号（旁表查无此号）", "append", INJECT_EID_LINK),
    "expect_uid": ("expect_uid 收下但不校验（对不上照样点）", "append", INJECT_EXPECT_UID),
    "after_state": ("after.uid_state 恒报 OK（点错对象也说达成）", "append", INJECT_AFTER_STATE),
    # ★ 窗G3b 的新注入：capture 不带元素明细（真门路上 eid 直接断）
    "cap_detail": ("capture 一律不写元素明细（eid 在真门路上点不了）", "append", INJECT_CAP_DETAIL),
    # ★ 窗G3c 的新注入：动作后回读推进调用方的 since 基线
    "after_baseline": ("after 回读照旧推进基线（调用方看不见自己动作的变化）",
                    "append", INJECT_AFTER_BASELINE),
    # ★ 窗B 的两个新注入（不是接 core.py，是改 cdp.py / 契约的影子件）
    "cdp_port": ("cdp.py 把 --port 丢掉（非 9222 硬连 9222）", "cdp", INJECT_CDP_PORT),
    "contract_env": ("契约删掉「data.结果」那一句", "contract", INJECT_CONTRACT_ENV),
}


def build_injected(tmp, kinds):
    global CDP_UNDER_TEST, CONTRACT_UNDER_TEST
    with io.open(CORE, "r", encoding="utf-8") as f:
        src = f.read()
    notes = []
    for k in kinds:
        desc, mode, snippet = INJECT[k]
        if mode == "append":
            src = src + snippet
        elif mode == "cdp":
            # cdp.py 的影子件：现役产物 + 注入段。判据改盯它，就等于「装出去的那份被改坏了」。
            d = os.path.join(tmp, "legacy_tools")
            os.makedirs(d, exist_ok=True)
            CDP_UNDER_TEST = os.path.join(d, "cdp.py")
            with io.open(CDP_PROD, "r", encoding="utf-8", newline="") as f:
                csrc = f.read()
            with io.open(CDP_UNDER_TEST, "w", encoding="utf-8", newline="") as f:
                f.write(csrc + snippet)
        elif mode == "contract":
            pass          # 下面统一处理：影子件先抄好，再按需把那句删掉
        else:
            raise SystemExit("未知注入方式：%s" % mode)
        notes.append("%s=%s" % (k, desc))
    tag = "_".join(kinds)
    pkg = os.path.join(tmp, "src", "agentctl")
    os.makedirs(pkg, exist_ok=True)
    os.makedirs(os.path.join(tmp, "contract"), exist_ok=True)
    dst = os.path.join(pkg, "core.py")
    with io.open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    # 契约也照抄一份：影子仓的 ROOT 要能找到它（tool_list 靠它）
    cpath = os.path.join(tmp, "contract", "tools.json")
    shutil.copyfile(CONTRACT, cpath)
    CONTRACT_UNDER_TEST = cpath
    for k in kinds:
        if INJECT[k][1] != "contract":
            continue
        with io.open(cpath, "r", encoding="utf-8", newline="") as f:
            cs = f.read()
        if CONTRACT_ENV_SENTENCE not in cs:
            raise SystemExit("契约注入锚点不在现役契约里（窗B 那句已被改动？）")
        with io.open(cpath, "w", encoding="utf-8", newline="") as f:
            f.write(cs.replace(CONTRACT_ENV_SENTENCE, "", 1))
    return dst, "；".join(notes)

# ======================================================================
# 断言组。每组返回 (组名, 红线列表, 证据字典)
# ======================================================================
def need(cond, msg, reds):
    if not cond:
        reds.append(msg)
    return bool(cond)


def check_top_and_must(core, fx):
    """(1) 默认 top=40：返回 ≤40，且**全部视口内可交互节点都在**；omitted 诚实报数。"""
    reds = []
    core._screen_forget(0)
    r, sc = call(core, query=QUERY)
    ev = {}
    if not need(bool(r.get("ok")), "dispatch 没成功：code=%s warning=%s"
                % (r.get("code"), r.get("warning")), reds):
        return "(1) 默认 top 与必进保证", reds, ev
    objs = sc.get("objects") or []
    om = sc.get("omitted") or {}
    ev["objects条数"] = len(objs)
    ev["omitted"] = {k: om.get(k) for k in ("total", "returned", "reason", "top",
                                              "必进节点", "breakdown")}
    need(len(objs) <= 40, "默认返回 %d 条 > top=40（必进保证不该顶穿到这个夹具上）" % len(objs), reds)
    need(om.get("top") == 40, "omitted.top=%r 不是默认的 40" % om.get("top"), reds)

    got = sorted(o["label"] for o in objs if o.get("interactive") and o.get("in_view"))
    want = sorted(fx.button_labels())
    missing = [x for x in want if x not in got]
    ev["视口内可交互"] = "%d/%d" % (len(got), len(want))
    need(not missing, "视口内可交互节点被裁掉 %d 个：%s" % (len(missing), missing[:6]), reds)
    need(len(got) == N_BTN, "视口内可交互节点数 %d ≠ 夹具的 %d" % (len(got), N_BTN), reds)

    need(om.get("total") == EXPECT_TOTAL,
         "omitted.total=%r ≠ 真实候选总数 %d" % (om.get("total"), EXPECT_TOTAL), reds)
    need(om.get("returned") == len(objs),
         "omitted.returned=%r 与实际 objects %d 不一致" % (om.get("returned"), len(objs)), reds)
    bd = om.get("breakdown") or {}
    need(bd.get("viewport_filter") == N_OFF,
         "omitted.breakdown.viewport_filter=%r ≠ %d" % (bd.get("viewport_filter"), N_OFF), reds)
    need(bd.get("attention_topn") == EXPECT_IN_VIEW - len(objs),
         "omitted.breakdown.attention_topn=%r ≠ %d"
         % (bd.get("attention_topn"), EXPECT_IN_VIEW - len(objs)), reds)
    need(om.get("total") - om.get("returned", 0)
         == bd.get("attention_topn", 0) + bd.get("viewport_filter", 0),
         "omitted 对不上账：total-returned ≠ topn+viewport", reds)

    # 默认口径下屏外按钮**不该**在返回里（视口过滤真的在起作用）
    need(not by_label(objs, "屏外按钮"), "默认口径把屏外按钮也返回了", reds)

    # include_offscreen=True：屏外那些也要能回来，且带 in_view=false。
    # ★ 这里必须把 top 放开：top=40 的名额已经被 38 个必进按钮 + 2 块大文本占满，
    #   屏外按钮挤不进来是**正确行为**（它们分 0.25，只有真的排得上才该给）。
    r2, sc2 = call(core, query=QUERY, include_offscreen=True, top=500)
    off = by_label(sc2.get("objects") or [], "屏外按钮")
    ev["include_offscreen时屏外节点"] = len(off)
    need(len(off) == N_OFF, "include_offscreen=True 时屏外按钮只回了 %d/%d"
         % (len(off), N_OFF), reds)
    need(all(o.get("in_view") is False for o in off), "屏外节点没标 in_view=false", reds)
    need((sc2.get("omitted") or {}).get("total") == EXPECT_TOTAL,
         "include_offscreen=True 时 omitted.total 变了（应恒为真实总数 %d）" % EXPECT_TOTAL, reds)
    need((sc2.get("omitted") or {}).get("breakdown", {}).get("viewport_filter") == 0,
         "include_offscreen=True 时还在报视口丢弃", reds)
    return "(1) 默认 top 与必进保证", reds, ev


def check_uid_and_version(core, fx):
    """(2) uid 三连稳定；改标题 ⇒ screen_version 变；版本**服务端签发**，回传改不了它。"""
    reds = []
    core._screen_forget(0)
    seen, vers = [], []
    for _ in range(3):
        _, sc = call(core, top=500)
        seen.append(tuple(uids_of(sc.get("objects") or [])))
        vers.append(sc.get("screen_version"))
    ev = {"三次uid是否相同": len(set(seen)) == 1,
          "三次version": sorted(set(vers)), "uid数": len(seen[0])}
    need(len(set(seen)) == 1, "同一屏连续三次 uid 不稳定（%d 种组合）" % len(set(seen)), reds)
    need(len(set(vers)) == 1, "窗口没变时 screen_version 却每次都变：%s" % sorted(set(vers)), reds)

    fx.set_title("B1语义屏判据靶窗（已改标题）")
    _, sc = call(core, top=500)
    v2 = sc.get("screen_version")
    ev["改标题后version"] = v2
    need(v2 != vers[0], "改窗口标题后 screen_version 没变（%s）" % v2, reds)
    need(tuple(uids_of(sc.get("objects") or [])) == seen[0],
         "只改标题却把 uid 改掉了（uid 不该受标题影响）", reds)

    # 回传一个瞎编的 since：只许被回显，不许影响签发
    r3, sc3 = call(core, top=500, since="sv1-我瞎编的版本")
    ev["回传瞎编since后的version"] = sc3.get("screen_version")
    need(sc3.get("screen_version") == v2,
         "回传 since 之后签发的版本变了（调用方能影响版本 ⇒ 修正2 没做到）", reds)
    need(sc3.get("screen_version") != "sv1-我瞎编的版本", "签发的版本等于调用方传进来的字符串", reds)
    need((sc3.get("delta") or {}).get("since_回显") == "sv1-我瞎编的版本",
         "since 没被如实回显", reds)
    need((sc3.get("delta") or {}).get("since_是本进程签发的") is False,
         "瞎编的 since 被当成真版本了", reds)

    # 试图直接指定版本：契约层 additionalProperties=false 挡一道，core 层忽略并明说
    r4, sc4 = call(core, top=500, version="我指定一个")
    need(sc4.get("screen_version") == v2, "传 version 竟改掉了签发的版本", reds)
    need(any("服务端签发" in str(x) for x in (r4.get("blind_spots") or [])),
         "传 version 之后没有在 blind_spots 里明说被忽略", reds)
    fx.set_title("B1语义屏判据靶窗")
    return "(2) uid 稳定与 screen_version 签发", reds, ev


def check_volatile(core, fx):
    """(3) volatile 豁免。**两半一起判**：改了字 uid 变的（text_key 在跑）+ 计时器不变的（豁免在跑）。"""
    reds = []
    core._screen_forget(0)
    fx.tick = 0
    _, sc1 = call(core, top=500)
    # ★ 选择器用「剩余 0」（计时器形如「剩余 00:5x」）：窗D1 加的 T2「剩余可选 N 项」
    #   也以「剩余」开头，用「剩余」会同时命中两个 —— 这里只把**选择器**收紧，断言本身没动。
    t1 = by_label(sc1.get("objects") or [], "剩余 0")
    s1 = by_label(sc1.get("objects") or [], "当前状态")
    ev = {}
    if not (len(t1) == 1 and len(s1) == 1):
        reds.append("夹具里没找着计时器/状态行（各要 1 个，实得 %d/%d）" % (len(t1), len(s1)))
        return "(3) volatile 豁免", reds, ev
    timer1, stat1 = t1[0], s1[0]
    ev["计时器volatile"] = timer1.get("volatile")
    ev["计时器uid"] = timer1.get("uid")
    ev["状态行uid"] = stat1.get("uid")
    need(timer1.get("volatile") is True, "计时器节点 volatile 不为 true（它就该是 volatile）", reds)
    need(stat1.get("volatile") is False, "状态行被判成 volatile 了（它不该是）", reds)
    need(str(timer1.get("uid")).split("@")[-1] == "~",
         "volatile 节点的 text_key 不是 ~：%r" % timer1.get("uid"), reds)
    need(str(stat1.get("uid")).split("@")[-1].startswith("t"),
         "非 volatile 节点没有 text_key：%r" % stat1.get("uid"), reds)

    v1 = sc1.get("screen_version")
    fx.advance()
    _, sc2 = call(core, top=500, since=v1, watch=True)
    t2 = by_label(sc2.get("objects") or [], "剩余 0")
    s2 = by_label(sc2.get("objects") or [], "当前状态")
    if not (len(t2) == 1 and len(s2) == 1):
        reds.append("第二次调用后计时器/状态行不见了")
        return "(3) volatile 豁免", reds, ev
    timer2, stat2 = t2[0], s2[0]
    ev["计时器label前后"] = [timer1.get("label"), timer2.get("label")]
    ev["状态行label前后"] = [stat1.get("label"), stat2.get("label")]
    need(timer1.get("label") != timer2.get("label"),
         "计时器文本没变，这条断言就是空的（夹具坏了）", reds)
    need(stat1.get("label") != stat2.get("label"),
         "状态行文本没变，这条断言就是空的（夹具坏了）", reds)
    need(timer1.get("uid") == timer2.get("uid"),
         "volatile 节点换了字 uid 就变了 ⇒ STALE 风暴没被挡住：%r vs %r"
         % (timer1.get("uid"), timer2.get("uid")), reds)
    need(stat1.get("uid") != stat2.get("uid"),
         "非 volatile 节点换了字 uid 却没变 ⇒ uid 里的 text_key 根本没参与比对", reds)
    d = sc2.get("delta") or {}
    ev["delta.消失数"] = d.get("消失数")
    ev["delta.新增数"] = d.get("新增数")
    need(timer1.get("uid") not in (d.get("消失") or []),
         "计时器被算进了 delta.消失（它没变，不该消失）", reds)
    need(timer1.get("uid") not in (d.get("新增") or []),
         "计时器被算进了 delta.新增（它一直在，不该是新的）", reds)
    fx.tick = 0
    return "(3) volatile 豁免", reds, ev

def check_confidence_min(core, fx):
    """(4) source_confidence 多来源**取 min**；返回里没有裸 confidence 字段。"""
    reds = []
    core._screen_forget(0)
    _, sc = call(core, query=QUERY, top=500)
    objs = sc.get("objects") or []
    ev = {"对象数": len(objs)}

    need(all("confidence" not in o for o in objs),
         "返回里出现了裸 confidence 字段（应当只有 source_confidence）", reds)
    need(all("source_confidence" in o for o in objs), "有对象缺 source_confidence", reds)
    need(all(abs(o["source_confidence"] - 1.0) < 1e-9 for o in objs),
         "只有 UIA 一条来源时 source_confidence 应恒为 1.0，实得 %s"
         % sorted(set(o["source_confidence"] for o in objs)), reds)
    need(all(o["source"] == "uia" for o in objs), "source 应为 uia", reds)

    # 纯函数直验：min，不是 mean 也不是 max
    got = core._agg_source_confidence([
        {"source": "uia", "source_confidence": 1.0},
        {"source": "ocr", "source_confidence": 0.55},
        {"source": "cdp", "source_confidence": 0.20}])[0]
    ev["三来源合成值"] = got
    need(abs(got - 0.20) < 1e-9, "三来源(1.0/0.55/0.20)合成=%r，应取 min=0.20（mean=0.583、max=1.0 都错）" % got, reds)
    need(core._agg_source_confidence([])[0] == 0.0, "空证据表应报 0.0，不是 1.0", reds)

    # 端到端：给「状态行」混进一条 OCR 证据，看它是不是真的取 min
    target = by_label(objs, "当前状态")
    if not need(len(target) == 1, "夹具里没找着状态行", reds):
        return "(4) source_confidence 取 min", reds, ev
    tkey = target[0]["uid"]
    real = core._screen_extra_evidence

    def fake_extra(uid, node, _t=tkey):
        if uid == _t:
            return [{"source": "ocr", "source_confidence": 0.55}]
        return []

    core._screen_extra_evidence = fake_extra
    try:
        _, sc2 = call(core, query=QUERY, top=500)
        hit = by_label(sc2.get("objects") or [], "当前状态")[0]
        others = [o for o in (sc2.get("objects") or []) if o["uid"] != tkey]
        ev["混入OCR后的source"] = hit.get("source")
        ev["混入OCR后的source_confidence"] = hit.get("source_confidence")
        need(hit.get("source") == "uia+ocr", "混入 OCR 后 source 应是 uia+ocr，实得 %r" % hit.get("source"), reds)
        need(abs(hit.get("source_confidence", 0) - 0.55) < 1e-9,
             "混入 0.55 的 OCR 证据后 source_confidence 应取 min=0.55，实得 %r"
             % hit.get("source_confidence"), reds)
        need(all(abs(o["source_confidence"] - 1.0) < 1e-9 for o in others),
             "没混证据的对象被连坐了（不该取它们的 min）", reds)
    finally:
        core._screen_extra_evidence = real
    return "(4) source_confidence 取 min", reds, ev


def check_channels_and_trust(core, fx):
    """(5) channels 段逐格对上；name_trust 对 Chromium 给 low；字段齐全。"""
    reds = []
    core._screen_forget(0)
    ev = {}

    tt = core.screen_must_vlm
    need(tt(False, False, False) is True, "must_vlm(¬R1,¬R2,¬R3) 应为 True", reds)
    need(tt(True, False, False) is False, "R1 成立时 must_vlm 应为 False", reds)
    need(tt(False, True, False) is False, "R2 成立时 must_vlm 应为 False", reds)
    need(tt(False, False, True) is False, "R3 成立时 must_vlm 应为 False", reds)
    ev["真值表"] = "(000)=%s (100)=%s (010)=%s (001)=%s" % (
        tt(False, False, False), tt(True, False, False),
        tt(False, True, False), tt(False, False, True))

    # query 命中 ⇒ R2 成立（可交互 38 ≥ 5 且目标命中）⇒ 不必上 VLM
    _, sc = call(core, query=QUERY, top=500)
    ch = sc.get("channels") or {}
    ev["query命中时的channels"] = {k: ch.get(k) for k in ("R1_cdp", "R2_uia", "R3_ocr", "must_vlm")}
    need(ch.get("R2_uia", {}).get("hit") is True,
         "可交互 %r 个 ≥5 且 query 命中，R2 应成立" % ch.get("R2_uia", {}).get("interactive_count"), reds)
    need(ch.get("must_vlm") is False, "R2 已成立，must_vlm 应为 False", reds)
    need(ch.get("R1_cdp", {}).get("wired") is False and ch.get("R1_cdp", {}).get("hit") is False,
         "R1(CDP) 本版未接线，必须报 wired=false 且 hit=false", reds)
    need(ch.get("R3_ocr", {}).get("wired") is False and ch.get("R3_ocr", {}).get("hit") is False,
         "R3(OCR) 本版未接线，必须报 wired=false 且 hit=false", reds)
    need(ch.get("R2_uia", {}).get("interactive_count") == N_BTN,
         "R2 的可交互数 %r ≠ %d" % (ch.get("R2_uia", {}).get("interactive_count"), N_BTN), reds)

    # 没有 query ⇒ 目标未命中 ⇒ R2 不成立 ⇒ must_vlm=True
    _, sc0 = call(core, query="", top=500)
    ch0 = sc0.get("channels") or {}
    ev["无query时的channels"] = {"R2": ch0.get("R2_uia"), "must_vlm": ch0.get("must_vlm")}
    need(ch0.get("R2_uia", {}).get("hit") is False, "无 query 时目标未命中，R2 不该成立", reds)
    need(ch0.get("must_vlm") is True, "三条通道都不成立时 must_vlm 应为 True", reds)

    # name_trust：原生窗口 high / Chromium 窗口 low
    fx.cls = "TkTopLevel"
    _, scn = call(core, top=500)
    trusts = sorted(set(o.get("name_trust") for o in scn.get("objects") or []))
    ev["原生窗口name_trust"] = trusts
    need(trusts == ["high"], "原生窗口的 name_trust 应为 high，实得 %s" % trusts, reds)
    fx.cls = "Chrome_WidgetWin_1"
    _, scw = call(core, top=500)
    trusts = sorted(set(o.get("name_trust") for o in scw.get("objects") or []))
    ev["Chromium窗口name_trust"] = trusts
    need(trusts == ["low"], "Chromium 窗口的 name_trust 应为 low，实得 %s" % trusts, reds)
    fx.cls = "TkTopLevel"

    # 字段齐全 + uid 两段式
    bad_fields, bad_uid = [], []
    for o in (scn.get("objects") or []):
        miss = [k for k in REQUIRED_OBJ_FIELDS if k not in o]
        if miss:
            bad_fields.append((o.get("id"), miss))
        if str(o.get("uid")).count("@") != 1:
            bad_uid.append(o.get("uid"))
    need(not bad_fields, "有对象缺字段：%s" % bad_fields[:3], reds)
    need(not bad_uid, "uid 不是 struct_key@text_key 两段式：%s" % bad_uid[:3], reds)
    need("打分口径" in scn and "必进保证" in scn, "返回里没把打分口径与必进保证讲出来", reds)

    # uid 必须两两不同。★ 这条专治「同一父节点下同角色的兄弟拿到同一个 struct_key」
    #   那种病：路径分不出来时，两个同父同角色同名的控件 uid 会撞车 —— 身份就废了。
    all_objs = scn.get("objects") or []
    uu = uids_of(all_objs)
    dup = sorted(set(u for u in uu if uu.count(u) > 1))
    ev["uid去重"] = "%d 个对象 / %d 个不同 uid" % (len(all_objs), len(set(uu)))
    need(not dup, "有 uid 撞车（%d 个）：%s" % (len(dup), dup[:3]), reds)
    skeys = [str(o["uid"]).split("@")[0] for o in all_objs]
    ev["不同struct_key数"] = len(set(skeys))
    need(len(set(skeys)) == len(skeys), "struct_key 有重复：同父下同角色的兄弟没分开编号", reds)

    # 不可交互的节点不许承诺动作。actions=["click"] 挂在题干文字上，模型会照着去点。
    bad_act = [o["label"][:12] for o in all_objs
               if bool(o.get("actions")) != bool(o.get("interactive"))]
    ev["actions口径"] = "可交互 %d 个有动作 / 不可交互 %d 个 actions 为空" % (
        sum(1 for o in all_objs if o.get("interactive")),
        sum(1 for o in all_objs if not o.get("interactive")))
    need(not bad_act, "actions 与 interactive 对不上（不可交互的节点承诺了动作）：%s" % bad_act[:4], reds)
    return "(5) channels / name_trust / 字段", reds, ev


def check_contract(core):
    """(6) 契约与接线：tools.json 合法、条目齐全、TOOLS 表接上、tool_list 能端出去。"""
    reds = []
    ev = {}
    try:
        with io.open(CONTRACT, encoding="utf-8") as f:
            spec = json.load(f)
    except Exception as e:
        return "(6) 契约与接线", ["contract/tools.json 不是合法 JSON：%s" % e], ev
    names = [t["name"] for t in spec.get("tools") or []]
    ev["契约工具数"] = len(names)
    if not need("ctl_screen" in names, "契约里没有 ctl_screen", reds):
        return "(6) 契约与接线", reds, ev
    need(len(names) == len(set(names)), "契约里有重名工具", reds)
    ent = [t for t in spec["tools"] if t["name"] == "ctl_screen"][0]
    sch = ent.get("input_schema") or {}
    need(ent.get("gate") == "A", "ctl_screen 的 gate 应是 A（只读感知），实得 %r" % ent.get("gate"), reds)
    need((ent.get("annotations") or {}).get("readOnlyHint") is True, "readOnlyHint 应为 true", reds)
    need(sch.get("additionalProperties") is False, "additionalProperties 应为 false", reds)
    for p in ("hwnd", "top", "include_offscreen", "watch", "query", "since"):
        need(p in (sch.get("properties") or {}), "契约入参缺 %s" % p, reds)
    need("ctl_screen" in core.TOOLS, "TOOLS 表里没有 ctl_screen", reds)
    listed = {t["name"]: t for t in core.tool_list()}
    ev["tool_list里有"] = "ctl_screen" in listed
    need("ctl_screen" in listed, "tool_list() 没端出 ctl_screen（tools/list 会缺）", reds)
    need(not listed.get("ctl_screen", {}).get("_status"), "ctl_screen 被标成未实现", reds)
    ev["契约gate"] = ent.get("gate")
    return "(6) 契约与接线", reds, ev

def _load_cdp(path, name):
    """把 cdp.py 当模块装进来（不执行 main）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _cdp_probe(path, name, extra_argv):
    """跑一次 cdp.py 的 `pages` 子命令，把它**真正要连的 URL** 录下来。

    ★ 假 cdp 的接缝在 urllib.request.urlopen 这一层 —— 端口到底有没有被转发，
      看最终拼出来的 URL 里是 :9333 还是 :9222。这比「argparse 认不认识 --port」硬得多：
      认了参数、底下还连 9222（正是改前 cdp.py 的样子）照样会红。
    ★ 判据里**绝不起浏览器、不连网**：urlopen 换掉、gate_check 放行、标签页表给空表。
    ★ 返回 (rc, 录到的 URL 列表)。
    """
    m = _load_cdp(path, name)
    m.gate_check = lambda cmd: None                 # 判据不许碰真闸门 / 真通行证
    seen = []

    class _Resp(object):
        status = 200

        def read(self):
            return json.dumps([]).encode("utf-8")    # 空标签页表：后面什么都不做

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout=None):
        seen.append(url if isinstance(url, str) else getattr(url, "full_url", str(url)))
        return _Resp()

    import urllib.request
    real = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    old_argv = sys.argv
    sys.argv = [name, "pages"] + list(extra_argv)
    try:
        rc = m.main()
    finally:
        sys.argv = old_argv
        urllib.request.urlopen = real
    return rc, seen


def check_cdp_port_and_contract():
    """(11) 窗B：cdp.py 的 --port 真被转发（起落都非 9222 也认），＋ 契约那两句在位。

    判四件事：
      a. `--port 9333` ⇒ cdp.py 真的去连 **:9333**（不是认了参数、底下还连 9222）；
      b. **不给** --port ⇒ 仍然连 :9222（向后兼容，老调用方不许被打断）；
      c. 真源与产物**都**如此（产物是包里真正跑的那份，两份都要盯）；
      d. 契约那两句在位：ctl_screen 写明结果包在 `data.结果`（objects 在
         `data.结果.objects`）、ctl_browser 的 port 写明与 `cdp.py --port` 同步。
    """
    reds = []
    ev = {}

    # ── a / b / c：cdp.py 的 --port ────────────────────────────────────────
    pairs = [("产物", CDP_UNDER_TEST)]
    if os.path.normcase(os.path.abspath(CDP_UNDER_TEST)) != \
            os.path.normcase(os.path.abspath(CDP_SRC)):
        pairs.append(("真源", CDP_SRC))       # 盯影子件时，真源一并对照
    for tag, path in pairs:
        if not os.path.exists(path):
            reds.append("%s cdp.py 不在：%s" % (tag, path))
            continue
        try:
            rc, seen = _cdp_probe(path, "_cdp_under_test_" + tag, ["--port", "9333"])
        except SystemExit as e:
            reds.append("%s cdp.py 的 --port 9333 被 argparse 拒了：%s" % (tag, e))
            continue
        ev["%s_9333" % tag] = seen
        need(rc == 0, "%s cdp.py pages --port 9333 返回 rc=%s（期望 0）" % (tag, rc), reds)
        need(bool(seen), "%s cdp.py pages --port 9333 一个 HTTP 请求都没发" % tag, reds)
        bad = [u for u in seen if ":9333" not in u]
        need(not bad, "%s cdp.py 收了 --port 9333 却仍去连这些地址（端口没转发）：%s"
             % (tag, bad[:3]), reds)

        rc0, seen0 = _cdp_probe(path, "_cdp_default_" + tag, [])
        ev["%s_默认" % tag] = seen0
        bad0 = [u for u in seen0 if ":9222" not in u]
        need(not bad0, "%s cdp.py 不给 --port 时没连默认 9222（向后兼容破了）：%s"
             % (tag, bad0[:3]), reds)

    # ── d：契约那两句 ─────────────────────────────────────────────────────
    try:
        with io.open(CONTRACT_UNDER_TEST, encoding="utf-8") as f:
            spec = json.load(f)
    except Exception as e:
        return "(11) cdp.py --port 与契约两句", ["契约不是合法 JSON：%s" % e], ev
    ents = {t["name"]: t for t in spec.get("tools") or []}
    scr = (ents.get("ctl_screen") or {}).get("description") or ""
    # ★ 盯「信封结构」这四个字，而**不是**只盯「data.结果」这五个字 ——
    #   description 里本来就还有一句「data.结果.capture_id 与它是同一张」（窗G3 加的），
    #   只盯 token 的话，**把窗B 这整句删掉照样绿** —— 那这条判据就是死的。
    need("信封结构" in scr,
         "ctl_screen 的 description 里没有「结果包在 data.结果 里（信封结构）」那一句", reds)
    need("data.结果.objects" in scr,
         "ctl_screen 的 description 没有写明 objects 在 data.结果.objects", reds)
    ev["ctl_screen含data.结果"] = {"信封结构": "信封结构" in scr,
                                    "data.结果.objects": "data.结果.objects" in scr}
    br = ents.get("ctl_browser") or {}
    portd = (((br.get("input_schema") or {}).get("properties") or {})
             .get("port", {}).get("description") or "")
    need("cdp.py --port" in portd,
         "ctl_browser 的 port 参数没写明与 cdp.py --port 同步", reds)
    ev["ctl_browser_port说明"] = portd[-120:]
    return "(11) cdp.py --port 与契约两句", reds, ev


def check_conf_attrs(core, fx):
    """(7) 属性级置信度：三档都在、聚合=min(existence,text)、**bbox 不被 text 拖低**。"""
    reds = []
    core._screen_forget(0)
    _, sc = call(core, query=QUERY, top=500)
    objs = sc.get("objects") or []
    ev = {"对象数": len(objs)}
    if not need(objs, "夹具这一屏空了", reds):
        return "(7) 属性级置信度", reds, ev

    # ── 纯函数直验：UIA 坐标精确（1.00）+ OCR 框还行（0.90）但**认字很弱**（0.30）
    items = [{"source": "uia",
              "source_confidence": {"bbox": 1.00, "text": 1.00, "existence": 1.00}},
             {"source": "ocr",
              "source_confidence": {"bbox": 0.90, "text": 0.30, "existence": 0.80}}]
    a = core._agg_source_conf_attrs(items)
    ev["uia精确+ocr弱文本"] = a
    need(isinstance(a, dict), "_agg_source_conf_attrs 应返回逐字段的 dict，实得 %r" % type(a), reds)
    for k, want in (("bbox", 0.90), ("text", 0.30), ("existence", 0.80)):
        need(abs(float((a or {}).get(k, -1)) - want) < 1e-9,
             "属性级聚合 %s 应=min(各来源)=%r，实得 %r" % (k, want, (a or {}).get(k)), reds)
    need(abs(float((a or {}).get("agg", -1)) - 0.30) < 1e-9,
         "聚合口径 agg 应=min(existence,text)=0.30，实得 %r" % (a or {}).get("agg"), reds)
    # ★ 本组的命门：bbox 严格高于 text —— 一旦退回「对象级取 min」，这两个数必然相等。
    need(float((a or {}).get("bbox", 0)) > float((a or {}).get("text", 0)) + 1e-9,
         "bbox 被 text 拖平了（退回对象级取 min）：bbox=%r text=%r"
         % ((a or {}).get("bbox"), (a or {}).get("text")), reds)
    z = core._agg_source_conf_attrs([])
    need(all(abs(float((z or {}).get(k, -1))) < 1e-9
             for k in ("bbox", "text", "existence", "agg")),
         "空证据表三档都该报 0.0，实得 %r" % z, reds)

    # ── 对象上三档都在；只有 UIA 时恒为 1.0；聚合值 = min(conf_existence, conf_text)
    need(all(all(k in o for k in ("conf_bbox", "conf_text", "conf_existence",
                                  "source_confidence")) for o in objs),
         "有对象缺 conf_bbox / conf_text / conf_existence / source_confidence", reds)
    need(all(abs(o["conf_bbox"] - 1.0) < 1e-9 and abs(o["conf_text"] - 1.0) < 1e-9
             and abs(o["conf_existence"] - 1.0) < 1e-9
             and abs(o["source_confidence"] - 1.0) < 1e-9 for o in objs),
         "只有 UIA 一条来源时三档与聚合值都该是 1.0", reds)
    need(all(abs(o["source_confidence"] - min(o["conf_existence"], o["conf_text"])) < 1e-9
             for o in objs),
         "source_confidence 与 min(conf_existence, conf_text) 对不上", reds)
    need("conf_口径" in sc, "返回里没把 conf 的口径讲出来", reds)

    # ── 端到端：给状态行混进一条**属性级** OCR 证据（框还行、字很弱、存在性中等）
    target = by_label(objs, "当前状态")
    if not need(len(target) == 1, "夹具里没找着状态行", reds):
        return "(7) 属性级置信度", reds, ev
    tkey = target[0]["uid"]
    real = core._screen_extra_evidence

    def fake_extra(uid, node, _t=tkey):
        if uid == _t:
            return [{"source": "ocr",
                     "source_confidence": {"bbox": 0.90, "text": 0.30, "existence": 0.80}}]
        return []

    core._screen_extra_evidence = fake_extra
    try:
        _, sc2 = call(core, query=QUERY, top=500)
        hits = by_label(sc2.get("objects") or [], "当前状态")
        if not need(len(hits) == 1, "混证据后状态行不见了", reds):
            return "(7) 属性级置信度", reds, ev
        hit = hits[0]
        others = [o for o in (sc2.get("objects") or []) if o["uid"] != tkey]
        ev["混入OCR后"] = {k: hit.get(k) for k in ("source", "conf_bbox", "conf_text",
                                                 "conf_existence", "source_confidence")}
        need(hit.get("source") == "uia+ocr",
             "混入 OCR 后 source 应是 uia+ocr，实得 %r" % hit.get("source"), reds)
        need(abs(hit["conf_bbox"] - 0.90) < 1e-9,
             "conf_bbox 应=0.90（UIA 的 1.00 与 OCR 的 0.90 取 min），实得 %r" % hit["conf_bbox"], reds)
        need(abs(hit["conf_text"] - 0.30) < 1e-9,
             "conf_text 应=0.30，实得 %r" % hit["conf_text"], reds)
        need(abs(hit["conf_existence"] - 0.80) < 1e-9,
             "conf_existence 应=0.80，实得 %r" % hit["conf_existence"], reds)
        need(abs(hit["source_confidence"] - 0.30) < 1e-9,
             "source_confidence 应=min(0.80,0.30)=0.30，实得 %r" % hit["source_confidence"], reds)
        # ★「bbox 不被 text 拉低」的端到端版
        need(hit["conf_bbox"] > hit["conf_text"] + 1e-9,
             "端到端也没挡住：conf_bbox=%r 被 conf_text=%r 拖平了"
             % (hit["conf_bbox"], hit["conf_text"]), reds)
        need(hit["conf_bbox"] > hit["source_confidence"] + 1e-9,
             "conf_bbox 被聚合值压平了（bbox 该单列，不进聚合）", reds)
        need(all(abs(o["conf_bbox"] - 1.0) < 1e-9 and abs(o["conf_text"] - 1.0) < 1e-9
                 for o in others), "没混证据的对象被连坐了（不该取它们的 min）", reds)
    finally:
        core._screen_extra_evidence = real
    return "(7) 属性级置信度", reds, ev


def check_uid_state(core, fx):
    """(8) uid 三档：改文本⇒TEXT_CHANGED；挪结构⇒STALE；都不变⇒OK；没基线⇒STALE。"""
    reds = []
    ev = {}
    core._screen_forget(0)
    fx.tick = 0
    fx.n_extra_btn = 0

    # ── 纯函数：三档各自的口径（含「没有基线」与「uid 拆不开」）
    st = core.screen_uid_state
    cases = [("完全一样", "PaneControl/TextControl@tAAA", "PaneControl/TextControl@tAAA", "OK"),
             ("只改文本", "PaneControl/TextControl@tAAA", "PaneControl/TextControl@tBBB", "TEXT_CHANGED"),
             ("结构变了", "PaneControl/TextControl@tAAA", "PaneControl/ButtonControl@tAAA", "STALE"),
             ("没有基线", "", "PaneControl/TextControl@tAAA", "STALE"),
             ("uid拆不开", "这不是一个uid", "PaneControl/TextControl@tAAA", "STALE")]
    ev["纯函数三档"] = {}
    for name, p, c, want in cases:
        got = st(p, c)
        ev["纯函数三档"][name] = "%s（应 %s）" % (got, want)
        need(got == want,
             "screen_uid_state 的「%s」档 = %r，应为 %r" % (name, got, want), reds)
    states = sorted(getattr(core, "SCREEN_UID_STATES", ()) or ())
    ev["SCREEN_UID_STATES"] = states
    need(states == ["OK", "STALE", "TEXT_CHANGED"],
         "SCREEN_UID_STATES 应恰好是 OK / TEXT_CHANGED / STALE 三档，实得 %r" % states, reds)

    # ── 没给 since 就没有 uid_state（没有基线就没有「变没变」可判）
    _, sc1 = call(core, top=500)
    objs1 = sc1.get("objects") or []
    if not need(objs1, "夹具第一屏空了", reds):
        return "(8) uid 三档", reds, ev
    need(all("uid_state" not in o for o in objs1),
         "没给 since 却发了 uid_state（没有基线可比）", reds)
    v1 = sc1.get("screen_version")
    s1 = by_label(objs1, "当前状态")
    b1 = by_label(objs1, "选项格01")
    if not need(len(s1) == 1 and len(b1) == 1, "夹具里没找着状态行/按钮01", reds):
        return "(8) uid 三档", reds, ev
    s1, b1 = s1[0], b1[0]

    # ── ① 什么都不变 ⇒ 全 OK
    _, sc2 = call(core, top=500, since=v1)
    states2 = (sc2.get("delta") or {}).get("uid_state_统计") or {}
    ev["不变时的uid_state_统计"] = states2
    need(all(o.get("uid_state") == "OK" for o in (sc2.get("objects") or [])),
         "什么都没变却不是全 OK：%r" % sorted(set(o.get("uid_state")
                                            for o in (sc2.get("objects") or []))), reds)
    need(int(states2.get("OK") or 0) == len(sc2.get("objects") or []),
         "统计里 OK 的条数 %r 与返回对象数 %d 对不上" % (states2.get("OK"),
                                                len(sc2.get("objects") or [])), reds)

    # ── ② 只改文本（同对象）⇒ TEXT_CHANGED（**动作核验就期待这一档**）
    fx.advance()
    _, sc3 = call(core, top=500, since=sc2.get("screen_version"))
    s3 = by_label(sc3.get("objects") or [], "当前状态")
    if not need(len(s3) == 1, "第二次调用后状态行不见了", reds):
        fx.tick = 0
        return "(8) uid 三档", reds, ev
    s3 = s3[0]
    ev["状态行uid前后"] = [s1.get("uid"), s3.get("uid")]
    ev["状态行uid_state"] = s3.get("uid_state")
    need(s1.get("uid") != s3.get("uid"), "状态行换了字 uid 却没变（这条断言就是空的）", reds)
    need(str(s1["uid"]).split("@")[0] == str(s3["uid"]).split("@")[0],
         "这一步只该改 text_key，struct_key 不该变", reds)
    need(s3.get("uid_state") == "TEXT_CHANGED",
         "同对象改了字应报 TEXT_CHANGED（动作核验期待这一档），实得 %r" % s3.get("uid_state"), reds)
    t3 = by_label(sc3.get("objects") or [], "剩余 0")
    if t3:
        ev["计时器uid_state（volatile换了字）"] = t3[0].get("uid_state")
        need(t3[0].get("uid_state") == "OK",
             "volatile 节点换了字（uid 不变）却不是 OK：%r" % t3[0].get("uid_state"), reds)
    states3 = (sc3.get("delta") or {}).get("uid_state_统计") or {}
    ev["改文本后的uid_state_统计"] = states3
    need(int(states3.get("TEXT_CHANGED") or 0) >= 1,
         "统计里一条 TEXT_CHANGED 都没有：%r" % states3, reds)

    # ── ③ 挪结构（多插一个同角色按钮 ⇒ 兄弟编号后移）⇒ STALE
    fx.n_extra_btn = 1
    try:
        _, sc4 = call(core, top=500, since=sc3.get("screen_version"))
        b4 = by_label(sc4.get("objects") or [], "选项格01")
        if need(len(b4) == 1, "插了按钮之后找不着「选项格01」了", reds):
            ev["按钮uid前后"] = [b1.get("uid"), b4[0].get("uid")]
            ev["按钮uid_state"] = b4[0].get("uid_state")
            need(str(b1["uid"]).split("@")[0] != str(b4[0]["uid"]).split("@")[0],
                 "插了一个同角色按钮之后 struct_key 竟没变（这条断言就是空的）", reds)
            need(b4[0].get("uid_state") == "STALE",
                 "struct_key 变了 ⇒ 应报 STALE（寻址拒绝），实得 %r" % b4[0].get("uid_state"), reds)
            states4 = (sc4.get("delta") or {}).get("uid_state_统计") or {}
            ev["挪结构后的uid_state_统计"] = states4
            need(int(states4.get("STALE") or 0) >= 1, "统计里一条 STALE 都没有：%r" % states4, reds)
        need("uid_state_口径" in sc4, "返回里没把 uid_state 的口径讲出来", reds)
    finally:
        fx.n_extra_btn = 0
        fx.tick = 0
    return "(8) uid 三档", reds, ev


def check_r2_name_trust_gate(core, fx):
    """(9) R2 的 name_trust 门槛：offset-suspect ⇒ R2 不成立 ⇒ 这一屏必须落到 VLM。"""
    reds = []
    ev = {}

    # ── 纯函数：门槛只拦 offset-suspect（low ≠ 不许寻址）
    hi = core.screen_channels(interactive_count=38, target_hit=True, name_trust="high")
    lo = core.screen_channels(interactive_count=38, target_hit=True, name_trust="low")
    bad = core.screen_channels(interactive_count=38, target_hit=True,
                               name_trust="offset-suspect")
    ev["name_trust=high"] = {"R2": hi["R2_uia"]["hit"], "must_vlm": hi["must_vlm"]}
    ev["name_trust=low"] = {"R2": lo["R2_uia"]["hit"], "must_vlm": lo["must_vlm"]}
    ev["name_trust=offset-suspect"] = {"R2": bad["R2_uia"]["hit"],
                                        "must_vlm": bad["must_vlm"],
                                        "why": bad.get("must_vlm_why")}
    need(hi["R2_uia"]["hit"] is True and hi["must_vlm"] is False,
         "name_trust=high 时 R2 应成立、must_vlm=False", reds)
    need(lo["R2_uia"]["hit"] is True and lo["must_vlm"] is False,
         "name_trust=low 时 R2 仍应成立（门槛只拦 offset-suspect，别把网页一刀切）", reds)
    need(bad["R2_uia"]["hit"] is False,
         "offset-suspect 时 R2 **不成立**（错位命中就是假通过），实得 hit=%r"
         % bad["R2_uia"]["hit"], reds)
    need(bad["must_vlm"] is True,
         "R2 被门槛关掉后 must_vlm 必须为 True（这一屏得用眼睛看）", reds)
    need(bad["R2_uia"].get("name_trust_门槛通过") is False,
         "R2 段没有如实报「门槛是否通过」", reds)
    need("offset-suspect" in str(bad.get("must_vlm_why") or ""),
         "must_vlm_why 没说明是被 offset-suspect 门槛拦下的：%r" % bad.get("must_vlm_why"), reds)
    need(core.screen_channels(interactive_count=1, target_hit=True,
                              name_trust="offset-suspect")["R2_uia"]["hit"] is False,
         "可交互数不足时 R2 本就不该成立", reds)

    # ── 端到端：网页窗口 + 选项字母错位
    core._screen_forget(0)
    fx.cls = "Chrome_WidgetWin_1"
    try:
        fx.web_options = "normal"
        _, scn = call(core, query=QUERY, top=500)
        trusts = sorted(set(o.get("name_trust") for o in (scn.get("objects") or [])))
        ev["网页+字母正序（对照组）"] = {"name_trust": trusts,
                                 "R2": (scn.get("channels") or {}).get("R2_uia", {}).get("hit")}
        need(trusts == ["low"],
             "网页但字母正序时只该到 low，实得 %s" % trusts, reds)
        need((scn.get("channels") or {}).get("R2_uia", {}).get("hit") is True,
             "字母没乱时不该关掉 R2（别过度触发）", reds)

        fx.web_options = "scramble"
        _, scw = call(core, query=QUERY, top=500)
        trusts = sorted(set(o.get("name_trust") for o in (scw.get("objects") or [])))
        chw = scw.get("channels") or {}
        ev["网页+字母错位"] = {"name_trust": trusts,
                           "R2_uia": chw.get("R2_uia"), "must_vlm": chw.get("must_vlm"),
                           "name_trust_依据": scw.get("name_trust_依据")}
        need(trusts == ["offset-suspect"],
             "网页 + 选项字母错位应报 offset-suspect，实得 %s" % trusts, reds)
        need(chw.get("R2_uia", {}).get("hit") is False,
             "错位页面 R2 竟还成立（那就是假通过）", reds)
        need(chw.get("must_vlm") is True, "错位页面 must_vlm 必须是 True", reds)
        need("错位" in str(scw.get("name_trust_依据") or ""),
             "没把错位判据讲出来：%r" % scw.get("name_trust_依据"), reds)

        # 对照组：同样的错位放在**原生**窗口 ⇒ 不报 offset-suspect（错位判定只对网页生效）
        fx.cls = "TkTopLevel"
        _, sct = call(core, query=QUERY, top=500)
        trusts = sorted(set(o.get("name_trust") for o in (sct.get("objects") or [])))
        ev["原生窗口+同样错位（对照组）"] = {
            "name_trust": trusts,
            "R2": (sct.get("channels") or {}).get("R2_uia", {}).get("hit")}
        need(trusts == ["high"],
             "原生窗口不该报 offset-suspect（字母乱序多半是排版），实得 %s" % trusts, reds)
        need((sct.get("channels") or {}).get("R2_uia", {}).get("hit") is True,
             "原生窗口的 R2 不该被这道门槛关掉", reds)
    finally:
        fx.web_options = ""
        fx.cls = "TkTopLevel"
    return "(9) R2 的 name_trust 门槛", reds, ev


def check_since_version_reply(core, fx):
    """(10) 给了 since：since_echo 原样回传 + version_match 正确 + 带回**新** screen_version。"""
    reds = []
    ev = {}
    core._screen_forget(0)
    fx.tick = 0
    _, sc1 = call(core, top=500)
    v1 = sc1.get("screen_version")
    need(all(k not in sc1 for k in ("since_echo", "version_match", "screen_version_新值")),
         "没给 since 却凭空回了版本应答三件套", reds)

    # ── ① 屏没变：回传上次的版本 ⇒ 对得上；新签发的值与上次相同（这屏真没动）
    _, sc2 = call(core, top=500, since=v1)
    ev["①没变时"] = {"since_echo": sc2.get("since_echo"),
                     "version_match": sc2.get("version_match"),
                     "screen_version_新值": sc2.get("screen_version_新值")}
    need(sc2.get("since_echo") == v1,
         "since_echo 没原样回传上次版本：%r（应 %r）" % (sc2.get("since_echo"), v1), reds)
    need(sc2.get("version_match") is True,
         "回传真版本时 version_match 应为 True，实得 %r" % sc2.get("version_match"), reds)
    need(sc2.get("screen_version_新值") == sc2.get("screen_version") == v1,
         "屏没变时新签发的版本应与上次相同（%r），实得 %r" % (v1, sc2.get("screen_version_新值")), reds)

    # ── ② 屏变了：带回**新**版本号（防「盲扫准风暴」就靠这个凭据）
    fx.advance()
    _, sc3 = call(core, top=500, since=v1)
    v3 = sc3.get("screen_version")
    ev["②变了时"] = {"since_echo": sc3.get("since_echo"),
                     "version_match": sc3.get("version_match"),
                     "screen_version_新值": sc3.get("screen_version_新值")}
    need(sc3.get("since_echo") == v1, "since_echo 被改写了：%r" % sc3.get("since_echo"), reds)
    need(sc3.get("version_match") is True,
         "回传真版本时 version_match 应仍是 True，实得 %r" % sc3.get("version_match"), reds)
    need(v3 != v1,
         "内容变了 screen_version 却没变（那就没有「变没变」的凭据）", reds)
    need(sc3.get("screen_version_新值") == v3,
         "screen_version_新值 应等于本次签发的 screen_version=%r，实得 %r"
         % (v3, sc3.get("screen_version_新值")), reds)
    d3 = sc3.get("delta") or {}
    need(d3.get("since_echo") == v1 and d3.get("version_match") is True
         and d3.get("screen_version_新值") == v3,
         "delta 段里的版本应答与顶层不一致：%r" % {k: d3.get(k) for k in
                                             ("since_echo", "version_match", "screen_version_新值")}, reds)

    # ── ③ 瞎编的 since：照样原样回传，但对不上 ⇒ version_match 必须 False
    _, sc4 = call(core, top=500, since="sv1-我瞎编的版本")
    ev["③瞎编since"] = {"since_echo": sc4.get("since_echo"),
                      "version_match": sc4.get("version_match"),
                      "screen_version": sc4.get("screen_version")}
    need(sc4.get("since_echo") == "sv1-我瞎编的版本", "瞎编的 since 也要原样回传", reds)
    need(sc4.get("version_match") is False,
         "瞎编的 since 被当成本进程签发过的版本了（version_match 应为 False），实得 %r"
         % sc4.get("version_match"), reds)
    need(sc4.get("screen_version") != "sv1-我瞎编的版本",
         "签发值竟等于调用方传进来的字符串", reds)

    # ── ④ 明写「版本由服务端签发，客户端只能回传」
    ko = str(sc4.get("版本口径") or "")
    ev["版本口径"] = ko[:160]
    need("服务端签发" in ko and "回传" in ko,
         "返回里没明写「版本由服务端签发、客户端只能回传」：%r" % ko[:120], reds)
    fx.tick = 0
    return "(10) since 的版本应答", reds, ev

def check_watch_declared(core, fx):
    """S1 · 声明过的 volatile 节点改了 ⇒ 必须 TEXT_CHANGED（**声明压过一切豁免源**）。"""
    reds = []
    ev = {}
    core._screen_forget(0)
    fx.timer_tick = 0
    fx.quota = 3
    # 声明表：倒计时（真 volatile，R1 命中）/ T2 任务信息 / 一个**屏外**节点（验规则2）
    DECL = ["剩余 00:", "剩余可选", "屏外按钮07"]

    r1, sc1 = call(core, top=500, watch_text=DECL)
    if not need(r1.get("ok"), "带声明的调用没成功：%s" % r1.get("warning"), reds):
        return "S1 声明即不豁免", reds, ev
    objs = sc1.get("objects") or []
    tmr = by_label(objs, "剩余 0")
    quota = by_label(objs, "剩余可选")
    off = by_label(objs, "屏外按钮07")
    if not need(len(tmr) == 1 and len(quota) == 1 and len(off) == 1,
                "夹具里没找着计时器/T2/屏外按钮07（%d/%d/%d）"
                % (len(tmr), len(quota), len(off)), reds):
        return "S1 声明即不豁免", reds, ev
    tmr, quota, off = tmr[0], quota[0], off[0]
    ev["计时器(声明)"] = {k: tmr.get(k) for k in
                       ("label", "volatile", "watched", "exempted", "uid")}
    ev["T2任务信息(声明)"] = {k: quota.get(k) for k in
                       ("label", "volatile", "watched", "exempted")}

    # ── 规则1：声明即不豁免（这条是本组的命门）
    need(tmr.get("volatile") is True,
         "计时器按现役规则**本来就是 volatile**，这条断言是空的（夹具坏了）", reds)
    need(tmr.get("watched") is True, "声明命中的节点没标 watched", reds)
    need(tmr.get("exempted") is False,
         "声明了却仍被豁免（volatile=true ∧ watched=true ⇒ exempted 必须 false），实得 %r"
         % tmr.get("exempted"), reds)
    need(not str(tmr.get("uid") or "").endswith("@~"),
         "声明节点的 text_key 被压成了 ~（豁免没压过去）：%r" % tmr.get("uid"), reds)
    need(str(tmr.get("uid") or "").split("@")[-1].startswith("t"),
         "声明节点没有实算 text_key：%r" % tmr.get("uid"), reds)
    # T2：Z3 已删掉裸关键词「剩余」，它当前**不是** volatile ⇒ 声明对它验的是「钉住」这层价值
    ev["T2是否volatile(现役规则)"] = quota.get("volatile")
    need(quota.get("watched") is True, "T2 任务信息被声明了却没标 watched", reds)
    need(quota.get("exempted") is False, "T2 声明后不该处于豁免态", reds)

    # ── 规则2：声明压过视口过滤与 top 预算
    need(off.get("in_view") is False,
         "屏外按钮07 明明在视口外，in_view 却报了 true（这条断言就是空的）", reds)
    need(off.get("watched") is True and bool(off.get("uid")),
         "声明命中的**屏外**节点没被带回来（声明必须压过视口过滤）", reds)
    ev["屏外声明节点"] = {k: off.get(k) for k in ("label", "in_view", "watched")}
    # ★ 「声明必进压过 top 预算」这趟**挪到最后**再跑：快照是按 hwnd 共享的，
    #   中途插一趟「只声明屏外按钮」的调用，计时器在那趟是未声明状态（text_key 恒 ~），
    #   基线就被写脏了，后面那趟 TEXT_CHANGED 会对不上 —— 那是测试自造，不是实现的错。
    # ── 校验段：matched 逐条列明，unmatched/pending 空
    ck = sc1.get("watch_校验") or {}
    ev["watch_校验"] = ck
    need(ck.get("matched") == DECL, "watch_校验.matched=%r，应逐条等于声明 %r"
         % (ck.get("matched"), DECL), reds)
    need(not (ck.get("unmatched") or []), "matched 全中却报了 unmatched：%r" % ck.get("unmatched"), reds)
    need(not (ck.get("pending") or []), "没开 watch_pending 却报了 pending：%r" % ck.get("pending"), reds)
    need("watch_口径" in sc1, "返回里没把 watch_text 的口径讲出来", reds)

    # ── 规则1 的结果：推一格计时器 ⇒ 必报 TEXT_CHANGED，且进位**可归因到它**
    v1 = sc1.get("screen_version")
    old_uid = tmr.get("uid")
    fx.advance_timer_only()
    r3, sc3 = call(core, top=500, since=v1, watch=True, watch_text=DECL)
    if not need(r3.get("ok"), "第二次带声明的调用没成功：%s" % r3.get("warning"), reds):
        return "S1 声明即不豁免", reds, ev
    tmr2 = by_label(sc3.get("objects") or [], "剩余 0")
    if not need(len(tmr2) == 1, "推格后计时器不见了", reds):
        return "S1 声明即不豁免", reds, ev
    tmr2 = tmr2[0]
    d = sc3.get("delta") or {}
    st = d.get("uid_state_统计") or {}
    ev["推格后"] = {"label前后": [tmr.get("label"), tmr2.get("label")],
                "uid前后": [old_uid, tmr2.get("uid")],
                "uid_state": tmr2.get("uid_state"),
                "version前后": [v1, sc3.get("screen_version")],
                "消失": d.get("消失"), "新增": d.get("新增"), "uid_state_统计": st}
    need(tmr.get("label") != tmr2.get("label"), "计时器文本没变，这条断言就是空的", reds)
    need(tmr2.get("uid_state") == "TEXT_CHANGED",
         "声明过的节点改了字必须报 TEXT_CHANGED（实得 %r）—— 这就是「声明即不豁免」的全部意义"
         % tmr2.get("uid_state"), reds)
    need(old_uid != tmr2.get("uid"), "换了字 uid 却没变（声明没压过豁免）", reds)
    need(sc3.get("screen_version") != v1,
         "声明节点变了，screen_version 却没进位（豁免把它一起吞了）", reds)
    need(d.get("消失") == [old_uid],
         "delta.消失 应恰好记着这个声明节点的旧 uid，实得 %r" % (d.get("消失"),), reds)
    need(d.get("新增") == [tmr2.get("uid")],
         "delta.新增 应恰好记着新 uid，实得 %r" % (d.get("新增"),), reds)
    need(int(st.get("TEXT_CHANGED") or 0) == 1 and int(st.get("STALE") or 0) == 0,
         "进位应**恰好**归因到那一个声明节点（TEXT_CHANGED=1 且 STALE=0），实得 %r" % st, reds)

    # ── 规则2 后半：声明压过 top 预算（放在最后，别用不同的声明集污染共享基线）
    r2, sc2 = call(core, top=1, watch_text=["屏外按钮07"])
    got2 = by_label(sc2.get("objects") or [], "屏外按钮07")
    ev["top=1时的声明节点"] = "%d 条（returned=%d）" % (len(got2), len(sc2.get("objects") or []))
    need(len(got2) == 1,
         "top=1 时声明节点被 top 预算裁掉了（声明必进与必进保证同级）", reds)
    fx.timer_tick = 0
    fx.quota = 3
    return "S1 声明即不豁免", reds, ev


def check_watch_undeclared(core, fx):
    """S2 · **未声明**的倒计时改了 ⇒ uid 恒、version 不因它进位（豁免没失守 = 风暴没回流）。"""
    reds = []
    ev = {}
    core._screen_forget(0)
    fx.timer_tick = 0

    _, sc1 = call(core, top=500)
    t1 = by_label(sc1.get("objects") or [], "剩余 0")
    if not need(len(t1) == 1, "夹具里没找着计时器（%d 个）" % len(t1), reds):
        return "S2 未声明不进位", reds, ev
    t1 = t1[0]
    v1 = sc1.get("screen_version")
    ev["第一扫"] = {k: t1.get(k) for k in ("label", "volatile", "watched", "exempted", "uid")}
    need(t1.get("volatile") is True, "计时器按现役规则就该是 volatile（夹具坏了）", reds)
    need(not t1.get("watched"), "没声明却标了 watched", reds)
    need(t1.get("exempted") is True,
         "未声明的 volatile 节点应处于豁免态（exempted=true），实得 %r" % t1.get("exempted"), reds)
    need(str(t1.get("uid") or "").endswith("@~"),
         "未声明的 volatile 节点 text_key 应恒为 ~，实得 %r" % t1.get("uid"), reds)
    need("watch_校验" not in sc1, "没给 watch_text 却回了 watch_校验 段", reds)

    # 只推计时器一格：别的什么都不动
    fx.advance_timer_only()
    _, sc2 = call(core, top=500, since=v1, watch=True)
    t2 = by_label(sc2.get("objects") or [], "剩余 0")
    if not need(len(t2) == 1, "第二扫后计时器不见了", reds):
        return "S2 未声明不进位", reds, ev
    t2 = t2[0]
    d = sc2.get("delta") or {}
    st = d.get("uid_state_统计") or {}
    ev["第二扫"] = {"label前后": [t1.get("label"), t2.get("label")],
                "uid前后": [t1.get("uid"), t2.get("uid")],
                "uid_state": t2.get("uid_state"),
                "version前后": [v1, sc2.get("screen_version")],
                "新增数": d.get("新增数"), "消失数": d.get("消失数"),
                "uid_state_统计": st}
    need(t1.get("label") != t2.get("label"), "计时器文本没变，这条断言就是空的", reds)
    need(t2.get("uid") == t1.get("uid"),
         "未声明的 volatile 节点换了字 uid 就变了（豁免失守 ⇒ STALE 风暴回流）", reds)
    need(t2.get("uid_state") == "OK",
         "未声明的 volatile 节点换字不该报 TEXT_CHANGED，实得 %r" % t2.get("uid_state"), reds)
    need(sc2.get("screen_version") == v1,
         "只动了倒计时，screen_version 却进位了（version=%r ≠ %r）—— 风暴回流的凭据"
         % (sc2.get("screen_version"), v1), reds)
    need(t1.get("uid") not in (d.get("新增") or []) and
         t1.get("uid") not in (d.get("消失") or []),
         "未声明的倒计时被算进了 delta（它没变，不该进）", reds)
    need(int(st.get("TEXT_CHANGED") or 0) == 0 and int(st.get("STALE") or 0) == 0,
         "未声明节点不该产生任何 TEXT_CHANGED / STALE，实得 %r" % st, reds)
    need("watch_校验" not in sc2, "没给 watch_text 却回了 watch_校验 段", reds)
    fx.timer_tick = 0
    return "S2 未声明不进位", reds, ev


def check_watch_unmatched(core, fx):
    """S3 · 声明一个**不存在**的串 ⇒ 整个调用拒绝（rc≠0）且 unmatched 逐条列明；挂起须显式。"""
    reds = []
    ev = {}
    core._screen_forget(0)
    fx.timer_tick = 0

    def 校验(r):
        return ((r.get("data") or {}).get("watch_校验") or {})

    # ── 一条都没命中 ⇒ 拒绝
    r1, d1 = call(core, top=500, watch_text=["不存在串xyz"])
    ck1 = 校验(r1)
    ev["①不存在"] = {"ok": r1.get("ok"), "code": r1.get("code"),
                  "warning": (r1.get("warning") or "")[:120], "watch_校验": ck1}
    need(r1.get("ok") is False, "声明了一个不存在的串，调用竟返回 ok=True（静默吞声明）", reds)
    need(r1.get("code") not in (0, None), "拒绝时 code 竟为 0/None：%r" % r1.get("code"), reds)
    need(ck1.get("unmatched") == ["不存在串xyz"],
         "unmatched 应逐条列明那一条声明，实得 %r" % ck1.get("unmatched"), reds)
    need(not d1, "拒绝时不该还把这一屏的 objects 回给调用方（否则会被当成声明已兑现）", reds)
    need(any("watch_pending" in str(b) for b in (r1.get("blind_spots") or [])),
         "拒绝时 blind_spots 没告诉调用方「可以 watch_pending=true 挂起」", reds)

    # ── 一条命中 + 一条没命中 ⇒ **仍然拒绝**，且 unmatched 只列没命中的那条
    r2, d2 = call(core, top=500, watch_text=["剩余 00:", "不存在串xyz"])
    ck2 = 校验(r2)
    ev["②半命中"] = {"ok": r2.get("ok"), "watch_校验": ck2}
    need(r2.get("ok") is False, "部分声明没命中却照样返回 ok=True（半截静默）", reds)
    need(ck2.get("unmatched") == ["不存在串xyz"],
         "半命中时 unmatched 应只列没命中的那条，实得 %r" % ck2.get("unmatched"), reds)

    # ── watch_pending=true ⇒ 放行，挂起的逐条标 pending
    r3, d3 = call(core, top=500, watch_text=["剩余 00:", "不存在串xyz"], watch_pending=True)
    ck3 = 校验(r3)
    ev["③挂起"] = {"ok": r3.get("ok"), "watch_校验": ck3}
    need(r3.get("ok") is True,
         "watch_pending=true 时不该拒绝（显式挂起是允许的）：%s" % (r3.get("warning") or "")[:120], reds)
    need(ck3.get("pending") == ["不存在串xyz"],
         "挂起的那条应逐条标进 pending，实得 %r" % ck3.get("pending"), reds)
    need(not (ck3.get("unmatched") or []),
         "挂了 pending 就不该再出现在 unmatched 里（两处都列=重复报），实得 %r" % ck3.get("unmatched"), reds)
    need(ck3.get("matched") == ["剩余 00:"], "matched 应只含真命中的那条，实得 %r" % ck3.get("matched"), reds)

    # ── 规则4：条数上限 32（33 条拒绝；恰好 32 条只因 unmatched 被拒，不是被上限拒）
    many = ["串%02d" % i for i in range(33)]
    r4, _ = call(core, top=500, watch_text=many)
    ev["④33条"] = {"ok": r4.get("ok"), "warning": (r4.get("warning") or "")[:120]}
    need(r4.get("ok") is False, "33 条声明竟被放行（上限没生效）", reds)
    need("32" in str(r4.get("warning") or ""),
         "超限的报错没点明上限 32：%r" % (r4.get("warning") or "")[:120], reds)
    need("不匹配" not in str(r4.get("warning") or "") and
         "没匹配到" not in str(r4.get("warning") or ""),
         "超限应报「条数上限」，不该混成「匹配不到」的报错", reds)
    r5, _ = call(core, top=500, watch_text=many[:32], watch_pending=True)
    ev["⑤整32条(挂起)"] = {"ok": r5.get("ok"), "pending条数": len((校验(r5).get("pending") or []))}
    need(r5.get("ok") is True,
         "恰好 32 条被拒了（上限应是 32 而不是 31）", reds)
    need(len(校验(r5).get("pending") or []) == 32,
         "32 条都挂起时 pending 应有 32 条，实得 %d" % len(校验(r5).get("pending") or []), reds)

    # ── 形态守卫：空声明 / 非数组都明确拒
    r6, _ = call(core, top=500, watch_text=["剩余 00:", "   "])
    ev["⑥空声明"] = (r6.get("warning") or "")[:120]
    need(r6.get("ok") is False and "空声明" in str(r6.get("warning") or ""),
         "空声明没被明确拒绝（空串会匹配一屏所有节点）", reds)
    r7, _ = call(core, top=500, watch_text="剩余 00:")
    ev["⑦非数组"] = (r7.get("warning") or "")[:120]
    need(r7.get("ok") is False and "数组" in str(r7.get("warning") or ""),
         "watch_text 传成字符串没被拒（契约要求数组）", reds)

    # ── 对照：认得的声明照常通过（别把规则做成「一声明就拒」）
    r8, d8 = call(core, top=500, watch_text=["剩余 00:"])
    ev["⑧认得的声明"] = {"ok": r8.get("ok"), "watch_校验": 校验(r8)}
    need(r8.get("ok") is True and 校验(r8).get("matched") == ["剩余 00:"],
         "认得的声明没能正常通过", reds)
    return "S3 未命中必拒", reds, ev


def check_watch_shadow_allhit(core, fx):
    """S4 · 全收记账（窗E1 · 修 Z4 F1）：两条重叠声明都 matched、unmatched 空、
    调用不被拒 —— 「剩余可选」与「第 7/20 步」都是 T2 同一节点的真子串，
    旧版只记第一条时第二条被判 unmatched ⇒ 整调用误拒（Z4 P2a 的病灶）。"""
    reds = []
    ev = {}
    core._screen_forget(0)
    fx.timer_tick = 0
    fx.quota = 3
    DECL = ["剩余可选", "第 7/20 步"]

    r1, d1 = call(core, top=500, watch_text=DECL)
    ck = (r1.get("data") or {}).get("watch_校验") or {}
    ev["watch_校验"] = ck
    need(r1.get("ok") is True,
         "两条重叠声明（命中同一节点）竟被拒：%s" % (r1.get("warning") or "")[:160], reds)
    need(ck.get("matched") == DECL,
         "两条重叠声明应**都**进 matched，实得 %r" % (ck.get("matched"),), reds)
    need(not (ck.get("unmatched") or []),
         "全收记账下 unmatched 应为空，实得 %r" % (ck.get("unmatched"),), reds)
    need(not (ck.get("pending") or []),
         "没开挂起却报了 pending：%r" % (ck.get("pending"),), reds)
    hit = ck.get("命中节点数") or {}
    need(int(hit.get("剩余可选") or 0) >= 1 and int(hit.get("第 7/20 步") or 0) >= 1,
         "两条声明的命中节点数都应 ≥1（互蔽=第二条 0），实得 %r" % hit, reds)
    t2 = by_label(d1.get("objects") or [], "剩余可选")
    need(len(t2) == 1 and t2[0].get("watched") is True,
         "T2 没被标 watched（命中 %d 个）" % len(t2), reds)
    need("全收" in str(d1.get("watch_口径") or ""),
         "watch_口径 没把全收记账讲出来", reds)

    # ── 半重叠对照：一条全中 + 一条真不存在 ⇒ 仍拒（全收 ≠ 放水）
    r2, _ = call(core, top=500, watch_text=["剩余可选", "不存在串xyz"])
    need(r2.get("ok") is False,
         "真不存在的声明没被拒（全收被做成了「有命中就全放」）", reds)
    core._screen_forget(0)
    fx.timer_tick = 0
    fx.quota = 3
    return "S4 重叠声明全收", reds, ev


def check_watch_width(core, fx):
    """S5 · 单条声明宽度上限（窗E1 · 修 Z4 F2）：命中 9 ⇒ 拒且点名串与命中数；
    恰好命中 8（=WATCH_TEXT_WIDTH_MAX）⇒ 放行；条数上限 32 仍生效；
    watch_pending 救不了超宽（它现在就命中了，不存在「等它出现」）。"""
    reds = []
    ev = {}
    core._screen_forget(0)
    fx.timer_tick = 0
    fx.quota = 3

    # ── 宽度 9：「选项格0」⊆ 选项格01..09（N_BTN=38 夹具）⇒ 超宽必拒且点名
    r1, _ = call(core, top=500, watch_text=["选项格0"])
    w1 = str(r1.get("warning") or "")
    ev["宽度9"] = {"ok": r1.get("ok"), "code": r1.get("code"), "warning": w1[:160],
                 "watch_校验": (r1.get("data") or {}).get("watch_校验")}
    need(r1.get("ok") is False, "命中 9 个节点的声明（>上限 8）没被拒", reds)
    need("选项格0" in w1 and "9" in w1,
         "宽度拒绝没点名声明串与命中数：%s" % w1[:160], reds)
    need(r1.get("code") not in (0, None), "宽度拒绝时 code 竟为 0/None：%r" % r1.get("code"), reds)
    r1b, _ = call(core, top=500, watch_text=["选项格0"], watch_pending=True)
    need(r1b.get("ok") is False,
         "watch_pending=true 竟救回了超宽声明（它现在就命中，不存在「等它出现」）", reds)

    # ── 宽度 8：「备注段」⊆ 备注段01..08（N_TEXT=8 夹具）⇒ 恰好在上限上 ⇒ 放行
    r2, d2 = call(core, top=500, watch_text=["备注段"])
    ck2 = (r2.get("data") or {}).get("watch_校验") or {}
    ev["宽度8"] = {"ok": r2.get("ok"), "命中节点数": ck2.get("命中节点数")}
    need(r2.get("ok") is True,
         "恰好命中 %d 个（=上限）的声明被拒了（边界 off-by-one）：%s"
         % (N_TEXT, (r2.get("warning") or "")[:160]), reds)
    need(int((ck2.get("命中节点数") or {}).get("备注段") or 0) == N_TEXT,
         "边界放行的那条命中数应恰为 %d，实得 %r" % (N_TEXT, ck2.get("命中节点数")), reds)

    # ── 条数上限 32 仍生效（新规不挤掉老规）
    many = ["串%02d" % i for i in range(33)]
    r3, _ = call(core, top=500, watch_text=many)
    need(r3.get("ok") is False and "32" in str(r3.get("warning") or ""),
         "33 条声明没被条数上限拒：%s" % (r3.get("warning") or "")[:120], reds)
    r4, _ = call(core, top=500, watch_text=many[:32], watch_pending=True)
    need(r4.get("ok") is True, "恰好 32 条挂起被拒了（条数上限应是 32 不是 31）", reds)
    core._screen_forget(0)
    fx.timer_tick = 0
    fx.quota = 3
    return "S5 声明宽度上限", reds, ev


def check_watch_reject_ctx(core, fx):
    """S6 · 拒绝信封带屏上下文（窗E1 · 修 Z4 F3）：screen_version / window /
    对象总数 / blind_spots 四样齐全，且明写「这是校验失败，不是屏幕空」；
    仍不回 objects。宽度拒绝的信封同款。"""
    reds = []
    ev = {}
    core._screen_forget(0)
    fx.timer_tick = 0
    fx.quota = 3

    # ── unmatched 拒绝的信封
    r1, d1 = call(core, top=500, watch_text=["不存在串xyz"])
    data = r1.get("data") or {}
    ev["unmatched拒绝信封"] = {"ok": r1.get("ok"), "code": r1.get("code"),
                       "screen_version": data.get("screen_version"),
                       "window": data.get("window"),
                       "对象总数": data.get("对象总数"),
                       "blind_spots": (r1.get("blind_spots") or [])[:3]}
    need(r1.get("ok") is False, "unmatched 该拒没拒", reds)
    need(bool(data.get("screen_version")),
         "拒绝信封缺 screen_version（agent 分不清是哪一屏的账）", reds)
    win = data.get("window") or {}
    need(isinstance(win, dict) and win.get("hwnd") is not None and win.get("title"),
         "拒绝信封缺 window{hwnd,title}（agent 分不清是不是拿错了窗口）：%r" % win, reds)
    total = data.get("对象总数")
    need(isinstance(total, int) and total > 0,
         "拒绝信封缺 对象总数（agent 分不清白屏与声明写错）：%r" % total, reds)
    bs = " ".join(str(b) for b in (r1.get("blind_spots") or []))
    need("校验失败" in bs and "屏幕空" in bs,
         "blind_spots 没明写「这是校验失败，不是屏幕空」：%s" % bs[:200], reds)
    need(not d1, "拒绝时仍回了 objects（会被当成声明已兑现）", reds)

    # ── 宽度拒绝的信封同款。★ 分层：宽度上限**会不会生效**归 S5 管（拆了它 S5 红）；
    #   本组只管「拒的时候信封要全」—— 所以宽度调用若被放行（如 --inject watch_width），
    #   这里如实记账不越权红，否则一条缺陷带红两组、判别力反而糊。
    r2, d2 = call(core, top=500, watch_text=["选项格0"])
    data2 = r2.get("data") or {}
    ev["宽度拒绝信封"] = {"ok": r2.get("ok"),
                      "screen_version": bool(data2.get("screen_version")),
                      "对象总数": data2.get("对象总数")}
    if r2.get("ok") is False:
        need(bool(data2.get("screen_version"))
             and isinstance(data2.get("对象总数"), int) and data2.get("对象总数") > 0,
             "宽度拒绝的信封没带屏上下文", reds)
        bs2 = " ".join(str(b) for b in (r2.get("blind_spots") or []))
        need("校验失败" in bs2 and "屏幕空" in bs2,
             "宽度拒绝的 blind_spots 没明写「校验失败≠屏幕空」：%s" % bs2[:200], reds)
        need(not d2, "宽度拒绝时仍回了 objects", reds)
    else:
        ev["宽度拒绝信封"]["说明"] = "宽度上限未生效（归 S5 管），信封形状无从验"
    core._screen_forget(0)
    fx.timer_tick = 0
    fx.quota = 3
    return "S6 拒绝信封带屏上下文", reds, ev

DOM_ONLY_QUERY = '只在DOM里出现的那个词'
OCR_ONLY_QUERY = '只画在像素里的那个词'


def check_action_side(core, fx):
    '''S10 · 动作侧：eid 能直接点、expect_uid 严守、动作后 after 归因、滚出视口仍可点。

    ★ 旁表**不 mock**：ctl_screen 真走 walk、真签 capture_id、真落旁表，
      ctl_click 真按旁表 + 身份复核走一遍（D1 那条链原封不动）。
    ★ 只换两个 I/O 边界：`_act`（它会去拉 act.py 子进程；判据里换成假点击器，
      假点击器**会按脚本真改夹具**，好让 after 归因有东西可归）与 `uia_tree.window_state`。
    '''
    reds = []
    ev = {}

    if "_act" not in ORIG:
        ORIG["_act"] = core.__dict__.get("_act")
        ORIG["_win_rect"] = core.__dict__.get("_win_rect")
        ORIG["_is_foreground"] = core.__dict__.get("_is_foreground")
        ORIG["_window_state"] = getattr(core.uia_tree, "window_state", None)

    点过 = []

    def 假点击(action, args, argv, scope="B"):
        """假点击器：记一笔，并**按脚本改夹具**（点的就是这个 eid 的那个按钮）。"""
        点过.append({"action": action, "xy": list(args.get("xy") or [])})
        if fx.g3_click_effect == "advance":
            fx.advance()          # 计时器与状态行同时换字 ⇒ 文字型变化
        elif fx.g3_click_effect == "insert":
            fx.n_extra_btn = int(fx.n_extra_btn or 0) + 1   # 结构变了 ⇒ STALE
        return {"ok": True, "code": 0, "data": {"假点击": True}, "warning": None,
                "blind_spots": [], "event_id": None, "elapsed_ms": 1,
                "session_id": "", "t_mono": 0.0, "t_wall": "", "notices": []}

    core._act = 假点击
    core._win_rect = lambda h: [0, 0, 1280, 900]
    core._is_foreground = lambda h: True
    core.uia_tree.window_state = lambda h: {"最小化": False, "根矩形宽": 1280,
        "根矩形高": 900, "说明": "假窗口状态"}
    # ★ 窗G3 · ④「滚出视口」要造得**真**：把客户区整体往下挪（等价于向下滚），
    #   **节点矩形不动**（文档坐标不动）⇒ 那几个按钮真的落到客户区之外、
    #   in_view=False，但它们**仍在树里**。这正是「签发在视口内、点之前滚了一下」。
    #   ⚠ 只挪视口、不改节点矩形：改矩形会被认成「节点变了」，那是另一回事。
    def _假客户区(handle):
        return [CLIENT[0], CLIENT[1] + int(fx.scroll_y or 0),
                CLIENT[2], CLIENT[3] + int(fx.scroll_y or 0)]
    core.uia_tree._client_rect = _假客户区

    def 重扫(**kw):
        core._screen_forget(0)
        return call(core, top=500, **kw)

    def 找对象(sc, needle):
        for o in (sc.get("objects") or []):
            if str(o.get("label") or "").startswith(needle):
                return o
        return {}

    # ── ① ctl_screen 的 eid 能直接喂 ctl_click，身份复核通过
    fx.g3_click_effect = "none"
    fx.n_extra_btn = 0
    fx.timer_tick = 0
    _, sc1 = 重扫()
    o1 = 找对象(sc1, "选项格01")
    ev["①对象字段"] = {k: o1.get(k) for k in ("id", "eid", "uid", "label")}
    need(bool(o1.get("eid")), "对象没有 eid（%r）—— 这一单就是让它能直接点" % o1.get("eid"), reds)
    need(bool(o1.get("uid")), "对象没有 uid（uid 与 eid 并存，不许互相顶替）", reds)
    need(o1.get("id") != o1.get("eid"),
         "id 与 eid 撞成同一个（%r）—— id 是本次屏内的对象号，eid 是旁表钥匙" % o1.get("id"), reds)
    tok1 = sc1.get("capture_id")
    need(bool(tok1), "这一屏没有签发 capture_id，eid 就是无根之木", reds)
    r1 = core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": o1.get("eid"),
                                 "from_capture": tok1, "actor": "判据G3"}, actor="判据G3")
    ev["①点它"] = {"ok": r1.get("ok"), "code": r1.get("code"),
                   "warning": (r1.get("warning") or "")[:200], "真的点了": len(点过)}
    need(r1.get("ok") is True, "拿 ctl_screen 的 eid 直接点失败：%s" % (r1.get("warning") or "")[:200], reds)
    need(len(点过) == 1,
         "点击没真发出去（点过 %d 次）—— 这条证明 eid→旁表→复核→坐标整条链是通的" % len(点过), reds)

    # ── ② expect_uid：匹配能点；不匹配**拒且不点**
    _, sc2 = 重扫()
    o2 = 找对象(sc2, "选项格02")
    tok2 = sc2.get("capture_id")
    n0 = len(点过)
    r2ok = core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": o2.get("eid"),
                                   "from_capture": tok2, "expect_uid": o2.get("uid"),
                                   "actor": "判据G3"}, actor="判据G3")
    v2 = ((r2ok.get("data") or {}).get("expect_uid_校验") or {})
    ev["②匹配"] = {"ok": r2ok.get("ok"), "expect_uid_校验": v2, "点了": len(点过) - n0}
    need(r2ok.get("ok") is True and v2.get("判定") == "匹配",
         "uid 对得上却没点成：ok=%r 判定=%r" % (r2ok.get("ok"), v2.get("判定")), reds)
    need(len(点过) - n0 == 1, "uid 匹配那次没真点（点了 %d 次）" % (len(点过) - n0), reds)

    n1 = len(点过)
    r2no = core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": o2.get("eid"),
                                   "from_capture": tok2, "expect_uid": "完全不是这个对象的uid",
                                   "actor": "判据G3"}, actor="判据G3")
    v2n = ((r2no.get("data") or {}).get("expect_uid_校验") or {})
    ev["②不匹配"] = {"ok": r2no.get("ok"), "code": r2no.get("code"), "expect_uid_校验": v2n,
                    "点了": len(点过) - n1, "warning": (r2no.get("warning") or "")[:200]}
    need(r2no.get("ok") is False, "uid 对不上却照样点了（ok=%r）—— 那正是本单要治的" % r2no.get("ok"), reds)
    need(r2no.get("code") not in (0, None), "拒了却没给非 0 码：%r" % r2no.get("code"), reds)
    need(v2n.get("判定") == "不匹配", "三值判定没报「不匹配」：%r" % v2n, reds)
    need(len(点过) == n1, "拒了却还是点了（点了 %d 次）—— 必须一个像素都没动" % (len(点过) - n1), reds)
    need("没点" in str(r2no.get("warning") or "") or "STALE" in str(r2no.get("warning") or ""),
         "拒绝文案没把「没点」/STALE_SCREEN 说清：%s" % (r2no.get("warning") or "")[:160], reds)

    # ── ③ 动作后 after.uid_state 归因：文字变 ⇒ TEXT_CHANGED，结构变 ⇒ STALE
    #   ★ 两组点的**不是同一个对象**，因为要打的靶子本来就不同：
    #     advance（点计时器）：计时器是 volatile 节点，它的 text_key 恒为 ~ 
    #       （改了字 uid 也不变，这是 §⑫ 豁免的设计）⇒ 只能靠「同一结构位置上的
    #       东西换了字」来归因 —— 正好证明 after 不是只比 uid。
    #     insert（在按钮队列最前面插一个）：后面每个按钮的 struct_key 整体后移
    #       ⇒ 同一个按钮换了个路径 ⇒ STALE（可能点错对象）。
    for effect, want_state, target in (("advance", "TEXT_CHANGED", "剩余 00"),
                                       ("insert", "STALE", "选项格01")):
        fx.g3_click_effect = effect
        fx.n_extra_btn = 0
        fx.timer_tick = 0
        fx.tick = 0
        _, sc3 = 重扫()
        o3 = 找对象(sc3, target)
        need(bool(o3.get("eid")),
             "③ %s：靶场里找不到『%s』这组要点的对象" % (effect, target), reds)
        tok3 = sc3.get("capture_id")
        r3 = core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": o3.get("eid"),
                                   "from_capture": tok3, "expect_uid": o3.get("uid"),
                                   "actor": "判据G3"}, actor="判据G3")
        af = (r3.get("data") or {}).get("after") or {}
        ev["③after_%s" % effect] = {"want": want_state, "got": af.get("uid_state"),
                                "screen_version": af.get("screen_version"),
                                "依据": (af.get("依据") or "")[:160]}
        need(r3.get("ok") is True,
             "③ %s 那次点击没成功：%s" % (effect, (r3.get("warning") or "")[:160]), reds)
        need(af.get("uid_state") == want_state,
             "③ %s：点完 uid_state 应是 %s，实得 %r（依据：%s）"
             % (effect, want_state, af.get("uid_state"), (af.get("依据") or "")[:120]), reds)
        need(bool(af.get("screen_version")), "③ %s：after 没给 screen_version" % effect, reds)
        if af.get("uid_state") == "unknown":
            need("不等于动作成功" in str(af.get("口径") or ""),
                 "uid_state=unknown 时口径里必须写明「不等于动作成功」", reds)

    # ── ④ 滚出视口的对象仍可点：复核必须取**超集**（A2 要求）
    fx.g3_click_effect = "none"
    fx.n_extra_btn = 0
    fx.scroll(0)
    _, sc4 = 重扫()
    o4 = 找对象(sc4, "选项格01")
    tok4 = sc4.get("capture_id")
    fid, at = fx.scroll(600)      # ★ 真的把那个按钮滚出视口（客户区整体下移 600px）
    need(o4.get("label") == "选项格01", "④ 靶场里没有『选项格01』", reds)
    r4 = core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": o4.get("eid"),
                                 "from_capture": tok4, "expect_uid": o4.get("uid"),
                                 "actor": "判据G3"}, actor="判据G3")
    v4 = ((r4.get("data") or {}).get("expect_uid_校验") or {})
    ev["④滚出视口"] = {"滚了": fid, "scroll_y": at, "ok": r4.get("ok"),
                   "expect_uid_校验": v4.get("判定"),
                   "warning": (r4.get("warning") or "")[:200]}
    need(r4.get("ok") is True,
         "元素滚出视口后点不了（%s）—— 复核那趟必须显式 include_offscreen=True 取超集，"
         "「签发在视口内、点之前滚一下」不该被误报成「元素不见了」"
         % (r4.get("warning") or "")[:160], reds)
    need(v4.get("判定") == "匹配",
         "滚出视口后 uid 竟然对不上了（%r）—— 复核取超集时结构键口径应当一致" % v4, reds)
    fx.scroll(0)

    # ── ⑤ 真门路（窗G3b）：capture 必须**带元素明细**。
    #   ★ 关键：**取外层 data.capture_id**，不是 data.结果.capture_id。
    #     上一单判据取的是后者（call() 返回 结果）⇒ 拿到的是 ctl_screen 自己签的那张，
    #     于是真门路上 _uia_gate 拿 res 重签的空快照把明细盖掉了，判据却全绿
    #     （DSH 2026-10-18 真 Edge 实跑逮到：code=12「这次观测没有元素明细」）。
    #   这里走**真的 t_screen → 真信封**，用的就是调用方会拿到的那个 capture_id。
    fx.g3_click_effect = "none"
    fx.n_extra_btn = 0
    r5s = core.dispatch("ctl_screen", {"hwnd": FAKE_HWND, "top": 500,
                                   "cdp": False, "ocr": False}, actor="判据G3b")
    d5 = r5s.get("data") or {}
    sc5 = d5.get("结果") or {}
    tok5 = d5.get("capture_id")                 # ★ 外层：真调用方拿的就是这个
    o5 = 找对象(sc5, "选项格01")
    ev["⑤真门路capture"] = {"外层capture_id": tok5, "内层capture_id": sc5.get("capture_id"),
                        "capture_指纹": d5.get("capture_指纹"),
                        "外层==内层": tok5 == sc5.get("capture_id")}
    need(bool(tok5), "ctl_screen 没在外层给 capture_id", reds)
    need(tok5 == sc5.get("capture_id"),
         "外层 capture_id（%r）与内层（%r）不是同一张 —— 调用方拿的是外层，"
         "两张并存就意味着其中一张的明细根本没生效" % (tok5, sc5.get("capture_id")), reds)
    need("未采集节点" not in str(d5.get("capture_指纹") or ""),
         "capture_指纹 明写「本次未采集节点」（%r）—— ctl_screen 做的是全量 walk，"
         "它的节点明细是顺带的，不该说没采集" % (d5.get("capture_指纹") or ""), reds)
    n5 = len(点过)
    r5 = core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": o5.get("eid"),
                                 "from_capture": tok5, "actor": "判据G3b"}, actor="判据G3b")
    ev["⑤真门路点它"] = {"ok": r5.get("ok"), "code": r5.get("code"),
                     "点了": len(点过) - n5, "warning": (r5.get("warning") or "")[:220]}
    need(r5.get("ok") is True,
         "拿**外层** capture_id 喂 ctl_click 被拒（code=%s）：%s —— "
         "ctl_screen 的 capture 没带元素明细，旁表查不到 eid"
         % (r5.get("code"), (r5.get("warning") or "")[:180]), reds)
    need(len(点过) - n5 == 1, "真门路那次没真点（点了 %d 次）" % (len(点过) - n5), reds)

    # ── ⑥ 无明细时要 code=12，且**文案点名来源**（不能只说 ctl_tree 轻量）。
    #   造法走**真链路**：ctl_tree 默认轻量（不传 detail）天然就是空快照那张。
    #   这不是 mock —— 是真的调 ctl_tree，真的点一个文本树里的 eid。
    r6t = core.dispatch("ctl_tree", {"hwnd": FAKE_HWND}, actor="判据G3b")
    tok6 = (r6t.get("data") or {}).get("capture_id")
    r6 = core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": "e1",
                                 "from_capture": tok6, "actor": "判据G3b"}, actor="判据G3b")
    w6 = str(r6.get("warning") or "")
    ev["⑥无明细文案"] = {"ok": r6.get("ok"), "code": r6.get("code"), "warning": w6[:260]}
    need(r6.get("code") == 12,
         "空快照那次没给 code=12（给了 %r）" % r6.get("code"), reds)
    need("没有元素明细" in w6, "文案没明说「没有元素明细」：%s" % w6[:160], reds)
    need("ctl_tree" in w6,
         "文案没点名这次观测来自 ctl_tree（%r）—— 空口说「轻量模式」，"
         "换个工具来的调用方会照着去调一个自己压根没用过的开关" % w6[:160], reds)
    need("ctl_find" in w6 and "detail" in w6,
         "文案没给可执行出路（要能看出「改用 ctl_find」或「带 detail」）：%s" % w6[:200], reds)

    # ── ⑦ 老路回归（不许被这次改动带坏）：ctl_find 的 eid 直点。
    r7f = core.dispatch("ctl_find", {"hwnd": FAKE_HWND, "query": "选项格02"},
                     actor="判据G3b")
    #   ⚠ ctl_find 的「命中」也在 data.结果 里（_uia_gate 统一套一层），别取错层。
    hit7 = ((r7f.get("data") or {}).get("结果") or {}).get("命中") or []
    tok7 = (r7f.get("data") or {}).get("capture_id")
    n7 = len(点过)
    r7 = core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": (hit7[0] or {}).get("eid"),
                                 "from_capture": tok7, "actor": "判据G3b"}, actor="判据G3b")
    ev["⑦ctl_find老路"] = {"命中": len(hit7), "ok": r7.get("ok"), "点了": len(点过) - n7,
                       "warning": (r7.get("warning") or "")[:160]}
    need(r7.get("ok") is True,
         "ctl_find 老路的 eid 直点坏了（%s）—— 这一单不许动它的行为"
         % (r7.get("warning") or "")[:160], reds)

    # ── ⑧ 动作后回读**不许吃掉调用方的 since 基线**（窗G3c）。
    #   造法走**完整真链路**：ctl_screen → ctl_click（改字）→ ctl_screen(since=第一屏的版本)。
    #   ★ 要点：after 回读自己也扫过一屏；以前那次扫把基线推进了，于是第三次调用
    #     比的是「点完之后」那版 ⇒ 明明变了字的对象报 OK、delta 空、version_match 假。
    fx.g3_click_effect = "none"
    fx.n_extra_btn = 0
    fx.timer_tick = 0
    fx.tick = 0
    core._screen_forget(0)
    r8a = core.dispatch("ctl_screen", {"hwnd": FAKE_HWND, "top": 500,
                                   "cdp": False, "ocr": False}, actor="判据G3c")
    sc8a = (r8a.get("data") or {}).get("结果") or {}
    v8 = sc8a.get("screen_version")
    o8 = 找对象(sc8a, "当前状态")
    tok8 = (r8a.get("data") or {}).get("capture_id")
    need(bool(v8) and bool(o8.get("eid")),
         "⑧ 前置：没拿到第一屏的版本/对象（%r / %r）" % (v8, o8.get("label")), reds)
    fx.g3_click_effect = "advance"          # ★ 点一下就让状态行换字
    core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": o8.get("eid"),
                             "from_capture": tok8,
                             "actor": "判据G3c"}, actor="判据G3c")
    r8b = core.dispatch("ctl_screen", {"hwnd": FAKE_HWND, "top": 500, "since": v8,
                                   "cdp": False, "ocr": False}, actor="判据G3c")
    sc8 = (r8b.get("data") or {}).get("结果") or {}
    st8 = 找对象(sc8, "当前状态")
    ev["⑧since基线"] = {"第一屏version": v8,
                     "version_match": sc8.get("version_match"),
                     "被点对象label": st8.get("label"),
                     "被点对象uid_state": st8.get("uid_state"),
                     "delta新增数": (sc8.get("delta") or {}).get("新增数"),
                     "delta消失数": (sc8.get("delta") or {}).get("消失数")}
    need(sc8.get("version_match") is True,
         "点击后再带 since 调 ctl_screen，version_match=%r —— 调用方回传的正是"
         "它自己刚见过的版本，却说不认得：动作内部的回读把基线顶掉了"
         % sc8.get("version_match"), reds)
    need(st8.get("uid_state") == "TEXT_CHANGED",
         "被点对象『%s』点完换了字，带 since 再看却报 uid_state=%r（应 TEXT_CHANGED）"
         "—— after 回读把「点前那版」吃掉了，调用方再也看不见自己动作造成的变化"
         % (st8.get("label"), st8.get("uid_state")), reds)
    need((sc8.get("delta") or {}).get("新增数") or (sc8.get("delta") or {}).get("消失数"),
         "delta 新增/消失全是 0（%r）—— 状态行换了字，uid 变了，本该在差分里露面"
         % (sc8.get("delta") or {}), reds)

    # ── ⑨ click.after.changed_objects：顺带省调用方一次 ctl_screen。
    fx.g3_click_effect = "none"
    fx.timer_tick = 0
    fx.tick = 0
    core._screen_forget(0)
    r9a = core.dispatch("ctl_screen", {"hwnd": FAKE_HWND, "top": 500,
                                   "cdp": False, "ocr": False}, actor="判据G3c")
    sc9a = (r9a.get("data") or {}).get("结果") or {}
    o9 = 找对象(sc9a, "当前状态")
    fx.g3_click_effect = "advance"
    r9 = core.dispatch("ctl_click", {"hwnd": FAKE_HWND, "eid": o9.get("eid"),
                                 "from_capture": (r9a.get("data") or {}).get("capture_id"),
                                 "actor": "判据G3c"}, actor="判据G3c")
    af9 = (r9.get("data") or {}).get("after") or {}
    ch9 = af9.get("changed_objects") or []
    ev["⑨after变化清单"] = {"changed_objects": ch9,
                       "口径": (af9.get("changed_objects_口径") or "")[:120]}
    ev["⑨点击本体"] = {"ok": r9.get("ok"), "code": r9.get("code"),
                     "warning": (r9.get("warning") or "")[:200],
                     "有after": "after" in (r9.get("data") or {})}
    need(len(ch9) > 0,
         "after.changed_objects 是空的（%r）—— 点击明显改了字，变化清单却什么都不说"
         % (af9.get("changed_objects_口径") or "")[:120], reds)
    need(any(str(c.get("label") or "").startswith("当前状态") for c in ch9),
         "after.changed_objects 里没有**被点的那个对象**（%r）—— 它才是这次动作的直接后果"
         % ch9, reds)
    need(all(c.get("uid_state") in ("TEXT_CHANGED", "STALE") for c in ch9),
         "changed_objects 里混进了 uid_state=OK 的（%r）—— OK 是「没变」，不该报成变化"
         % ch9, reds)

    # 收尾：把 _act 等还原，别影响后面的组
    # 收尾：把 _act 等还原，别影响后面的组
    if ORIG.get("_act") is not None:
        core._act = ORIG["_act"]
    if ORIG.get("_window_state") is not None:
        core.uia_tree.window_state = ORIG["_window_state"]
    core.uia_tree._client_rect = ORIG["_client_rect"]
    fx.n_extra_btn = 0
    fx.timer_tick = 0
    fx.g3_click_effect = "none"
    core._screen_forget(0)
    return 'S10 动作侧接线', reds, ev

def check_ocr_r3(core, fx):
    '''S9 · OCR 通道（R3）：命中才算、不可用就如实说、words 有上限、bbox 越界就不算锚、超时不挂。

    器材：**假 OCR**（OCRSTUB）—— 只换 `agentctl.ocr` 模块上的两个函数，**绝不真跑 OCR**。
    '''
    reds = []
    ev = {}

    def 盲点(r):
        return ' '.join(str(b) for b in (r.get('blind_spots') or []))

    # ── ① 引擎可用 + 像素里认出了 + 框能当锚 ⇒ R3 真、must_vlm 变 false
    OCRSTUB.reset()
    OCRSTUB.engine_ok_flag = True
    OCRSTUB.words = OCRSTUB.words_made(12, hit_text=OCR_ONLY_QUERY)
    OCRSTUB.text = OCR_ONLY_QUERY + ' 与其它字'
    fx.cls = 'TkTopLevel'
    core._screen_forget(0)
    r1, sc1 = call(core, query=OCR_ONLY_QUERY, top=500, ocr=True)
    ch1 = sc1.get('channels') or {}
    c3 = ch1.get('R3_ocr') or {}
    oc1 = sc1.get('ocr') or {}
    ev['①命中'] = {'R3_ocr': c3, 'R2_uia_hit': (ch1.get('R2_uia') or {}).get('hit'),
                 'must_vlm': ch1.get('must_vlm'),
                 'ocr段': {k: oc1.get(k) for k in ('wired', 'hit', 'usable', '跑了', 'dpi_scale', 'ms')}}
    need(oc1.get('跑了') is True, 'OCR 压根没跑（跑了=%r）—— ocr=True 是强制' % oc1.get('跑了'), reds)
    need(oc1.get('wired') is True and oc1.get('hit') is True,
         '像素里明明有「%s」，OCR 却报没认出来：%r' % (OCR_ONLY_QUERY, oc1), reds)
    need(c3.get('wired') is True and c3.get('hit') is True, 'R3 三要素没成立：%r' % c3, reds)
    need(ch1.get('must_vlm') is False,
         'R3 已成立，must_vlm 必须是 false（像素已经给出真相了）', reds)
    need((ch1.get('R2_uia') or {}).get('hit') is False,
         '本该 UIA 不命中（R2=%r），否则「must_vlm 变 false」分不清是谁干的'
         % (ch1.get('R2_uia') or {}).get('hit'), reds)

    # ── ①b CDP 与 OCR **同时在**：R1/R2/R3 三条都要报出来，谁也不许覆盖谁
    CDP.reset()
    CDP.up = True
    CDP.dom_objects = CDP.make_dom(12, hit_text=DOM_ONLY_QUERY)
    r1b, sc1b = call(core, query=OCR_ONLY_QUERY, top=500, ocr=True)
    ch1b = sc1b.get('channels') or {}
    ev['①b三通道并存'] = {k: (ch1b.get(k) or {}).get('hit') for k in ('R1_cdp', 'R2_uia', 'R3_ocr')}
    need((ch1b.get('R3_ocr') or {}).get('hit') is True,
         'CDP 在场时 R3 被吞了：%r' % ch1b.get('R3_ocr'), reds)

    # ── ①c **中文逐字切开**也必须命中（2026-10-18 DSH 真页实测锁）
    #   Windows OCR 把「剩余可选」认成「剩 余 可 选」（每字之间一个空格）；
    #   用普通规范化比较时**多字中文查询永远不中**（实测：`"剩余" in text` 为 False、`"剩" in text` 为 True）。
    #   这条锁的就是 `_ocr_tight`（吃掉 CJK 间空白；**拉丁词之间的空格不动**）。
    OCRSTUB.reset()
    OCRSTUB.engine_ok_flag = True
    OCRSTUB.text = '119 ： 56 剩 余 可 选 3 项 · 第 7 / 20 步'
    OCRSTUB.words = OCRSTUB.words_made(8, hit_text='剩')
    core._screen_forget(0)
    r1c, sc1c = call(core, query='剩余可选', top=500, ocr=True)
    oc1c = sc1c.get('ocr') or {}
    ev['①c逐字切开'] = {k: oc1c.get(k) for k in ('wired', 'hit', 'usable')}
    need(oc1c.get('hit') is True,
         'OCR 文本是「剩 余 可 选」而查询是「剩余可选」⇒ 必须命中（CJK 间空白要吃掉）；实得 %r'
         % (oc1c,), reds)
    need(core._ocr_tight('not allowed') == 'not allowed',
         '拉丁词之间的空格不许被吃掉（否则 not allowed 会粘成 notallowed）', reds)
    need(ch1b.get('R1_cdp') is not None and ch1b.get('R2_uia') is not None,
         '三通道并存时 channels 少了别的通道：%r' % list(ch1b), reds)
    need('hit' in (ch1b.get('R1_cdp') or {}) and 'hit' in (ch1b.get('R2_uia') or {}),
         'R1/R2 的判定字段被 OCR 那次重算弄丢了', reds)

    # ── ② 引擎不可用 ⇒ R3 false、**不假装**，blind_spots 说清楚
    OCRSTUB.reset()
    OCRSTUB.engine_ok_flag = False
    core._screen_forget(0)
    r2, sc2 = call(core, query=OCR_ONLY_QUERY, top=500, ocr=True)
    ch2 = sc2.get('channels') or {}
    oc2 = sc2.get('ocr') or {}
    bs2 = 盲点(r2)
    ev['②引擎不可用'] = {'R3_ocr': ch2.get('R3_ocr'),
                       'ocr段': {k: oc2.get(k) for k in ('wired', 'hit', 'usable', '原因')},
                       'blind_spots': bs2[:240]}
    need(oc2.get('wired') is False and (ch2.get('R3_ocr') or {}).get('hit') is False,
         '引擎不可用却报 R3 有线：%r / %r' % (oc2, ch2.get('R3_ocr')), reds)
    need(r2.get('ok') is True,
         'OCR 不可用把整次调用变成失败了（ok=%r）—— 通道不可用是**证据不足**，不是错误' % r2.get('ok'), reds)
    need('OCR' in bs2, 'OCR 没接上却没有 blind_spots 说明：%s' % bs2[:200], reds)
    need('不等于' in bs2 or '没有' in bs2,
         'blind_spots 没把「R3 不成立 ≠ 页面上没有」说透：%s' % bs2[:200], reds)

    # ── ③ words ≤80、omitted.total 诚实、dpi_scale 必须在
    OCRSTUB.reset()
    OCRSTUB.engine_ok_flag = True
    OCRSTUB.words = OCRSTUB.words_made(200, hit_text=OCR_ONLY_QUERY)
    core._screen_forget(0)
    r3, sc3 = call(core, query=OCR_ONLY_QUERY, top=500, ocr=True)
    oc3 = sc3.get('ocr') or {}
    wds3 = oc3.get('words') or []
    om3 = oc3.get('omitted') or {}
    ev['③上限'] = {'假 OCR 给了': len(OCRSTUB.words), '回了': len(wds3),
                 'omitted': om3, 'dpi_scale': oc3.get('dpi_scale')}
    need(len(wds3) <= 80, 'ocr 段回了 %d 个词（上限 80）' % len(wds3), reds)
    need(int(om3.get('total') or 0) == len(OCRSTUB.words),
         'omitted.total 不诚实：引擎给了 %d，报了 %r' % (len(OCRSTUB.words), om3.get('total')), reds)
    need(oc3.get('dpi_scale') not in (None, ''),
         'dpi_scale 不在返回里 —— 坐标是按什么换算的？调用方没法自己核', reds)
    need((sc3.get('channels') or {}).get('R3_ocr', {}).get('dpi_scale') is not None,
         'channels.R3_ocr 里没有 dpi_scale', reds)

    # ── ④ bbox 落在客户区外 ⇒ usable=false（框不能当锚，点下去会点到别处）
    OCRSTUB.reset()
    OCRSTUB.engine_ok_flag = True
    OCRSTUB.words = OCRSTUB.words_made(12, hit_text=OCR_ONLY_QUERY, inside=False)
    core._screen_forget(0)
    r4, sc4 = call(core, query=OCR_ONLY_QUERY, top=500, ocr=True)
    ch4 = sc4.get('channels') or {}
    oc4 = sc4.get('ocr') or {}
    ev['④bbox越界'] = {'命中': oc4.get('hit'), 'usable': oc4.get('usable'),
                     'R3': ch4.get('R3_ocr'), '原因': oc4.get('原因')}
    need(oc4.get('hit') is True, '这组造的是「认出了但框越界」，hit 本该为 true：%r' % oc4, reds)
    need(oc4.get('usable') is False,
         '框落在客户区 1280×900 之外（x=5000,y=4000）却还 usable —— 那会点偏', reds)
    need((ch4.get('R3_ocr') or {}).get('hit') is False,
         'usable=false 时 R3 不该成立：%r' % ch4.get('R3_ocr'), reds)
    need((ch4.get('R3_ocr') or {}).get('bbox_usable') is False,
         'R3_ocr.bbox_usable 没有如实报 false：%r' % ch4.get('R3_ocr'), reds)

    # ── ⑤ 超时 ⇒ ok=false + 原因，**不许挂**
    OCRSTUB.reset()
    OCRSTUB.engine_ok_flag = True
    OCRSTUB.slow_ms = 2000
    OCRSTUB.words = OCRSTUB.words_made(12, hit_text=OCR_ONLY_QUERY)
    core._screen_forget(0)
    t5 = time.time()
    r5, sc5 = call(core, query=OCR_ONLY_QUERY, top=500, ocr=True, ocr_max_ms=100)
    dt5 = (time.time() - t5) * 1000.0
    oc5 = sc5.get('ocr') or {}
    ev['⑤超时'] = {'ms预算': 100, '实测调用毫秒': int(dt5),
                 'ocr段': {k: oc5.get(k) for k in ('wired', 'hit', '原因', 'ms')},
                 'R3': (sc5.get('channels') or {}).get('R3_ocr')}
    need(dt5 < 1500, 'max_ms=100 却花了 %d ms —— 超时预算没生效（**不许挂**）' % int(dt5), reds)
    need(oc5.get('wired') is False,
         '超时了却还报 wired=true（拿没跑完的结果当证据）：%r' % oc5, reds)
    need('超时' in str(oc5.get('原因') or ''), '超时时没给原因：%r' % oc5.get('原因'), reds)
    need(not (oc5.get('words') or []), '超时了还回了一截 words —— 那是半截证据，比没有更坏', reds)
    need((sc5.get('channels') or {}).get('R3_ocr', {}).get('hit') is False,
         '超时却报 R3 命中：%r' % (sc5.get('channels') or {}).get('R3_ocr'), reds)

    # 收尾：把假 OCR 收回「引擎不可用」，别影响后面的组
    OCRSTUB.reset()
    OCRSTUB.engine_ok_flag = False
    OCRSTUB.install(core)
    CDP.reset()
    CDP.install(core)
    fx.cls = 'TkTopLevel'
    core._screen_forget(0)
    return 'S9 OCR 接线与 R3', reds, ev


def check_cdp_r1(core, fx):
    '''S8 · CDP 通道**接线**：R1 要能用真事实成立，不可达时如实 false，dom 段有上限，独立实例不抢端口。

    器材：**假 cdp**（CDP / FakeCdp）—— 只换 `_cdp_http` 与 `_cdp_eval` 两个接缝，
    不开真浏览器、不碰 9222 端口、不起任何进程。
    '''
    reds = []
    ev = {}

    def 盲点(r):
        return ' '.join(str(b) for b in (r.get('blind_spots') or []))

    # ── ① CDP 可用 + DOM 命中 ⇒ R1 真、must_vlm 变 false
    #   ★ 关键设计：query 用**只在 DOM 里出现**的词 —— UIA 那边必然不命中（R2 不成立），
    #     于是 must_vlm 变 false 就**只可能**是 R1 干的，不是 R2 蹭的。
    CDP.reset()
    CDP.up = True
    CDP.dom_objects = CDP.make_dom(12, hit_text=DOM_ONLY_QUERY)
    fx.cls = 'Chrome_WidgetWin_1'
    core._screen_forget(0)
    r1, sc1 = call(core, query=DOM_ONLY_QUERY, top=500)
    ch1 = sc1.get('channels') or {}
    c1 = ch1.get('R1_cdp') or {}
    dom1 = sc1.get('dom') or {}
    ev['①CDP可用+DOM命中'] = {'R1_cdp': c1, 'R2_uia_hit': (ch1.get('R2_uia') or {}).get('hit'),
                          'must_vlm': ch1.get('must_vlm'), 'dom条数': len(dom1.get('objects') or []),
                          'blind_spots': 盲点(r1)[:200]}
    need(c1.get('wired') is True and c1.get('reachable') is True,
         '端口应答了却没报 wired/reachable：%r' % c1, reds)
    need(c1.get('dom_hit') is True and c1.get('hit') is True,
         'DOM 里明明有「%s」，R1 却不成立（R1=%r dom_hit=%r）—— 这就是没接线'
         % (DOM_ONLY_QUERY, c1.get('hit'), c1.get('dom_hit')), reds)
    need((ch1.get('R2_uia') or {}).get('hit') is False,
         '本该 UIA 不命中（R2=%r），否则「must_vlm 变 false」就分不清是谁干的'
         % (ch1.get('R2_uia') or {}).get('hit'), reds)
    need(ch1.get('must_vlm') is False,
         'R1 已成立，must_vlm 必须是 false（DOM 已经给出真相了，不必再上 VLM）', reds)
    need((c1.get('source_confidence') or {}).get('existence') == 0.90,
         'R1 命中时 source_confidence.cdp 该给 DOM 的真值（0.90 量级），实得 %r'
         % (c1.get('source_confidence'),), reds)
    need(int(c1.get('命中节点数') or 0) >= 1,
         'R1 命中却没报命中节点数：%r' % c1.get('命中节点数'), reds)

    # ── ② CDP 不可达 ⇒ R1 如实 false，**不假装**，并且 blind_spots 说清楚
    CDP.reset()
    CDP.up = False
    core._screen_forget(0)
    r2, sc2 = call(core, query=DOM_ONLY_QUERY, top=500)
    ch2 = sc2.get('channels') or {}
    c2 = ch2.get('R1_cdp') or {}
    dom2 = sc2.get('dom') or {}
    bs2 = 盲点(r2)
    ev['②CDP不可达'] = {'R1_cdp': c2, 'must_vlm': ch2.get('must_vlm'),
                     'dom.wired': dom2.get('wired'), 'blind_spots': bs2[:240]}
    need(c2.get('wired') is False and c2.get('reachable') is False and c2.get('hit') is False,
         '端口不通却报 R1 有线/可达：%r' % c2, reds)
    need(dom2.get('wired') is False and not (dom2.get('objects') or []),
         'CDP 不可达却回了一段 DOM 对象 —— 那就是编的：%r' % (dom2.get('objects') or [])[:2], reds)
    need(r2.get('ok') is True,
         'CDP 不可达把整次调用变成失败了（ok=%r）—— 通道不可用是**证据不足**，不是错误'
         % r2.get('ok'), reds)
    need('CDP' in bs2, 'CDP 没接上却没有 blind_spots 说明：%s' % bs2[:200], reds)
    need('不等于' in bs2 or '不是「没有」' in bs2,
         'blind_spots 没把「R1 不成立 ≠ 页面上没有」说透：%s' % bs2[:200], reds)
    need('launch' in bs2,
         'blind_spots 没给可执行的下一步（起个独立实例）：%s' % bs2[:200], reds)

    # ── ③ dom 段：≤60 条、每条带 role/name/rect，且裁剪账对得上
    CDP.reset()
    CDP.up = True
    CDP.dom_objects = CDP.make_dom(200, hit_text=DOM_ONLY_QUERY)
    core._screen_forget(0)
    r3, sc3 = call(core, query=DOM_ONLY_QUERY, top=500)
    dom3 = sc3.get('dom') or {}
    objs3 = dom3.get('objects') or []
    om3 = dom3.get('omitted') or {}
    ev['③dom段上限'] = {'页面给了': len(CDP.dom_objects), '返回': len(objs3),
                     'omitted': om3, '样例': objs3[0] if objs3 else None}
    need(len(objs3) <= 60, '页面给了 %d 条 DOM，ctl_screen 回了 %d 条 —— 上限 60 是硬要求'
         % (len(CDP.dom_objects), len(objs3)), reds)
    need(len(objs3) > 0, '一条 DOM 都没回（上限不该把内容清空）', reds)
    need(int(om3.get('cap') or 0) == 60,
         'omitted.cap 应写明 60，实得 %r' % om3.get('cap'), reds)
    need(int(om3.get('total') or 0) == len(CDP.dom_objects) and int(om3.get('returned') or 0) == len(objs3),
         'omitted 对不上账：页面 %d / returned %r / total %r'
         % (len(CDP.dom_objects), om3.get('returned'), om3.get('total')), reds)
    bad = [o for o in objs3 if not (o.get('role') and o.get('name')
                                    and isinstance(o.get('rect'), list) and len(o['rect']) == 4
                                    and o.get('selector') and o.get('source') == 'cdp')]
    need(not bad, '有 %d 条 DOM 缺 role/name/rect/selector/source：%r' % (len(bad), bad[:1]), reds)

    # ── ④ 端口被**别人**占着 ⇒ 明确拒绝、绝不抢（连起进程都不许起）
    CDP.reset()
    CDP.taken_by_other = True
    r4 = core.dispatch('ctl_browser', {'action': 'launch', 'port': 9222}, actor='判据G1')
    d4 = r4.get('data') or {}
    ev['④端口被占'] = {'ok': r4.get('ok'), 'code': r4.get('code'),
                     'warning': (r4.get('warning') or '')[:260],
                     '我们起的': d4.get('我们起的'), '建议': d4.get('建议'),
                     '起过进程': len(CDP.spawned)}
    need(r4.get('ok') is False and r4.get('code') not in (0, None),
         '端口已被别人的实例占着，launch 却返回成功（ok=%r）—— 那等于替用户抢浏览器'
         % r4.get('ok'), reds)
    need(not CDP.spawned,
         '端口被占还**起了新进程**：%r —— 这不只是抢端口，是凭空多一个浏览器' % CDP.spawned, reds)
    w4 = str(r4.get('warning') or '')
    need('占用' in w4, '拒绝时没说明「端口已被占用」：%s' % w4[:200], reds)
    need('port' in w4, '拒绝时没给下一步（换 port）：%s' % w4[:200], reds)
    need(d4.get('我们起的') is False and bool(d4.get('建议')),
         '拒绝信封没写清「那不是我们起的」+ 建议：%r' % d4, reds)

    # 收尾：把假 cdp 收回「不可达」，别影响后面的组
    CDP.reset()
    CDP.install(core)
    fx.cls = 'TkTopLevel'
    core._screen_forget(0)
    return 'S8 CDP 接线与 R1', reds, ev


def check_screen_ready(core, fx):
    """S7 · 首扫不可信：暖机**有界**、screen_ready **如实**、**未热 ≠ 拒绝**。

    器材：**时间门控的假采集器**（fx.start_cold(ms)）—— 接下来 ms 毫秒里 uia_tree.walk
    只会拿到一棵**外壳**树（6 个节点、0 个可交互），ms 之后恢复全量。
    ★ 只换了 I/O 边界（跟本判据一贯的 install_stubs 同款），**walk() 仍是现役真代码**。
    """
    reds = []
    ev = {}
    core._screen_forget(0)
    fx.timer_tick = 0
    fx.quota = 3

    def 证据(sc):
        return sc.get("ready_证据") or {}

    def 盲点(r):
        return " ".join(str(b) for b in (r.get("blind_spots") or []))

    # ── ① 冷窗首扫：要么暖机过（重试≥1），要么 screen_ready=false 且 blind_spots 有提示
    core._screen_forget(0)
    fx.start_cold(300)
    t0 = time.time()
    r1, sc1 = call(core, top=500)
    dt1 = (time.time() - t0) * 1000.0
    e1 = 证据(sc1)
    ev["①冷窗首扫"] = {"screen_ready": sc1.get("screen_ready"),
                   "重试": e1.get("重试"), "等待毫秒": e1.get("等待毫秒"),
                   "各次节点数": e1.get("各次节点数"), "耗时毫秒": int(dt1),
                   "对象数": len(sc1.get("objects") or [])}
    need(r1.get("ok") is True, "冷窗首扫没成功：%s" % (r1.get("warning") or "")[:120], reds)
    need("screen_ready" in sc1, "返回里没有 screen_ready（就绪是这单的核心产出）", reds)
    warmed = int(e1.get("重试") or 0) >= 1 and sc1.get("screen_ready") is True
    notready = sc1.get("screen_ready") is False and "未热" in 盲点(r1)
    need(warmed or notready,
         "冷窗首扫既没暖机（重试=%r）也没标未热（screen_ready=%r, blind_spots=%s）"
         % (e1.get("重试"), sc1.get("screen_ready"), 盲点(r1)[:80]), reds)
    if warmed:
        # ★ 取的是**节点数更多的那次**：第一趟（外壳）不该被交出去
        tries = e1.get("各次节点数") or []
        need(len(tries) >= 2 and int(tries[-1]) > int(tries[0]),
             "暖机了却没取节点数更多的那次（各次=%r，交出的=%d）"
             % (tries, int(e1.get("节点数") or 0)), reds)
        need(int(e1.get("节点数") or 0) == int(tries[-1]),
             "ready_证据.节点数 与各次最大值对不上：%r vs %r" % (e1.get("节点数"), tries), reds)
        need(int(e1.get("等待毫秒") or 0) >= 300,
             "暖机了却没等够间隔（等待 %r ms，派单要求 300~500ms）" % e1.get("等待毫秒"), reds)
    need(int(e1.get("重试") or 0) <= int(e1.get("重试上限") or 2),
         "重试次数越界：%r > 上限 %r" % (e1.get("重试"), e1.get("重试上限")), reds)

    # ── ② 一直冷：两次后仍判未热 ⇒ screen_ready=false + 有界（不许无限等）
    core._screen_forget(0)
    fx.start_cold(8000)
    t2 = time.time()
    r2, sc2 = call(core, top=500)
    dt2 = (time.time() - t2) * 1000.0
    e2 = 证据(sc2)
    ev["②一直冷"] = {"screen_ready": sc2.get("screen_ready"), "重试": e2.get("重试"),
                  "等待毫秒": e2.get("等待毫秒"), "各次节点数": e2.get("各次节点数"),
                  "判定": e2.get("判定"), "耗时毫秒": int(dt2)}
    need(sc2.get("screen_ready") is False,
         "连采 %r 趟外壳还报 screen_ready=true（外壳被当成热屏交出去了）" % e2.get("重试"), reds)
    need(int(e2.get("重试") or 0) == 2,
         "重试次数应恰好 2（有界的具体数值），实得 %r" % e2.get("重试"), reds)
    need(dt2 < 5000, "暖机**有界**：这次调用花了 %d ms，超过 2×间隔+采集的合理上限" % int(dt2), reds)
    need(e2.get("判定"), "screen_ready=false 却没给出判定依据（凭什么说没热）", reds)
    bs2 = 盲点(r2)
    ev["②的blind_spots"] = bs2[:260]
    need("未热" in bs2, "未热时 blind_spots 没写「本屏可能未热」：%s" % bs2[:160], reds)
    need("不等于" in bs2 or "不要当成" in bs2,
         "blind_spots 没明写「不等于页面上没有」：%s" % bs2[:200], reds)
    need("稍候" in bs2 or "再调" in bs2,
         "blind_spots 没给可执行的下一步（稍候再调/交叉确认）：%s" % bs2[:200], reds)

    # ── ③ ★ 未热 ≠ 拒绝：两种信封不许混
    #   （a）未热 + 无声明 ⇒ ok=true/code=0，证据不足写在 screen_ready + blind_spots
    need(r2.get("ok") is True and r2.get("code") in (0, None),
         "未热被当成了错误（ok=%r code=%r）—— 它是**证据不足**，不是失败"
         % (r2.get("ok"), r2.get("code")), reds)
    need(bool(sc2.get("screen_ready")) is False and not bool(sc2.get("ready_证据") is None),
         "未热那一趟没带 screen_ready/ready_证据", reds)
    #   （b）同一块冷屏 + 声明一个匹配不到的串 ⇒ 那是**拒绝**（code=4 + watch_校验.unmatched）
    r3, d3 = call(core, top=500, watch_text=["这串冷屏上不存在"])
    ck3 = ((r3.get("data") or {}).get("watch_校验") or {})
    ev["③冷屏+声明"] = {"ok": r3.get("ok"), "code": r3.get("code"),
                   "unmatched": ck3.get("unmatched")}
    need(r3.get("ok") is False and r3.get("code") not in (0, None),
         "冷屏 + 声明匹配不到：应是**拒绝**（rc≠0），实得 ok=%r code=%r"
         % (r3.get("ok"), r3.get("code")), reds)
    need(ck3.get("unmatched") == ["这串冷屏上不存在"],
         "拒绝信封没列出 unmatched：%r" % ck3.get("unmatched"), reds)
    need(not d3, "拒绝时还回了 objects（会被当成声明已兑现）", reds)
    need("校验失败" in 盲点(r3),
         "拒绝信封的 blind_spots 没写「这是校验失败，不是屏幕空」：%s" % 盲点(r3)[:160], reds)

    # ── ④ warmup=false ⇒ 不重试（省时间），但 screen_ready 仍**如实**判
    core._screen_forget(0)
    fx.start_cold(8000)
    t4 = time.time()
    r4, sc4 = call(core, top=500, warmup=False)
    dt4 = (time.time() - t4) * 1000.0
    e4 = 证据(sc4)
    ev["④warmup=false"] = {"screen_ready": sc4.get("screen_ready"), "重试": e4.get("重试"),
                     "等待毫秒": e4.get("等待毫秒"), "耗时毫秒": int(dt4)}
    need(int(e4.get("重试") or 0) == 0,
         "warmup=false 还重试了 %r 次（这参数就是用来关重试的）" % e4.get("重试"), reds)
    need(int(e4.get("等待毫秒") or 0) == 0 and dt4 < 2000,
         "warmup=false 却等了 %r ms（本次调用 %d ms）—— 没真关掉" % (e4.get("等待毫秒"), int(dt4)), reds)
    need(sc4.get("screen_ready") is False,
         "warmup=false 时 screen_ready 仍应**如实**判未热，实得 %r" % sc4.get("screen_ready"), reds)
    need("未热" in 盲点(r4),
         "warmup=false + 未热 ⇒ blind_spots 仍要点名（否则调用方无从知道这屏不可信）：%s"
         % 盲点(r4)[:160], reds)

    # ── ⑤ 暖回来之后：热屏就应当如实报 ready（别一路误报未热）
    fx._cold_until = 0.0
    core._screen_forget(0)
    r5, sc5 = call(core, top=500)
    ev["⑤热屏"] = {"screen_ready": sc5.get("screen_ready"), "节点数": 证据(sc5).get("节点数"),
                 "重试": 证据(sc5).get("重试"), "与上次相比": 证据(sc5).get("与上次相比")}
    need(sc5.get("screen_ready") is True,
         "热屏反被报成未热（误报）—— 判据依据：%r" % 证据(sc5).get("判定"), reds)
    need(int(证据(sc5).get("重试") or 0) == 0,
         "热屏不该触发重试，实得 %r" % 证据(sc5).get("重试"), reds)
    fx._cold_until = 0.0

    # ══ 窗F2b：真页冷扫是「三条依据一条都不中」的漏判（F2 自己就交代了这个风险面）══
    #   真页实测（2026-10-18，work\_暖机真页复核_20261018.py）：
    #     冷扫 ≈ 18 个可报对象 / 60 个原始节点 / 372ms  →  F2 的三条依据一条都没命中，被判成已热
    #     热扫 ≈ 49 个可报对象 / 111 个原始节点 / 259ms
    #   所以光「有基线时的 ①」不够：**首扫（无基线）必须强制探两次**。

    # ── ⑥ 网页类窗口首扫（无基线）⇒ **必然探两次**，并把节点更多的那次交出去
    fx._cold_until = 0.0
    fx._half_until = 0.0
    fx.cls = "Chrome_WidgetWin_1"
    core._screen_forget(0)
    fx.start_half_cold(150)       # 第一趟半热（真页冷扫形状）；暖机那 400ms 一睡，它就热了
    t6 = time.time()
    r6, sc6 = call(core, top=500)
    dt6 = (time.time() - t6) * 1000.0
    e6 = 证据(sc6)
    t6n = e6.get("各次节点数") or []
    ev["⑥网页类首扫"] = {"screen_ready": sc6.get("screen_ready"), "重试": e6.get("重试"),
                     "等待毫秒": e6.get("等待毫秒"), "各次节点数": t6n,
                     "窗口类别": e6.get("窗口类别"), "首扫强制探测": e6.get("首扫强制探测"),
                     "耗时毫秒": int(dt6)}
    need(int(e6.get("重试") or 0) >= 1,
         "网页类窗口首扫**没有探第二次**（重试=%r，各次节点数=%r）—— 首扫没有上一屏可对比，"
         "不探第二次就会把 18 个对象的半屏当热屏交出去" % (e6.get("重试"), t6n), reds)
    need(len(t6n) >= 2 and int(t6n[-1]) > int(t6n[0]),
         "探了两次却没把**节点更多的那次**交出去（各次=%r，交出的=%r）"
         % (t6n, e6.get("节点数")), reds)
    need(bool(t6n) and int(e6.get("节点数") or 0) == max(int(x) for x in t6n),
         "交出的节点数不是各趟最大值：%r vs %r" % (e6.get("节点数"), t6n), reds)
    need(sc6.get("screen_ready") is True,
         "第二趟已经热了却还报未热（误报）：判定=%r" % e6.get("判定"), reds)
    need(e6.get("窗口类别") == "网页类" and e6.get("首扫强制探测") is True,
         "ready_证据 没写明这次是网页类首扫强制探测：窗口类别=%r 首扫强制探测=%r"
         % (e6.get("窗口类别"), e6.get("首扫强制探测")), reds)

    # ── ⑦ 首扫探了两次**都还是冷** ⇒ screen_ready=false，且明写「证据不足≠没有」
    fx._half_until = 0.0
    core._screen_forget(0)
    fx.start_half_cold(8000)      # 一直半热：探几次都是真页冷扫那个形状
    t7 = time.time()
    r7, sc7 = call(core, top=500)
    dt7 = (time.time() - t7) * 1000.0
    e7 = 证据(sc7)
    ev["⑦网页首扫两次都冷"] = {"screen_ready": sc7.get("screen_ready"),
                         "重试": e7.get("重试"), "各次节点数": e7.get("各次节点数"),
                         "判定": e7.get("判定"), "耗时毫秒": int(dt7)}
    need(int(e7.get("重试") or 0) >= 1,
         "网页类首扫压根没探第二次（重试=%r）—— 那两次都少就无从谈起" % e7.get("重试"), reds)
    need(sc7.get("screen_ready") is False,
         "网页类探完还是冷的（各趟原始节点=%r，真页冷扫是 60/18 对象）却报 screen_ready=true"
         "—— 这正是 F2 漏判的那一屏" % e7.get("各次节点数"), reds)
    need(any("网页" in str(x) for x in (e7.get("判定") or [])),
         "screen_ready=false 却没点名网页类门槛（判定=%r）" % (e7.get("判定"),), reds)
    need(r7.get("ok") is True and r7.get("code") in (0, None),
         "网页首扫未热被当成了错误（ok=%r code=%r）—— 它是证据不足"
         % (r7.get("ok"), r7.get("code")), reds)
    bs7 = 盲点(r7)
    need("未热" in bs7 and ("不等于" in bs7 or "不要当成" in bs7),
         "网页首扫未热时 blind_spots 没明写「证据不足，不等于页面上没有」：%s" % bs7[:200], reds)
    need(dt7 < 5000, "暖机**有界**：这次调用花了 %d ms" % int(dt7), reds)

    # ── ⑧ 非网页类窗口首扫 ⇒ **不加延迟**（别给记事本这类白等 400ms）
    fx._half_until = 0.0
    fx.cls = "TkTopLevel"
    core._screen_forget(0)
    t8 = time.time()
    r8, sc8 = call(core, top=500)
    dt8 = (time.time() - t8) * 1000.0
    e8 = 证据(sc8)
    ev["⑧非网页类首扫"] = {"screen_ready": sc8.get("screen_ready"), "重试": e8.get("重试"),
                     "等待毫秒": e8.get("等待毫秒"), "窗口类别": e8.get("窗口类别"),
                     "首扫强制探测": e8.get("首扫强制探测"), "耗时毫秒": int(dt8)}
    need(int(e8.get("重试") or 0) == 0 and int(e8.get("等待毫秒") or 0) == 0,
         "非网页类窗口首扫也去探第二次了（重试=%r，等待=%r ms）—— 首扫强制探测**只该给网页类**，"
         "原生窗口不该平白多等 400ms" % (e8.get("重试"), e8.get("等待毫秒")), reds)
    need(dt8 < 800, "非网页类首扫耗时 %d ms，明显多等了一趟" % int(dt8), reds)
    need(sc8.get("screen_ready") is True and e8.get("窗口类别") == "非网页类",
         "非网页类热屏被误报未热/类别写错：screen_ready=%r 窗口类别=%r"
         % (sc8.get("screen_ready"), e8.get("窗口类别")), reds)

    # ── ⑨ 网页类 + warmup=false ⇒ 不探第二次，但 screen_ready **仍如实**判
    fx.cls = "Chrome_WidgetWin_1"
    fx._half_until = 0.0
    core._screen_forget(0)
    fx.start_half_cold(8000)
    t9 = time.time()
    r9, sc9 = call(core, top=500, warmup=False)
    dt9 = (time.time() - t9) * 1000.0
    e9 = 证据(sc9)
    ev["⑨网页+warmup=false"] = {"screen_ready": sc9.get("screen_ready"), "重试": e9.get("重试"),
                        "等待毫秒": e9.get("等待毫秒"), "各次节点数": e9.get("各次节点数"),
                        "耗时毫秒": int(dt9)}
    need(int(e9.get("重试") or 0) == 0 and int(e9.get("等待毫秒") or 0) == 0,
         "warmup=false 却还是探了第二次（重试=%r，等待=%r ms）—— 这参数就是用来关掉重试的"
         % (e9.get("重试"), e9.get("等待毫秒")), reds)
    need(len(e9.get("各次节点数") or []) == 1,
         "warmup=false 采了 %r 趟" % len(e9.get("各次节点数") or []), reds)
    need(sc9.get("screen_ready") is False,
         "warmup=false 时网页类首扫仍应**如实**判未热，实得 %r" % sc9.get("screen_ready"), reds)
    need("未热" in 盲点(r9),
         "warmup=false + 网页未热 ⇒ blind_spots 仍要点名：%s" % 盲点(r9)[:160], reds)
    fx._half_until = 0.0
    fx.cls = "TkTopLevel"
    core._screen_forget(0)
    return "S7 首扫就绪判定", reds, ev




# ======================================================================
# --live：真开一个自己的 tkinter 窗口（用完立刻关）
# ======================================================================
def count_top_windows():
    try:
        import win32gui
        n = [0]
        win32gui.EnumWindows(lambda h, p: n.__setitem__(0, n[0] + 1), None)
        return n[0]
    except Exception:
        return 0


def count_border_windows():
    """屏幕上残留几个 AgentCtlCtrlBorder* 窗。判据只读，不该拉起任何边框。

    返回 (个数, 说明)。数不出来就把原因带出来 —— 只丢一个 -1 等于没说话。
    """
    try:
        import win32gui
        hits = []

        def cb(h, _):
            try:
                if "AgentCtlCtrlBorder" in (win32gui.GetClassName(h) or ""):
                    hits.append(h)
            except Exception:
                pass
        try:
            win32gui.EnumWindows(cb, None)
        except Exception:
            time.sleep(0.3)          # 偶发 error 183，重试一次再认输
            win32gui.EnumWindows(cb, None)
        return len(hits), "按窗口类名枚举成功"
    except Exception as e:
        return -1, ("枚举失败（%s: %s）—— 本会话多半没有交互桌面，"
                       "这一项**没验成**，不是 0" % (type(e).__name__, e))


def run_live(core):
    """真窗口那半段。**没有交互桌面就明说跑不了** —— 不给半个结论。"""
    reds, ev, blocked = [], {}, False
    if count_top_windows() <= 0:
        return reds, ev, True
    import tkinter as tk
    root = None
    try:
        root = tk.Tk()
        root.title("B1语义屏判据_真窗")
        root.geometry("1100x760+40+40")
        for i in range(N_BTN):
            tk.Button(root, text="选项格%02d" % (i + 1)).place(x=40, y=20 + 20 * i, width=90, height=17)
        for i in range(N_QTEXT):
            tk.Label(root, text="题干第%d段：下列说法正确的是" % (i + 1),
                     wraplength=900, justify="left").place(x=200, y=20 + 60 * i, width=880, height=50)
        tk.Label(root, text=TIMER_LABELS[0]).place(x=200, y=600, width=200, height=20)
        root.update()
        hwnd = _tk_hwnd(root)
        r1 = core.dispatch("ctl_screen", {"hwnd": hwnd, "query": QUERY}, actor="判据B1")
        d1 = (r1.get("data") or {}).get("结果") or {}
        ev["live.ok"] = bool(r1.get("ok"))
        ev["live.窗口"] = (d1.get("window") or {})
        ev["live.objects数"] = len(d1.get("objects") or [])
        if not r1.get("ok"):
            reds.append("真窗口调用失败：code=%s %s" % (r1.get("code"), r1.get("warning")))
            return reds, ev, False
        if len(d1.get("objects") or []) > 40:
            reds.append("真窗口默认返回 %d 条 > 40" % len(d1.get("objects") or []))
        v1 = d1.get("screen_version")
        u1 = uids_of(d1.get("objects") or [])
        r2 = core.dispatch("ctl_screen", {"hwnd": hwnd, "query": QUERY}, actor="判据B1")
        d2 = ((r2.get("data") or {}).get("结果") or {})
        if uids_of(d2.get("objects") or []) != u1:
            reds.append("真窗口连调两次 uid 不稳")
        if d2.get("screen_version") != v1:
            reds.append("真窗口没动，screen_version 却变了")
        root.title("B1语义屏判据_真窗_改标题")
        root.update()
        r3 = core.dispatch("ctl_screen", {"hwnd": hwnd, "query": QUERY}, actor="判据B1")
        d3 = ((r3.get("data") or {}).get("结果") or {})
        ev["live.改标题后version变了"] = d3.get("screen_version") != v1
        if d3.get("screen_version") == v1:
            reds.append("真窗口改了标题，screen_version 没变")
    except Exception as e:
        reds.append("真窗口那半段崩了：%s: %s" % (type(e).__name__, e))
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
    return reds, ev, blocked


def _tk_hwnd(win):
    """tkinter 窗口的原生句柄（winfo_id 是子窗口，取它的顶层父）。"""
    import win32gui
    h = int(win.winfo_id())
    parent = win32gui.GetParent(h)
    return parent or h


# ======================================================================
# main
# ======================================================================
def main():
    ap = argparse.ArgumentParser(description="F4 语义屏 ctl_screen 判据（窗B1）")
    ap.add_argument("--live", action="store_true", help="真开 tkinter 窗口跑（需交互桌面）")
    ap.add_argument("--dump", action="store_true", help="打一份真实返回 JSON（截断到 --dump-lines 行）")
    ap.add_argument("--dump-lines", type=int, default=60)
    ap.add_argument("--inject", choices=sorted(INJECT) + ["all"],
                    help="跑红自证：把现役 core.py 复制到 %%TEMP%% 注入缺陷再跑同一套断言")
    args = ap.parse_args()

    kinds = sorted(INJECT) if args.inject == "all" else ([args.inject] if args.inject else [])
    tmp = tempfile.mkdtemp(prefix="_B1判据_")
    sandbox(tmp)
    target, notes = CORE, "现役文件（未注入）"
    if kinds:
        target, notes = build_injected(tmp, kinds)
    core, path = load_core(target, "_B1_core_under_test")

    print("=" * 100)
    print("窗B1 · F4 语义屏 ctl_screen 判据")
    print("被测 core   : %s" % path)
    print("模式        : %s" % notes)
    print("夹具        : 合成 UIA 树 · %d 个视口内按钮 / %d 块 query 大文本 / %d 条备注"
          " / 计时器+状态行 / %d 个屏外按钮（客户区 %dx%d）"
          % (N_BTN, N_QTEXT, N_TEXT, N_OFF, CLIENT[2], CLIENT[3]))
    print("真闸门/真事件流: 一个字节都没碰（沙箱在 %s）" % tmp)
    print("=" * 100)

    fx = Fixture()
    install_stubs(core, fx)
    res = {}
    for name, fn in (("(1) 默认 top 与必进保证", lambda: check_top_and_must(core, fx)),
                     ("(2) uid 稳定与 screen_version 签发", lambda: check_uid_and_version(core, fx)),
                     ("(3) volatile 豁免", lambda: check_volatile(core, fx)),
                     ("(4) source_confidence 取 min", lambda: check_confidence_min(core, fx)),
                     ("(5) channels / name_trust / 字段", lambda: check_channels_and_trust(core, fx)),
                     ("(6) 契约与接线", lambda: check_contract(core)),
                     ("(7) 属性级置信度", lambda: check_conf_attrs(core, fx)),
                     ("(8) uid 三档", lambda: check_uid_state(core, fx)),
                     ("(9) R2 的 name_trust 门槛", lambda: check_r2_name_trust_gate(core, fx)),
                     ("(10) since 的版本应答", lambda: check_since_version_reply(core, fx)),
                     ("S1 声明即不豁免", lambda: check_watch_declared(core, fx)),
                     ("S2 未声明不进位", lambda: check_watch_undeclared(core, fx)),
                     ("S3 未命中必拒", lambda: check_watch_unmatched(core, fx)),
                     ("S4 重叠声明全收", lambda: check_watch_shadow_allhit(core, fx)),
                     ("S5 声明宽度上限", lambda: check_watch_width(core, fx)),
                     ("S6 拒绝信封带屏上下文", lambda: check_watch_reject_ctx(core, fx)),
                     ("S7 首扫就绪判定", lambda: check_screen_ready(core, fx)),
                     ("S8 CDP 接线与 R1", lambda: check_cdp_r1(core, fx)),
                     ("S9 OCR 接线与 R3", lambda: check_ocr_r3(core, fx)),
                     ("S10 动作侧接线", lambda: check_action_side(core, fx)),
                     ("S11 cdp.py --port 与契约两句", check_cdp_port_and_contract)):
        title, reds, ev = fn()
        res[title] = reds
        print("")
        print("-- %s -- %s" % (title, "红" if reds else "绿"))
        for line in reds:
            print("   **红 · %s" % line)
        for k, v in ev.items():
            print("   %s = %s" % (k, json.dumps(v, ensure_ascii=False)[:600]))

    total = sum(len(v) for v in res.values())

    if args.dump:
        core._screen_forget(0)
        _, sc = call(core, query=QUERY)
        sc["window"] = dict(sc.get("window") or {})
        sc["objects"] = (sc.get("objects") or [])[:6]
        txt = json.dumps(sc, ensure_ascii=False, indent=2).splitlines()
        print("")
        print("-- 真实返回 JSON（前 %d 个对象，共 %d 个）--" % (6, len(sc["objects"])))
        for i, line in enumerate(txt[:args.dump_lines]):
            print("   %s" % line)
        if len(txt) > args.dump_lines:
            print("   ……【截断：还有 %d 行】" % (len(txt) - args.dump_lines))
        # ★ 窗C1：再打一份**给了 since** 的返回（版本应答三件套 + 逐对象 uid_state）
        core._screen_forget(0)
        _, base = call(core, query=QUERY)
        _, s2 = call(core, query=QUERY, since=base.get("screen_version"))
        d2 = s2.get("delta") or {}
        sample = {"screen_version": s2.get("screen_version"),
                  "since_echo": s2.get("since_echo"),
                  "version_match": s2.get("version_match"),
                  "screen_version_新值": s2.get("screen_version_新值"),
                  "版本口径": s2.get("版本口径"),
                  "delta": {k: d2.get(k) for k in ("since_echo", "version_match",
                                                "screen_version_新值", "uid_state_统计")},
                  "channels_R2_uia": (s2.get("channels") or {}).get("R2_uia"),
                  "第一个对象": (s2.get("objects") or [{}])[0]}
        print("")
        print("-- 给了 since 时的版本应答与 uid_state --")
        for line in json.dumps(sample, ensure_ascii=False, indent=2).splitlines():
            print("   %s" % line)

    if args.live:
        print("")
        print("-- --live 真窗口 --")
        # 真窗口那半段**不装 stub**：把三个边界还原成真身，走真 COM、真 win32gui
        core.uia_tree._lib = ORIG["_lib"]
        core.uia_tree._client_rect = ORIG["_client_rect"]
        core._screen_window_info = ORIG["_screen_window_info"]
        reds, ev, blocked = run_live(core)
        for k, v in ev.items():
            print("   %s = %s" % (k, json.dumps(v, ensure_ascii=False)[:600]))
        for line in reds:
            print("   **红 · %s" % line)
        if blocked:
            print("")
            print("   [环境限制] 本会话没有交互桌面（EnumWindows 看不到任何顶层窗口），真窗口那半段跑不了。")
            print("            **这不算红也不算绿** —— 宁可跑不起来，也不要半个结论。")
            print("            有桌面的会话请重跑：python -X utf8 tests\\_语义屏判据.py --live")
            print("退出前 AgentCtlCtrlBorder* 窗 = %s（%s）" % count_border_windows())
            shutil.rmtree(tmp, ignore_errors=True)
            return 2
        total += len(reds)

    print("")
    print("=" * 100)
    if total:
        print("结论：RED —— 红 %d 条%s"
              % (total, ("（注入模式：注入的定义就是必红，红了正说明判据有判别力）" if kinds else "")))
    else:
        print("结论：GREEN —— (1)默认≤40且视口内可交互全在、omitted 对得上账；"
              "(2)uid 三连稳、标题变了版本变、版本只能回传不能指定；"
              "(3)计时器换字 uid 不变（不爆 STALE）、状态行换字 uid 会变；"
              "(4)source_confidence 多来源取 min；"
              "(5)must_vlm=¬R1∧¬R2∧¬R3 逐格对上、Chromium 窗口 name_trust=low；"
              "(6)契约合法且接线完整；"
              "(7)置信度按 {bbox,text,existence} 分档、聚合=min(existence,text) 且 bbox 不被 text 拖低；"
              "(8)uid 三档 OK/TEXT_CHANGED/STALE 逐档对得上；"
              "(9)offset-suspect ⇒ R2 不成立 ⇒ 落到 VLM；"
              "(10)给了 since 带回 since_echo/version_match/新 screen_version；"
              "S1声明过的 volatile 节点改了必 TEXT_CHANGED、进位恰好归因到它；"
              "S2未声明的倒计时不进位（uid 恒 skey@~，风暴没回流）；"
              "S3声明匹配不到的东西必拒（rc≠0 + unmatched 逐条），挂起须显式；"
              "S4重叠声明全收（一个节点可同时命中多条）；"
              "S5单条声明命中节点数有上限，超宽拒绝；"
              "S6拒绝信封带屏上下文（分得清校验失败与白屏）；"
              "S7首扫就绪判定：暖机有界（最多2次）、screen_ready 如实、未热≠拒绝；"
              "⑥网页类首扫必探两次（重试≥1，取节点更多那趟）；"
              "⑦首扫两次都冷⇒screen_ready=false（按可报对象数判，不看耗时）；"
              "⑧非网页类首扫不加延迟（重试=0）；⑨warmup=false 不探第二次但就绪仍如实；"
              "S8CDP接线：DOM命中⇒R1真且must_vlm=false（R2不成立时单独扛）、"
              "CDP不可达⇒如实false并写进blind_spots（不等于「页面没有」）、"
              "dom段≤60条且逐条带role/name/rect/selector、端口被别人占⇒明确拒绝不抢；"
              "S10动作侧：ctl_screen 的 eid 能直接喂 ctl_click 且身份复核通过、"
              "expect_uid 对得上才点、对不上拒且一个像素没点、"
              "点完 after.uid_state 归因正确（文字变 TEXT_CHANGED、结构变 STALE）、"
              "滚出视口的对象仍可点（复核取超集）；"
              "S9OCR接线：像素里认出且框能当锚⇒R3真且must_vlm=false（R2不成立时单独扛）、"
              "引擎不可用/超时⇒如实false并写进blind_spots（不等于「页面没有」）、"
              "words≤80且omitted.total诚实、dpi_scale在、bbox越界⇒usable=false、"
              "三通道并存时R1/R2/R3各自都报得出来。")
    print("退出前 AgentCtlCtrlBorder* 窗 = %s（%s）" % count_border_windows())
    print("=" * 100)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
