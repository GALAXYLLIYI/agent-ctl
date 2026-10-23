# -*- coding: utf-8 -*-
r"""M20 判据 · act 干跑（dry-run）语义：**演练只算不做**（X1 / X2 / X3）

★ 本判据要修的三个缺陷（**改前代码里真实存在**，来源：OpenCode 独立静态审计 §九
  「其他非 FIND 问题（同批建议修）」，行号按**代码锚点**定位，不按报告里的行号硬套）：

  X1  `do_type` 的 `--force-send` 段（改前 act.py:3401-3404）：
      `b0`/`b1` 已经用 `{} if dry else evidence(...)` 护住了，
      但**发送本身没有 `if not dry:`** ⇒ `do_type --force-send --dry-run`
      会**真的把字打进目标窗口** ⇒ 演练语义（docstring 的「只算不做」）被破。**安全级**。

  X2  `do_scroll`（改前 act.py:3122）：
      `wheel` 被 dry 护住了，`user32.SetCursorPos(x, y)` 没有
      ⇒ `--dry-run` 会**把用户光标挪到窗口中心**。**安全级**。

  X3  `do_scroll`（改前 act.py:3113，路线 P 兜底）：
      `_uia` 超时/失败时把 `root` 置 `None`（注释写「直接走路线 P」），
      但这里**无条件** `_center(root)` ⇒ `ctrl.BoundingRectangle` → AttributeError
      ⇒ 进程 rc=1 ⇒ **路线 P 的前台兜底永远走不到**。

★★ ★★ **本判据的口径与红线**（写死在这里，改产品的人必须照着做）：

  【口径·dry 语义边界】
    · `--dry-run` = **只报告不动作**：演练不许产生**任何**对目标世界的副作用
      —— 不许打字、不许移动鼠标光标、不许发滚轮、不许抓真屏取证。
    · 非 dry（`--dry-run` 不带）= **行为一字不许改**：调用次数、顺序、参数、
      返回码口径、三态映射（True⇒0 / None⇒5 / False⇒8）全部保持原样。
    · ★ 本判据的「红线」就一句：**dry 分支不许动作，非 dry 分支不许变**。
      修 dry 语义 **不许**顺手动非 dry 的行为；修非 dry **不许**顺手动 dry 的返回码。

  【口径·X3 崩溃】
    · `root=None`（UIA 超时/失败）时**不许抛异常**（AttributeError ⇒ rc=1 是崩溃，不是拒绝）；
      必须有**可解释的返回/兜底**：走「窗口矩形中心」的前台兜底（rc=0），
      或给出 rc=5 的坐标类拒绝 —— **两者都可接受，崩溃不可接受**。

  【本判据自己的铁律】
    · **全程离线、不许碰真 GUI**：不许真打字、不许真移鼠标、不许真滚轮、不许抓真屏。
      ⇒ 所有会碰到目标世界的入口（`send_text` / `send_text_scan` / `wheel` /
        `user32.SetCursorPos` / `evidence` / `_post_wheel` / `read_doc` / `_uia` /
        `check_target` / `require_no_modal` / `ensure_foreground` / `gate.require` / `log`）
        **全部打桩**；动作后的等待旋钮 `AGENTCTL_SETTLE_MS=0`（不许真睡）；
        闸门的 AUTHDIR 指到 %TEMP%（判据不许在真仓 `_auth\` 里留痕迹）。
    · **零足迹**：`sys.dont_write_bytecode = True`（不生成 __pycache__），
      临时目录跑完就删（真仓不留任何副产物）。

★ 本判据**默认跑三处**（真源 / 产物 / live）⇒ 三处不一致会直接报红 ——
  「三处同步」这件事因此也归判据管，而不是靠人记得跑生成器。

★ 本判据是**缺陷驱动**的：
  · **改前跑 ⇒ 必须红**（缺陷在）—— 这证明判据有牙；
  · 改后跑 ⇒ 必须绿；
  · `--inject`（把某一处修复**打回原样**）⇒ 必须红 ⇒ 证明判据抓得住这个病。

用法：
    python -X utf8 tests\_P3_M20_act干跑语义_判据.py             # 正跑（期望 exit=0）
    python -X utf8 tests\_P3_M20_act干跑语义_判据.py --inject    # 注入自证（期望 exit=1）
    python -X utf8 tests\_P3_M20_act干跑语义_判据.py --act <路径> # 只跑某一份 act.py

退出码：
    0 = 全绿 ｜ 1 = 有红（缺陷仍在 / 注入被逮到）｜ 2 = 环境错 / 注入没让行为退化（判据没牙）
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile

# ★ 判据要 import 三份 act.py ⇒ 顺手会在它们旁边生成 __pycache__\*.pyc。
#   ⇒ 落笔前先关掉字节码写入 ⇒ 判据**零足迹**（跑完不留任何副产物）。
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ── 判据自用的临时家：闸门日志、注入副本都落这儿 ⇒ 真仓不留痕迹 ────────────
#   ★ atexit 兜底：判据若被**当模块导入**（而不是当脚本跑），main() 的 finally 不会执行，
#     临时目录就会留在 %TEMP% 里没人回收 ⇒ 这里用 atexit 让它随进程退出一起走。
import atexit

_TMP = tempfile.mkdtemp(prefix="_M20_干跑_判据_")
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
WIN_CENTER = (400, 300)          # 窗口矩形 [0,0,800,600] 的中心
CTRL_RECT = (100, 200, 300, 400)  # 假 UIA 根控件的矩形
CTRL_CENTER = (200, 300)         # 控件矩形的中心
TEXT = "靶字甲"


# ══════════════════════════════════════════════════════════════════════════
# 一 · 定位「三处 act.py」
# ══════════════════════════════════════════════════════════════════════════
def live_act_path():
    """live（真在跑的那份）—— 指针文件 `build\\local_legacy_home.txt`，**不写死盘符**。"""
    ptr = os.path.join(ROOT, "build", "local_legacy_home.txt")
    try:
        with open(ptr, "r", encoding="utf-8") as f:
            home = f.read().strip()
    except OSError:
        return None
    if not home:
        return None
    p = os.path.join(home.replace("/", os.sep), "act.py")
    return p if os.path.isfile(p) else None


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def three_copies():
    """三处：真源 / 产物 / live。返回 [(角色, 路径)]（不存在的跳过）。"""
    out = []
    for role, p in (("真源", SRC_ACT), ("产物", PRODUCT_ACT), ("live", live_act_path())):
        if p and os.path.isfile(p):
            out.append((role, p))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 二 · 打桩（★ 本判据不许碰真 GUI）
# ══════════════════════════════════════════════════════════════════════════
class _FakeRect(object):
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b


class _FakeRoot(object):
    """假的 UIA 根控件：只有 BoundingRectangle 与 GetChildren（够 `_center`/`walk` 用）。"""

    ControlTypeName = "PaneControl"

    def __init__(self, rect):
        self.BoundingRectangle = _FakeRect(*rect)

    def GetChildren(self):
        return []


class _FakeUser32(object):
    """只把 `SetCursorPos` 换成记录器；其余属性照旧代理给真 user32。

    ★ 为什么要代理而不是整个替掉：act 里还有别处用 user32（枚举窗口等），
      判据只该拦「挪光标」这一个动作，不该顺手改别的行为。
    """

    def __init__(self, real):
        self._real = real
        self.cursor_moves = []

    def SetCursorPos(self, x, y):
        self.cursor_moves.append((int(x), int(y)))
        return 1

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Env(object):
    """一次性打桩：把 act 的关键入口换成桩，跑完复原（原样还回去，不留痕）。"""

    def __init__(self, mod, **extra):
        self.mod = mod
        self.extra = extra
        self.logs = []
        self.sent = []            # send_text 调用记录
        self.sent_scan = []       # send_text_scan 调用记录
        self.wheels = []          # wheel 调用记录
        self.posted = []          # _post_wheel 调用记录
        self.shots = []           # evidence 调用记录（抓屏）
        self.user32 = _FakeUser32(mod.user32)
        self._names = []
        self._gate_old = None

    def _set(self, name, value):
        setattr(self.mod, name, value)
        self._names.append(name)

    def __enter__(self):
        m = self.mod
        self._old = {n: getattr(m, n) for n in
                     ("check_target", "require_no_modal", "ensure_foreground", "log",
                      "user32", "wheel", "_post_wheel", "_img_fp")}
        self._set("check_target", lambda hwnd: (dict(FAKE_INFO), None))
        self._set("require_no_modal", lambda hwnd, what: None)
        self._set("ensure_foreground", lambda hwnd, what, note="", dry=False: None)
        self._set("log", lambda msg: self.logs.append(str(msg)))
        self._set("user32", self.user32)
        self._set("wheel", lambda delta: self.wheels.append(delta))
        self._set("_post_wheel", lambda hwnd, info, amount: self.posted.append(amount) or False)
        self._set("_img_fp", lambda p: p)
        for name, value in self.extra.items():
            self._old[name] = getattr(m, name)
            self._set(name, value)
        self._gate_old = m.gate.require
        m.gate.require = lambda *a, **k: None
        return self

    def __exit__(self, *exc):
        for name in self._names:
            setattr(self.mod, name, self._old[name])
        self.mod.gate.require = self._gate_old
        return False


def _mk_evidence(same=False):
    """evidence 桩：返回**假路径**（不落盘、不抓屏）。same=True ⇒ 前后同图 ⇒ 画面没变。"""
    tag = {"n": 0}

    def _f(hwnd, name):
        tag["n"] += 1
        return {"文件": "shot_same.png" if same else "shot_%s.png" % name}
    return _f


# ── 跑一次 `do_type --force-send` ─────────────────────────────────────────
def run_type_forcesend(mod, *, dry=False, scan_code=False, same_shot=False):
    """返回 dict：rc / exc / sent / sent_scan / logs / shots。"""
    env = _Env(mod)
    out = {}

    def _read_doc(hwnd):
        # ★ 2026-10-23 主控修正：桩的措辞**与真源 `read_doc` 逐字对齐** =「找不到文本编辑区」。
        #   原因：M19P3 给 `read_doc` 的失败原因分了因（`_read_fail_rc` 按**前缀**匹配四种原因：
        #   未获授权 / 目标窗口不响应 / 找不到文本编辑区 / 读不到文本）⇒ 桩若自造第三种措辞，
        #   会被归成「未知原因」⇒ 读失败 ⇒ 拦下盲打 ⇒ **X1 场景被偷换成了另一个场景**，
        #   于是本判据与 M19P3 判据互相打架（表现为 X1 组 15 项红）。
        #   ★ 真源的失败措辞**恰好只有这四种**（act.py read_doc 的四个 return）⇒ 分因覆盖完整。
        #   ⚠ 本次只改**桩的字符串**，**一个字断言都没动**（元判据①：断言不许被削弱）。
        return (None, None, "找不到文本编辑区")

    def _send_text(text, **kw):
        env.sent.append(text)

    def _send_text_scan(text, **kw):
        env.sent_scan.append(text)
        return (len(text), [])

    def _evidence(hwnd, name):
        env.shots.append(name)
        return {"文件": "shot_same.png" if same_shot else "shot_%s.png" % name}

    env.extra = {"read_doc": _read_doc, "send_text": _send_text,
                 "send_text_scan": _send_text_scan, "evidence": _evidence}
    with env:
        try:
            rc = mod.do_type(FAKE_HWND, TEXT, dry=dry, force_send=True, scan_code=scan_code)
            exc = None
        except Exception as e:                      # 崩了也是一条红（不许当成功）
            rc, exc = None, "%s: %s" % (type(e).__name__, e)
    out.update(rc=rc, exc=exc, sent=list(env.sent), sent_scan=list(env.sent_scan),
               logs=list(env.logs), shots=list(env.shots))
    return out


# ── 跑一次 `do_scroll` ────────────────────────────────────────────────────
def run_scroll(mod, *, dry=False, root_mode="none", amount=-3):
    """root_mode：'none' = UIA 超时（root=None，走路线 P）；'ok' = 有根控件。

    返回 dict：rc / exc / cursor_moves / wheels / logs / shots / posted。
    """
    env = _Env(mod)
    out = {}

    def _uia(hwnd):
        if root_mode == "none":
            raise mod._UiATimeout("判据桩：UIA 建构超时")
        return (None, _FakeRoot(CTRL_RECT))

    def _evidence(hwnd, name):
        env.shots.append(name)
        return {"文件": "shot_%s.png" % name}

    env.extra = {"_uia": _uia, "evidence": _evidence}
    with env:
        try:
            rc = mod.do_scroll(FAKE_HWND, amount, dry=dry)
            exc = None
        except Exception as e:                      # 崩了也是一条红（不许当成功）
            rc, exc = None, "%s: %s" % (type(e).__name__, e)
    out.update(rc=rc, exc=exc, cursor_moves=list(env.user32.cursor_moves),
               wheels=list(env.wheels), logs=list(env.logs),
               shots=list(env.shots), posted=list(env.posted))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 三 · 断言组
# ══════════════════════════════════════════════════════════════════════════
def battery(mod):
    """跑完整套断言。返回 (rows, key)。row = (标签, 期望, 实得, 通过?, 说明)。"""
    out = []

    def add(label, want, got, note=""):
        out.append((label, want, got, got == want, note))

    # ── X1 · do_type --force-send · dry 语义 ─────────────────────────────
    r = run_type_forcesend(mod, dry=True)
    add("X1 dry(--force-send) ⇒ 不许真的把字打出去", True, (len(r["sent"]) == 0),
        "送出 %d 次%s" % (len(r["sent"]), ("（★改前这里真的打了）" if r["sent"] else "")))
    add("X1 dry(--force-send) ⇒ 不许崩", True, (r["exc"] is None), r["exc"] or "")
    add("X1 dry(--force-send) ⇒ rc=0（演练恒 0）", 0,
        r["rc"] if r["exc"] is None else "EXC " + r["exc"])
    add("X1 dry(--force-send) ⇒ 日志必须写「演练」", True,
        ("演练" in "\n".join(r["logs"])))
    add("X1 dry(--force-send) ⇒ 不许抓真屏取证", True, (len(r["shots"]) == 0),
        "抓了 %d 次" % len(r["shots"]))

    r = run_type_forcesend(mod, dry=True, scan_code=True)
    add("X1 dry(--force-send --scan-code) ⇒ 扫描码注入也不许发", True,
        (len(r["sent_scan"]) == 0 and len(r["sent"]) == 0),
        "扫描码 %d 次 / 逐字 %d 次" % (len(r["sent_scan"]), len(r["sent"])))

    # 回归：非 dry 路径**不许被改坏**
    r = run_type_forcesend(mod, dry=False)
    add("X1 回归：非 dry(--force-send) ⇒ 仍要真的打一次", True, (len(r["sent"]) == 1),
        "送出 %d 次" % len(r["sent"]))
    add("X1 回归：非 dry(--force-send) ⇒ 不许崩", True, (r["exc"] is None), r["exc"] or "")
    add("X1 回归：非 dry --force-send，画面变了 ⇒ rc=0", 0,
        r["rc"] if r["exc"] is None else "EXC " + r["exc"])
    r = run_type_forcesend(mod, dry=False, same_shot=True)
    add("X1 回归：非 dry --force-send，画面没变 ⇒ rc=8（确认没变）", 8,
        r["rc"] if r["exc"] is None else "EXC " + r["exc"])

    # ── X2 · do_scroll · dry 语义 ────────────────────────────────────────
    r = run_scroll(mod, dry=True, root_mode="ok")
    add("X2 dry 滚动 ⇒ 不许移动鼠标光标", True, (len(r["cursor_moves"]) == 0),
        "挪了 %d 次%s" % (len(r["cursor_moves"]),
                          ("（★改前演练真的挪了光标）" if r["cursor_moves"] else "")))
    add("X2 dry 滚动 ⇒ 不许发滚轮", True, (len(r["wheels"]) == 0),
        "发了 %d 次" % len(r["wheels"]))
    add("X2 dry 滚动 ⇒ 不许崩", True, (r["exc"] is None), r["exc"] or "")
    add("X2 dry 滚动 ⇒ rc=0", 0, r["rc"] if r["exc"] is None else "EXC " + r["exc"])
    add("X2 dry 滚动 ⇒ 日志必须写「演练」", True, ("演练" in "\n".join(r["logs"])))
    add("X2 dry 滚动 ⇒ 不许抓真屏取证", True, (len(r["shots"]) == 0),
        "抓了 %d 次" % len(r["shots"]))

    # 回归：非 dry 路径**不许被改坏**
    r = run_scroll(mod, dry=False, root_mode="ok")
    add("X2 回归：非 dry 滚动 ⇒ 仍要挪一次光标", True, (len(r["cursor_moves"]) == 1),
        "挪了 %d 次" % len(r["cursor_moves"]))
    add("X2 回归：非 dry 滚动 ⇒ 仍要发一次滚轮", True, (len(r["wheels"]) == 1),
        "发了 %d 次" % len(r["wheels"]))
    add("X2 回归：非 dry 滚动 ⇒ 滚轮量 = 120×amount（方向不许反）", -360,
        r["wheels"][0] if len(r["wheels"]) == 1 else r["wheels"])
    add("X2 回归：非 dry 滚动 ⇒ rc=0", 0, r["rc"] if r["exc"] is None else "EXC " + r["exc"])
    add("X2 回归：非 dry 滚动 ⇒ 不许崩", True, (r["exc"] is None), r["exc"] or "")

    # ── X3 · do_scroll · root=None（路线 P 兜底） ─────────────────────────
    r = run_scroll(mod, dry=False, root_mode="none")
    add("X3 root=None（UIA 超时）⇒ 不许崩", True, (r["exc"] is None),
        r["exc"] or ("（★改前这里 AttributeError ⇒ rc=1）" if r["exc"] else ""))
    add("X3 root=None ⇒ 返回码必须可解释（0 或 5，不许是异常）", True,
        (r["rc"] in (0, 5)) if r["exc"] is None else False,
        "rc=%s" % (r["rc"],))
    add("X3 root=None ⇒ 路线 P 的前台兜底要走到（挪一次光标）", True,
        (len(r["cursor_moves"]) == 1),
        "挪了 %d 次" % len(r["cursor_moves"]))
    add("X3 root=None ⇒ 落点用**窗口矩形**中心 (400,300)", WIN_CENTER,
        r["cursor_moves"][0] if len(r["cursor_moves"]) == 1 else r["cursor_moves"])
    add("X3 root=None ⇒ 前台兜底要真的发一次滚轮", True, (len(r["wheels"]) == 1),
        "发了 %d 次" % len(r["wheels"]))
    add("X3 root=None ⇒ rc=0", 0, r["rc"] if r["exc"] is None else "EXC " + r["exc"])

    # 回归：root 不为 None 时**不许**改成用窗口矩形（正常路径不许被改坏）
    r = run_scroll(mod, dry=False, root_mode="ok")
    add("X3 回归：root 正常时 ⇒ 落点用**控件矩形**中心 (200,300)", CTRL_CENTER,
        r["cursor_moves"][0] if len(r["cursor_moves"]) == 1 else r["cursor_moves"])

    return out, {r[0]: r for r in out}


# ══════════════════════════════════════════════════════════════════════════
# 四 · 加载与注入（把修复打回原样 ⇒ 判据必须转红）
# ══════════════════════════════════════════════════════════════════════════
def load_act(path, name="act_under_test"):
    """按**路径**加载一份 act.py（三处各能各测，注入副本也能测）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_shadow(act_path):
    """把 act.py 所在目录复制到 %TEMP%（现役一个字节都不动）。"""
    base = tempfile.mkdtemp(prefix="_M20_注入_")
    src_dir = os.path.dirname(os.path.abspath(act_path))
    for fn in os.listdir(src_dir):
        if fn.startswith("__"):
            continue
        if fn.endswith(".py") or fn.endswith(".json"):
            shutil.copyfile(os.path.join(src_dir, fn), os.path.join(base, fn))
    return os.path.join(base, os.path.basename(act_path)), base


# ★ 注入表：每一项 = (把「修好的形态」匹配出来的正则, 打回「缺陷形态」的文本,
#                   「本来就是缺陷形态」的判据正则)
INJECT = {
    "X1": (
        re.compile(
            r"(?m)^        if not dry:\n"
            r"            if scan_code:\n"
            r"                n_scan, fb_chars = send_text_scan\(text\)\n"
            r"            else:\n"
            r"                send_text\(text\)\n"
        ),
        "        if scan_code:\n"
        "            n_scan, fb_chars = send_text_scan(text)\n"
        "        else:\n"
        "            send_text(text)\n",
        re.compile(r"(?m)^        if scan_code:\n"
                   r"            n_scan, fb_chars = send_text_scan\(text\)\n"
                   r"        else:\n"
                   r"            send_text\(text\)\n"),
    ),
    "X2": (
        re.compile(
            r"(?ms)^    if not dry:\n"
            r"        user32\.SetCursorPos\(x, y\)\n"
            r"        time\.sleep\(0\.12\)\n"
            r".*?"
            r"^        wheel\(120 \* amount\)\n"
        ),
        "    user32.SetCursorPos(x, y)\n"
        "    time.sleep(0.12)\n"
        "    if not dry:\n"
        "        wheel(120 * amount)\n",
        re.compile(r"(?m)^    user32\.SetCursorPos\(x, y\)\n"),
    ),
    "X3": (
        re.compile(
            r"(?ms)^    if root is None:\n"
            r".*?"
            r"^    else:\n"
            r"        x, y, rr, ok_rect = _center\(root\)\n"
        ),
        "    x, y, rr, ok_rect = _center(root)\n",
        re.compile(r"(?m)^    x, y, rr, ok_rect = _center\(root\)\n"),
    ),
}

# 每一处缺陷对应的「必须变红」的断言标签前缀
INJECT_WATCH = {
    "X1": "X1 dry(--force-send) ⇒ 不许真的把字打出去",
    "X2": "X2 dry 滚动 ⇒ 不许移动鼠标光标",
    "X3": "X3 root=None（UIA 超时）⇒ 不许崩",
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
        print("  %-56s 期望=%-10s 实得=%-22s %s%s"
              % (label, want, got, "PASS" if ok else "FAIL",
                 ("  · " + note) if note else ""), flush=True)


def do_normal(targets):
    fails = []
    md5s = {}
    for role, path in targets:
        print("\n[被测] %-4s %s" % (role, path), flush=True)
        print("       MD5=%s  %d 字节" % (md5_of(path), os.path.getsize(path)), flush=True)
        md5s[role] = md5_of(path)
        mod = load_act(path, name="act_under_test_%s" % role)
        rows, _ = battery(mod)
        show_rows("%s · X1/X2/X3 干跑语义断言" % role, rows)
        fails.extend(["%s：%s" % (role, r[0]) for r in rows if not r[3]])
    if len(set(md5s.values())) > 1:
        fails.append("三处 act.py 的 MD5 不全等：%s" % md5s)
        print("\n  ★ 三处 MD5 不全等 ⇒ 红：%s" % md5s, flush=True)
    else:
        print("\n  ★ 三处 act.py 的 MD5 全等：%s" % list(set(md5s.values()))[0], flush=True)
    print()
    print("=" * 78, flush=True)
    if fails:
        print("【红】失败 %d 项 -> %s" % (len(fails), fails), flush=True)
        print("★ 判据是活的（能抓住这个缺陷）", flush=True)
        return 1
    print("【绿】全过（%d 份 act.py × %d 条断言）" % (len(targets), len(rows)), flush=True)
    return 0


def do_inject(targets):
    """注入自证：把某一处修复打回原样，判据必须转红。"""
    role, path = targets[0]
    print("[注入源] %s %s" % (role, path), flush=True)
    caught = []
    for defect in ("X1", "X2", "X3"):
        shadow, base = make_shadow(path)
        ok, why = revert(shadow, defect)
        print("\n[注入 %s] 副本：%s\n         %s" % (defect, shadow, why), flush=True)
        if not ok:
            shutil.rmtree(base, ignore_errors=True)
            print("【✗崩溃】注入点没找到 ⇒ 判据没牙 ⇒ exit=2", flush=True)
            return 2
        mod = load_act(shadow, name="act_under_test_shadow_%s" % defect)
        rows, key = battery(mod)
        show_rows("注入副本 · %s 回退成缺陷形态" % defect, rows)
        shutil.rmtree(base, ignore_errors=True)
        watch = INJECT_WATCH[defect]
        row = key.get(watch)
        if row is not None and not row[3]:
            caught.append("%s：%s（期望 %s，实得 %s）" % (defect, watch, row[1], row[2]))
            print("  ⇒ ★ %s 被逮到：该条变红了。" % defect, flush=True)
        else:
            print("  ⇒ ✗ %s **没有**让对应断言变红 ⇒ 判据没牙。" % defect, flush=True)
    print()
    print("=" * 78, flush=True)
    if len(caught) == 3:
        print("【红】注入被逮到（%s）⇒ exit=1" % "；".join(caught), flush=True)
        print("★ 判据有证明力：把修复打回原样，它当场就红。", flush=True)
        return 1
    if caught:
        print("【✗崩溃】只逮到 %d/3 处 ⇒ 判据没牙 ⇒ exit=2" % len(caught), flush=True)
        return 2
    print("【✗崩溃】注入**没有**让任何一条变红 ⇒ 判据没牙 ⇒ exit=2", flush=True)
    return 2


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红，exit=1）")
    ap.add_argument("--act", default=None, help="只跑某一份 act.py")
    args, _unknown = ap.parse_known_args()      # ★ 未知参数不许变成红（与四跑法一致）

    targets = three_copies()
    if args.act:
        targets = [("指定", os.path.abspath(args.act))]
        if not os.path.isfile(targets[0][1]):
            print("找不到：%s" % targets[0][1], flush=True)
            return 2
    if not targets:
        print("找不到任何 act.py（真源/产物/live 全不在）", flush=True)
        return 2

    print("== M20 判据 · act 干跑语义：演练**只算不做**（X1/X2/X3） ==")
    print("[口径] dry ⇒ 只报告不动作：不许打字 / 不许挪光标 / 不许发滚轮 / 不许抓真屏")
    print("[口径] 非 dry ⇒ 行为一字不许改（调用次数·顺序·返回码口径全部照旧）")
    print("[口径] X3：root=None 不许崩（AttributeError ⇒ rc=1 是崩溃，不是拒绝）")
    print("[红线] dry 分支不许动作，非 dry 分支不许变")
    print("[打桩] 全程离线：打字/滚轮/挪光标/取证/UIA/闸门 全部打桩，SETTLE_MS=0")
    print("[产品] %s" % ("产品真代码（M20_USE_PRODUCT=1）"
                          if os.environ.get("M20_USE_PRODUCT") == "1"
                          else "产品真代码（本判据没有参考实现，默认即产品模式）"))
    print("[模式] %s" % ("注入自证（--inject）" if args.inject
                          else "正跑：%s" % "、".join(r for r, _ in targets)))
    try:
        return do_inject(targets) if args.inject else do_normal(targets)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
