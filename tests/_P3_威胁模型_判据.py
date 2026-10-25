# -*- coding: utf-8 -*-
"""P3 判据 · 威胁模型探针骨架（tools\\threat_probe.py）的离线判据（派单 Z1 · 2026-10-25）

★ 管什么（对应派单的断言清单）：
    ① 登记：T01–T12 全部登记、id 唯一、五字段（id/goal/method/needs_desktop/expected）
       齐且非空、needs_desktop 必须是 bool；
    ② 一致：needs_desktop=True 的集合 == real 白名单（REAL_ALLOWED）
       —— 改一边不改另一边必红（--inject 自证）；
    ③ 零输入：dry-run 对全部 12 项 rc=0，且把 _send_input monkeypatch 成留痕 spy 后
       **一次都没被调**（外加：pyautogui 始终没进 sys.modules）；
    ④ 默认拒绝：real 不带 --i-understand ⇒ rc=2（12 项全试）；
       带开关但非白名单 ⇒ rc=1；带开关的白名单项 ⇒ rc∈{0,3} 且 _send_input 仍未被调
       （骨架停在计划层，连 real 路径都不接输入）。

★ --inject（注入自证，判据必须有牙）：
    把 needs_desktop=True 的 T10 从 REAL_ALLOWED 里删掉（**只改本进程内存**）⇒
    一致性断言（TM-04）必须转红。被逮到 ⇒ exit 1（红=有牙，符合仓内注入自证约定）；
    没逮到 ⇒ exit 2（判据没牙，比红更糟）。

★ 跑法（约定）：
    正跑        python -X utf8 tests\\_P3_威胁模型_判据.py      ⇒ exit 0
    注入必红    … --inject                                     ⇒ exit 1
    未知参数    … --未知参数                                    ⇒ exit 0（静默忽略，不许假红）

★ 本判据离线：不碰 GUI、不发输入、不截图、不落任何临时文件
    （sys.dont_write_bytecode 先设 ⇒ 连 import 探针的 .pyc 都不写进仓）。
"""
import argparse
import importlib.util
import os
import sys

sys.dont_write_bytecode = True          # ★ 先于 import：仓内零写入（连 .pyc 都不落）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PROBE_PATH = os.path.join(REPO, "tools", "threat_probe.py")

ALL_IDS = tuple("T%02d" % i for i in range(1, 13))
REQUIRED_KEYS = ("id", "goal", "method", "needs_desktop", "expected")


class _InputSpy:
    """顶替 _send_input：真有路径想发输入，会先在这里留痕。"""

    def __init__(self):
        self.calls = []

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        return None


def load_probe():
    """从文件路径加载探针模块（不依赖包布局；读盘只读）。"""
    if not os.path.isfile(PROBE_PATH):
        return None
    spec = importlib.util.spec_from_file_location("threat_probe", PROBE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["threat_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


def rows_registration(tp):
    """① 登记 + ② 一致性。读 tp 的**当前**值 ⇒ --inject 改完重跑同一函数即可对照。"""
    rows = []
    probes = getattr(tp, "PROBES", None) or ()
    ids = tuple(p.get("id", "?") for p in probes)
    rows.append(("TM-01", "T01–T12 全部登记（不多不少）", ",".join(ALL_IDS),
                 ",".join(ids) or "（空）", tuple(sorted(ids)) == ALL_IDS))
    rows.append(("TM-02", "id 唯一", "12 个唯一 id",
                 "%d 个唯一" % len(set(ids)), len(ids) == len(set(ids)) == 12))
    bad_keys = [p.get("id", "?") for p in probes
                if not isinstance(p.get("needs_desktop"), bool)
                or any(p.get(k) in (None, "") for k in REQUIRED_KEYS)]
    rows.append(("TM-03", "五字段齐且非空（needs_desktop 必须是 bool）",
                 "无缺", "；".join(bad_keys) or "无缺", not bad_keys))
    nd = tuple(p["id"] for p in probes if p.get("needs_desktop") is True)
    wl = tuple(getattr(tp, "REAL_ALLOWED", ()))
    rows.append(("TM-04", "needs_desktop=True 集合 == real 白名单",
                 "白名单=%s" % (wl,), "桌面项=%s 白名单=%s" % (nd, wl),
                 set(nd) == set(wl)))
    rc = (getattr(tp, "RC_OK", None), getattr(tp, "RC_ARGS", None),
          getattr(tp, "RC_CONFIRM", None), getattr(tp, "RC_ENV", None))
    rows.append(("TM-05", "退出码常量 0/1/2/3 就位", "(0, 1, 2, 3)",
                 str(rc), rc == (0, 1, 2, 3)))
    return rows


def rows_behaviour(tp, spy):
    """③ 零输入 + ④ 默认拒绝。全部 in-process 调 main()（不开子进程、不发输入）。"""
    rows = []
    rcs = [(tid, tp.main(["dry-run", tid])) for tid in ALL_IDS]
    bad = [r for r in rcs if r[1] != tp.RC_OK]
    rows.append(("TM-06", "dry-run 全部 12 项 rc=0", "12/12 为 0",
                 "%d/12 为 0%s" % (12 - len(bad), ("，异常=%s" % bad) if bad else ""),
                 not bad))
    rows.append(("TM-07", "dry-run 全程 _send_input 未被调（monkeypatch 留痕=0）",
                 "0 次", "%d 次" % len(spy.calls), not spy.calls))
    rows.append(("TM-08", "pyautogui 始终没进 sys.modules", "不在",
                 "在" if "pyautogui" in sys.modules else "不在",
                 "pyautogui" not in sys.modules))
    bad2 = [tid for tid in ALL_IDS if tp.main(["real", tid]) != tp.RC_CONFIRM]
    rows.append(("TM-09", "real 不带 --i-understand ⇒ rc=2（12 项全试）", "12/12 为 2",
                 "%d/12 为 2%s" % (12 - len(bad2), ("，异常=%s" % bad2) if bad2 else ""),
                 not bad2))
    refuse = [tid for tid in ("T01", "T05", "T08", "T12")
              if tp.main(["real", tid, "--i-understand"]) != tp.RC_ARGS]
    rows.append(("TM-10", "real 带开关但非白名单 ⇒ rc=1（抽 T01/T05/T08/T12）", "4/4 为 1",
                 "%d/4 为 1%s" % (4 - len(refuse), ("，异常=%s" % refuse) if refuse else ""),
                 not refuse))
    calls_before = len(spy.calls)
    rc_real = tp.main(["real", "T09", "--i-understand"])
    rows.append(("TM-11", "real 白名单项带开关 ⇒ rc∈{0,3} 且仍 0 次输入（骨架停在计划层）",
                 "rc∈{0,3} 且 0 次",
                 "rc=%s、新增输入留痕 %d 次" % (rc_real, len(spy.calls) - calls_before),
                 rc_real in (tp.RC_OK, tp.RC_ENV) and len(spy.calls) == calls_before))
    rc_list = tp.main(["list"])
    rows.append(("TM-12", "list ⇒ rc=0", "0", str(rc_list), rc_list == tp.RC_OK))
    rc_bad = tp.main(["dry-run", "T99"])
    rows.append(("TM-13", "dry-run 未知编号 ⇒ rc=1", "1", str(rc_bad),
                 rc_bad == tp.RC_ARGS))
    return rows


def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for aid, desc, want, got, ok in rows:
        print("  %-7s %-52s 期望=%-14s 实得=%-34s %s"
              % (aid, desc[:52], str(want)[:14], str(got)[:34],
                 "PASS" if ok else "FAIL"), flush=True)


def do_normal():
    tp = load_probe()
    if tp is None:
        print("!! tools\\threat_probe.py 不在 ⇒ 判据不成立（不许当通过）")
        return 1
    rows = rows_registration(tp)
    spy = _InputSpy()
    orig, tp._send_input = tp._send_input, spy
    try:
        rows += rows_behaviour(tp, spy)
    finally:
        tp._send_input = orig
    show_rows("正跑 · 登记与行为断言", rows)
    print()
    print("=" * 78)
    fails = [r for r in rows if not r[4]]
    if fails:
        print("【红】失败 %d 项：%s" % (len(fails), [f[0] for f in fails]))
        return 1
    print("【绿】登记/一致性/零输入/默认拒绝 全过 ⇒ 探针骨架可信")
    return 0


def do_inject():
    tp = load_probe()
    if tp is None:
        print("!! tools\\threat_probe.py 不在 ⇒ 判据不成立")
        return 1
    base = rows_registration(tp)
    show_rows("注入前 · 基线必须全绿", base)
    if not all(r[4] for r in base):
        print("【✗】基线本身就不绿，注入对照失去意义 ⇒ 先修正跑再谈有牙没牙")
        return 2
    orig = tp.REAL_ALLOWED
    tp.REAL_ALLOWED = tuple(t for t in orig if t != "T10")   # ★ 故意删掉 needs_desktop=True 的 T10
    try:
        after = rows_registration(tp)
    finally:
        tp.REAL_ALLOWED = orig
    show_rows("注入后 · 白名单删 T10（needs_desktop=True）⇒ 一致性必须红", after)
    print()
    print("=" * 78)
    if not dict((r[0], r) for r in after)["TM-04"][4]:
        collateral = [r[0] for r in after if not r[4] and r[0] != "TM-04"]
        if collateral:
            print("     （附带转红：%s —— 同一处缺陷的连带影响，正常）" % collateral)
        print("【红】注入被逮到（TM-04 转红）⇒ 判据有牙 ⇒ exit=1")
        return 1
    print("【✗】删了白名单里的桌面项，一致性断言居然没红 ⇒ 判据没牙 ⇒ exit=2")
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红，exit=1）")
    args, _unknown = ap.parse_known_args()      # ★ 未知参数静默忽略（不许假红）

    print("== P3 判据 · 威胁模型探针骨架（tools\\threat_probe.py） ==")
    print("[口径] in-process 调 main()；_send_input 换成留痕 spy；"
          "不碰 GUI/输入/截图/仓内文件")
    print("[模式] %s" % ("注入自证（--inject）" if args.inject else "正跑"))
    return do_inject() if args.inject else do_normal()


if __name__ == "__main__":
    raise SystemExit(main())
