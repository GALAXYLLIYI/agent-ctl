# -*- coding: utf-8 -*-
r"""tests\_ctrl_border_孤儿判据.py —— 「父进程死后边框自己收摊」的判据（窗Y）

跑法（在仓库根下）：
    python -X utf8 tests\_ctrl_border_孤儿判据.py              # 绿：跑全部用例
    python -X utf8 tests\_ctrl_border_孤儿判据.py --red-copy   # 跑红自证

被测的产品代码：src\agentctl\ctrl_border.py
    --follow 原来只拿 OpenProcess(SYNCHRONIZE) 盯**父的 pid**。pid 会被系统回收
    再利用 ⇒ 父死之后 pid 落到别的活进程头上时，边框会永远盯着一台**跟自己无关**
    的进程，屏幕上留下 8 个 AgentCtlCtrlBorder* 窗口（2026-10-17 pid 16400 现场）。

用例（每条都断言「≤10 s 内退出」+「屏幕上边框窗 = 0」，两个都要）：
    A  内核腿·Job：中间父走 start_follow()（边框被塞进 KILL_ON_JOB_CLOSE 的 Job），
       且 --parent-pid 指向一个**别的活进程**（= pid 复用的终局状态），
       硬杀中间父 ⇒ 只可能靠内核 Job 收摊。
    B  pid 腿（**没有** Job）：命令行直起 --follow，硬杀中间父
       ⇒ 靠句柄 + 创建时间收摊（不许把「能退出」做成「全靠 Job」）。
    C1 优雅路径：中间父调 stop_follow() 再正常退出 ⇒ 边框收摊。
    C2 终止信号：测试对中间父发 terminate（Windows 上 SIGTERM 的等价物）
       ⇒ 边框收摊（不许把「能退出」做成「只在强杀时退出」）。
    D  创建时间腿：给的 --parent-create-time **对不上** ⇒ 认亲失败、收摊、留日志；
       给**对上** ⇒ 必须继续活着（否则就成了「一律自杀」，那是另一种坏）。
    E  fail-closed：--parent-pid 指向一个**已死且已回收**的 pid ⇒ 必须收摊。
       （老实现是 fail-open：永远返回 False ⇒ 父已死也活成孤儿。）

红自证（--red-copy）：把 src\agentctl 整包复制到 <tmp>，在**副本**里把
    AssignProcessToJobObject 那一步掐掉，再跑同一批用例 ——
    **A 必须当场转红**（边框活过 10 s、屏幕上留 8 个窗 = 孤儿原样复现），
    其余用例仍绿。这证明判据真能抓这个 bug，不是「怎么跑都绿」。

卫生：不抢前台、不点鼠标、不拉急停（边框窗本来就 WS_EX_NOACTIVATE + 点击穿透）；
每个用例 try/finally 收干净，退出前断言「本判据拉起的边框窗 = 0」。
"""
import argparse
import atexit
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
BORDER_CLS_PREFIX = "AgentCtlCtrlBorder"
CREATE_NO_WINDOW = 0x08000000
EXIT_DEADLINE = 10.0                # 派单定的口径：≤10 s 内退出

k32 = ctypes.windll.kernel32
k32.OpenProcess.restype = ctypes.c_void_p
k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
k32.CloseHandle.argtypes = [ctypes.c_void_p]
u32 = ctypes.windll.user32

# ── 中间父进程的源码（每次跑现写进 <tmp>）────────────────────────────────
MIDDLE = r"""
# -*- coding: utf-8 -*-
'''中间父进程：拉起一个 --follow 边框子进程，然后一直活着等死。

由 tests\_ctrl_border_孤儿判据.py 生成并拉起。用法：
    python -X utf8 _middle.py <mode> <工作目录>

mode:
    job       走 ctrl_border.start_follow()  ⇒ 子进程进 KILL_ON_JOB_CLOSE 的 Job
    job_decoy 同上，但 --parent-pid 指向一个**别的活进程**
              （AGENTCTL_BORDER_PARENT_PID，由判据塞进来）= pid 复用的终局
    plain     不用 start_follow，命令行直起 ⇒ **没有任何 Job**（专测 pid 腿）
    stop      走 start_follow()，看到 <目录>/stop 文件就调 stop_follow() 正常退出
'''
import os
import subprocess
import sys
import time

sys.path.insert(0, os.environ["AGENTCTL_SRC"])
import agentctl.ctrl_border as cb

mode = sys.argv[1]
d = sys.argv[2]

if mode in ("job", "job_decoy", "stop"):
    pid = cb.start_follow("判据_" + mode)
else:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.environ["AGENTCTL_SRC"] + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, "-X", "utf8", "-m", "agentctl.ctrl_border", "--follow"]
    cmd += ["--parent-pid", str(os.getpid())]
    cmd += ["--parent-create-time", str(cb._process_create_time(os.getpid()))]
    cmd += ["--pass-file", env["AGENTCTL_PASS_FILE"]]
    cmd += ["--brake-flag", env["AGENTCTL_BRAKE_FLAG"]]
    p = subprocess.Popen(cmd, env=env, cwd=os.environ["AGENTCTL_SRC"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=0x08000000)
    pid = p.pid

with open(os.path.join(d, "middle.pid"), "w") as f:
    f.write(str(os.getpid()))
with open(os.path.join(d, "border.pid"), "w") as f:
    f.write(str(pid))
sys.stdout.write("READY pid=%s border=%s\n" % (os.getpid(), pid))
sys.stdout.flush()

stop_file = os.path.join(d, "stop")
while True:
    if mode == "stop" and os.path.exists(stop_file):
        cb.stop_follow()
        sys.stdout.write("STOPPED\n")
        sys.stdout.flush()
        break
    time.sleep(0.1)
"""


def _alive(pid):
    """pid 上的进程还活着吗（口径与 tests/_T1_窗3_自证.py 的 _proc_alive 一致）。"""
    if not pid:
        return False
    h = k32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    k32.CloseHandle(ctypes.c_void_p(h))
    return bool(ok) and code.value == 259          # STILL_ACTIVE


def _hard_kill(pid):
    """kill -9 式强杀（Windows 上就是 TerminateProcess，finally 一律跑不到）。"""
    if not pid or not _alive(pid):
        return
    h = k32.OpenProcess(0x0001, False, int(pid))    # PROCESS_TERMINATE
    if h:
        k32.TerminateProcess(ctypes.c_void_p(h), 1)
        k32.CloseHandle(ctypes.c_void_p(h))


def _border_windows():
    """当前屏幕上所有 AgentCtlCtrlBorder* 顶层窗 → [(hwnd, pid, 类名)]。"""
    P = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPARAM)
    buf = []

    def cb(h, l):
        b = ctypes.create_unicode_buffer(256)
        if u32.GetClassNameW(h, b, 256) and b.value.startswith(BORDER_CLS_PREFIX):
            pid = ctypes.c_ulong()
            u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            buf.append((int(h), int(pid.value), b.value))
        return 1

    p = P(cb)
    u32.EnumWindows(p, 0)
    return buf


def _read(path, default=""):
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return default


def _wait_gone(pid, timeout=EXIT_DEADLINE):
    """等 pid 消失。返回 (消失了?, 用了几秒)。"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if not _alive(pid):
            return True, time.monotonic() - t0
        time.sleep(0.2)
    return not _alive(pid), time.monotonic() - t0


class Ctx(object):
    """一次判据运行的全部现场（src 目录 + 临时目录 + 本判据拉起过的 pid）。"""

    def __init__(self, src, label, tmp):
        self.src = src
        self.label = label
        self.tmp = tmp
        self.mine = []                 # 本判据拉起过的所有 pid（收尾挨个收）
        self.baseline = len(_border_windows())
        self.n = 0
        with io.open(self.d("_middle.py"), "w", encoding="utf-8") as f:
            f.write(MIDDLE)

    def d(self, name):
        return os.path.join(self.tmp, name)

    def tag(self):
        self.n += 1
        return "%s_%d" % (self.label, self.n)

    def env(self, tag, decoy_pid=0):
        env = dict(os.environ)
        # AGENTCTL_BORDER=0 会让 start_follow 直接跳过拉起（那是生产开关，
        # 本机 DSH 侧确实设过）—— 判据必须自己把它摘掉，否则一条都起不来。
        env.pop("AGENTCTL_BORDER", None)
        env["AGENTCTL_SRC"] = self.src
        env["PYTHONPATH"] = self.src + os.pathsep + env.get("PYTHONPATH", "")
        env["AGENTCTL_BORDER_MUTEX"] = ("Local" + chr(92) +
                                       "AGBorderJudge_%s_%d" % (tag, os.getpid()))
        env["AGENTCTL_BORDER_LOG"] = self.d("border_%s.log" % tag)
        env["AGENTCTL_BRAKE_FLAG"] = self.d("brake_%s.flag" % tag)
        env["AGENTCTL_PASS_FILE"] = self.d("auth_%s.json" % tag)
        if decoy_pid:
            env["AGENTCTL_BORDER_PARENT_PID"] = str(decoy_pid)
        # 判据自己有张有效的证 ⇒ 边框真的会**显示**（不只建窗），
        # 这样「显示出来的边框能被收干净」才是真的被验过。
        with io.open(env["AGENTCTL_PASS_FILE"], "w", encoding="utf-8") as f:
            f.write(json.dumps({"scopes": ["B"],
                                "expires_at": time.time() + 3600}))
        return env

    def spawn_middle(self, tag, mode, decoy_pid=0):
        d = self.d("case_%s" % tag)
        os.makedirs(d, exist_ok=True)
        p = subprocess.Popen([sys.executable, "-X", "utf8",
                              self.d("_middle.py"), mode, d],
                             env=self.env(tag, decoy_pid), cwd=self.src,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,
                             creationflags=CREATE_NO_WINDOW)
        self.mine.append(p.pid)
        border = 0
        end = time.monotonic() + 25
        while time.monotonic() < end:
            border = int(_read(os.path.join(d, "border.pid"), "0") or 0)
            if border:
                break
            if p.poll() is not None:
                break
            time.sleep(0.2)
        if border:
            self.mine.append(border)
        return p, border, d

    def log_of(self, tag):
        return _read(self.d("border_%s.log" % tag))

    def cleanup(self):
        """把本判据拉起来的东西挨个收干净（每条用例 try/finally + atexit 双保险）。"""
        for pid in list(self.mine):
            _hard_kill(pid)
        time.sleep(1.2)


def _wait_windows(pid, want=8, timeout=25.0):
    """等 pid 拥有的边框窗出现（数到 want 个就算起来了）。返回 (够了?, 数到几个)。"""
    end = time.monotonic() + timeout
    n = 0
    while time.monotonic() < end:
        n = len([w for w in _border_windows() if w[1] == pid])
        if n >= want:
            return True, n
        time.sleep(0.2)
    return False, n


def _verdict(ctx, name, tag, border, secs, note=""):
    """收尾判定：边框进程没了 + 本判据的边框窗一个不剩（+ 屏幕边框窗回到基线）。"""
    wins = _border_windows()
    mine_left = [w for w in wins if w[1] in ctx.mine]
    ok = (not _alive(border)) and not mine_left and len(wins) == ctx.baseline
    if not border:
        # ★ 反假绿：边框压根没起来时，"进程没了 + 窗口为 0" 是**当然**成立的，
        #   直接判 PASS 等于什么都没验。凡 pid=0 一律 FAIL。
        ok = False
        detail = ("⚠ 边框子进程压根没起来（pid=0）⇒ 本用例没验到任何东西，"
                  "按失败计（防假绿）")
        return name, ok, detail
    detail = ("边框 pid=%s 存活=%s，%.1fs 内消失=%s；本判据残留边框窗=%d；"
              "屏幕边框窗总数=%d（基线 %d）%s"
              % (border, _alive(border), secs, not _alive(border),
                 len(mine_left), len(wins), ctx.baseline,
                 ("；" + note) if note else ""))
    return name, ok, detail


def case_a(ctx):
    """A 内核腿·Job —— pid 腿被人为打瞎：--parent-pid 指向一个**别的活进程**
    （这正是「父 pid 被系统复用」的终局状态）。硬杀中间父后，
    只可能靠 KILL_ON_JOB_CLOSE 的 Job 让 OS 收摊。"""
    tag = ctx.tag()
    decoy = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(180)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    ctx.mine.append(decoy.pid)
    border = 0
    try:
        mid, border, d = ctx.spawn_middle(tag, "job_decoy", decoy_pid=decoy.pid)
        up, nwin = _wait_windows(border)
        lg = ctx.log_of(tag)
        # ★ 认的是**父进程**那行 "[内核腿] ... 已入 KILL_ON_JOB_CLOSE 的 Job"，
        #   不是随便一个含 "KILL_ON_JOB_CLOSE" 的字样 —— 子进程自己那句
        #   「父进程监护：内核腿=…」里也有这几个字，拿它当证据会被自己骗过去。
        job_seen = "[内核腿]" in lg and "已入 KILL_ON_JOB_CLOSE" in lg
        print("    [A] 中间父 pid=%s，边框 pid=%s，诱饵（占了父 pid 的活进程）pid=%s"
              % (mid.pid, border, decoy.pid))
        print("    [A] 边框建起 %d 个窗口；日志里有内核腿证据（父进程那行 [内核腿]…）=%s"
              % (nwin, job_seen))
        _hard_kill(mid.pid)                       # kill -9 式：父进程暴毙
        gone, secs = _wait_gone(border, EXIT_DEADLINE)
        name, ok, detail = _verdict(ctx, "A 内核腿·Job：父被硬杀（pid 已被复用）后 "
                                        "边框 ≤10 s 内被 OS 收摊、窗口不剩", tag,
                                    border, secs,
                                    "建起窗口=%s（应为 True）" % up)
        if not job_seen:
            ok = False
            detail += "；⚠ 日志里没看到 Job 腿的证据（父进程那行 [内核腿]…已入 KILL_ON_JOB_CLOSE）"
        if not up:
            ok = False
            detail += "；⚠ 边框窗口没建起来，这条用例没验到东西"
        return name, ok, detail
    finally:
        _hard_kill(border)
        _hard_kill(decoy.pid)


def case_b(ctx):
    """B pid 腿（**没有** Job）—— 命令行直起 --follow，硬杀中间父。
    证明「能收摊」不是全靠 Job：拿掉内核腿，句柄 + 创建时间这条腿自己也站得住。"""
    tag = ctx.tag()
    border = 0
    mid = None
    try:
        mid, border, d = ctx.spawn_middle(tag, "plain")
        up, nwin = _wait_windows(border)
        print("    [B] 中间父 pid=%s（直接命令行拉起 ⇒ 没有任何 Job），边框 pid=%s，"
              "建起 %d 个窗口" % (mid.pid, border, nwin))
        _hard_kill(mid.pid)
        gone, secs = _wait_gone(border, EXIT_DEADLINE)
        name, ok, detail = _verdict(ctx, "B pid 腿：没有 Job 时，硬杀父进程后 "
                                        "边框 ≤10 s 内靠句柄+创建时间自己退出", tag,
                                    border, secs,
                                    "建起窗口=%s（应为 True）" % up)
        if not up:
            ok = False
            detail += "；⚠ 边框窗口没建起来，这条用例没验到东西"
        return name, ok, detail
    finally:
        _hard_kill(border)
        if mid is not None:
            _hard_kill(mid.pid)


def case_c1(ctx):
    """C1 优雅路径 —— 中间父调 stop_follow() 再正常退出，边框必须收摊。"""
    tag = ctx.tag()
    border = 0
    mid = None
    try:
        mid, border, d = ctx.spawn_middle(tag, "stop")
        up, nwin = _wait_windows(border)
        print("    [C1] 中间父 pid=%s 走 stop_follow() 正常退出，边框 pid=%s，"
              "建起 %d 个窗口" % (mid.pid, border, nwin))
        with io.open(os.path.join(d, "stop"), "w", encoding="utf-8") as f:
            f.write("stop")
        gone, secs = _wait_gone(border, EXIT_DEADLINE)
        mid_gone, _ = _wait_gone(mid.pid, 10.0)
        name, ok, detail = _verdict(ctx, "C1 优雅路径：父调 stop_follow() 正常退出后 "
                                        "边框 ≤10 s 内收摊", tag, border, secs,
                                    "中间父自己也退了=%s" % mid_gone)
        return name, (ok and mid_gone), detail
    finally:
        _hard_kill(border)
        if mid is not None:
            _hard_kill(mid.pid)


def case_c2(ctx):
    """C2 终止信号 —— 测试对中间父发 terminate（Windows 上 SIGTERM 的等价物）。
    不许把「能退出」做成「只在 TerminateProcess 强杀时退出」。"""
    tag = ctx.tag()
    border = 0
    mid = None
    try:
        mid, border, d = ctx.spawn_middle(tag, "job")
        up, nwin = _wait_windows(border)
        print("    [C2] 中间父 pid=%s，边框 pid=%s，建起 %d 个窗口；发 terminate"
              % (mid.pid, border, nwin))
        mid.terminate()
        gone, secs = _wait_gone(border, EXIT_DEADLINE)
        name, ok, detail = _verdict(ctx, "C2 终止信号：父被 terminate 后边框 ≤10 s "
                                        "内收摊（不只是在硬杀时才退）", tag,
                                    border, secs)
        return name, ok, detail
    finally:
        _hard_kill(border)
        if mid is not None:
            _hard_kill(mid.pid)


def _spawn_direct(ctx, tag, parent_pid, create_time):
    """判据自己直接拉一个 --follow（不走中间父）。"""
    env = ctx.env(tag)
    cmd = [sys.executable, "-X", "utf8", "-m", "agentctl.ctrl_border", "--follow"]
    cmd += ["--parent-pid", str(parent_pid)]
    cmd += ["--parent-create-time", str(create_time)]
    cmd += ["--pass-file", env["AGENTCTL_PASS_FILE"]]
    cmd += ["--brake-flag", env["AGENTCTL_BRAKE_FLAG"]]
    p = subprocess.Popen(cmd, env=env, cwd=ctx.src, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=CREATE_NO_WINDOW)
    ctx.mine.append(p.pid)
    return p


def case_d1(ctx):
    """D1 创建时间腿（对不上）—— 给的 --parent-create-time 与 pid 上的进程不是
    一回事 ⇒ 认亲失败 ⇒ 收摊 + 留日志（不许静默）。"""
    tag = ctx.tag()
    decoy = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(180)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    ctx.mine.append(decoy.pid)
    border = 0
    try:
        p = _spawn_direct(ctx, tag, decoy.pid, 123456789)   # 故意给错的创建时间
        border = p.pid
        _wait_windows(border, want=1, timeout=6)
        gone, secs = _wait_gone(border, EXIT_DEADLINE)
        lg = ctx.log_of(tag)
        said = "父进程已换人" in lg
        print("    [D1] 诱饵 pid=%s（活着的别的进程），边框 pid=%s，%.1fs 后存活=%s；"
              "日志里有「父进程已换人」=%s" % (decoy.pid, border, secs,
                                          _alive(border), said))
        name, ok, detail = _verdict(ctx, "D1 创建时间对不上 ⇒ 认亲失败、自行收摊、"
                                        "留日志（不许静默）", tag, border, secs,
                                    "日志留痕=%s" % said)
        return name, (ok and said), detail
    finally:
        _hard_kill(border)
        _hard_kill(decoy.pid)


def case_d2(ctx):
    """D2 创建时间腿（对得上）—— 同样的 pid，但创建时间报的是它自己的
    ⇒ 必须**继续活着**。否则就成了「一律自杀」，那是另一种坏（边框一闪就没）。"""
    tag = ctx.tag()
    decoy = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(180)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    ctx.mine.append(decoy.pid)
    border = 0
    try:
        sys.path.insert(0, ctx.src)
        import agentctl.ctrl_border as cb
        ct = cb._process_create_time(decoy.pid)
        sys.path.pop(0)
        p = _spawn_direct(ctx, tag, decoy.pid, ct)
        border = p.pid
        up, nwin = _wait_windows(border)
        time.sleep(3.0)
        still = _alive(border)
        print("    [D2] 诱饵 pid=%s 创建时间=%s；边框 pid=%s 建起 %d 个窗口，"
              "3 s 后仍存活=%s（应当仍存活）" % (decoy.pid, ct, border, nwin, still))
        # ★ D2 反过来：这里**要**它活着，所以不能套 _verdict（那套判的是
        #   "该收摊"）。收不收得干净由 finally 收完后的全局残留检查来管。
        name = "D2 创建时间对得上 ⇒ 必须继续正常跑（不许变成「一律自杀」）"
        ok = bool(still and up)
        detail = ("边框 pid=%s 建起 %d 个窗，3 s 后仍存活=%s（必须 True）；"
                  "用完已在 finally 里收掉，收干净与否看末尾的全局残留检查"
                  % (border, nwin, still))
        if not still:
            detail += "；⚠ 创建时间明明对得上却自己退出了 = 误杀"
        return name, ok, detail
    finally:
        _hard_kill(border)
        _hard_kill(decoy.pid)


def case_e(ctx):
    """E fail-closed —— --parent-pid 指向一个**已死且已回收**的 pid。
    老实现是 fail-open（永远返回 False）⇒ 父已死也活成孤儿。"""
    tag = ctx.tag()
    dead = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    dead.wait()
    dead._handle.Close()             # 放开最后一个句柄 ⇒ pid 真正从进程表消失
    border = 0
    try:
        p = _spawn_direct(ctx, tag, dead.pid, 0)
        border = p.pid
        gone, secs = _wait_gone(border, EXIT_DEADLINE)
        lg = ctx.log_of(tag)
        warns = lg.count("监护没生效")
        print("    [E] 死 pid=%s；边框 pid=%s，%.1fs 后存活=%s；"
              "「监护没生效」告警 %d 条（老实现 0.5 s 一条，6 s 刷 12 条）"
              % (dead.pid, border, secs, _alive(border), warns))
        name, ok, detail = _verdict(ctx, "E fail-closed：父已彻底消失时边框必须收摊"
                                        "（老实现是永远等 ⇒ 孤儿）", tag, border, secs,
                                    "告警条数=%d（应 ≤2，不许刷屏）" % warns)
        ok = ok and warns <= 2
        return name, ok, detail
    finally:
        _hard_kill(border)


CASES = [case_a, case_b, case_c1, case_c2, case_d1, case_d2, case_e]

RED_MARK_OLD = "            ok, aerr = _assign_to_job(job, _proc_handle(_FOLLOW_PROC))"
RED_MARK_NEW = ("            ok, aerr = False, 0  "
                "# ★跑红自证：Job Object 那一步被掐掉了（副本）")


def build_red_copy(dst):
    """把 src\\agentctl 整包复制一份，并在**副本**里掐掉 Job Object 那一步。

    ★ 为什么必须动副本：判据要证明的是「去掉内核腿 ⇒ 孤儿复现」，
      而仓库里的产品代码当然不能为了跑红自证去改。
    ★ 掐的是 AssignProcessToJobObject 这一行：内核腿没了，pid 腿还在 ——
      于是只有「pid 腿被打瞎」（用例 A 那种 pid 复用终局）才会红。
    """
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(SRC, dst)
    p = os.path.join(dst, "agentctl", "ctrl_border.py")
    s = io.open(p, encoding="utf-8").read()
    if s.count(RED_MARK_OLD) != 1:
        raise SystemExit("跑红自证失败：副本里找不到要掐的那一行（产品代码结构变了？）")
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        s.replace(RED_MARK_OLD, RED_MARK_NEW, 1))
    return dst


def main(argv=None):
    ap = argparse.ArgumentParser(description="孤儿边框判据（窗Y）")
    ap.add_argument("--red-copy", action="store_true",
                    help="跑红自证：拿掐掉 Job Object 那一步的**副本**再跑一遍，"
                         "要求用例 A 当场转红（孤儿复现）")
    ap.add_argument("--only", default="", help="只跑某一条（按用例字母序，如 A）")
    a = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="_orphan_judge_")
    if a.red_copy:
        label, src = "RED", build_red_copy(os.path.join(tmp, "redpkg"))
        expect = "A 必须转红（孤儿复现），其余仍绿"
    else:
        label, src = "GRN", SRC
        expect = "全部绿"
    ctx = Ctx(src, label, tmp)
    atexit.register(ctx.cleanup)
    print("=" * 72)
    print("孤儿边框判据 · %s ｜ 被测 src=%s" % (label, src))
    print("临时目录 %s" % tmp)
    print("开工前屏幕上已有的边框窗 = %d 个（基线，结束时必须回到这个数）"
          % ctx.baseline)
    print("期望：%s" % expect)
    print("=" * 72)

    results = []
    try:
        for fn in CASES:
            key = fn.__name__.split("_")[-1].upper()
            if a.only and a.only.upper() != key:
                continue
            t0 = time.monotonic()
            try:
                name, ok, detail = fn(ctx)
            except Exception as e:
                name, ok, detail = (fn.__name__, False, "用例抛异常：%r" % (e,))
            results.append((key, name, ok, detail, time.monotonic() - t0))
            print("  %s %s" % ("✔" if ok else "✘", name))
            print("      %s" % detail)
    finally:
        ctx.cleanup()

    time.sleep(1.0)
    wins = _border_windows()
    print("-" * 72)
    nbad = len([r for r in results if not r[2]])
    for key, name, ok, detail, secs in results:
        print("  %s %-2s %s（%.1fs）" % ("PASS" if ok else "FAIL", key, name, secs))
    print("-" * 72)
    print("收尾：屏幕上边框窗 = %d 个（基线 %d，本判据残留 %d）"
          % (len(wins), ctx.baseline, len([w for w in wins if w[1] in ctx.mine])))
    print("总计 %d 条，失败 %d 条" % (len(results), nbad))

    if a.red_copy:
        # 跑红自证的判据：只有「A 红了、其余没红」才算这个自证成立。
        by = dict((r[0], r[2]) for r in results)
        a_red = (by.get("A") is False)
        others = [r[0] for r in results if r[0] != "A"]
        others_green = all(by.get(k) for k in others)
        print("-" * 72)
        print("跑红自证判定：A 转红=%s（孤儿在副本里原样复现）；其余用例仍绿=%s"
              % (a_red, others_green))
        if a_red and others_green:
            print("结论：判据**能红** —— 去掉 Job Object 那一步，用例 A 当场抓到孤儿。")
            return 0
        print("结论：自证**不成立** —— 副本里孤儿没复现，判据的分辨力存疑。")
        return 1
    return 1 if nbad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
