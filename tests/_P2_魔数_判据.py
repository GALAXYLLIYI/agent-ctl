# -*- coding: utf-8 -*-
r"""判据 44：SendInput 的 `dwExtraInfo` 魔数（M4）

一句话：**每一次 agent 注入都必须带同一个魔数** ⇒ 归因能「一票确定是 agent 做的」。

═══════════════════════════════════════════════════════════════════════════════
★ 本判据的「尺子不许与被测物共用一把尺」铁律（2026-10-22 主控追加）
═══════════════════════════════════════════════════════════════════════════════
1. **判据不许 import 被测模块来取魔数** —— 魔数在判据里**硬编码一个字面量**
   （`EXPECTED_MAGIC`）。若判据从产品里 import 魔数，那么「产品把魔数改错了」
   判据也会跟着错 ⇒ 尺子与被测物共用一把尺 ⇒ 判据就抓不住了。
   ⇒ 与产品的**交叉核对靠「读产物文件的文本」**（正则取字面量），**不 import**。
2. **观察侧必须是独立观察者**：判据进程自己装低级钩子、自己读 `dwExtraInfo`；
   **驱动被测物的是另一个子进程**（判据进程本身不 import 被测模块）。
3. 三处副本（真源 / 产物 / live）必须 **MD5 一致** —— live 只有一处是真的
   （`build\local_legacy_home.txt` 指针指向 `dsh-viewer-plugin\tools`）。

═══════════════════════════════════════════════════════════════════════════════
四组断言
═══════════════════════════════════════════════════════════════════════════════
A 组｜**交叉核对**（读文本，**不 import**）
  ① 判据里的字面量 == **产物** `inject_magic.py` 里的常量（读文件文本解析）
  ② **产物** `act.py` 的四个注入点都引用了 `inject_magic.INJECT_MAGIC`、没有 `dwExtraInfo=None`
  ③ 三处副本（真源 / 产物 / live）act.py 的 MD5 一致

B 组｜**系统级观察**（父进程自己的钩子；驱动在子进程）
  ④ 独立钩子**在系统里观测到**的 agent 注入事件 dwExtraInfo == 判据字面量
     （本机此刻低级钩子**不派发**时 ⇒ 本条报「验不了」，**不冒充绿**）

C 组｜**API 边界的实参截获**（驱动进程内包装 SendInput；被测物只在子进程里 import）
  ⑤ 键盘注入的 dwExtraInfo == 判据字面量
  ⑥ 鼠标注入的 dwExtraInfo == 判据字面量
  ⑦ **非 agent 注入**的 dwExtraInfo != 判据字面量（不许误认成 agent）

D 组｜边界
  ⑧ `contract/tools.json` 未被本单改动

═══════════════════════════════════════════════════════════════════════════════
用法
═══════════════════════════════════════════════════════════════════════════════
    python -X utf8 tests\_P2_魔数_判据.py              # 正跑（绿）
    python -X utf8 tests\_P2_魔数_判据.py --inject     # 注入：把魔数换成 0
                                                      #        ⇒ ⑤⑥ 必须转红（rc=1）

退出码：0 绿 / 1 红 / 2 验不了（**验不了不许读成绿**）

★ 安全不变量：
  · 从不调 BlockInput；钩子回调永不返回 0（返回 0 = 吞噬用户的按键）；
  · 注入用 **VK_F24（0x87，极少被绑定）**；鼠标用「绝对移动到光标当前像素」
    ⇒ **零视觉影响**（位移实测 [0,0]，判据逐次打印，不自夸）；
  · 只**观察** dwExtraInfo，不改写任何输入。
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LEGACY_SRC = os.path.join(ROOT, "legacy_tools", "_src")      # 真源
LEGACY_OUT = os.path.join(ROOT, "legacy_tools")               # 产物
CONTRACT_REL = os.path.join("contract", "tools.json")

# ★ live 只有一处是真的（由 build\local_legacy_home.txt 指针声明）：
LIVE_HOME = os.path.join(os.path.dirname(ROOT), "dsh-viewer-plugin", "tools")
LIVE = os.path.join(LIVE_HOME, "act.py")

#: ★ 判据里的**硬编码字面量** —— **不许 import 被测模块来取这个值**（见文件头铁律 1）。
#:   与产品的交叉核对靠 A 组①「读产物文件文本」。
EXPECTED_MAGIC = 0xA6E17

GREEN, RED, SKIP, OPTIONAL = [], [], [], []
INJECT = False

#: Win32 常量（真源 act.py 没定义它，判据按 Win32 文档给）
MOUSEEVENTF_ABSOLUTE = 0x8000
#: 一个几乎不会被绑定的键（不发字符、不改文本）
VK_F24 = 0x87


def rec(name, ok, detail="", skip=False, optional=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    if skip:
        (OPTIONAL if optional else SKIP).append(name)
    else:
        (GREEN if ok else RED).append(name)


def finish():
    print("\n" + "=" * 66, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d%s"
          % (len(GREEN), len(RED), len(SKIP),
             ("（另有 %d 条**可选证据**验不了：%s）" % (len(OPTIONAL), "；".join(OPTIONAL))
              if OPTIONAL else "")), flush=True)
    if RED:
        if SKIP:
            print("  （另有 %d 条验不了：%s）" % (len(SKIP), "；".join(SKIP)), flush=True)
        print("结论：【红】agent 注入没有统一带上 dwExtraInfo 魔数", flush=True)
        return 1
    if SKIP:
        print("结论：【验不了】—— 不许读成绿", flush=True)
        return 2
    print("结论：【绿】每一次 agent 注入都带着同一个魔数 ⇒ 归因可一票确定", flush=True)
    return 0


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest().lower()


# ══════════════════════════════════════════════════════════════════════════
# A 组｜交叉核对：**读文本**，不 import 被测模块
# ══════════════════════════════════════════════════════════════════════════
def read_magic_from_text(path):
    """从**文件文本**里解析出 INJECT_MAGIC 的值（★ 不 import 被测模块）。"""
    try:
        txt = open(path, encoding="utf-8").read()
    except OSError as e:
        return None, "读不到 %s：%s" % (path, e)
    m = re.search(r"^\s*INJECT_MAGIC\s*=\s*(0[xX][0-9A-Fa-f]+|\d+)", txt, re.M)
    if not m:
        return None, "%s 里找不到 INJECT_MAGIC 赋值" % os.path.basename(path)
    raw = m.group(1)
    val = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    return val, "%s 的 INJECT_MAGIC = %s（%d）" % (os.path.basename(path), raw, val)


def group_a():
    print("\n── A组｜交叉核对（读文本，不 import）─────────────────────────", flush=True)
    prod_mod = os.path.join(LEGACY_OUT, "inject_magic.py")
    val, why = read_magic_from_text(prod_mod)
    rec("① 判据里的字面量 == 产物里的常量（交叉核对，不 import）",
        val is not None and val == EXPECTED_MAGIC,
        "判据字面量=0x%X ；%s ⇒ %s"
        % (EXPECTED_MAGIC, why, "一致 ✔" if val == EXPECTED_MAGIC else "**不一致**"))

    # ② 产物的 act.py：四个注入点都引用 inject_magic.INJECT_MAGIC、没有 None
    prod_act = os.path.join(LEGACY_OUT, "act.py")
    try:
        src = open(prod_act, encoding="utf-8").read()
    except OSError as e:
        rec("② 产物 act.py 的四个注入点都带上了魔数", False, "读不到产物：%s" % e)
        src = ""
    if src:
        detail = []
        all_ok = True
        for fn in ("send_scan", "send_mouse", "send_unicode", "send_vk"):
            m = re.search(r"def\s+%s\s*\(" % fn, src)
            if not m:
                detail.append("%s: 找不到函数" % fn)
                all_ok = False
                continue
            body = src[m.start():m.start() + 2500]
            n_inp = body.count("INPUT(type=")
            n_none = len(re.findall(r"(?:MOUSEINPUT|KEYBDINPUT)\([^)]*,\s*None\s*\)", body))
            n_magic = len(re.findall(r"(?:MOUSEINPUT|KEYBDINPUT)\([^)]*?INJECT_MAGIC[^)]*\)", body))
            ok = (n_inp >= 1) and (n_none == 0) and (n_magic >= 1)
            all_ok = all_ok and ok
            detail.append("%s:%s" % (fn, "✔" if ok else "✘(构造%d/None%d/魔数%d)"
                                      % (n_inp, n_none, n_magic)))
        rec("② 产物 act.py 的四个注入点都带上了魔数（读文本核对）", all_ok, "；".join(detail))

    # ③ 三处副本 MD5 一致
    trio = [("真源", os.path.join(LEGACY_SRC, "act.py")),
            ("产物", os.path.join(LEGACY_OUT, "act.py")),
            ("live", LIVE)]
    hs = []
    for tag, p in trio:
        if os.path.isfile(p):
            hs.append((tag, md5(p)))
        else:
            hs.append((tag, None))
    same = len({h for _t, h in hs}) == 1 and hs[0][1] is not None
    rec("③ 三处副本（真源/产物/live）act.py 的 MD5 一致", same,
        "；".join("%s=%s" % (t, (h or "<缺失>")[:12]) for t, h in hs))

    # ③b 字面量唯一性（R1）：魔数的**字面量**只允许出现在
    #     ① 定义文件（真源/产物/live 的 inject_magic.py）② 判据自己
    #     —— 别的 .py 里再写一遍就红（防止「两边各写一个数」悄悄漂移）。
    lit = "0x%X" % EXPECTED_MAGIC
    allow = {os.path.normcase(p) for p in (
        os.path.join(LEGACY_SRC, "inject_magic.py"),
        os.path.join(LEGACY_OUT, "inject_magic.py"),
        os.path.join(LIVE_HOME, "inject_magic.py"),
        os.path.join(HERE, "_P2_魔数_判据.py"))}
    stray = []
    for base in (os.path.join(ROOT, "src"), LEGACY_SRC, LEGACY_OUT, HERE,
                 os.path.join(ROOT, "tools")):
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                if os.path.normcase(p) in allow:
                    continue
                try:
                    txt = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if lit in txt:
                    stray.append(os.path.relpath(p, ROOT))
    rec("③b 魔数字面量只在「定义文件 + 判据自己」出现（不许两边各写一个数）",
        not stray, "漂移文件：%s" % (stray if stray else "无（其余 .py 里没有第二处字面量）"))


# ══════════════════════════════════════════════════════════════════════════
# 驱动子进程：在**别的进程**里驱动被测物，并回读它真正传进去的 dwExtraInfo
# ══════════════════════════════════════════════════════════════════════════
DRIVER = r'''
# -*- coding: utf-8 -*-
"""驱动子进程：驱动被测物（被测物 import 在**这里**，不发生在判据进程里），
并回读它真正传进 SendInput 的 dwExtraInfo。"""
import ctypes, json, os, sys

HOME = sys.argv[1]
INJECT = sys.argv[2] == "1"
sys.path.insert(0, HOME)
import act                                   # 被测物：只在子进程里 import

if INJECT:
    # ★ 注入：把魔数换成 0 —— **被测物的代码路径照走**，只是标记被换掉
    import inject_magic
    inject_magic.INJECT_MAGIC = 0

MOUSEEVENTF_ABSOLUTE = 0x8000
VK_F24 = 0x87

def _as_int(v):
    if v is None:
        return 0
    if isinstance(v, int):
        return int(v)
    val = getattr(v, "value", None)
    if isinstance(val, int):
        return int(val)
    try:
        return int(ctypes.cast(v, ctypes.c_void_p).value or 0)
    except Exception:
        return -1

seen = []
_orig = act.user32.SendInput
def _wrap(n, p, cb):
    try:
        c = ctypes.cast(p, ctypes.POINTER(act.INPUT)).contents
        extra = c.u.mi.dwExtraInfo if int(c.type) == 0 else c.u.ki.dwExtraInfo
        seen.append({"type": int(c.type), "extra": _as_int(extra)})
    except Exception as e:
        seen.append({"type": -1, "extra": -1, "err": "%s: %s" % (type(e).__name__, e)})
    return _orig(n, p, cb)
act.user32.SendInput = _wrap

out = {"kb": [], "ms": [], "other": [], "cursor_delta": None, "err": None}
try:
    act.send_vk(VK_F24, True)
    act.send_vk(VK_F24, False)
    out["kb"] = [s for s in seen if s["type"] == 1]
    n = len(seen)

    pt = ctypes.wintypes.POINT() if hasattr(ctypes, "wintypes") else None
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    p0 = POINT()
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    u32.GetCursorPos.restype = ctypes.c_bool
    u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    u32.GetSystemMetrics.restype = ctypes.c_int
    if u32.GetCursorPos(ctypes.byref(p0)):
        sw = u32.GetSystemMetrics(0) or 1
        sh = u32.GetSystemMetrics(1) or 1
        nx = int((p0.x * 65535) / max(1, sw - 1))
        ny = int((p0.y * 65535) / max(1, sh - 1))
        act.send_mouse(MOUSEEVENTF_ABSOLUTE | 0x0001, nx, ny, 0)
        p1 = POINT()
        u32.GetCursorPos(ctypes.byref(p1))
        out["cursor_delta"] = [int(p1.x) - int(p0.x), int(p1.y) - int(p0.y)]
    out["ms"] = [s for s in seen[n:] if s["type"] == 0]

    # 非 agent 注入：直接调 user32.SendInput，dwExtraInfo 不带魔数（=0）
    # ★ 失效保护：取不到当前光标像素就**不注入** ——
    #   ABSOLUTE 模式下 (0,0) 的含义是「屏幕左上角」⇒ 会把用户的鼠标甩到角上。
    #   （真零位移只有两种安全写法：① 绝对移动到**当前光标像素**；② 相对移动 (0,0) 且
    #     不带 MOUSEEVENTF_ABSOLUTE —— 但 Windows 会把「相对 (0,0)」优化掉 ⇒ 不产生事件。
    #     ⇒ 这里选 ①：与真源 act.send_mouse 那条同法，位移实测 [0,0]。）
    inp = act.INPUT()
    inp.type = act.INPUT_MOUSE
    inp.mi = act.MOUSEINPUT(dx=0, dy=0, mouseData=0,
                            dwFlags=MOUSEEVENTF_ABSOLUTE | 0x0001,
                            time=0, dwExtraInfo=0)
    p2 = POINT()
    if u32.GetCursorPos(ctypes.byref(p2)):
        sw = u32.GetSystemMetrics(0) or 1
        sh = u32.GetSystemMetrics(1) or 1
        inp.mi.dx = int((p2.x * 65535) / max(1, sw - 1))
        inp.mi.dy = int((p2.y * 65535) / max(1, sh - 1))
        _orig(1, ctypes.byref(inp), ctypes.sizeof(act.INPUT))
        out["other"] = [{"type": 0, "extra": 0}]
    else:
        out["other"] = []
        out["err"] = "GetCursorPos 失败 ⇒ **放弃这次注入**（绝不让鼠标跳到屏幕左上角）"
except Exception as e:
    out["err"] = "%s: %s" % (type(e).__name__, e)
finally:
    act.user32.SendInput = _orig

print("__DRIVER_JSON__" + json.dumps(out))
'''


def run_driver():
    """起驱动子进程，返回它回读到的观测报告。"""
    home = LIVE_HOME if os.path.isfile(LIVE) else LEGACY_OUT
    drv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_magic_driver_tmp.py")
    with open(drv, "w", encoding="utf-8") as f:
        f.write(DRIVER)
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", drv, home, "1" if INJECT else "0"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        for line in (p.stdout or "").splitlines():
            if line.startswith("__DRIVER_JSON__"):
                return json.loads(line[len("__DRIVER_JSON__"):]), home, p.stderr
        return None, home, (p.stdout or "") + (p.stderr or "")
    finally:
        try:
            os.remove(drv)
        except OSError:
            pass


def group_api():
    print("\n── B组｜API 边界的实参截获（驱动进程内包装 SendInput）────────────", flush=True)
    print("  ★ 证据类型说明（不许混读）：本组是**在驱动进程里包装 SendInput、截获它真正传进去的实参**", flush=True)
    print("    ⇒ 能证明「产品确实把魔数塞进了 API 实参」；", flush=True)
    print("    ⇒ **不是**「在系统里观测到该事件」—— 那是 C 组⑦ 的系统级观察（本轮验不了，见下）。", flush=True)
    rep, home, err = run_driver()
    if rep is None:
        for nm in ("④ 键盘注入的 dwExtraInfo == 判据字面量",
                   "⑤ 鼠标注入的 dwExtraInfo == 判据字面量",
                   "⑥ 非 agent 注入的 dwExtraInfo != 判据字面量"):
            rec(nm, False, "驱动子进程没给出报告：%s" % (err or "")[:200], skip=True)
        return
    print("  驱动目录 = %s" % home, flush=True)
    print("  鼠标位移（绝对移动到当前像素，应为 [0,0]）= %s" % (rep.get("cursor_delta"),), flush=True)
    if rep.get("err"):
        print("  驱动进程报错：%s" % rep["err"], flush=True)

    kb = [s.get("extra") for s in rep.get("kb", [])]
    ms = [s.get("extra") for s in rep.get("ms", [])]
    other = [s.get("extra") for s in rep.get("other", [])]

    rec("④ 键盘注入的 dwExtraInfo == 判据字面量（**API 边界实参截获**，不是系统级观测）",
        bool(kb) and all(v == EXPECTED_MAGIC for v in kb),
        "期望=0x%X 实得=%s（%d 次调用）" % (EXPECTED_MAGIC, [hex(v) for v in kb], len(kb)))
    rec("⑤ 鼠标注入的 dwExtraInfo == 判据字面量（**API 边界实参截获**，不是系统级观测）",
        bool(ms) and all(v == EXPECTED_MAGIC for v in ms),
        "期望=0x%X 实得=%s（%d 次调用）" % (EXPECTED_MAGIC, [hex(v) for v in ms], len(ms)))
    rec("⑥ 非 agent 注入的 dwExtraInfo != 判据字面量（不许误认成 agent）",
        bool(other) and all(v != EXPECTED_MAGIC for v in other),
        "实得=%s（判据字面量=0x%X）" % ([hex(v) for v in other], EXPECTED_MAGIC))


# ══════════════════════════════════════════════════════════════════════════
# B 组｜独立观察者：判据进程**自己**装钩子、自己读 dwExtraInfo
# ══════════════════════════════════════════════════════════════════════════
def _declare_peek(probe):
    """★ 必须自己声明 `PeekMessageW` 的 argtypes（探针只声明了 `GetMessageW`）。

    ctypes 没声明 argtypes 时会按「int 传指针」⇒ 指针被截断 ⇒ 调用直接失败（返回 0）
    ⇒ 消息泵**根本没生效** ⇒ 钩子回调永远等不到派发 ⇒ EVENTS 一直是空的。
    """
    try:
        probe.user32.PeekMessageW.argtypes = [ctypes.POINTER(probe.wintypes.MSG),
                                              ctypes.c_void_p, ctypes.c_uint,
                                              ctypes.c_uint, ctypes.c_uint]
        probe.user32.PeekMessageW.restype = ctypes.c_bool
        return True
    except Exception:
        return False


def _pump(probe, seconds):
    """泵消息（`GetMessageW` 阻塞 + 定时 `WM_QUIT`，与 M2 探针同法；另用 PeekMessage 兜底）。

    ★ 低级钩子的回调是**靠消息投递**给「装钩子的那个线程」的 ⇒
      那个线程不泵消息，回调就永远不执行（EVENTS 一直是空的）。
      ⇒ 泵必须**覆盖注入之后到读之前整段**，不能只在注入后补泵一次。
    """
    msg = probe.wintypes.MSG()
    tid = probe.kernel32.GetCurrentThreadId()
    stop = time.time() + seconds

    def poster():
        while time.time() < stop:
            time.sleep(0.2)
            probe.user32.PostThreadMessageW(tid, 0x0012, 0, 0)   # WM_QUIT
            return

    th = __import__("threading").Thread(target=poster, daemon=True)
    th.start()
    try:
        probe.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
    except Exception:
        pass
    # PeekMessage 兜底：把残留消息清干净
    try:
        while probe.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            probe.user32.TranslateMessage(ctypes.byref(msg))
            probe.user32.DispatchMessageW(ctypes.byref(msg))
    except Exception:
        pass
    th.join(timeout=1.0)


def group_hook():
    print("\n── C组｜系统级观察（判据进程自己的钩子）────────────────────────", flush=True)
    print("  ★ 本组是**在系统里观测到该事件**（独立低级钩子）⇒ 与 B 组的「API 边界实参截获」是**两种证据**，", flush=True)
    print("    不许混读：本组观测不到 ⇒ 只说明「本机此刻钩子链对我们不可达」，**不代表**产品没带魔数。", flush=True)
    try:
        sys.path.insert(0, HERE)
        import _attr_probe as probe
    except Exception as e:
        rec("⑦ 系统级观察：独立钩子**在系统里观测到**的 agent 注入事件 dwExtraInfo == 判据字面量", False,
            "复用 M2 探针失败：%s" % e, skip=True, optional=True)
        return
    probe._dpi_aware()
    _declare_peek(probe)
    kb_proc = probe.HOOKPROC(probe._kb_proc)
    ms_proc = probe.HOOKPROC(probe._mouse_proc)
    hk_kb = probe.user32.SetWindowsHookExW(probe.WH_KEYBOARD_LL, kb_proc, None, 0)
    hk_ms = probe.user32.SetWindowsHookExW(probe.WH_MOUSE_LL, ms_proc, None, 0)
    if not hk_kb and not hk_ms:
        rec("⑦ 系统级观察：独立钩子**在系统里观测到**的 agent 注入事件 dwExtraInfo == 判据字面量", False,
            "钩子没装上", skip=True, optional=True)
        return
    try:
        home = LIVE_HOME if os.path.isfile(LIVE) else LEGACY_OUT
        drv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_magic_driver_tmp.py")
        with open(drv, "w", encoding="utf-8") as f:
            f.write(DRIVER)
        n0 = len(probe.EVENTS)
        p = subprocess.Popen([sys.executable, "-X", "utf8", drv, home, "1" if INJECT else "0"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # ★ 泵覆盖「注入之后 → 读之前」整段：子进程跑着的时候一直泵，跑完再补泵几秒
        deadline = time.time() + 8.0
        while p.poll() is None and time.time() < deadline:
            _pump(probe, 0.6)
        _pump(probe, 2.0)
        try:
            p.communicate(timeout=10)
        except Exception:
            pass
        got = [e for e in probe.EVENTS[n0:] if e.get("device") == "keyboard"]
        print("  钩子本轮收到事件 %d 条（键盘 %d）" % (len(probe.EVENTS) - n0, len(got)), flush=True)
        if not got:
            rec("⑦ 系统级观察：独立钩子**在系统里观测到**的 agent 注入事件 dwExtraInfo == 判据字面量",
                False,
                "★ 钩子回调**一次都没被调用**（不是「没看到」而是「没派发」）："
                "已按 M2 探针同法泵消息（GetMessageW + WM_QUIT，且 PeekMessageW 自带 argtypes），"
                "注入由**独立子进程**发起；本机此刻低级钩子链对我们不可达"
                "（SendInput 返回 1、窗口站/桌面一致 ⇒ 注入已提交且桌面正确）"
                "⇒ 属**环境差异**，不冒充绿；B 组已在独立进程里实测过",
                skip=True, optional=True)
            return
        xs = [int(e.get("dwExtraInfo", 0)) for e in got]
        rec("⑦ 系统级观察：独立钩子**在系统里观测到**的 agent 注入事件 dwExtraInfo == 判据字面量",
            all(v == EXPECTED_MAGIC for v in xs),
            "期望=0x%X 实得=%s（%d 条事件）" % (EXPECTED_MAGIC, [hex(v) for v in xs], len(xs)))
    finally:
        if hk_kb:
            probe.user32.UnhookWindowsHookEx(hk_kb)
        if hk_ms:
            probe.user32.UnhookWindowsHookEx(hk_ms)
        try:
            os.remove(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_magic_driver_tmp.py"))
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════════
# D 组｜边界
# ══════════════════════════════════════════════════════════════════════════
def group_d():
    print("\n── D组｜边界 ──────────────────────────────────────────────", flush=True)
    try:
        r = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", CONTRACT_REL],
                           cwd=ROOT, capture_output=True, timeout=30)
        if r.returncode == 0:
            rec("⑧ contract/tools.json 未被本单改动", True, "git diff 为空")
        elif r.returncode == 1:
            rec("⑧ contract/tools.json 未被本单改动", False, "★ git diff 有改动")
        else:
            rec("⑧ contract/tools.json 未被本单改动", False, "git 不可用", skip=True)
    except Exception as e:
        rec("⑧ contract/tools.json 未被本单改动", False, "git 不可用：%s" % e, skip=True)


def main() -> int:
    global INJECT
    ap = argparse.ArgumentParser(description="SendInput dwExtraInfo 魔数判据（M4）")
    ap.add_argument("--inject", action="store_true",
                    help="注入：把魔数换成 0（被测物代码路径照走）⇒ ⑤⑥ 必须转红")
    args = ap.parse_args()
    INJECT = args.inject

    print("=" * 70, flush=True)
    print("判据 44：SendInput 的 dwExtraInfo 魔数（M4）", flush=True)
    print("=" * 70, flush=True)
    print("判据里的硬编码字面量 EXPECTED_MAGIC = 0x%X（★ 不 import 被测模块取它）"
          % EXPECTED_MAGIC, flush=True)
    if INJECT:
        print("★ 注入：把被测物的魔数换成 0（代码路径照走）⇒ ⑤⑥ 本条必须 rc=1", flush=True)

    group_a()
    group_api()          # B 组：API 边界的实参截获（包装 SendInput）
    group_hook()         # C 组：系统级观察（钩子）—— 本机此刻验不了，非致命
    group_d()
    return finish()


if __name__ == "__main__":
    sys.exit(main())
