# -*- coding: utf-8 -*-
r"""tests\_MCPactor_判据.py —— 第 28 条判据：**MCP 传输必须带得上 actor**。

治的病（2026-10-19 Codex 实跑「把分镜脚本发到微信群」时踩出）：
  `core.dispatch` 的 actor 是**关键字参数**（mcp.py 从 `AGENTCTL_ACTOR` 环境变量取出后传进来），
  但它只把 `args` 往下传（`TOOLS[tool](args)`）；闸门读的是 `args.get("actor")`。
  ⇒ **actor 被算出来然后丢掉**。后果：MCP 这条主通道上**所有写动作**一律 `code=10`，
  报「本次调用没带 actor，请加 --actor」—— 而包装器
  `.codex\mcp\run_agentctl.cmd` 里明明写了 `set "AGENTCTL_ACTOR=codex"`。
  CLI 传输不受影响（act.py 的 --actor 会进 args），所以**只有 MCP 用户中招**，很难自查。

四条断言（真窗口，不 mock 闸门）：
  ① 对照：actor 为空 + 无租约 ⇒ `code=10` 且 warning 含「没带 actor」
  ② 正例：同样的调用，只把 actor 交给 **dispatch 的关键字参数** ⇒ **不再是 code=10**
  ③ 反证（`--inject`）：把注入关掉 ⇒ ② 必须失败 ⇒ 本判据红
  ④ 守门：actor **真的**没给（args 与关键字都没有）⇒ 闸门仍必须拒（防「靠取消检查蒙混过关」）

退出码：0=绿；1=红；2=验不了（无通行证 / 起不出窗 / 额度用尽 —— **2 不许当绿读**）。
"""
import ctypes
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agentctl import core, gate                            # noqa: E402

u = ctypes.windll.user32
try:
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

GREEN, RED, SKIP = [], [], []


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, str(detail)[:200]), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def notepad():
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        c = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, c, 64)
        if c.value == "Notepad" and u.IsWindowVisible(h):
            hits.append(int(h))
        return True
    u.EnumWindows(cb, 0)
    return hits[0] if hits else 0


def _ensure_notepad():
    """没有记事本就自己拉一个（返回 hwnd, pid；拿不到返回 (0, 0)）。判据结束时负责收掉。"""
    import subprocess
    import time as _t
    h = notepad()
    if h:
        return h, 0
    before = set()
    p = subprocess.Popen(["notepad.exe"])
    for _ in range(12):
        _t.sleep(0.4)
        cur = notepad()
        if cur:
            return cur, p.pid
    return 0, p.pid


def missing_actor(r):
    """是不是那条「没带 actor」的拒。**只认这一条**，别把别的拒绝算进来。

    ⚠ 收紧过一次（2026-10-19 第一次跑就抓到了自己的假绿）：
      原写法是 `"actor" in w`，结果「租约已过期（holder=codex）」也被算成通过 ——
      它同样含 "actor" 三个字母。**教训与本文件同批的两条判据同源**：
      判据自己必须比被测对象更严，否则它测的是自己。
      现在只认 T12 修过的那句原文：「没带 actor」。
    """
    w = (r.get("warning") or "") + (r.get("data") or {}).get("raw", "") if isinstance(r.get("data"), dict) else (r.get("warning") or "")
    return r.get("code") == 10 and "没带 actor" in w


def _dispatch_prefix(tool, args, *, actor=""):
    """**修前** dispatch 的真实形状：actor 拿到了手，但不往 args 里塞。

    这不是 mock —— 它逐字复现 `dispatch` 修复前的核心那一行 `TOOLS[tool](args)`，
    闸门、租约、窗口全是真的。用它当 `--inject` 的反证。
    """
    return core.TOOLS[tool](args or {})


def main():
    inject = "--inject" in sys.argv
    dispatch = _dispatch_prefix if inject else core.dispatch

    ok_pass, why, _ = gate.check("A")
    if not ok_pass:
        rec("前置·通行证", False, why, skip=True)
        return 2

    hwnd, _np_pid = _ensure_notepad()
    if not hwnd:
        rec("前置·真记事本窗口", False, "起不来记事本", skip=True)
        return 2

    # ① 对照：actor 全空 ⇒ 必须被「没带 actor」拒
    r1 = dispatch("ctl_focus", {"hwnd": hwnd}, actor="")
    rec("① 对照·actor 空 ⇒ code=10 且明说没带 actor",
        missing_actor(r1),
        "code=%s warn=%s" % (r1.get("code"), (r1.get("warning") or "")[:60]))

    # ② 正例：actor 只交给 dispatch 的关键字参数（MCP 的真实形状）
    lease = None
    try:
        lease = gate.acquire_lease("codex", ["B"], 120)
    except Exception as e:
        rec("② 正例·租约", False, "acquire_lease 失败：%s" % e, skip=True)
        return 2
    try:
        r2 = dispatch("ctl_focus", {"hwnd": hwnd}, actor="codex")
        rec("② 正例·actor 走关键字参数 ⇒ 不再是 code=10",
            not missing_actor(r2),
            "code=%s warn=%s" % (r2.get("code"), (r2.get("warning") or "")[:60]))
    finally:
        gate.release_lease((lease or {}).get("lease_id", ""))

    # ④ 守门：actor 真的没有 ⇒ 闸门仍必须拒（防「取消检查蒙混过关」）
    r4 = dispatch("ctl_focus", {"hwnd": hwnd}, actor="")
    rec("④ 守门·actor 真没给 ⇒ 闸门仍拒",
        missing_actor(r4),
        "code=%s" % r4.get("code"))

    # ③ MCP 的真实调用形状：mcp.py 就是这样从环境变量取 actor 再交给 dispatch 的
    os.environ["AGENTCTL_ACTOR"] = "codex"
    r3 = dispatch("ctl_focus", {"hwnd": hwnd}, actor=os.environ.get("AGENTCTL_ACTOR", "mcp-client"))
    rec("③ MCP 真实形状（AGENTCTL_ACTOR→dispatch）⇒ 不再是 code=10",
        not missing_actor(r3),
        "code=%s warn=%s" % (r3.get("code"), (r3.get("warning") or "")[:120]))

    if RED:
        print("\n红 %d：%s" % (len(RED), "；".join(RED)), flush=True)
        return 1
    print("\n全绿 %d" % len(GREEN), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
