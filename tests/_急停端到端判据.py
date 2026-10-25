# -*- coding: utf-8 -*-
r"""窗S 判据：**真急停 ⇒ 一个真实动作真的被拒**（端到端，不是看外观）

================================ 本单要什么 ================================
到 2026-10-16 为止，本项目所有「急停证据」验的都是**急停的外观**
（弹窗出来了？租约吊销了？状态显示已停？），**没有一次**验过
「真旗子 ⇒ 一个真实的鼠标动作真的没发出去」。这是整个项目最核心的安全属性。

本判据只钉这一条，且必须**同时**满足两半才算数：
    实验组：真旗子拉下 ⇒ 通过**动作出口**（core.dispatch，不是 gate.* 直调）
            发起的一次真实 B 级单击 ⇒ **被拒**（code=11 / CODE_BRAKE），
            且**像素证据**显示指针位置与目标窗内容**没有变化**；
    对照组：**不拉**旗子 ⇒ 同一个动作 ⇒ **必须成功**，且像素证据显示内容**变了**。
★ 对照组是**硬的一半**：没有它，「被拒」可能只是「这个动作本来就发不出去」
  （没证、没租约、窗口不对、坐标错）—— 那证明不了急停拦住了什么。
  对照组失败 ⇒ 本判据必须记「**验不了**」（rc=2），**不许**把失败说成通过。

================================ 跑法 ==========================================
    python -X utf8 tests\_急停端到端判据.py
    rc=0 全绿 ｜ rc=1 有红条 ｜ rc=2 **验不了**（前置不具备，见输出）

★ 必须在**能碰到真机状态**的进程里跑（本判据**故意**不隔离，见下）。
  在 窗口D 的 "-s workspace-write" 沙箱里跑会**直接 rc=2 验不了** —— 沙箱只允许
  写工作区，而真旗子在 %LOCALAPPDATA%\AgentCtl、通行证与老线驱动都在工作区之外。
  2026-10-16 窗S 实测（沙箱内）：真旗子路径 PermissionError(13)、通行证读不到、
  GetCursorPos WinError 5、桌面 BitBlt 截屏失败、tasklist Access denied。

================================ 与其它判据的反差（刻意）==========================
tests\_P2_动作面刹车判据.py 等判据都把 AGENTCTL_APP_DIR / AGENTCTL_BRAKE_FLAG /
AGENTCTL_LEGACY_HOME 指到临时目录，跑完即删 —— 那是**对的**，它们不该碰用户真机。
本判据**反过来：绝不设这三个环境变量**，且启动时若发现它们已被设置 ⇒ 直接 rc=2。
理由：本单的全部价值就在「真路径」上；一落到夹具，验的就不是产品。

================================ 红证据 ========================================
同一份代码里跑第三趟：**跳过 set_brake 那一步**、跑同一个断言函数
（assert_experiment_denied(engage_brake=False)）。
    · 若它返回 True（=「不拉闸也拒了」）⇒ 断言空转，本判据自己作废；
    · 期望它返回 **False**，且那次动作 ok=True ⇒ 证明这条断言真在测「急停」。

================================ 硬边界（不可协商）==============================
    ⛔ 动作目标**只有本脚本自己起的那个窗**（一个 BS_CHECKBOX 复选框）：
      点中它 ⇒ 打勾 ⇒ 像素变化肉眼可见、BM_GETCHECK 机器可读 ⇒ 对照组有真变化可证。
    ⛔ 只做 B 级单击。**不许打字（C）、不许拖拽（D）**、不许碰任何别的窗口。
    ⛔ finally 必解除真旗子，并复查：旗子不存在 / 弹窗 0 / daemon 0。
    ⛔ 全程 ≤ 60 秒。

★ 状态：**本判据在窗S 执行时未能运行**（当时跑在沙箱里，真路径不可达，见回执 §1）。
  每一条都按真链路写，但**一次都没在真机状态下跑过** ⇒
  在被人从工作区之外实跑并全绿**之前**，不要把它登记进 tests\run_all.py。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

ACTOR = "wS-急停端到端判据"
T0 = time.time()
BUDGET_SEC = 60.0
OK: list = []
BAD: list = []
UNVERIFIABLE: list = []
EVID: dict = {}


# ───────────────────────── 打印小工具 ─────────────────────────
def section(t):
    print("\n── %s %s" % (t, "─" * max(2, 56 - len(t))), flush=True)


def attempt(title, fn):
    try:
        good, why = fn()
    except Exception as e:                                  # noqa: BLE001
        good, why = False, "判据自身抛异常 %s: %s" % (type(e).__name__, e)
    if good:
        OK.append(title)
        print("  【通】%s —— %s" % (title, why), flush=True)
    else:
        BAD.append("%s（%s）" % (title, why))
        print("  【红】%s —— %s" % (title, why), flush=True)
    return good


def unverifiable(title, why):
    UNVERIFIABLE.append("%s（%s）" % (title, why))
    print("  【验不了】%s —— %s" % (title, why), flush=True)


def over_budget():
    return time.time() - T0 > BUDGET_SEC


# ───────────────────────── Win32 小工具 ─────────────────────────
u32 = ctypes.windll.user32
u32.CreateWindowExW.restype = wt.HWND
u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
u32.SendMessageW.restype = ctypes.c_ssize_t
u32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]

WS_CHILD, WS_VISIBLE = 0x40000000, 0x10000000
WS_OVERLAPPEDWINDOW = 0x00CF0000
BS_CHECKBOX = 0x00000002
BM_GETCHECK, BM_SETCHECK = 0x00F0, 0x00F1
BST_CHECKED, BST_UNCHECKED = 1, 0


def dpi_aware():
    """物理像素口径（与 core._win_rect 走 coord.py 的口径一致）。"""
    try:
        u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def pump(ms=120):
    """转一会儿消息循环，让窗真渲染出来（不转 = 抓到的是空白）。"""
    end = time.time() + ms / 1000.0
    m = wt.MSG()
    while time.time() < end:
        while u32.PeekMessageW(ctypes.byref(m), None, 0, 0, 1):
            u32.TranslateMessage(ctypes.byref(m))
            u32.DispatchMessageW(ctypes.byref(m))
        time.sleep(0.01)


def make_test_window(title="窗S_自起测试靶"):
    """起靶窗 —— ★ 必须是**非 Python 宿主的普通桌面程序**（本地 agent 2026-10-17 三改）。

    为什么：自指保护（窗K/P0）**按属主进程名硬拒 `python.exe`**（授权/急停弹窗都是
    python 起的，见 act.py 的 BLOCKED_PROCESSES_HARD）。任何 python 拥有的窗**永远点不到**
    —— 那不是缺陷，是设计。⇒ 靶窗由 `tests\_wS_靶标进程.py` 现场编的
    `_wS_target.exe`（WinForms）拥有；"靶被打勾"由**状态文件**判定。
    """
    import subprocess
    import tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    host = os.path.join(here, "_wS_靶标进程.py")
    if not os.path.isfile(host):
        raise RuntimeError("靶窗宿主不在：%s" % host)
    state = os.path.join(tempfile.mkdtemp(prefix="agentctl_wS_target_"), "state.txt")
    proc = subprocess.Popen([sys.executable, "-X", "utf8", host, state],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace")
    top = 0
    t0 = time.time()
    while time.time() - t0 < 20:
        line = (proc.stdout.readline() or "").strip()
        if line.startswith("{"):
            break
        if proc.poll() is not None:
            break
    # 等窗体真正出现（FindWindow 按时标题找）
    import win32gui
    while time.time() - t0 < 20:
        top = win32gui.FindWindow(None, title)
        if top:
            break
        time.sleep(0.1)
    if not top:
        try:
            proc.terminate()
        except Exception:
            pass
        raise RuntimeError("靶窗没出来（FindWindow 找不到标题 %r）" % title)
    pump(300)
    return {"top": int(top), "state": state, "proc": proc, "title": title}


def destroy_test_window(w):
    p = (w or {}).get("proc")
    # ★ 先按 pid 杀**靶 exe 本体**（它是启动器的孙进程；只 terminate 启动器会留下窗）
    try:
        import os as _os
        pidf = _os.path.splitext(w.get("state", ""))[0] + ".exe.pid"
        if _os.path.isfile(pidf):
            pid = int(open(pidf, encoding="utf-8").read().strip() or 0)
            if pid:
                import subprocess as _sp
                _sp.run(["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=10)
    except Exception:
        pass
    if p is not None:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        return
    try:
        u32.DestroyWindow(w["top"])
    except Exception:
        pass


def btn_state(w):
    """靶状态：读**状态文件**（WinForms 靶点中会写 "1"）。BST_CHECKED 仍是 1，断言不用改。"""
    try:
        with open(w["state"], encoding="utf-8") as f:
            return int((f.read() or "0").strip() or 0)
    except Exception:
        return 0


def btn_reset(w):
    """把靶复位成未勾选（写文件即可 —— 这是夹具复位，不是被测对象）。"""
    try:
        with open(w["state"], "w", encoding="utf-8") as f:
            f.write("0")
    except Exception:
        pass
    pump(120)


def btn_screen_center(w):
    """复选框中心 ⇒ 屏幕坐标。

    ★ 2026-10-17：窗口有 **DPI 缩放**（380x200 的窗实测 570x300）⇒ 布局里的
    Left=30/Top=45/W=280/H=30 是 DIP，客户区像素要用 scale 换算，否则点偏
    （实测就是"点到了窗但没打勾"）。scale 用「客户区宽 / 380」现算。
    """
    import win32gui
    l_, t_, r_, b_ = win32gui.GetClientRect(int(w["top"]))
    cw = max(1, r_ - l_)
    scale = cw / 380.0
    x = int((30 + 140) * scale)
    y = int((45 + 15) * scale)
    sx, sy = win32gui.ClientToScreen(int(w["top"]), (x, y))
    return [int(sx), int(sy)]


def cursor_pos():
    p = wt.POINT()
    if not u32.GetCursorPos(ctypes.byref(p)):
        return None
    return (int(p.x), int(p.y))


def capture_hwnd(hwnd):
    """PrintWindow 抓**我们自己那个窗**（不走桌面 BitBlt：后者在会话受限时失败）。"""
    import win32gui
    import win32ui
    l, t, r, b = win32gui.GetWindowRect(int(hwnd))
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None
    hdc = win32gui.GetWindowDC(int(hwnd))
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    bits = None
    for flag in (2, 0):
        try:
            if u32.PrintWindow(int(hwnd), mem.GetSafeHdc(), flag):
                bits = bytes(bmp.GetBitmapBits(True))
                break
        except Exception:
            continue
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(int(hwnd), hdc)
    # ★ 2026-10-17 修：PyCBitmap 没有 DeleteObject()（窗S 这条也没跑过）——
    #   pywin32 的规矩是拿句柄交给 win32gui.DeleteObject，否则 GDI 句柄泄漏。
    win32gui.DeleteObject(bmp.GetHandle())
    if bits is None:
        return None
    return bits, w, h


def pixel_diff(a, b):
    """两帧之间**变了多少个像素**（逐像素 4 通道全等才算同）。尺寸不同 ⇒ None。"""
    if a is None or b is None:
        return None
    ba, w, h = a
    bb, w2, h2 = b
    if (w, h) != (w2, h2) or len(ba) != len(bb):
        return None
    n = 0
    for i in range(0, len(ba) - 3, 4):
        if ba[i:i + 4] != bb[i:i + 4]:
            n += 1
    return n


def visible_windows():
    """当前可见窗快照（标题/类/矩形），判断「桌面安不安静」「有没有多出弹窗」。"""
    import win32gui
    out = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h):
                t = (win32gui.GetWindowText(h) or "").strip()
                if t:
                    out.append((int(h), t, win32gui.GetClassName(h),
                                tuple(win32gui.GetWindowRect(h))))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return sorted(out)


def daemons():
    """本项目自己起的常驻进程（daemon / popup）。收尾复查用。"""
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:
            continue
        if ("brake_daemon" in cl or "brake_popup" in cl
                or "panel_main" in cl or "agentctl_main" in cl):
            out.append((int(p.info["pid"]), cl[:120]))
    return out


# ───────────────────────── 真链路：闸门 / 动作出口 ─────────────────────────
def _gate():
    from agentctl import gate
    return gate


def _core():
    from agentctl import core
    return core


def fire_click(hwnd, xy):
    """**走动作出口**发一次真实 B 级单击（不是 gate.check、不是 mock）。

    顺序照抄现实用法：先 ctl_shot 拿 capture_id（观测-动作绑定），再点。
    坐标动作必须绑观测，否则 core._bind_capture 会以 code=12 拒 ——
    那种拒与急停无关，会把本判据的结论带偏，所以这里老老实实先观测。
    """
    core = _core()
    u32.SetForegroundWindow(int(hwnd))
    pump(150)
    sh = core.dispatch("ctl_shot", {"hwnd": int(hwnd), "actor": ACTOR})
    tok = (sh.get("data") or {}).get("capture_id")
    if not tok:
        return {"stage": "ctl_shot", "ok": False, "code": sh.get("code"),
                "warning": sh.get("warning"), "data": sh.get("data")}
    return core.dispatch("ctl_click", {"actor": ACTOR, "hwnd": int(hwnd),
                                      "xy": list(xy), "from_capture": tok})


def assert_experiment_denied(engage_brake):
    r"""★ 本判据**唯一**那条断言（实验组与红证据趟共用它）。

    engage_brake=True  ⇒ 必须「被拒 + 像素零变化 + 指针没动 + 靶没被打勾」。
    engage_brake=False ⇒ **必须不成立**（不拉闸就该放行）—— 这就是红证据。
    """
    gate = _gate()
    W = make_test_window()
    ev = {"engage_brake": engage_brake}
    try:
        btn_reset(W)
        xy = btn_screen_center(W)
        ev["xy"] = xy
        ev["flag_前"] = bool(os.path.exists(gate.BRAKE_FLAG))

        if engage_brake:
            ok, msg = gate.set_brake("窗S 判据：真急停端到端验证（%s）"
                                     % time.strftime("%H:%M:%S"))
            ev["set_brake"] = [bool(ok), msg]
            # ★ 拉闸返回 True **不够** —— 必须真去问旗子在不在（存在即停）：
            #   「它说自己写了」不算证据，「文件在那儿」才算。
            ev["flag_后"] = bool(os.path.exists(gate.BRAKE_FLAG))
            ev["flag_路径"] = gate.BRAKE_FLAG
            if not ev["flag_后"]:
                ev["verdict"] = "拉闸没落盘 ⇒ 后面全是空谈"
                return False, ev
        else:
            ev["set_brake"] = [None, "对照组/红证据趟：刻意不拉闸"]
            ev["flag_后"] = bool(os.path.exists(gate.BRAKE_FLAG))

        u32.SetForegroundWindow(W["top"])
        pump(200)
        before = capture_hwnd(W["top"])
        cur_before = cursor_pos()
        st_before = btn_state(W)
        ev["前"] = {"像素": (len(before[0]) if before else 0),
                    "指针": list(cur_before) if cur_before else None,
                    "靶状态": st_before}

        r = fire_click(W["top"], xy)
        pump(350)
        after = capture_hwnd(W["top"])
        cur_after = cursor_pos()
        st_after = btn_state(W)
        ev["动作"] = {"ok": bool(r.get("ok")), "code": r.get("code"),
                      "warning": r.get("warning"),
                      "data_raw": ((r.get("data") or {}).get("raw") or "")[:400]}
        ev["后"] = {"像素": (len(after[0]) if after else 0),
                    "指针": list(cur_after) if cur_after else None,
                    "靶状态": st_after}
        ev["像素差"] = pixel_diff(before, after)
        ev["拒绝理由原文"] = r.get("warning") or ""

        denied = (r.get("ok") is not True) and (int(r.get("code") or 0) == gate.CODE_BRAKE)
        ev["被拒"] = bool(denied)

        # 断言本体：拒了 **且** 画面没变 **且** 指针没动 **且** 靶没被打勾
        good = bool(denied) and ev["像素差"] == 0 and cur_before == cur_after \
            and st_before == st_after == BST_UNCHECKED
        ev["断言成立"] = bool(good)
        return bool(good), ev
    finally:
        btn_reset(W)
        destroy_test_window(W)


# ───────────────────────── 前置（fail-closed）─────────────────────────
def preflight():
    """前置不具备 ⇒ 整单记「验不了」，**一条都不许算绿**。"""
    good = True

    for k in ("AGENTCTL_BRAKE_FLAG", "AGENTCTL_APP_DIR", "AGENTCTL_LEGACY_HOME"):
        if os.environ.get(k):
            unverifiable("0·真路径", "%s=%s 已设置 —— 这是夹具/沙箱口径，本判据只认真路径"
                                    % (k, os.environ[k]))
            good = False

    gate = _gate()
    real = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "AgentCtl", "_急停.flag")
    if os.path.normcase(gate.BRAKE_FLAG) != os.path.normcase(real):
        unverifiable("0·真路径", "gate.BRAKE_FLAG=%s ≠ 生产路径 %s"
                                 % (gate.BRAKE_FLAG, real))
        good = False

    d = os.path.dirname(gate.BRAKE_FLAG)
    probe = os.path.join(d, "_wS_判据写探针.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("probe")
        os.remove(probe)
    except Exception as e:
        unverifiable("0·真旗子可写", "%s: %s（真旗子落不下去 ⇒ set_brake 是空话）"
                                    % (type(e).__name__, e))
        good = False

    if not gate.HOME:
        unverifiable("0·老线驱动", "gate.HOME=None —— 真点击发不出去；被刷掉的候选：%s"
                                    % "；".join((r.get("why") or "") for r
                                                in gate.HOME_REJECTED)[:200])
        good = False
    try:
        gate.require_home()
    except Exception as e:
        unverifiable("0·老线驱动", "%s: %s" % (type(e).__name__, e))
        good = False

    try:
        ok, why, code = gate.check("B")
        EVID["通行证"] = {"ok": ok, "why": why, "code": code}
        if not ok:
            unverifiable("0·通行证", "gate.check(B) 不通过：%s（code=%s）—— "
                                      "没有有效通行证，B 级动作压根发不出去" % (why, code))
            good = False
    except Exception as e:
        unverifiable("0·通行证", "%s: %s" % (type(e).__name__, e))
        good = False

    try:
        if cursor_pos() is None:
            raise RuntimeError("GetCursorPos 拿不到位置")
    except Exception as e:
        unverifiable("0·指针可读", "%s: %s（像素证据的一半读不出来）"
                                    % (type(e).__name__, e))
        good = False

    # 桌面安静：连着两次快照（间隔 2s）必须完全一致
    try:
        s1 = visible_windows()
        time.sleep(2.0)
        s2 = visible_windows()
        EVID["桌面快照"] = {"第一次": s1, "第二次": s2}
        if s1 != s2:
            unverifiable("0·桌面安静", "2 秒内可见窗集合变了 ⇒ 有别的窗在动，本单不作数"
                                       "（今晚两次「飘」就是这个根因）")
            good = False
        EVID["开跑前_daemon"] = daemons()
    except Exception as e:
        unverifiable("0·桌面安静", "%s: %s" % (type(e).__name__, e))
        good = False
    return good


# ───────────────────────── 主流程 ─────────────────────────
def main():
    dpi_aware()
    section("0 · 前置（不具备就『验不了』，不许绕）")
    if not preflight():
        print("\n  ★ 前置不具备 ⇒ 本单【验不了】，按派单要求停手，"
              "不许拿夹具顶替真路径。", flush=True)
        return 2

    gate = _gate()
    core = _core()

    # 租约：B 级写动作的前置之一（与真环境一致，不许 AGENTCTL_REQUIRE_LEASE=0）
    core.dispatch("ctl_lease", {"action": "acquire", "actor": ACTOR,
                                "scopes": ["B"], "ttl_sec": 120})

    section("1 · 实验组：拉真旗子 ⇒ 同一次点击必须被拒")
    EVID["拉闸前_flag实存"] = bool(os.path.exists(gate.BRAKE_FLAG))
    exp_ok, exp = assert_experiment_denied(True)
    EVID["实验组"] = exp
    EVID["拉闸时_flag实存"] = exp.get("flag_后")
    print("    拒绝理由原文：%s" % (exp.get("拒绝理由原文") or "（无）"), flush=True)
    print("    像素差=%s 指针前=%s 指针后=%s 靶状态 %s→%s"
          % (exp.get("像素差"), (exp.get("前") or {}).get("指针"),
             (exp.get("后") or {}).get("指针"),
             (exp.get("前") or {}).get("靶状态"),
             (exp.get("后") or {}).get("靶状态")), flush=True)
    # ★ 立刻放旗子：不管成不成，都不许把用户真机留在「已停」
    gate.clear_brake()
    attempt("1·实验组：真旗子下动作被拒，且画面/指针/靶都没动",
            lambda: (exp_ok, "code=%s 像素差=%s"
                     % ((exp.get("动作") or {}).get("code"), exp.get("像素差"))))
    attempt("1·实验组：拒绝理由原文点名了急停",
            lambda: (int((exp.get("动作") or {}).get("code") or 0) == gate.CODE_BRAKE
                     and "急停" in (exp.get("拒绝理由原文") or ""),
                     (exp.get("拒绝理由原文") or "")[:120]))

    if over_budget():
        unverifiable("1·实验组", "已超 %ds 预算，判据到此为止" % BUDGET_SEC)
        return 2

    section("2 · 对照组：**不拉**旗子 ⇒ 同一个动作必须成功"
            "（少了这半，前面的拒说明不了任何事）")
    # ★ 2026-10-17（本地 agent 补）：实验组拉闸会**联动吊销租约**（by=brake），所以对照组
    #   必须先重新拿一张租约 —— 否则"对照组失败"其实是"租约没了"，跟急停一点关系都没有。
    #   （第 1 次真跑就红在这：对照组 code=10 租约已被吊销 by=brake。）
    _lr = core.dispatch("ctl_lease", {"action": "acquire", "actor": ACTOR,
                                      "scopes": ["B"], "ttl_sec": 120})
    print("    （对照组前重新申请租约：%s）"
          % ("ok" if (_lr or {}).get("ok") else ((_lr or {}).get("warning") or "")[:160]),
          flush=True)
    EVID["对照组前租约"] = _lr
    ctl_ok, ctl = assert_experiment_denied(False)
    EVID["对照组"] = ctl
    print("    对照组 ok=%s code=%s 像素差=%s 靶状态 %s→%s"
          % ((ctl.get("动作") or {}).get("ok"), (ctl.get("动作") or {}).get("code"),
             ctl.get("像素差"), (ctl.get("前") or {}).get("靶状态"),
             (ctl.get("后") or {}).get("靶状态")), flush=True)
    attempt("2·对照组：不拉急停 ⇒ 同一个动作**成功**",
            lambda: (bool((ctl.get("动作") or {}).get("ok")),
                     (ctl.get("动作") or {}).get("warning") or ""))
    attempt("2·对照组：成功是真的（画面变了 + 靶被打勾）",
            lambda: (isinstance(ctl.get("像素差"), int) and ctl["像素差"] > 0
                     and (ctl.get("后") or {}).get("靶状态") == BST_CHECKED,
                     "像素差=%s 靶=%s" % (ctl.get("像素差"),
                                       (ctl.get("后") or {}).get("靶状态"))))

    section("3 · 红证据：去掉 set_brake 那步 ⇒ 同一断言必不成立")
    red_ok, red = assert_experiment_denied(False)
    EVID["红证据"] = red
    attempt("3·红证据：不拉闸时断言【不成立】（动作成功了）⇒ 这条断言真在测急停",
            lambda: ((not red_ok) and bool((red.get("动作") or {}).get("ok")),
                     "断言=%s 而动作 ok=%s"
                     % (red_ok, (red.get("动作") or {}).get("ok"))))

    section("4 · 收尾复查")
    gate.clear_brake()
    attempt("4·真旗子已解除（文件不存在）",
            lambda: (not os.path.exists(gate.BRAKE_FLAG), gate.BRAKE_FLAG))
    try:
        dmn = daemons()
        pop = [w for w in visible_windows() if "急停" in w[1] or "窗S" in w[1]]
        EVID["收尾_daemon"] = dmn
        EVID["收尾_相关窗口"] = pop
    except Exception as e:
        dmn, pop = ["查不了: %s" % e], []
    attempt("4·零 daemon / 零本单弹窗",
            lambda: (not dmn and not pop, "daemon=%s 弹窗=%s" % (dmn, pop)))

    try:
        with open(os.path.join(REPO, "work", "_wS_判据_证据.json"), "w",
                  encoding="utf-8") as f:
            json.dump(EVID, f, ensure_ascii=False, indent=1, default=str)
    except Exception as e:
        print("  （证据落盘失败：%s）" % e, flush=True)
    return 0


if __name__ == "__main__":
    _done = False
    _rc = 2
    try:
        _rc = main()
        _done = True
    except BaseException as e:                                  # noqa: BLE001
        print("  ★ 判据自身崩了（fail-closed）：%s: %s" % (type(e).__name__, e),
              flush=True)
    finally:
        # ★ 兜底放旗子：判据崩了也不能把用户真机留在「已停」状态
        try:
            from agentctl import gate as _g
            if os.path.exists(_g.BRAKE_FLAG):
                _g.clear_brake()
                print("  （finally：真旗子已兜底解除）", flush=True)
        except Exception:
            pass
        section("汇总")
        print("  绿 %d / 红 %d / 验不了 %d ｜ 用时 %.1fs"
              % (len(OK), len(BAD), len(UNVERIFIABLE), time.time() - T0), flush=True)
        for b in BAD:
            print("     - 红：%s" % b)
        for u in UNVERIFIABLE:
            print("     - 验不了：%s" % u)
        if not _done:
            print("  结论：【验不了】判据没跑完 —— 一条都不算绿", flush=True)
            sys.exit(2)
        if _rc == 2 or UNVERIFIABLE:
            print("  结论：【验不了】前置不具备或中途放弃 —— **不许**当成通过",
                  flush=True)
            sys.exit(2)
        if BAD:
            print("  结论：【红】%d 条不成立" % len(BAD), flush=True)
            sys.exit(1)
        print("  结论：【绿】真旗子 ⇒ 动作真被拒（有像素+对照组+红证据），且已收尾复原",
              flush=True)
        sys.exit(0)

