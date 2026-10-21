# -*- coding: utf-8 -*-
r"""tests/_P1_凭据内容寻址_判据.py —— 判据：凭据必须**按内容**认人，不是按时间

病根（2026-10-21 单 A「统一哈希来源」）：
    签发侧取的是「legacy 帧文件（PrintWindow 落盘 PNG）的**文件字节**」的 sha256，
    校验侧取的是「**mss 实时抓屏**的 RGB 缓冲」的 sha256
    —— 两个来源**必然不等**（就算画面一模一样）⇒ 闸门 gate.py:1179 必然判
    「画面哈希变了」(code=12) ⇒ **假拦**；
    而且 verify_image 从来没进过契约 ⇒ 那条分支**一次都没走到过**
    ⇒ 「凭据内容寻址」这个能力事实上**不存在**。

判据（本判据**不碰真窗、不点真鼠标、不用真通行证**，全部打桩 + 临时闸门目录）：
    A  同源（静态）：签发侧与校验侧**必须调同一个函数**取画面摘要；
                     校验侧不许再出现 mss 抓屏算摘要；签发侧不许再自己哈希文件字节。
    B  同一张图 ⇒ **通过**（内容寻址认得出来）
    C  内容变了 ⇒ **拒绝**（code=12 画面哈希变了）且**零像素**（act.py 一次都没被调）
    D  默认行为不变：不传 verify_image 时**不做**图像比对（今天就是这样，不许顺手加严）
    E  真实抓取通路：自建真窗口 ⇒ 客户区摘要**稳定**；换图案 ⇒ 摘要变化；
                     拿不到 ⇒ **空串**（不许假装有摘要）
    F  --inject 必须变红：注入「签发与校验取自不同来源」/「恒不比较」两种坏实现
                     ⇒ 对应判据必须变红（证明这条判据有判别力，不是死判据）

跑红自证：
    python -X utf8 tests/_P1_凭据内容寻址_判据.py --inject
    —— 注入只在**进程内 monkeypatch**，现役文件一个字节不动。

退出码：0 = 全绿 ｜ 1 = 有红（逐条点名）｜ 2 = 环境不满足（不许写绿）
"""
import ast
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

RC_GREEN, RC_RED, RC_ENV = 0, 1, 2

# 判据里用的假窗口（打桩环境用；不点、不抢焦点）
HWND = 4242
RECT = [100, 100, 500, 400]


# ══════════════════════════ 环境准备 ══════════════════════════
def _prep_env():
    """事件流与闸门状态都指到临时目录 ⇒ 本判据**不碰**真事件流、真通行证。"""
    tmp = tempfile.mkdtemp(prefix="agentctl_digest_")
    os.environ["AGENTCTL_TIMELINE"] = os.path.join(tmp, "timeline")
    os.makedirs(os.environ["AGENTCTL_TIMELINE"], exist_ok=True)
    src = os.path.join(ROOT, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    return tmp


def _load_core(tmp):
    from agentctl import core, digest, gate          # noqa: E402
    authdir = os.path.join(tmp, "auth")
    os.makedirs(authdir, exist_ok=True)
    gate.AUTHDIR = authdir
    gate.PASSFILE = os.path.join(authdir, "auth_pass.json")
    gate.CAPTURE_DB = os.path.join(authdir, "capture.json")
    gate.LEASE_DB = os.path.join(authdir, "lease.json")
    gate.LOCKFILE = os.path.join(authdir, "gate.lock")
    gate.LOG = os.path.join(authdir, "gate.log")
    gate.log = lambda msg: None
    core._ensure_border = lambda: None                 # 不拉指示层边框
    return core, digest, gate


# ══════════════════════════ A · 同源（静态） ══════════════════════════
def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _uses(node, predicate):
    """函数体里**实际用到**某个东西吗（注释与文档串不算实现 —— 与本项目
    「注释行不算实现」的老规矩一致：只看 AST 节点，不看源码文本）。"""
    if node is None:
        return False
    for sub in ast.walk(node):
        if predicate(sub):
            return True
    return False


def _is_digest_call(sub):
    return (isinstance(sub, ast.Attribute) and sub.attr == "window_digest"
            and isinstance(sub.value, ast.Name) and sub.value.id == "digest")


def _is_digest_callnode(sub):
    """Call 节点上的 digest.window_digest(...)"""
    return (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "window_digest"
            and isinstance(sub.func.value, ast.Name) and sub.func.value.id == "digest")


def _static_same_source():
    """签发与校验必须走**同一个**摘要函数。返回 (oks, reds)。"""
    oks, reds = [], []
    core_py = os.path.join(ROOT, "src", "agentctl", "core.py")
    digest_py = os.path.join(ROOT, "src", "agentctl", "digest.py")
    if not os.path.isfile(core_py) or not os.path.isfile(digest_py):
        reds.append("A 同源：读不到 core.py / digest.py（环境不满足）")
        return oks, reds
    src = open(core_py, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        reds.append("A 同源：core.py 语法错（%s）" % e)
        return oks, reds
    issue = _find_func(tree, "_issue_capture_from_shot")
    bind = _find_func(tree, "_bind_capture")
    if issue is None or bind is None:
        reds.append("A 同源：core.py 里找不到 _issue_capture_from_shot / _bind_capture"
                    "（被改名或删了？接线断了）")
        return oks, reds
    if _uses(issue, _is_digest_call) and _uses(bind, _is_digest_call):
        oks.append("A 同源：签发与校验两处都调 digest.window_digest（同一个来源）")
    else:
        reds.append("A 同源：签发/校验没有都调 digest.window_digest"
                    "（签发=%s / 校验=%s）"
                    % ("有" if _uses(issue, _is_digest_call) else "没有",
                       "有" if _uses(bind, _is_digest_call) else "没有"))
    # ★ 校验侧不许再抓屏算摘要（mss / grab 出现在**代码**里才算，注释不算）
    if _uses(bind, lambda s: (isinstance(s, ast.Name) and s.id == "mss")
               or (isinstance(s, ast.Attribute) and s.attr == "grab")):
        reds.append("A 同源：校验侧还在用 mss 抓屏算摘要（那正是老病根的另一半）")
    else:
        oks.append("A 同源：校验侧不再用 mss 抓屏算摘要")
    # ★ 签发侧不许自己哈希文件字节（sha256/hexdigest/hashlib 出现在代码里才算）
    if _uses(issue, lambda s: (isinstance(s, ast.Attribute)
                                and s.attr in ("sha256", "hexdigest"))
               or (isinstance(s, ast.Name) and s.id == "hashlib")):
        reds.append("A 同源：签发侧还在自己哈希文件字节（摘要必须只出自 digest.window_digest）")
    else:
        oks.append("A 同源：签发侧不再自己哈希文件字节")
    # ★ 摘要口径：必须是**客户区**（PrintWindow flag=3），不含标题栏噪声
    dsrc = open(digest_py, encoding="utf-8").read()
    try:
        dtree = ast.parse(dsrc)
    except SyntaxError as e:
        reds.append("A 口径：digest.py 语法错（%s）" % e)
        return oks, reds
    cap = _find_func(dtree, "_capture_client_rgb")

    def _printwindow_flag3(sub):
        if not (isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "PrintWindow"):
            return False
        if not sub.args:
            return False
        last = sub.args[-1]
        return isinstance(last, ast.Constant) and last.value == 3
    if _uses(cap, _printwindow_flag3):
        oks.append("A 口径：摘要取**客户区**（PrintWindow flag=3，不含标题栏噪声）")
    else:
        reds.append("A 口径：digest.py 的取像不是客户区 flag=3（口径漂了）")
    if "客户区" in (ast.get_docstring(dtree) or ""):
        oks.append("A 口径：digest.py 顶部写明了「客户区」口径（改口径的人会先看到它）")
    else:
        reds.append("A 口径：digest.py 顶部没写明「客户区」口径（后来人会踩同一个坑）")

    # ★ **顺序**也是口径的一部分：签发侧必须先 _win_rect（老 coord 导入即设 DPI 感知）
    #   再取摘要。否则新进程里 GetClientRect 给的是虚拟化坐标（150% 屏上 1706×1066），
    #   PrintWindow 渲的却是物理尺寸（2560×1600）⇒ 画面被裁掉一半，
    #   之后拿物理尺寸再比一次必然不等 ⇒ 误判「画面哈希变了」。
    def _first_lineno(node, pred):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and pred(sub):
                return sub.lineno
        return None

    lr = _first_lineno(issue, lambda s: isinstance(s, ast.Call)
                       and isinstance(s.func, ast.Name) and s.func.id == "_win_rect")
    ld = _first_lineno(issue, _is_digest_callnode)
    if lr is not None and ld is not None and lr < ld:
        oks.append("A 顺序：签发侧先 _win_rect（设 DPI 感知）再取摘要（不会截到虚拟化坐标）")
    else:
        reds.append("A 顺序：签发侧没有先 _win_rect 再取摘要"
                    "（_win_rect 在第 %s 行、摘要调用在第 %s 行）—— "
                    "新进程里会按虚拟化坐标取像，画面会被裁掉一半"
                    % (lr, ld))
    return oks, reds


# ══════════════════════════ 打桩环境 ══════════════════════════
def _synth(color=(30, 60, 90), size=(120, 80)):
    """合成一张「有结构」的图（纯色会被 blank 判据当成没画面）。

    ★ 条纹用**底色的互补色** ⇒ 无论底色取什么，画面都保持高对比（stddev ≫ 3），
      不会被「近纯色 ⇒ 没有摘要」那条规则误伤（踩过：底色 (200,40,40) 与固定
      条纹色 (210,40,40) 几乎同色 ⇒ 画面近纯色 ⇒ 摘要为空 ⇒ 比对被跳过）。
    """
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    stripe = tuple(255 - c for c in color)
    for i in range(0, size[0], 10):
        d.rectangle([i, 0, i + 4, size[1]], fill=stripe)
    return img


def _stub(core, digest, calls, state):
    """把外部依赖全部打桩：闸门 / 租约 / 窗口 / 取像 / 真动作。"""
    core._win_rect = lambda hwnd: list(RECT)
    core._is_foreground = lambda hwnd: True
    core._require_write = lambda actor: None           # 租约：不要求真租约
    core._ownership_precheck = lambda args, hwnd: (None, None)
    # ★ 动作前/后快照：给一张合成图（_act_grab 要的是 PIL.Image，不是路径），
    #   落盘也拦掉（判据不往真 work 目录里写证据图）。
    core._act_grab = lambda hwnd: _synth(state["color"])
    core._act_diff_store = lambda fid, before, after, df: {}
    core.gate.check = lambda *a, **k: (True, "", 0)    # 通行证：不要求真通行证

    def _legacy(script, argv, timeout=None):
        calls.append(script)                            # ← 记下「真的去点了」
        return 0, "{}", ""
    core._legacy = _legacy
    digest._capture_client_rgb = lambda hwnd: _synth(state["color"])


# ══════════════════════════ B / C / D 行为 ══════════════════════════
def run_behavior(core, digest, gate, mode="normal"):
    """跑 B/C/D 三条行为判据（打桩环境）。返回 (oks, reds)。"""
    oks, reds = [], []
    calls = []
    state = {"color": (30, 60, 90)}

    # 记下原值，结束前还原（不同 mode 之间不许互相污染）
    saved = {k: getattr(core, k) for k in
             ("_win_rect", "_is_foreground", "_require_write",
              "_ownership_precheck", "_act_grab", "_act_diff_store",
              "_legacy", "_bind_capture")}
    saved["gate.check"] = core.gate.check
    saved["_capture_client_rgb"] = digest._capture_client_rgb
    saved["window_digest"] = digest.window_digest
    try:
        _stub(core, digest, calls, state)
        real_wd = digest.window_digest
        real_bind = core._bind_capture
        if mode == "bad_source":
            # 坏实现①：签发与校验**取自不同来源**（每次调用换一个来源）
            n = {"i": 0}

            def bad_wd(hwnd):
                n["i"] += 1
                base = real_wd(hwnd)
                return ("%s|src%d" % (base, n["i"])) if base else ""
            digest.window_digest = bad_wd
        elif mode == "bad_nocompare":
            # 坏实现②：摘要比对照旧**关掉**（内容变了也放行）
            def bad_bind(args, hwnd):
                a = dict(args)
                a.pop("verify_image", None)
                return real_bind(a, hwnd)
            core._bind_capture = bad_bind

        # ── 签发（走真路径：core._issue_capture_from_shot → digest.window_digest）
        tok = core._issue_capture_from_shot(HWND, "{}")
        if not tok:
            reds.append("B 签发：core._issue_capture_from_shot 没签出 capture_id")
            return oks, reds

        # ── B 同一张图 ⇒ 通过 ────────────────────────────────────────────
        err = core._bind_capture({"xy": [200, 200], "from_capture": tok,
                                  "verify_image": True}, HWND)
        if err is None:
            oks.append("B 同一张图 ⇒ 通过（内容寻址认得出来，_bind_capture 不拦）")
        else:
            reds.append("B 同一张图 ⇒ **被拒**（code=%s｜%s）—— 同一张图都过不去，"
                        "说明签发与校验不是同一个来源"
                        % (err.get("code"), str(err.get("warning"))[:110]))

        # ── C 内容变了 ⇒ 拒绝 ────────────────────────────────────────────
        tok2 = core._issue_capture_from_shot(HWND, "{}")
        state["color"] = (200, 40, 40)                  # 画面变了
        err2 = core._bind_capture({"xy": [200, 200], "from_capture": tok2,
                                   "verify_image": True}, HWND)
        code2 = int((err2 or {}).get("code") or 0)
        if code2 == gate.CODE_STALE_CAPTURE:
            oks.append("C 内容变了 ⇒ 拒绝（code=%d「画面哈希变了」）"
                       % gate.CODE_STALE_CAPTURE)
        else:
            reds.append("C 内容变了 ⇒ **没被拒**（err=%s）—— 旧内容还能拿来点"
                        % (str(err2)[:110] if err2 else "None（放行）"))

        # ── C2 零像素：拦下之后一个像素都不许点 ──────────────────────────
        calls.clear()
        tok3 = core._issue_capture_from_shot(HWND, "{}")
        state["color"] = (10, 200, 10)
        r = core._act("click", {"hwnd": HWND, "xy": [200, 200], "from_capture": tok3,
                                "verify_image": True, "actor": "判据"},
                      ["click", "--hwnd", str(HWND)], "B")
        code3 = int((r or {}).get("code") or 0)
        if code3 == gate.CODE_STALE_CAPTURE and not calls:
            oks.append("C2 零像素：内容变了 ⇒ code=%d 且 act.py **一次都没被调**"
                       % gate.CODE_STALE_CAPTURE)
        else:
            reds.append("C2 零像素：code=%s，act.py 被调 %d 次 —— 拦下之后还点了"
                        % (code3, len(calls)))

        # ── D 默认行为不变 ───────────────────────────────────────────────
        tok4 = core._issue_capture_from_shot(HWND, "{}")
        state["color"] = (33, 33, 33)                  # 内容又变了
        err3 = core._bind_capture({"xy": [200, 200], "from_capture": tok4}, HWND)
        if err3 is None:
            oks.append("D 默认不变：不传 verify_image ⇒ 不做图像比对（与改前逐字一致）")
        else:
            reds.append("D 默认不变：不传 verify_image 也被拒（code=%s）—— "
                        "默认口径被悄悄改动了" % (err3 or {}).get("code"))
    finally:
        for k, v in saved.items():
            if k == "gate.check":
                core.gate.check = v
            elif k in ("_capture_client_rgb", "window_digest"):
                setattr(digest, k, v)
            else:
                setattr(core, k, v)
    return oks, reds


# ══════════════════════════ E 真实抓取通路 ══════════════════════════
def _fixture():
    """造一个**会画图**的真窗口（客户区有结构 ⇒ 不会被 blank 判据当成没画面）。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32
    k = ctypes.windll.kernel32
    u.CreateWindowExW.restype = wt.HWND
    u.CreateWindowExW.argtypes = [ctypes.c_ulong, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_ulong,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
    u.DefWindowProcW.restype = ctypes.c_ssize_t
    u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    u.RegisterClassW.argtypes = [ctypes.c_void_p]
    g.CreateSolidBrush.restype = wt.HBRUSH
    g.CreateSolidBrush.argtypes = [ctypes.c_uint32]
    # ★ 64 位句柄必须显式声明原型，否则 FillRect 收窄成 int ⇒ OverflowError（画图静默失败）
    u.FillRect.argtypes = [wt.HDC, ctypes.c_void_p, wt.HBRUSH]
    u.FillRect.restype = wt.BOOL
    g.DeleteObject.argtypes = [wt.HGDIOBJ]
    g.DeleteObject.restype = wt.BOOL
    u.GetClientRect.argtypes = [wt.HWND, ctypes.c_void_p]
    u.GetClientRect.restype = wt.BOOL
    WM_PAINT, WM_PRINTCLIENT = 0x000F, 0x0318
    state = {"pattern": 0}

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT),
                    ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                    ("rgbReserved", wt.BYTE * 32)]

    class WNDCLASS(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                    ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    def paint(hdc, w, h):
        n = 8
        cw, ch = max(1, w // n), max(1, h // n)
        for y in range(n):
            for x in range(n):
                on = ((x + y) % 2 == 0) if state["pattern"] == 0 else ((x * y) % 3 == 0)
                br = g.CreateSolidBrush(ctypes.c_uint32(0x202020 if on else 0xE0E0E0))
                r = wt.RECT(x * cw, y * ch, (x + 1) * cw, (y + 1) * ch)
                u.FillRect(hdc, ctypes.byref(r), br)
                g.DeleteObject(br)

    def proc(h, m, w, l):
        if m in (WM_PRINTCLIENT, WM_PAINT):
            if m == WM_PRINTCLIENT:
                hdc = wt.HDC(w)
            else:
                ps = PAINTSTRUCT()
                hdc = u.BeginPaint(h, ctypes.byref(ps))
            r = wt.RECT()
            u.GetClientRect(h, ctypes.byref(r))
            paint(hdc, r.right - r.left, r.bottom - r.top)
            if m == WM_PAINT:
                u.EndPaint(h, ctypes.byref(ps))
            return 1
        return u.DefWindowProcW(h, m, w, l)

    CLS = "AgentCtlDigestFixture"
    PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, ctypes.c_size_t,
                              ctypes.c_ssize_t)(proc)
    hinst = k.GetModuleHandleW(None)
    wc = WNDCLASS()
    wc.lpfnWndProc = ctypes.cast(PROC, ctypes.c_void_p)
    wc.hInstance = hinst
    wc.lpszClassName = CLS
    try:
        u.RegisterClassW(ctypes.byref(wc))
    except Exception:
        pass                                  # 已注册（重跑）就沿用
    hwnd = u.CreateWindowExW(0, CLS, "digest fixture", 0x00CF0000, 300, 300, 320, 240,
                              None, None, hinst, None)
    if not hwnd:
        raise RuntimeError("夹具窗口建不出来")
    u.ShowWindow(wt.HWND(hwnd), 5)             # SW_SHOW：只为拿到真实尺寸
    msg = wt.MSG()

    def pump(n=40):
        for _ in range(n):
            while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))

    def set_pattern(p):
        state["pattern"] = int(p)
        u.InvalidateRect(wt.HWND(hwnd), None, True)
        u.UpdateWindow(wt.HWND(hwnd))
        pump()

    pump()
    return int(hwnd), set_pattern, (u, PROC, CLS, hinst, int(hwnd))


def _destroy_fixture(keep):
    import ctypes.wintypes as wt
    u, PROC, CLS, hinst, hwnd = keep
    try:
        u.DestroyWindow(wt.HWND(hwnd))
    except Exception:
        pass
    try:
        u.UnregisterClassW(CLS, hinst)
    except Exception:
        pass


def run_real(digest):
    """E 真实抓取通路。返回 (oks, reds, unverifiable)。"""
    oks, reds, unverifiable = [], [], []
    try:
        hwnd, set_pattern, keep = _fixture()
    except Exception as e:
        return [], [], ["E 真实抓取：造不出夹具窗口（%s: %s）" % (type(e).__name__, e)]
    try:
        d1 = digest.window_digest(hwnd)
        d2 = digest.window_digest(hwnd)
        if not d1:
            unverifiable.append("E 真实抓取：PrintWindow 拿不到客户区画面（摘要为空）")
        elif d1 == d2:
            oks.append("E 真实抓取：同一窗口连续两次客户区摘要一致（%s…）" % d1[:12])
        else:
            reds.append("E 真实抓取：同一窗口两次摘要不同（%s… vs %s…）—— 口径不稳定"
                        % (d1[:12], d2[:12]))
        set_pattern(1)
        d3 = digest.window_digest(hwnd)
        if d3 and d1 and d3 != d1:
            oks.append("E2 内容敏感：换图案后摘要变了（%s… → %s…）" % (d1[:12], d3[:12]))
        elif d3 == d1:
            reds.append("E2 内容敏感：换了图案摘要没变 —— 摘要分不出内容变化")
        else:
            unverifiable.append("E2 内容敏感：换图案后摘要为空（环境不满足）")
        if digest.window_digest(999999) == "":
            oks.append("E3 拿不到就空串：无效 hwnd ⇒ 空摘要（不许假装有）")
        else:
            reds.append("E3 拿不到就空串：无效 hwnd 却给出了摘要")
    finally:
        _destroy_fixture(keep)
    return oks, reds, unverifiable


# ══════════════════════════ 主流程 ══════════════════════════
def main(argv):
    inject = "--inject" in argv
    tmp = _prep_env()
    oks, reds, unverifiable = [], [], []
    try:
        core, digest, gate = _load_core(tmp)

        a_oks, a_reds = _static_same_source()
        oks += a_oks
        reds += a_reds

        b_oks, b_reds = run_behavior(core, digest, gate, mode="normal")
        oks += b_oks
        reds += b_reds

        e_oks, e_reds, e_unv = run_real(digest)
        oks += e_oks
        reds += e_reds
        unverifiable += e_unv

        if inject:
            for mode, label in (("bad_source", "坏实现①：签发与校验取自**不同来源**"),
                                ("bad_nocompare", "坏实现②：摘要比对照旧**恒不比较**")):
                m_oks, m_reds = run_behavior(core, digest, gate, mode=mode)
                if m_reds:
                    print("  · 注入「%s」⇒ 变红 %d 条：%s"
                          % (label, len(m_reds), m_reds[0][:120]))
                    # ★ 注入变红**必须计入总账** —— 不计的话结论会误判成「注入后仍绿」。
                    reds += m_reds
                else:
                    reds.append("F 注入自证失败：注入「%s」后仍然全绿 ⇒ 这条判据没有判别力"
                                % label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 72)
    print("P1 凭据内容寻址判据  ｜  闸门目录=临时（不碰真通行证、不点真鼠标）")
    print("=" * 72)
    for o in oks:
        print("  ✅ %s" % o)
    for r in reds:
        print("  ❌ %s" % r)
    for u_ in unverifiable:
        print("  ⚠️  验不了：%s" % u_)
    print("-" * 72)
    print("绿 %d 条 / 红 %d 条%s"
          % (len(oks), len(reds),
             (" / 验不了 %d 条" % len(unverifiable)) if unverifiable else ""))
    if inject:
        if reds:
            print("结论：【注入自证成立】注入后变红 %d 条 ⇒ 这条判据是活的。" % len(reds))
            return RC_RED
        print("结论：【注入自证失败】注入后仍绿 ⇒ 这条判据是死的。")
        return RC_RED
    if reds:
        print("结论：【红】")
        return RC_RED
    if unverifiable:
        print("结论：【验不了】环境不满足 —— 不许写绿，也不许算红。")
        return RC_ENV
    print("结论：【绿】")
    return RC_GREEN


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
