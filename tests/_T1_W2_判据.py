# -*- coding: utf-8 -*-
r"""T1 窗2 判据（Codex）—— D1 治本 / W2-B / W2-C / W2-D + bench b11 回归守

用法（两条是「同一条判据、换一份 core.py 跑」）：
    python -X utf8 tests\_T1_W2_判据.py --new    # 跑现役 src\agentctl
    python -X utf8 tests\_T1_W2_判据.py --old    # 跑 git show HEAD:... 导出的旧版

★ 仓根（窗M 2026-10-16 改）：**从 __file__ 推**（tests\ 的上一级 = 仓根），
  所以在干净 clone / 别的机器上跑，验的都是**它自己所在的那份仓**；
  要显式指到别处（例如某份归档红基线仓）用环境变量覆盖口：
      AGENTCTL_ROOT=<另一份仓> python -X utf8 tests\_T1_W2_判据.py --new

★ 为什么这套判据**不依赖动态页面、不依赖两次快照**：
   uia_tree 的 walk/tree/find 全部换成脚本内的假实现（FakeTree），
   而假实现是**按 roles 口径分表**的 —— 这正是 D1 的机制本身：
     roles=("interactive","text") → [甲, 乙, 丙]        （ctl_tree 签发侧）
     roles=("all",)                → [壳, 甲, 乙, 丙]    （ctl_find / 改前 t_click 侧）
   于是「同一个 e1 在两张表里指不同节点」是**给定事实**，不是靠网页抖动撞出来的。
   每条判据只发**一次**观测（ctl_tree / ctl_find）+ 一次点击，不做「拍两次快照比对」。

★ 全程只读 + 沙箱状态目录：AGENTCTL_LEGACY_HOME / AGENTCTL_EID_SESSIONS /
   AGENTCTL_TIMELINE 全部指向 work\_T1_W2_沙箱\*，
   **绝不碰真 auth_pass.json、绝不碰真急停 flag**。
★ ⛔ 全程不碰 git 写操作；旧版由 git show HEAD:... 导出（§一.1 的做法）。
"""
import io
import json
import os
import shutil
import sys
import time

# ★ 窗M（2026-10-16）修「闸门会撒谎」：仓根不再写死。
#   写死时本判据在任何机器上都 import **那台开发机**的 src（含未提交改动）
#   ⇒ 干净 clone 跑出来的绿，与入库物无关。现在从 __file__ 推（tests\ 的上一级 = 仓根），
#   AGENTCTL_ROOT 仍可显式指到别处（指红基线仓跑红）。断言一条没删、也没放宽。
REPO = os.path.abspath(os.environ.get("AGENTCTL_ROOT") or
                       os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SANDBOX = os.path.join(REPO, "work", "_T1_W2_沙箱")
MODE = sys.argv[1] if len(sys.argv) > 1 else "--new"

# ── 沙箱状态目录：闸门状态 / 旁表 / 时间线三样全隔离 ─────────────────
STATE = os.path.join(SANDBOX, "state_" + MODE.strip("-"))
if os.path.isdir(STATE):
    shutil.rmtree(STATE)
os.makedirs(os.path.join(STATE, "_auth"))
_now = time.time()
_pass = {"scope": "B", "scopes": ["A", "B", "C", "D"], "issued": "t1w2-criterion",
         "expires_at": _now + 3600, "expires": "2099-01-01 00:00:00",
         "acts_limit": 999, "nonce": "t1w2", "consumed": False, "acts": 0}
with io.open(os.path.join(STATE, "_auth", "auth_pass.json"), "w", encoding="utf-8") as _f:
    json.dump(_pass, _f)

os.environ["AGENTCTL_LEGACY_HOME"] = STATE
os.environ["AGENTCTL_EID_SESSIONS"] = os.path.join(STATE, "_auth", "eid_sessions.json")
os.environ["AGENTCTL_TIMELINE"] = os.path.join(STATE, "timeline")
os.environ["AGENTCTL_BRAKE_FLAG"] = os.path.join(STATE, "_急停.flag")
for _k in ("AGENTCTL_EID_STRICT", "AGENTCTL_CLICK_NAME_LEGACY", "AGENTCTL_REQUIRE_LEASE"):
    os.environ.pop(_k, None)

PKG = os.path.join(REPO, "src")
if MODE != "--new":
    PKG = os.path.join(REPO, "work", "_tmp_旧版_W2")
sys.path.insert(0, PKG)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agentctl import core                                    # noqa: E402
from agentctl import uia_tree                                # noqa: E402

ACTOR = "t1w2"
HWND = 424242
RECT = [100, 200, 900, 700]


# ═════════ 假元素树：按 roles 口径分表（D1 的机制写死在数据里）═════════
class FakeTree:
    def __init__(self):
        self.order_int = ["A", "B", "C"]
        self.order_all = ["X", "A", "B", "C"]
        self.pos = {"X": (0, 0), "A": (10, 10), "B": (20, 20), "C": (30, 30)}
        self.name = {"X": "浏览器外壳", "A": "按钮甲", "B": "按钮乙", "C": "按钮丙"}
        self.type = {"X": "PaneControl", "A": "ButtonControl",
                     "B": "ButtonControl", "C": "ButtonControl"}
        self.enabled = {"X": True, "A": True, "B": True, "C": True}
        self.minimized = False
        self.walk_calls = []

    def keys(self, roles):
        return self.order_all if "all" in tuple(roles or ()) else self.order_int

    def nodes(self, roles):
        out = []
        for k in self.keys(roles):
            x, y = self.pos[k]
            rect = [x, y, x + 40, y + 20]
            out.append({"eid": "e%d" % len(out), "depth": len(out),
                        "type": self.type[k], "name": self.name[k], "rect": rect,
                        "center": [x + 20, y + 10],
                        "enabled": self.enabled[k], "aid": k})
        return out

    def move(self, key, dx, dy):
        x, y = self.pos[key]
        self.pos[key] = (x + dx, y + dy)

    def window_state(self, handle):
        return {"句柄": int(handle), "最小化": bool(self.minimized), "前台": False,
                "根矩形宽": 0 if self.minimized else 800,
                "根矩形高": 0 if self.minimized else 500,
                "说明": "窗口已最小化，UIA 只能拿到根节点" if self.minimized else "正常"}

    def walk(self, handle, *, max_nodes=600, max_depth=25,
             roles=("interactive", "text"), detail=False, drop_browser_chrome=True, **kw):
        self.walk_calls.append({"roles": list(roles), "max_nodes": max_nodes})
        return [dict(n) for n in self.nodes(roles)[:max_nodes]], False

    def tree(self, handle, *, max_nodes=600, max_depth=25, detail=False,
             roles=("interactive", "text"), **kw):
        ns, tr = self.walk(handle, max_nodes=max_nodes, max_depth=max_depth,
                           roles=roles, detail=detail)
        out = {"句柄": int(handle), "节点数": len(ns), "截断": tr,
               "启用性未知": 0, "耗时_ms": 0,
               "树": uia_tree.render(ns, tr),
               "节点": ns if detail else []}
        return out

    def find(self, handle, query, *, max_nodes=3000, max_depth=25,
             limit=40, detail=False, **kw):
        ns, _tr = self.walk(handle, max_nodes=max_nodes, max_depth=max_depth,
                            roles=("all",), detail=detail)
        q = (query or "").lower()
        hits = [n for n in ns
                if (not q) or (q in (n["name"] or "").lower())
                or (q in n["type"].lower()) or (q in (n.get("aid") or "").lower())]
        out = {"命中": hits[:limit], "命中数": len(hits), "启用性未知": 0,
               "扫过节点": len(ns), "截断": False, "耗时_ms": 0,
               "提示": "id 可以直接给 ctl_click 用，不用自己算坐标"}
        return out


FT = FakeTree()
uia_tree.window_state = FT.window_state
uia_tree.walk = FT.walk
uia_tree.tree = FT.tree
uia_tree.find = FT.find

# ── 拦掉真窗口 / 真 act.py：只记录 argv，不真点 ─────────────────────
ACT_LOG = []


def _fake_legacy(name, argv, timeout=60):
    ACT_LOG.append(list(argv))
    return (0, "", "")


core._legacy = _fake_legacy
core._win_rect = lambda hwnd: list(RECT)
core._is_foreground = lambda hwnd: False

_lease = core.t_lease({"action": "acquire", "actor": ACTOR, "scopes": ["B", "C"]})
assert _lease.get("ok"), ("租约没拿到，判据跑不下去：", _lease.get("warning"))


def _clicked_xy():
    if not ACT_LOG:
        return None
    a = ACT_LOG[-1]
    return a[a.index("--xy") + 1] if "--xy" in a else None


def _last_argv():
    return list(ACT_LOG[-1]) if ACT_LOG else []


def _reset():
    del ACT_LOG[:]
    FT.minimized = False
    FT.pos = {"X": (0, 0), "A": (10, 10), "B": (20, 20), "C": (30, 30)}
    FT.enabled = {"X": True, "A": True, "B": True, "C": True}


# ═══════════════════════════ 判据 ═══════════════════════════
RESULTS = []


def check(cid, title, ok, detail):
    RESULTS.append((cid, title, bool(ok), detail))
    print("[%s] %-4s %s" % ("PASS" if ok else "FAIL", cid, title))
    for ln in str(detail).splitlines():
        print("        " + ln)


def j1_绑那次快照():
    _reset()
    obs = core.t_tree({"hwnd": HWND, "detail": True})
    assert obs.get("ok"), obs.get("warning")
    tok = obs["data"]["capture_id"]
    seen = [ln.split()[0] for ln in obs["data"]["结果"]["树"].splitlines() if ln.strip()]
    r = core.t_click({"hwnd": HWND, "actor": ACTOR, "eid": "e1", "from_capture": tok})
    d1 = ("ctl_tree 文本里的 eid 顺序 = %s\n"
          "e1 在签发侧 roles=interactive,text ⇒ 按钮乙，中心 40,30\n"
          "e1 在重排侧 roles=all              ⇒ 按钮甲，中心 30,20 ← 改前点的是这个（点错）\n"
          "实际 --xy = %s ；ok=%s code=%s" % (seen, _clicked_xy(), r.get("ok"), r.get("code")))
    d1 += "\nwarning=%r" % ((r.get("warning") or "")[:120],)
    cond = r.get("ok") and _clicked_xy() == "40,30"
    return check("J1", "★ D1：eid 绑签发它的那次快照（ctl_tree e1 ⇒ 按钮乙）", cond, d1)


def j2_eid_与_xy_同时给():
    _reset()
    obs = core.t_tree({"hwnd": HWND, "detail": True})
    tok = obs["data"]["capture_id"]
    _a = {"hwnd": HWND, "actor": ACTOR, "eid": "e1",
          "xy": [777, 888], "from_capture": tok}
    r = core.t_click(_a)
    d1 = ("ok=%s code=%s ；act.py 被调了 %d 次（必须 0 次）"
          % (r.get("ok"), r.get("code"), len(ACT_LOG)))
    d1 += "\nwarning=%r" % ((r.get("warning") or "")[:200],)
    cond = (not r.get("ok")) and len(ACT_LOG) == 0
    return check("J2", "--eid 与 --xy 同时给 ⇒ 拒绝，一个都不点", cond, d1)


def j3_挪位要拒绝():
    _reset()
    obs = core.t_find({"hwnd": HWND, "query": "按钮"})
    tok = obs["data"]["capture_id"]
    hit1 = obs["data"]["结果"]["命中"][1]
    eid, before = hit1["eid"], hit1["center"]
    FT.move("B", 100, 60)
    r = core.t_click({"hwnd": HWND, "actor": ACTOR, "eid": eid, "from_capture": tok})
    d1 = ("点的是 eid=%s（=按钮乙，原中心 %s，签发后被挪到 +100,+60）\n"
          "ok=%s code=%s ；act.py 被调了 %d 次（必须 0 次）"
          % (eid, before, r.get("ok"), r.get("code"), len(ACT_LOG)))
    d1 += "\nwarning=%r" % ((r.get("warning") or "")[:200],)
    cond = (not r.get("ok")) and len(ACT_LOG) == 0
    return check("J3", "元素挪位 ⇒ 拒绝（不许照旧坐标戳下去）", cond, d1)


def j4_查无此号归12():
    _reset()
    r = core.t_click({"hwnd": HWND, "actor": ACTOR, "eid": "zzz_从未签发"})
    w = r.get("warning") or ""
    d1 = "code=%s（要 12）" % (r.get("code"),)
    d1 += "\nwarning=%r" % (w[:220],)
    cond = r.get("code") == 12 and "不存在" in w and "重新" in w
    return check("J4", "旁表查无此号 ⇒ code=12，处方含「不存在」+「重新」", cond, d1)


def j5_eid_不带_capture():
    _reset()
    core.t_find({"hwnd": HWND, "query": "按钮甲"})
    r = core.t_click({"hwnd": HWND, "actor": ACTOR, "eid": "e1"})
    w = r.get("warning") or ""
    d1 = ("ok=%s code=%s ；act.py 被调了 %d 次（必须 0 次）"
          % (r.get("ok"), r.get("code"), len(ACT_LOG)))
    d1 += "\nwarning=%r" % (w[:220],)
    cond = (not r.get("ok")) and len(ACT_LOG) == 0 and "from_capture" in w
    return check("J5", "eid 不带 from_capture（该号由别的观测签发）⇒ 拒并说清怎么补",
                 cond, d1)


def j6_name_唯一命中走新线():
    _reset()
    r = core.t_click({"hwnd": HWND, "actor": ACTOR, "name": "按钮甲"})
    a = _last_argv()
    d1 = "act.py argv = %s\n" % (a,)
    d1 += "期望 --xy 30,20（按钮甲 10,10 + 中心 20,10），且**不含** --name\n"
    d1 += "ok=%s code=%s warning=%r" % (r.get("ok"), r.get("code"),
                                        (r.get("warning") or "")[:160])
    cond = r.get("ok") and _clicked_xy() == "30,20" and "--name" not in a
    return check("J6", "W2-B：--name 唯一命中 ⇒ 解析成坐标走新线（argv 无 --name）",
                 cond, d1)


def j7_name_多命中拒绝并列():
    _reset()
    FT.name["B"] = "按钮甲"
    try:
        r = core.t_click({"hwnd": HWND, "actor": ACTOR, "name": "按钮甲"})
        w = r.get("warning") or ""
        d1 = ("ok=%s code=%s ；act.py 被调了 %d 次（必须 0 次）"
              % (r.get("ok"), r.get("code"), len(ACT_LOG)))
        d1 += "\nwarning=%r" % (w[:260],)
        cond = (not r.get("ok")) and len(ACT_LOG) == 0 and "2 个命中" in w
        return check("J7", "W2-B：--name 多命中 ⇒ 拒绝并列出来", cond, d1)
    finally:
        FT.name["B"] = "按钮乙"


def j8_name_零命中处方():
    _reset()
    r = core.t_click({"hwnd": HWND, "actor": ACTOR, "name": "根本不存在的按钮"})
    w = r.get("warning") or ""
    d1 = "code=%s（要 4）；act.py 被调了 %d 次（必须 0 次）" % (r.get("code"), len(ACT_LOG))
    d1 += "\nwarning=%r" % (w[:240],)
    cond = r.get("code") == 4 and len(ACT_LOG) == 0 and "找不到" in w
    return check("J8", "W2-B：--name 零命中 ⇒ code=4 且处方说清三件事", cond, d1)


def j9_坐标口径():
    _reset()
    r = core.t_click({"hwnd": HWND, "actor": ACTOR, "name": "按钮甲"})
    kou = (r.get("data") or {}).get("坐标口径")
    d1 = "data.坐标口径 = %r" % (kou,)
    cond = bool(kou) and "屏幕物理像素" in kou and "不要减窗口原点" in kou
    return check("J9", "W2-C：回执 data.坐标口径 写死「屏幕物理像素，与 ctl_find 同源」",
                 cond, d1)


def j10_写动作三步():
    _reset()
    st = core.t_status({})
    v = (st.get("data") or {}).get("写动作三步")
    ok = bool(v) and all(k in v for k in ("ctl_lease", "ctl_find", "ctl_click"))
    ok = ok and "不要减窗口原点" in v and "指针" in v
    d1 = "写动作三步 = %r" % (v,)
    return check("J10", "W2-D：ctl_status.写动作三步 = 最小三步 + 指针（不是第二份 SOP）",
                 ok, d1)


def j11_轻量树要对症处方():
    """ctl_tree 默认轻量模式**不交节点明细** ⇒ 树文本里的 eid 没有钥匙。

    这时候必须说清「是这次观测没明细」，而不是含糊地说「这个号不存在」——
    后者会把 agent 引去反复 ctl_find，治不好病。
    """
    _reset()
    obs = core.t_tree({"hwnd": HWND})          # 注意：**不带 detail**
    tok = obs["data"]["capture_id"]
    r = core.t_click({"hwnd": HWND, "actor": ACTOR, "eid": "e1", "from_capture": tok})
    w = r.get("warning") or ""
    d1 = "ok=%s code=%s ；act.py 被调了 %d 次（必须 0 次）" % (
        r.get("ok"), r.get("code"), len(ACT_LOG))
    d1 += "\nwarning=%r" % (w[:260],)
    cond = ((not r.get("ok")) and len(ACT_LOG) == 0
            and "没有元素明细" in w and "--detail" in w)
    return check("J11", "ctl_tree 不带 --detail ⇒ 说清「本次观测没明细」并给出路", cond, d1)


def g1_b11_A():
    _reset()
    FT.minimized = True
    r = core.t_click({"hwnd": HWND, "eid": "e1"})
    FT.minimized = False
    w = r.get("warning") or ""
    d1 = "ok=%s code=%s warning=%r" % (r.get("ok"), r.get("code"), w[:160])
    cond = (not r.get("ok")) and ("不可读" in w or "叫醒" in w)
    return check("G1", "回归守：最小化窗口 + eid ⇒ 先报「不可读/叫醒」（b11-A）", cond, d1)


def g2_b11_B():
    _reset()
    r = core.t_click({"hwnd": HWND, "eid": "zzz_bench_不存在"})
    w = r.get("warning") or ""
    d1 = "ok=%s code=%s warning=%r" % (r.get("ok"), r.get("code"), w[:160])
    cond = (not r.get("ok")) and ("不存在" in w or "重新" in w)
    return check("G2", "回归守：可见窗口 + 假 eid ⇒ 报「不存在/重新」（b11-B）", cond, d1)


def g3_回退开关():
    _reset()
    os.environ["AGENTCTL_EID_STRICT"] = "0"
    try:
        r = core.t_click({"hwnd": HWND, "actor": ACTOR, "eid": "e1"})
        d1 = "ok=%s ；--xy=%s（改前口径：roles=all 的 e1 = 按钮甲 30,20）" % (
            r.get("ok"), _clicked_xy())
        cond = r.get("ok") and _clicked_xy() == "30,20"
        return check("G3", "回退开关 AGENTCTL_EID_STRICT=0 ⇒ 回到改前行为（点 30,20）",
                     cond, d1)
    finally:
        os.environ.pop("AGENTCTL_EID_STRICT", None)


def g4_没有第二套坐标系():
    src = io.open(core.__file__, encoding="utf-8").read()
    bad = [k for k in ("rect_local", "center_local", "窗口原点坐标", "local_rect")
           if k in src]
    d1 = "命中的禁用词：%s" % (bad or "无")
    return check("G4", "⛔ core.py 里没有任何 *_local / 窗口原点换算字段", not bad, d1)


ALL = (j1_绑那次快照, j2_eid_与_xy_同时给, j3_挪位要拒绝, j4_查无此号归12,
       j5_eid_不带_capture, j6_name_唯一命中走新线, j7_name_多命中拒绝并列,
       j8_name_零命中处方, j9_坐标口径, j10_写动作三步,
       j11_轻量树要对症处方,
       g1_b11_A, g2_b11_B, g3_回退开关, g4_没有第二套坐标系)

for _fn in ALL:
    _fn()

_red = [c for c, _t, ok, _d in RESULTS if not ok and not c.startswith("G")]
_guard = [c for c, _t, ok, _d in RESULTS if not ok and c.startswith("G")]
_ng = len([1 for c, _t, _o, _d in RESULTS if c.startswith("G")])
print("")
print("=" * 78)
print("模式 %s  包 %s" % (MODE, PKG))
print("判据 %d 条：绿 %d，红 %d ；回归守 %d 条中红 %d"
      % (len(RESULTS), len(RESULTS) - len(_red) - len(_guard), len(_red), _ng, len(_guard)))
if _red:
    print("★ 红的判据（这些就是「改后绿」要转绿的）：" + "、".join(_red))
if _guard:
    print("★★ 红的回归守（真顶掉了覆盖面，必须先修）：" + "、".join(_guard))
print("结论：%s" % ("全绿" if not _red and not _guard else "有红"))

# ★ 2026-10-14 DSH 补（依据退回单 work\_T1_退回_W2_判据退出码.md）：
#   原版只有 print、没有 sys.exit ⇒ 红绿一律退 0，门禁会把 11 条红读成「通过」。
#   判据红必须让它自己喊出来：非零退出码。
sys.exit(1 if (_red or _guard) else 0)



