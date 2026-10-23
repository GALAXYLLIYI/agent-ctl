# -*- coding: utf-8 -*-
r"""W4 判据 · act.py 安全批（X4 就绪闸 / X5 列表失败与空列表）

★ 本单要修的两个缺陷（**改前代码里真实存在**，来源：OpenCode 独立静态审计
  `docs\复核_act全量rc出口审计_20221023.md` §九「其他非 FIND 问题」，行号按**代码锚点**定位）：

  X4  `do_click` 的**就绪轮询白做**：`_rdy = _wait_ready(...)` 的返回值**从头到尾没被读过**
      （全文件仅此一处）⇒ 反竞态只看「矩形有没有变」⇒
      **「界面还在动但矩形恰好没变」时照样点下去** ⇒ M7 的就绪轮询（连续 N 帧没变才动手）
      这个护栏形同虚设。修法：让调用点**真的读它**（本单选这条，不删轮询 ——
      删掉会丢功能并把「等 250 ms」退化成固定 sleep，**更慢**且更不安全）。

  X5  `targets` 的**空列表兼任失败**：`targets()` 在两种**失败**下都返回 `[]` ——
      「没通行证」（`_need_perceive` 没过）与「真的没有可交互控件」**分不开**；
      `_uia` 失败则直接抛异常（不在 `targets` 内捕获，`main` 也没有 try）⇒
      ① 失败被报成**成功 + 空列表**（静默假成功）② 崩溃时 rc=1（没约定的码）。
      修法：给 `targets` 一个**显式失败标记**（`TargetsUnavailable` + rc），
      并把「空列表」从成功改判为**明确的「没目标」失败码**（4 找不到控件）。

★ 口径与红线（写死在这里，改产品的人必须照着做）：
  【不许动的东西】
    · **三态语义（M19′）**：`changed is True⇒0 / None⇒5 / False⇒8`，`_rc_from_changed` 一字不动；
    · **M20 甲案触发条件**：core 侧「加长观测」的判定条件与预算一字不动；
    · **F3 的 rc 维持 0**（主控已拍板「画面结论归 core」，见 M19P3 判据 F3-3/F3-7）——
      本判据只**复核**它，不改它；
    · X4/X5 都**不新增错误码**（只用现成的 4 / 5 / 9）。
  【X4 的严格程度】
    · 就绪 = True ⇒ 照旧点（**不许**把正常点击误伤成拒绝）；
    · 就绪 = False（预算内没稳，界面还在动）⇒ **拒绝**（rc=5，fail-closed）；
    · 就绪 = None（没测出来：抓不到画面）⇒ **拒绝**（rc=5）——
      按本仓铁律「unknown 永不当成功」，**没测出来 ≠ 稳了**；
    · `dry` 分支不受影响（演练不许动作、rc=0）。
  【X5 的严格程度】
    · 枚举失败（没通行证 ⇒ 9 / UIA 失败 ⇒ 5）⇒ **显式失败码**，不许再当成功；
    · 枚举成功但**真的没有**可交互控件 ⇒ **4（找不到控件）**，不再是 0
      （工单 W4 口径；审计 P5 的「空列表可以是合法结果」是更软的口径，采纳工单口径，
      并**同时**加「枚举失败 / 已截断」标记，让人机都能分辨两种空）；
    · 枚举成功且有控件 ⇒ 仍是 0（**不许**把正常路径也改成失败）。

★ 本判据自己的铁律：
  · **全程离线、不许碰真 GUI**：不真点击、不真打字、不真滚轮、不抓真屏 ⇒
    会碰到目标世界的入口（`click_at` / `evidence` / `_poll_base` / `_wait_ready` /
    `_baseline_from_env` / `find_control` / `_center` / `_has` / `win_info` /
    `_uia` / `_need_perceive` / `_bands` / `check_target` / `require_no_modal` /
    `ensure_foreground` / `gate.require*` / `log`）**全部打桩**；
  · 动作后的等待旋钮 `AGENTCTL_SETTLE_MS=0`（不许真睡）；闸门 AUTHDIR 指到 %TEMP%
    （判据不许在真仓 `_auth\` 里留痕迹）；
  · **零足迹**：`sys.dont_write_bytecode = True`，临时目录跑完就删（atexit 兜底）。

★ 四跑法（与本仓既有判据同款）：
    python -X utf8 tests\_P3_W4_act安全批_判据.py                 # 正跑（期望 exit=0）
    python -X utf8 tests\_P3_W4_act安全批_判据.py --inject        # 注入自证（期望 exit=1）
    python -X utf8 tests\_P3_W4_act安全批_判据.py --scope product # 产物路（期望 exit=0）
    python -X utf8 tests\_P3_W4_act安全批_判据.py --未知参数       # 未知参数被忽略（与正跑一致）

★ 本判据**默认只跑真源**（`--scope src`）；产物路**在产物与真源 MD5 不同 ⇒ 判「验不了」**
  （不是假绿）—— 等主控统一重生成后补跑。

退出码：0 = 全绿 ｜ 1 = 有红（缺陷仍在 / 注入被逮到）｜ 2 = 环境错 / 注入没让行为退化
"""
from __future__ import annotations

import argparse
import atexit
import io
import os
import re
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ── 判据自用的临时家：闸门日志、注入副本都落这儿 ⇒ 真仓不留痕迹 ────────────
_TMP = tempfile.mkdtemp(prefix="_W4_判据_")
atexit.register(shutil.rmtree, _TMP, True)
os.environ["AGENTCTL_AUTHDIR"] = os.path.join(_TMP, "_auth")
os.environ["AGENTCTL_SETTLE_MS"] = "0"          # 动作后不许真睡
try:
    os.makedirs(os.environ["AGENTCTL_AUTHDIR"], exist_ok=True)
except OSError:
    pass

SRC_ACT = os.path.join(ROOT, "legacy_tools", "_src", "act.py")      # 真源
PRODUCT_ACT = os.path.join(ROOT, "legacy_tools", "act.py")          # 产物（生成物）
FAKE_HWND = 424242
FAKE_INFO = {"hwnd": FAKE_HWND, "title": "靶场记事本", "class": "Notepad",
             "process": "notepad.exe", "rect": [0, 0, 800, 600], "foreground": True}
WIN_RECT = [0, 0, 800, 600]
CTRL_RECT = [100, 200, 300, 400]
CTRL_CENTER = (200, 300)


# ══════════════════════════════════════════════════════════════════════════
# 一 · 定位 act.py（真源 / 产物）
# ══════════════════════════════════════════════════════════════════════════
def md5_of(path):
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def scope_path(scope):
    if scope == "src":
        return "真源", SRC_ACT
    if scope == "product":
        return "产物", PRODUCT_ACT
    return "（其它）", None


def load_act(path, name="act_under_test"):
    """按**路径**加载一份 act.py（注入副本也能测）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_shadow(act_path):
    """把 act.py 所在目录复制到 %TEMP%（现役一个字节都不动）。"""
    base = tempfile.mkdtemp(prefix="_W4_注入_")
    src_dir = os.path.dirname(os.path.abspath(act_path))
    for fn in os.listdir(src_dir):
        if fn.startswith("__"):
            continue
        if fn.endswith(".py") or fn.endswith(".json"):
            shutil.copyfile(os.path.join(src_dir, fn), os.path.join(base, fn))
    return os.path.join(base, os.path.basename(act_path)), base


# ══════════════════════════════════════════════════════════════════════════
# 二 · 桩件（把「世界」打桩；**被测代码一律不桩**）
# ══════════════════════════════════════════════════════════════════════════
class FakeCtrl:
    """一个**没有** Invoke / SelectionItem 的控件 ⇒ do_click 只能走坐标路径。"""
    Name = "靶场按钮"
    ControlTypeName = "Button"

    def GetInvokePattern(self):
        raise Exception("no invoke")

    def GetSelectionItemPattern(self):
        raise Exception("no selectionitem")

    def GetChildren(self):
        return []


class FakeImg:
    """够大、不是 None 的假基线图（只当「有基线」用，不参与判差）。"""
    size = (64, 64)


def stub_world(mod, *, ready=None, rect2=None, clicks=None, logs=None,
               perceive_ok=True, uia_exc=None, tree=None):
    """把 act.py 看到的世界全部换成假货。返回收集到的副作用容器。"""
    clicks = clicks if clicks is not None else []
    logs = logs if logs is not None else []
    st = {"clicks": clicks, "logs": logs, "center_calls": 0}

    def _log(msg):
        logs.append(str(msg))

    def _click_at(x, y):
        clicks.append((x, y))

    def _center(ctrl):
        st["center_calls"] += 1
        r = rect2 if (rect2 is not None and st["center_calls"] >= 2) else CTRL_RECT
        return ((r[0] + r[2]) // 2, (r[1] + r[3]) // 2, list(r), True)

    def _wait_ready(hwnd, base_img, budget_ms=None, need=None, capture=None):
        if ready is None:
            return {"就绪": True, "轮次": 1, "耗时ms": 1.0}
        return dict(ready)

    def _need_perceive(note):
        return bool(perceive_ok)

    def _uia(hwnd):
        if uia_exc is not None:
            raise uia_exc
        return (object(), tree if tree is not None else FakeCtrl())

    mod.log = _log
    mod.click_at = _click_at
    mod._center = _center
    mod._wait_ready = _wait_ready
    mod._need_perceive = _need_perceive
    mod._uia = _uia
    mod.check_target = lambda h: (dict(FAKE_INFO), "")
    mod.require_no_modal = lambda h, why="": None
    mod.ensure_foreground = lambda h, why="", dry=False: None
    mod.win_info = lambda h: {"rect": list(WIN_RECT)}
    mod.find_control = lambda h, name, max_depth=10: FakeCtrl()
    mod._baseline_from_env = lambda: (FakeImg(), {"来源": "客户区"})
    mod._poll_base = lambda h: FakeImg()
    mod.evidence = lambda h, tag="": {"文件": "fake-%s.png" % tag}
    mod._bands = lambda h, geo: {"窗口矩形": list(WIN_RECT)}
    # ★ 动作后的「等生效」轮询与证据回传也打桩 ⇒ 判据**不抓真屏**、不吐 effect JSON
    mod._wait_effect = lambda hwnd, base_img, budget_ms=None, need=None, capture=None: {
        "生效": True, "轮次": 1, "首次变化ms": 0.0, "退化": False}
    mod._emit_effect = lambda eff, hwnd=None: None
    # 闸门：读操作不许真过闸（判据不打真通行证）
    try:
        mod.gate.require = lambda *a, **k: None
        mod.gate.require_any = lambda *a, **k: None
    except Exception:
        pass
    return st


def run_click(mod, *, ready=None, rect2=None, dry=False):
    """跑一次 do_click（坐标路径）。返回 {rc, clicks, logs, exc}。"""
    st = stub_world(mod, ready=ready, rect2=rect2)
    try:
        rc = mod.do_click(FAKE_HWND, "靶场按钮", None, dry)
        exc = None
    except Exception as e:            # 崩溃也是红（rc=1 那类）
        rc, exc = None, "%s: %s" % (type(e).__name__, e)
    return {"rc": rc, "clicks": st["clicks"], "logs": st["logs"], "exc": exc}


def run_targets(mod, *, mode):
    """跑一次 targets(strict=True)。返回 (rc, err_kind, items)。

    ★ 改前（产品还没有 `strict` / `TargetsUnavailable`）⇒ **判缺陷形态**，不崩：
      返回 rc=None + 原因 ⇒ 断言红（先红证据要的是「干净的红」，不是「崩」）。
    """
    tu = getattr(mod, "TargetsUnavailable", None)
    if tu is None:
        return {"rc": None, "exc": "产品里没有 TargetsUnavailable（缺陷形态：失败被空列表兼任）",
                "items": []}
    if not hasattr(mod, "_targets_rc"):
        return {"rc": None, "exc": "产品里没有 _targets_rc（缺陷形态：空列表仍按成功处理）",
                "items": []}
    exc = None
    items = []
    if mode == "denied":
        stub_world(mod, perceive_ok=False)
    elif mode == "uia_fail":
        stub_world(mod, uia_exc=RuntimeError("UIA 挂了"))
    else:
        class _Leaf(FakeCtrl):
            def __init__(self, name, ctype="Button"):
                self.Name = name
                self.ControlTypeName = ctype

            def GetInvokePattern(self):
                return object()

        class _Root(FakeCtrl):
            def __init__(self, children):
                self.Name = "root"
                self.ControlTypeName = "Pane"
                self._kids = children

            def GetChildren(self):
                return self._kids

        kids = [] if mode == "empty" else [_Leaf("靶场按钮")]
        stub_world(mod, tree=_Root(kids))
    try:
        ts, geo = mod.targets(FAKE_HWND, with_geo=True, strict=True)
        items = ts
        rc = mod._targets_rc(ts)
    except tu as e:
        rc, items = e.rc, []
        exc = e
    except Exception as e:            # 没被显式失败标记接住 ⇒ 崩溃（红）
        rc, exc = None, e
    return {"rc": rc, "exc": exc, "items": items}


def run_main_targets(mod, *, mode):
    """跑一次 `act.py targets`（main 的出口）—— 返回 (rc, stdout)。"""
    stub_world(mod, perceive_ok=(mode != "denied"),
               uia_exc=(RuntimeError("UIA 挂了") if mode == "uia_fail" else None))
    argv = sys.argv
    sys.argv = ["act.py", "targets", "--hwnd", str(FAKE_HWND)]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = mod.main()
    except SystemExit as e:           # main 里的 argparse 退出
        rc = e.code
    except Exception as e:
        rc = None
        buf.write("EXC %s: %s" % (type(e).__name__, e))
    finally:
        sys.argv = argv
    return rc, buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════
# 三 · 断言组
# ══════════════════════════════════════════════════════════════════════════
def battery(mod):
    rows = []

    def add(label, want, got, ok, note=""):
        rows.append((label, want, got, bool(ok), note))

    # ── X4 · do_click 的就绪闸（_rdy 真的被读）──────────────────────────
    r = run_click(mod, ready={"就绪": False, "轮次": 3, "耗时ms": 250.0})
    add("X4-1 就绪=False（界面还在动）⇒ rc=5（不许当就绪）", 5,
        r["rc"] if r["exc"] is None else "EXC " + r["exc"],
        r["exc"] is None and r["rc"] == 5, "改前：_rdy 算了却从不看 ⇒ 照点不误")
    add("X4-2 就绪=False ⇒ 不许点下去（click_at 一次都不许调）", 0,
        len(r["clicks"]), len(r["clicks"]) == 0, "点了就是点歪了")
    add("X4-3 就绪=False ⇒ 文案必须说「未就绪/还在动」（不许说「矩形稳定」）", True,
        ("就绪" in "\n".join(r["logs"]) or "还在动" in "\n".join(r["logs"])
         or "没稳" in "\n".join(r["logs"])),
        ("就绪" in "\n".join(r["logs"]) or "还在动" in "\n".join(r["logs"])
         or "没稳" in "\n".join(r["logs"])))

    r = run_click(mod, ready={"就绪": None, "轮次": 1, "耗时ms": 20.0})
    add("X4-4 就绪=None（没测出来）⇒ rc=5（unknown 永不当成功）", 5,
        r["rc"] if r["exc"] is None else "EXC " + r["exc"],
        r["exc"] is None and r["rc"] == 5, "抓不到画面 ≠ 稳了")
    add("X4-5 就绪=None ⇒ 也不许点下去", 0, len(r["clicks"]), len(r["clicks"]) == 0)

    r = run_click(mod, ready={"就绪": True, "轮次": 2, "耗时ms": 30.0})
    add("X4-6 回归：就绪=True ⇒ rc=0（不许把正常点击误伤成拒绝）", 0,
        r["rc"] if r["exc"] is None else "EXC " + r["exc"],
        r["exc"] is None and r["rc"] == 0)
    add("X4-7 回归：就绪=True ⇒ 真的要点下去（click_at 至少 1 次）", "≥1",
        len(r["clicks"]), len(r["clicks"]) >= 1,
        "★ 不写死「恰好 1 次」：现行代码在**具名控件的坐标路径**上会点两次"
        "（几何块里一次 + 尾部重试循环再一次）—— 那是既有行为，本单不碰，只钉「要点」")

    r = run_click(mod, ready={"就绪": True, "轮次": 2, "耗时ms": 30.0}, rect2=[110, 210, 310, 410])
    add("X4-8 回归：矩形变了 ⇒ 仍按老语义拒（rc=5「矩形不稳定」）", 5,
        r["rc"] if r["exc"] is None else "EXC " + r["exc"],
        r["exc"] is None and r["rc"] == 5, "老闸不许被拆")

    r = run_click(mod, ready={"就绪": False, "轮次": 3, "耗时ms": 250.0}, dry=True)
    add("X4-9 回归：dry 分支 ⇒ rc=0（演练恒 0，新闸不许误伤 dry）", 0,
        r["rc"] if r["exc"] is None else "EXC " + r["exc"],
        r["exc"] is None and r["rc"] == 0)
    add("X4-10 回归：dry 分支 ⇒ 不许点（click_at 零次）", 0, len(r["clicks"]),
        len(r["clicks"]) == 0)

    # ── X5 · targets 的失败与空列表 ─────────────────────────────────────
    r = run_targets(mod, mode="denied")
    add("X5-1 没通行证（_need_perceive 失败）⇒ 显式失败 rc=9（不许空列表当成功）", 9,
        r["rc"] if r["rc"] is not None else "EXC " + str(r["exc"]),
        r["rc"] == 9 and r["exc"] is not None,
        "改前：返回 [] ⇒ main 一律 return 0 ⇒ 静默假成功")
    r = run_targets(mod, mode="uia_fail")
    add("X5-2 UIA 枚举失败 ⇒ 显式失败 rc=5（没测出来，不许崩溃 rc=1）", 5,
        r["rc"] if r["rc"] is not None else "EXC " + str(r["exc"]),
        r["rc"] == 5 and r["exc"] is not None, "改前：直接抛异常 ⇒ rc=1（没约定的码）")
    r = run_targets(mod, mode="empty")
    add("X5-3 枚举成功但真的没有控件 ⇒ rc=4（明确的「没目标」，不再是 0）", 4,
        r["rc"] if r["rc"] is not None else "EXC " + str(r["exc"]), r["rc"] == 4)
    r = run_targets(mod, mode="ok")
    add("X5-4 回归：枚举成功且有控件 ⇒ rc=0（不许把正常路径也改成失败）", 0,
        r["rc"] if r["rc"] is not None else "EXC " + str(r["exc"]), r["rc"] == 0)
    add("X5-5 回归：枚举成功且有控件 ⇒ 列表里真有那一个", 1, len(r["items"]),
        len(r["items"]) == 1)

    meta_fn = getattr(mod, "_targets_meta", None)
    if meta_fn is None:
        add("X5-6 元信息：必须有「枚举失败/已截断」标记（人机都能分辨两种空）", True,
            "（产品里没有 _targets_meta）", False, "缺陷形态")
    else:
        meta_ok = meta_fn([{"名称": "x"}], limit=40)
        add("X5-6 元信息：没失败时「枚举失败」= False", False, meta_ok.get("枚举失败"),
            meta_ok.get("枚举失败") is False)
        meta_cut = meta_fn([{"名称": "x"}] * 40, limit=40)
        add("X5-7 元信息：撞上 limit ⇒「已截断」= True（人机都能分辨）", True,
            meta_cut.get("已截断"), meta_cut.get("已截断") is True)
        meta_err = meta_fn([], limit=40, err="未获授权")
        add("X5-8 元信息：失败时「枚举失败」= True 且带原因", "未获授权",
            meta_err.get("失败原因"), meta_err.get("枚举失败") is True
            and meta_err.get("失败原因") == "未获授权")

    rc, out = run_main_targets(mod, mode="denied")
    add("X5-9 main 的 targets 出口：没通行证 ⇒ rc=9（不许 0）", 9,
        rc if rc is not None else "EXC", rc == 9)
    rc, out = run_main_targets(mod, mode="uia_fail")
    add("X5-10 main 的 targets 出口：UIA 失败 ⇒ rc=5（不许 traceback/rc=1）", 5,
        rc if rc is not None else "EXC", rc == 5)
    rc, out = run_main_targets(mod, mode="empty")
    add("X5-11 main 的 targets 出口：真的没有控件 ⇒ rc=4（不再是成功）", 4,
        rc if rc is not None else "EXC", rc == 4)
    add("X5-12 main 的 targets 出口：输出里必须带「枚举失败」标记（人机都能分辨两种空）",
        True, ("枚举失败" in out), ("枚举失败" in out),
        "改前：输出只有「可交互控件: []」，分不清「没列出来」与「列出来是空的」")

    # ── F3 / F5 / F7 复核（★ 只复核，不改：M19P3 已落地并经主控拍板定案）────────
    r = run_keys(mod, changed=None)
    add("F3-1 复核：do_keys 回读失败（changed=None）⇒ 日志必须明说「没测出来」", True,
        ("没测出来" in "\n".join(r["logs"]) or "判不出来" in "\n".join(r["logs"])),
        ("没测出来" in "\n".join(r["logs"]) or "判不出来" in "\n".join(r["logs"])))
    add("F3-2 复核：do_keys 回读失败 ⇒ rc 维持 0（主控拍板：画面结论归 core）", 0,
        r["rc"] if r["exc"] is None else "EXC " + r["exc"],
        r["exc"] is None and r["rc"] == 0)
    add("F5-3 复核：main 的 read 失败 ⇒ 不许再报成功（rc≠0）", True,
        r["read_rc"] not in (0, None), r["read_rc"] not in (0, None))
    add("F7-4 复核：do_type 前置读失败 ⇒ 不许盲发（网页表单模式不许进）", True,
        r["type_blind"] is False, r["type_blind"] is False,
        "读失败时走 --force-send 盲打会污染真实文档")
    return rows


def run_keys(mod, changed=None):
    """跑一次 do_keys（只读证据图 ⇒ 指纹取不到 ⇒ changed=None）。"""
    logs = []
    mod.log = lambda m: logs.append(str(m))
    mod.evidence = lambda h, tag="": {"文件": ""}          # 空文件名 ⇒ 指纹取不到
    mod._img_fp = lambda p: None
    mod.send_vk = lambda vk, down: None
    mod.send_vk_scan = lambda vk, down: None
    mod.check_target = lambda h: (dict(FAKE_INFO), "")
    mod.gate.require = lambda *a, **k: None
    mod.gate.require_any = lambda *a, **k: None
    try:
        rc = mod.do_keys(FAKE_HWND, vk=0x41, times=1)
        exc = None
    except Exception as e:
        rc, exc = None, e
    out = {"rc": rc, "logs": logs, "exc": exc, "read_rc": None, "type_blind": None}
    # —— F5 复核：main 的 read 出口（read_doc 失败）——
    mod.read_doc = lambda h: (None, None, "读不到文本：模拟")
    mod.read_selection = lambda h: None
    argv = sys.argv
    sys.argv = ["act.py", "read", "--hwnd", str(FAKE_HWND)]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            out["read_rc"] = mod.main()
    except SystemExit as e:
        out["read_rc"] = e.code
    except Exception:
        out["read_rc"] = None
    finally:
        sys.argv = argv
    # —— F7 复核：do_type 前置读失败 ⇒ 不许盲发 ——
    sent = []
    mod.read_doc = lambda h: (object(), None, "读不到文本：模拟")   # 控件在、读不出来
    mod.send_text = lambda t, **k: sent.append(t)
    mod.ensure_foreground = lambda h, why="", dry=False: None
    mod.require_no_modal = lambda h, why="": None
    try:
        mod.do_type(FAKE_HWND, "靶字甲", force_send=True)
        out["type_blind"] = bool(sent)
    except Exception:
        out["type_blind"] = True
    return out


# ══════════════════════════════════════════════════════════════════════════
# 四 · 注入表（把修好的形态**打回缺陷形态**）
# ══════════════════════════════════════════════════════════════════════════
INJECT = {
    "X4": (
        re.compile(
            r"(?ms)^                # ★ W4（2026-10-24）X4：`_rdy` 原来\*\*算了却从不看\*\* ——.*?"
            r"^                if _rdy_ok is not True:\n"
            r".*?"
            r"^                              \"      下一步：等界面停下（或先 scroll 到位）再重试。\"\)\n"
        ),
        "",
        re.compile(r"(?m)^                _rdy_ok = \(_rdy or \{\}\)\.get\(\"就绪\"\)\n"),
    ),
    "X5a": (
        re.compile(
            r"(?ms)^    if not ts:\n"
            r"        return 4\n"
            r"    return 0\n"
        ),
        "    return 0\n",
        re.compile(r"(?m)^    if not ts:\n"),
    ),
    "X5b": (
        re.compile(
            r"(?ms)^        if strict:\n"
            r"            raise TargetsUnavailable\(\"UIA 枚举失败：%s\" % \(e,\), 5, \"uia_failed\"\)\n"
        ),
        "",
        re.compile(r"(?m)^        if strict:\n            raise TargetsUnavailable"),
    ),
}

INJECT_WATCH = {
    "X4": "X4-1 就绪=False（界面还在动）⇒ rc=5（不许当就绪）",
    "X5a": "X5-3 枚举成功但真的没有控件 ⇒ rc=4（明确的「没目标」，不再是 0）",
    "X5b": "X5-2 UIA 枚举失败 ⇒ 显式失败 rc=5（没测出来，不许崩溃 rc=1）",
}


def revert(path, defect):
    """把副本里的某一处修复**打回原样**（缺陷态）。返回 (成功?, 说明)。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    pat, repl, already = INJECT[defect]
    new, n = pat.subn(repl, text, count=1)
    if n == 0:
        if already.search(text):
            return True, "副本本来就是缺陷形态（无需回退）"
        return False, "注入点没找到（既不是修好的形态，也不是缺陷形态）"
    out = new.replace("\n", nl) if nl != "\n" else new
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    return True, "已回退成缺陷形态"


# ══════════════════════════════════════════════════════════════════════════
# 五 · 主流程
# ══════════════════════════════════════════════════════════════════════════
def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for label, want, got, ok, note in rows:
        print("  %-58s 期望=%-14s 实得=%-22s %s%s"
              % (label[:58], str(want)[:14], str(got)[:22],
                 "PASS" if ok else "FAIL", ("  · " + note) if note else ""), flush=True)


def do_normal(scope, act_path=None):
    unverifiable = []
    if act_path:
        targets = [("指定", os.path.abspath(act_path))]
    else:
        role, p = scope_path(scope)
        targets = [(role, p)]
    for role, path in targets:
        print("\n[被测] %-4s %s" % (role, path), flush=True)
        if not path or not os.path.isfile(path):
            print("       【验不了】文件不在 ⇒ 验不了", flush=True)
            unverifiable.append("%s：文件不在" % role)
            continue
        print("       MD5=%s  %d 字节" % (md5_of(path), os.path.getsize(path)), flush=True)
        # ★ 产物与真源不同步 ⇒ 该路**验不了**（不是假绿）：本单不许跑 gen_legacy，
        #   等主控统一重生成后补跑。
        if role == "产物" and os.path.isfile(SRC_ACT) and md5_of(path) != md5_of(SRC_ACT):
            print("       【验不了】产物尚未重生成（与真源 MD5 不同）⇒ "
                  "等主控统一重生成后补跑", flush=True)
            unverifiable.append("产物：尚未重生成（等主控统一重生成后补跑）")
            continue
        mod = load_act(path, name="act_under_test_%s" % role)
        buf = io.StringIO()
        with redirect_stdout(buf):          # 被测代码的 print 不许污染判据输出
            rows = battery(mod)
        show_rows("%s · 断言组" % role, rows)
        bad = [r for r in rows if not r[3]]
        if bad:
            print("\n【红】%s：%d 条不成立" % (role, len(bad)), flush=True)
            for r in bad:
                print("   · %s：期望 %s，实得 %s" % (r[0], r[1], r[2]), flush=True)
            return 1
    print()
    print("=" * 78, flush=True)
    if unverifiable:
        print("【验不了】%d 路：%s" % (len(unverifiable), unverifiable), flush=True)
        print("【绿】其余全绿（★ 有验不了项，不是假绿）", flush=True)
        return 0
    print("【绿】全过", flush=True)
    return 0


def do_inject(act_path=None):
    print("== W4 判据 · 注入自证（把修好的形态打回缺陷形态）==", flush=True)
    path = act_path or SRC_ACT
    shadow, base = make_shadow(path)
    print("注入副本（现役不动）：%s" % shadow, flush=True)
    caught = []
    try:
        for defect in ("X4", "X5a", "X5b"):
            ok, why = revert(shadow, defect)
            print("\n[注入 %s] %s" % (defect, why), flush=True)
            if not ok:
                print("  ★ 注入点没找到 ⇒ 判据可能与产品脱节", flush=True)
                continue
            mod = load_act(shadow, name="act_inject_%s" % defect)
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rows = battery(mod)
            except Exception as e:
                print("  注入副本直接崩了：%s: %s" % (type(e).__name__, e), flush=True)
                rows = []
            bad = [r for r in rows if not r[3]]
            watch = INJECT_WATCH[defect]
            hit = [r for r in bad if r[0] == watch]
            if hit:
                caught.append("%s：%s（实得 %s）" % (defect, watch, hit[0][2]))
                print("  ⇒ 该条变红（期望）：%s（实得 %s）" % (watch, hit[0][2]), flush=True)
            else:
                print("  ⇒ ★ 没有变红：%s" % watch, flush=True)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()
    print("=" * 78, flush=True)
    if caught:
        print("【红】注入被逮到（%s）⇒ exit=1" % "；".join(caught), flush=True)
        print("★ 判据有证明力：把修复打回缺陷形态，它当场就红。", flush=True)
        return 1
    print("【✗崩溃】注入**没有**让行为退化 ⇒ 判据没牙 ⇒ exit=2", flush=True)
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红，exit=1）")
    ap.add_argument("--scope", default="src", choices=("src", "product"),
                    help="src=真源（默认）；product=产物（未重生成 ⇒ 验不了）")
    ap.add_argument("--act", default=None, help="跑指定的一份 act.py")
    # ★ 未知参数**忽略**（与本仓既有判据同款：第 ④ 跑法「未知参数被忽略 ⇒ 与正跑一致」）
    args, _unknown = ap.parse_known_args()
    print("== W4 判据 · act.py 安全批（X4 就绪闸 / X5 列表失败与空列表）==", flush=True)
    print("[口径] X4：就绪=False/None ⇒ 拒（rc=5）；就绪=True ⇒ 照点（不许误伤）", flush=True)
    print("[口径] X5：枚举失败 ⇒ 9/5；真的没有 ⇒ 4（不再是 0）；有控件 ⇒ 0", flush=True)
    print("[口径] F3/F5/F7 只复核（M19P3 已落地并经主控拍板定案），本单不改", flush=True)
    print("[模式] %s" % ("注入自证（--inject）" if args.inject
                        else ("正跑：%s" % args.scope)), flush=True)
    return do_inject(args.act) if args.inject else do_normal(args.scope, args.act)


if __name__ == "__main__":
    raise SystemExit(main())
