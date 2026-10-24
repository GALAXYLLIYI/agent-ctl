# -*- coding: utf-8 -*-
r"""M20 判据 · core 判决层「加长观测」（甲案）—— **判据先红**（产品尚未落地 ⇒ 正跑应当是红的）

★ 本判据测的是哪一刀（施工图：`docs\方案_M20_core甲案接线_20221023.md`）：
  core 在判「确认没变（code=8）」**之前**，用**自己的尺子**（`framecache.wait_change`）
  **再看一会儿**（≤ `framecache.WAIT_BUDGET_MS` = 300ms）：
    · 窗口内**测到变化** ⇒ 改判成功（code=0）⇒ 慢应用（点了 200~500ms 才响应）被救回；
    · 一直不变 / 抓不到帧 / 口径对不上 ⇒ **原判 code=8 一个字不动**；
    · 触发条件**只收窄到「末帧==基线（`df.changed is False`）且没测出退化」这一格**
      ⇒ 成功路（changed=True）、退化路、没测出路 **零额外等待**。

★ 口径（**全程离线、零真 GUI**）：
  · **假时钟**：`framecache.time` 换成 `FakeTime`（`sleep` 只拨钟，不真睡）⇒
    「300ms 预算够几轮」是**确定的**（不测运气；沿用 M19 判据同款思路）。
  · **假抓帧**：`framecache.capture_client` 换成脚本驱动的替身（合成图 / None）。
  · **假判差计时**：`framecache.diff_images` **转调真判差**，只把耗时记进假钟
    （抓帧 24ms ＋ 判差 12.2ms ＋ 间隔 25ms ⇒ 一轮 61.2ms）。
  · **不抓真屏、不起子进程、不点任何按钮、不碰任何窗口**：`_act` 级的端到端断言把
    `_legacy` / `_act_grab` / `_act_diff_store` / `_win_rect` / `_rm_snapdir` 等全部打桩。
  · ★ 假时钟的单位口径：`framecache.wait_change` 用 `time.perf_counter()`（**秒**）
    与 `time.sleep(秒)` ⇒ `FakeTime` 内部按**毫秒**记账，对外 `perf_counter()/time()`
    仍返回**秒**（与真 time 同单位）⇒ 300ms 预算的比较才是对的。

★ 本判据实现的断言（**14 条**，每条一行标签 + PASS/FAIL；M19P5 新增 A13/A14）：
  A1  末帧≠基线 ⇒ code=0 且**零额外等待**（假钟一毫秒都没走、`capture_client` 一次没被调）
  A2  末帧==基线 且窗口内变化 ⇒ **不再是 code=8**（改判成功；滞回 HYSTERESIS=2 ⇒ 轮次=4）
  A3  末帧==基线 且**一直不变** ⇒ **仍是 code=8**（「确认没变」语义保住）
  A4  **闪烁**（只 1 帧变）⇒ **仍是 code=8**（抗假阳性红线）
  A5  **退化** ⇒ **仍是 code=5** 且**不做观测**
  A6  **采样失败**（首轮判差就失败）⇒ **仍是 code=5** 且不做观测
  A7  观测**自己**抓不到帧 ⇒ **判词不动**（仍 code=8；施工图决策 D2）
  A8  **口径/尺寸不同** ⇒ **不升级**（防假成功）
  A9  **加长观测预算 = 默认 900ms 且可配**（env `AGENTCTL_ACT_EXT_OBSERVE_MS`；M19P5 改）
  A10 **加长观测不超预算**（假钟账：900 ≤ 耗时 ≤ 961.2ms）
  A11 **快路径逐字节不变**（未触发 ⇒ 回执不加「加长观测」键）
  A12 **端到端（`_act` 级）**：末帧==基线 + 窗口内变化 ⇒ 回执 code=0（改前 8）
  A13 **慢应用 900ms 才变 ⇒ 加长观测后 code=0**（假时钟离线；旧 300ms 预算下同一脚本仍是 8）
  A14 **明确越界（≈1500ms 才变）⇒ 仍 code=8**（不许无限等；边界不许被"救"成成功）

★ 与 M19 / M19P2 现有判据的关系（**本单不改那两个文件**）：
  · `tests\_P3_M19_语义修复_判据.py` 测 **act 侧**三态（预算耗尽 ⇒ 没测出来）；
  · `tests\_P3_M19P2_同族判据.py` 测 **act 侧**同族第四/第五处；
  · **本判据测 core 侧判决层（甲案）** ⇒ 与它们**互补、不重叠**：
    甲案一行 act 都不改 ⇒ 那两个判据的四种跑法（0/1/0/0）必须**保持不变**。

★ 四跑法（与本仓约定一致）：
    python -X utf8 tests\_P3_M20_core加长观测_判据.py                    # 正跑
    python -X utf8 tests\_P3_M20_core加长观测_判据.py --inject            # 注入自证（必须红）
    M20_USE_PRODUCT=1 python -X utf8 tests\_P3_M20_core加长观测_判据.py   # 产品真代码模式
    python -X utf8 tests\_P3_M20_core加长观测_判据.py --nosucharg        # 未知参数（被忽略）
  退出码：0 绿 ｜ 1 红 ｜ 2 「注入没让行为退化 ⇒ 判据没牙」（更响的错）。
  ★ **判据不解析参数** ⇒ 未知参数被静默忽略 ⇒ 退出码**必须跟正跑一致**。

★ ★ **当前状态（2026-10-24 更新）：甲案已落地**（提交 `a19fbad`，core.py 改了 5 处触点）⇒
  正跑**应当是绿的（exit=0）**；`src\agentctl\core.py` 里**已有** `_act_extend_observe`
  与 `ACT_EXT_OBSERVE_MS`。
  ⇒ ★ 本判据仍然是「活的」：把 core.py 那 5 处触点**改坏/拿掉**（或把预算常量换成别的数），
    正跑会**当场红**（A2/A9/A12 等）⇒ **判据不认「实现没做对」，只认「实现了且做对了」**。
  ⇒ （历史说明：本段原先写的是「产品尚未落地 ⇒ 正跑应当是红的（这是预期，不是失败）」——
    那是因为**判据先到、产品后到**；该状态已于 2026-10-23 12:04 结束，此处已更正，
    免得后人误以为正跑该是红的。修的是**说明文字**，没有动任何断言。）

★ 红线（本判据自己守着）：
  · **不改** `src\agentctl\core.py`（判据只**打桩 / 替换引用**，不动产品源码）；
  · **不改** `legacy_tools\**` / `contract\tools.json` / `src\agentctl\gate.py` /
    `src\agentctl\action_guard_policy.json` / 任何**既有**判据文件 / 任何既有预算与阈值常量；
  · **不跑真 GUI**、不起子进程、不碰任何窗口；跑完**零足迹**（临时目录自删）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile

# ★ 判据零足迹：import 不许在仓里生成 __pycache__（照 M19P2 判据同款纪律）。
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (os.path.join(ROOT, "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PIL import Image                                  # noqa: E402
import agentctl.core as core                           # noqa: E402
import agentctl.framecache as framecache               # noqa: E402

USE_PRODUCT = os.environ.get("M20_USE_PRODUCT") == "1"
ONLY_INJECT = "--inject" in sys.argv

# ── 合成图（沿用 M19 判据同款：BASE / CHG / SMALL）────────────────────────
W, H = 1632, 1191
BASE = Image.new("RGB", (W, H), (250, 250, 250))
CHG = BASE.copy()
for _x in range(100, 108):
    for _y in range(100, 108):
        CHG.putpixel((_x, _y), (0, 0, 0))       # 8×8 方块 ⇒ 真变化（光标规则不会丢它）
SMALL = Image.new("RGB", (800, 600), (250, 250, 250))    # 口径/尺寸不同用

FAKE_HWND = 424242        # ★ 假句柄：**不指向任何真窗口**（GetClientRect 之类只会拿到 0/异常）

# ── 假时钟的成本口径（实测值：framecache.py 头注 / M19 判据同款）──────────
CAPTURE_MS = 24.0        # 客户区 PrintWindow 抓帧
DIFF_MS = 12.2           # 判差
POLL_MS = float(framecache.WAIT_POLL_MS)      # 25
GRACE_MS = float(framecache.WAIT_GRACE_MS)    # 50
BUDGET_MS = float(framecache.WAIT_BUDGET_MS)  # 300（act 侧/framecache 的既有预算，**没动**）
ROUND_MS = CAPTURE_MS + DIFF_MS + POLL_MS     # 61.2ms / 轮
MAX_OBSERVE_MS = BUDGET_MS + CAPTURE_MS + DIFF_MS    # 336.2ms（预算 + 最后一轮超支）

# ★ M19P5（2026-10-24 · N5-W11）：core 的**加长观测**预算改成「可配置、默认 900」。
#   ⇒ 判据的期望值跟着走**产品常量**（core.ACT_EXT_OBSERVE_MS），现场用 env 调了也自洽。
OBS_BUDGET_MS = float(getattr(core, "ACT_EXT_OBSERVE_MS", 900))          # 默认 900
# ★ 上界按**真实账**算（framecache.wait_change：先 50ms 宽限期，再「抓帧→判差→查预算→睡」）：
#   最后一轮只算 capture+diff（36.2ms），退出时最多比预算多**一轮**（61.2ms）⇒ 上界 = 预算 + 一轮。
MAX_OBSERVE_MS_NEW = OBS_BUDGET_MS + ROUND_MS                         # 961.2ms
# ★ 旧预算（改前的 300）—— A13 用它做**对照**：同一脚本在 300 窗口下必须仍是 code=8
#   ⇒ 证明这一刀真的把窗口拉长了，而不是"判据自己放水"。
OLD_BUDGET_MS = 300.0
# ★ 慢应用档的换算（**假时钟口径，量的是"加长观测窗内"的时刻**）：
#   真机口径：act 侧有效覆盖 ≈ 431ms，core 再加观测窗 ⇒ 总覆盖 ≈ 431 + 观测窗。
#   ⇒ 「应用侧 900ms 才变」≈ 观测窗内第 469ms ⇒ 落在 [0,900) 内、旧 [0,300) 外 ⇒ 被救回。
#   ⇒ 「应用侧 ≈1500ms 才变」≈ 观测窗内第 1069ms ⇒ 越界 ⇒ 仍 code=8（不许无限等）。
#   （卡片写「1300ms 才变 ⇒ 仍 code=8」；按卡片自己的算术 431+900=1331，1300 落在窗内
#     ⇒ A14 取在**明确越界**的一侧，免得判据踩在 31ms 的刀尖上；口径差异已在交卷写明。）
SLOW_APP_MS = 900          # 应用侧慢响应（真机档位）
SLOW_WINDOW_ROUND = 8      # 观测窗内：第 8 轮才出现变化（≈490ms，旧 300 窗口抓不到）
BEYOND_APP_MS = 1500       # 应用侧：明确越界
BEYOND_WINDOW_ROUND = 15   # 观测窗内：第 15 轮才出现变化（≈980ms > 900 ⇒ 抓不到）

_REAL_DIFF = framecache.diff_images            # 真判差（装桩前先抓在手里）
_REAL_VERDICT = core._picture_verdict          # 真判词（注入 I3 时绕开它）

# ── 14 条断言的标签（每条一行标签 + PASS/FAIL）───────────────────────────
A1_LABEL = "A1 末帧≠基线 ⇒ code=0 且零额外等待"
A2_LABEL = "A2 末帧==基线且窗口内变化 ⇒ 不再是 code=8（改判成功）"
A3_LABEL = "A3 末帧==基线且一直不变 ⇒ 仍是 code=8"
A4_LABEL = "A4 闪烁（只 1 帧变）⇒ 仍是 code=8"
A5_LABEL = "A5 退化 ⇒ 仍是 code=5 且不做观测"
A6_LABEL = "A6 采样失败 ⇒ 仍是 code=5 且不做观测"
A7_LABEL = "A7 观测自己抓不到帧 ⇒ 判词不动（仍 code=8）"
A8_LABEL = "A8 口径/尺寸不同 ⇒ 不升级（防假成功）"
A9_LABEL = "A9 加长观测预算 = 默认 900ms 且可配（env）"
A10_LABEL = "A10 加长观测不超预算（假钟账：900 ≤ 耗时 ≤ 961.2ms）"
A11_LABEL = "A11 快路径逐字节不变（不加「加长观测」键）"
A12_LABEL = "A12 端到端（_act 级）：末帧==基线+窗口内变化 ⇒ code=0"
A13_LABEL = "A13 慢应用 900ms 才变 ⇒ 加长观测后 code=0（旧 300 窗口仍是 8）"
A14_LABEL = "A14 明确越界（≈1500ms 才变）⇒ 仍 code=8（不许无限等）"

_MISSING = "产品未实现 `core._act_extend_observe`（甲案未落地）⇒ 本条无法验证"


# ══════════════════════════════════════════════════════════════════════════
# 一 · 打桩：S1 假时钟 / S2 假抓帧 / S3 假判差计时
# ══════════════════════════════════════════════════════════════════════════
class FakeTime(object):
    """S1 替身：装到 `framecache.time` 上。

    ★ 单位口径（关键）：`framecache.wait_change` 用 `time.perf_counter()`（**秒**）
      与 `time.sleep(秒)` ⇒ 本替身内部按**毫秒**记账，对外：
        `perf_counter()` → **秒**（与真 time.perf_counter 同单位）
        `sleep(sec)`    → 只把假钟往前拨，不真睡
        `time()`        → **秒**
      ⇒ 「预算 300ms 够几轮」被钉死，判据不测运气。
    """

    def __init__(self, t0=0.0):
        self.t = float(t0)          # 内部记账单位：毫秒
        self.sleeps = []

    def perf_counter(self):
        return self.t / 1000.0

    def sleep(self, sec):
        self.sleeps.append(float(sec))
        self.t += float(sec) * 1000.0

    def time(self):
        return self.t / 1000.0


class FakeEnv(object):
    """S1+S2+S3 装在一起：假时钟 + 假抓帧 + 假判差计时（**不碰真窗口**）。"""

    def __init__(self, script):
        self.clock = FakeTime()
        self.script = script          # list[Image|None] 或 callable(i)->Image|None
        self.n_capture = 0
        self.n_diff = 0

    # ── S2 替身：`(hwnd:int) -> Image|None`
    def capture(self, hwnd):
        i = self.n_capture
        self.n_capture += 1
        self.clock.sleep(CAPTURE_MS / 1000.0)
        if callable(self.script):
            return self.script(i)
        return self.script[i] if i < len(self.script) else None

    # ── S3 替身：`(before, after, **kw) -> dict`（**转调真判差**，只记耗时）
    def diff(self, before, after, **kw):
        self.n_diff += 1
        out = _REAL_DIFF(before, after, **kw)
        self.clock.sleep(DIFF_MS / 1000.0)
        return out

    def install(self):
        """装桩；返回还原函数（必须在 finally 里调）。"""
        _old_time, _old_cap, _old_diff = (framecache.time,
                                           framecache.capture_client,
                                           framecache.diff_images)
        framecache.time = self.clock
        framecache.capture_client = self.capture
        framecache.diff_images = self.diff

        def restore():
            framecache.time = _old_time
            framecache.capture_client = _old_cap
            framecache.diff_images = _old_diff
        return restore


# ── S4 桩：`_act` 级端到端要打掉的入口（**不起子进程、不租约、不凭据、不抓真屏**）──
_ACT_STUBS = ("_act_precheck", "_bind_capture", "_ownership_precheck", "_legacy",
              "_act_grab", "_act_diff_store", "_win_rect", "_rm_snapdir",
              "_act_frame_origin")

_TMPDIR = None


def _tmpdir():
    global _TMPDIR
    if _TMPDIR is None:
        _TMPDIR = tempfile.mkdtemp(prefix="_M20_判据_")
    return _TMPDIR


def _cleanup_tmp():
    global _TMPDIR
    if _TMPDIR:
        shutil.rmtree(_TMPDIR, ignore_errors=True)
        _TMPDIR = None


# ══════════════════════════════════════════════════════════════════════════
# 二 · 判据自带的「规格等价实现」（照施工图 §2.2 逐条抄）
#     ★ 用途只有两个：① 注入自证时当基线（证明判据抓得住退化）；
#       ② 在「产品尚未落地」时，让注入自证仍然有意义。
#     ★ 它**不是**被测对象：正跑/产品模式打的是**产品真代码**。
# ══════════════════════════════════════════════════════════════════════════
REF_OBSERVE_MS = framecache.WAIT_BUDGET_MS        # 规格里的预算 = 既有常量


def ref_extend_observe(hwnd, before, after, df, win_off=None, ignore=None,
                        degraded=False, budget_ms=None):
    """规格等价实现（施工图 §2.2 的代码）—— 只作**注入基线**，不作被测对象。"""
    _obs = {"做了": False, "原因": "", "耗时ms": 0.0, "轮次": 0, "结果": None}
    _out = {"after": after, "df": df, "观测": _obs}
    if degraded:
        _obs["原因"] = "画面退化 ⇒ 没测出来 ⇒ 不加长观测（保持 code=5）"
        return _out
    if (df or {}).get("changed") is not False:
        _obs["原因"] = "末帧≠基线（或没测出来）⇒ 不加长观测（零额外等待）"
        return _out
    if before is None or after is None:
        _obs["原因"] = "没有基线帧/末帧 ⇒ 无从比对 ⇒ 不加长观测"
        return _out
    _ig = ignore if isinstance(ignore, dict) else dict(core.ACT_DIFF_IGNORE_DEFAULT)
    try:
        r = framecache.wait_change(
            int(hwnd), before,
            budget_ms=int(budget_ms if budget_ms is not None else REF_OBSERVE_MS),
            poll_ms=framecache.WAIT_POLL_MS,
            caret_px=int(_ig.get("caret_px", 8) or 0),
            min_area_px=int(_ig.get("min_area_px", 24) or 0))
    except Exception as e:
        _obs["原因"] = "加长观测异常：%s: %s" % (type(e).__name__, e)
        return _out
    _obs["做了"] = True
    _obs["耗时ms"] = float(r.get("耗时ms") or 0.0)
    _obs["轮次"] = int(r.get("轮次") or 0)
    _obs["结果"] = r.get("changed")
    _obs["说明"] = r.get("说明") or ""
    if r.get("changed") is not True:
        _obs["原因"] = "窗口内没有测到变化 ⇒ 判词不动（仍 code=8「确认没变」）"
        return _out
    _img = r.get("图")
    if _img is None:
        _obs["原因"] = "测到变化但没拿到帧 ⇒ 判词不动"
        return _out
    try:
        if before.size != _img.size:
            _obs["原因"] = ("新帧与基线口径/尺寸不同（%s vs %s）⇒ 不升级"
                            % (before.size, _img.size))
            return _out
    except Exception:
        _obs["原因"] = "取不到基线尺寸 ⇒ 不升级"
        return _out
    _new_df = core._act_diff_of(before, _img, win_off, ignore)
    if (_new_df or {}).get("changed") is not True:
        _obs["原因"] = "复核判差未确认变化 ⇒ 判词不动"
        return _out
    _out["after"] = _img
    _out["df"] = _new_df
    _obs["原因"] = "窗口内测到变化 ⇒ 改判成功（code=0）"
    return _out


# ══════════════════════════════════════════════════════════════════════════
# 三 · 小工具
# ══════════════════════════════════════════════════════════════════════════
def _verdict_code(df, degraded=False):
    """用**产品的** `_picture_verdict` 判一次，返回 code（注入 I3 时会被换掉）。"""
    r = {"ok": True, "code": 0, "data": {}}
    out = core._picture_verdict(r, df, degraded=degraded)
    return (out or {}).get("code")


def _run_extend(ext, script, before=None, after=None, df=None, degraded=False,
                budget_ms=None):
    """在假环境里跑一次「加长观测」；返回 (obs, env, df)。

    ★ `df` 若不给，就**在装桩之前**用真判差算好（这样它不占假钟 ⇒
      A1 的「假钟一毫秒都没走」才成立）。
    ★ `budget_ms` 给了就**显式**传进去 ⇒ 用来做「旧 300ms 窗口」的对照实验（A13）。
    """
    before = BASE if before is None else before
    after = BASE if after is None else after
    if df is None:
        df = core._act_diff_of(before, after, (0, 0), None)
    env = FakeEnv(script)
    restore = env.install()
    try:
        try:
            if budget_ms is None:
                obs = ext(int(FAKE_HWND), before, after, df, (0, 0), None,
                          degraded=degraded)
            else:
                obs = ext(int(FAKE_HWND), before, after, df, (0, 0), None,
                          degraded=degraded, budget_ms=int(budget_ms))
        except TypeError:
            obs = ext(int(FAKE_HWND), before, after, df, (0, 0), None, degraded)
    finally:
        restore()
    return obs, env, df


def _run_act_end_to_end(script, frame_img):
    """在 S4 桩下跑一次 `core._act`（**不起子进程、不抓真屏、不碰真窗口**）。

    · `_legacy` 替身在「子进程跑完」的时刻写一张**与基线相同**的帧当 `frame_ref`
      （mtime ≥ `t_spawn` ⇒ core 直接用它）⇒ 模拟「act 报了『没看到变化』」那条真实路径。
    """
    env = FakeEnv(script)
    restore = env.install()
    saved = {k: getattr(core, k) for k in _ACT_STUBS}
    try:
        core._act_precheck = lambda args, scope: None            # 通行证：放行
        core._bind_capture = lambda args, hwnd: None             # 凭据绑定：放行
        core._ownership_precheck = lambda args, hwnd: (None, [])  # 归属闸：放行
        core._win_rect = lambda hwnd: [0, 0, W, H]               # 判定区：变化块在窗内
        core._act_frame_origin = lambda hwnd, img=None: (0, 0)   # 原点：打掉（不碰真窗）
        core._act_diff_store = lambda fid, b, a, d: {}            # 落盘：打掉
        core._rm_snapdir = lambda p: shutil.rmtree(p, ignore_errors=True)  # 临时目录：真删
        core._act_grab = lambda hwnd: BASE                      # 基线 = BASE

        def _fake_legacy(name, argv, timeout=60, extra_env=None):
            p = os.path.join(_tmpdir(), "frame_ref.bmp")
            frame_img.save(p, format="BMP")
            eff = {"effect": {"rounds": 12, "first_change_ms": 780.0,
                              "degraded": False, "frame_ref": p,
                              "口径": {"w": W, "h": H}}}
            return 0, json.dumps(eff, ensure_ascii=False), ""

        core._legacy = _fake_legacy
        r = core._act("click", {"hwnd": FAKE_HWND, "xy": [10, 10]}, ["click"])
        return r, env
    finally:
        for k, v in saved.items():
            setattr(core, k, v)
        restore()


def _read_core_src():
    """读**正在跑的那份** core.py 的源码（不写死路径 ⇒ 影子/副本也能验）。

    ★ 用 `core.__file__` 而不是仓根拼路径：这样判据跑到别处的 core.py 副本上时，
      A9 的「不许新增数字」检查查的是**那一份**源码。
    """
    p = getattr(core, "__file__", None) or os.path.join(ROOT, "src", "agentctl", "core.py")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def product_status():
    """产品现状体检（只读 core.py 源码）：甲案的 5 处触点落地了没有。"""
    src = _read_core_src()
    n_def = 1 if "def _act_extend_observe(" in src else 0
    n_call = src.count("_act_extend_observe(") - n_def
    return [
        ("① 常量 ACT_EXT_OBSERVE_MS = 可配置、默认 900（env AGENTCTL_ACT_EXT_OBSERVE_MS）",
         bool(re.search(r"ACT_EXT_OBSERVE_MS\s*=\s*int\(os\.environ\.get\(\s*"
                        r"\"AGENTCTL_ACT_EXT_OBSERVE_MS\"\s*\)\s*or\s*900\s*\)", src))),
        ("② 函数 def _act_extend_observe(", n_def == 1),
        ("③ _act 两处调用点（改动点③/④）", n_call >= 2),
        ("④ 回执留痕「加长观测」两处（改动点⑤A/⑤B）",
         src.count('"加长观测"') >= 2),
        ("⑤ wait_change 调用点 2 处（_act_after + _act_extend_observe）",
         src.count("wait_change(") >= 2),
    ]


# ══════════════════════════════════════════════════════════════════════════
# 四 · 14 条断言（每条一行标签 + PASS/FAIL）
# ══════════════════════════════════════════════════════════════════════════
def _a1(ext):
    """末帧≠基线 ⇒ code=0 且**零额外等待**。"""
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, CHG, (0, 0), None)
    obs, env, _ = _run_extend(ext, script=[BASE, CHG, CHG], df=df)
    code = _verdict_code(df)
    o = obs.get("观测") or {}
    ok = (df.get("changed") is True and code == 0 and o.get("做了") is False
          and abs(env.clock.t) < 1e-9 and env.n_capture == 0)
    return ok, ("df.changed=%s code=%s 做了=%s 假钟=%.1fms 抓帧次数=%d"
                % (df.get("changed"), code, o.get("做了"), env.clock.t, env.n_capture))


def _a2(ext):
    """末帧==基线 且窗口内变化 ⇒ 不再是 code=8（改判成功）。"""
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, BASE, (0, 0), None)
    obs, env, _ = _run_extend(ext, script=[BASE, BASE, CHG, CHG, CHG, CHG], df=df)
    o = obs.get("观测") or {}
    code = _verdict_code(obs.get("df") or {})
    ok = (df.get("changed") is False and o.get("做了") is True
          and o.get("结果") is True and (obs.get("df") or {}).get("changed") is True
          and code == 0 and int(o.get("轮次") or 0) == 4)
    return ok, ("观测.做了=%s 结果=%s 轮次=%s（滞回 HYSTERESIS=2 ⇒ 第 4 帧才成立）"
                " 新判差=%s code=%s 假钟=%.1fms"
                % (o.get("做了"), o.get("结果"), o.get("轮次"),
                   (obs.get("df") or {}).get("changed"), code, env.clock.t))


def _a3(ext):
    """末帧==基线 且一直不变 ⇒ 仍是 code=8（「确认没变」语义保住）。"""
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, BASE, (0, 0), None)
    obs, env, _ = _run_extend(ext, script=[BASE] * 20, df=df)
    o = obs.get("观测") or {}
    code = _verdict_code(obs.get("df") or {})
    ok = (o.get("做了") is True and o.get("结果") is False
          and (obs.get("df") or {}).get("changed") is False and code == 8)
    return ok, ("观测.结果=%s 判差仍=%s code=%s 假钟=%.1fms"
                % (o.get("结果"), (obs.get("df") or {}).get("changed"), code, env.clock.t))


def _a4(ext):
    """闪烁（只 1 帧变）⇒ 仍是 code=8（抗假阳性红线）。

    ★ M19P5：脚本**必须够长**（900ms 窗口要跑 ~15 轮）⇒ 否则假抓帧返回 None
      ⇒ 观测会走「抓不到帧」那条路（结果=None），测的就不是"闪烁"而是"抓不到帧"了。
    """
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, BASE, (0, 0), None)
    obs, env, _ = _run_extend(ext, script=[BASE, CHG] + [BASE] * 40, df=df)
    o = obs.get("观测") or {}
    code = _verdict_code(obs.get("df") or {})
    ok = (o.get("结果") is False and (obs.get("df") or {}).get("changed") is False
          and code == 8)
    return ok, ("观测.结果=%s（HYSTERESIS=2 挡住单帧闪）判差=%s code=%s 假钟=%.1fms"
                % (o.get("结果"), (obs.get("df") or {}).get("changed"), code, env.clock.t))


def _a5(ext):
    """退化 ⇒ 仍是 code=5 且不做观测。"""
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, BASE, (0, 0), None)
    obs, env, _ = _run_extend(ext, script=[BASE] * 20, df=df, degraded=True)
    o = obs.get("观测") or {}
    code = _verdict_code(df, degraded=True)
    ok = (o.get("做了") is False and env.n_capture == 0 and code == 5)
    return ok, ("观测.做了=%s 抓帧次数=%d code=%s"
                % (o.get("做了"), env.n_capture, code))


def _a6(ext):
    """采样失败（首轮判差就失败，changed is None）⇒ 仍是 code=5 且不做观测。"""
    if ext is None:
        return False, _MISSING
    df = {"changed": None, "bbox": None, "说明": "求差失败：判据注入"}
    obs, env, _ = _run_extend(ext, script=[BASE] * 20, df=df)
    o = obs.get("观测") or {}
    code = _verdict_code(df)
    ok = (o.get("做了") is False and env.n_capture == 0 and code == 5)
    return ok, ("观测.做了=%s 抓帧次数=%d code=%s"
                % (o.get("做了"), env.n_capture, code))


def _a7(ext):
    """观测自己抓不到帧 ⇒ 判词不动（仍 code=8；施工图决策 D2）。"""
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, BASE, (0, 0), None)
    obs, env, _ = _run_extend(ext, script=[None] * 20, df=df)
    o = obs.get("观测") or {}
    code = _verdict_code(obs.get("df") or {})
    ok = (o.get("做了") is True and o.get("结果") is None
          and (obs.get("df") or {}).get("changed") is False and code == 8)
    return ok, ("观测.做了=%s 结果=%s 判差仍=%s code=%s"
                % (o.get("做了"), o.get("结果"),
                   (obs.get("df") or {}).get("changed"), code))


def _a8(ext):
    """口径/尺寸不同 ⇒ 不升级（防假成功）。"""
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, BASE, (0, 0), None)
    obs, env, _ = _run_extend(ext, script=[SMALL] * 20, df=df)
    o = obs.get("观测") or {}
    code = _verdict_code(obs.get("df") or {})
    why = str(o.get("原因") or "")
    ok = (o.get("结果") is True and (obs.get("df") or {}).get("changed") is False
          and code == 8 and ("尺寸" in why or "口径" in why))
    return ok, ("观测.结果=%s 判差仍=%s code=%s 原因=%r"
                % (o.get("结果"), (obs.get("df") or {}).get("changed"), code, why))


def _a9(ext):
    """加长观测预算：**默认 900ms** 且**可配**（env `AGENTCTL_ACT_EXT_OBSERVE_MS`）。

    ★ M19P5（2026-10-24 · N5-W11）：这一条的口径**由主控改写** ——
      改前是「预算恒等 = framecache.WAIT_BUDGET_MS（不许新增数字）」，
      现在是「默认 900（可配）」⇒ 真机长跑里 slow@900 那一档才救得回来。
    ★ 「可配」怎么验（**不起子进程**）：把源码里那**同一条表达式**在**假 os.environ**
      下求值 ⇒ 设 1200 得 1200、不设得 900 ⇒ 证明 env 那条路真的通。
    """
    has = hasattr(core, "ACT_EXT_OBSERVE_MS")
    val = getattr(core, "ACT_EXT_OBSERVE_MS", None)
    src = _read_core_src()
    m = re.search(r"^ACT_EXT_OBSERVE_MS\s*=\s*(.+)$", src, re.M)
    expr = (m.group(1).strip() if m else "")
    form_ok = bool(re.search(r"AGENTCTL_ACT_EXT_OBSERVE_MS", expr))
    default_ok = (val == 900)
    env_ok = False
    env_detail = "没取到表达式"
    try:
        def _eval_with(v):
            class _O(object):
                environ = ({"AGENTCTL_ACT_EXT_OBSERVE_MS": v} if v is not None else {})

            return eval(expr, {"os": _O})

        v_set = _eval_with("1200")
        v_unset = _eval_with(None)
        env_ok = (int(v_set) == 1200 and int(v_unset) == 900)
        env_detail = "env=1200⇒%s ｜ env 未设⇒%s" % (v_set, v_unset)
    except Exception as e:
        env_detail = "求值失败：%s: %s" % (type(e).__name__, e)
    ok = bool(has and form_ok and default_ok and env_ok)
    return ok, ("存在=%s 值=%s（默认须 900）源码 env 可配=%s ｜ %s"
                % (has, val, form_ok, env_detail))


def _a10(ext):
    """加长观测不超预算（假钟账）：900 ≤ 耗时 ≤ 936.2ms（**跟着产品常量走**）。"""
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, BASE, (0, 0), None)
    obs, env, _ = _run_extend(ext, script=[BASE] * 40, df=df)
    el = env.clock.t
    lo = OBS_BUDGET_MS - 1e-9
    hi = MAX_OBSERVE_MS_NEW + 1e-9
    ok = (lo <= el <= hi)
    return ok, ("假钟耗时=%.1fms（须在 [%.0f, %.1f]ms：等满预算且不多超一轮；产品预算=%s）"
                % (el, OBS_BUDGET_MS, MAX_OBSERVE_MS_NEW,
                   getattr(core, "ACT_EXT_OBSERVE_MS", "?")))


def _a11(ext):
    """快路径逐字节不变：未触发 ⇒ 回执不加「加长观测」键。"""
    if ext is None:
        return False, _MISSING
    # ① 函数级：末帧≠基线 ⇒ 观测「没做」
    df = core._act_diff_of(BASE, CHG, (0, 0), None)
    obs, env, _ = _run_extend(ext, script=[BASE, CHG, CHG], df=df)
    o = obs.get("观测") or {}
    fn_ok = (o.get("做了") is False and env.n_capture == 0)
    # ② 端到端级：末帧≠基线（act 回传的帧=CHG）⇒ code=0 且 data 里没有「加长观测」
    try:
        r, _env2 = _run_act_end_to_end([BASE, CHG, CHG], CHG)
        data = (r or {}).get("data") or {}
        e2e_ok = ((r or {}).get("code") == 0 and "加长观测" not in data)
        e2e_detail = "端到端 code=%s data 有无「加长观测」=%s" % (
            (r or {}).get("code"), "加长观测" in data)
    except Exception as e:
        e2e_ok, e2e_detail = False, "端到端异常：%s: %s" % (type(e).__name__, e)
    return bool(fn_ok and e2e_ok), ("函数级(做了=%s 抓帧=%d) ｜ %s"
                                    % (o.get("做了"), env.n_capture, e2e_detail))


def _a12(ext):
    """端到端（`_act` 级）：末帧==基线 + 窗口内变化 ⇒ 回执 code=0（改前 8）。"""
    try:
        r, env = _run_act_end_to_end([BASE, BASE, CHG, CHG, CHG, CHG], BASE)
    except Exception as e:
        return False, "端到端异常：%s: %s" % (type(e).__name__, e)
    data = (r or {}).get("data") or {}
    diff = ((data.get("after") or {}).get("diff") or {})
    obs = data.get("加长观测") or {}
    ok = ((r or {}).get("code") == 0 and diff.get("changed") is True
          and obs.get("做了") is True)
    return ok, ("code=%s data.after.diff.changed=%s 加长观测.做了=%s 假钟=%.1fms"
                % ((r or {}).get("code"), diff.get("changed"),
                   obs.get("做了"), env.clock.t))


def _a13(ext):
    """★ M19P5（2026-10-24 · N5-W11）**慢应用 900ms 才变 ⇒ 加长观测后 code=0**。

    ★ 同一段脚本跑两遍（**这才是"窗口变长救回了它"的证明**）：
      ① 用产品默认预算（900）⇒ 应救回（观测到变化 ⇒ code=0）；
      ② 把预算显式压回**旧值 300** ⇒ 同一段脚本**抓不到**变化 ⇒ 仍 code=8。
      ⇒ 差值就是这一刀的全部收益；只测①会分不清"是窗口变长了"还是"判据放水了"。
    ★ 换算（假时钟量的是**观测窗内**的时刻）：act 侧有效覆盖 ≈431ms ⇒
      「应用侧 900ms 才变」≈ 观测窗内第 469ms ⇒ 落在 [0,900) 内、旧 [0,300) 外。
    """
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, BASE, (0, 0), None)
    script = [BASE] * SLOW_WINDOW_ROUND + [CHG] * 12
    obs, env, _ = _run_extend(ext, script=script, df=df)
    o = obs.get("观测") or {}
    code_new = _verdict_code(obs.get("df") or {})
    obs_old, env_old, _ = _run_extend(ext, script=script, df=df,
                                      budget_ms=OLD_BUDGET_MS)
    o_old = obs_old.get("观测") or {}
    code_old = _verdict_code(obs_old.get("df") or {})
    ok = (o.get("做了") is True and o.get("结果") is True
          and (obs.get("df") or {}).get("changed") is True and code_new == 0
          and code_old == 8)
    return ok, ("新预算(%sms)⇒观测结果=%s code=%s（假钟 %.0fms）｜ 旧预算(%sms)⇒结果=%s code=%s"
                " ｜ 变化出现在观测窗内第 %d 轮（≈%.0fms）"
                % (getattr(core, "ACT_EXT_OBSERVE_MS", "?"), o.get("结果"), code_new,
                   env.clock.t, OLD_BUDGET_MS, o_old.get("结果"), code_old,
                   SLOW_WINDOW_ROUND, (SLOW_WINDOW_ROUND + 1) * ROUND_MS))


def _a14(ext):
    """★ M19P5 **明确越界 ⇒ 仍 code=8**（不许无限等；边界不许被"救"成成功）。

    ★ 变化出现在观测窗内第 15 轮（≈980ms）⇒ **超出 900ms 预算** ⇒ 抓不到 ⇒
      判词不动 ⇒ 仍 code=8（「确认没变」语义一点没丢）。
    ★ 口径（★ 已写进交卷，请主控确认）：卡片写「1300ms 才变 ⇒ 仍 code=8」，
      但按卡片自己的算术（act 431 + core 900 = 1331）**1300ms 落在窗内** ⇒
      本条取在**明确越界**的一侧（应用侧 ≈1500ms），免得判据踩在 31ms 的刀尖上。
    """
    if ext is None:
        return False, _MISSING
    df = core._act_diff_of(BASE, BASE, (0, 0), None)
    script = [BASE] * BEYOND_WINDOW_ROUND + [CHG] * 12
    obs, env, _ = _run_extend(ext, script=script, df=df)
    o = obs.get("观测") or {}
    code = _verdict_code(obs.get("df") or {})
    ok = (o.get("做了") is True and o.get("结果") is False
          and (obs.get("df") or {}).get("changed") is False and code == 8)
    return ok, ("观测.结果=%s（变化落在第 %d 轮 ≈%.0fms > 预算 %sms ⇒ 抓不到）判差=%s code=%s"
                % (o.get("结果"), BEYOND_WINDOW_ROUND, (BEYOND_WINDOW_ROUND + 1) * ROUND_MS,
                   getattr(core, "ACT_EXT_OBSERVE_MS", "?"),
                   (obs.get("df") or {}).get("changed"), code))


# ══════════════════════════════════════════════════════════════════════════
# 五 · 注入替身（施工图 §3.4：I1/I2/I3，每一种都必须让判据变红）
# ══════════════════════════════════════════════════════════════════════════
def _inj_always_upgrade(hwnd, before, after, df, win_off=None, ignore=None,
                         degraded=False, budget_ms=None):
    """I1 · 一律升级：不管测没测到变化都说「变了」⇒ 真没变的界面被误判成功。"""
    _df = dict(df or {})
    _df["changed"] = True
    _df["bbox"] = [100, 100, 8, 8]
    return {"after": after, "df": _df,
            "观测": {"做了": True, "原因": "注入I1：一律升级", "耗时ms": 0.0,
                     "轮次": 1, "结果": True}}


def _inj_always_observe(hwnd, before, after, df, win_off=None, ignore=None,
                         degraded=False, budget_ms=None):
    """I2 · 一律观测：忽略触发条件（哪怕末帧≠基线）也去等 ⇒ 快路径不再零额外等待。"""
    _obs = {"做了": False, "原因": "", "耗时ms": 0.0, "轮次": 0, "结果": None}
    _out = {"after": after, "df": df, "观测": _obs}
    _ig = ignore if isinstance(ignore, dict) else dict(core.ACT_DIFF_IGNORE_DEFAULT)
    try:
        r = framecache.wait_change(
            int(hwnd), before,
            budget_ms=int(budget_ms if budget_ms is not None else REF_OBSERVE_MS),
            poll_ms=framecache.WAIT_POLL_MS,
            caret_px=int(_ig.get("caret_px", 8) or 0),
            min_area_px=int(_ig.get("min_area_px", 24) or 0))
    except Exception as e:
        _obs["原因"] = "注入I2：%s" % e
        return _out
    _obs.update({"做了": True, "耗时ms": float(r.get("耗时ms") or 0.0),
                 "轮次": int(r.get("轮次") or 0), "结果": r.get("changed")})
    return _out


def _inj_verdict_always5(r, df, degraded=False, what="动作"):
    """I3 · 一律 code=5：把「确认没变」放松成「没测出来」（乙案误接线）。"""
    out = _REAL_VERDICT(r, df, degraded=degraded, what=what)
    if (not degraded) and (df or {}).get("changed") is False and (r or {}).get("ok"):
        return core.env(code=5, data=(out or {}).get("data"),
                        warning="注入I3：确认没变被放松成没测出来（code=5）")
    return out


# (注入名, 被替换的属性, 替身, 必须因此变红的断言前缀)
INJECT_CASES = (
    ("I1 · 一律升级（把「确认没变」也升级成成功）", "_act_extend_observe",
     _inj_always_upgrade, "A3"),
    ("I2 · 一律观测（忽略触发条件 ⇒ 快路径也等）", "_act_extend_observe",
     _inj_always_observe, "A1"),
    ("I3 · 一律 code=5（确认没变被放松成没测出来）", "_picture_verdict",
     _inj_verdict_always5, "A3"),
)


def _with_attr(mod, name, value):
    """临时替换模块属性；返回还原函数（只动内存，不改任何文件）。"""
    had = hasattr(mod, name)
    old = getattr(mod, name, None)
    setattr(mod, name, value)

    def restore():
        if had:
            setattr(mod, name, old)
        else:
            try:
                delattr(mod, name)
            except AttributeError:
                pass
    return restore


# ══════════════════════════════════════════════════════════════════════════
# 六 · 跑一组断言
# ══════════════════════════════════════════════════════════════════════════
_AUTO = object()      # ★ 哨兵：让 battery 在**调用时**从 core 上取「加长观测」实现
                      #   （否则注入替身挂不上去 ⇒ 注入自证会假绿）


def battery(ext=_AUTO, level="all"):
    """跑断言，返回 [(标签, 通过?, 详情)]。

    ★ `ext` 默认=_AUTO ⇒ **每次调用时**从 `core._act_extend_observe` 取实现
      ⇒ 注入替身（挂在这个属性上）立刻生效。
    ★ `level`：
      · "all"（正跑 / 产品真代码模式）⇒ **14 条全跑**。
      · "func"（注入自证）⇒ 只跑**由被注入符号决定**的那几条（A1~A8/A10/A11/A13/A14）。
        A9（产品常量 `ACT_EXT_OBSERVE_MS`）与 A12（产品接线：`_act` 里那两处调用）
        **不由被注入的符号决定** ⇒ 属产品层，只在正跑/产品模式里评。
    """
    if ext is _AUTO:
        _fn = getattr(core, "_act_extend_observe", None)
        ext = _fn if callable(_fn) else None
    plan = [(A1_LABEL, _a1), (A2_LABEL, _a2), (A3_LABEL, _a3), (A4_LABEL, _a4),
            (A5_LABEL, _a5), (A6_LABEL, _a6), (A7_LABEL, _a7), (A8_LABEL, _a8),
            (A13_LABEL, _a13), (A14_LABEL, _a14)]
    if level == "all":
        plan.append((A9_LABEL, _a9))
    plan.append((A10_LABEL, _a10))
    plan.append((A11_LABEL, _a11))
    if level == "all":
        plan.append((A12_LABEL, _a12))
    rows = []
    for label, fn in plan:
        try:
            ok, detail = fn(ext)
        except Exception as e:            # 判据自身崩了也是红（不许当成功）
            ok, detail = False, "判据异常：%s: %s" % (type(e).__name__, e)
        rows.append((label, bool(ok), str(detail)))
    return rows


def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for label, ok, detail in rows:
        print("  [%s] %-46s %s" % ("PASS" if ok else "FAIL", label, detail),
              flush=True)


def _product_extend():
    """产品自己的「加长观测」实现；没落地 ⇒ None。"""
    fn = getattr(core, "_act_extend_observe", None)
    return fn if callable(fn) else None


# ══════════════════════════════════════════════════════════════════════════
# 七 · 两种跑法
# ══════════════════════════════════════════════════════════════════════════
def do_normal():
    """正跑 / 产品真代码模式 / 未知参数：断言打在**产品真代码**上。"""
    ext = _product_extend()
    print("[被测] core._act_extend_observe = %s"
          % ("产品真代码（%s）" % getattr(core._act_extend_observe, "__name__", "?")
             if ext else "★ **不存在**（甲案未落地）"), flush=True)
    print("---- 产品现状体检（core.py 5 处触点，只读）----", flush=True)
    for name, ok in product_status():
        print("  [%s] %s" % ("在" if ok else "缺", name), flush=True)
    rows = battery(level="all")
    show_rows("%d 条断言（产品真代码）" % len(rows), rows)
    bad = [r for r in rows if not r[1]]
    print()
    if bad:
        print("【红】失败 %d 项 -> %s" % (len(bad), [r[0] for r in bad]), flush=True)
        if ext is None:
            print("★ **甲案未落地** ⇒ 正跑此刻**应当是红的**（判据是活的："
                  "它只认「实现了且做对了」）。", flush=True)
            print("  ⇒ 甲案落地后（core.py 改 5 处触点）本条必须转绿（exit=0）。",
                  flush=True)
        else:
            print("★ 判据是活的（能抓住「甲案做错了」）。", flush=True)
        return 1
    print("【绿】全过（%d 条断言）" % len(rows), flush=True)
    return 0


def do_inject():
    """注入自证：把「加长观测」换成退化替身 ⇒ 判据**必须**变红（exit=1）。"""
    prod = _product_extend()
    base_fn = prod if prod else ref_extend_observe
    print("[基线] %s" % ("产品真代码（甲案已落地）" if prod
                         else "★ 产品未落地 ⇒ 用判据自带的**规格等价实现**当基线"
                              "（只证明判据抓得住退化，不代表产品已实现）"),
          flush=True)
    print("[口径] 注入模式只跑**函数级**断言（A1~A8/A10/A11）："
          "A9（产品常量）与 A12（产品接线）不由被注入的符号决定 ⇒ 属产品层，"
          "正跑才评。", flush=True)
    restore_ext = _with_attr(core, "_act_extend_observe", base_fn)
    try:
        base_rows = battery(level="func")
        show_rows("基线（未注入）· 函数级 A1~A8/A10/A11", base_rows)
        bad = [r for r in base_rows if not r[1]]
        if bad:
            print()
            print("【✗崩溃】基线自己就红（%s）⇒ 判据/规格自相矛盾 ⇒ exit=2"
                  % [r[0] for r in bad], flush=True)
            return 2

        caught = []
        missed = []
        for name, attr, stub, must_red in INJECT_CASES:
            restore_attr = _with_attr(core, attr, stub)
            try:
                rows = battery(level="func")
            finally:
                restore_attr()
            tgt = [r for r in rows if r[0].startswith(must_red + " ")]
            ok_red = bool(tgt) and (not tgt[0][1])
            print("---- 注入 %s ⇒ %s 必须变红 ----" % (name, must_red), flush=True)
            for label, ok, detail in rows:
                if label.startswith(must_red + " ") or not ok:
                    print("  [%s] %-46s %s" % ("PASS" if ok else "FAIL", label, detail),
                          flush=True)
            if ok_red:
                caught.append("%s ⇒ %s 红" % (name, must_red))
            else:
                missed.append("%s ⇒ %s **没红**" % (name, must_red))
        print()
        if missed:
            print("【✗崩溃】注入**没有**让行为退化（%s）⇒ 判据没牙 ⇒ exit=2"
                  % missed, flush=True)
            return 2
        print("【红】注入被逮到（%s）⇒ exit=1" % "；".join(caught), flush=True)
        print("★ 判据有证明力：把甲案改回原样/改坏，它当场就红。", flush=True)
        return 1
    finally:
        restore_ext()


def main():
    print("== M20 判据 · core 判决层「加长观测」（甲案）==", flush=True)
    print("[口径] 假时钟（perf_counter/sleep 全部替身，不真睡）＋ 假抓帧（合成图）"
          "＋ 假判差计时（转调真判差）", flush=True)
    print("[口径] 一轮 = 抓帧 %.1fms ＋ 判差 %.1fms ＋ 间隔 %.1fms = %.1fms"
          "；预算 %.0fms ⇒ 恒定轮次，判据不测运气"
          % (CAPTURE_MS, DIFF_MS, POLL_MS, ROUND_MS, BUDGET_MS), flush=True)
    print("[口径] 全程离线：**不抓真屏、不起子进程、不点任何按钮、不碰任何窗口**",
          flush=True)
    print("[模式] %s" % ("注入自证（--inject）" if ONLY_INJECT
                          else ("产品真代码模式（M20_USE_PRODUCT=1）" if USE_PRODUCT
                                else "正跑（断言打在产品真代码上）")), flush=True)
    if USE_PRODUCT and not ONLY_INJECT:
        print("[模式] ★ 本模式与**正跑同一批断言**（都打在产品真代码上："
              "core._act_extend_observe / core._picture_verdict / core._act_diff_of / "
              "core._act / framecache.wait_change）⇒ 退出码与正跑一致；"
              "甲案落地后本模式 ⇒ exit=0。", flush=True)
    try:
        return do_inject() if ONLY_INJECT else do_normal()
    finally:
        _cleanup_tmp()


if __name__ == "__main__":
    sys.exit(main())
