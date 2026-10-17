# -*- coding: utf-8 -*-
r"""永久判据 · 窗F —— 边框配色 / 厚度 / 反色描边**冻结钉子**（2026-10-15）

判的问题（派单 §一.3）：
    配色是用户 2026-10-15 20:41 拍板、DSH 定死五个值的一件事。
    档位色一改，用户屏幕上「这根线是什么意思」就变了 —— 而**没人会再记得改过**。
    所以把它**写死成快照**钉在判据里：谁改了配色，这条立刻红。

为什么不能问「当前值是多少」再跟它自己比（窗E 的教训，同一个病）：
    判据若从 ctrl_border 现读期望值，等式退化成 x == x **恒真**，等于没钉。
    本文件里的 FROZEN_* 全部是**字面量**，来源是用户拍板的那张表，不是代码当前内容。

十条断言（任一红 ⇒ 退出码 1）：
    ① 四档 RGB 逐字节 == 冻结值（改一个通道就红）
    ② 每档厚度 == 冻结值（A3 / B4 / C5 / D6）
    ③ 急停红 == 冻结值 #F44336，且红**不在** A–D 渐变里（红只表状态，不占档位）
    ④ 每档反色描边色 == 冻结值，且与「L* 分界」这条规则一致（表和规则都钉）
    ⑤ 几何：描边占**最内一圈**、宽 1px，且**总厚不变**（body+edge == 冻结厚度）
    ⑥ 显式 --thickness 仍然压得住一切（老用法不许被配色改死）
    ⑦ 亮度逐档单调下降（A>B>C>D）—— 防「深浅乱跳」复发（派单 §理由①）
    ⑧ 契约 contract/tools.json 的四行颜色名 == 冻结名，且带同一个 hex；
       旧色名（黄/橙/紫）一个字都不许残留
    ⑨ 契约里的 hex == 代码里的 RGB（两份真相不许分家）
    ⑩ schema_version 仍是 5 —— **改配色不许动它**（见下面 §为什么）

★ §为什么 schema_version 不许跟着配色升（派单 §一.2 的硬要求）：
    schema_version 记的是**工具 schema 的形状**（字段名、types、enum、required、
    错误码），是给「写 agent 代码的人」看的兼容承诺。本轮改动只是 conventions 里
    几段**描述文案**里的颜色名 —— 工具怎么调、一个字段都没变。
    升它等于对外宣称「接口不兼容」，那是谎报；而且 tests\check_t1_w1.py 里的
    「契约-schema_version=5」断言会当场变红（那是另一条线的活）。
    ⇒ 配色改了、契约颜色名同步了，**schema_version 未动**（理由如上）。

跑红（同一份判据，只换 --target）：
    python -X utf8 tests\_2h_wF_配色冻结判据.py --target work\_2h_档位口径_红基线_ctrl_border.py
        ⇒ 退出码 1（红基线是改色之前的版本：蓝黄橙紫、全局 8px、无描边）
    python -X utf8 tests\_2h_wF_配色冻结判据.py --target src\agentctl\ctrl_border.py
        ⇒ 退出码 0

★ 本判据**只读**：不建窗口、不写任何产品文件、不碰真通行证 / 急停 flag / 事件流。
★ 几何那条验的是**纯函数** _edge_geometry()（只算矩形，不碰 Win32），
  所以跑判据时屏幕上不会闪出边框。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ══════════════════════════════════════════════════════════════════════
# ★ 冻结快照（**字面量**，来源＝用户 2026-10-15 20:41 拍板的色卡③建议档）
#   改这里 = 改契约，必须走单独立项 + 用户重新拍板，并同步改本段历史记录。
# ══════════════════════════════════════════════════════════════════════
FROZEN_RGB = {                 # 档位 → RGB（十六进制，与 tools.json 里写的同一份）
    "A": (0x8F, 0xD8, 0xFF),   # 浅蓝  L*=83.0  只读感知
    "B": (0x3B, 0xA6, 0xF0),   # 中浅蓝 L*=65.4 点击滚动
    "C": (0x15, 0x65, 0xC0),   # 中深蓝 L*=43.2 打字快捷键
    "D": (0x0B, 0x3D, 0x91),   # 深蓝  L*=28.0  拖拽（最厚，封顶 6）
}
FROZEN_LSTAR = {"A": 83.0, "B": 65.4, "C": 43.2, "D": 28.0}
FROZEN_BRAKE_RGB = (0xF4, 0x43, 0x36)     # 急停/过期 红，本单**未动**
FROZEN_BRAKE_LSTAR = 55.6
FROZEN_THICKNESS = {"A": 3, "B": 4, "C": 5, "D": 6}   # 物理 px，逐档加深
FROZEN_BRAKE_THICKNESS = 6                             # 红用 6（=D 档厚度）
FROZEN_EDGE_DARK = (0x0A, 0x0E, 0x14)     # 亮档配深描边
FROZEN_EDGE_LIGHT = (0xF5, 0xF8, 0xFF)    # 暗档配浅描边
FROZEN_EDGE = {
    "A": FROZEN_EDGE_DARK,    # L*=83.0 > 55
    "B": FROZEN_EDGE_DARK,    # L*=65.4 > 55
    "C": FROZEN_EDGE_LIGHT,   # L*=43.2 ≤ 55
    "D": FROZEN_EDGE_LIGHT,   # L*=28.0 ≤ 55
}
FROZEN_BRAKE_EDGE = FROZEN_EDGE_DARK      # L*=55.6 > 55
FROZEN_EDGE_RULE_THRESHOLD = 55.0         # L* 分界：> 55 配深描边，≤ 55 配浅描边
FROZEN_SCHEMA_VERSION = 5                 # 改配色**不许**动它（§为什么）
FROZEN_EXPLICIT_THICKNESS = 12            # 验「显式 --thickness 压得住」用的探针值
FROZEN_MONITOR = (0, 0, 1920, 1080)       # 几何验算用的假屏幕，纯算术

# 契约里的四行颜色名（conventions.edge_border_hint）。冻结的是**文案**，一字不许差。
FROZEN_CONTRACT_NAMES = {
    "A": "浅蓝 #8FD8FF｜只读感知｜只能看，不动",
    "B": "中浅蓝 #3BA6F0｜点击滚动",
    "C": "中深蓝 #1565C0｜打字快捷键｜多档取最高（A+B+C → C）",
    "D": "深蓝 #0B3D91｜拖拽",
}
# 旧色名（改色前的 蓝/黄/橙/紫）。黄/橙/紫 必须从 edge_border_hint 里**消失干净**。
RETIRED_CONTRACT_COLORS = ("黄", "橙", "紫")


def hexs(rgb):
    return "#%02X%02X%02X" % tuple(rgb)


def _lin(c8):
    """8 位 sRGB 分量 → 线性光（IEC 61966-2-1）。判据自带一份，不问被测件。"""
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (_lin(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def lstar(rgb):
    y = luminance(rgb)
    if y <= 216.0 / 24389.0:
        return y * (24389.0 / 27.0)
    return 116.0 * (y ** (1.0 / 3.0)) - 16.0


def load_border(path):
    """按路径加载一份 ctrl_border（好让判据能指改前旧版去跑红）。"""
    path = os.path.abspath(path)
    spec = importlib.util.spec_from_file_location("_wF_ctrl_border_under_test", path)
    if spec is None or spec.loader is None:
        raise SystemExit("加载失败：%s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, path


class Report:
    """逐条记判定；红条目连同**为什么红**一起留下，别只丢个计数。"""

    def __init__(self):
        self.reds = []

    def check(self, name, ok, why=""):
        tag = "绿" if ok else "红"
        print("  [%s] %s%s" % (tag, name, "" if ok else "  ←★ %s" % why))
        if not ok:
            self.reds.append((name, why))
        return ok


def check_rgb(mod, rep, tiers_attr, frozen, label):
    """比对一张 {档: (rgb, desc)} 表里各档的 RGB（只比 rgb，desc 不归本条管）。"""
    table = getattr(mod, tiers_attr, None)
    if not isinstance(table, dict):
        rep.check("%s 表存在" % label, False,
                  "模块里没有 %s（改名/删掉？）⇒ 本条无法验" % tiers_attr)
        return
    for tier in ("A", "B", "C", "D"):
        got = table.get(tier)
        rgb = tuple(got[0]) if isinstance(got, (tuple, list)) and got else None
        rep.check("①%s %s 档 RGB = %s" % (label, tier, hexs(frozen[tier])),
                  rgb == frozen[tier],
                  "实际 %s，冻结 %s" % (hexs(rgb) if rgb else "（缺）", hexs(frozen[tier])))


def check_thickness(mod, rep, frozen, brake_frozen, label="②"):
    """按档厚度。红单独比（BRAKE_THICKNESS）。"""
    tbl = getattr(mod, "TIER_THICKNESS", None)
    if not isinstance(tbl, dict):
        rep.check("%s 每档厚度表存在" % label, False,
                  "模块里没有 TIER_THICKNESS ⇒ 厚度可能退回全局单值了")
    else:
        for tier in ("A", "B", "C", "D"):
            rep.check("%s %s 档厚度 = %d px" % (label, tier, frozen[tier]),
                      tbl.get(tier) == frozen[tier],
                      "实际 %r，冻结 %d" % (tbl.get(tier), frozen[tier]))
    rep.check("② 红厚度 = %d px" % brake_frozen,
              getattr(mod, "BRAKE_THICKNESS", None) == brake_frozen,
              "实际 %r，冻结 %d" % (getattr(mod, "BRAKE_THICKNESS", None), brake_frozen))
    # resolve_style 是运行期真正取厚度的那个函数 —— 表对了它也得跟着对
    fn = getattr(mod, "resolve_style", None)
    if fn is None:
        rep.check("② resolve_style() 存在", False, "运行期取厚度的入口不在了")
        return
    for tier in ("A", "B", "C", "D"):
        rgb = tuple(mod.TIERS[tier][0])
        try:
            t, _edge = fn(rgb)
        except Exception as e:
            rep.check("② resolve_style(%s 档) 厚度 = %d" % (tier, frozen[tier]), False,
                      "抛异常 %s: %s" % (type(e).__name__, e))
            continue
        rep.check("② resolve_style(%s 档) 厚度 = %d" % (tier, frozen[tier]),
                  t == frozen[tier], "实际 %r，冻结 %d" % (t, frozen[tier]))
    brgb = tuple(mod.BRAKE_COLOR[0])
    try:
        t, _edge = fn(brgb)
        rep.check("② resolve_style(红) 厚度 = %d" % brake_frozen, t == brake_frozen,
                  "实际 %r，冻结 %d" % (t, brake_frozen))
    except Exception as e:
        rep.check("② resolve_style(红) 厚度 = %d" % brake_frozen, False,
                  "抛异常 %s: %s" % (type(e).__name__, e))


def check_brake_not_in_ramp(mod, rep):
    """③红 == 冻结值，且红**不占档位**：与 A–D 任何一档都不同。"""
    got = getattr(mod, "BRAKE_COLOR", None)
    rgb = tuple(got[0]) if isinstance(got, (tuple, list)) and got else None
    rep.check("③ 急停红 = %s" % hexs(FROZEN_BRAKE_RGB), rgb == FROZEN_BRAKE_RGB,
              "实际 %s，冻结 %s" % (hexs(rgb) if rgb else "（缺）", hexs(FROZEN_BRAKE_RGB)))
    tiers = getattr(mod, "TIERS", {}) or {}
    for tier in FROZEN_RGB:
        v = tiers.get(tier)
        trgb = tuple(v[0]) if isinstance(v, (tuple, list)) and v else None
        rep.check("③ 红不占档位（%s 档 ≠ 红）" % tier,
                  trgb is not None and trgb != FROZEN_BRAKE_RGB,
                  "%s 档实际就是红 ⇒ 红混进渐变里，状态与档位就分不开了" % tier)


def check_edge(mod, rep):
    """④每档反色描边色 == 冻结值，且与 L* 分界规则一致（表 + 规则都钉）。"""
    tbl = getattr(mod, "TIER_EDGE", None)
    fn = getattr(mod, "resolve_style", None)
    if not isinstance(tbl, dict):
        rep.check("④ 反色描边表存在", False, "模块里没有 TIER_EDGE")
    else:
        for tier in ("A", "B", "C", "D"):
            rep.check("④ %s 档反色描边 = %s" % (tier, hexs(FROZEN_EDGE[tier])),
                      tuple(tbl.get(tier) or ()) == FROZEN_EDGE[tier],
                      "实际 %s，冻结 %s"
                      % (hexs(tbl[tier]) if tbl.get(tier) else "（缺）",
                         hexs(FROZEN_EDGE[tier])))
        b_edge = getattr(mod, "BRAKE_EDGE", None)
        rep.check("④ 红反色描边 = %s" % hexs(FROZEN_BRAKE_EDGE),
                  tuple(b_edge or ()) == FROZEN_BRAKE_EDGE,
                  "实际 %s，冻结 %s"
                  % (hexs(b_edge) if b_edge else "（缺）", hexs(FROZEN_BRAKE_EDGE)))
    # 规则侧：L* > 55 ⇒ 深描边。规则和表必须一致，否则改一边就静默分家。
    rule = getattr(mod, "pick_edge", None) or getattr(mod, "_pick_edge", None)
    if rule is None:
        rep.check("④ 描边规则函数存在", False, "pick_edge()/_pick_edge() 不在，规则无从验")
        return
    if fn is None:
        rep.check("④ resolve_style() 存在", False, "运行期取描边色的入口不在了")
        return
    for tier in ("A", "B", "C", "D"):
        rgb = FROZEN_RGB[tier]
        want = FROZEN_EDGE[tier]
        try:
            got_rule = tuple(rule(rgb) or ())
            _t, got_style = fn(rgb)
            got_style = tuple(got_style or ())
        except Exception as e:
            rep.check("④ %s 档描边规则" % tier, False,
                      "抛异常 %s: %s" % (type(e).__name__, e))
            continue
        rep.check("④ %s 档描边：规则 %s / 运行期 %s / 冻结 %s"
                  % (tier, hexs(got_rule), hexs(got_style), hexs(want)),
                  got_rule == want and got_style == want,
                  "规则给 %s、运行期给 %s、冻结 %s"
                  % (hexs(got_rule), hexs(got_style), hexs(want)))
    try:
        _t, got_style = fn(FROZEN_BRAKE_RGB)
        got_style = tuple(got_style)
        rep.check("④ 红描边：运行期 %s / 冻结 %s" % (hexs(got_style), hexs(FROZEN_BRAKE_EDGE)),
                  got_style == FROZEN_BRAKE_EDGE,
                  "实际 %s，冻结 %s" % (hexs(got_style), hexs(FROZEN_BRAKE_EDGE)))
    except Exception as e:
        rep.check("④ 红描边规则", False, "抛异常 %s: %s" % (type(e).__name__, e))


def check_geometry(mod, rep):
    """⑤描边占最内一圈、宽 1px、**总厚不变**（body+edge == 冻结厚度）。

    验的是纯函数 _edge_geometry()（只算矩形，不建窗）⇒ 跑判据屏幕上不闪边框。
    """
    geo = getattr(mod, "_edge_geometry", None)
    if geo is None:
        rep.check("⑤ 几何函数存在", False,
                  "模块里没有 _edge_geometry() ⇒ 描边画在哪、算不算加厚，无从验")
        return
    x1, y1, x2, y2 = FROZEN_MONITOR
    for tier in ("A", "B", "C", "D"):
        t = FROZEN_THICKNESS[tier]
        try:
            rects = list(geo(x1, y1, x2, y2, t))
        except Exception as e:
            rep.check("⑤ %s 档几何" % tier, False,
                      "_edge_geometry 抛异常 %s: %s" % (type(e).__name__, e))
            continue
        # 期望结构：四条边各 1 个 body(t-1) + 1 个 edge(1) ⇒ 8 个矩形
        bodies = [r for r in rects if r[-1] == "body"]
        edges = [r for r in rects if r[-1] == "edge"]
        others = [r for r in rects if r[-1] not in ("body", "edge")]
        ok = (len(bodies) == 4 and len(edges) == 4 and not others and len(rects) == 8)
        rep.check("⑤ %s 档：body×4 + edge×4（描边另占一圈窗口，不叠在主色上）" % tier,
                  ok, "实际 %d 个矩形（body %d / edge %d / 其它 %d）%r"
                  % (len(rects), len(bodies), len(edges), len(others), rects[:2]))
        if not ok:
            continue
        # 矩形是 (标签, x, y, 宽, 高, kind) ⇒ 宽=r[3]、高=r[4]
        bad = [r for r in rects if r[3] <= 0 or r[4] <= 0]
        rep.check("⑤ %s 档：所有矩形宽高都 > 0" % tier, not bad,
                  "有零/负尺寸的矩形：%r" % (bad,))

        def inward(rects_, side):
            """从屏幕边缘往里数，这条边最远落在第几 px（含描边）。"""
            side_rects = [r for r in rects_ if r[0] == side]
            if side == "top":
                return max(r[2] + r[4] for r in side_rects) - y1
            if side == "bottom":
                return y2 - min(r[2] for r in side_rects)
            if side == "left":
                return max(r[1] + r[3] for r in side_rects) - x1
            return x2 - min(r[1] for r in side_rects)      # 右边：从右边缘往左数

        per_side = dict((s, inward(rects, s)) for s in ("top", "bottom", "left", "right"))
        rep.check("⑤ %s 档：描边**不增加总厚**，四边总厚都是 %d px" % (tier, t),
                  all(v == t for v in per_side.values()),
                  "实测 %r，冻结 %d（描边必须是主色最内那一圈，不是加出去的一圈）"
                  % (per_side, t))
        # 描边必须在**最内**（离屏幕边缘最远）那一圈，且宽恰 1、主色恰 t-1
        inner_ok, why = True, ""
        for side in ("top", "bottom", "left", "right"):
            side_rects = [r for r in rects if r[0] == side]
            if len(side_rects) != 2:
                inner_ok, why = False, "%s 边有 %d 个矩形（要 2：body + edge）" % (
                    side, len(side_rects))
                break
            edge_r = [r for r in side_rects if r[-1] == "edge"][0]
            body_r = [r for r in side_rects if r[-1] == "body"][0]
            if side in ("top", "bottom"):
                size_e, size_b = edge_r[4], body_r[4]      # 高
                pos_e, pos_b = edge_r[2], body_r[2]        # y
                want_e = y1 + t - 1 if side == "top" else y2 - t
                want_b = y1 if side == "top" else y2 - t + 1
            else:
                size_e, size_b = edge_r[3], body_r[3]      # 宽
                pos_e, pos_b = edge_r[1], body_r[1]        # x
                want_e = x1 + t - 1 if side == "left" else x2 - t
                want_b = x1 if side == "left" else x2 - t + 1
            if size_e != 1 or size_b != t - 1:
                inner_ok = False
                why = "%s 边描边宽 %d（要 1）、主色宽 %d（要 %d）" % (
                    side, size_e, size_b, t - 1)
                break
            if pos_e != want_e or pos_b != want_b:
                inner_ok = False
                why = "%s 边描边没落在最内一圈（描边 @%d 期望 @%d，主色 @%d 期望 @%d）" % (
                    side, pos_e, want_e, pos_b, want_b)
                break
        rep.check("⑤ %s 档：描边占**最内**一圈（主色 %d px + 描边 1 px = %d px）"
                  % (tier, t - 1, t), inner_ok, why or "（几何位置不对）")


def check_explicit_override(mod, rep):
    """⑥显式 --thickness 仍然压得住一切（老用法不许被配色改死）。"""
    fn = getattr(mod, "resolve_style", None)
    if fn is None:
        rep.check("⑥ resolve_style() 存在", False, "没有它，--thickness 覆盖无从验")
        return
    tiers = getattr(mod, "TIERS", {}) or {}
    for tier in ("A", "B", "C", "D"):
        rgb = tuple(tiers[tier][0])
        try:
            t, edge = fn(rgb, FROZEN_EXPLICIT_THICKNESS)
        except Exception as e:
            rep.check("⑥ %s 档显式厚度覆盖" % tier, False,
                      "抛异常 %s: %s" % (type(e).__name__, e))
            continue
        rep.check("⑥ %s 档显式 --thickness %d 生效（覆盖按档的 %d）"
                  % (tier, FROZEN_EXPLICIT_THICKNESS, FROZEN_THICKNESS[tier]),
                  t == FROZEN_EXPLICIT_THICKNESS,
                  "实际 %r（显式给了值就该用它，不能还按档走）" % (t,))
        # 覆盖厚度不该把描边顶掉 —— 描边仍随主色亮暗走
        rep.check("⑥ %s 档显式厚度下描边仍在（%s）" % (tier, hexs(FROZEN_EDGE[tier])),
                  tuple(edge or ()) == FROZEN_EDGE[tier],
                  "实际 %s" % (hexs(tuple(edge)) if edge else "（无）"))
    # 命令行默认值必须**不给**（None = 按档走），而不是把旧的 8 留着当默认
    try:
        line = ""
        with open(mod.__file__, encoding="utf-8") as src:
            for ln in src:
                if "--thickness" in ln and "add_argument" in ln:
                    line = ln.strip()
                    break
        rep.check("⑥ --thickness 默认值不是写死的数字（None=按档取）",
                  bool(line) and "default=None" in line.replace(" ", ""),
                  "源码里 %s —— 默认写死就等于回到「全局单值」"
                  % (line[:90] if line else "没找到 --thickness"))
    except Exception as e:
        rep.check("⑥ 查 --thickness 默认值", False, "%s: %s" % (type(e).__name__, e))


def check_monotone(mod, rep):
    """⑦亮度逐档单调下降（A>B>C>D）—— 防「深浅乱跳」复发（派单 §理由①）。"""
    tiers = getattr(mod, "TIERS", {}) or {}
    seq = []
    for tier in ("A", "B", "C", "D"):
        v = tiers.get(tier)
        if not isinstance(v, (tuple, list)) or not v:
            rep.check("⑦ %s 档有颜色" % tier, False, "TIERS 里缺 %s" % tier)
            return
        seq.append((tier, lstar(tuple(v[0]))))
    for i in range(1, len(seq)):
        if not seq[i][1] < seq[i - 1][1]:
            rep.check("⑦ 亮度逐档单调下降", False,
                      "%s(L*=%.1f) 不比 %s(L*=%.1f) 深 ⇒ 又是深浅乱跳"
                      % (seq[i][0], seq[i][1], seq[i - 1][0], seq[i - 1][1]))
            return
    rep.check("⑦ 亮度逐档单调下降（A>B>C>D）", True)
    for tier, ls in seq:
        rep.check("⑦ %s 档 L* = %.1f（冻结 %.1f）" % (tier, ls, FROZEN_LSTAR[tier]),
                  abs(ls - FROZEN_LSTAR[tier]) <= 0.1,
                  "实际 %.1f，冻结 %.1f" % (ls, FROZEN_LSTAR[tier]))
    rep.check("⑦ 红 L* = %.1f（冻结 %.1f）"
              % (lstar(FROZEN_BRAKE_RGB), FROZEN_BRAKE_LSTAR),
              abs(lstar(FROZEN_BRAKE_RGB) - FROZEN_BRAKE_LSTAR) <= 0.1, "")


def check_contract(contract_path, rep):
    """⑧⑨⑩契约：颜色名同步、hex 与代码同一份、schema_version 不许动。"""
    if not os.path.isfile(contract_path):
        rep.check("⑧ 契约文件在", False, "%s 不存在" % contract_path)
        return
    with open(contract_path, encoding="utf-8") as f:
        js = json.load(f)
    eb = (js.get("conventions") or {}).get("edge_border_hint")
    if not isinstance(eb, dict):
        rep.check("⑧ conventions.edge_border_hint 在", False, "契约里没有这一块")
    else:
        for tier in ("A", "B", "C", "D"):
            got = eb.get(tier)
            rep.check("⑧ 契约 %s 档颜色名 = 「%s」" % (tier, FROZEN_CONTRACT_NAMES[tier]),
                      got == FROZEN_CONTRACT_NAMES[tier], "实际 %r" % (got,))
            rep.check("⑨ 契约 %s 档带同一个 hex %s" % (tier, hexs(FROZEN_RGB[tier])),
                      isinstance(got, str) and hexs(FROZEN_RGB[tier]) in got,
                      "没写 %s ⇒ agent 看到的颜色名与代码里的 RGB 可能分家"
                      % hexs(FROZEN_RGB[tier]))
        for k in ("红", "无边框", "_前提", "_不是传感信号"):
            rep.check("⑧ 契约 %s 条目还在" % k, k in eb, "edge_border_hint 缺 %s" % k)
        blob = "\n".join(str(eb.get(t, "")) for t in ("A", "B", "C", "D"))
        for old in RETIRED_CONTRACT_COLORS:
            rep.check("⑧ 旧色名「%s」已从契约清干净" % old, old not in blob,
                      "还留着「%s」⇒ 契约说的和代码画的不是一回事" % old)
        rep.check("⑧ 契约 _说明 提到 1px 反色描边",
                  "描边" in str(eb.get("_说明", "")),
                  "_说明 没提 1px 反色描边 ⇒ 只改了颜色名，机制没告诉 agent")
    # ⑩ schema_version：改配色**不许**动它（§为什么 在本文件开头）
    rep.check("⑩ 契约 schema_version 仍是 %d（改配色不动它）" % FROZEN_SCHEMA_VERSION,
              js.get("schema_version") == FROZEN_SCHEMA_VERSION,
              "实际 %r —— 配色变更升 schema_version 等于对外谎报接口不兼容"
              % (js.get("schema_version"),))


def main(argv=None):
    ap = argparse.ArgumentParser(description="边框配色/厚度/反色描边 冻结钉子（窗F）")
    ap.add_argument("--target",
                    default=os.path.join(ROOT, "src", "agentctl", "ctrl_border.py"),
                    help="被测的 ctrl_border.py（指向改色之前的旧版即可跑红）")
    ap.add_argument("--contract", default=os.path.join(ROOT, "contract", "tools.json"),
                    help="契约路径（颜色名同步面）")
    ap.add_argument("--skip-contract", action="store_true",
                    help="只验代码里的配色，不验契约")
    ap.add_argument("--inject", action="store_true",
                    help="自证跑红（窗M）：把现役 ctrl_border.py 复制一份到 %%TEMP%% 并把 C 档"
                         "配色漂移一格，同一套断言跑那份副本 ⇒ 必红。产品代码不动。")
    a = ap.parse_args(argv)

    if a.inject:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _inject                                   # noqa: E402  本地小助手
        a.target = _inject.shadow_ctrl_border(
            _inject.tmpdir("_wF_注入红_"), _inject.BORDER_COLOR_DRIFT)

    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    mod, target = load_border(a.target)
    rep = Report()

    print("=" * 96)
    print("窗F · 边框配色冻结钉子（五个 RGB + 每档厚度 + 反色描边，全部写死在判据里）")
    print("被测 ctrl_border : %s" % target)
    print("契约            : %s%s" % (a.contract, "（跳过）" if a.skip_contract else ""))
    print("=" * 96)
    print("冻结快照：%s ｜ 厚度 %s ｜ 红 %s/%dpx"
          % (" ".join("%s=%s" % (k, hexs(v)) for k, v in FROZEN_RGB.items()),
             "/".join("%s%d" % (k, v) for k, v in FROZEN_THICKNESS.items()),
             hexs(FROZEN_BRAKE_RGB), FROZEN_BRAKE_THICKNESS))
    print("-" * 96)

    check_rgb(mod, rep, "TIERS", FROZEN_RGB, "档位色")
    check_thickness(mod, rep, FROZEN_THICKNESS, FROZEN_BRAKE_THICKNESS)
    check_brake_not_in_ramp(mod, rep)
    check_edge(mod, rep)
    check_geometry(mod, rep)
    check_explicit_override(mod, rep)
    check_monotone(mod, rep)
    if not a.skip_contract:
        check_contract(a.contract, rep)

    print("-" * 96)
    if rep.reds:
        print("结论：RED —— 红 %d 条" % len(rep.reds))
        for name, why in rep.reds:
            print("   · %s" % name)
            if why:
                print("     %s" % why)
        return 1
    print("结论：GREEN —— 五个 RGB、五档厚度、1px 反色描边（不增总厚）、"
          "显式 --thickness 覆盖、L* 单调、契约颜色名与 schema_version 全部对得上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
