# -*- coding: utf-8 -*-
r"""bench.py —— 固定回归集（小 Bench）：改完代码跑这一条，挡住「改一个坏一个」

与 selftest.py 的分工
--------------------------------------------------------------------------------
  selftest.py  冒烟：模块能不能导入、CLI 完不完整、闸门在不在 —— 快、粗、不碰状态。
  bench.py     行为：**判定器/闭环/主路**在"该成"和"该败"两种输入下，结论对不对。

为什么非要它（2026-10-12 一晚踩了三次同一个病）
--------------------------------------------------------------------------------
  · `t_manual_footer` 在没设 AGENTCTL_MANUAL_DIR 时**空转通过**（报 ✅ 却一个文件都没查）
  · 3 项检查因缺夹具**静默跳过**，只印「前置条件不足」，看不出缺什么
  · 我自己的负例对照把 sys.path 顺序写反，**加载的是真文件而不是被改坏的副本**，
    "证明"了检查是死的 —— 救我的只有一行 print(act.__file__)
  ⇒ 所以本 Bench 立三条规矩：
    ① **跳过必须说清缺什么、怎么补**（不许只说"前置条件不足"）
    ② **每个"该败"的用例都真的要求它败**（该成/该败成对出现，互相当对方的反例）
    ③ **每个用例都打印它实际用的产物路径**（防止验错了对象）

用例（13 项，其中 B9/B10 需要有效通行证，没有就跳过并说明）
--------------------------------------------------------------------------------
  B1  判定器正例：文件真的变了        ⇒ satisfied(0)
  B2  判定器负例★：声称要变、其实没变  ⇒ unsatisfied(3)      ← 绝不许 0
  B3  判定器记账：--expect 只写在 before ⇒ 账本 expect 非空
  B4  判定器：前后快照路径集不一致     ⇒ 拒绝判定(5) 且不记账
  B5  闭环正例：真变                  ⇒ 0
  B6  闭环负例★：没变                 ⇒ 3
  B7  闭环：动作自身失败不许被掩盖     ⇒ 退出码 7，且输出里两个结果都在
  B8  模态：目标=对话框本身不许被挡    ⇒ 未被挡；且目标进程有真模态时仍必须被挡
  B9  主路★：verify_expect 不满足      ⇒ 信封 ok=False（需通行证）
  B10 主路：不声明验证键               ⇒ 信封里不出现 verdict/verify（需通行证）

  B12 ★_map_rc 表外 rc 不许静默兜底     ⇒ code=5 + unknown_rc（不需通行证）
  B13 ★lint_gate 缺 pyflakes 不许假通过  ⇒ rc=2（不需通行证）

用法
--------------------------------------------------------------------------------
    python bench.py            # 全跑
    python bench.py --keep     # 保留沙箱目录（排查用）

退出码：0 全过 ｜ 1 有失败 ｜ （跳过不计入失败，但会明确列出来）
"""
import ctypes
import io
import json
import os
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
# ★ 两者都能被环境变量指向【别的候选实现】—— 用来做对照实验：
#   喂给它一个"永远说成功"的坏判定器，看 bench 抓不抓得到（抓不到就是死检查）。
# ★ 被测的 agentctl 包位置。AGENTCTL_BENCH_SRC 可覆盖 —— 用来做对照实验：
#   指向一份"被改坏"的包，看 bench 会不会红（不能红就说明这些检查是死的）。
SRC_DIR = os.path.abspath(os.environ.get("AGENTCTL_BENCH_SRC")
                          or os.path.join(HERE, "..", "src"))

def _beside(*names):
    """先找与本脚本【同目录】的，再找上级 tools 子目录的。

    ★ T17 A3-(a)（2026-10-12 用户拍板）：闭环三件改成随 legacy_tools 走，
      于是 bench.py 会被 gen_legacy 放进 legacy_tools 目录 —— 那时判定器就在它【旁边】，
      上级 tools 子目录反而是空的。两种布局都要能自洽，所以按这个顺序找。
    """
    cands = [os.path.join(HERE, *names),
             os.path.join(HERE, "..", "tools", *names)]
    for cand in cands:
        cand = os.path.abspath(cand)
        if os.path.exists(cand):
            return cand
    return os.path.abspath(cands[-1])


VERIFY = os.path.abspath(os.environ.get("AGENTCTL_VERIFY_PY") or _beside("验动作.py"))
LOOP = os.path.abspath(os.environ.get("AGENTCTL_LOOP_PY") or _beside("闭环.py"))

# ★ B13 的被测对象：构建门槛 lint_gate.py（pack_dist.py:242 靠它的 rc 决定发不发包）。
#   路径用 _beside()：真源 tools\ 下没有它；从 legacy_tools 跑时命中 仓根\tools\lint_gate.py。
#   环境变量出口照 VERIFY / LOOP 的既有先例 —— 它是**对照实验**入口：
#   把「改前的 lint_gate」喂进来，B13 必须变红；红不了就说明这条用例是死的。
LINT_GATE = os.path.abspath(os.environ.get("AGENTCTL_LINT_GATE") or _beside("lint_gate.py"))

OK, FAIL, SKIP = [], [], []
BOXES = []


def check(name, fn):
    """契约：True=通过；("skip", 原因)=跳过并说明；其它字符串=失败原因。"""
    try:
        r = fn()
    except Exception as e:
        FAIL.append((name, "异常 %s: %s" % (type(e).__name__, str(e)[:110])))
        return
    if r is True:
        OK.append(name)
    elif isinstance(r, tuple) and len(r) == 2 and r[0] == "skip":
        SKIP.append((name, str(r[1])[:150]))
    else:
        FAIL.append((name, str(r)[:160]))


def sandbox(tag):
    b = tempfile.mkdtemp(prefix="_bench_%s_" % tag)
    BOXES.append(b)
    return b


def run(args, env=None, timeout=300, cwd=None):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, cwd=cwd, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def vrun(box, *args):
    """在指定沙箱里跑判定器（账本与快照都隔离到 box）。"""
    env = {**os.environ, "AGENTCTL_VERIFY_WORK": box, "PYTHONIOENCODING": "utf-8"}
    return run([PY, "-X", "utf8", VERIFY] + list(args), env=env)


def ledger_rows(box):
    f = os.path.join(box, "tier_ledger.jsonl")
    if not os.path.exists(f):
        return []
    return [json.loads(x) for x in io.open(f, encoding="utf-8").read().splitlines() if x.strip()]


def _preflight():
    miss = []
    if not os.path.exists(VERIFY):
        miss.append("判定器 %s" % VERIFY)
    if not os.path.exists(LOOP):
        miss.append("闭环入口 %s" % LOOP)
    return miss


PRE_MISS = _preflight()


# ══════════════════ B1–B4：判定器（验动作.py） ══════════════════
def _mk(box, name="t.txt", body="v0\n"):
    p = os.path.join(box, name)
    io.open(p, "w", encoding="utf-8").write(body)
    return p


def b1_verify_positive():
    box = sandbox("b1")
    f = _mk(box)
    predicate = "file-changed:%s" % f
    vrun(box, "snap", "--tag", "b", "--paths", f, "--expect", predicate)
    io.open(f, "a", encoding="utf-8").write("changed\n")
    vrun(box, "snap", "--tag", "a", "--paths", f)
    rc, out = vrun(box, "check", "--tag", "a", "--before", "b", "--action", "B1", "--tier", "B")
    if rc != 0:
        return "期望 satisfied(0)，实际 %d\n%s" % (rc, out[-400:])
    return True


def b2_verify_negative():
    box = sandbox("b2")
    f = _mk(box)
    predicate = "file-changed:%s" % f
    vrun(box, "snap", "--tag", "b", "--paths", f, "--expect", predicate)
    # ★ 故意什么都不做
    vrun(box, "snap", "--tag", "a", "--paths", f)
    rc, out = vrun(box, "check", "--tag", "a", "--before", "b", "--action", "B2", "--tier", "B")
    if rc != 3:
        return "★ 声称要变、实际没变，期望 unsatisfied(3)，实际 %d —— 判定器不诚实\n%s" % (rc, out[-400:])
    return True


def b3_verify_ledger_expect():
    box = sandbox("b3")
    f = _mk(box)
    predicate = "file-changed:%s" % f
    vrun(box, "snap", "--tag", "b", "--paths", f, "--expect", predicate)
    io.open(f, "a", encoding="utf-8").write("x\n")
    vrun(box, "snap", "--tag", "a", "--paths", f)          # ★ 不带 --expect
    vrun(box, "check", "--tag", "a", "--before", "b", "--action", "B3记账", "--tier", "B")
    rows = [r for r in ledger_rows(box) if r.get("action") == "B3记账"]
    if not rows:
        return "账本里没找到 B3 这条记录"
    got = (rows[-1].get("expect") or "").strip()
    if got != predicate:
        return "账本 expect = %r，期望 %r（判定用了谓词、账本却写空 = 审计轨迹失效）" % (got, predicate)
    return True


def b4_verify_mismatch():
    box = sandbox("b4")
    f1 = _mk(box, "a.txt")
    f2 = _mk(box, "b.txt")
    vrun(box, "snap", "--tag", "b", "--paths", f1)
    vrun(box, "snap", "--tag", "a", "--paths", f2)          # ★ 路径集不同
    n0 = len(ledger_rows(box))
    rc, out = vrun(box, "check", "--tag", "a", "--before", "b", "--action", "B4", "--tier", "B")
    if rc != 5:
        return "前后快照不可比时期望拒绝判定(5)，实际 %d\n%s" % (rc, out[-400:])
    if len(ledger_rows(box)) != n0:
        return "判定无效却仍然记账了（会污染账本）"
    return True


# ══════════════════ B5–B7：闭环入口（与本文件同目录的 闭环.py） ══════════════════
def b5_loop_positive():
    box = sandbox("b5")
    f = _mk(box)
    env = {**os.environ, "AGENTCTL_VERIFY_WORK": box, "PYTHONIOENCODING": "utf-8"}
    rc, out = run([PY, "-X", "utf8", LOOP, "--action", "B5", "--tier", "B",
                   "--paths", f, "--expect", "file-changed:%s" % f,
                   "--", PY, "-c",
                   "open(r'%s','a',encoding='utf-8').write('x')" % f],
                  env=env, cwd=box)
    if rc != 0:
        return "期望 0，实际 %d\n%s" % (rc, out[-400:])
    return True


def b6_loop_negative():
    box = sandbox("b6")
    f = _mk(box)
    env = {**os.environ, "AGENTCTL_VERIFY_WORK": box, "PYTHONIOENCODING": "utf-8"}
    rc, out = run([PY, "-X", "utf8", LOOP, "--action", "B6", "--tier", "B",
                   "--paths", f, "--expect", "file-changed:%s" % f,
                   "--", PY, "-c", "pass"],
                  env=env, cwd=box)
    if rc != 3:
        return "★ 什么都没做，期望 3，实际 %d —— 闭环不诚实\n%s" % (rc, out[-400:])
    return True


def b7_loop_no_mask():
    """动作自身 rc=7 但文件确实变了 ⇒ 退出码 7，且输出里【两个结果都在】。"""
    box = sandbox("b7")
    f = _mk(box)
    env = {**os.environ, "AGENTCTL_VERIFY_WORK": box, "PYTHONIOENCODING": "utf-8"}
    rc, out = run([PY, "-X", "utf8", LOOP, "--action", "B7", "--tier", "B",
                   "--paths", f, "--expect", "file-changed:%s" % f,
                   "--", PY, "-c",
                   "open(r'%s','a',encoding='utf-8').write('y'); raise SystemExit(7)" % f],
                  env=env, cwd=box)
    if rc != 7:
        return "动作自身 7 且判定 satisfied，期望退出码 7（透传动作失败），实际 %d" % rc
    if "动作自身 rc = 7" not in out:
        return "输出里没报出动作自身的 rc=7 ⇒ 被掩盖了"
    if "satisfied" not in out:
        return "输出里没报出判定结论"
    return True


# ══════════════════ B8：模态（modal_blocker） ══════════════════
_MODAL_CHILD = r'''
import sys, io, ctypes, threading
import win32gui, win32con
user32 = ctypes.windll.user32
mode, flag = sys.argv[1], sys.argv[2]

def wndproc(h, msg, w, l):
    if msg == win32con.WM_CLOSE:
        user32.PostQuitMessage(0); return 0
    if msg == win32con.WM_USER + 1:
        threading.Thread(target=lambda: user32.MessageBoxW(h, "bench", "bench", 0),
                         daemon=True).start()
        return 0
    return user32.DefWindowProcW(h, msg, w, l)

wc = win32gui.WNDCLASS()
wc.lpfnWndProc = wndproc
wc.lpszClassName = "AgentCtlBenchModal"
wc.hInstance = win32gui.GetModuleHandle(None)
try:
    win32gui.RegisterClass(wc)
except Exception:
    pass
# ★ 2026-10-14：窗口挪到【屏幕外】建 —— 以前是 (160,160)，于是每跑一次 bench
#   就在用户桌面闪一个 "AgentCtlBenchModal" 窗 + 一个标题/正文都是 bench 的
#   MessageBox（用户已为此被打扰多次）。
#   为什么不是"藏起来/不 ShowWindow"：本条测试要验的正是「**可见的**模态框能否被认出」——
#   modal_blocker 的枚举回调里 `not IsWindowVisible(h)` 就直接跳过 ⇒ 不 Show 等于把判据废掉。
#   ⇒ 挪到 (-32000,-32000)：窗口**仍然可见**（IsWindowVisible 为真），但落在任何显示器之外，
#     用户看不见；MessageBox 以它为属主，也跟着跑到屏外。
#   （与 selftest.py:218-227 同一条处置，照抄体例。）
h = win32gui.CreateWindowEx(0x00000080, wc.lpszClassName, "AgentCtlBenchModal",   # WS_EX_TOOLWINDOW：不进任务栏
                          win32con.WS_OVERLAPPEDWINDOW, -32000, -32000, 380, 260, 0, 0,
                          wc.hInstance, None)
win32gui.ShowWindow(h, win32con.SW_SHOW)
io.open(flag, "w").write(str(h))
if mode == "modal":
    user32.PostMessageW(h, win32con.WM_USER + 1, 0, 0)
win32gui.PumpMessages()
'''


def b8_modal_not_self_blocking():
    """真机复现过的缺陷：目标=对话框本身时不许判「被挡」；且真模态仍必须被挡。"""
    try:
        import win32gui
        import win32process
    except Exception as e:
        return ("skip", "缺 win32 依赖：%s" % e)
    sys.path.insert(0, HERE)
    user32 = ctypes.windll.user32
    box = sandbox("b8")
    child = os.path.join(box, "child.py")
    io.open(child, "w", encoding="utf-8").write(_MODAL_CHILD)
    flag = os.path.join(box, "flag.txt")
    p = subprocess.Popen([PY, child, "modal", flag], cwd=box,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd = 0
    t0 = time.time()
    while time.time() - t0 < 8:
        time.sleep(0.1)
        if os.path.exists(flag):
            try:
                hwnd = int(io.open(flag).read().strip())
            except Exception:
                hwnd = 0
            if hwnd:
                break
    if not hwnd:
        p.kill()
        return ("skip", "自造夹具窗口没起来（环境不允许建窗，本次未验证）")
    time.sleep(0.7)
    got = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _l):
        try:
            if h == hwnd or not user32.IsWindowVisible(h):
                return True
            if (win32gui.GetClassName(h) or "") != "#32770":
                return True
            _t, pid2 = win32process.GetWindowThreadProcessId(h)
            if pid2 == p.pid:
                got.append(h)
        except Exception:
            pass
        return True

    dlg = 0
    for _ in range(25):
        got.clear()
        user32.EnumWindows(cb, 0)
        if got:
            dlg = got[0]
            break
        time.sleep(0.2)
    if not dlg:
        p.kill()
        return ("skip", "夹具的 MessageBox 没找到（本次未验证）")

    import act
    b_modal, _ = act.modal_blocker(hwnd)
    b_self, _ = act.modal_blocker(dlg)
    p.kill()
    if not b_modal:
        return "目标进程有真模态，却判【未被挡】⇒ 安全护栏失效"
    if b_self:
        return "目标=对话框本身被判【被挡】（自己挡自己）⇒ 2026-10-12 修掉的假阳性回归了"
    return True


# ══════════════════ B9–B10：主路（core.py::_act） ══════════════════
def _core_ready():
    """能不能测主路：要有 agentctl 包 + 一张有效 B 级通行证。"""
    if not os.path.isdir(os.path.join(SRC_DIR, "agentctl")):
        return None, "找不到 agentctl 包（%s）" % SRC_DIR
    sys.path.insert(0, SRC_DIR)
    try:
        from agentctl import core, gate
    except Exception as e:
        return None, "import agentctl 失败：%s" % e
    try:
        ok, why, _c = gate.check("B")
    except Exception as e:
        return None, "gate.check 抛异常：%s" % e
    if not ok:
        return None, "此刻没有有效 B 级通行证（%s）—— 先跑 auth_popup 签证再跑这两项" % why[:60]
    # 打桩隔离：只测闭环那一段，不碰全局写租约（租约是有主的，抢它会打断别的 agent）
    core._require_write = lambda actor: None
    return core, None


def _pick_hwnd():
    import win32gui
    got = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _l):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h) or ""
            if len(t.strip()) > 2:
                got.append(h)
                return False
        return True

    ctypes.windll.user32.EnumWindows(cb, 0)
    return got[0] if got else 0


def b9_main_negative():
    core, why = _core_ready()
    if core is None:
        return ("skip", why)
    hwnd = _pick_hwnd()
    if not hwnd:
        return ("skip", "找不到可用的目标窗口")
    missing = os.path.join(tempfile.gettempdir(), "_bench_绝对不存在_%d.txt" % time.time())
    env = core.t_focus({"hwnd": hwnd, "dry_run": True, "actor": "bench",
                        "verify_expect": "file-exists:%s" % missing})
    if env.get("ok"):
        return "★ verify_expect 不满足却报了 ok —— 闭环没接进主路"
    v = env.get("verdict") or ((env.get("data") or {}).get("verdict"))
    if v != "unsatisfied":
        return "期望 verdict=unsatisfied，实际 %r（code=%s）" % (v, env.get("code"))
    return True


def b10_main_no_keys():
    core, why = _core_ready()
    if core is None:
        return ("skip", why)
    hwnd = _pick_hwnd()
    if not hwnd:
        return ("skip", "找不到可用的目标窗口")
    env = core.t_focus({"hwnd": hwnd, "dry_run": True, "actor": "bench"})
    extra = [k for k in ("verdict", "verify") if k in env]
    if extra:
        return "没声明验证键，信封里却出现了 %s ⇒ 对既有调用方不是非破坏性变更" % extra
    return True


# ══════════════════ B11：最小化窗口不许被误读（回执可信度规矩②）══════════════════
_MIN_CHILD = """
import sys, io, ctypes
import win32gui, win32con
user32 = ctypes.windll.user32
flag = sys.argv[1]

def wndproc(h, msg, w, l):
    return user32.DefWindowProcW(h, msg, w, l)

wc = win32gui.WNDCLASS()
wc.lpfnWndProc = wndproc
wc.lpszClassName = "AgentCtlBenchMin"
wc.hInstance = win32gui.GetModuleHandle(None)
try:
    win32gui.RegisterClass(wc)
except Exception:
    pass
h = win32gui.CreateWindowEx(0x00000080, wc.lpszClassName, "AgentCtlBenchMin",     # WS_EX_TOOLWINDOW：不进任务栏
                          win32con.WS_OVERLAPPEDWINDOW, 240, 240, 360, 240, 0, 0,
                          wc.hInstance, None)
win32gui.ShowWindow(h, win32con.SW_SHOW)
io.open(flag, "w").write(str(h))
win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
win32gui.PumpMessages()
"""


def b11_minimized_window_not_misread():
    """规矩②：最小化窗口的 UIA 树会塌 —— 必须被识别成「窗口不可读」，不许当成「元素不存在」。

    A 最小化窗口 + 任意 eid ⇒ 必须回「不可读 / 先把窗口叫醒」
    B 可见窗口  + 假 eid    ⇒ 必须回「元素…不存在」（对照：证明 A 的判据不是把一切都拒了）
    ★ 两条拒绝都在 _act（闸门）之前，所以本项不需要通行证。
    """
    try:
        import win32con          # noqa: F401
        import win32gui          # noqa: F401
    except Exception as e:
        return ("skip", "缺 win32 依赖：%s" % e)
    if not os.path.isdir(os.path.join(SRC_DIR, "agentctl")):
        return ("skip", "找不到 agentctl 包（%s）" % SRC_DIR)
    sys.path.insert(0, SRC_DIR)
    sys.path.insert(0, HERE)
    try:
        from agentctl import core
    except Exception as e:
        return ("skip", "import agentctl 失败：%s" % e)

    def spawn():
        b = sandbox("b11")
        child = os.path.join(b, "child.py")
        io.open(child, "w", encoding="utf-8").write(_MIN_CHILD)
        flag = os.path.join(b, "flag.txt")
        pr = subprocess.Popen([PY, child, flag], cwd=b,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        h = 0
        t0 = time.time()
        while time.time() - t0 < 8:
            time.sleep(0.1)
            if os.path.exists(flag):
                try:
                    h = int(io.open(flag).read().strip())
                except Exception:
                    h = 0
                if h:
                    break
        time.sleep(0.9)          # 等最小化真正生效
        return pr, h

    pr, h = spawn()
    if not h:
        pr.kill()
        return ("skip", "自造窗口没起来（环境不允许建窗，本次未验证）")
    eA = core.t_click({"hwnd": h, "eid": "e1"})
    pr.kill()
    wA = eA.get("warning") or ""
    if eA.get("ok") or not ("不可读" in wA or "叫醒" in wA):
        return ("最小化窗口应被识别成「不可读 / 先叫醒」，实际 ok=%s code=%s warning=%r"
                % (eA.get("ok"), eA.get("code"), wA[:90]))

    pr2, h2 = spawn()
    if not h2:
        pr2.kill()
        return "对照窗口没起来"
    # 对照：把窗口恢复成可见，再用一个假 eid
    try:
        import win32gui as _g
        import win32con as _c
        _g.ShowWindow(h2, _c.SW_RESTORE)
        time.sleep(0.6)
    except Exception:
        pass
    eB = core.t_click({"hwnd": h2, "eid": "zzz_bench_不存在"})
    pr2.kill()
    wB = eB.get("warning") or ""
    if "不可读" in wB or "叫醒" in wB:
        return "可见窗口也被判成「不可读」⇒ 可读性判据过严，warning=%r" % wB[:90]
    if ("不存在" not in wB) and ("重新" not in wB):
        return ("可见窗口 + 假 eid 的响应出乎意料：ok=%s code=%s warning=%r"
                % (eB.get("ok"), eB.get("code"), wB[:90]))
    return True



# ══════════════════ B12：契约码表（core.py::_map_rc）══════════════════
# ★ 被测的是**契约码表**，不是 GUI 行为 —— 不需要通行证，所以不会被跳过。
#   表内 rc 的期望值（0 是早退支；其余取自 core.py:1323-1327 的 m）。
_MAP_RC_TABLE = {0: (True, 0), 3: (False, 3), 4: (False, 4), 5: (False, 5),
                 6: (False, 5), 7: (False, 7), 8: (False, 8), 9: (False, 9),
                 127: (False, 4)}


def b12_map_rc_unknown_rc():
    """★S3-B2 的回归：表外 rc 不许静默兜底成 code=4「找不到控件」。

    A 表外 rc（含 13/15 —— 它们是**信封码**，但不是老脚本 act.py 的 rc）
      ⇒ ok=False + code=5 + data.unknown_rc=True，且**绝不 code=4**
    B 对照：表内 9 个 rc 的结论一个都不许变，且都不许被标 unknown_rc
    C 全表扫描：被判 unknown_rc 的 rc 集合 ==【恰好】不在表内的 rc 集合
      —— 有人往 m 里加键却忘同步早退分支时，这条**当场变红**（core.py 的「第二道保险」）
    """
    if not os.path.isdir(os.path.join(SRC_DIR, "agentctl")):
        return ("skip", "找不到 agentctl 包（%s）" % SRC_DIR)
    sys.path.insert(0, SRC_DIR)
    try:
        from agentctl import core
    except Exception as e:
        return ("skip", "import agentctl 失败：%s" % e)
    print("     · [B12] 被测包 = %s" % core.__file__)      # 规矩③：印实际用的产物路径

    # ── A 表外 rc
    outside = [1, 2, 99, -1, 124, 125, 255, 999, 13, 15]
    for rc in outside:
        env = core._map_rc(rc, "out", "err")
        if env.get("ok") is not False:
            return "rc=%d 应 ok=False，实际 ok=%r ⇒ 表外 rc 被放行" % (rc, env.get("ok"))
        if env.get("code") == 4:
            return "★ rc=%d 被兜底成 code=4「找不到控件」—— 这正是 S3-B2 修掉的病" % rc
        if env.get("code") != core.gate.CODE_UNSTABLE:
            return "rc=%d 应 code=5，实际 code=%r" % (rc, env.get("code"))
        data = env.get("data") or {}
        if data.get("unknown_rc") is not True:
            return "rc=%d 应带 data.unknown_rc=True，实际 %r" % (rc, data.get("unknown_rc"))
        if data.get("rc") != rc:
            return "rc=%d 的 data.rc 应原样保留，实际 %r" % (rc, data.get("rc"))
        if not (data.get("raw") or "").strip():
            return "rc=%d 的 data.raw 为空（现场丢了）" % rc
        w = env.get("warning") or ""
        if ("未知返回码" not in w) or (str(rc) not in w):
            return "rc=%d 的 warning 应说明「未知返回码 %d」，实际 %r" % (rc, rc, w[:110])

    # ── B 对照：表内一条都不许变
    for rc, (exp_ok, exp_code) in _MAP_RC_TABLE.items():
        env = core._map_rc(rc, "out", "err")
        if env.get("ok") is not exp_ok:
            return "对照 rc=%d：ok 应 %r，实际 %r" % (rc, exp_ok, env.get("ok"))
        if env.get("code") != exp_code:
            return "对照 rc=%d：code 应 %d，实际 %r" % (rc, exp_code, env.get("code"))
        if (env.get("data") or {}).get("unknown_rc"):
            return "对照 rc=%d 被误标 unknown_rc（表内 rc 不许当未知）" % rc
    d6 = core._map_rc(6, "out", "err").get("data") or {}
    if d6.get("blocked_by") != "modal_dialog":
        return "rc=6 的 blocked_by=modal_dialog（C4 的成果）丢了：%r" % d6.get("blocked_by")

    # ── C 全表扫描：unknown 集合必须恰好等于表外集合
    scan = list(range(-3, 141)) + [255, 999]
    want = {rc for rc in scan if rc not in _MAP_RC_TABLE}
    got = {rc for rc in scan if (core._map_rc(rc, "out", "err").get("data") or {}).get("unknown_rc")}
    if got != want:
        return ("★ 全表扫描失配：应判未知却漏 %s；不该判未知却判 %s"
                " ⇒ 有人改了 m 却没同步早退分支"
                % (sorted(want - got)[:8], sorted(got - want)[:8]))
    return True


# ══════════════════ B13：构建门槛（tools/lint_gate.py）══════════════════
# ★ 被测对象是**构建门槛**（pack_dist.py 无条件 import 它，rc≠0 就不发包），
#   同样不需要通行证。
def _has_pyflakes(py):
    """与 lint_gate 自己的探针同源：问 import 成败，不看报错文案。"""
    r = subprocess.run([py, "-X", "utf8", "-c", "import pyflakes"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0


def _poison_pyflakes(box):
    """造一个「import pyflakes 必失败」的**临时**环境（只经 env 传给子进程）。"""
    d = os.path.join(box, "poison")
    os.makedirs(d, exist_ok=True)
    io.open(os.path.join(d, "pyflakes.py"), "w", encoding="utf-8").write(
        'raise ImportError("bench B13: 故意让 pyflakes 不可用（仅临时环境，不动本机）")\n')
    return d


def _lint_run(target, poison=None, extra=()):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if poison:
        env["PYTHONPATH"] = poison + os.pathsep + env.get("PYTHONPATH", "")
    return run([PY, "-X", "utf8", LINT_GATE] + list(extra) + [target], env=env)


def b13_lint_gate_no_fake_pass():
    """★S3-B1 的回归：缺 pyflakes 时不许打印「致命项为零 —— 门槛通过」。

    L1 对照（证明门槛既不是一律放行、也不是一律拒绝）
    L2 毒丸★：import pyflakes 必失败 ⇒ 必须 rc=2，且明说「未生效 / 不成立」
    L3 毒丸 + --list-only★：同样不许假装通过（lint_gate.py:120 的承诺）
    """
    if not os.path.exists(LINT_GATE):
        return ("skip", "找不到被测门槛 %s（从 legacy_tools 跑时应命中 仓根\\tools\\lint_gate.py）"
                % LINT_GATE)
    box = sandbox("b13")
    ok_py = _mk(box, "ok.py", "x = 1\nprint(x)\n")
    bad_py = _mk(box, "bad.py", "def f():\n    return bench_undefined_name\n")
    print("     · [B13] 被测门槛 = %s｜解释器 = %s｜夹具 = %s" % (LINT_GATE, PY, box))

    have = _has_pyflakes(PY)
    # ── L1 对照
    if have:
        rc, out = _lint_run(ok_py)
        if rc != 0:
            return "L1 干净夹具应 rc=0，实际 %d\n%s" % (rc, out[-400:])
        if "✔ 致命项为零" not in out:
            return "L1 干净夹具没有「致命项为零」的结论\n%s" % out[-400:]
        rc2, out2 = _lint_run(bad_py)
        if rc2 != 1:
            return "L1 致命夹具应 rc=1，实际 %d\n%s" % (rc2, out2[-400:])
        if ("致命" not in out2) or ("undefined name" not in out2):
            return "L1 致命夹具没报出「致命」/「undefined name」\n%s" % out2[-400:]
    else:
        print("     · [B13] L1 跳过：解释器 %s 没装 pyflakes"
              "（装法：python -m pip install pyflakes）" % PY)

    # ── L2 毒丸 ★
    poison = _poison_pyflakes(box)
    rc, out = _lint_run(ok_py, poison=poison)
    if rc != 2:
        return ("★ L2 毒丸下应 rc=2（门槛未生效），实际 %d —— 缺 pyflakes 却敢下结论\n%s"
                % (rc, out[-400:]))
    if ("未生效" not in out) or ("不成立" not in out):
        return "L2 毒丸下 rc=2 了，但没说清「未生效 / 本次判定不成立」\n%s" % out[-400:]
    if "✔ 致命项为零" in out:
        return "★ L2 毒丸下仍印出「致命项为零/门槛通过」—— 假通过复发\n%s" % out[-400:]

    # ── L3 毒丸 + --list-only ★
    rc, out = _lint_run(ok_py, poison=poison, extra=("--list-only",))
    if rc != 2:
        return ("★ L3 毒丸 + --list-only 应 rc=2（lint_gate.py:120 承诺它同样不许假装通过），"
                "实际 %d\n%s" % (rc, out[-400:]))
    return True
CASES = [
    ("B1  判定器正例（真变⇒0）", b1_verify_positive),
    ("B2  ★判定器负例（没变⇒3）", b2_verify_negative),
    ("B3  判定器记账 expect 非空", b3_verify_ledger_expect),
    ("B4  快照不可比⇒拒绝判定5且不记账", b4_verify_mismatch),
    ("B5  闭环正例（真变⇒0）", b5_loop_positive),
    ("B6  ★闭环负例（没变⇒3）", b6_loop_negative),
    ("B7  闭环不掩盖动作自身失败", b7_loop_no_mask),
    ("B8  模态不自己挡自己", b8_modal_not_self_blocking),
    ("B9  ★主路 verify_expect 不满足", b9_main_negative),
    ("B10 主路不声明验证键不加字段", b10_main_no_keys),
    ("B11 最小化窗口不许被误读", b11_minimized_window_not_misread),
    ("B12 ★_map_rc 表外 rc 不许静默兜底", b12_map_rc_unknown_rc),
    ("B13 ★lint_gate 缺 pyflakes 不许假通过", b13_lint_gate_no_fake_pass),
]


def main():
    keep = "--keep" in sys.argv
    print("=" * 78)
    print("  固定回归集 · bench.py")
    print("=" * 78)
    print("  判定器 = %s" % VERIFY)
    print("  闭环   = %s" % LOOP)
    print("  用例   = %d 项" % len(CASES))
    print()
    if PRE_MISS:
        print("  ★ 缺件：%s" % "；".join(PRE_MISS))
        print("    （B1–B7 会失败；这不是测试坏了，是装得不全）")
        print()
    for name, fn in CASES:
        check(name, fn)
    for n in OK:
        print("  ✅ %s" % n)
    for n, why in SKIP:
        print("  ○  %s  （跳过：%s）" % (n, why))
    for n, why in FAIL:
        print("  ❌ %s\n       %s" % (n, why.replace("\n", "\n       ")))
    print("-" * 78)
    print("  通过 %d ｜ 跳过 %d ｜ 失败 %d ｜ 共 %d 项"
          % (len(OK), len(SKIP), len(FAIL), len(CASES)))
    if SKIP:
        print("  ⚠ 跳过 %d 项 = 这些行为【本次没有验证过】，不等于通过。" % len(SKIP))
        print("    补齐办法见上面每项括号里的原因。")
    if not keep:
        import shutil
        for b in BOXES:
            shutil.rmtree(b, ignore_errors=True)
    else:
        print("  --keep：沙箱保留在 %s" % ", ".join(BOXES))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
