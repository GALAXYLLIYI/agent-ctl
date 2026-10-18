# -*- coding: utf-8 -*-
r"""_T1_窗3_自证.py —— W3-A（uia_tree 坐标系标注）＋ W3-B（ctrl_border --follow）自证。

★ 铁律三条，本脚本自己先做到：
  1. **只读 + 临时目录假状态**：所有通行证 / 急停 flag 都在
     %TEMP%\T1_W3_<pid> 下的假文件里。**绝不碰真 auth_pass.json、绝不碰真急停 flag。**
     —— 用法上还有一道保险：边框段**必须**显式传 --pass-file / --brake-flag，
        两者都指向临时目录；不给就拒绝跑。
  2. **判据能跑红**：用 --src <目录> 把 PYTHONPATH 指到任意一份 uia_tree.py / ctrl_border.py，
     同一套判据原样跑。旧版（git show HEAD:... 导出的副本）跑同一条必红 —— 这就是改前红证据。
  3. **不点不动**：坐标段只读树（walk/tree/find 只读属性），边框段只画自己那 4 条边。

用法：
    python -X utf8 tests\_T1_窗3_自证.py coords            # W3-A 全部断言
    python -X utf8 tests\_T1_窗3_自证.py border            # W3-B 15 例
    python -X utf8 tests\_T1_窗3_自证.py wire             # 接线（server.py / mcp.py）
    python -X utf8 tests\_T1_窗3_自证.py all
    python -X utf8 tests\_T1_窗3_自证.py coords --src <旧版src目录>

★ 仓根（窗M 2026-10-16 改）：**从 __file__ 推**（tests\ 的上一级 = 仓根），
  在干净 clone / 别的机器上跑，验的都是**它自己所在的那份仓**；
  要显式指到别处用环境变量覆盖口：
      AGENTCTL_ROOT=<另一份仓> python -X utf8 tests\_T1_窗3_自证.py all
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time

# ★ 2026-10-17（DSH 补 · 窗Z 的第 18 条红了以后才看清）：
#   只在"某些 spawn"的 env 里设 AGENTCTL_BORDER_LOG 不够 —— 还有子进程（holder /
#   间接 start_follow）继承的是**本进程环境**，于是照样写生产目录的 ctrl_border.log，
#   第 18 条「生产目录前后快照完全一致」就会红。
#   ⇒ 在**模块导入时**全局设一次（用 setdefault，不覆盖窗Z 的按例覆盖），
#     任何继承环境的子进程都落到本次沙箱。
_GLOBAL_BORDER_LOG = os.path.join(tempfile.mkdtemp(prefix="T1_W3_border_g_"), "ctrl_border.log")
os.environ.setdefault("AGENTCTL_BORDER_LOG", _GLOBAL_BORDER_LOG)

# ★ 窗M（2026-10-16）修「闸门会撒谎」：仓根不再写死。
#   写死时本判据在任何机器上验的都是那台开发机的 src/contract —— 干净 clone 的绿灯
#   与入库物无关。现在从 __file__ 推（tests\ 的上一级 = 仓根），
#   AGENTCTL_ROOT 仍可显式指到别处（指红基线仓跑红）。断言一条没删、也没放宽。
REPO = os.path.abspath(os.environ.get("AGENTCTL_ROOT") or
                       os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "src")
CONTRACT = os.path.join(REPO, "contract", "tools.json")
BORDER_CLS = "AgentCtlCtrlBorder"
# ★ 窗F（2026-10-15）：边框每条边现在建**两个**窗口 —— 主色 + 1px 反色描边，
#   两者类名不同（一个窗口类的背景刷刷不了两种颜色）。枚举边框窗要认**两个**类名，
#   否则描边那 4 个窗口会被漏数，窗口数/点击穿透/孤儿回收三条都会假绿。
BORDER_EDGE_CLS = "AgentCtlCtrlBorderEdge"
BORDER_CLS_ALL = (BORDER_CLS, BORDER_EDGE_CLS)

# ══ 窗F（2026-10-18）跑红自证注入口 ══════════════════════════════════════════
#   把用例12 的「就绪轮询」整个旁路，退回**固定 sleep**（故意取短到边框还没上屏）
#   => 12b「不带 WDA 抓得到边框」必须红。
#   用法（PowerShell）：
#     $env:T1_W3_INJECT_FIXED_SLEEP="0.2"; python -X utf8 tests\_T1_窗3_自证.py all
#   ★ 不设这个变量 = 现役路径（有界就绪轮询）。这是**判据自身的开关**，
#     不碰 src/ 一个字节，也不需要 tests\_inject.py（那个是往产品副本注缺陷用的，
#     这里的缺陷在判据自己身上，注入到产品上证明不了「轮询有没有在干活」）。
_INJECT_FIXED_SLEEP = os.environ.get("T1_W3_INJECT_FIXED_SLEEP")
# ★★ 窗G（2026-10-18）：这两个开关各自**作用到哪些用例**。
#   · 延迟（T1_W3_FORCE_START_DELAY）：窗F 只给「12」加 —— 当时 13/14/16/17 还走固定
#     sleep，一起加会被打红。窗G 给 13/16 补上就绪轮询了 ⇒ 把 13/16 加进这份名单，
#     它们等得到。**14/15/17 本单没改**，继续不延迟：给它们加延迟那一跑必红，
#     而那些红与「13/16 的就绪轮询对不对」无关，是把延迟扩散到了不该延迟的地方。
#   · 注入（T1_W3_INJECT_FIXED_SLEEP + T1_W3_INJECT_CASES）：默认仍只给「12」，
#     保持窗F 已验收的行为一字不变；要单独验 13/16 的注入就显式点名：
#       $env:T1_W3_INJECT_CASES="13,16-第1轮,16-第2轮"
_DELAY_CASES = ("12", "13", "14", "15", "16", "17")
_INJECT_CASES = tuple(x.strip() for x in
                      (os.environ.get("T1_W3_INJECT_CASES") or "12").split(",")
                      if x.strip())


# ══ 窗F 补（2026-10-18 12:5x）：**强制启动延迟**开关 ══════════════════════════
#   为什么要它：光把 sleep 取短，注入只在「慢的时候」有判别力 ——
#   DSH 2026-10-18 12:49 实测：空载机器上 T1_W3_INJECT_FIXED_SLEEP=0.2 根本没红
#   （0.2s 已经够边框上屏），绿67/红0。而「慢」正是偶发场景、不能指望复现
#   ⇒ 那条注入会永远「碰运气绿」。
#   治法：**把「慢」变成开关**。设了它，边框子进程在 import 之前先睡 N 秒
#   （N 秒内屏幕上什么都没有），于是：
#       现役有界就绪轮询（上限 10s / 间隔 0.2s）⇒ 照样等得到 ⇒ 仍绿；
#       注入的固定 sleep（只要 < N）⇒ 必然抓空 ⇒ **确定性红**。
#   ⚠ **不碰 src/**：延迟是靠 spawn 时把 `-m agentctl.ctrl_border`
#     换成 `runpy.run_module` 的小包装实现的，延迟发生在 **import 之前**，
#     产品代码一个字节没改、行为语义也没变（runpy.run_module(run_name='__main__')
#     与 `-m` 等价）。
#     用法：$env:T1_W3_FORCE_START_DELAY="3"（秒；不设 = 0 = 不延迟）
_FORCE_START_DELAY = float(os.environ.get("T1_W3_FORCE_START_DELAY") or 0)

OK, BAD, SKIP = [], [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("绿" if cond else "红", name,
                           ("  ← " + str(detail)) if detail else ""), flush=True)
    return bool(cond)


def section(t):
    print("\n=== %s ===" % t, flush=True)


# ── 一处 Python 3.6 以上的 unicode 打印保险 ──────────────────
for _s in (sys.stdout, sys.stderr):
    if _s and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# W3-A · uia_tree 坐标系标注 —— 纯读，不点不动
# ═══════════════════════════════════════════════════════════════
#
# ★ 4.1 之后的断言清单（原计划 §五 B 段的断言④「rect_local == rect − 原点」**已作废**，
#   换成「**没有任何** *_local 字段」+「标注键措辞与契约原文一致」）：
#   ① window_state() 有「坐标系」键；旧键一个没丢
#   ② tree() 顶层有「坐标系」键
#   ③ find() 顶层有「坐标系」键
#   ④ 全文 repo 的 uia_tree 返回值里**不存在**任何 *_local / 窗口原点 字段 ← 治「将来有人加回来」
#   ⑤ 标注键措辞与 contract\tools.json 的 conventions.coordinates 原文一致
#   ⑥ walk() 返回签名仍是 (nodes, truncated) 二元组，core.py:1657 那条用法照旧能跑
#   ⑦ 元素指纹 sha256 与节点数、eid 序列 —— **旧版/新版必须完全一致**（纯增量的铁证）


def _fixture_hwnd():
    """造一个最小真实窗口当靶子。为什么要造：判据不能依赖"恰好桌面上开着什么窗口"，
    否则换台机器/换个时刻这条判据就飘了。**只建一个普通顶层窗，不点不动、不抢焦点。**"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    u.CreateWindowExW.restype = wt.HWND
    u.CreateWindowExW.argtypes = [ctypes.c_ulong, wt.LPCWSTR, wt.LPCWSTR,
                                   ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE,
                                   ctypes.c_void_p]
    u.DefWindowProcW.restype = ctypes.c_ssize_t
    u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t,
                                 ctypes.c_ssize_t]
    u.RegisterClassW.argtypes = [ctypes.c_void_p]
    u.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
    CLS = "AgentCtlSelftestFixture"
    hinst = k.GetModuleHandleW(None)

    class WNDCLASS(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                    ("hCursor", ctypes.c_void_p),
                    ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", wt.LPCWSTR),
                    ("lpszClassName", wt.LPCWSTR)]

    def proc(h, m, w, l):
        return u.DefWindowProcW(h, m, w, l)
    PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                              ctypes.c_size_t, ctypes.c_ssize_t)(proc)
    wc = WNDCLASS()
    wc.lpfnWndProc = ctypes.cast(PROC, ctypes.c_void_p)
    wc.hInstance = hinst
    wc.lpszClassName = CLS
    try:
        u.RegisterClassW(ctypes.byref(wc))
    except Exception:
        pass                                   # 已注册（重跑）就沿用
    hwnd = u.CreateWindowExW(0, CLS, "T1窗3自证夹具", 0x00CF0000,  # WS_OVERLAPPEDWINDOW
                             300, 300, 520, 360, None, None, hinst, None)
    if not hwnd:
        raise RuntimeError("夹具窗口建不出来")
    # 塞一个 STATIC 子窗：UIA 才认得出"有文字的节点"（tree 默认 roles 含 text）
    u.CreateWindowExW(0, "STATIC", "T1窗3自证靶标 ABC", 0x50010000,
                      20, 40, 400, 40, wt.HWND(hwnd), None, hinst, None)
    u.ShowWindow(wt.HWND(hwnd), 5)               # SW_SHOW：只为拿到真实尺寸
    # ⛔ 刻意**不**调 SetForegroundWindow —— 判据不需要前台，
    #    而且用例14 要验的就是"边框不抢焦点"，这里先把用户的焦点抢了就没法验了。
    return int(hwnd), (u, PROC, CLS, hinst, int(hwnd))


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


def coords(src_dir=None, hwnd=None):
    src_dir = src_dir or SRC
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from agentctl import uia_tree
    section("W3-A · uia_tree（src=%s）" % src_dir)
    print("  uia_tree 来自：%s" % uia_tree.__file__, flush=True)

    keep = None
    if hwnd is None:
        hwnd, keep = _fixture_hwnd()
    print("  靶子窗口 hwnd=%s" % hwnd, flush=True)
    try:
        st = uia_tree.window_state(hwnd)
        tr = uia_tree.tree(hwnd, detail=True)
        fd = uia_tree.find(hwnd, "")

        note = getattr(uia_tree, "COORD_SYS", None)

        # ① window_state
        check("①a window_state() 有「坐标系」标注键", "坐标系" in st, sorted(st))
        old_keys = ["句柄", "最小化", "前台", "根矩形", "根矩形宽", "根矩形高", "说明"]
        check("①b window_state() 既有键一个没丢",
              all(k in st for k in old_keys),
              "缺：%s" % [k for k in old_keys if k not in st])
        # ②③ tree / find
        check("② tree() 顶层有「坐标系」标注键", "坐标系" in tr, sorted(tr))
        check("③ find() 顶层有「坐标系」标注键", "坐标系" in fd, sorted(fd))
        check("②b tree() 既有键一个没丢",
              all(k in tr for k in ("句柄", "节点数", "截断", "启用性未知", "耗时_ms", "树", "节点")),
              "缺：%s" % [k for k in ("句柄", "节点数", "截断", "启用性未知", "耗时_ms", "树", "节点")
                          if k not in tr])
        check("③b find() 既有键一个没丢",
              all(k in fd for k in ("命中", "命中数", "启用性未知", "扫过节点", "截断", "耗时_ms", "提示")),
              "缺：%s" % [k for k in ("命中", "命中数", "启用性未知", "扫过节点", "截断", "耗时_ms", "提示")
                          if k not in fd])

        # ④ ★ 原计划断言④已作废，换成「一个 *_local 都不许有」
        banned = ["rect_local", "center_local", "窗口原点", "原点", "origin"]
        found = []
        for src in (st, tr, fd):
            for k in src:
                if any(b in str(k) for b in banned):
                    found.append(k)
        for n in (tr.get("节点") or []) + (fd.get("命中") or []):
            for k in n:
                if any(b in str(k) for b in banned):
                    found.append(k)
        # render() 也不许有 origin 关键字参数了
        import inspect
        params = list(inspect.signature(uia_tree.render).parameters)
        check("④ 返回值与节点里**没有任何** *_local / 原点 / origin 字段",
              not found and "origin" not in params,
              "发现：%s；render 形参=%s" % (sorted(set(found)), params))

        # ⑤ 措辞与契约原文一致
        ctext = json.load(io.open(CONTRACT, encoding="utf-8"))["conventions"]["coordinates"]
        note = st.get("坐标系", "")
        check("⑤a 标注键提到「屏幕物理像素」", "屏幕物理像素" in note, note)
        key_frag = "窗口 rect 由 ctl_windows 实时提供，不许缓存"
        check("⑤b 标注键里有契约原句片段：%r" % key_frag[:18] + "…",
              key_frag in note, note)
        check("⑤c ★ 契约 conventions.coordinates 原文里也有同一句（措辞同源，不是各写各的）",
              key_frag in ctext, ctext)
        check("⑤d 契约原文确实说的是屏幕坐标（不是窗口局部）",
              ("屏幕坐标" in ctext) and ("局部" not in ctext), ctext)
        check("⑤e tree()/find()/window_state() 三处是**同一个常量**，不是三份各写各的",
              (tr.get("坐标系") == fd.get("坐标系") == note) and bool(note),
              "st=%r tr=%r fd=%r" % (note[:20], str(tr.get("坐标系"))[:20], str(fd.get("坐标系"))[:20]))

        # ⑥ walk() 签名仍是 (nodes, truncated)
        res = uia_tree.walk(hwnd)
        check("⑥ walk() 仍返回 (nodes, truncated) 二元组（core.py:1657 照旧能用）",
              isinstance(res, tuple) and len(res) == 2
              and isinstance(res[0], list) and isinstance(res[1], bool),
              type(res).__name__)

        # ⑦ 语义指纹（与 core._issue_capture_from_tree 同一个算法：(type, name[:40], tuple(rect))）
        nodes = res[0]
        sig = [(n.get("type"), (n.get("name") or "")[:40], tuple(n.get("rect") or ()))
               for n in nodes]
        blob = json.dumps(sig, ensure_ascii=False, sort_keys=True).encode("utf-8")
        print("  >>> 节点数=%d" % len(nodes), flush=True)
        print("  >>> eid序列=%s" % ",".join(n["eid"] for n in nodes[:20]), flush=True)
        print("  >>> 元素指纹sha256=%s" % hashlib.sha256(blob).hexdigest(), flush=True)
    finally:
        if keep:
            _destroy_fixture(keep)


# ═══════════════════════════════════════════════════════════════
# W3-B · ctrl_border --follow —— 全部在临时目录的假状态上跑
# ═══════════════════════════════════════════════════════════════
#
# ⛔ 本段**只读**，且**绝不碰真闸门文件**：
#   · 通行证 / 急停 flag 都在 <tmp>/auth_pass.json 与 <tmp>/_急停.flag
#   · 每一道边框子进程都**显式**传 --pass-file / --brake-flag 指到 <tmp>
#   · 边框日志也重定向到 <tmp>/ctrl_border.log（不污染真 %LOCALAPPDATA% 那份）


def _write_json(path, obj):
    io.open(path, "w", encoding="utf-8").write(json.dumps(obj, ensure_ascii=False))


def _enum_border_hwnds():
    """数当前有多少个边框窗（EnumWindows + GetClassNameW，和 WNDCLASS_NAME 对齐）。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPARAM)
    buf = []

    def cb(h, l):
        b = ctypes.create_unicode_buffer(256)
        if u.GetClassNameW(h, b, 256) and b.value in BORDER_CLS_ALL:
            buf.append(int(h))
        return 1
    p = ENUMPROC(cb)
    u.EnumWindows(p, 0)
    return buf


def _bitblt_edge(y=None, x=None, w=24, h=24):
    """GDI BitBlt 抓屏幕一角，返回 RGB 字节串。**和 perceive 同属屏幕 DC 抓取这条路**。"""
    import ctypes
    import ctypes.wintypes as wt
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    # ★ ctypes 在 64 位下不声明 restype/argtypes 就会把句柄截断成 32 位
    #   （踩过：SelectObject 报 "OverflowError: int too long to convert"）。
    u.GetDC.restype = ctypes.c_void_p
    u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                         ctypes.c_int]
    g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g.SelectObject.restype = ctypes.c_void_p
    g.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                         ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_uint]
    g.DeleteDC.argtypes = [ctypes.c_void_p]
    if x is None or y is None:
        SM_CY = 1                                # GetSystemMetrics(SM_CY) = 屏幕高
        # ★ GetSystemMetrics 在 **user32**，不在 gdi32（踩过：AttributeError）
        x, y = 0, u.GetSystemMetrics(SM_CY) - h   # 默认抓**左下角**（bottom 那条边）
    g.CreateCompatibleDC.restype = ctypes.c_void_p
    g.CreateCompatibleBitmap.restype = ctypes.c_void_p
    hdc = u.GetDC(None)
    try:
        mdc = g.CreateCompatibleDC(hdc)
        bmp = g.CreateCompatibleBitmap(hdc, w, h)
        g.SelectObject(mdc, ctypes.c_void_p(bmp))
        g.BitBlt(ctypes.c_void_p(mdc), 0, 0, w, h,
                 ctypes.c_void_p(hdc), x, y, 0x00CC0020)      # SRCCOPY
        class BMI(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                        ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]
        bi = BMI()
        bi.biSize = ctypes.sizeof(BMI)
        bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = w, -h, 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        g.GetDIBits(ctypes.c_void_p(mdc), ctypes.c_void_p(bmp), 0, h,
                    ctypes.cast(buf, ctypes.c_void_p),
                    ctypes.byref(bi), 0)                         # DIB_RGB_COLORS
        g.DeleteObject(ctypes.c_void_p(bmp))
        g.DeleteDC(ctypes.c_void_p(mdc))
        return bytes(buf)
    finally:
        u.ReleaseDC(None, hdc)


def _border_owner_pids():
    """边框窗口的属主进程集合 —— 用来判断"是不是还有个 --follow 在外头跑着"。
    ★ 判据必须能分辨"我自己起的"和"上一轮漏下的"：漏下的会占着单例互斥量，
      让本轮每个 spawn 都秒退（"已有一个在跑"），判据就变成在测空气。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    P = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPARAM)
    got = set()

    def cb(h, l):
        b = ctypes.create_unicode_buffer(256)
        if u.GetClassNameW(h, b, 256) and b.value in BORDER_CLS_ALL:
            pid = wt.DWORD(0)
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            got.add(int(pid.value))
        return 1
    u.EnumWindows(P(cb), 0)
    return got


# ═══════════════════════════════════════════════════════════════
# 窗F（2026-10-18）：**就绪轮询** —— 替掉用例12 的固定 sleep(2.5)
# ═══════════════════════════════════════════════════════════════
#
# 根因（窗C 用红/绿/单跑三份留档钉死）：原流程是
#       spawn 边框 -> 固定 sleep(2.5) -> 抓屏 -> 数边框色像素
# 而 ctrl_border.py:1383 的边框窗是「**初始隐藏、等判定**」，由 0.2s 轮询才 ShowWindow；
# 红跑与绿跑的「已建边框」日志计数**逐字同为 7 次** => 两次 spawn 都建出了 8 个窗，
# 缺的只是**抓屏那一瞬间屏幕上还没有像素**（首帧上屏晚于 2.5s，负载高时）。
# => 固定 sleep 是拿「猜的时间」当「就绪的证据」—— 猜错了不是判红，是判运气。

READY_LOG_MARK = "已建 8 个边框窗口"      # ctrl_border.py:1383 的原话
READY_N_WINDOWS = 8                       # 主色 4 + 1px 反色描边 4（窗F 起）
READY_TIMEOUT_SEC = 10.0                  # 派单：上限 10s
READY_INTERVAL_SEC = 0.2                  # 派单：间隔 200ms


def _descendant_pids(pid):
    """pid 这棵进程树（含自己）。

    ★ 必须带子孙：`--follow` 起的是**守望进程**，真正画窗的是它再拉起的那个子进程
      （ctrl_border.py:790 `start_follow`）。只按 p.pid 数，属主对不上，会恒为 0。
    """
    got = {int(pid)}
    try:
        import psutil
        got |= set(c.pid for c in psutil.Process(int(pid)).children(recursive=True))
    except Exception:
        pass
    return got


def _enum_border_hwnds_of(pids):
    """只数**属主 pid 在 pids 里**的边框窗。

    ★ `_enum_border_hwnds()` 不分属主 —— 屏幕上要是还有别的边框窗（上一轮漏下的、
      用户自己起的），数出来的就不是「本轮建了几个」。派单要求「按属主 pid 自数」，
      就是为了不让上一轮的残留把本轮的等待喂饱。
    """
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    P = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPARAM)
    buf = []

    def cb(h, l):
        b = ctypes.create_unicode_buffer(256)
        if u.GetClassNameW(h, b, 256) and b.value in BORDER_CLS_ALL:
            pid = wt.DWORD(0)
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if int(pid.value) in pids:
                buf.append(int(h))
        return 1
    u.EnumWindows(P(cb), 0)
    return buf


def _any_visible(hwnds):
    """这批窗里有没有**已经真正显示**的（IsWindowVisible）。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    u.IsWindowVisible.argtypes = [wt.HWND]
    u.IsWindowVisible.restype = wt.BOOL
    return any(u.IsWindowVisible(wt.HWND(int(h))) for h in hwnds)


def _kill_border_owner_pids(pids, exclude=()):
    """把这批 pid **自己拥有的**边框窗对应的进程杀掉（属主 pid 在 pids 里且不在 exclude）。

    ★ 为什么用例12 收尾要显式这么做：spawn 出来的 --follow 带的是
      **--parent-pid = 自证进程**（防脚本崩了留孤儿），不是那个守望进程自己
      ⇒ 调 p.terminate() **杀不到真正画窗的孙进程**。
      平时看不出来 —— 后面 15 系列的 stop_follow 会顺手把它收掉；
      一旦给用例12 加启动延迟（本文件的 T1_W3_FORCE_START_DELAY），清理就赶不上
      建窗，先后顺序被颠倒 ⇒ 15f/15h/15l/16 一起红（实测：绿 63 / 红 4）。
      那几条红与「就绪轮询对不对」无关，是延迟撞出来的收尾缺口 —— 这里按属主收干净。
    """
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    P = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPARAM)
    victims = set()
    pset = set(int(x) for x in pids)

    def cb(h, l):
        b = ctypes.create_unicode_buffer(256)
        if u.GetClassNameW(h, b, 256) and b.value in BORDER_CLS_ALL:
            pid = wt.DWORD(0)
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if int(pid.value) in pset and int(pid.value) not in set(exclude):
                victims.add(int(pid.value))
        return 1
    u.EnumWindows(P(cb), 0)
    for vp in sorted(victims):
        try:
            os.kill(vp, 9)
        except Exception:
            pass
    return sorted(victims)


def _rlog_since(log, off):
    """读日志**从字节偏移 off 起**新追加的内容。

    ★ 派单点名：「按本轮日志文件/偏移判断，**不要数历史累计**」。本文件是全程 append 的，
      前面用例10/11/16 都往里写过「已建 8 个边框窗口」=> 直接 `in rlog()` 永远为真，
      就绪条件①会变成永远满足，等于没有。
    """
    try:
        with open(log, "rb") as f:
            f.seek(int(off))
            return f.read().decode("utf-8", "replace")
    except Exception:
        return ""


def _wait_border_ready(log, off, pid, need_pixels, count_pixels,
                       timeout=READY_TIMEOUT_SEC, interval=READY_INTERVAL_SEC):
    """有界就绪轮询：**三件齐**才进断言；超时**如实红**并说清哪件没等到、等了多久。

    三件：
      (1) 本轮日志（off 之后新追加的那段）出现「已建 8 个边框窗口」
      (2) 按属主 pid 自数：属于本轮 spawn 那棵进程树的 AgentCtlCtrlBorder* 窗 = 8
      (3) 试抓命中：角里数到的边框色像素 > 0

    ★ (3)**只对不带 WDA 那轮**开（need_pixels=True 时才查）：带 WDA 时窗被
      SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) 排除在截图外
      （ctrl_border.py:1081-1083）=> **设计上就抓不到**。拿它当就绪条件，
      带 WDA 那轮会每轮空等满 10 秒再报红 —— 而「抓不到」恰恰是那一轮的**期望结果**，
      不是缺陷。带 WDA 那轮改等「窗已真正显示」(2b)，否则「带WDA 抓不到」
      有可能只是因为窗压根还没画出来，断言会**假绿**。

    返回 (就绪?, 用了多少秒, 诊断 dict)。
    """
    t0 = time.monotonic()
    diag = {"log": False, "nwin": 0, "px": None}
    轮询次数 = 0
    while True:
        轮询次数 += 1
        pids = _descendant_pids(pid)             # 每轮重取：孙进程可能刚起来
        hs = _enum_border_hwnds_of(pids)
        c1 = READY_LOG_MARK in _rlog_since(log, off)     # (1) 本轮日志
        c2 = len(hs) == READY_N_WINDOWS                    # (2) 按属主 pid 自数
        c2b = _any_visible(hs)                             # (2b) 真的显示出来了
        c3, px = True, None
        if need_pixels:                                    # (3) 试抓命中
            px = count_pixels()
            c3 = px > 0
        # ★ 窗F 补：把「轮询到底有没有在等」变成**看得见的数** ——
        #   次数 / 等待毫秒 / 三件各等到没。以前只能从耗时反推，
        #   空载机器上「等了 0 次就绿」和「等了 30 次才绿」长得一模一样。
        diag = {"log": c1, "nwin": len(hs), "px": px, "vis": bool(hs) and _any_visible(hs),
                "次数": 轮询次数,
                "等待毫秒": int((time.monotonic() - t0) * 1000),
                "间隔毫秒": int(interval * 1000), "上限毫秒": int(timeout * 1000),
                "用了像素条件": bool(need_pixels)}
        if c1 and c2 and (c3 if need_pixels else c2b):
            diag["等待毫秒"] = int((time.monotonic() - t0) * 1000)
            return True, time.monotonic() - t0, diag
        if time.monotonic() - t0 >= timeout:
            diag["等待毫秒"] = int((time.monotonic() - t0) * 1000)
            return False, time.monotonic() - t0, diag
        time.sleep(interval)


def _ready_diag(tag, secs, diag, added_bytes):
    """把「没等到哪件、等了多久、当时数到几个窗/几个像素」拼成一句人话。"""
    miss = []
    if not diag.get("log"):
        miss.append("(1)本轮日志没有「%s」" % READY_LOG_MARK)
    if diag.get("nwin") != READY_N_WINDOWS:
        miss.append("(2)按属主 pid 自数边框窗=%d（要 %d）"
                    % (diag.get("nwin") or 0, READY_N_WINDOWS))
    if diag.get("px") is not None and not (diag.get("px") or 0) > 0:
        miss.append("(3)试抓命中像素=0（边框还没上屏）")
    if not diag.get("vis"):
        miss.append("(2b)边框窗还没真正显示出来")
    return ("%s 就绪超时 %.1fs（上限 %.0fs / 间隔 %.2fs）：%s；本轮日志新追加 %d 字节；"
            "轮询 %d 次 / 等了 %d ms"
            % (tag, secs, READY_TIMEOUT_SEC, READY_INTERVAL_SEC,
               "；".join(miss) or "（三件都齐了却没进断言 —— 判据自身有问题）",
               added_bytes, int(diag.get("次数") or 0),
               int(diag.get("等待毫秒") or 0)))

# ═══════════════════════════════════════════════════════════════
# 窗G（2026-10-18）：13 / 16 的就绪门 —— 复用窗F 的零件，不重造
# ═══════════════════════════════════════════════════════════════
# 病因与 12 同族：13/16 也是「spawn → 固定 sleep(2.5) → 数窗」。
# 窗F 实测（T1_W3_FORCE_START_DELAY=3 那一跑）13a/13b/16 全红、hs=0。


def _logsize(log):
    """当前日志字节数 —— 当作「本轮起点」用（读新内容一律配 `_rlog_since`）。"""
    try:
        return os.path.getsize(log)
    except Exception:
        return 0


def _wait_case_ready(case, log, log_off, p):
    """13/16 用的就绪门：现役走**有界轮询**，注入走「固定 sleep + 事后自判」。

    ★ 就绪条件**按各自关心的那件事**定，**不用像素条件**：
        13 问的是「窗口在不在、显示没显示」（WM_NCHITTEST 是发给窗口的消息），
        16 问的是「这一轮建没建出 8 个窗」（治僵尸窗口类 / 互斥量漏释放）。
        两者要的都是**窗本身**，不是「抓屏抓到边框色」⇒ need_pixels 恒为 False。
        硬套像素条件＝拿截图当就绪凭证，与这两条的断言对不上。

    返回 (就绪?, 属主过滤后的窗列表, 秒数, 诊断 dict)。
    """
    inject = case in _INJECT_CASES
    if inject:
        # ── 注入分支：**自己判就绪**，不齐就是红。
        #   绝不「sleep 完把 ready 硬写成 True」—— 那样注入证明不了任何事
        #   （窗F 已踩过：空载机器上 0.2s 就够用，注入绿，白注）。
        _inj = float(_INJECT_FIXED_SLEEP)
        time.sleep(_inj)
        secs = _inj
        hs = _enum_border_hwnds_of(_descendant_pids(p.pid))
        diag = {"log": READY_LOG_MARK in _rlog_since(log, log_off),
                "nwin": len(hs),
                "px": None,
                "vis": bool(hs) and _any_visible(hs),
                "次数": 1,
                "等待毫秒": int(_inj * 1000),
                "间隔毫秒": 0,
                "上限毫秒": 0,
                "用了像素条件": False}
        ready = bool(diag["log"] and diag["nwin"] == READY_N_WINDOWS
                     and diag["vis"])
        added = _logsize(log) - log_off
        check("%s-就绪 ★ 反向注入：固定 sleep(%.2f) 之后三件齐"
              "（这一条绿 ⇒ 说明 sleep 已经够长，注入**证明不了**轮询在干活）"
              % (case, _inj), ready,
              "" if ready else
              ("固定 sleep(%.2f) 之后三件不齐 ⇒ 这一版**没在等**：%s；"
               "本轮日志新追加 %d 字节。要确定性红请配 T1_W3_FORCE_START_DELAY=3"
               "（边框晚 3s 才起，现役轮询照样绿）"
               % (_inj, _ready_diag(case, _inj, diag, added), added)))
        print("  [%s] ⚠ 注入：就绪轮询被旁路，改成固定 sleep(%.2f)·探了 1 次"
              % (case, _inj), flush=True)
    else:
        ready, secs, diag = _wait_border_ready(
            log, log_off, p.pid, False, None)      # need_pixels=False
        hs = _enum_border_hwnds_of(_descendant_pids(p.pid))
        added = _logsize(log) - log_off
        if not ready:
            check("%s-就绪 ★ 三件齐才进断言（上限 %.0fs / 间隔 %.2fs）"
                  % (case, READY_TIMEOUT_SEC, READY_INTERVAL_SEC),
                  False, _ready_diag(case, secs, diag, added))
    # ★ 两处都要「看得见的数」：探了几次 / 累计等了多久 / 三件逐条 ✔✘
    print("  [%s] 轮询可观测：探了 %d 次 / 累计等待 %d ms / 间隔 %d ms / 上限 %d ms；"
          "三件 = (1)日志 %s (2)按属主 pid 自数窗 %d/%d %s (3)像素 不适用（本用例不靠像素）"
          % (case, int(diag.get("次数") or 0), int(diag.get("等待毫秒") or 0),
             int(diag.get("间隔毫秒") or 0), int(diag.get("上限毫秒") or 0),
             "✔" if diag.get("log") else "✘",
             diag.get("nwin") or 0, READY_N_WINDOWS,
             "（已显示）✔" if diag.get("vis") else "（还没显示）✘"),
          flush=True)
    return ready, hs, secs, diag




def _wait_gate(case, probe, descs,
               timeout=READY_TIMEOUT_SEC, interval=READY_INTERVAL_SEC):
    """通用有界就绪门（窗G 给 13/16 用的 _wait_case_ready 是它的一个特例，这里泛化给 14/15/17）。

    probe() 必须返回一个 dict：{条件名: (满足?, 当时的值字符串)}，**顺序即展示顺序**。
    descs 是给人看的一句话说明，会进诊断文案。

    现役：有界轮询到三件全齐；超时 ⇒ 交回调用方判红。
    注入：**固定 sleep 之后自己探一次**，不齐 ⇒ 直接红（并说明缺哪件）。
    两个分支都**没有**「sleep 完把 ready 硬写成 True」——那正是窗F 修掉的原病。
    """
    keys = list(probe().keys())

    def _fmt(st):
        return "；".join("%s%s（%s）" % (k, "✔" if st[k][0] else "✘", descs.get(k, ""))
                         for k in keys)

    inject = case in _INJECT_CASES
    t0 = time.monotonic()
    if inject:
        _inj = float(_INJECT_FIXED_SLEEP)
        time.sleep(_inj)
        st = probe()
        secs = _inj
        次数, 等待ms = 1, int(_inj * 1000)
    else:
        次数 = 0
        while True:
            次数 += 1
            st = probe()
            secs = time.monotonic() - t0
            if all(v[0] for v in st.values()):
                break
            if secs >= timeout:
                break
            time.sleep(interval)
        等待ms = int(secs * 1000)
    ready = all(v[0] for v in st.values())
    print("  [%s] %s可观测：探了 %d 次 / 累计等待 %d ms / 间隔 %d ms / 上限 %d ms；三件逐条 = %s"
          % (case,
             ("注入·固定 sleep(%.2f)·" % float(_INJECT_FIXED_SLEEP)) if inject else "",
             次数, 等待ms, int(interval * 1000), int(timeout * 1000), _fmt(st)),
          flush=True)
    if inject:
        check("%s-就绪 ★ 反向注入：固定 sleep(%.2f) 之后三件齐"
              "（这一条绿 ⇒ 说明 sleep 已经够长，注入**证明不了**轮询在干活）"
              % (case, float(_INJECT_FIXED_SLEEP)), ready,
              "" if ready else
              ("固定 sleep(%.2f) 之后三件不齐 ⇒ 这一版**没在等**：%s"
               "（要确定性红请配 T1_W3_FORCE_START_DELAY=3：边框晚 3s 才起）"
               % (float(_INJECT_FIXED_SLEEP), _fmt(st))))
    return ready, secs, {"次数": 次数, "等待毫秒": 等待ms, "项": st}


def _rlog_all(log):
    try:
        with open(log, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except Exception:
        return ""


def _wait_wire_border_started(case, log, timeout=READY_TIMEOUT_SEC,
                              interval=READY_INTERVAL_SEC):
    """15b 的就绪门：**这一轮** server/mcp 把边框拉起来并写进日志。

    三件（按 15b 自己断言的东西定）：
      (1) 沙箱日志出现「边框已启动 pid=」—— 15b 断言的就是这行；
      (2) 那个 pid 还活着 —— 免得读到一行「起来了但立刻退了」的历史；
      (3) 按**属主 pid** 自数边框窗 = 8 —— 接线真的把窗画出来了，不是只打了行日志。
    ★ 不用像素条件：15b 判的是「接线有没有接上」，不是「抓屏抓不抓得到边框色」。
    """
    def probe():
        txt = _rlog_all(log)
        m = re.search(r"边框已启动 pid=(\d+)", txt or "")
        pid = int(m.group(1)) if m else 0
        hs = _enum_border_hwnds_of(_descendant_pids(pid)) if pid else []
        return {"(1)日志有「边框已启动 pid=」": (bool(m), (m.group(0) if m else "无")),
                "(2)那个 pid 还活着": (bool(pid) and _proc_alive(pid),
                                      "pid=%s" % (pid or "无")),
                "(3)按属主 pid 自数窗=8": (len(hs) == READY_N_WINDOWS, "窗=%d" % len(hs))}
    return _wait_gate(case, probe,
                      {"(1)日志有「边框已启动 pid=」": "15b 断言的就是这行",
                       "(2)那个 pid 还活着": "免得读到历史行",
                       "(3)按属主 pid 自数窗=8": "接线真的画出窗了"},
                      timeout=timeout, interval=interval)

_ALLRUN_LOG = {"text": ""}


def _mark_allrun_log(log):
    """把 border() 段的沙箱日志快照进 _ALLRUN_LOG —— 供 17 的「本段确实跑过 spawn」那一条用。"""
    try:
        _ALLRUN_LOG["text"] = _rlog_all(log)
    except Exception:
        _ALLRUN_LOG["text"] = ""
    return _ALLRUN_LOG["text"]


def _wait_teardown_gone(case, timeout=READY_TIMEOUT_SEC, interval=READY_INTERVAL_SEC):
    """**收干净**的有界等待 —— 用在 15f/15h/15l 与用例 17。

    ★ 这几处断言的都是「**已经收干净了**」，不是「建出来了」：早等晚等的结果都是
      「屏幕上 0 个边框窗」。所以这里等的是**收尾完成**，不是**建窗出现**。
    ★ **绝不能**套 _wait_border_ready 那套「窗=8 + 已显示」：那会把「还没建出来」
      误判成「没等够」——正是派单点名要防的「把『已收干净』当成『没建出来』」。
    """

    def probe():
        left = _enum_border_hwnds()
        owners = _border_owner_pids()
        _ran = ("已建 8 个边框窗口" in _ALLRUN_LOG.get("text", ""))
        return {"收尾·屏幕无残留边框窗": (len(left) == 0, "EnumWindows=%d" % len(left)),
                "收尾·无边框属主进程": (len(owners) == 0, "属主=%s" % (sorted(owners) or "无")),
                "收尾·本段确实跑过 spawn": (_ran, "日志里有本段建窗记录=%s" % _ran)}
    return _wait_gate(case, probe,
                      {"收尾·屏幕无残留边框窗": "已收干净（不是没建出来）",
                       "收尾·无边框属主进程": "属主进程也退了",
                       "收尾·本段确实跑过 spawn": "日志里要有本段建窗记录"},
                      timeout=timeout, interval=interval)
def _proc_alive(pid):
    import ctypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    h = k.OpenProcess(0x1000, False, int(pid))     # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    k.CloseHandle(ctypes.c_void_p(h))
    return bool(ok) and code.value == 259       # STILL_ACTIVE


def _wait_gone(pid, timeout=12.0):
    """等 pid 消失。返回 (消失了?, **用了几秒**) —— 秒数是耗时，不是绝对时钟。"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if not _proc_alive(pid):
            return True, time.monotonic() - t0
        time.sleep(0.2)
    return False, time.monotonic() - t0


def border(src_dir=None, keep_border=False):
    src_dir = src_dir or SRC
    # 7d 要**在本进程内** import ctrl_border 验 _stderr_is_log，所以也得进 sys.path
    # （其它用例都是起子进程，只靠 env 的 PYTHONPATH）。
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    env = dict(os.environ)
    env["PYTHONPATH"] = src_dir + (os.pathsep + env["PYTHONPATH"]
                                     if env.get("PYTHONPATH") else "")
    tmp = tempfile.mkdtemp(prefix="T1_W3_border_")
    passf = os.path.join(tmp, "auth_pass.json")
    brakef = os.path.join(tmp, "_急停.flag")
    log = os.path.join(tmp, "ctrl_border.log")
    env["AGENTCTL_BORDER_LOG"] = log
    env.pop("AGENTCTL_BORDER", None)        # 别让环境里遗留的开关干扰判据
    # ★ 判据用**自己的**单例互斥量。本机可能有 agentctl 服务正在跑并占着默认那个
    #   （实测现场：一个活的 agentctl.mcp 拉起了 --follow 占着它）⇒ 判据起的每个
    #   --follow 都会正确地秒退「已有一个在跑」。那不是判据错，是**在跟生产实例抢**。
    #   AGENTCTL_BORDER_MUTEX 是产品里留的**测试专用**覆盖口，生产默认一字不变。
    mutex_name = "Local\\AgentCtlCtrlBorderFollow__T1W3_%d" % os.getpid()
    env["AGENTCTL_BORDER_MUTEX"] = mutex_name

    def run(extra, timeout=25, poll="0.2"):
        cmd = [sys.executable, "-X", "utf8", "-m", "agentctl.ctrl_border",
               "--poll", poll, "--pass-file", passf, "--brake-flag", brakef] + extra
        return subprocess.run(cmd, env=env, cwd=tmp, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)

    def spawn(extra, delay=0.0, label="用例12"):
        # ★ 每个 spawn 都带 --parent-pid（本自证进程）⇒ 万一脚本中途崩了、
        #   finally 没跑到，子进程也会跟着本进程一起死，**不会留下孤儿边框**。
        #   （实测踩过：脚本崩在中途 ⇒ 漏下一个 --follow 占着单例互斥量，
        #     后面每一轮 spawn 都秒退，判据全在测空气。）
        argv = ["--poll", "0.2", "--pass-file", passf, "--brake-flag", brakef,
                "--parent-pid", str(os.getpid())] + extra
        delay = float(delay or 0.0)
        if delay > 0:
            # ★ 窗F 补：延迟发生在 **import 之前** ⇒ 那段时间里边框窗压根还不存在。
            #   runpy.run_module(..., run_name="__main__") 与 `python -m` 等价
            #   （含 __name__ == "__main__" 那条入口），所以产品行为一字不差。
            _wrap = ("import time,runpy,sys;time.sleep(%r);"
                     "sys.argv=['agentctl.ctrl_border']+%r;"
                     "runpy.run_module('agentctl.ctrl_border',run_name='__main__')"
                     % (delay, argv))
            cmd = [sys.executable, "-X", "utf8", "-c", _wrap]
            print("  [%s] ⚠ 强制启动延迟 %.1fs：边框子进程 import 前先睡这么久"
                  "（现役轮询会等得到；注入的固定 sleep 必然抓空）" % (label, delay),
                  flush=True)
        else:
            cmd = [sys.executable, "-X", "utf8", "-m", "agentctl.ctrl_border"] + argv
        lf = open(log, "a", encoding="utf-8", errors="replace")
        try:
            return subprocess.Popen(cmd, env=env, cwd=tmp, stdout=lf, stderr=lf,
                                    stdin=subprocess.DEVNULL)
        finally:
            lf.close()

    def rlog():
        try:
            return io.open(log, encoding="utf-8", errors="replace").read()
        except Exception:
            return ""

    procs = []
    try:
        section("W3-B · ctrl_border（src=%s）" % src_dir)
        stale = _border_owner_pids()
        print("  [前置] 屏幕上现有的边框属主进程：%s（判据用的是自己的互斥量 %s，"
              "**不与生产实例抢**）" % (sorted(stale) or "无", mutex_name), flush=True)
        check("前置 · 判据用自己的单例互斥量，不与在跑的生产实例抢"
              "（默认互斥量被占着时，本段判据仍能测到自己的逻辑）",
              bool(mutex_name), mutex_name)
        print("  临时假状态目录：%s" % tmp, flush=True)
        print("  ★ 全程只读：真 auth_pass.json 与真急停 flag 本脚本一次也没碰", flush=True)
        FUTURE = 4102444800        # 2099-12-31，代替"永不过期"

        # 1) scopes=["A"] ⇒ 浅蓝 A
        # ★ 窗F（2026-10-15）：四档改成逐档加深的蓝并逐档加厚，下面三条的
        #   期望色跟着换成定板值（A #8FD8FF / C #1565C0 / D #0B3D91）。
        #   判据钉的是**档位→颜色**这条映射，不是某次刷漆；改色必须同步这里，
        #   且配色另有 tests\_2h_wF_配色冻结判据.py 钉死五个 RGB（改了就红）。
        _write_json(passf, {"scopes": ["A"], "expires_at": FUTURE})
        r = run(["--once"])
        print("  [用例1 原始输出] %s" % r.stdout.strip(), flush=True)
        check("1 scopes=[A] ⇒ 浅蓝/A（#8FD8FF）",
              "RGB=(143, 216, 255)" in r.stdout and r.returncode == 0, r.stdout.strip())

        # 2) scopes=["A","C"] ⇒ 取最高档 中深蓝/C
        _write_json(passf, {"scopes": ["A", "C"], "expires_at": FUTURE})
        r = run(["--once"])
        print("  [用例2 原始输出] %s" % r.stdout.strip(), flush=True)
        check("2 scopes=[A,C] ⇒ **取最高档** 中深蓝/C（#1565C0）",
              "RGB=(21, 101, 192)" in r.stdout, r.stdout.strip())

        # 3) scopes=[A,B,C,D] ⇒ 深蓝/D
        _write_json(passf, {"scopes": ["A", "B", "C", "D"], "expires_at": FUTURE})
        r = run(["--once"])
        print("  [用例3 原始输出] %s" % r.stdout.strip(), flush=True)
        check("3 scopes=[A,B,C,D] ⇒ 深蓝/D（#0B3D91）",
              "RGB=(11, 61, 145)" in r.stdout, r.stdout.strip())

        # 4) expires_at 设成过去 ⇒ 红，理由含「过期」
        _write_json(passf, {"scopes": ["A", "C"], "expires_at": 1000})
        r = run(["--once"])
        print("  [用例4 原始输出] %s" % r.stdout.strip(), flush=True)
        check("4 expires_at 在过去 ⇒ 红，理由含「过期」",
              "状态=red" in r.stdout and "过期" in r.stdout, r.stdout.strip())

        # 5) consumed=true ⇒ 红
        _write_json(passf, {"scopes": ["A", "C"], "expires_at": FUTURE,
                             "consumed": True})
        r = run(["--once"])
        print("  [用例5 原始输出] %s" % r.stdout.strip(), flush=True)
        check("5 consumed=true ⇒ 红，理由含「用完」",
              "状态=red" in r.stdout and "用完" in r.stdout, r.stdout.strip())

        # 6) 点亮临时 brake flag ⇒ 红（且压过档位）
        _write_json(passf, {"scopes": ["D"], "expires_at": FUTURE})
        io.open(brakef, "w").close()
        r = run(["--once"])
        print("  [用例6 原始输出] %s" % r.stdout.strip(), flush=True)
        check("6 临时急停 flag 存在 ⇒ 红，且压过 D 档（优先级最高）",
              "状态=red" in r.stdout and "急停" in r.stdout, r.stdout.strip())

        # 7) 删掉 pass ⇒ 隐藏（idle 默认）＋ 日志里必须有「无通行证 ⇒ 隐藏」
        os.remove(brakef)
        os.remove(passf)
        r = run(["--once"])
        print("  [用例7 原始输出] %s" % r.stdout.strip(), flush=True)
        lg = rlog()
        check("7a 无通行证 ⇒ 隐藏（--idle 默认 hide）",
              "状态=hidden" in r.stdout, r.stdout.strip())
        check("7b ★ 隐藏态**必须**往日志写一行「无通行证 ⇒ 隐藏」"
              "（DSH 追加的硬要求，否则「看不见边框」会被误判成急停）",
              "无通行证 ⇒ 隐藏" in lg, "日志尾部：%s" % lg[-200:])

        # 7c --idle red 可翻成常红
        r = run(["--once", "--idle", "red"])
        check("7c --idle red 能把「无证」翻成红（不改代码）",
              "状态=red" in r.stdout and "--idle=red" in r.stdout, r.stdout.strip())

        # 7d 日志不重复：被 spawn 时 stderr 就是日志文件本身，_emit 不能再 append 一次
        import io as _io
        p2 = os.path.join(tmp, "dedup.log")
        f2 = open(p2, "a", encoding="utf-8")
        # ★ 必须先把 fd 1/2 存下来：os.dup2 是**永久**换掉本进程的标准输出，
        #   不还原的话这个自证脚本后面所有的 print 都进那个文件了（自己判自己瞎）。
        _sv_out, _sv_err = os.dup(1), os.dup(2)
        os.dup2(f2.fileno(), 1)
        os.dup2(f2.fileno(), 2)
        try:
            from agentctl import ctrl_border as _cb
            # ★ 用 getattr 而不是直接取属性：旧版（HEAD）压根没有这个函数 ——
            #   直接取会 AttributeError 把整个判据中断在 7d，后面的 8~15 就跑不到了
            #   （改前红必须**整份**跑完，才看得出哪些判据真的能分辨新旧）。
            _fn = getattr(_cb, "_stderr_is_log", None)
            _emit_fn = getattr(_cb, "_emit", None)
            if _fn is None or _emit_fn is None:
                _same = "旧版没有 _stderr_is_log/_emit 这两个函数"
            else:
                _same = _fn(p2)
                _emit_fn("DEDUP探针行")
        finally:
            f2.flush()
            f2.close()
            os.dup2(_sv_out, 1)
            os.dup2(_sv_err, 2)
            os.close(_sv_out)
            os.close(_sv_err)
        _txt = _io.open(p2, encoding="utf-8").read()
        print("  [用例7d] stderr 是日志=%s，「DEDUP探针行」在日志里出现 %d 次"
              % (_same, _txt.count("DEDUP探针行")), flush=True)
        check("7d ★ 日志**不重复**：stderr 已被重定向进日志时 _emit 只写一遍"
              "（否则同一句话出现两遍，读者以为出了两次事）",
              _same is True and _txt.count("DEDUP探针行") == 1,
              "same=%r 次数=%d" % (_same, _txt.count("DEDUP探针行")))
        # 旧版跑到这里时 7d 是红的，接着要能继续往下跑，所以别在这里抛。

        # 8) 坏 JSON ⇒ 红 ＋ 日志有「解析失败」
        io.open(passf, "w", encoding="utf-8").write("{ 这不是 json")
        r = run(["--once"])
        print("  [用例8 原始输出] %s" % r.stdout.strip(), flush=True)
        check("8a 坏 JSON ⇒ 红（fail-closed）", "状态=red" in r.stdout, r.stdout.strip())
        check("8b 日志里有「JSON 解析失败」",
              "JSON 解析失败" in rlog(), "日志尾部：%s" % rlog()[-200:])
        # 8c 带 BOM 的通行证 ⇒ 也红。★ 这里的口径**必须**与 gate.py:599 的
        #     open(path, encoding="utf-8") 一字不差：BOM 文件在闸门那里读不出来、
        #     等于没证 ⇒ 全拒；边框若"更宽容"就会冒出「边框说没事、闸门说不行」。
        io.open(passf, "wb").write(b"\xef\xbb\xbf" +
                                    json.dumps({"scopes": ["C"],
                                                "expires_at": FUTURE}).encode("utf-8"))
        r = run(["--once"])
        check("8c ★ 带 BOM 的通行证 ⇒ 红（与 gate 同口径，不另立更宽松的规则）",
              "状态=red" in r.stdout and "解析失败" in r.stdout, r.stdout.strip())


        # 9) ★ 老用法 --tier B --duration 1 必须照旧能跑（minicode 端到端脚本指着它）
        os.remove(passf)
        cmd9 = [sys.executable, "-X", "utf8", "-m", "agentctl.ctrl_border",
                "--tier", "B", "--duration", "1"]
        r9 = subprocess.run(cmd9, env=env, cwd=tmp, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
        print("  [用例9 原始输出 rc=%d]" % r9.returncode, flush=True)
        for ln in (r9.stdout or "").splitlines():
            print("      %s" % ln, flush=True)
        check("9 ★ 老用法 --tier B --duration 1 照旧 rc=0 且输出原样（老脚本/端到端依赖）",
              r9.returncode == 0
              and "档位：B 级·点击滚动" in r9.stdout
              # ★ 窗F：厚度改成**按档**（B=4，不再是全局 8），且每条边现在
              #   有主色 + 1px 反色描边两个窗口 ⇒ 每屏 8 个。老用法的**参数**没变，
              #   变的是它画出来什么样 —— 这里跟的是新口径。
              and "已画边框：显示器 1 块 × 每屏 4 条 = 8 个窗口" in r9.stdout
              and "厚度 4 px" in r9.stdout
              and "--duration=1s 到点自动退出" in r9.stdout,
              "rc=%d out=%r" % (r9.returncode, r9.stdout))
        check("9b 老用法 --list-monitors 照旧 rc=0",
              subprocess.run([sys.executable, "-X", "utf8", "-m",
                              "agentctl.ctrl_border", "--list-monitors"],
                             env=env, cwd=tmp, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=30).returncode == 0)
        check("9c 老用法 --brake 照旧 rc=0",
              subprocess.run([sys.executable, "-X", "utf8", "-m",
                              "agentctl.ctrl_border", "--brake", "--duration", "1"],
                             env=env, cwd=tmp, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=30).returncode == 0)

        # 10) 单例：先起一个 --follow 停住，再起第二个
        _write_json(passf, {"scopes": ["B"], "expires_at": FUTURE})
        p1 = spawn(["--follow"])
        procs.append(p1)
        n0 = None
        # 8 s 足够：实测同机 spawn + 建这几个窗口 < 1 s。原来 15 s 是保守值，
        # 但旧版（--follow 不存在）要干等满，白白拖慢改前红那一轮。
        end = time.monotonic() + 8
        while time.monotonic() < end:
            # ★ 窗F：每屏 8 个窗口（主色 4 + 1px 反色描边 4），不是 4 个。
            if len(_enum_border_hwnds()) >= 8 and "已建 8 个边框窗口" in rlog():
                n0 = len(_enum_border_hwnds())
                break
            time.sleep(0.25)
        check("10a 第一个 --follow 真的建出了 8 个边框窗口（EnumWindows 数到）",
              n0 is not None, "数到 %s 个；日志尾 %s"
              % (len(_enum_border_hwnds()), rlog()[-300:]))
        r = run(["--follow"])
        lg2 = rlog()
        print("  [用例10 第二个 --follow 输出] %s" % (r.stdout + r.stderr).strip()[:400],
              flush=True)
        n1 = len(_enum_border_hwnds())
        check("10b 第二个 --follow：rc=0 且打印「已有一个在跑」",
              r.returncode == 0 and "已有一个 --follow 在跑" in (r.stdout + r.stderr + lg2),
              "rc=%d out=%r" % (r.returncode, (r.stdout + r.stderr)[:300]))
        check("10c ★ 第二个没产生第二组窗口（EnumWindows 仍是 %s 个）" % n0, n1 == n0,
              "改前 %s ⇒ 改后 %s" % (n0, n1))
        p1.terminate()
        try:
            p1.wait(timeout=10)
        except Exception:
            p1.kill()
        time.sleep(1.0)

        # 11) 父进程监护：父进程马上退，子进程须自己收摊
        holder = subprocess.Popen([sys.executable, "-X", "utf8", "-c",
                                   "import time; time.sleep(6)"])
        pf = spawn(["--follow", "--parent-pid", str(holder.pid)])
        procs.append(pf)
        time.sleep(3.0)
        alive_before = _proc_alive(pf.pid)
        holder.terminate()
        try:
            holder.wait(timeout=10)
        except Exception:
            holder.kill()
        gone, secs = _wait_gone(pf.pid, timeout=20)
        check("11a --follow 起来后确实活着（父还没退时不该自杀）", alive_before,
              "alive_before=%s" % alive_before)
        # ★ 判据里必须带上 alive_before：否则在**旧版**上（--follow 根本不存在、
        #   子进程秒退）"gone" 会是真值 ⇒ 判据假绿。判据必须能分辨"真的跑起来又收了"
        #   与"压根没起来"。
        check("11b ★ 父进程一退，子进程 %.1f s 内自己退出，**不留孤儿边框**"
              "（前提 11a：它确实起来过）" % secs,
              bool(alive_before) and gone,
              "alive_before=%s gone=%s alive=%s"
              % (alive_before, gone, _proc_alive(pf.pid)))
        try:
            pf.wait(timeout=5)
        except Exception:
            pass
        lg = rlog()
        check("11c 日志里有「父进程已退出 ⇒ 自己收摊」这句如实记录",
              "父进程已退出" in lg, "日志尾 %s" % lg[-300:])
        check("11d 父进程收摊后屏幕上**一个边框窗都不剩**（EnumWindows=%d）"
              % len(_enum_border_hwnds()), len(_enum_border_hwnds()) == 0,
              _enum_border_hwnds())


        # 12) WDA 双向对照：带 / 不带 --no-wda 起边框，抓屏比边缘像素
        section("W3-B · 用例12-14 需要真窗口，会在屏幕上闪一下边框"
                "（按档厚度 A3/B4/C5/D6，几秒内自动撤）")
        _write_json(passf, {"scopes": ["C"], "expires_at": FUTURE})
        try:
            from PIL import ImageGrab
            PIL_OK = True
        except Exception:
            PIL_OK = False

        # ★ 窗F：判据**跟着配色走**，但不跟着配色漂 ——
        #   目标色写死成当前 C 档的中深蓝（窗F 定板 #1565C0），改成"看着像彩色"
        #   就等于把判据放松成永远绿。这里判的是**抓屏里有没有边框主色**。
        C_ON_SCREEN = (0x15, 0x65, 0xC0)

        def edge_looks_orange(data, order="bgr"):
            """边缘 24x24 里有没有接近 C 档主色的像素（窗F 后是 #1565C0）。

            ★★ 2026-10-15 修（DSH 实读发现，**不是** GLM 复检提的）：
               **两条通道的字节序不一样，必须显式声明** ——
                 · GDI BitBlt（`_bitblt_edge()`）走 `GetDIBits` ⇒ 32bpp BI_RGB ⇒ 字节是 **BGRA**（B 在前）
                 · PIL `ImageGrab.grab().tobytes()` ⇒ 模式 'RGB' ⇒ 字节是 **RGB**（R 在前）
               原来两边共用一种读法 `b,g,r = data[i],data[i+1],data[i+2]` ⇒ **必然有一路恒 0**。
               实测对照：20:0x 是「GDI=0 / PIL=80」（那时读法配的是 PIL），21:1x 之后变成
               「GDI=96 / PIL=0」（窗G 把读法翻成 BGR，救活 GDI、废掉 PIL）⇒
               **12c 当年的"红"是判据自身的字节序 bug，不是环境态**（此点已向用户更正）。
            """
            hit = 0
            for i in range(0, len(data) - 3, 4):
                if order == "bgr":
                    b, g, r = data[i], data[i + 1], data[i + 2]
                else:                                   # "rgb"
                    r, g, b = data[i], data[i + 1], data[i + 2]
                # RGB 欧氏距离 ≤ 90 算命中（够松，能吃下截图/缩放的轻微偏色；
                # 又够紧，不会把桌面 wallpaper 那种"也有点蓝"的像素算进来）
                d2 = ((r - C_ON_SCREEN[0]) ** 2 + (g - C_ON_SCREEN[1]) ** 2
                      + (b - C_ON_SCREEN[2]) ** 2)
                if d2 <= 90 ** 2:
                    hit += 1
            return hit

        def is_blank(buf):
            """整片同一个值 ⇒ 抓回来的不是真实画面（无显示表面的沙箱/会话就是这种）。"""
            if not buf:
                return True
            first = bytes(buf[:4])
            return all(bytes(buf[j:j + 4]) == first for j in range(0, len(buf), 4))

        results = {}
        readiness = {}
        for tag, extra in (("带WDA", []), ("不带WDA", ["--no-wda"])):
            # ★ 窗F（2026-10-18）：本轮日志的**起点偏移**必须在 spawn **之前**取。
            #   这个 log 全程 append，前面用例10/11/16 都往里写过「已建 8 个边框窗口」⇒
            #   拿整份日志去 `in` 判断就永远是真的，等于没有这个条件。
            try:
                log_off = os.path.getsize(log)
            except Exception:
                log_off = 0
            # ★ 窗F 补：延迟**只给用例12**。别的用例（13/14/16/17）走的还是固定
            #   sleep，给它们也加上延迟会把它们一起打红 —— 那不是「现役轮询仍绿」，
            #   是把延迟扩散到了不该延迟的地方（实测踩过：绿 62 / 红 5）。
            p = spawn(["--follow"] + extra, delay=_FORCE_START_DELAY)
            procs.append(p)

            # ── ★ 就绪轮询（替掉原来的固定 sleep(2.5)）────────────────────
            #   (3)「试抓命中像素>0」只对**不带 WDA** 那轮开：带 WDA 时窗被
            #   SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) 排除在截图外，
            #   **设计上就抓不到**，而那正是带 WDA 那轮要断言的结果（见 12）。
            need_pixels = bool(extra)          # extra 非空 == 不带 WDA

            def _count_px():
                return edge_looks_orange(_bitblt_edge())

            if _INJECT_FIXED_SLEEP is not None:
                # ── 反向注入（窗F 跑红自证）：把就绪轮询退回**固定 sleep**。
                #   ★ 窗F 补（2026-10-18 12:5x）：这条分支现在**自己判就绪**。
                #     改前它 sleep 完就把 ready 硬写成 True、就绪判定整个旁路 ——
                #     空载机器上「0.2s 已经够上屏」就绿，注入**证明不了轮询在干活**
                #     （DSH 12:49 实测：绿 67 / 红 0）。
                #     现在 sleep 完按**同一套三件**探一次：齐 ⇒ 绿；不齐 ⇒ **红**，
                #     红字直接说「固定 sleep 之后三件不齐 ⇒ 这一版没在等」。
                #     ⇒ sleep 取多短都会被抓，判别力不再取决于机器快慢。
                #   ★ 要**确定性红**（连快机器也必红）就配 T1_W3_FORCE_START_DELAY：
                #     延迟 N 秒 > sleep 值 ⇒ 固定 sleep 必然抓空。
                _inj = float(_INJECT_FIXED_SLEEP)
                time.sleep(_inj)
                secs = _inj
                _hs = _enum_border_hwnds_of(_descendant_pids(p.pid))
                _d = {"log": READY_LOG_MARK in _rlog_since(log, log_off),
                      "nwin": len(_hs),
                      "px": (_count_px() if need_pixels else None),
                      "vis": bool(_hs) and _any_visible(_hs),
                      "次数": 1, "等待毫秒": int(_inj * 1000),
                      "间隔毫秒": 0, "上限毫秒": 0, "用了像素条件": bool(need_pixels)}
                ready = bool(_d["log"] and _d["nwin"] == READY_N_WINDOWS
                             and ((_d["px"] or 0) > 0 if need_pixels else _d["vis"]))
                try:
                    _added = os.path.getsize(log) - log_off
                except Exception:
                    _added = 0
                readiness[tag] = ("注入·固定 sleep(%.2f)·探了 1 次·三件=%s"
                                  "（轮询 %d 次 / 等待 %d ms）"
                                  % (_inj, "齐" if ready else "不齐",
                                     _d["次数"], _d["等待毫秒"]),)
                print("  [用例12 %s] ⚠ 注入：就绪轮询被旁路，改成固定 sleep(%.2f)"
                      % (tag, _inj), flush=True)
                check("12-就绪 %s ★ 反向注入：固定 sleep 之后三件齐"
                      "（这一条绿 ⇒ 说明 sleep 已经够长，注入**证明不了**轮询在干活）"
                      % tag, ready,
                      "" if ready else
                      ("固定 sleep(%.2f) 之后三件不齐 ⇒ 这一版**没在等**：%s；"
                       "本轮日志新追加 %d 字节。⚠ 光设 T1_W3_INJECT_FIXED_SLEEP 在空载"
                       "机器上可能碰巧够（判别力依赖机器快慢）；要**确定性红**请配 "
                       "T1_W3_FORCE_START_DELAY=3（边框晚 3s 才起，现役轮询照样绿）"
                       % (_inj, _ready_diag(tag, _inj, _d, _added), _added)))
            else:
                ready, secs, diag = _wait_border_ready(
                    log, log_off, p.pid, need_pixels, _count_px)
                try:
                    added = os.path.getsize(log) - log_off
                except Exception:
                    added = 0
                readiness[tag] = ("三件齐，用了 %.2fs（轮询 %d 次 / 等待 %d ms / "
                                  "间隔 %d ms / 上限 %d ms）；本轮日志新追加 %d 字节；"
                                  "窗=%d 像素=%s"
                                  % (secs, int(diag.get("次数") or 0),
                                     int(diag.get("等待毫秒") or 0),
                                     int(diag.get("间隔毫秒") or 0),
                                     int(diag.get("上限毫秒") or 0),
                                     added, diag["nwin"], diag["px"]),)
                print("  [用例12 %s] 就绪=%s（%.2fs）：本轮日志「%s」%s / 按属主 pid 数到 %d 窗%s"
                      % (tag, "齐" if ready else "超时", secs,
                         READY_LOG_MARK, "有" if diag["log"] else "无", diag["nwin"],
                         (" / 试抓 %s 像素" % diag["px"]) if diag["px"] is not None
                         else " / 试抓不适用（带 WDA，设计上抓不到）"), flush=True)
                print("  [用例12 %s] 轮询可观测：探了 %d 次 / 累计等待 %d ms / 间隔 %d ms"
                      " / 上限 %d ms；三件 = (1)日志 %s (2)窗 %d/%d %s (3)像素 %s"
                      % (tag, int(diag.get("次数") or 0), int(diag.get("等待毫秒") or 0),
                         int(diag.get("间隔毫秒") or 0), int(diag.get("上限毫秒") or 0),
                         "✔" if diag.get("log") else "✘", diag.get("nwin") or 0,
                         READY_N_WINDOWS, "（已显示）" if diag.get("vis") else "（还没显示）",
                         ("✔%s" % diag.get("px")) if diag.get("px") is not None
                         else "不适用（带 WDA）"), flush=True)
                if not ready:
                    check("12-就绪 %s ★ 三件齐才进断言（上限 %.0fs / 间隔 %.2fs）"
                          % (tag, READY_TIMEOUT_SEC, READY_INTERVAL_SEC),
                          False, _ready_diag(tag, secs, diag, added))
            # ───────────────────────────────────────────────────────────

            bmp = _bitblt_edge()
            pil_hit, pil_why = None, ""
            if PIL_OK:
                for kw in ({"all_screens": True}, {}):
                    try:
                        im = ImageGrab.grab(**kw)
                        crop = im.crop((0, im.height - 24, 24, im.height))
                        # ★ 显式声明字节序：PIL 的 tobytes() 是 RGB（见 edge_looks_orange 的注释）
                        pil_hit = edge_looks_orange(crop.tobytes(), order="rgb")
                        break
                    except Exception as e:
                        pil_why = (pil_why + " | " if pil_why else "") + \
                                  "%s%s: %s" % (kw or "单屏", type(e).__name__, e)
            results[tag] = (edge_looks_orange(bmp), pil_hit, bmp, pil_why)
            print("  [用例12 %s] GDI BitBlt 边缘彩色像素=%d%s"
                  % (tag, results[tag][0],
                     ("，PIL ImageGrab=%d" % pil_hit) if pil_hit is not None
                     else ("，PIL 本机抓不到屏（%s）" % (pil_why or "?"))),
                  flush=True)
            # ★ 窗F 补：**必须在 terminate 之前**把这一轮的进程树取全 ——
            #   守望进程一死，孙进程会被 reparent 掉，psutil 就再也找不到它了。
            _own = _descendant_pids(p.pid) | {p.pid}
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
            # ★ 窗F 补：p.terminate() 杀不到真正画窗的**孙进程**（--parent-pid
            #   指的是自证进程，不是守望进程）。平时靠 15 系列顺手收，这次按
            #   **属主 pid** 显式收干净 —— 否则启动延迟会把「清理」排在「建窗」
            #   后面，15f/15h/15l/16 会一起红（那几条红与轮询无关）。
            #   ★ 按 pid 杀**整棵还活着的树**（不按窗口）：加了启动延迟之后，
            #     收摊那一刻孙进程可能**还在 sleep、一个窗都还没建** ——
            #     按窗口找就找不到它，它 3s 后醒来照样留一屏边框（实测：16 / 15o-4 红）。
            _killed = []
            for _vp in sorted(_own - {os.getpid()}):
                if _proc_alive(_vp):
                    try:
                        os.kill(_vp, 9)
                        _killed.append(_vp)
                    except Exception:
                        pass
            for _vp in _kill_border_owner_pids(_own, exclude={os.getpid()}):
                if _vp not in _killed:
                    _killed.append(_vp)
            if _killed:
                print("  [用例12 %s] 收摊：按属主 pid 补杀 %s（守望进程退了、"
                      "画窗的孙进程还活着的那种）" % (tag, _killed), flush=True)
            print("  [用例12 %s] 收摊自证：本轮树 %s / 补杀 %s / 枚举到边框窗 %s"
                  % (tag, sorted(_own - {os.getpid()}), _killed,
                     _enum_border_hwnds_of(_own)), flush=True)
            time.sleep(1.2)

        # 窗F：把两轮的就绪耗时摊开印出来 —— 「轮询有没有让正常路径变慢」
        #   要靠这个实测数说话，不能靠估。
        for _rtag in ("带WDA", "不带WDA"):
            if _rtag in readiness:
                print("  [用例12 就绪小结] %s：%s" % (_rtag, readiness[_rtag][0]),
                      flush=True)

        blank = dict((t, is_blank(results[t][2])) for t in results)
        print("  [抓屏自检] 左下角 24x24 抓回来的是不是整片空白：%s" % blank, flush=True)
        if all(blank.values()):
            # ★ 如实处理：本机（无显示表面的沙箱会话）抓屏回空白，PIL 也直接
            #   "screen grab failed"。这时候 12/12b/12c **验不了** ——
            #   判绿是自欺（没抓到东西不等于"看不见边框"），判红是把环境问题
            #   算到代码头上。两个都不做，走 SKIP 登记。
            SKIP.append("12 / 12b / 12c（WDA 不进截图的三向对照）：本机 GDI BitBlt 与 "
                        "PIL ImageGrab 都抓不回真实画面（BitBlt 整片空白；PIL 报 "
                        "OSError: screen grab failed）⇒ 这台机器上验不了，"
                        "需要 DSH 在有真实显示的会话里跑一次")
            print("  [用例12] ⚠ 本机抓屏回**整片空白**（PIL 同样 screen grab failed）")
            print("           ⇒ 12 / 12b / 12c **本机验不了**：判绿是自欺，判红是"
                  "把环境问题算到代码头上。已登记 SKIP，请 DSH 在有真实显示的会话跑一次。",
                  flush=True)
        else:
            check("12 ★ 带 WDA ⇒ 抓屏**看不到**边框（前提 12b：同样条件下不带 WDA 抓得到）",
                  results["不带WDA"][0] > 0 and results["带WDA"][0] == 0,
                  "带WDA=%s 不带WDA=%s" % (results["带WDA"][0], results["不带WDA"][0]))
            check("12b 不带 WDA（--no-wda）⇒ 抓屏**能**看到边框（证明判据本身有效）",
                  results["不带WDA"][0] > 0, results["不带WDA"][0])
            if PIL_OK and results["带WDA"][1] is not None:
                if results["不带WDA"][1] > 0:
                    check("12c ★ 第三条抓屏通道对照（PIL ImageGrab）：带WDA 看不到 / 不带WDA 看得到"
                          "｜⚠️ 本条**不是** perceive 走的路 —— 原文案把 PIL 说成『perceive 真正用的路』"
                          "是**假账**，2026-10-15 GLM(ZCode) 复检指出、DSH 实读核对：perceive 用 **mss**"
                          "（core.py:850-853 的 _look_only、:1443-1446 的坐标动作 verify_image），"
                          "而 mss 在 Windows 上就是 GDI BitBlt ⇒ 与 12/12b **同族**，那两条才覆盖 perceive 的路；"
                          "PIL 只是第三通道（若真要『perceive 同库对照』，应另加一条 import mss 的通道）",
                          results["不带WDA"][1] > 0 and results["带WDA"][1] == 0,
                          "带WDA=%s 不带WDA=%s"
                          % (results["带WDA"][1], results["不带WDA"][1]))
                else:
                    # ★ 2026-10-15 用户裁定：「抓屏自检回空白 ⇒ 记验不了、不计红」。
                    #   本机实测：GDI BitBlt 那条路正常（不带WDA 抓到 96 像素），
                    #   但 **PIL ImageGrab 抓不到任何画面**（不带WDA 也是 0）。
                    #   12c 是【反向对照】——它的前提是"不带WDA 时 PIL 看得到"；
                    #   前提不成立时：判红 = 把环境问题算到代码头上；判绿 = 自欺。
                    #   ⇒ 两条都不做，走 SKIP 登记。GDI 那条路（12/12b）仍然强判。
                    SKIP.append("12c（PIL ImageGrab 那条路）：本机 PIL 抓不到任何画面"
                                "（不带WDA 也是 0 像素 ⇒ 反向对照的前提不成立）"
                                "⇒ 记验不了、不计红（用户 2026-10-15 裁定）；"
                                "同一次运行里 GDI BitBlt 的 12/12b 仍按强判执行")
                    print("  [用例12c] ⚠ PIL 抓不到任何画面（不带WDA 也是 0）⇒ 反向对照前提不成立，"
                          "记**验不了**、不计红。GDI 那条路仍强判。", flush=True)
            else:
                SKIP.append("12c（PIL ImageGrab 那条路）：本机 ImageGrab 直接 "
                            "OSError: screen grab failed")

        # 13) 点击穿透：WM_NCHITTEST 必须回 HTTRANSPARENT(-1)
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        # ── ★ 窗G：13 的固定 sleep(2.5) 换成有界就绪轮询 ────────────────
        #   13 关心的是「窗口在不在、显示没显示」——WM_NCHITTEST 是**发给窗口**的
        #   消息，与「抓屏能不能抓到边框色」无关 ⇒ 就绪条件用
        #   (1)本轮日志有「已建 8 个」 (2)按属主 pid 自数窗=8 (3)窗已真正显示。
        #   ★ hs 从 `_enum_border_hwnds()`（**全屏数、不分属主**）改成按属主过滤：
        #     别人的边框窗混进来时，13a/13b 断言的是**不是本轮的窗**，等于在测空气。
        log_off13 = _logsize(log)
        p = spawn(["--follow"],
                  delay=(_FORCE_START_DELAY if "13" in _DELAY_CASES else 0.0),
                  label="用例13")
        procs.append(p)
        ready13, hs, _s13, _d13 = _wait_case_ready("13", log, log_off13, p)
        # ★ 2026-10-15 修（GLM/ZCode 复检 刺2）：原来是 hs[:4] —— 那是**4 窗时代**的旧参数。
        #   窗F 之后每屏 8 窗（主色 4 + 1px 反色描边 4），而 EnumWindows 的顺序**不保证** ⇒
        #   只测前 4 个完全可能"恰好全是主色窗"，**edge 窗的 WM_NCHITTEST 从未被断言**，
        #   判据看起来绿、其实一半没覆盖。这里改成全量，并在下面加一条覆盖性断言（13b）。
        rts = [int(u.SendMessageW(wt.HWND(h), 0x0084, 0, 0)) for h in hs]
        try:
            wfp = int(u.WindowFromPoint(wt.POINT(4, 4)) or 0)
            cbuf = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(wt.HWND(wfp), cbuf, 256)
            wfp_cls = cbuf.value
        except Exception as e:
            wfp, wfp_cls = 0, "<%r>" % (e,)
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
        time.sleep(1.0)
        print("  [用例13] 边框 hwnd=%s → WM_NCHITTEST=%s；"
              "WindowFromPoint(4,4)=%s class=%r"
              % (hs, rts, wfp, wfp_cls), flush=True)
        check("13a ★ 边框窗口 WM_NCHITTEST 回 HTTRANSPARENT(-1) ⇒ 点击穿透",
              len(rts) > 0 and all(v == -1 for v in rts), rts)
        check("13b ★ 本条必须覆盖**全部**边框窗（一屏 = 主色 4 + 1px 反色描边 4 = 8 个），"
              "不许只测一半 —— 只测 hs[:4] 时 edge 窗可能从未被断言（GLM/ZCode 复检 刺2）",
              len(rts) == len(hs) and len(hs) >= 8, "hs=%d rts=%d" % (len(hs), len(rts)))
        print("  （WindowFromPoint 类=%r —— 这一条**只如实记录**不判红："
              "WindowFromPoint 本身就不看 WS_EX_TRANSPARENT，真正的穿透靠上面的 "
              "WM_NCHITTEST + WS_EX_TRANSPARENT 两条。）" % wfp_cls, flush=True)

        # 16) ★ 连着起两轮 --follow：第二轮**也**要建出 4 个窗口。
        #   这条是被一个**真 bug** 逼出来的（实测现场：有个活着的 agentctl.mcp 拉起的
        #   --follow 占着单例互斥量、自己却**一个边框窗口都没有**、一个线程也不多）。
        #   根因二连：
        #     ① hInstance=NULL 注册的窗口类**不随进程退出注销**，类里存着 lpfnWndProc
        #        —— 那是上个进程的地址，进程一死就成野指针；下一个进程 RegisterClassW
        #        拿到 1410，若"沿用"它，CreateWindowExW 拿着野指针当窗口过程
        #        ⇒ 实测卡住、四个窗口一个都建不出来。
        #     ② _create_all() 原来在 try 外面 ⇒ 建窗一失败，底下的 finally
        #        （销毁窗口 + 释放单例互斥量）跑不到 ⇒ 互斥量被一个"没边框的进程"
        #        长期占着，之后每个 --follow 都秒退「已有一个在跑」。
        #   ⇒ 现在的行为：每轮 _register 前先 UnregisterClassW 清僵尸类，
        #     _create_all 挪进 try，退出时主动注销类。
        section("用例16 · 连着起两轮 --follow（治僵尸窗口类 / 互斥量漏释放）")
        _write_json(passf, {"scopes": ["C"], "expires_at": FUTURE})
        counts = []
        round_logs = []
        for rnd in (1, 2):
            # ★ 窗G：每轮各取一次**本轮日志起点**（字节偏移）。
            #   16b 要判的是「**第二轮**也建了自己的窗、不是被单例挡掉」，
            #   拿整份 rlog() 去 count 就是**历史累计**——用例10/11/13 也都往同一份
            #   log 里写过同一句话，累计数天然 ≥2，这条判据对「第二轮没建窗」完全瞎。
            #   逐轮用 `_rlog_since(log, off)` 切本轮，才能既不漏也不**读成两次**。
            off16 = _logsize(log)
            p = spawn(["--follow"],
                      delay=(_FORCE_START_DELAY if "16" in _DELAY_CASES else 0.0),
                      label="用例16-第%d轮" % rnd)
            procs.append(p)
            _r16, _hs16, _s16, _d16 = _wait_case_ready(
                "16-第%d轮" % rnd, log, off16, p)
            counts.append(len(_hs16))
            round_logs.append(_rlog_since(log, off16))
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
            time.sleep(1.2)
        print("  [用例16] 第1轮建了 %d 个窗口，第2轮建了 %d 个" % tuple(counts),
              flush=True)
        # ★ 窗F：每屏 8 个窗口（主色 4 + 1px 反色描边 4）。
        check("16 ★ 连着起两轮 --follow，**两轮都**建出 8 个边框窗口"
              "（回归判据：僵尸窗口类 ⇒ 第二轮建不出窗口；建窗失败 ⇒ 单例互斥量漏释放）",
              counts == [8, 8], "第1轮 %d 个 / 第2轮 %d 个" % (counts[0], counts[1]))
        # ★ 窗G：16b 改成**逐轮**数（每轮按自己的字节偏移切本轮新增），
        #   并且要求「恰好 1 行」—— `>= 1` 抓不住「两轮读成同一段日志」这种错，
        #   `>= 2`（原来那条）更是被历史累计喂饱，对「第二轮没建窗」完全瞎。
        each = [t.count(READY_LOG_MARK) for t in round_logs]
        print("  [用例16] 逐轮日志计数（按各轮字节偏移）= %s；"
              "整份日志历史累计 = %d 次（本条**不**拿它当证据）"
              % (each, rlog().count(READY_LOG_MARK)), flush=True)
        check("16b 第二轮的日志里有**自己那一份**「已建 8 个边框窗口」"
              "（不是被单例挡掉）｜逐轮按字节偏移各数一次，不得读成两轮同一段",
              each == [1, 1],
              "逐轮计数=%s（要 [1, 1]）；整份日志累计=%d"
              % (each, rlog().count(READY_LOG_MARK)))

        # 14) 不抢焦点：显示前后 GetForegroundWindow 不变
        # ── ★ 窗H：14 的固定 sleep(2.5) 换成有界就绪轮询 ────────────────
        #   14 断言的是「**边框窗在不在**」+「显示前后前台窗不变」，判据的前提就是
        #   `len(hs14) > 0`（「前提：这轮确实建出了 N 个边框窗口」）。
        #   ⇒ 就绪条件 = (1)本轮日志有「已建 8 个」 (2)按属主 pid 自数窗=8 (3)窗已真正显示。
        #   不用像素条件：14 判的是「有没有抢前台」，与「抓屏抓不抓得到边框色」无关。
        #   ★ hs14 从 _enum_border_hwnds()（全屏数、不分属主）改成按属主过滤 ——
        #     「边框自己抢了前台」那条分支判的是 (fg1 in set(hs14)) 或 fg1 的 pid 属边框，
        #     混进别人的窗会让它判错对象。
        fg0 = int(u.GetForegroundWindow() or 0)
        log_off14 = _logsize(log)
        p = spawn(["--follow"],
                  delay=(_FORCE_START_DELAY if "14" in _DELAY_CASES else 0.0),
                  label="用例14")
        procs.append(p)
        ready14, hs14, _s14, _d14 = _wait_case_ready("14", log, log_off14, p)
        fg1 = int(u.GetForegroundWindow() or 0)
        # ★ 必须在 terminate **之前**数窗口：杀完之后当然是 0 个 ——
        #   （踩过：数在 terminate 之后，前提条件恒假，判据形同虚设。）
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
        time.sleep(0.8)
        print("  [用例14] 边框窗口数=%d；显示前 fg=%s，显示后 fg=%s"
              % (len(hs14), fg0, fg1), flush=True)
        if fg0 == 0:
            # ★ 本会话压根**没有前台窗口**（GetForegroundWindow() 恒为 NULL，
            #   无交互桌面的沙箱会话就是这样）⇒ "有没有抢焦点"在这里无从谈起。
            #   判绿是自欺（fg 恒 0，怎么比都是"没变"）。
            SKIP.append("14（不抢焦点）：本会话 GetForegroundWindow() 恒为 NULL"
                        "（没有前台窗口/无交互桌面）⇒ 这台机器上验不了，"
                        "需要 DSH 在真实登录会话里跑一次")
            print("           ⇒ 本会话 fg 恒为 0（压根没有前台窗口）⇒ 14 验不了，"
                  "已登记 SKIP。请 DSH 在真实登录会话跑一次。", flush=True)
        else:
            # ★ 2026-10-15 **三方判定**（GLM/ZCode 复检 ② 的建议，采纳入账）：
            #   本用例的产品语义是「**边框**不抢前台」⇒ 只判边框自己的罪，第三方噪声如实登记并带证据。
            #   ⛔ 原先的"检测到前台被夺 ⇒ 笼统记验不了"被否：它会在
            #      「边框真抢了焦点 + 恰好第三方也在抢」那个窗口里**放走真 regression**（把牙齿拔了）。
            #   判法：fg 没变 ⇒ 绿；fg 变了且新前台属**边框进程** ⇒ **红**；属第三方 ⇒ 验不了（带 pid/class）。
            import ctypes.wintypes as _wt
            _border_pids = _border_owner_pids()
            _fg1_pid, _fg1_cls = 0, ""
            try:
                _p = _wt.DWORD(0)
                u.GetWindowThreadProcessId(_wt.HWND(fg1), ctypes.byref(_p))
                _fg1_pid = int(_p.value)
                _cb = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(_wt.HWND(fg1), _cb, 256)
                _fg1_cls = _cb.value
            except Exception as _e:
                _fg1_cls = "<%r>" % (_e,)
            _by_border = (fg1 in set(hs14)) or bool(_fg1_pid and _fg1_pid in _border_pids)
            if fg0 == fg1:
                check("14 ★ 边框显示前后 GetForegroundWindow() 不变（不抢用户正在输入的窗口）"
                      "（前提：这轮确实建出了 %d 个边框窗口）" % len(hs14),
                      len(hs14) > 0, "边框窗数=%d fg0=%s fg1=%s" % (len(hs14), fg0, fg1))
            elif _by_border:
                check("14 ★ 边框显示前后 GetForegroundWindow() 不变（不抢用户正在输入的窗口）"
                      " —— **边框自己抢了前台**（fg1 属边框：hwnd=%s pid=%s class=%r）⇒ 必红"
                      % (fg1, _fg1_pid, _fg1_cls),
                      False, "边框窗数=%d fg0=%s fg1=%s pid=%s" % (len(hs14), fg0, fg1, _fg1_pid))
            else:
                SKIP.append("14（不抢焦点）：本轮前台被**第三方进程**夺走（不在边框进程里 ⇒ 不是边框的错）"
                            "—— fg0=%s → fg1=%s，夺走者 pid=%s class=%r；"
                            "本用例只判边框自己的罪，故记验不了、不计红；"
                            "★ 若 fg1 属边框进程则必红（见上面那条分支），逻辑没放松"
                            % (fg0, fg1, _fg1_pid, _fg1_cls))
                print("           ⇒ 14：前台被第三方 pid=%s class=%r 夺走（fg0=%s→fg1=%s）"
                      "⇒ 记**验不了**、不计红。" % (_fg1_pid, _fg1_cls, fg0, fg1), flush=True)
    finally:
        for p in procs:
            try:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=8)
                    except Exception:
                        p.kill()
            except Exception:
                pass
        time.sleep(0.6)
        left = _enum_border_hwnds()
        if left:
            print("  [收尾] ⚠ 判据跑完还剩 %d 个边框窗口：%s（如实报告，不静默）"
                  % (len(left), left), flush=True)
        else:
            print("  [收尾] 判据跑完，屏幕上 0 个残留边框窗口", flush=True)
        check("收尾 · 判据跑完屏幕上**没有残留边框窗口**（不把测试窗口留给用户）",
              len(left) == 0, left)
        # ★ 17（窗Z · 2026-10-17 新增）：边框日志落在**沙箱**，不许落生产目录。
        #   机制：本函数 env 已设 AGENTCTL_BORDER_LOG=<tmp>\ctrl_border.log，
        #   ctrl_border 的 log_path() 优先读该覆盖口 ⇒ 产品日志应写在判据沙箱里。
        #   （须在 finally 的 rmtree(tmp) 之前断言——删了目录就没得验了。）
        # * 窗H：17 原来只 os.path.getsize(log) 采样一次。
        #   17 断言的是「边框日志落在沙箱」——它依赖的是**产物落位**，不是「窗还在」。
        #   上面那句收尾 check 断言的恰恰是「已经收干净了」，所以这里等的是
        #   **收尾完成 + 日志已落位**，不是建窗出现。
        #   （拿「窗=8 + 已显示」那套来套 17，会把「已收干净」当成「没建出来」，
        #     正是派单点名要防的那个坑。）
        _mark_allrun_log(log)
        _wait_teardown_gone("17")
        try:
            _sz17 = os.path.getsize(log)
        except OSError:
            _sz17 = -1
        check("17（窗Z）· 边框日志确实落在沙箱（<tmp>/ctrl_border.log 存在且非空）",
              _sz17 > 0, "沙箱日志字节数=%d（log=%s）" % (_sz17, log))
        owners_left = _border_owner_pids()
        if owners_left:
            print("  [收尾] ⚠ 判据跑完还有残留的边框属主进程 %s（多半是本脚本的 pid=%d，"
                  "等本进程退出会被 --parent-pid 监护带走）"
                  % (sorted(owners_left), os.getpid()), flush=True)
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# 用例 15 · 接线：server.py / mcp.py 启动时拉起、退出时收掉
# ═══════════════════════════════════════════════════════════════
#
# ★ 这里也**必须**保证不碰真闸门文件，所以给子进程显式改了三条环境：
#     AGENTCTL_LEGACY_HOME=<tmp>/fakehome（目录里**没有** gate.py ⇒ gate 把它当
#         「状态目录」原样采用 ⇒ gate.PASSFILE 落在 <tmp> 内，见 gate.py:294-297）
#     AGENTCTL_BRAKE_FLAG=<tmp>/_急停.flag（覆盖真急停路径，gate.py:407 本来就支持）
#     AGENTCTL_BORDER_LOG=<tmp>/wire.log（不污染真 %LOCALAPPDATA% 那份日志）
#   两条路径都不存在 ⇒ 边框只会判「隐藏」，真 auth_pass.json / 真急停 flag 一次没碰。


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wire(src_dir=None):
    src_dir = src_dir or SRC
    tmp = tempfile.mkdtemp(prefix="T1_W3_wire_")
    log = os.path.join(tmp, "wire.log")
    fakehome = os.path.join(tmp, "fakehome")        # 空目录，里面没有 gate.py
    os.makedirs(os.path.join(fakehome, "_auth"), exist_ok=True)
    state = os.path.join(tmp, "state")
    os.makedirs(state, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": src_dir + (os.pathsep + env["PYTHONPATH"]
                                 if env.get("PYTHONPATH") else ""),
        "AGENTCTL_LEGACY_HOME": fakehome,
        "AGENTCTL_BRAKE_FLAG": os.path.join(tmp, "_急停.flag"),
        "AGENTCTL_BORDER_LOG": log,
        "AGENTCTL_STATE": state,
        # 同上：测试专用覆盖口，别跟在跑的生产服务抢单例互斥量
        "AGENTCTL_BORDER_MUTEX": "Local\\AgentCtlCtrlBorderFollow__T1W3_wire_%d"
                                 % os.getpid(),
    })
    env.pop("AGENTCTL_BORDER", None)

    def _wire_cmd(mod):
        """wire() 段起 agentctl.<mod> 的命令行；带启动延迟时用 runpy 包一层。

        * 窗H：让用例 15 也能被 T1_W3_FORCE_START_DELAY 延迟，否则 15 永远测不到慢路径。
        * 产物零改动：runpy.run_module(..., run_name="__main__") 与 `python -m` 等价，
          延迟发生在 import 之前，产品行为一字不变（与 border() 段同一套做法）。
        """
        argv = ["-X", "utf8", "-m", "agentctl." + mod]
        dly = float(_FORCE_START_DELAY or 0.0) if "15" in _DELAY_CASES else 0.0
        if dly > 0:
            _wrap = ("import time,runpy,sys;time.sleep(%r);"
                     "sys.argv=['agentctl.%s']+%r;"
                     "runpy.run_module('agentctl.%s',run_name='__main__')"
                     % (dly, mod, argv, mod))
            print("  [用例15] ⚠ 强制启动延迟 %.1fs：agentctl.%s import 前先睡这么久"
                  % (dly, mod), flush=True)
            return [sys.executable, "-X", "utf8", "-c", _wrap]
        return [sys.executable] + argv

    def rlog():
        try:
            return io.open(log, encoding="utf-8", errors="replace").read()
        except Exception:
            return ""

    procs = []
    try:
        section("用例15 · 接线（src=%s）" % src_dir)
        print("  临时目录：%s（真闸门文件未被触碰）" % tmp, flush=True)

        # ── 15a server.py：Ctrl+Break 走正常退出路径 → finally 里 stop_follow ──
        port = _free_port()
        env["AGENTCTL_PORT"] = str(port)
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        sp = subprocess.Popen(_wire_cmd("server"),
                              env=env, cwd=tmp, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace",
                              creationflags=CREATE_NEW_PROCESS_GROUP)
        procs.append(sp)
        import urllib.request
        import urllib.error
        health = None
        end = time.monotonic() + 40
        while time.monotonic() < end:
            if sp.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/v1/health" % port, timeout=2) as r:
                    health = json.loads(r.read().decode("utf-8"))
                break
            except Exception:
                time.sleep(0.4)
        print("  [15a /v1/health] %s" % health, flush=True)
        check("15a server.py 起得来且 /v1/health 通", bool(health), health)
        lg = rlog()
        # ★ 窗H：15b 原来紧跟 15a 的健康轮询就断言「边框已启动 pid=」—— health 通只说明
        #   server 活着，**边框可能还没被拉起来**（尤其带启动延迟时）。这是同一族的偶发。
        #   ⇒ 加有界就绪门：日志出现那行 ∧ 那个 pid 还活着 ∧ 按属主 pid 自数窗=8。
        _rdy15b, _s15b, _dg15b = _wait_wire_border_started("15", log)
        lg = _rlog_all(log)
        m = re.search(r"边框已启动 pid=(\d+)", lg)
        check("15b ★ 日志里有「边框已启动 pid=…」（接线真的接上了）", bool(m), lg[-400:])
        bpid = int(m.group(1)) if m else 0
        # ★ 回归判据（这条是被一个真 bug 逼出来的）：Windows 上通过
        #   STARTUPINFO.lpStdHandles 继承给子进程的标准句柄**不保留 append 语义** ——
        #   子进程里的 write 落在继承时的偏移(0)，会把父进程先前 append 的日志行**覆盖掉**。
        #   实测踩过：日志里只剩子进程的 "[--follow] ..."，父进程那行"边框已启动 pid="
        #   凭空消失 ⇒ 接线到底有没有生效都看不出来。
        #   ⇒ 父与子的行必须**同时**在日志里，且都带时间戳。
        parent_lines = [l for l in lg.splitlines() if "边框已启动 pid=" in l]
        child_lines = [l for l in lg.splitlines() if "[--follow]" in l]
        check("15b-1 ★ 父、子两边的日志行**都在**（Windows 继承的标准句柄不保留 append，"
              "子进程写会把父那行覆盖掉 —— 这条就是那个 bug 的回归判据）",
              bool(parent_lines) and bool(child_lines),
              "父行 %d 条 / 子行 %d 条" % (len(parent_lines), len(child_lines)))
        ts = "20"  # 时间戳前缀形如 "2026-10-14 03:21:25 "
        # ★ 必须带上"至少有一行"：空列表 all() 也是 True ⇒ 旧版（一个字都没写）
        #   会空判成绿，那是最阴的一种假绿。
        _all_lines = parent_lines + child_lines
        check("15b-2 每一行都带时间戳（父与子各写各的、append 语义正确，没有互相踩）；"
              "前提：日志里确实有行",
              bool(_all_lines) and all(l.startswith(ts) for l in _all_lines),
              "共 %d 行；无时间戳的行：%s"
              % (len(_all_lines), [l[:60] for l in _all_lines if not l.startswith(ts)]))
        check("15c 那个边框子进程确实活着", bool(bpid) and _proc_alive(bpid),
              "pid=%s alive=%s" % (bpid, _proc_alive(bpid) if bpid else False))
        try:
            sp.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception as e:
            print("  （发 CTRL_BREAK 失败：%r）" % (e,), flush=True)
        try:
            sp.wait(timeout=15)
            graceful = True
        except Exception:
            sp.terminate()
            try:
                sp.wait(timeout=10)
            except Exception:
                sp.kill()
            graceful = False
        lg = rlog()
        print("  [15a server 退出] graceful=%s 日志尾：%s"
              % (graceful, lg[-260:].replace("\n", " | ")), flush=True)
        # ★ server.py 的**优雅退出**路径在脚本里触发不了，如实走 SKIP：
        #   我用 CTRL_BREAK_EVENT 让它"优雅"退出，但 Windows 上 CTRL_BREAK 走 SIGBREAK，
        #   而 Python 只给 SIGINT 装了 KeyboardInterrupt ⇒ 默认动作是**直接终止进程**，
        #   finally 根本跑不到（所以下面 15e/15f 靠 --parent-pid 监护收的尾，是绿的）。
        #   想在这里验 stop_follow，得改 server.py 去处理 SIGBREAK —— 那是**为了判据改产品**，
        #   不做。优雅路径改由 15c（mcp.py：stdin 一关就正常走完 finally）和
        #   下面 15o（start/stop_follow 单元级）证明。
        SKIP.append("15d（server.py 正常退出时的 stop_follow）：脚本里无法可靠触发 server.py 的"
                    "优雅退出（CTRL_BREAK 在 Windows 上默认直接终止进程，finally 跑不到）。"
                    "同一段逻辑由 15c（mcp.py stdin 关闭 ⇒ finally 正常执行）与 "
                    "15o（start_follow/stop_follow 单元级）覆盖")
        print("  [用例15d] ⚠ 本机触发不了 server.py 的优雅退出（CTRL_BREAK ⇒ SIGBREAK ⇒ "
              "Python 默认直接终止，finally 跑不到）⇒ 15d 走 SKIP，"
              "优雅路径由 15c / 15o 覆盖。", flush=True)
        gone, secs = _wait_gone(bpid, timeout=15) if bpid else (True, 0)
        check("15e 退出后那个边框子进程确实不在了（%.1fs 内）" % secs, gone,
              "alive=%s" % (_proc_alive(bpid) if bpid else False))
        # -- 15e/15f --
        # * 窗H：15f/15h/15l 原来都是采样一次判「屏幕上没边框」，那是赌
        #   「清理已经跑完了」——与 12/13/16 赌上屏时间是同一族病，只是方向相反。
        #   换成有界等待「收干净」：残留窗=0 且 无属主进程 且 本段跑过 spawn。
        #   这里等的是「收尾完成」而不是「建窗出现」：套 _wait_border_ready 那套
        #     「窗=8 + 已显示」会把「还没建出来」误判成「没等够」。
        _wait_teardown_gone("15")
        check("15f ★ 屏幕上没留下孤儿边框（EnumWindows=%d）" % len(_enum_border_hwnds()),
              len(_enum_border_hwnds()) == 0, _enum_border_hwnds())

        # ── 15b server.py：硬杀（TerminateProcess，finally 跑不到）⇒ 靠 --parent-pid 自救 ──
        try:
            io.open(log, "w", encoding="utf-8").close()      # 清日志好定位
        except Exception:
            pass
        port = _free_port()
        env["AGENTCTL_PORT"] = str(port)
        sp2 = subprocess.Popen(_wire_cmd("server"),
                               env=env, cwd=tmp, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        procs.append(sp2)
        # 10 s 足够：server 一起来 start_follow 就同步返回，边框 pid 立刻写日志。
        # 原来 40 s 是保守值，但旧版（压根没有这行）要干等满，白白拖慢改前红那一轮。
        end = time.monotonic() + 10
        bpid2 = 0
        while time.monotonic() < end:
            m = re.search(r"边框已启动 pid=(\d+)", rlog())
            if m:
                bpid2 = int(m.group(1))
                break
            time.sleep(0.4)
        sp2.terminate()                     # 硬杀：finally 跑不到
        try:
            sp2.wait(timeout=10)
        except Exception:
            sp2.kill()
        gone2, secs2 = _wait_gone(bpid2, timeout=25) if bpid2 else (True, 0)
        print("  [15b 硬杀后] 边框 pid=%s，%.1fs 内消失=%s"
              % (bpid2, secs2, gone2), flush=True)
        check("15g ★ server 被硬杀（finally 跑不到）时，边框子进程靠 --parent-pid "
              "在 %.1fs 内自己退出，不留孤儿" % secs2, bpid2 and gone2,
              "bpid2=%s gone=%s" % (bpid2, gone2))
        # * 窗H：同上，15h 之前等「收干净」而不是采样一次。
        _wait_teardown_gone("15")
        check("15h 硬杀后屏幕上也没有孤儿边框（EnumWindows=%d）" % len(_enum_border_hwnds()),
              len(_enum_border_hwnds()) == 0, _enum_border_hwnds())

        # ── 15c mcp.py：手喂 JSON-RPC，stdin 一关主循环就结束 → finally 收掉 ──
        try:
            io.open(log, "w", encoding="utf-8").close()
        except Exception:
            pass
        stdin_data = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
        ]) + "\n"
        mp = subprocess.run(_wire_cmd("mcp"),
                            env=env, cwd=tmp, input=stdin_data,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60)
        lg = rlog()
        lines = [l for l in (mp.stdout or "").splitlines() if l.strip()]
        parsed = []
        for l in lines:
            try:
                parsed.append(json.loads(l))
            except Exception as e:
                parsed.append({"__parse_error__": "%r :: %s" % (e, l[:120])})
        print("  [15c mcp stdout 回 %d 行] %s" % (len(lines), lines[:3]), flush=True)
        check("15i mcp.py 的 stdout **只有 JSON-RPC**（每行都能 json.loads，边框没污染协议）",
              len(lines) >= 2 and all("__parse_error__" not in d for d in parsed),
              parsed[:3])
        check("15j mcp.py 应答了 initialize 与 ping",
              any(d.get("id") == 1 for d in parsed if "__parse_error__" not in d)
              and any(d.get("id") == 2 for d in parsed if "__parse_error__" not in d),
              parsed[:3])
        check("15k ★ mcp.py 日志里有「边框已启动」且退出时有「边框已收掉」",
              "边框已启动" in lg and "边框已收掉" in lg, lg[-400:])
        # * 窗H：同上，15l 之前等「收干净」。

        _wait_teardown_gone("15")
        check("15l mcp 退出后屏幕上没有边框（EnumWindows=%d）" % len(_enum_border_hwnds()),
              len(_enum_border_hwnds()) == 0, _enum_border_hwnds())

        # ── 15d 冻结态：不静默 ──
        # 15o start_follow → stop_follow 的单元级往返（补 15d 验不了的那一段逻辑）
        section("用例15 附加 · start_follow / stop_follow 单元级往返")
        try:
            io.open(log, "w", encoding="utf-8").close()
        except Exception:
            pass
        mp4 = subprocess.run([sys.executable, "-X", "utf8", "-c",
                              "import sys,os,time;"
                              "sys.path.insert(0,os.environ['PYTHONPATH']);"
                              "from agentctl import ctrl_border as cb;"
                              "p=cb.start_follow('unit');"
                              "time.sleep(2.5);"
                              "print('border_pid=%d' % p);"
                              "cb.stop_follow();"
                              "print('stopped')"],
                             env=env, cwd=tmp, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=90)
        print("  [15o 子进程输出] %s" % (mp4.stdout or "").strip(), flush=True)
        lg4 = rlog()
        upid = re.search(r"border_pid=(\d+)", mp4.stdout or "")
        check("15o-1 start_follow 拉起了边框（返回非 0 pid）", bool(upid), (mp4.stdout or "").strip())
        check("15o-2 ★ stop_follow 之后日志里有「边框已收掉 pid=…」（finally 那段逻辑是对的）",
              "边框已收掉" in lg4, lg4[-400:])
        gone4, _ = _wait_gone(int(upid.group(1)), timeout=20) if upid else (True, 0)
        check("15o-3 stop_follow 之后那个子进程确实不在了", gone4,
              "alive=%s" % (_proc_alive(int(upid.group(1))) if upid else False))
        check("15o-4 stop_follow 之后屏幕上没有边框（EnumWindows=%d）"
              % len(_enum_border_hwnds()), len(_enum_border_hwnds()) == 0,
              _enum_border_hwnds())

        section("用例15 附加 · 冻结态与开关（如实报告，不静默）")
        env2 = dict(env)
        env2["AGENTCTL_BORDER"] = "0"
        mp2 = subprocess.run([sys.executable, "-X", "utf8", "-c",
                              "import sys,os;sys.path.insert(0,os.environ['PYTHONPATH']);"
                              "from agentctl import ctrl_border;"
                              "print('start_follow ->', ctrl_border.start_follow('selftest'));"
                              "ctrl_border.stop_follow()"],
                             env=env2, cwd=tmp, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        print("  [15d AGENTCTL_BORDER=0] %s" % (mp2.stdout or "").strip(), flush=True)
        check("15m AGENTCTL_BORDER=0 ⇒ start_follow 返回 0 且**如实打日志**说跳过",
              "start_follow -> 0" in (mp2.stdout or "") and "AGENTCTL_BORDER=0" in rlog(),
              (mp2.stdout or "").strip())

        # 冻结态：用 sys.frozen=True 假装冻结 ⇒ 期望「跳过边框接线」的如实警告
        env3 = dict(env)
        mp3 = subprocess.run([sys.executable, "-X", "utf8", "-c",
                              "import sys,os;sys.path.insert(0,os.environ['PYTHONPATH']);"
                              "sys.frozen=True;"
                              "from agentctl import ctrl_border;"
                              "print('start_follow ->', ctrl_border.start_follow('frozen-test'))"],
                             env=env3, cwd=tmp, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        lg3 = rlog()          # ★ 先读再清（原来反过来了，等于自己把刚写的证据擦掉）
        print("  [15d 冻结态] stdout=%r" % (mp3.stdout or "").strip(), flush=True)
        check("15n ★ 冻结态（sys.frozen=True）⇒ 返回 0 且**如实警告**「跳过边框接线」，"
              "不静默", "start_follow -> 0" in (mp3.stdout or "")
              and "冻结态" in lg3 and "跳过边框接线" in lg3, lg3[-400:])
    finally:
        for p in procs:
            try:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=8)
                    except Exception:
                        p.kill()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def _prod_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "AgentCtl")


def snapshot_prod():
    """★ 窗Z 新增：生产目录逐项快照 {name: (size, mtime_ns)}；目录不存在 ⇒ None。只读。"""
    d = _prod_dir()
    if not os.path.isdir(d):
        return None
    snap = {}
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if os.path.isfile(p):
            st = os.stat(p)
            snap[n] = (st.st_size, st.st_mtime_ns)
    return snap


# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# 窗J（2026-10-18）：**跨进程互斥** —— 并发跑会互相污染（DSH 已实测）
# ═══════════════════════════════════════════════════════════════
# 实测现场（DSH 15:4x 并发压力自查，两份 all 同时跑）：
#   A: 红 1 —— 10c「第二个没产生第二组窗口（EnumWindows 仍是 16 个）」
#   B: 红 1 —— 15o-4「stop_follow 之后屏幕上没有边框」，列出的 8 个 hwnd 是**对方**的窗口
# 不是判据坏了，是两份实例**共用同一块屏幕 + 同一套单例互斥量**，
#   互相把对方的边框窗数进自己账上。run_all 是串行所以看不见；
#   两个 agent 同时跑、或将来并行化就会踩。

W3_MUTEX_NAME = "Local\\AgentCtlT1W3SelfTest_v1"
W3_OWNER_FILE = os.path.join(tempfile.gettempdir(), "AgentCtlT1W3SelfTest.owner")


def _proc_start_str(pid):
    """pid 的启动时间（人读）。**拿不到就说「未知」，绝不猜**。"""
    try:
        import datetime as _dt
        import psutil
        return _dt.datetime.fromtimestamp(
            psutil.Process(int(pid)).create_time()).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "未知"


def w3_acquire_mutex():
    """拿跨进程互斥。返回 (句柄 or None, 占用者 pid 或 0, 说明/占用者二元组)。

    ★ 选**命名互斥量**（CreateMutexW）而不是锁文件：进程一死**内核自动释放**，
      不会留下需要「猜 pid 死没死」的孤儿锁；锁文件被强杀后残留，还得做存活探测，
      反而更容易误判成「有人在跑」而白挡一次。
    ★ 本函数**只碰互斥量与一个 owner 小文件**，绝不建窗、不 spawn 任何东西
      —— 判定必须发生在任何副作用之前（见 main）。
    """
    import ctypes
    import ctypes.wintypes as wt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    k32.CreateMutexW.restype = wt.HANDLE
    ERROR_ALREADY_EXISTS = 183
    ctypes.set_last_error(0)
    h = k32.CreateMutexW(None, True, W3_MUTEX_NAME)
    err = ctypes.get_last_error()
    if not h:
        return None, 0, "CreateMutexW 失败（GetLastError=%d）——这不是「有人在跑」，" % err
    if err != ERROR_ALREADY_EXISTS:
        try:
            io.open(W3_OWNER_FILE, "w", encoding="utf-8").write(
                "%d\n%s\n" % (os.getpid(), _proc_start_str(os.getpid())))
        except Exception:
            pass            # 落盘失败不影响互斥本身，别因此误判成「有人在跑」
        return h, 0, ""
    other, started = 0, "未知"
    try:
        with io.open(W3_OWNER_FILE, encoding="utf-8") as f:
            _ls = [x.strip() for x in f.read().splitlines() if x.strip()]
        if _ls:
            other = int(_ls[0])
        if len(_ls) > 1:
            started = _ls[1]
    except Exception:
        pass
    k32.CloseHandle(h)
    return None, other, (other, started)


def w3_release_mutex(h):
    """释放互斥（正常退出与异常退出都走这里）。句柄为空则什么都不做。"""
    if not h:
        return
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.ReleaseMutex(h)
        k32.CloseHandle(h)
    except Exception:
        pass
    try:
        if os.path.exists(W3_OWNER_FILE):
            os.remove(W3_OWNER_FILE)
    except Exception:
        pass


def w3_busy_report(other, started):
    """拿不到锁时的文案：点名是谁、说清为什么不可信、给下一步。"""
    return "\n".join([
        "⛔ w3 自证 · 跨进程互斥**没拿到** —— 本次**不跑**（exit 2，不是红也不是绿）。",
        "",
        "另一份 w3 自证正在跑：pid=%s，启动于 %s。" % (other or "未知", started),
        "",
        "为什么必须挡住：border/wire 段要**真建边框窗**、并占用同一套单例互斥量",
        "（AgentCtlCtrlBorderFollow）。两份同时跑 = **共用整块屏幕 + 共用单例**，",
        "互相把对方的边框窗数进自己账上 ⇒ **结果不可信**。",
        "实测现场（2026-10-18 15:4x DSH 并发压力自查）：",
        "  · 一份红 10c「第二个没产生第二组窗口（EnumWindows 仍是 16 个）」",
        "  · 另一份红 15o-4「stop_follow 之后屏幕上没有边框」，列出的 8 个 hwnd 是对方的窗",
        "那两条红**不是判据坏了**，是并发造成的假账。",
        "",
        "下一步（二选一）：",
        "  ① 等它跑完，再起这一份；",
        "  ② 确定它卡住了，先 taskkill %s 再重跑。" % (other or "<那一份的 pid>"),
        "",
        "本次**一个窗口都没建、没 spawn 任何子进程** —— 互斥判定在 main() 最前面，",
        "早于 argparse 与任何建窗动作，不会反过来污染那一份。",
    ])


def main(argv=None):
    """脚本入口。窗J：跨进程互斥在这里拿 —— 早于 argparse、早于任何 spawn/建窗。

    拿不到锁 -> 打印点名文案并 return 2（既不是红也不是绿），一个窗都不建。
    正常/异常退出都经 finally 释放（进程死时内核也会兜底释放）。
    """
    _h, _other, _info = w3_acquire_mutex()
    if not _h:
        if isinstance(_info, tuple):
            print(w3_busy_report(_info[0], _info[1]), flush=True)
        else:
            print("w3 自证 · 互斥不可用：%s" % _info, flush=True)
        return 2
    try:
        return _main_locked(argv)
    finally:
        w3_release_mutex(_h)


def _main_locked(argv=None):
    ap = argparse.ArgumentParser(description="T1 窗3 自证（W3-A 坐标标注 / W3-B 边框）")
    ap.add_argument("what", choices=("coords", "border", "wire", "all"))
    ap.add_argument("--src", default=None,
                    help="把 agentctl 从这个目录导入（指到 git show HEAD 导出的旧版"
                         "目录 ⇒ 同一套判据跑旧代码，就是改前红证据）")
    ap.add_argument("--hwnd", type=int, default=0,
                    help="coords 段的靶子窗口；不给就自己造一个夹具窗")
    a = ap.parse_args(argv)
    src_dir = os.path.abspath(a.src) if a.src else SRC
    t0 = time.monotonic()
    pre_prod = snapshot_prod()   # ★ 窗Z：生产目录跑前快照（18 号断言用）
    # ★ 窗Z 背景写者探测：生产 log 在**判据未启动时**若已在增长，说明有别的进程
    #   （常驻服务/并行判据作业）在写它 ⇒ 跑前跑后快照无法归因 ⇒ 18 记「验不了」
    #   （如实登记，不计红也不装绿）；探测稳定 ⇒ 18 正常严格断言。
    _bg_writer, _bg_s1, _bg_s2 = False, None, None
    _plog18 = os.path.join(_prod_dir(), "ctrl_border.log")
    if os.path.isfile(_plog18):
        _bg_s1 = os.path.getsize(_plog18)
        time.sleep(1.2)
        _bg_s2 = os.path.getsize(_plog18)
        _bg_writer = _bg_s2 != _bg_s1
    print("=" * 78, flush=True)
    print("T1 窗3 自证 · 判据段=%s · src=%s · pid=%d"
          % (a.what, src_dir, os.getpid()), flush=True)
    print("=" * 78, flush=True)
    try:
        if a.what in ("coords", "all"):
            coords(src_dir, a.hwnd or None)
        if a.what in ("border", "all"):
            border(src_dir)
        if a.what in ("wire", "all"):
            wire(src_dir)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("自证脚本本身没崩（%s: %s）" % (type(e).__name__, e), False, "见上")
    # ★ 18（窗Z · 2026-10-17 新增）：生产目录没被碰 —— 跑前跑后逐项快照（名字+大小+mtime）
    #   完全一致；目录原本不存在 ⇒ 允许"跑完仍不存在"。mtime 用 ns 级：任何触碰都算变化。
    post_prod = snapshot_prod()
    if _bg_writer:
        SKIP.append("18（窗Z）· 生产目录快照：探测到 ctrl_border.log 在判据未启动时已被"
                    "**其他进程**持续写入（跑前两次采样 size %d→%d）—— 谁写的无法归因，"
                    "本判据无法自证清白，如实登记验不了；生产文件：%s"
                    % (_bg_s1, _bg_s2, _plog18))
    elif pre_prod is None:
        check("18（窗Z）· 生产目录原本不存在 ⇒ 跑完仍不存在",
              post_prod is None,
              "跑后=%s" % ("仍不存在" if post_prod is None
                           else "出现了 %d 项" % len(post_prod)))
    else:
        _diffs = []
        for _n in sorted(set(pre_prod) | set(post_prod or {})):
            _a, _b = pre_prod.get(_n), (post_prod or {}).get(_n)
            if _a is None:
                _diffs.append("%s 新增(size=%s)" % (_n, _b[0] if _b else "?"))
            elif _b is None:
                _diffs.append("%s 被删除" % _n)
            elif _a != _b:
                _diffs.append("%s 变化 size %s→%s" % (_n, _a[0], _b[0]))
        check("18（窗Z）· 生产目录 %s 跑前跑后逐项快照完全一致"
              % _prod_dir(), not _diffs,
              "；".join(_diffs[:5]) if _diffs else "逐项一致（%d 项）" % len(pre_prod))
    print("\n" + "=" * 78, flush=True)
    print("判据汇总：绿 %d 条 / 红 %d 条 / 验不了 %d 条，用时 %.1fs"
          % (len(OK), len(BAD), len(SKIP), time.monotonic() - t0), flush=True)
    if SKIP:
        print("验不了的（**不是通过，也不是失败** —— 本机环境做不到，如实登记）：",
              flush=True)
        for sk in SKIP:
            print("  ~ %s" % sk, flush=True)
    if BAD:
        print("红的条目（这就是\"还没做到\"的清单）：", flush=True)
        for b in BAD:
            print("  - %s" % b, flush=True)
    print("=" * 78, flush=True)
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
