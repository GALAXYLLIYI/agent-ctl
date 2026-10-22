# -*- coding: utf-8 -*-
r"""_P3_M18a_eid复核_判据.py —— M18-a 刀1（eid 复核精确定位）的判据

靶场：M18a靶场 记事本（own spawn，按名字杀，铁律13）。
对照法：同一 feat 在「改前模式」（AGENTCTL_EID_RECHECK_PRECISE=0，强制全树 walk）
与「改后模式」（默认精确快查）各跑一次 _eid_recheck，三档（通过/不在原位/未知）
必须逐例一致；「通过」例 center/enabled 一致；expectuid 例 uid 一致。

用法：
  python tests\_P3_M18a_eid复核_判据.py              # 正常：exit 0/1
  python tests\_P3_M18a_eid复核_判据.py __inject     # 注入劣化定位（同名只按名字忽略矩形）：必须 exit 1
  python tests\_P3_M18a_eid复核_判据.py --mode timing  # 时序断言（需令牌；无令牌=不可执行不算失败）
"""
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))
RAW = HERE.parent / "work" / "_M18_动作面瘦身"
RAW.mkdir(parents=True, exist_ok=True)
MARK = "M18a靶场"

PROBE_SRC = '''# -*- coding: utf-8 -*-
"""M18-a 探针：对给定 feat 跑一次 core._eid_recheck（模式由环境变量/argv 切）。"""
import json, os, sys
sys.path.insert(0, r"<工作区>\\Documents\\agent-ctl\\src")
hwnd = int(sys.argv[1]); feat = json.loads(sys.argv[2])
need_keys = sys.argv[3] == "1"; inject = len(sys.argv) > 4 and sys.argv[4] == "inject"
from agentctl import core
if inject:
    # 注入缺陷：劣化定位——「同类型同名即命中，忽略矩形」。
    # 这是精确定位最要命的漏法：同名/矩形漂移全部漏检（rect 不匹配也放行）。
    from agentctl import uia_tree
    def _degraded(h, feat_, roles):
        nodes, _t = uia_tree.walk(h, roles=tuple(roles or ("all",)),
                                  max_depth=25, include_offscreen=True)
        for n in nodes:
            if (n.get("type") == feat_.get("type")
                    and (n.get("name") or "") == (feat_.get("name") or "")):
                return [(n, "（注入：同名即命中，忽略矩形）")]
        return []
    core._eid_locate_fast = _degraded
try:
    cur, why, rctx = core._eid_recheck(hwnd, feat, {"roles": ["all"], "max_depth": 25},
                                       need_keys=need_keys)
except Exception as e:
    print(json.dumps({"档": "未知", "错误": "%s: %s" % (type(e).__name__, e)},
                     ensure_ascii=False)); sys.exit(0)
if cur is None:
    print(json.dumps({"档": "不在原位", "why": (why or "")[:160]}, ensure_ascii=False))
    sys.exit(0)
uid = ""
if need_keys:
    uid = (rctx or {}).get("uid") or core._screen_uid_at(rctx, cur)
print(json.dumps({"档": "通过", "center": cur.get("center"), "rect": cur.get("rect"),
                  "enabled": cur.get("enabled"), "uid": uid},
                 ensure_ascii=False))
'''


def probe(mode_old, hwnd, feat, need_keys=False, inject=False):
    env = dict(os.environ)
    env["AGENTCTL_EID_RECHECK_PRECISE"] = "0" if mode_old else "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = HERE / "_P3_M18a_eid复核_探针.py"
    p.write_text(PROBE_SRC, encoding="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", str(p), str(hwnd),
                        json.dumps(feat, ensure_ascii=False),
                        "1" if need_keys else "0",
                        "inject" if inject else ""],
                       capture_output=True, env=env, timeout=180)
    try:
        return json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception:
        return {"档": "未知",
                "错误": (r.stdout + r.stderr).decode("utf-8", "replace")[-200:]}


# ────────────────────────── 靶场：M18a靶场 记事本 ──────────────────────────
def residue_count():
    import win32gui
    cnt = [0]
    def cb(h, _l):
        t = win32gui.GetWindowText(h)
        if win32gui.IsWindowVisible(h) and MARK in t:
            cnt[0] += 1
        return True
    win32gui.EnumWindows(lambda h, l: cb(h, l), 0)
    return cnt[0]


def spawn_target():
    import subprocess as sp
    import win32gui
    txt = RAW / "M18a靶场.txt"
    txt.write_text("M18-a 靶场内容行 0123456789\n" * 30, encoding="utf-8")
    si = sp.STARTUPINFO(); si.dwFlags |= sp.STARTF_USESHOWWINDOW; si.wShowWindow = 1
    sp.Popen(["notepad.exe", str(txt)], cwd=str(RAW), startupinfo=si)
    hwnd = 0
    for _ in range(80):
        time.sleep(0.1)
        hits = []
        def cb(h, _l):
            if win32gui.IsWindowVisible(h) and MARK in win32gui.GetWindowText(h):
                hits.append(int(h))
            return True
        win32gui.EnumWindows(lambda h, l: cb(h, l), 0)
        if hits:
            hwnd = hits[0]; break
    return hwnd


def kill_target():
    import subprocess as sp
    sp.run(["taskkill", "/F", "/IM", "notepad.exe", "/FI", "WINDOWTITLE eq %s*" % MARK],
           capture_output=True)
    for _ in range(30):
        time.sleep(0.1)
        if residue_count() == 0:
            return True
    return residue_count() == 0


def find_node(nodes, **kw):
    for n in nodes:
        if all(str(n.get(k)) == str(v) for k, v in kw.items()):
            return n
    return None


def run_cases(inject=False):
    from agentctl import core, uia_tree
    pre = residue_count()
    hwnd = spawn_target()
    assert hwnd, "靶场记事本没起来"
    bad, rows = [], []
    try:
        time.sleep(0.5)
        nodes, trunc = uia_tree.walk(hwnd, roles=("all",), max_depth=25,
                                     include_offscreen=True)
        # 动态找「同类型同名 ≥2」的一组（同名多元素案例）；找一组独立单例做其余案例
        groups = {}
        for n in nodes:
            groups.setdefault((n.get("type"), n.get("name")), []).append(n)
        dup_key = max((k for k, v in groups.items() if len(v) >= 2
                       and (v[0].get("rect") or [0]) != [0, 0, 0, 0]),
                      key=lambda k: len(groups[k]), default=None)
        assert dup_key, "没找到同名多元素组"
        dup = groups[dup_key]
        solo = [n for n in nodes
                if n.get("type") == "DocumentControl" or n.get("type") == "EditControl"]
        solo = solo[0] if solo else nodes[1]
        n1, n2 = dup[0], dup[1]
        core._eid_sessions_put("M18a判定token", hwnd, nodes,
                               {"roles": ["all"], "max_depth": 25, "tool": "M18a判据"})
        def f(n, **over):
            d = {"type": n.get("type"), "name": n.get("name"),
                 "rect": list(n.get("rect") or ()), "center": list(n.get("center") or ())}
            d.update(over)
            return d

        cases = [
            ("01_同名组第一枚", f(n1), False),
            ("02_同名组第二枚(同名多元素)", f(n2), False),
            ("03_单例元素_存在未变", f(solo), False),
            ("04_rect错标(同名串位)", f(n2, rect=list(n1.get("rect") or ())), False),
            ("05_name错标(不存在)", f(n1, name="这个名字不存在"), False),
            ("06_type错标", f(n1, type="EditControl"
                              if n1.get("type") != "EditControl" else "ButtonControl"), False),
            ("07_expectuid路径_同名第一枚", f(n1), True),
            ("08_expectuid路径_单例", f(solo), True),
            ("09_feat全空", {"type": "", "name": "", "rect": [], "center": []}, False),
        ]
        seq = []
        for name, feat, nk in cases:
            seq.append((name, probe(True, hwnd, feat, nk, inject),
                        probe(False, hwnd, feat, nk, inject)))
        # ── 状态变化类 ──
        import win32gui, win32con, ctypes
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        win32gui.MoveWindow(hwnd, l + 37, t + 23, r - l, b - t, True)
        time.sleep(0.4)
        seq.append(("10_整窗移动后_rect漂移", probe(True, hwnd, f(n1), False, inject),
                    probe(False, hwnd, f(n1), False, inject)))
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE); time.sleep(0.4)
        seq.append(("11_最小化后_树塌", probe(True, hwnd, f(n1), False, inject),
                    probe(False, hwnd, f(n1), False, inject)))
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE); time.sleep(0.4)
        seq.append(("12_还原后_恢复通过", probe(True, hwnd, f(solo), False, inject),
                    probe(False, hwnd, f(solo), False, inject)))
        seq.append(("13_hwnd无效", probe(True, 173401, f(n1), False, inject),
                    probe(False, 173401, f(n1), False, inject)))

        for name, old, new in seq:
            same = old.get("档") == new.get("档")
            if same and old.get("档") == "通过":
                same = (old.get("center") == new.get("center")
                        and old.get("enabled") == new.get("enabled"))
                if same and "expectuid" in name:
                    same = bool(old.get("uid")) and old.get("uid") == new.get("uid")
            rows.append({"例": name, "改前档": old.get("档"), "改后档": new.get("档"),
                         "一致": same,
                         "改前": {k: old.get(k) for k in ("center", "enabled", "uid")},
                         "改后": {k: new.get(k) for k in ("center", "enabled", "uid")}})
            if not same:
                bad.append(name)
        out = {"n": len(rows), "不一致例": bad, "逐例": rows,
               "靶场残留_跑后": residue_count(), "inject": inject,
               "时间": time.strftime("%Y-%m-%dT%H:%M:%S")}
        (RAW / ("M18a_对照_%s.json" % ("inject" if inject else "normal"))).write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        for r in rows:
            print("%-28s 改前=%-6s 改后=%-6s 一致=%s" %
                  (r["例"], r["改前档"], r["改后档"], r["一致"]))
        print("n=%d 不一致=%d → %s" % (len(rows), len(bad), bad or "无"))
        post = residue_count()
        kill_target()
        post = residue_count()
        print("靶场记事本残留：跑前 %d → 跑后 %d" % (pre, post))
        return bad
    finally:
        kill_target()
        time.sleep(0.2)


def mode_timing(n=20):
    """时序断言（须持测量窗口令牌）：交错测 旧路(walk) vs 新路(精确) 的 _eid_recheck。
    交错同场景抵消背景负载；<10%（±145ms 噪声门槛）的差一律写「不可判」。"""
    tok = RAW / "窗口令牌_M18a.txt"
    if not tok.exists():
        print("时序断言不可执行：无测量窗口令牌。待令牌后补跑。")
        return 0
    txt = tok.read_text(encoding="utf-8", errors="replace")
    print("令牌：", txt.splitlines()[0] if txt else "?")
    from agentctl import core, uia_tree
    pre = residue_count()
    hwnd = spawn_target()
    assert hwnd, "靶场没起来"
    try:
        time.sleep(0.5)
        nodes, _t = uia_tree.walk(hwnd, roles=("all",), max_depth=25,
                                  include_offscreen=True)
        groups = {}
        for nn in nodes:
            groups.setdefault((nn.get("type"), nn.get("name")), []).append(nn)
        dup_key = max((k for k, v in groups.items() if len(v) >= 2),
                      key=lambda k: len(groups[k]))
        n1 = groups[dup_key][0]
        feat = {"type": n1.get("type"), "name": n1.get("name"),
                "rect": list(n1.get("rect") or ()), "center": list(n1.get("center") or ())}
        walk_meta = {"roles": ["all"], "max_depth": 25}
        os.environ["AGENTCTL_EID_RECHECK_PRECISE"] = "1"   # 每次调用都读 env ⇒ 可交错
        old_ts, new_ts = [], []
        for i in range(n):
            for mode_old in (True, False):                 # 交错：旧-新-旧-新…
                os.environ["AGENTCTL_EID_RECHECK_PRECISE"] = "0" if mode_old else "1"
                t0 = time.perf_counter()
                cur, why, rctx = core._eid_recheck(hwnd, feat, walk_meta, need_keys=False)
                dt = (time.perf_counter() - t0) * 1000.0
                assert cur is not None, "测量中复核失败（场景必须稳定）"
                (old_ts if mode_old else new_ts).append(round(dt, 2))

        def stat(xs):
            xs = sorted(xs)
            return {"n": len(xs), "p50": xs[len(xs) // 2],
                    "p95": xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]}
        so, sn = stat(old_ts), stat(new_ts)
        delta = so["p50"] - sn["p50"]
        verdict = ("不可判" if abs(delta) < max(14.5, so["p50"] * 0.10)
                   else "省 %.0fms（p50，%d%%）" % (delta, round(delta * 100 / max(1, so["p50"]))))
        out = {"n每组": n, "旧路_walk": so, "新路_精确": sn,
               "p50差": round(delta, 1), "结论": verdict,
               "背景负载声明": "MiniMax Code 一直在跑用户桌宠任务 ~150~200% CPU，绝对值偏高，结论限相对比较",
               "口径": "进程内 _eid_recheck（不含 spawn/门禁），交错旧-新各 %d 次" % n,
               "时间": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "靶场残留_跑后": None}
        (RAW / "M18a_timing.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=1))
        post = residue_count()
        kill_target()
        out["靶场残留_跑后"] = residue_count()
        (RAW / "M18a_timing.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print("靶场记事本残留：跑前 %d → 跑后 %d" % (pre, residue_count()))
        return 0
    finally:
        kill_target()


def main():
    inject = "__inject" in sys.argv
    if "--mode" in sys.argv and "timing" in sys.argv:
        return mode_timing()
    bad = run_cases(inject=inject)
    if inject:
        if bad:
            print("注入跑：红（劣化定位被逮到，不一致例=%s）✔ 判据是活的" % bad)
        else:
            print("注入跑：全绿 ✘ 判据是死的（注入没被逮到）——照样 exit 1，判据须返工")
        return 1
    if bad:
        print("正常跑：红（不一致例=%s）" % bad)
        return 1
    print("正常跑：绿（三档逐例一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
