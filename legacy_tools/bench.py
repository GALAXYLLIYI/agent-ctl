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

用例（10 项，其中 B9/B10 需要有效通行证，没有就跳过并说明）
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


# ══════════════════ B5–B7：闭环入口（tools/闭环.py） ══════════════════
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
h = win32gui.CreateWindow(wc.lpszClassName, "AgentCtlBenchModal",
                          win32con.WS_OVERLAPPEDWINDOW, 160, 160, 380, 260, 0, 0,
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
h = win32gui.CreateWindow(wc.lpszClassName, "AgentCtlBenchMin",
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
