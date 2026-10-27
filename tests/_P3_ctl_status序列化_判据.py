# -*- coding: utf-8 -*-
r"""_P3_ctl_status序列化_判据.py —— ctl_status 的响应**必须能 JSON 序列化**（2026-10-27）

★ 这条判据是为了钉住一个真 bug：

  现象：调 ctl_status 直接返回 JSON-RPC 内部错误
        {"code": -32603, "message": "Internal error: Object of type LockedDict is not JSON serializable"}

  根因：src/agentctl/core.py 的 _my_integrity() 返回的是 **LockedDict 对象本身**
        （继承自 object，不是 dict ⇒ json.dumps 必抛），
        而 t_status() 把它塞进 st["service"]["integrity"] ⇒ **整个 ctl_status 挂掉**。

  为什么这条判据必须有：ctl_status 是「**任何 agent 开工第一件事**」——
  契约里白纸黑字写着。而它的 integrity 字段恰恰是用来诊断
  「UIPI 拦点击 / 完整性级别不对」这类问题的。
  ⇒ **它自己先挂了**，agent 连自己什么状态都看不到，只能瞎猜。

本判据钉三件事：
  1. `_my_integrity()` 的返回值**必须能被 json.dumps 序列化**（不许返回 LockedDict 对象）；
  2. `t_status()` 的整个返回值**必须能被 json.dumps 序列化**（端到端，不只那一格）；
  3. **注入必红**：把实现换回「返回 LockedDict 对象本身」⇒ 必须 exit=1。

用法：
  python tests\_P3_ctl_status序列化_判据.py           # 正跑：exit 0
  python tests\_P3_ctl_status序列化_判据.py --inject  # 注入回缺陷 ⇒ 必须 exit 1
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILS = []
INJECT = "--inject" in sys.argv


def ok(name, cond, detail=""):
    print("  [%s] %s%s" % ("绿" if cond else "红", name, ("  ← " + detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def load_core():
    spec = importlib.util.spec_from_file_location(
        "agentctl_core_probe", os.path.join(ROOT, "src", "agentctl", "core.py"))
    # core.py 需要作为包内模块 import（它内部 from agentctl import ...）
    from agentctl import core
    return core


def main() -> int:
    print("== _P3_ctl_status序列化 · %s ==" % ("注入模式" if INJECT else "正跑"))
    from agentctl import core, sync

    # ── ① _my_integrity() 的返回值必须可序列化 ────────────────────────────
    if INJECT:
        # ★ 注入：模拟修好前的行为（返回 LockedDict 对象本身）
        def bad_integrity():
            d = sync.LockedDict()
            d.update({"rid": "0x2000", "name": "Medium"})
            return d
        got = bad_integrity()
    else:
        got = core._my_integrity()

    ok("① _my_integrity() 返回的是普通 dict（不是 LockedDict）",
       isinstance(got, dict) and not isinstance(got, sync.LockedDict),
       "实际类型=%s" % type(got).__name__)

    try:
        json.dumps(got, ensure_ascii=False)
        ser = True
        err = ""
    except TypeError as e:
        ser = False
        err = str(e)
    ok("① _my_integrity() 的返回值能被 json.dumps 序列化", ser, err)

    # ── ② 缓存语义不能丢：仍然只真正探测一次 ─────────────────────────────
    if not INJECT:
        a = core._my_integrity()
        b = core._my_integrity()
        ok("② 缓存语义保留（两次调用拿到等价内容）", a == b,
           "第一次=%r 第二次=%r" % (a, b))

    # ── ③ t_status() 整个返回值必须可序列化（端到端）──────────────────────
    try:
        st = core.t_status({})
        payload = json.dumps(st, ensure_ascii=False)
        ser2 = True
        err2 = ""
    except TypeError as e:
        ser2 = False
        err2 = str(e)
        st = None
    ok("③ t_status() 的整个返回值能被 json.dumps 序列化", ser2, err2)

    if st is not None:
        ok("③ t_status() 返回 ok=True", bool(st.get("ok")), repr(st.get("code")))
        # ★ 注意：t_status 的内容都在信封的 **data** 下（env() 把 data 单独放一层），
        #   不是顶层。判据取错层级 ⇒ 会误报「service 字段不存在」。
        body = st.get("data") or {}
        svc = body.get("service") or {}
        ok("③ t_status() 带 service 字段", bool(svc),
           "data 的键=%s" % sorted(body.keys()))
        ok("③ t_status() 带 integrity 字段", "integrity" in svc,
           "service 的键=%s" % sorted(svc.keys()))
        integ = svc.get("integrity")
        ok("③ integrity 是普通 dict", isinstance(integ, dict),
           "实际类型=%s" % type(integ).__name__)
        ok("③ integrity 非空（真的探测到了完整性级别）", bool(integ),
           "integrity=%r" % integ)

    # ── ④ 注入模式必须整体判红 ────────────────────────────────────────────
    if INJECT:
        ok("④ 注入模式下 ① 必须判红（证明这条判据有牙）", bool(FAILS),
           "注入后一条都没红 ⇒ 判据没抓到缺陷")

    print()
    if FAILS:
        print("== 汇总：红 %d 条 ==" % len(FAILS))
        for f in FAILS:
            print("   红：%s" % f)
        return 1
    print("== 汇总：全绿 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
