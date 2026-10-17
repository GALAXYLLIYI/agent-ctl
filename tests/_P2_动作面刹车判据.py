# -*- coding: utf-8 -*-
r"""P2 判据：**动作面**异常刹车（把 guardian 的「连续两次才作数」搬到动作这一侧）

================================ 这条单要什么 ================================
改前动作面是**空的**：连点同一个坐标、连续失败、短时高频，只能靠人眼看。
要补的是三条可配阈值 + **两级**响应（第一次只提示，第二次才拉闸）。

================================ 判据冻结了什么 ================================
期望值全部写死在本文件里 —— **不每轮从实现现算**（否则 f(x)==f(x) 恒真，等于没测）：
    1 · 同目标连击：same_target_streak=5 ⇒ 第 5 次命中**只写事件不拉闸**，
        第 6 次（第二次连续命中）才拉闸，brake_engaged() 为真
    2 · 连续失败：fail_streak=6 ⇒ 同样两级（第 6 次只提示，第 7 次拉闸）
    3 · ★防误报：同一目标**只点 2 次**、连着换目标 ⇒ **一个字节都不许触发**
        （这条最能证明「不是乱停」；也是最容易被写坏的一条）
    4 · 留痕：每次触发都能在事件流里查到 by="action-anomaly" + 阈值名 + 计数
    5 · 阈值**可配**：改配置文件把 same_target_streak 调成 2，行为跟着变
        （这条钉住「不许硬编码」）
    6 · 接线：core 的**动作出口**真的调了 observe（不然模块写得再对也是死代码）
    7 · 阈值来源：模块读的是那份 json 配置（不许悄悄吃代码里的兜底值）
    8 · 面板提示：一级命中必须能在面板上看见（带证据），且闸门没被碰

================================ 能跑红 =======================================
    ① 造坏实现（work/_P2_造坏实现.py）：
        python -X utf8 work/_P2_造坏实现.py
        $env:P2_GUARD="<坏实现目录>/action_guard.py"
        python -X utf8 tests/_P2_动作面刹车判据.py
        · 坏A「第一次命中就拉闸」  ⇒ 实测红 1、2、5、8（绿 3、4、6、7），rc=1
        · 坏B「永不触发」          ⇒ 实测红 1、2、4、5、8（绿 3、6、7），rc=1
        · 坏C「一动就报」（阈值失效） ⇒ 实测红 1、2、3、5、8，rc=1
      ⚠ 注意坏A 跑出来第 3 条（防误报）**是绿的**：它只改了两级的分界，
        没动阈值 ⇒ 「只点 2 次」本来就不该触发。这不是判据漏了，是如实反映；
        要把「乱停」也造出来，得用坏C（阈值形同虚设）—— 它第 3 条就红。
    ② 改前基线（没有 action_guard.py）⇒ 第 0 条就红，判据 fail-closed

★ 本判据**不碰真机状态**：急停旗子 / 通行证 / 租约 / 时间线全部指向临时目录
  （env 覆盖），跑完即删。不会拉用户真急停 —— gate.py 的老规矩，保留。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CONTRACT = os.path.join(REPO, "contract", "tools.json")
CORE = os.path.join(REPO, "src", "agentctl", "core.py")
SRC = os.path.join(REPO, "src")
# ★ 判据模块自己不去改 sys.path，但它的默认依赖（core._ev / gate.set_brake）
#   走的是「from agentctl import …」—— 那是**线上那条路**，所以这里必须把 src 放上路径，
#   让判据验的就是真链路（事件真落盘、急停旗子真写），不是把依赖注入掉的空壳。
if SRC not in sys.path:
    sys.path.insert(0, SRC)
# ★ 红验证口：把它指到「坏实现」那份 action_guard.py 上，整条判据就按那个实现判绿红
GUARD_PATH = (os.environ.get("P2_GUARD")
              or os.path.join(REPO, "src", "agentctl", "action_guard.py"))

# ── 隔离环境：必须在 import 闸门/模块**之前**设好 ──
TMP = tempfile.mkdtemp(prefix="P2action_")
STATE = os.path.join(TMP, "state")      # 当**状态**目录用（里面不放 gate.py ⇒ 闸门豁免体检）
APP = os.path.join(TMP, "app")
os.makedirs(STATE, exist_ok=True)
os.makedirs(APP, exist_ok=True)
os.environ["AGENTCTL_LEGACY_HOME"] = STATE
os.environ["AGENTCTL_APP_DIR"] = APP
os.environ["AGENTCTL_BRAKE_FLAG"] = os.path.join(APP, "_急停.flag")
os.environ["AGENTCTL_TIMELINE"] = os.path.join(TMP, "timeline")
os.environ["AGENTCTL_REQUIRE_LEASE"] = "0"     # 本单只测刹车，不测租约（别让租约把动作全拦掉）
os.environ["AGENTCTL_ACTION_GUARD_POLICY"] = os.path.join(TMP, "policy.json")

DEFAULT_POLICY = {
    "enabled": True,
    "same_target_streak": 5,
    "fail_streak": 6,
    "rate_per_min": 60,
    "rate_window_sec": 60,
    "streak_window_sec": 300,
    "hits_to_brake": 2,
    "hint_ttl_sec": 600,
    "action_tools": ["ctl_click", "ctl_type", "ctl_fill", "ctl_focus", "ctl_browser"],
}

OK, BAD = [], []
ag = None                       # 被测判据模块（main() 里装）


def section(t):
    print("\n" + "=" * 74 + "\n" + t + "\n" + "=" * 74, flush=True)


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("绿" if cond else "红", name,
                           ("  ← %s" % detail) if detail else ""), flush=True)
    return bool(cond)


def attempt(name, fn):
    """跑一条判据；**抛异常一律记红**（不许因崩了而没结论）。"""
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, "抛异常 %s: %s" % (type(e).__name__, e)
    return check(name, ok, detail)


def load_guard(path):
    """按文件路径单独装一份判据模块（能指到别处 ⇒ 坏实现红验证）。"""
    spec = importlib.util.spec_from_file_location("p2_action_guard_under_test", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def write_policy(**over):
    """写阈值配置。判据第 5 条靠它证明「阈值可配」—— 改的是**文件**，不是代码。"""
    pol = dict(DEFAULT_POLICY)
    pol.update(over)
    p = os.environ["AGENTCTL_ACTION_GUARD_POLICY"]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(pol, f, ensure_ascii=False)
    return p


def clear_brake():
    """每条判据前清掉急停旗子与面板提示 —— 免得上一条的残留把下一条顶成绿。"""
    for p in (os.environ["AGENTCTL_BRAKE_FLAG"],
              os.path.join(APP, "_动作异常提示.json")):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def events(kind=None):
    """读时间线（真相源是 jsonl，跟 P1 判据同口径）。返回 [(kind, payload), ...]。"""
    tdir = os.environ["AGENTCTL_TIMELINE"]
    rows = []
    if not os.path.isdir(tdir):
        return rows
    for fn in sorted(os.listdir(tdir)):
        if not fn.startswith("timeline-"):
            continue
        with open(os.path.join(tdir, fn), encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                body = e.get("body") if isinstance(e.get("body"), dict) else e
                k = body.get("kind")
                if kind and k != kind:
                    continue
                p = body.get("payload")
                if isinstance(p, str):
                    try:
                        p = json.loads(p)
                    except Exception:
                        p = {}
                rows.append((k, p if isinstance(p, dict) else {}))
    return rows


def click(actor, xy=(100, 200), ok=True, tool="ctl_click", hwnd=1234):
    """一次「点击」。hwnd 固定、只有 xy 在变 —— 与真实调用同形状。

    ★ 走 ag.guard()（进程内单例）与 core.dispatch 走的是**同一个入口** ——
      判据测的就得是线上那条路，否则「单例被谁换掉了」这类问题测不出来。
    """
    return ag.guard().observe(actor=actor, tool=tool,
                              args={"hwnd": hwnd, "xy": list(xy)}, ok=ok)


def _brief(h):
    keys = ("stage", "threshold_name", "count", "target", "brake_ok")
    return json.dumps({k: h.get(k) for k in keys}, ensure_ascii=False)


def main():
    global ag
    section("P2 · 动作面异常刹车（连续 N 次阈值判据）")
    print("  判据模块 = %s" % GUARD_PATH, flush=True)
    print("  隔离状态 = %s" % STATE, flush=True)

    if not os.path.isfile(GUARD_PATH):
        check("0 · 被测判据模块存在（%s）" % GUARD_PATH, False,
              "文件不在，验不了（改前基线就是这一条红）")
        return
    ag = load_guard(GUARD_PATH)
    write_policy()
    ag.reset_policy_cache()
    print("  急停旗子 = %s" % os.environ["AGENTCTL_BRAKE_FLAG"], flush=True)
    print("  阈值来源 = %s" % ag.policy_path(), flush=True)

    # ══ 1 · 同目标连击：第 1 次命中只写事件，第 2 次才拉闸 ═══════════════
    def c1():
        clear_brake()
        ag.reset_state()
        FLAG = os.environ["AGENTCTL_BRAKE_FLAG"]
        raw, flag_after = [], {}
        for i in range(1, 7):
            raw.append(click("窗A", xy=(100, 200)))
            # ★ 旗子必须**当场**看：等 6 次点完再看，分不清是第 5 次拉的还是第 6 次拉的
            flag_after[i] = os.path.exists(FLAG)
        hits = [h for h in raw if h]          # 没触发的调用返回 None，不是「红」
        first = hits[0]
        if not first:
            return False, "连点 6 下同坐标，**一次都没触发**（每次返回值=%s）" % (
                [bool(h) for h in raw],)
        if flag_after[5]:
            return False, "★第 1 次命中就拉了急停（这就是『动辄就停』）"
        if first.get("stage") != "warn":
            return False, "第 1 次命中 stage 应为 warn（只写事件+提示），实测 %r" % (
                first.get("stage"))
        if first.get("threshold_name") != "same_target_streak":
            return False, "阈值名不对：%r" % first.get("threshold_name")
        if int(first.get("count") or 0) != 5:
            return False, "第 1 次命中的计数应是 5（阈值本身），实测 %r" % first.get("count")
        last = hits[-1]
        if not last or last.get("stage") != "brake":
            return False, "第 6 次（第二次连续命中）应拉闸，实测 stage=%r" % (
                (last or {}).get("stage"))
        if not flag_after[6]:
            return False, "★第 2 次命中后急停旗子仍不存在（没拉闸）"
        print("      第 1 次命中 payload = %s" % _brief(first), flush=True)
        print("      第 2 次命中 payload = %s" % _brief(last), flush=True)
        print("      逐次拉闸标记 = %s" % flag_after, flush=True)
        return True, "第 5 次只写事件、第 6 次拉闸"

    attempt("1 · 同目标连击：第 1 次命中只写事件**不拉闸**，第 2 次才拉闸", c1)

    # ══ 2 · 连续失败：同样两级 ══════════════════════════════════════════
    def c2():
        clear_brake()
        ag.reset_state()
        FLAG = os.environ["AGENTCTL_BRAKE_FLAG"]
        # 每次换目标 ⇒ 只有 fail_streak 能命中（同目标判据不许顺手命中）
        raw, flag_after = [], {}
        for i in range(1, 8):
            raw.append(click("窗B", xy=(200 + i, 300), ok=False))
            flag_after[i] = os.path.exists(FLAG)
        hits = [h for h in raw if h]
        first = hits[0]
        if not first:
            return False, "连续失败 7 次，**一次都没触发**（每次返回值=%s）" % (
                [bool(h) for h in raw],)
        if flag_after[6]:
            return False, "★第 1 次失败命中就拉了急停"
        if first.get("threshold_name") != "fail_streak":
            return False, "阈值名不对：%r" % first.get("threshold_name")
        if int(first.get("count") or 0) < 6:
            return False, "事件里的计数应 ≥ 6，实测 %r" % first.get("count")
        if hits[-1].get("stage") != "brake":
            return False, "第 7 次（第二次连续命中）应拉闸，实测 %r" % (
                hits[-1].get("stage"))
        if not flag_after[7]:
            return False, "★失败判据第二次命中后急停旗子不存在"
        print("      逐次拉闸标记 = %s" % flag_after, flush=True)
        return True, "第 6 次只提示、第 7 次拉闸（fail_streak；目标每次都变也照样两级）"

    attempt("2 · 连续失败 6 次：同样是两级（第 1 次不拉闸，第 2 次才拉）", c2)

    # ══ 3 · ★防误报：正常操作不许触发 ══════════════════════════════════
    def c3():
        clear_brake()
        ag.reset_state()
        ev_before = len(events())
        anom_before = len([p for k, p in events("action_anomaly")])
        # ① 同一目标**只点 2 次**（正常人点两下确认/重试）
        got = [click("正常人", xy=(300, 400)), click("正常人", xy=(300, 400))]
        # ② 连续换目标点 20 次（翻页/逐条处理）
        got += [click("正常人", xy=(500 + i, 600)) for i in range(20)]
        # ③ 失败/成功交替（失败-成功-失败-成功…）—— 不许被算成「连续失败」
        got += [click("正常人2", xy=(700 + i, 800), ok=bool(i % 2)) for i in range(14)]
        # ④ 短时高频但没到 60/min（40 次）
        got += [click("正常人3", xy=(900 + i, 900)) for i in range(40)]
        # ⑤ 感知类工具不算动作（面板自己 1 fps 跑在感知上，算进去等于自己举报自己）
        got.append(ag.guard().observe(actor="面板", tool="ctl_shot",
                                      args={"hwnd": 1}, ok=True))
        hit = [h for h in got if h]
        if hit:
            return False, "★正常操作被误报了 %d 次（第一处 %s）" % (len(hit), _brief(hit[0]))
        if os.path.exists(os.environ["AGENTCTL_BRAKE_FLAG"]):
            return False, "★正常操作把急停拉下来了"
        anom_after = len([p for k, p in events("action_anomaly")])
        if anom_after != anom_before:
            return False, ("正常操作不该新增 action_anomaly 事件"
                           "（前 %d 条 → 后 %d 条）" % (anom_before, anom_after))
        if len(events()) != ev_before:
            return False, "正常操作不该写任何事件（多了 %d 条）" % (len(events()) - ev_before)
        return True, "2 次同点 / 20 次换目标 / 交替成败 / 40 次短时高频 / 感知调用：全不触发"

    attempt("3 · ★防误报：同点 2 次、换目标、交替成败、短时 40 次 ⇒ 一律**不触发**", c3)

    # ══ 4 · 留痕：by + 阈值名 + 计数 ═══════════════════════════════════
    def c4():
        clear_brake()
        ag.reset_state()
        for _ in range(6):
            click("窗C", xy=(1234, 5678))
        rows = [p for k, p in events("action_anomaly")]
        if not rows:
            return False, "事件流里没有 kind=action_anomaly 的事件（实测 kinds=%s）" % (
                sorted({k for k, _ in events()}))
        bad = []
        for p in rows:
            if p.get("by") != "action-anomaly":
                bad.append("payload.by 应为 action-anomaly，实测 %r" % p.get("by"))
            if not p.get("threshold_name"):
                bad.append("payload 里缺阈值名 threshold_name")
            if not isinstance(p.get("count"), int):
                bad.append("payload.count 应为整数，实测 %r" % (p.get("count"),))
            if not p.get("target"):
                bad.append("payload 里缺目标 target（无证据的停机等于乱停）")
            if not p.get("window_sec"):
                bad.append("payload 里缺时间窗 window_sec")
        if not [p for p in rows if p.get("stage") != "warn"]:
            bad.append("拉闸那次的 stage 不是 warn（第 2 次命中）")
        for b in bad[:4]:
            print("      · %s" % b, flush=True)
        print("      事件样例 = %s" % json.dumps(
            {k: rows[-1].get(k) for k in ("by", "stage", "threshold_name",
                                          "threshold_value", "count", "target",
                                          "window_sec")}, ensure_ascii=False), flush=True)
        return (not bad), ("；".join(bad) if bad else
                           "by/threshold_name/count/target/window_sec 齐全")

    attempt("4 · 留痕：事件里查得到 by=action-anomaly + 阈值名 + 计数（+ 目标/时间窗）", c4)

    # ══ 5 · 阈值可配（不许硬编码）══════════════════════════════════════
    def c5():
        clear_brake()
        ag.reset_state()
        write_policy(same_target_streak=2, fail_streak=2)
        ag.reset_policy_cache()
        src_info = ag.policy_source()
        if int((src_info.get("thresholds") or {}).get("same_target_streak") or 0) != 2:
            return False, "改完配置读到的阈值还是 %r" % src_info.get("thresholds")
        r1 = click("窗D", xy=(11, 22))
        if r1:
            return False, "阈值调到 2 之后，第 1 次点击就触发了（没在读配置）"
        r2 = click("窗D", xy=(11, 22))
        if not r2:
            return False, "★阈值调到 2 之后连点 2 次仍不触发 —— 阈值被写死了"
        if os.path.exists(os.environ["AGENTCTL_BRAKE_FLAG"]):
            return False, "阈值=2 时第 1 次命中就拉闸（两级被写死成一级）"
        r3 = click("窗D", xy=(11, 22))
        if not r3 or r3.get("stage") != "brake":
            return False, "阈值=2 时第 2 次命中应拉闸，实测 %r" % ((r3 or {}).get("stage"))
        write_policy()             # 调回默认口径，别污染后面的判据
        ag.reset_policy_cache()
        return True, "改成 2 ⇒ 连点 2 次即命中；仍是两级（第 3 次才拉闸）"

    attempt("5 · 阈值**可配**：改配置文件 ⇒ 行为跟着变（钉住「不许硬编码」）", c5)

    # ══ 6 · 接线：core 的动作出口真的调了 ═══════════════════════════════
    def c6():
        with open(CORE, encoding="utf-8", errors="replace") as f:
            src = f.read()
        bad = []
        if "action_guard" not in src:
            bad.append("core.py 里没有 action_guard 的引用（模块写得再好也是死代码）")
        if "def dispatch(" not in src:
            bad.append("core.py 里找不到 dispatch（判据失效，请核代码结构）")
        else:
            body = src.split("def dispatch(", 1)[1].split("\ndef ", 1)[0]
            if "observe(" not in body:
                bad.append("dispatch 里没有调 observe()（动作出口没接上）")
            if "action_guard.guard()" not in body:
                bad.append("dispatch 里没有走 action_guard.guard() 单例（会各判各的）")
        # 契约面：动作异常**不是**一个新工具 —— 它是判据，不是给人手动触发的口
        if os.path.isfile(CONTRACT):
            with open(CONTRACT, encoding="utf-8") as f:
                c = json.load(f)
            names = {t.get("name") for t in c.get("tools", [])}
            if "ctl_action_guard" in names or "ctl_brake_anomaly" in names:
                bad.append("契约里多出了动作异常的工具条目（不该给模型一个手动停机的口）")
        return (not bad), ("；".join(bad) if bad else
                           "dispatch → action_guard.guard().observe()，契约面无新工具")

    attempt("6 · 接线：core.dispatch（动作出口）真的调了 observe，且契约没多出新工具", c6)

    # ══ 7 · 阈值来源：读的是那份 json，不是代码兜底值 ═══════════════════
    def c7():
        p = os.path.join(os.path.dirname(GUARD_PATH), "action_guard_policy.json")
        if not os.path.isfile(p):
            return False, "判据模块旁边没有 action_guard_policy.json（阈值没地方配）"
        with open(p, encoding="utf-8") as f:
            pol = json.load(f)
        bad = []
        for k, want in (("same_target_streak", 5), ("fail_streak", 6), ("rate_per_min", 60)):
            if k not in pol:
                bad.append("配置里缺 %s" % k)
            elif int(pol[k]) != want:
                bad.append("%s 的建议默认值应是 %s，实测 %r" % (k, want, pol[k]))
        with open(GUARD_PATH, encoding="utf-8", errors="replace") as f:
            src = f.read()
        if "action_guard_policy.json" not in src:
            bad.append("代码里找不到 action_guard_policy.json 的引用（没在读配置）")
        if not hasattr(ag, "policy_source"):
            bad.append("没有 policy_source() 之类「阈值出自哪」的自证接口")
        for b in bad[:4]:
            print("      · %s" % b, flush=True)
        return (not bad), ("；".join(bad) if bad else
                           "配置面存在（5/6/60）、代码确实在读它")

    attempt("7 · 阈值**有配置面**（action_guard_policy.json，5/6/60），代码确实在读它", c7)

    # ══ 8 · 面板提示：一级命中必须能在面板上看见 ═══════════════════════
    def c8():
        clear_brake()
        ag.reset_state()
        for _ in range(5):                 # 打到命中为止（本单默认阈值 same_target_streak=5）
            click("窗E", xy=(31, 41))
        hint = ag.hint_view()
        if not hint:
            return False, "一级命中后面板上没有提示（只写事件不够，用户看不见）"
        bad = []
        for k in ("threshold_name", "count", "target"):
            if not hint.get(k):
                bad.append("提示里缺 %s" % k)
        if hint.get("stage") != "warn":
            bad.append("提示的 stage 应为 warn（一级命中不许写成已拉闸），实测 %r"
                       % hint.get("stage"))
        if os.path.exists(os.environ["AGENTCTL_BRAKE_FLAG"]):
            bad.append("★出提示的同时把急停拉了（第一级不许动闸门）")
        wpath = os.path.join(SRC, "agentctl", "widget.py")
        if os.path.isfile(wpath):
            with open(wpath, encoding="utf-8", errors="replace") as f:
                w = f.read()
            if "hint_view" not in w:
                bad.append("widget.py 没有读 hint_view（提示出不来）")
        for b in bad[:4]:
            print("      · %s" % b, flush=True)
        return (not bad), ("；".join(bad) if bad else
                           "提示带证据（阈值名/计数/目标），widget 读得到")

    attempt("8 · 一级命中：面板上有带证据的提示（且闸门没被碰）", c8)


if __name__ == "__main__":
    _completed = False
    try:
        main()
        _completed = True
    except BaseException as e:                                  # noqa: BLE001
        # ★ fail-closed：判据自己崩了**必须记红**（P1 判据里那个坑的同款：
        #   finally 里的 sys.exit(0) 会把异常顶掉，崩掉的判据反而报「全绿」）。
        print("  ★ 判据自身崩了（fail-closed 记红）：%s: %s" % (type(e).__name__, e),
              flush=True)
    finally:
        section("汇总")
        print("  绿 %d 条 / 红 %d 条" % (len(OK), len(BAD)), flush=True)
        try:
            shutil.rmtree(TMP, ignore_errors=True)
        except Exception:
            pass
        if BAD or not _completed:
            if _completed:
                print("  结论：【红】%d 条不成立" % len(BAD), flush=True)
            else:
                print("  结论：【红】判据没跑完（自身崩了，fail-closed）—— 一条都不算绿",
                      flush=True)
            for b in BAD:
                print("     - %s" % b)
            print("")
            print("  ★ 红验证（造坏实现）：python -X utf8 work/_P2_造坏实现.py")
            print("    然后 $env:P2_GUARD=\"<坏实现目录>/action_guard.py\" 再跑本判据")
            sys.exit(1)
        print("  结论：【绿】两级响应/防误报/可配/接线/留痕/面板提示 全部成立", flush=True)
        sys.exit(0)
