# -*- coding: utf-8 -*-
r"""环境自检 · 判据（2026-10-25 · 工单 W4）

被测：tools\env_probe.py（离线只读环境自检）。
纯离线：不联网、不装依赖、不真卸载任何东西（注入走 env_probe._INJECT 参数开关）。

本判据**只钉行为、不改行为**：
  ① decide 退出码语义：0 全满足 / 1 有硬阻塞（**优先于**警告）/ 2 只有警告 / 空列表 0；
  ② 真实探测结构：11 项点名检查齐全（版本/位数/pywin32/uiautomation/PIL/DPI 感知/
     系统DPI 缩放/屏幕数/交互桌面/msvcrt/LOCALAPPDATA）；状态合法；非 OK 必带建议；
     渲染里非 OK 行必含「建议：」；行为关系断言（3.12 ⇔ OK 等，机器无关）；
  ③ 注入路径（_INJECT 开关，用完清空）：
       版本不对 (3,11,8) ⇒ 该项 BLOCK 且 decide==1；
       缺 PIL.Image      ⇒ 该项 WARN 且建议提到 requirements；decide 随全局；
       LOCALAPPDATA 不可写 ⇒ 该项 BLOCK 且 decide==1；
     —— 注入只影响点名项，别的项不被顺手改写；
  ④ CLI 一致性：真跑 python -X utf8 tools\env_probe.py（子进程，离线），
     退出码 == decide(collect_checks())；输出含三类标记、小结/退出码、全部点名项。

跑法：python -X utf8 tests\_P3_环境自检_判据.py
      python -X utf8 tests\_P3_环境自检_判据.py --inject   # 必红
      --inject 干的事：把 decide 换成「警告当阻塞、阻塞当警告」的坏语义表，
      再用**同一组断言**重跑 —— 退出码语义写反了就必须抓到。
退出码：0 绿；1 红。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import env_probe                                               # noqa: E402

FAILS = []

_MANDATED = ("Python 版本", "位数", "pywin32", "uiautomation", "PIL", "DPI 感知",
             "系统主屏 DPI", "屏幕数", "交互桌面", "msvcrt", "LOCALAPPDATA")

_OK_ONLY = [env_probe.Check(env_probe.OK, "x", "d", "")]
_WARN = env_probe.Check(env_probe.WARN, "x", "d", "建议")
_BLOCK = env_probe.Check(env_probe.BLOCK, "x", "d", "建议")


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  X   ") + name + (("  —— " + detail) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


def run_semantics(decide_fn, tag=""):
    """decide 的退出码语义表（同一组断言既验现役也验注入）。"""
    check(tag + "全满足 ⇒ 0", decide_fn(_OK_ONLY) == 0, "实际 %r" % (decide_fn(_OK_ONLY),))
    check(tag + "只有警告 ⇒ 2", decide_fn(_OK_ONLY + [_WARN]) == 2,
          "实际 %r" % (decide_fn(_OK_ONLY + [_WARN]),))
    check(tag + "只有硬阻塞 ⇒ 1", decide_fn(_OK_ONLY + [_BLOCK]) == 1,
          "实际 %r" % (decide_fn(_OK_ONLY + [_BLOCK]),))
    check(tag + "阻塞压过警告 ⇒ 1（不是 2）", decide_fn(_OK_ONLY + [_WARN, _BLOCK]) == 1,
          "实际 %r" % (decide_fn(_OK_ONLY + [_WARN, _BLOCK]),))
    check(tag + "空列表 ⇒ 0", decide_fn([]) == 0, "实际 %r" % (decide_fn([]),))


def run_structure(tag=""):
    """真实探测：结构合法 + 机器无关的关系断言。"""
    checks = env_probe.collect_checks()
    by_name = {c.name: c for c in checks}
    for kw in _MANDATED:
        check(tag + "点名检查在场：%s" % kw,
              any(kw in c.name for c in checks), "实际 %r" % ([c.name for c in checks],))
    check(tag + "状态合法（OK/WARN/BLOCK）",
          all(c.status in (env_probe.OK, env_probe.WARN, env_probe.BLOCK) for c in checks))
    check(tag + "非 OK 项必带建议",
          all(c.advice for c in checks if c.status != env_probe.OK),
          "实际 %r" % ([(c.name, c.advice) for c in checks if c.status != env_probe.OK and not c.advice],))

    # 关系断言（机器无关）：探测结论必须与解释器自身事实一致
    py_ok = (sys.version_info[0], sys.version_info[1]) == (3, 12)
    py_chk = by_name["Python 版本（3.12 硬要求）"]
    check(tag + "版本探测与解释器事实一致（3.12 ⇔ OK）",
          (py_chk.status == env_probe.OK) == py_ok, "实际 %r（本解释器 %s）" % (py_chk.status, py_ok))

    rendered = env_probe.render(checks)
    bad_checks = [c for c in checks if c.status != env_probe.OK]
    check(tag + "渲染：非 OK 行必含「建议：<原文>」",
          all(("建议：%s" % c.advice) in rendered for c in bad_checks),
          "渲染 %r" % (rendered[:300],))
    return checks


def run_scenarios(tag=""):
    """注入路径：_INJECT 参数开关模拟坏环境，不真卸载；用完清空。"""
    # 版本不对 ⇒ BLOCK，decide==1
    env_probe._INJECT["python"] = (3, 11, 8)
    try:
        checks = env_probe.collect_checks()
        py_chk = next(c for c in checks if "Python 版本" in c.name)
        check(tag + "注入 3.11 ⇒ 版本项 BLOCK 且写明 3.11",
              py_chk.status == env_probe.BLOCK and "3.11" in py_chk.detail,
              "实际 %r" % (py_chk,))
        check(tag + "注入 3.11 ⇒ decide==1", env_probe.decide(checks) == 1,
              "实际 %r" % (env_probe.decide(checks),))
        check(tag + "注入 3.11 不波及别的项（msvcrt 仍照实探测）",
              next(c for c in checks if "msvcrt" in c.name).status == env_probe.OK)
    finally:
        env_probe._INJECT.clear()

    # 缺依赖 ⇒ WARN（离线判据仍可跑），建议里指路 requirements
    env_probe._INJECT["imports"] = {"PIL.Image": False}
    try:
        checks = env_probe.collect_checks()
        pil_chk = next(c for c in checks if c.name.startswith("PIL"))
        check(tag + "注入缺 PIL ⇒ WARN 且建议指向 requirements",
              pil_chk.status == env_probe.WARN and "requirements" in pil_chk.advice,
              "实际 %r" % (pil_chk,))
        check(tag + "注入缺 PIL ⇒ 无阻塞时 decide==2",
              env_probe.decide(checks) == 2, "实际 %r" % (env_probe.decide(checks),))
    finally:
        env_probe._INJECT.clear()

    # LOCALAPPDATA 不可写 ⇒ BLOCK（硬阻塞），decide==1
    env_probe._INJECT["localappdata_writable"] = False
    try:
        checks = env_probe.collect_checks()
        la_chk = next(c for c in checks if "LOCALAPPDATA" in c.name)
        check(tag + "注入不可写 ⇒ LOCALAPPDATA 项 BLOCK",
              la_chk.status == env_probe.BLOCK, "实际 %r" % (la_chk,))
        check(tag + "注入不可写 ⇒ decide==1（阻塞优先）",
              env_probe.decide(checks) == 1, "实际 %r" % (env_probe.decide(checks),))
    finally:
        env_probe._INJECT.clear()
    check(tag + "注入已清空（_INJECT 回到空）", env_probe._INJECT == {}, "实际 %r" % (env_probe._INJECT,))


def run_cli(tag=""):
    """CLI 一致性：真跑一次子进程（离线），退出码与进程内 decide 对表。"""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", os.path.join(ROOT, "tools", "env_probe.py")],
        capture_output=True, timeout=120)
    out = proc.stdout.decode("utf-8", "replace")
    expected = env_probe.decide(env_probe.collect_checks())
    check(tag + "CLI 退出码 == decide(collect_checks())（%d vs %d）" % (proc.returncode, expected),
          proc.returncode == expected, "stderr: %s" % proc.stderr.decode("utf-8", "replace")[:300])
    check(tag + "输出含三类标记至少一类", any(m in out for m in ("✅", "⚠️", "❌")))
    check(tag + "输出含小结与退出码", "小结" in out and "退出码" in out)
    for kw in _MANDATED:
        check(tag + "CLI 输出点名：%s" % kw, kw in out)


print("== ① decide 退出码语义 ==")
run_semantics(env_probe.decide)

print("== ② 真实探测：结构 / 关系断言 ==")
run_structure()

print("== ③ 注入路径（_INJECT 开关，不真卸载） ==")
run_scenarios()

print("== ④ CLI 一致性（子进程真跑） ==")
run_cli()

print()
if "--inject" in sys.argv:
    print("== 注入：decide 换成「警告当阻塞、阻塞当警告」的坏语义表 —— 同一组断言必须抓到 ==")

    def _bad_decide(checks):
        statuses = {c.status for c in checks}
        if env_probe.WARN in statuses:
            return 1
        if env_probe.BLOCK in statuses:
            return 2
        return 0

    run_semantics(_bad_decide, tag="注入·")
    if FAILS:
        print("【红】%d 项失败：%s" % (len(FAILS), FAILS))
        raise SystemExit(1)
    print("【红】注入模式必须红：坏语义表没被同一组断言抓到 ⇒ 判据自证了，别信这次的绿")
    raise SystemExit(1)
if FAILS:
    print("【红】%d 项失败：%s" % (len(FAILS), FAILS))
    raise SystemExit(1)
print("【绿】全部通过：退出码语义（0/1/2、阻塞优先）正确；11 项点名检查齐全且探测与解释器事实一致；"
      "注入路径（坏版本/缺依赖/不可写）各就各位；CLI 退出码与进程内 decide 对表。")
