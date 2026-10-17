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

跑法：
    python -X utf8 tests\_语义屏判据.py                # 合成夹具（无桌面也能跑）
    python -X utf8 tests\_语义屏判据.py --live         # 真开 tkinter 窗口跑（需交互桌面）
    python -X utf8 tests\_语义屏判据.py --inject guarantee  # 必红①（拿掉必进保证）
    python -X utf8 tests\_语义屏判据.py --inject text_key    # 必红③（uid 去掉 text_key）
    python -X utf8 tests\_语义屏判据.py --inject version      # 必红②（screen_version 钉死）
    python -X utf8 tests\_语义屏判据.py --inject all          # 三条一起注，逐条报红

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

CLIENT = [0, 0, 1280, 900]          # 夹具客户区
FAKE_HWND = 424242                  # 合成句柄：界面上没有这个窗口，所以读不到也无所谓

# 夹具规模。**故意造得对「必进保证」不利**：
#   3 块 query 命中的大文本各占客户区 88% ⇒ 打分 0.638，压过小按钮的 0.601。
#   于是「纯按分取前 40」会必然裁掉至少一个视口内可交互按钮 ——
#   去掉必进保证就一定报红，这才叫这条断言有判别力。
N_BTN = 38                          # 视口内可交互按钮
N_QTEXT = 3                         # 视口内 query 命中的大文本
N_TEXT = 10                         # 视口内普通文本
N_OFF = 10                          # 视口外按钮
QUERY = "题干"
EXPECT_TOTAL = N_BTN + N_QTEXT + N_TEXT + 2 + N_OFF      # 63（+2 = 计时器 + 状态）
EXPECT_IN_VIEW = N_BTN + N_QTEXT + N_TEXT + 2             # 53

REQUIRED_OBJ_FIELDS = ("id", "uid", "label", "role", "rect", "in_view", "interactive",
                       "actions", "source", "source_confidence", "name_trust",
                       "volatile", "score")


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

    def set_title(self, t):
        self.title = t

    def advance(self):
        """推进一格：计时器与状态行**同时**换字（一个 volatile、一个不 volatile）。"""
        self.tick += 1

    @property
    def timer_label(self):
        return TIMER_LABELS[min(self.tick, len(TIMER_LABELS) - 1)]

    @property
    def status_label(self):
        return STATUS_LABELS[min(self.tick, len(STATUS_LABELS) - 1)]

    def button_labels(self):
        return ["选项格%02d" % (i + 1) for i in range(N_BTN)]

    def root(self):
        inner = []
        rid = [100, 1]
        # (1) query 命中的**大**文本（面积占客户区 88%）：打分 0.638，压过小按钮的 0.601
        for i in range(N_QTEXT):
            rid[0] += 1
            inner.append(_Ctrl("PaneControl", [20, 40, 1260, 860],
                               "题干第%d段：下列说法正确的是" % (i + 1), rid=list(rid)))
        # (2) 视口内可交互按钮（小）
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

INJECT = {
    "guarantee": ("拿掉必进保证（纯按分取前 top）", "append", INJECT_GUARANTEE),
    "text_key": ("uid 里去掉 text_key（只剩结构路径）", "append", INJECT_TEXT_KEY),
    "version": ("screen_version 钉死不变", "append", INJECT_VERSION),
}


def build_injected(tmp, kinds):
    with io.open(CORE, "r", encoding="utf-8") as f:
        src = f.read()
    notes = []
    for k in kinds:
        desc, mode, snippet = INJECT[k]
        if mode == "append":
            src = src + snippet
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
    shutil.copyfile(CONTRACT, os.path.join(tmp, "contract", "tools.json"))
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
    t1 = by_label(sc1.get("objects") or [], "剩余")
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
    t2 = by_label(sc2.get("objects") or [], "剩余")
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
                     ("(6) 契约与接线", lambda: check_contract(core))):
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
              "(6)契约合法且接线完整。")
    print("退出前 AgentCtlCtrlBorder* 窗 = %s（%s）" % count_border_windows())
    print("=" * 100)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())