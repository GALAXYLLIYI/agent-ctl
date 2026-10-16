# -*- coding: utf-8 -*-
r"""永久判据 · 窗A P3 —— 边框判档口径 vs 闸门判档口径（2026-10-15）

判的问题（派单 P3）：
    边框（ctrl_border）和闸门（gate）读同一张通行证的 scopes，
    却各认各的。分叉出来的**危险方向**是：边框说「D 级·可拖拽」，闸门实际不放行 ——
    指示器骗人。

怎么验的（**黑盒，不复刻闸门口径**，免得自己验自己）：
    参照系 = **真的把闸门叫起来**。每个用例写一张临时通行证，
    然后逐档调 gate.check(档)，拿它返回的放行/拒绝当真值。
    被测件 = --target 指的那份 ctrl_border.py（可以指改前旧版来跑红）。

三条断言（任一红 ⇒ 退出码非 0）：
    ① 危险方向 0 组：边框显示的档位，闸门必须**真的放行**，
       且不得高于闸门放行的最高档 ——「显示层只许比闸门更严，不许更松」。
    ② 认不出的串 ⇒ 边框必须红：闸门 A/B/C/D 全拒（scopes 里没有可识别档位）时，
       边框不许显示任何档位色，必须红（fail-closed）。
    ③ 口径单一源：边框显示的档位必须**恰好等于**闸门放行的最高档。
       虚低（边框比闸门保守）同样算不一致 —— 不可信的指示器跟骗人的一样有害。

用法（可独立重跑）：
    python -X utf8 tests\_2h_wA_档位口径判据.py --target src\agentctl\ctrl_border.py
    python -X utf8 tests\_2h_wA_档位口径判据.py --target work\_2h_档位口径_红基线_ctrl_border.py

★ 沙箱：闸门的 PASSFILE / LOCKFILE / BRAKE_FLAG / LOG 全部改指到临时目录，
  真 auth_pass.json、真急停 flag 一律不碰（§零 item 7）。
★ 本判据**只读**，不改任何产品文件。

────────────────────────────────────────────────────────────────────────
★ 2026-10-15 窗E 加固：判据不许再「自己验自己」
────────────────────────────────────────────────────────────────────────
顾问 ZCode 2026-10-15 18:37 指出：上面三条断言的**期望值是每轮 `gate.check(档)`
现问出来的** ⇒ 边框改成复用 `gate.short` 之后，等式退化成 `gate.short == gate.short`
**恒真**。它只能防「边框与闸门分叉」，**防不了「口径本身被放开」**：
往 `gate.SCOPES` 里加一档、把 `short()` 放宽到认 `'Debug'`、或让某档无条件放行 ——
这三种改法，旧判据一律照绿。

所以本文件从 2026-10-15 起带**两条独立参照**，都不再问 gate「应该是什么」：

  【冻结快照表】（断言④、⑤）—— 期望值**写死在本文件里**，来源是产品契约与
      铁律 1「不猜不编」，**不是** `gate.SCOPES` 的当前内容。任何人放宽档位表 /
      放宽 `short()`，④（闸门侧）/⑤（边框侧）立刻照红 —— 不必等边框先跟着改。

  【行为级黑盒】（断言⑥）—— 拿一张沙箱里的真证去**真调一次动作工具**
      （`core.dispatch('ctl_click')` / `('ctl_type')`，外加闸门自己的原子写路径
      `gate.require(..., count=False)`）。判的是**行为**而不是档位表：
      「边框亮着 T 级=可动，而真去调那个档位的工具被闸门拒绝」⇒ 指示器骗人 ⇒ 红。

★ 三条原断言（①②③）**一条没删**，只是从 3 条变成 6 条。
★ ⑥ 为什么不会真的去点/去打字：判据传 `hwnd=0`，`_act_precheck` 的顺序是
  「闸门 check → 租约 → hwnd」，到 hwnd 这一步以 code=4「缺 hwnd」收手，
  **永远走不到 legacy act.py，动作根本发不出去**；`gate.require` 一律 `count=False`
  （非消耗），A 级仍走 `skip_count` 早退（铁律 11⑥）。
  ⇒ 判据只读沙箱：真 auth_pass.json / 真急停 flag / 真事件流一个都不碰。

跑红 / 跑绿（同一份判据，只换 --target）：
    python -X utf8 tests\_2h_wA_档位口径判据.py --target work\_2h_档位口径_红基线_ctrl_border.py
        ⇒ 退出码 1，逐条报红；含 ⑥ 的行为级证据：scopes=['CD'] 那张证，
          边框亮「C 级·打字快捷键」，真调 ctl_type 被闸门 code=9 拒绝。
    python -X utf8 tests\_2h_wA_档位口径判据.py --target src\agentctl\ctrl_border.py
        ⇒ 退出码 0
"""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from agentctl import gate  # noqa: E402  参照系：真闸门

TIERS_ORDER = ("A", "B", "C", "D")  # 档位高低序：低 → 高


# ══════════════════════════════════════════════════════════════════════
# 窗E 加固 · 冻结快照表（期望值写死在本文件，**不许从 gate 现算**）
# ══════════════════════════════════════════════════════════════════════
# 为什么必须写死：原来三条断言的「期望」是每轮 `gate.check(档)` 现问出来的。
# 边框改成复用 gate.short 之后，等式退化成 `gate.short == gate.short` **恒真** ——
# 它只能发现「两边分叉」，发现不了「口径被放开」：往 gate.SCOPES 加一档、
# 把 short() 放宽到认 'Debug'、或让某档无条件放行 —— 这三种改法旧判据一律照绿。
#
# 下面的表是**契约抄本**：A/B/C/D 四档的中文名、以及每类 scopes 写法该被认成什么。
# 权威来源是产品契约（gate.py 的档位表 + 铁律 1「不猜不编」），
# **不是** gate.py 当前的内容 —— 两者一旦分家，④⑤ 就红，这正是要的效果。
# 冻结日期：2026-10-15（窗E）。改这些表 = 改契约，必须走单独立项 + 用户拍板。

# 冻结①：档位全集与中文名。多一档、少一档、改名、改顺序，一律红。
FROZEN_SCOPES = {
    "A": "A级 只读感知",
    "B": "B级 点击滚动",
    "C": "C级 打字快捷键",
    "D": "D级 拖拽",
}
FROZEN_TIER_ORDER = ("A", "B", "C", "D")   # 档位高低序：边框取「最高档」靠它

# 冻结②：gate.short() 的归一口径探针 —— 输入 → 期望输出。
# ★ 后半截全是「认不出就原样返回」那一栏（铁律 1 不猜不编）：
#   'Debug' 若被猜成 'D' ⇒ 边框亮紫（说能拖拽）而闸门一档都不放行 ⇒ 指示器骗人。
#   'a'（小写）也冻结成原样：闸门不认小写 ⇒ 全拒；边框若「顺手」大小写归一，
#   就会亮出一档闸门从没给过的颜色 —— 与 'Debug' 同一个病。
FROZEN_SHORT_PROBES = (
    ("A", "A"), ("B", "B"), ("C", "C"), ("D", "D"),
    ("A级 只读感知", "A"), ("B级 点击滚动", "B"),
    ("C级 打字快捷键", "C"), ("D级 拖拽", "D"),
    ("B级", "B"),
    ("Debug", "Debug"), ("All", "All"), ("ABC", "ABC"),
    ("CD", "CD"), ("A1", "A1"), ("Z", "Z"), ("拖拽", "拖拽"),
    ("a", "a"), ("", ""),
)

# 冻结③：每组用例的 scopes ⇒ **闸门应当放行的档位集合**（断言④用）。
#   键 = tuple(scopes)，必须与下面 CASES 一一对应；对不上判据自己退出码 2
#   （宁可判据跑不起来，也不让它带着半张表说绿）。
FROZEN_GATE = {
    ("A",):               frozenset({"A"}),
    ("B",):               frozenset({"B"}),
    ("C",):               frozenset({"C"}),
    ("D",):               frozenset({"D"}),
    ("B", "C"):           frozenset({"B", "C"}),
    ("A", "B", "C", "D"): frozenset({"A", "B", "C", "D"}),
    ("D级 拖拽",):         frozenset({"D"}),
    ("D级 拖拽", "A"):    frozenset({"A", "D"}),
    ("Debug",):           frozenset(),          # 认不出 ⇒ 一个档都不放行
    ("B", "Debug"):       frozenset({"B"}),  # 认得 B、认不出 Debug ⇒ 只放行 B
    ("All",):             frozenset(),
    ("ABC",):             frozenset(),
    ("A1",):              frozenset(),
    ("CD",):              frozenset(),          # 连写不是通行证写法 ⇒ 全拒
    ("Z",):               frozenset(),
    ("拖拽", "点击滚动"):  frozenset(),
    ():                   frozenset(),
}

# 冻结④：每组用例的 scopes ⇒ **边框应当显示的档位**（断言⑤用）；None = 必须红/隐藏。
#   口径：显示层只许比闸门更严、不许更松 ⇒ 显示 = 闸门放行的**最高**档；
#   闸门一个档都不放行 ⇒ 必须红（fail-closed），绝不猜一个颜色顶上。
FROZEN_BORDER = {
    ("A",): "A", ("B",): "B", ("C",): "C", ("D",): "D",
    ("B", "C"): "C", ("A", "B", "C", "D"): "D",
    ("D级 拖拽",): "D", ("D级 拖拽", "A"): "D",
    ("Debug",): None, ("B", "Debug"): "B",
    ("All",): None, ("ABC",): None, ("A1",): None, ("CD",): None,
    ("Z",): None, ("拖拽", "点击滚动"): None, (): None,
}

# 冻结⑤：**可调用的**动作工具 ↔ 各自要的档位（断言⑥用）。
#   ★ 只有 B（点击滚动）与 C（打字快捷键）有可调用的工具：
#     D（拖拽）在契约里**根本没有**对应工具（窗B·P6 A1 已钉：tools 数组无 drag）；
#     A 是「只读感知」，铁律 11⑥ 零副作用，不属「可动」档。
#     所以 ⑥ 能真调工具的档位就是 B / C —— 派单要的「至少一条」由此满足；
#     没有工具的档位（D）改由闸门自己的原子写路径覆盖（见 probe_tier）。
FROZEN_ACTION_TOOL = {
    "B": ("ctl_click", "B", {"hwnd": 0}),
    "C": ("ctl_type", "C", {"hwnd": 0, "text": "wE"}),
}

# ── 用例：每条 = 一张临时通行证的 scopes 列表 ──
#   ★ 必须含 Debug 反例（派单指定）。Debug 的首字母是 D，
#     旧版 _short_scope 只取首字母 ⇒ 边框显示「D 级·拖拽」，而闸门一个档都不放行。
CASES = [
    (["A"], "单档 A"),
    (["B"], "单档 B"),
    (["C"], "单档 C"),
    (["D"], "单档 D"),
    (["B", "C"], "多档 B+C"),
    (["A", "B", "C", "D"], "全档"),
    (["D级 拖拽"], "闸门完整串写法"),
    (["D级 拖拽", "A"], "完整串 + 单档"),
    (["Debug"], "★反例·首字母陷阱（旧版认成 D）"),
    (["B", "Debug"], "★反例·混档里的首字母陷阱"),
    (["All"], "★首字母陷阱（认成 A）"),
    (["ABC"], "★首字母陷阱（认成 A）"),
    (["A1"], "★首字母陷阱（认成 A）"),
    (["CD"], "★首字母陷阱（认成 C）"),
    (["Z"], "不可识别字母"),
    (["拖拽", "点击滚动"], "中文词，闸门不认"),
    ([], "scopes 为空"),
]


def load_border(path):
    """按文件路径加载一份 ctrl_border（好让判据能指改前旧版去跑红）。"""
    path = os.path.abspath(path)
    spec = importlib.util.spec_from_file_location("_wA_ctrl_border_under_test", path)
    if spec is None or spec.loader is None:
        raise SystemExit("加载失败：%s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, path


def sandbox_gate(tmp):
    """把闸门所有落盘位置改指临时目录 —— 真凭证/真急停一个都不碰。"""
    auth = os.path.join(tmp, "_auth")
    os.makedirs(auth, exist_ok=True)
    gate.PASSFILE = os.path.join(auth, "auth_pass.json")
    gate.AUTHDIR = auth
    gate.LOCKFILE = os.path.join(auth, "gate.lock")
    gate.LOG = os.path.join(auth, "judge.log")
    gate.BRAKE_FLAG = os.path.join(tmp, "_急停_不存在.flag")
    if os.path.exists(gate.BRAKE_FLAG):        # 判据自己也不许被急停污染
        os.remove(gate.BRAKE_FLAG)


def gate_allows_all_tiers():
    """真去问闸门：A/B/C/D 各放行否。这是不复刻、不猜的真值。"""
    out = {}
    for t in TIERS_ORDER:
        ok, why, _code = gate.check(t)
        out[t] = (bool(ok), why)
    return out


def border_shown(border, passfile, brake_flag):
    """问边框：现在显示什么 ⇒ (状态, 档位字母 或 None, 理由)。"""
    state, rgb, why = border.decide(passfile, brake_flag)
    # 档位字母靠颜色反查 TIERS（TIERS 是冻结的只读常量，判据不碰它）
    inv = {v[0]: k for k, v in border.TIERS.items()}
    tier = inv.get(rgb) if state == border.ST_TIER else None
    return state, tier, why


# ══════════════════════════════════════════════════════════════════════
# 窗E 加固 · 独立参照（冻结快照表 + 行为级黑盒）
# ══════════════════════════════════════════════════════════════════════

def load_core(tmp):
    """**延迟**导入 core（真正的动作工具入口），导入前先把落盘位置全指进沙箱。

    ★ 为什么必须延迟到函数里：core 在 import 时就按环境变量定位事件流
      （core.py:37 TIMELINE_DIR = AGENTCTL_TIMELINE、core.py:214 Store(TIMELINE_DIR)），
      而 dispatch() 每调一次都往事件流写一条
      —— 不先指进沙箱，这条判据就会污染**真事件流**。
    ★ gate 侧的 PASSFILE/LOCKFILE/BRAKE_FLAG 等由 sandbox_gate() 在 import 后覆盖
      （本文件原来就这么做）；这里补的是 core 侧那几个
      + AGENTCTL_BORDER_LOG（边框诊断落点）与 AGENTCTL_BORDER_MUTEX（单例互斥量）。
    ★ AGENTCTL_REQUIRE_LEASE=0：只验闸门那一层，不掺租约
      （与窗B 判据 _2h_wB_D级绊线判据.py 的沙箱口径一致）。
    """
    os.environ["AGENTCTL_APP_DIR"] = os.path.join(tmp, "app")
    os.environ["AGENTCTL_TIMELINE"] = os.path.join(tmp, "timeline")
    os.environ["AGENTCTL_REQUIRE_LEASE"] = "0"
    os.environ["AGENTCTL_BORDER_LOG"] = os.path.join(tmp, "ctrl_border.log")
    os.environ["AGENTCTL_BORDER_MUTEX"] = "Local\\_wE_%d" % os.getpid()
    from agentctl import core                        # noqa: E402  延迟导入
    return core


def write_pass(passfile, scopes, far):
    """在沙箱里铺一张临时通行证。一律「没过期、没 used 完」⇒ 只剩 scopes 一个变量。"""
    with open(passfile, "w", encoding="utf-8") as f:
        json.dump({"scopes": list(scopes), "acts": 0, "acts_limit": 99,
                   "expires_at": far}, f, ensure_ascii=False)


def frozen_table_gaps():
    """冻结表与 CASES 对不上就**自己退出码 2** —— 宁可跑不起来，也不带着半张表说绿。"""
    keys = [tuple(s) for s, _ in CASES]
    miss_g = [k for k in keys if k not in FROZEN_GATE]
    miss_b = [k for k in keys if k not in FROZEN_BORDER]
    extra_g = [k for k in FROZEN_GATE if k not in set(keys)]
    extra_b = [k for k in FROZEN_BORDER if k not in set(keys)]
    if miss_g or miss_b or extra_g or extra_b:
        return ("冻结表与 CASES 对不上 —— 用例缺 FROZEN_GATE: %s / 缺 FROZEN_BORDER: %s / "
                "多出来: gate=%s border=%s"
                % (miss_g or "无", miss_b or "无", extra_g or "无", extra_b or "无"))
    if tuple(FROZEN_SCOPES) != FROZEN_TIER_ORDER:
        return "FROZEN_SCOPES 的键序与 FROZEN_TIER_ORDER 不一致"
    return ""


def check_frozen_tier_table():
    """断言④.1：闸门的档位表必须**逐字**等于冻结抄本（加档/删档/改名/改顺序都红）。"""
    reds = []
    actual = dict(getattr(gate, "SCOPES", {}) or {})
    if actual != FROZEN_SCOPES:
        added = sorted(set(actual) - set(FROZEN_SCOPES))
        gone = sorted(set(FROZEN_SCOPES) - set(actual))
        changed = sorted(k for k in set(actual) & set(FROZEN_SCOPES)
                         if actual[k] != FROZEN_SCOPES[k])
        reds.append("gate.SCOPES 与冻结抄本不一致：多出 %s / 少了 %s / 改名 %s"
                    % (added or "无", gone or "无", changed or "无"))
    if tuple(actual) != FROZEN_TIER_ORDER:
        reds.append("gate.SCOPES 的档位顺序变成 %s（冻结为 %s）—— 边框取「最高档」靠的就是这个顺序"
                    % (tuple(actual), FROZEN_TIER_ORDER))
    return reds


def check_frozen_short_probes():
    """断言④.2：gate.short() 的归一口径逐条相符 —— 它被放宽/收紧一律红。"""
    reds = []
    short = getattr(gate, "short", None)
    if short is None:
        return ["gate 没有 short() ⇒ 档位归一口径已不可验"]
    for raw, want in FROZEN_SHORT_PROBES:
        try:
            got = short(raw)
        except Exception as e:
            reds.append("gate.short(%r) 抛异常：%s: %s" % (raw, type(e).__name__, e))
            continue
        if got != want:
            reds.append("gate.short(%r) = %r，冻结期望 %r ⇒ 归一口径被放宽/收紧了"
                        % (raw, got, want))
    return reds


def gate_refused(envelope):
    """这个信封是不是**闸门拒绝**（而不是工具自身的参数校验之类的别的理由）。"""
    try:
        if envelope.get("ok") is not False:
            return False
        return int(envelope.get("code") or 0) in (gate.CODE_UNAUTH, gate.CODE_BRAKE)
    except Exception:
        return False


def probe_tier(core, tier):
    """断言⑥的核心：**边框亮着 tier ⇒ 真去调那个档位的动作**。

    返回 (是否红, 证据)。判据要看的是**行为**：调下去被闸门拒 ⇒ 指示器骗人。

    ① 先真调一次动作工具（只有 B/C 有；D 无拖拽工具、A 是只读感知不属可动）。
    ② 再走一次闸门自己的原子写路径 gate.require(..., count=False)（非消耗）——
       覆盖「没有工具但边框照样宣称可动」的 D 档，也顺带钉住铁律 11⑥
       （A 级 skip_count 早退、一次都不扣）。
    ★ 安全：hwnd=0 ⇒ _act_precheck 收手在「缺 hwnd」(code=4)，
      永远走不到 legacy act.py ⇒ **动作根本发不出去**；require 一律 count=False。
    """
    entry = FROZEN_ACTION_TOOL.get(tier)
    if entry:
        tool, want_scope, args = entry
        try:
            env_ = core.dispatch(tool, dict(args), actor="wE_probe")
        except Exception as e:
            return True, "真调 core.dispatch(%r) 抛异常 %s: %s —— 判据自身不可信" % (
                tool, type(e).__name__, e)
        if gate_refused(env_):
            return True, ("边框亮「%s 级」，而真调 core.dispatch(%r)（要 %s 级）被闸门拒绝："
                          "code=%s %s ⇒ 指示器骗人"
                          % (tier, tool, want_scope, env_.get("code"),
                             str(env_.get("warning") or "")[:70]))
        tool_note = ("真调 core.dispatch(%r) ⇒ code=%s %s（闸门这一关放行了，收手在"
                     "工具自身的参数校验上，动作没发出去）"
                     % (tool, env_.get("code"), str(env_.get("warning") or "")[:40]))
    else:
        tool_note = ("%s 级**没有**可调用的动作工具（A=只读感知不属可动；D=拖拽在契约里"
                     "没有对应工具，窗B·P6 A1 已钉）" % tier)
    try:
        gate.require(tier, note="wE 黑盒", count=False)
    except gate.GateDenied as e:
        return True, ("边框亮「%s 级」，而闸门自己的原子写路径 gate.require(%r) 拒绝：%s"
                      " ⇒ 指示器骗人" % (tier, tier, e.why))
    except Exception as e:
        return True, "gate.require(%r) 抛异常 %s: %s" % (tier, type(e).__name__, e)
    return False, tool_note + "；gate.require(%r, count=False) 也放行" % tier


def cleanup_sandbox(tmp, tries=5):
    """把沙箱目录收干净；收不掉就**如实说路径**，不静默 ignore_errors。

    ★ 为什么加这一段（窗E 收口时实测出来的）：本文件为了 ⑥ 会 import core，
      而 core 的事件流 Store（store.py 的 _conn()）每次 append 都是
      sqlite3.connect(...) 配 with ... as c: —— **只 commit 不 close**，
      句柄要等 GC 才释放。Windows 上文件被占着直接 WinError 32，
      shutil.rmtree(ignore_errors=True) 会**一声不响地留下半个目录**
      （实测：%TEMP% 里每次多一个 _wA_口径_xxxx，里面只剩 timeline\timeline.db）。
      改前没有 import core，所以没这个问题 —— 这是引入 ⑥ 带来的回退，本段负责抹平。
    ★ 兜底：gc.collect() 之后仍有界重试；再不行就把残留路径打出来，
      让下一个人一眼看见，而不是以为已经清干净了。
    """
    for i in range(max(1, tries)):
        gc.collect()
        shutil.rmtree(tmp, ignore_errors=True)
        if not os.path.exists(tmp):
            return ""
        time.sleep(0.2 * (i + 1))
    left = []
    for root, _dirs, files in os.walk(tmp):
        for fn in files[:5]:
            left.append(os.path.join(root, fn))
        if len(left) >= 5:
            break
    return ("沙箱没能完全删掉（Store 的 sqlite 句柄还占着）：%s；残留 %s —— "
            "这是临时目录，下次清 %TEMP% 即可，不影响任何产品文件"
            % (tmp, left or "（空）"))


def main():
    ap = argparse.ArgumentParser(
        description="边框 vs 闸门 档位口径判据（窗A P3 · 窗E 加固：冻结快照表 + 行为级黑盒）")
    ap.add_argument("--target",
                    default=os.path.join(ROOT, "src", "agentctl", "ctrl_border.py"),
                    help="被测的 ctrl_border.py 路径（指向旧版即可跑红）")
    args = ap.parse_args()

    # 冻结表与用例对不上 ⇒ 判据自己不可信 ⇒ 退出码 2（**不是**红，也不是绿）。
    # 宁可跑不起来，也不要一张半表说绿 —— 那正是本单要治的病。
    gap = frozen_table_gaps()
    if gap:
        print("判据自身不可信：%s" % gap)
        return 2

    border, target = load_border(args.target)
    tmp = tempfile.mkdtemp(prefix="_wA_口径_")
    sandbox_gate(tmp)
    core = load_core(tmp)               # 窗E：真动作工具入口（延迟导入，落盘全进沙箱）
    brake_flag = gate.BRAKE_FLAG
    passfile = gate.PASSFILE
    far = time.time() + 3600            # 一律「没过期、没 used 完」⇒ 只剩 scopes 一个变量

    print("=" * 112)
    print("窗A P3 · 边框档位口径判据")
    print("被测 ctrl_border : %s" % target)
    print("参照 gate        : %s（真调 gate.check 取真值，不复刻口径）"
          % os.path.join(ROOT, "src", "agentctl", "gate.py"))
    print("沙箱 PASSFILE    : %s" % passfile)
    print("=" * 112)
    print("%-28s %-16s %-22s %s" % ("通行证 scopes 里写的", "闸门放行的档", "边框显示", "边框档位"))
    print("-" * 112)

    bad_more = []      # 断言①：比闸门更宽（危险方向）
    bad_red = []       # 断言②：该红没红
    bad_mismatch = []  # 断言③：口径不一致（含虚低）

    for scopes, note in CASES:
        write_pass(passfile, scopes, far)

        verdict = gate_allows_all_tiers()
        gset = [t for t in TIERS_ORDER if verdict[t][0]]
        gtop = gset[-1] if gset else None

        state, btier, why = border_shown(border, passfile, brake_flag)
        mark = ""
        # ① 危险方向：边框亮出的档，闸门必须真放行，且不得高于闸门放行的最高档
        if btier is not None:
            if not verdict[btier][0]:
                bad_more.append((scopes, btier, gtop,
                                 "边框亮 %s 级，闸门实际不放行 ⇒ 指示器骗人" % btier))
                mark = "  ★★①红"
            elif gtop is not None and TIERS_ORDER.index(btier) > TIERS_ORDER.index(gtop):
                bad_more.append((scopes, btier, gtop,
                                 "边框亮 %s 级，高于闸门放行的最高档 %s" % (btier, gtop)))
                mark = "  ★★①红"
        # ② 闸门一个档都不放行 ⇒ 边框必须红（不许亮档位色）
        if not gset and btier is not None:
            bad_red.append((scopes, btier, "闸门 A/B/C/D 全拒，边框却亮 %s 级" % btier))
            mark += "  ★★②红"
        # ③ 单一源：边框档位必须恰好 == 闸门放行的最高档
        if btier != gtop:
            bad_mismatch.append((scopes, btier, gtop,
                                 "边框 %s ≠ 闸门最高 %s"
                                 % (btier or ("红" if state == border.ST_RED else "隐藏"),
                                    gtop or "全拒")))
            mark += "  ★③红"

        if btier is None:
            shown = state + "（fail-closed）"
        else:
            shown = "%s 级·%s" % (btier, border.TIERS[btier][1].split("·")[-1].rstrip("）"))
        print("%-28s %-16s %-22s %s%s"
              % (repr(scopes)[:28], "、".join(gset) or "（全拒）", shown[:22],
                 btier or "-", mark))

    print("-" * 112)
    print("用例 %d 组（含 Debug 反例 2 组：scopes=['Debug'] 与 ['B','Debug']）" % len(CASES))
    for title, rows in (
        ("断言① 边框档位不比闸门更宽（危险方向）", bad_more),
        ("断言② 闸门全拒时边框必须红", bad_red),
        ("断言③ 边框档位 == 闸门放行最高档（口径单一源）", bad_mismatch),
    ):
        print("")
        print("%s —— 红 %d 组" % (title, len(rows)))
        for row in rows:
            scopes, btier, why_ = row[0], row[1], row[-1]
            gtop = row[2] if len(row) >= 4 else "全拒"
            print("   · scopes=%-24s 边框=%-4s 闸门=%-4s  %s"
                  % (repr(scopes), btier or "-", gtop or "全拒", why_))

    # ══════════════════════════════════════════════════════════════════════
    # 窗E 加固 · 断言④⑤ 冻结快照表（期望写死在本文件，**不再问 gate「应该是什么」**）
    # ══════════════════════════════════════════════════════════════════════
    print("")
    print("=" * 112)
    print("窗E 加固 · ④⑤ 冻结快照表（期望抄本写死在判据里；有人放开口径会撞这张表）")
    print("=" * 112)
    bad_frozen_gate = []     # 断言④：闸门实际口径 ≠ 冻结抄本
    bad_frozen_border = []   # 断言⑤：边框实际显示 ≠ 冻结抄本

    reds = check_frozen_tier_table()
    if reds:
        bad_frozen_gate += [("gate.SCOPES", "-", r) for r in reds]
    else:
        print("  [绿] ④.1 gate.SCOPES 逐字 == 冻结抄本（顺序也冻结为 A<B<C<D）：%s"
              % "、".join("%s=%s" % kv for kv in FROZEN_SCOPES.items()))

    reds = check_frozen_short_probes()
    if reds:
        bad_frozen_gate += [("gate.short()", "-", r) for r in reds]
    else:
        print("  [绿] ④.2 gate.short() %d 条探针逐条相符"
              "（'Debug'/'All'/'ABC'/'CD'/'a' 一律原样返回 —— 铁律 1 不猜不编）"
              % len(FROZEN_SHORT_PROBES))

    print("")
    print("  %-28s %-14s %-14s %-16s %s"
          % ("scopes", "冻结闸门放行", "实测闸门放行", "冻结边框显示", "实测边框"))
    print("  " + "-" * 108)
    for scopes, note in CASES:
        key = tuple(scopes)
        write_pass(passfile, scopes, far)
        verdict = gate_allows_all_tiers()
        gset = [t for t in TIERS_ORDER if verdict[t][0]]
        _state, btier, _why = border_shown(border, passfile, brake_flag)
        want_g, want_b = FROZEN_GATE[key], FROZEN_BORDER[key]
        want_s = "、".join(sorted(want_g)) or "（全拒）"
        got_s = "、".join(gset) or "（全拒）"
        mark = ""
        if frozenset(gset) != want_g:
            bad_frozen_gate.append((
                repr(scopes), got_s,
                "闸门实际放行 %s，冻结期望 %s ⇒ 档位口径被放开了" % (got_s, want_s)))
            mark += "  ★★④红"
        if btier != want_b:
            bad_frozen_border.append((
                repr(scopes), btier or "红/隐藏",
                "边框实际显示 %s，冻结期望 %s ⇒ 显示层与契约分家"
                % (btier or "红/隐藏", want_b or "红/隐藏")))
            mark += "  ★★⑤红"
        print("  %-28s %-14s %-14s %-16s %s%s"
              % (repr(scopes)[:28], want_s, got_s, want_b or "（红/隐藏）",
                 btier or "（红/隐藏）", mark))

    print("")
    print("断言④ 冻结闸门口径（档位表 + short 探针 + 每组用例） —— 红 %d 条"
          % len(bad_frozen_gate))
    for row in bad_frozen_gate:
        print("   · %-30s %s" % (row[0], row[2]))
    print("断言⑤ 冻结边框口径（每组用例边框**应当**显示的档位） —— 红 %d 条"
          % len(bad_frozen_border))
    for row in bad_frozen_border:
        print("   · %-30s %s" % (row[0], row[2]))
    print("")
    print("  [注] 边框配色只**登记**不判红（派单 §零.5 冻结）：%s"
          % "、".join("%s=0x%02X%02X%02X" % (k, v[0][0], v[0][1], v[0][2])
                      for k, v in border.TIERS.items()))

    # ══════════════════════════════════════════════════════════════════════
    # 窗E 加固 · 断言⑥ 行为级黑盒
    #   判的是「边框亮着 T 级（=可动），而真去调那个档位的动作工具被闸门拒绝」⇒ 红。
    #   这条与档位表长什么样无关：它是**行为**，不是抄表。
    # ══════════════════════════════════════════════════════════════════════
    print("")
    print("=" * 112)
    print("窗E 加固 · ⑥ 行为级黑盒：真调一次动作工具，判「边框亮 X 级而闸门拒绝」")
    print("=" * 112)
    print("  安全：判据传 hwnd=0 ⇒ _act_precheck 收手在 code=4「缺 hwnd」，")
    print("        永远走不到 legacy act.py ⇒ **动作根本没发出去**；require 一律 count=False。")
    print("")
    bad_behavior = []
    for scopes, note in CASES:
        write_pass(passfile, scopes, far)
        _state, btier, _why = border_shown(border, passfile, brake_flag)
        if btier is None:
            print("  [—] scopes=%-26s 边框红/隐藏 ⇒ 没宣称任何可动档位，无需调工具"
                  % repr(scopes)[:26])
            continue
        is_red, evidence = probe_tier(core, btier)
        if is_red:
            bad_behavior.append((repr(scopes), btier, evidence))
        print("  [%s] scopes=%-26s 边框亮 %s 级 ⇒ %s"
              % ("红" if is_red else "绿", repr(scopes)[:26], btier, evidence))
    print("")
    print("断言⑥ 边框亮出的可动档位必须真调得动 —— 红 %d 组" % len(bad_behavior))

    total = (len(bad_more) + len(bad_red) + len(bad_mismatch)
             + len(bad_frozen_gate) + len(bad_frozen_border) + len(bad_behavior))
    print("")
    print("=" * 112)
    if total:
        print("结论：RED —— 红 %d 条"
              "（①危险方向 %d / ②该红没红 %d / ③口径分叉 %d /"
              " ④冻结闸门口径 %d / ⑤冻结边框口径 %d / ⑥行为级黑盒 %d）"
              % (total, len(bad_more), len(bad_red), len(bad_mismatch),
                 len(bad_frozen_gate), len(bad_frozen_border), len(bad_behavior)))
    else:
        print("结论：GREEN —— ①边框档位 ⊆ ②闸门档位；认不出的串一律红（fail-closed）；"
              "③边框档位 == 闸门放行最高档；④闸门口径 == 冻结抄本；"
              "⑤边框显示 == 冻结抄本；⑥边框亮出的可动档位真调得动。")
    print("=" * 112)

    gate.PASSFILE = None
    gate.LOCKFILE = None
    gate.LOG = None
    leftover = cleanup_sandbox(tmp)
    if leftover:
        print("")
        print("[注] %s" % leftover)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
