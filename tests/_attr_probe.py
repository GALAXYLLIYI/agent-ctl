# -*- coding: utf-8 -*-
r"""tests\_attr_probe.py —— 输入归因层·最小探针（**只观察，不干预**）

================================ 铁律 ====================================
★ **只监听、只记录**。本文件绝不：
    · 调用 BlockInput（锁死输入）
    · 在钩子回调里返回 0（返回 0 ＝ 吞噬事件）
    · 改写任何按键/鼠标事件
  钩子回调**永远返回 CallNextHookEx 的结果**（=1 放行），
  即：本文件在系统输入链路上的存在对用户完全透明。

★ **跑完即退**：默认监听 8 秒，自己退出。没有常驻、没有后台线程残留
  （只有一个"到点叫醒主线程"的临时线程，退出前 join）。

★ **不装依赖**：只用 ctypes 走 Win32 原始 API（Python 3.12 标准库自带）。

★ **不读密钥**：不碰 work\state\token 之类；对账只取 timeline 的
  seq/kind/actor/tool/t_wall 五个字段，**不复制 payload 正文**。

================================ 用法 ====================================
    python -X utf8 tests\_attr_probe.py --seconds 8
    python -X utf8 tests\_attr_probe.py --seconds 8 --no-self-test
    python -X utf8 tests\_attr_probe.py --seconds 8 --json

  原始输出同时落 **同目录下的** attr_probe_out.txt（路径由 __file__ 推，不写死盘符）

================================ 自检（自注入，零视觉影响）=====================
  装好钩子后，用 SendInput 发一次 **"绝对移动到光标当前所在像素"** 的鼠标移动：
    · 光标**一个像素都不动** ⇒ 用户看不到任何东西
    · 但它必然产生一次真实的 WM_MOUSEMOVE ⇒ 能证明
      「钩子装上了 + 回调确实被调用 + 注入位(LLMHF_INJECTED)能读到」
  这一条是探针的**自证**：没有它，"没收到事件"就分不清是
  "钩子坏了"还是"这几秒刚好没人碰键盘"。

================================ 归因证据字段 ====================================
  每条事件记：
    t_mono / t_wall      单调钟 + 墙钟（双钟，漂移可查）
    device / vkCode / scanCode / flags / dwExtraInfo / time
    injected / lower_il  由 flags 解析（见 FLAGS 表）
  flags 解析口径（Windows 官方 KLLHOOKSTRUCT.flags / MSLLHOOKSTRUCT.flags）：
    键盘 LLKHF_INJECTED        = 0x10  程序注入
    键盘 LLKHF_LOWER_IL_INJECTED= 0x02  低层注入
    鼠标 LLMHF_INJECTED        = 0x01  程序注入
    鼠标 LLMHF_LOWER_IL_INJECTED=0x02  低层注入
    物理硬件输入**不带**这些位。

================================ 与 timeline 的对账 ====================================
  读 work\timeline\timeline-*.jsonl（**真相源**，不是 SQLite 索引），
  对每条探针事件找 ±WINDOW 秒内、kind ∈ {click,type,keys,scroll,fill,shot} 的
  timeline 行，报出 seq/kind/actor/tool 与时间差。
  ★ 只报"候选"，不下判决 —— 判决口径见设计稿的《归因判据表》。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import datetime as _dt
import glob
import json
import os
import sys
import threading
import time

# ── 路径 ────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TIMELINE_DIR = os.path.join(ROOT, "work", "timeline")
OUT_TXT = os.path.join(HERE, "attr_probe_out.txt")


def _rel(p: str) -> str:
    """把路径转成**相对仓根**的写法。

    ★ 为什么必须这样：这份原始输出要进版本库（tests/fixtures/）。
      若输出里带本机绝对路径，就等于把仓根路径公开给所有 clone 的人
      （本仓 tests\\_写死路径判据.py 的「仓根清零」就是查这个）。
    """
    try:
        return os.path.relpath(p, ROOT).replace("\\", "/")
    except Exception:
        return os.path.basename(p)

# ── Win32 常量 ──────────────────────────────────────────────────────────
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105

LLKHF_ALTDOWN = 0x01
LLKHF_LOWER_IL_INJECTED = 0x02
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
LLMHF_LOWER_IL_INJECTED = 0x02

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_REMOTESESSION = 0x1000

# 对账窗口：事件与 timeline 行相差多少秒算"可能相关"
RECONCILE_WINDOW_S = 2.0


# ── 结构体 ──────────────────────────────────────────────────────────────
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_long
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]


# ── 采集缓冲（回调里只 append，不做任何 I/O）────────────────────────────
EVENTS: list[dict] = []
_LOCK = threading.Lock()
SELF_TEST_TAG = "selftest-abs-move"


def _now() -> tuple[float, str]:
    m = time.perf_counter()
    w = time.time()
    return m, _dt.datetime.fromtimestamp(w).astimezone().isoformat(timespec="milliseconds")


def _kb_proc(code, wparam, lparam):
    """键盘低层钩子。**永远放行**（返回 CallNextHookEx 的结果）。

    ★★★ 安全不变量：本回调**在任何情况下都不得返回 0**。
      返回 0 ＝ 吞噬该次按键。ctypes 在回调里抛异常时会**忽略异常并返回 None**
      （None → 0）⇒ 那就等于吞事件。所以最外层再兜一层：
      任何异常都 return 1（放行）。
    """
    try:
        if code >= 0:
            try:
                st = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                m, w = _now()
                with _LOCK:
                    EVENTS.append({
                        "device": "keyboard", "t_mono": round(m, 6), "t_wall": w,
                        "msg": int(wparam), "vkCode": int(st.vkCode),
                        "scanCode": int(st.scanCode), "flags": int(st.flags),
                        "injected": bool(st.flags & LLKHF_INJECTED),
                        "lower_il": bool(st.flags & LLKHF_LOWER_IL_INJECTED),
                        "altDown": bool(st.flags & LLKHF_ALTDOWN),
                        "dwExtraInfo": int(st.dwExtraInfo), "time": int(st.time),
                    })
            except Exception:
                pass
    except Exception:
        pass
    try:
        return user32.CallNextHookEx(None, code, wparam, lparam)
    except Exception:
        return 1          # ★ 兜底放行；**绝不返回 0**


def _mouse_proc(code, wparam, lparam):
    """鼠标低层钩子。**永远放行**（同 _kb_proc 的安全不变量）。"""
    try:
        if code >= 0:
            try:
                st = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                m, w = _now()
                with _LOCK:
                    EVENTS.append({
                        "device": "mouse", "t_mono": round(m, 6), "t_wall": w,
                        "msg": int(wparam), "x": int(st.pt.x), "y": int(st.pt.y),
                        "mouseData": int(st.mouseData), "flags": int(st.flags),
                        "injected": bool(st.flags & LLMHF_INJECTED),
                        "lower_il": bool(st.flags & LLMHF_LOWER_IL_INJECTED),
                        "dwExtraInfo": int(st.dwExtraInfo), "time": int(st.time),
                    })
            except Exception:
                pass
    except Exception:
        pass
    try:
        return user32.CallNextHookEx(None, code, wparam, lparam)
    except Exception:
        return 1          # ★ 兜底放行；**绝不返回 0**


def _last_input_tick() -> int | None:
    """GetLastInputInfo：最后一次输入距今的 tick 数（用来量"闲置多久"）。"""
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if user32.GetLastInputInfo(ctypes.byref(lii)):
            return int(lii.dwTime)
    except Exception:
        pass
    return None


def _integrity_sid() -> str | None:
    """本进程完整性级别 SID（如 S-1-16-8192 = Medium）。**决定本探针能看见哪些窗口的输入。**"""
    try:
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        TOKEN_QUERY = 0x0008
        TokenIntegrityLevel = 25
        adv.OpenProcessToken.argtypes = [wintypes.HANDLE, ctypes.c_ulong,
                                         ctypes.POINTER(wintypes.HANDLE)]
        adv.OpenProcessToken.restype = wintypes.BOOL
        adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        adv.GetTokenInformation.restype = wintypes.BOOL
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        adv.ConvertSidToStringSidW.restype = wintypes.BOOL
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        tok = wintypes.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            n = wintypes.DWORD()
            adv.GetTokenInformation(tok, TokenIntegrityLevel, None, 0, ctypes.byref(n))
            if not n.value:
                return None
            buf = ctypes.create_string_buffer(n.value)
            if not adv.GetTokenInformation(tok, TokenIntegrityLevel, buf, n, ctypes.byref(n)):
                return None
            # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES Label; }  SID_AND_ATTRIBUTES { PSID Sid; DWORD Attributes; }
            p_sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p)).contents
            out = ctypes.c_wchar_p()
            if not adv.ConvertSidToStringSidW(p_sid, ctypes.byref(out)):
                return None
            try:
                return out.value
            finally:
                k32.LocalFree(ctypes.cast(out, ctypes.c_void_p))
        finally:
            k32.CloseHandle(tok)
    except Exception:
        return None


def _session_info() -> dict:
    """会话信息：session id + 是否远程会话（RDP）。
    ★ 远程会话里"电脑前的人"可能压根不在电脑前 ⇒ 归因必须知道这个。"""
    out = {"session_id": None, "remote_session": None, "integrity_sid": _integrity_sid()}
    try:
        sid = wintypes.DWORD()
        if kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
            out["session_id"] = int(sid.value)
    except Exception:
        pass
    try:
        out["remote_session"] = bool(user32.GetSystemMetrics(SM_REMOTESESSION))
    except Exception:
        pass
    return out


def _dpi_aware() -> bool:
    """PER_MONITOR_AWARE_V2。

    ★ 必须做：进程默认是 DPI-unaware ⇒ GetCursorPos / GetSystemMetrics 返回
      **逻辑坐标**，而低级钩子 KBDLLHOOKSTRUCT/鼠标 pt 给的是**物理像素**。
      本机实测 150% 缩放下两套坐标差 1.5 倍（逻辑 1510 → 物理 2265）。
      不切 DPI 感知，两套坐标对不上，任何按坐标的对账都会错位。
    """
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        return bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
    except Exception:
        return False


def _self_test_abs_move() -> dict:
    """自注入：把光标**绝对移动到它自己当前所在像素** ⇒ 零位移，但必然产生一次 WM_MOUSEMOVE。

    ★ 绝对移动比相对移动可靠：实测「相对 (0,0) 移动」会被 Windows 直接优化掉
      （SendInput 返回 1，但钩子**收不到**事件）；绝对移动则必定投递。
    ★ 光标位移在前后各取一次 GetCursorPos **实测**，不自夸「零影响」。
    """
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return {"ok": False, "why": "GetCursorPos 失败"}
    sw = user32.GetSystemMetrics(SM_CXSCREEN) or 1
    sh = user32.GetSystemMetrics(SM_CYSCREEN) or 1
    nx = int((pt.x * 65535) / max(1, sw - 1))
    ny = int((pt.y * 65535) / max(1, sh - 1))
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(dx=nx, dy=ny, mouseData=0,
                        dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                        time=0, dwExtraInfo=None)
    before = len(EVENTS)
    n = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    time.sleep(0.25)
    with _LOCK:
        got = [e for e in EVENTS[before:] if e.get("device") == "mouse"]
    pt2 = POINT()
    user32.GetCursorPos(ctypes.byref(pt2))
    dx = int(pt2.x) - int(pt.x)
    dy = int(pt2.y) - int(pt.y)
    return {"ok": bool(n), "sent": int(n), "cursor_before": [int(pt.x), int(pt.y)],
            "cursor_after": [int(pt2.x), int(pt2.y)], "cursor_delta": [dx, dy],
            "absolute_target": [nx, ny], "events_seen": len(got),
            "first_event": got[0] if got else None,
            "flag_injected": bool(got and got[0].get("injected")),
            "tag": SELF_TEST_TAG}


# ── timeline 对账（真相源 = jsonl，不是 SQLite 索引）────────────────────
def load_timeline_rows() -> tuple[list[dict], dict]:
    """读 work\timeline\timeline-*.jsonl。**只取 seq/kind/actor/tool/t_wall**，
    不复制 payload 正文（payload 里可能有用户输入内容）。"""
    info = {"files": [], "rows": 0, "dup_seq_groups": 0, "chain_breaks": 0, "first_break": None}
    rows: list[dict] = []
    seen: dict[int, int] = {}
    files = sorted(glob.glob(os.path.join(TIMELINE_DIR, "timeline-*.jsonl")))
    prev_hash = "0" * 64
    import hashlib

    def canonical(o):
        return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    for fn in files:
        info["files"].append(os.path.basename(fn))
        try:
            with open(fn, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    seq = int(r.get("seq", 0))
                    seen[seq] = seen.get(seq, 0) + 1
                    body = {k: v for k, v in r.items() if k != "hash"}
                    h = hashlib.sha256()
                    h.update(prev_hash.encode("ascii"))
                    h.update(b"\n")
                    h.update(canonical(body).encode("utf-8"))
                    if r.get("prev_hash") != prev_hash or r.get("hash") != h.hexdigest():
                        info["chain_breaks"] += 1
                        if info["first_break"] is None:
                            info["first_break"] = {"file": os.path.basename(fn), "seq": seq,
                                                   "kind": r.get("kind"), "actor": r.get("actor")}
                        prev_hash = r.get("hash") or prev_hash
                    else:
                        prev_hash = r.get("hash") or prev_hash
                    try:
                        ts = _dt.datetime.fromisoformat(r.get("t_wall")).timestamp()
                    except Exception:
                        ts = 0.0
                    rows.append({"seq": seq, "kind": r.get("kind"), "actor": r.get("actor"),
                                 "tool": r.get("tool"), "t_wall": r.get("t_wall"), "_ts": ts})
        except OSError:
            continue
    info["rows"] = len(rows)
    info["dup_seq_groups"] = sum(1 for v in seen.values() if v > 1)
    info["dup_extra_lines"] = sum(v - 1 for v in seen.values() if v > 1)
    return rows, info


ACT_KINDS = {"click", "type", "keys", "scroll", "fill", "shot", "focus", "browser"}


def reconcile(events: list[dict], rows: list[dict], window: float = RECONCILE_WINDOW_S) -> list[dict]:
    """给每条探针事件找 timeline 候选。**只报候选，不下判决**。"""
    out = []
    for e in events:
        m = e.get("t_mono")
        # 用墙钟对（timeline 只有墙钟）
        try:
            wts = _dt.datetime.fromisoformat(e["t_wall"]).timestamp()
        except Exception:
            continue
        cands = []
        for r in rows:
            if r["kind"] not in ACT_KINDS:
                continue
            d = abs(r["_ts"] - wts)
            if d <= window:
                cands.append((d, r))
        cands.sort(key=lambda t: t[0])
        best = None
        if cands:
            d, r = cands[0]
            best = {"seq": r["seq"], "kind": r["kind"], "actor": r["actor"], "tool": r["tool"],
                    "t_wall": r["t_wall"], "dt_ms": int(round(d * 1000)),
                    "n_candidates": len(cands)}
        out.append({
            "t_wall": e["t_wall"], "device": e["device"],
            "injected": e.get("injected"), "lower_il": e.get("lower_il"),
            "vkCode": e.get("vkCode"), "x": e.get("x"), "y": e.get("y"),
            "timeline_candidate": best,
        })
    return out


# ── 主流程 ──────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="输入归因层最小探针（只观察，不干预）")
    ap.add_argument("--seconds", type=float, default=8.0, help="监听秒数（默认 8，跑完即退）")
    ap.add_argument("--no-self-test", action="store_true", help="跳过自注入自检")
    ap.add_argument("--json", action="store_true", help="额外输出 JSON 段")
    ap.add_argument("--out", default=OUT_TXT, help="原始输出落盘路径")
    args = ap.parse_args()

    lines: list[str] = []

    def emit(s: str = ""):
        lines.append(s)
        print(s, flush=True)

    emit("=" * 78)
    emit("输入归因层·最小探针  tests/_attr_probe.py")
    emit("=" * 78)
    emit("模式        : 只观察（钩子回调恒返回 CallNextHookEx ⇒ 不吞噬、不改写）")
    emit("监听窗口    : %.1f 秒（跑完即退，无常驻）" % args.seconds)
    emit("python      : %s" % sys.version.split()[0])
    emit("宿主进程 pid: %d   主线程 id: %d" % (os.getpid(), kernel32.GetCurrentThreadId()))
    emit("时间戳来源  : t_mono=perf_counter（单调）／ t_wall=ISO8601（墙钟）")
    dpi_ok = _dpi_aware()
    emit("DPI 感知    : PER_MONITOR_AWARE_V2 = %s（否则逻辑坐标与钩子物理坐标差一个缩放倍数）" % dpi_ok)
    ctx = _session_info()
    emit("进程上下文  : 完整性=%s  会话 id=%s  远程会话(RDP)=%s"
         % (ctx.get("integrity_sid"), ctx.get("session_id"), ctx.get("remote_session")))
    emit("             ★ 完整性级别决定本探针能看见哪些窗口的输入："
         "低级钩子**看不见**比自己级别更高的窗口。")
    emit()

    # ── 1. 装钩子 ──
    emit("── ① 装低级钩子 ──────────────────────────────────────────────")
    # ★ hMod 必须给 NULL：钩子过程就在本进程里（文档：dwThreadId 指当前进程且
    #   钩子过程在当前进程的代码里时，hMod 可以为 NULL）。
    #   ★ 实测（本机 2026-10-21）：传 exe 的模块句柄会 **失败并返回错误 126
    #     (ERROR_MOD_NOT_FOUND)**；传 NULL 才装得上。
    kb_proc = HOOKPROC(_kb_proc)
    ms_proc = HOOKPROC(_mouse_proc)
    ctypes.set_last_error(0)
    hk_kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb_proc, None, 0)
    err_kb = ctypes.get_last_error()
    ctypes.set_last_error(0)
    hk_ms = user32.SetWindowsHookExW(WH_MOUSE_LL, ms_proc, None, 0)
    err_ms = ctypes.get_last_error()
    emit("  SetWindowsHookExW(WH_KEYBOARD_LL=%d, hMod=NULL) -> hhook=%s  err=%d %s"
         % (WH_KEYBOARD_LL, hk_kb, err_kb, "" if hk_kb else "（失败：可能被安全软件或权限挡）"))
    emit("  SetWindowsHookExW(WH_MOUSE_LL=%d,    hMod=NULL) -> hhook=%s  err=%d %s"
         % (WH_MOUSE_LL, hk_ms, err_ms, "" if hk_ms else "（失败：可能被安全软件或权限挡）"))
    emit("  ★ 本探针**从不**调用 BlockInput，钩子回调**从不**返回 0 ⇒ 对用户输入完全透明。")
    emit()
    if not hk_kb and not hk_ms:
        emit("!! 两个钩子都没装上 ⇒ 本机当前无法做输入归因（见设计稿《失败与降级语义》）")
        _write_out(args.out, lines)
        return 3

    # ── 2. 消息泵 ──
    thread_id = kernel32.GetCurrentThreadId()
    stop_evt = threading.Event()
    stopper = threading.Thread(target=lambda: (stop_evt.wait(args.seconds),
                                               user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)),
                               daemon=True)
    stopper.start()

    idle_tick_before = _last_input_tick()

    # ── 3. 自检（零视觉影响）──
    st = None
    if not args.no_self_test:
        emit("── ② 自检：自注入一次「绝对移动到光标当前像素」────────────────")
        st = _self_test_abs_move()
        emit("  光标位置    : 前=%s 后=%s  **实测位移=%s**"
             % (st.get("cursor_before"), st.get("cursor_after"), st.get("cursor_delta")))
        emit("  SendInput    : sent=%s  收到的鼠标事件=%s"
             % (st.get("sent"), st.get("events_seen")))
        fe = st.get("first_event")
        if fe:
            emit("  ★ 回调确实被调用：msg=0x%04X flags=0x%02X injected=%s lower_il=%s"
                 % (fe.get("msg", 0), fe.get("flags", 0), fe.get("injected"), fe.get("lower_il")))
            emit("  ★ 注入位判读成立：LLMHF_INJECTED(0x01) %s"
                 % ("读到 ⇒ 本机能区分注入 vs 物理" if st.get("flag_injected")
                    else "**没读到** ⇒ 本机注入位不可靠，归因只能降级"))
        else:
            emit("  ✗ 自检事件没回来 ⇒ 钩子链路有问题（不是「恰好没人操作」）")
        emit()

    emit("── ③ 监听窗口（%.1f 秒）────────────────────────────────" % args.seconds)
    msg = wintypes.MSG()
    got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
    emit("  GetMessageW 返回 %s（0 = 收到 WM_QUIT ⇒ 正常到点退出）" % got)
    stop_evt.set()
    stopper.join(timeout=2.0)
    idle_tick_after = _last_input_tick()
    emit()

    # ── 4. 卸钩子 ──
    emit("── ④ 卸钩子 ─────────────────────────────────────────────────")
    ok_kb = user32.UnhookWindowsHookEx(hk_kb) if hk_kb else 0
    ok_ms = user32.UnhookWindowsHookEx(hk_ms) if hk_ms else 0
    emit("  UnhookWindowsHookEx(keyboard) -> %s" % bool(ok_kb))
    emit("  UnhookWindowsHookEx(mouse)    -> %s" % bool(ok_ms))
    emit()

    # ── 5. 事件统计 ──
    with _LOCK:
        evs = list(EVENTS)
    emit("── ⑤ 采到的事件 ─────────────────────────────────────────────")
    emit("  总数 %d   键盘 %d   鼠标 %d"
         % (len(evs), sum(1 for e in evs if e["device"] == "keyboard"),
            sum(1 for e in evs if e["device"] == "mouse")))
    inj = [e for e in evs if e.get("injected")]
    emit("  其中 injected=True 的：%d" % len(inj))
    for e in evs[:40]:
        if e["device"] == "keyboard":
            emit("    %s kb  msg=0x%04X vk=%-3d scan=%-3d flags=0x%02X inj=%-5s lower=%-5s extra=0x%X"
                 % (e["t_wall"], e["msg"], e["vkCode"], e["scanCode"], e["flags"],
                    e["injected"], e["lower_il"], e["dwExtraInfo"]))
        else:
            emit("    %s ms  msg=0x%04X (%3d,%3d) flags=0x%02X inj=%-5s lower=%-5s extra=0x%X"
                 % (e["t_wall"], e["msg"], e["x"], e["y"], e["flags"],
                    e["injected"], e["lower_il"], e["dwExtraInfo"]))
    if len(evs) > 40:
        emit("    ……（其余 %d 条略）" % (len(evs) - 40))
    emit()

    # ── 6. 对账 ──
    emit("── ⑥ 与 timeline 对账（真相源 = work/timeline/timeline-*.jsonl）──")
    rows, info = load_timeline_rows()
    emit("  jsonl 文件 %d 个   事件行 %d" % (len(info["files"]), info["rows"]))
    emit("  ★ 链完整性：断链 %d 处（首个：%s）" % (info["chain_breaks"], info["first_break"]))
    emit("  ★ 重复 seq 组 %d（多出的行 %d）⇒ SQLite 索引里被 INSERT OR REPLACE 覆盖掉了那么多条"
         % (info["dup_seq_groups"], info["dup_extra_lines"]))
    emit("  ★ 对账结论：sqlite events 表**不能**当真相源，核对必须回 jsonl。")
    emit()
    recs = reconcile(evs, rows)
    matched = [r for r in recs if r.get("timeline_candidate")]
    emit("  探针事件 %d 条，其中能在 ±%.1fs 内对上一条 timeline 动作的：%d 条"
         % (len(recs), RECONCILE_WINDOW_S, len(matched)))
    for r in matched[:20]:
        c = r["timeline_candidate"]
        emit("    %s %-8s inj=%-5s  ⇢ seq=%s kind=%-8s actor=%-10s tool=%-12s Δ=%dms"
             % (r["t_wall"], r["device"], r["injected"], c["seq"], c["kind"],
                c["actor"], c["tool"], c["dt_ms"]))
    if not matched:
        emit("    （本次窗口内没有 agent 动作发生 ⇒ 没有候选，属正常）")
    emit()

    emit("── ⑦ 结论（本探针只给证据，不下判决）─────────────────────────")
    emit("  · 钩子可用：%s" % ("是（两个都装上）" if (hk_kb and hk_ms)
                              else ("部分（只有键盘或只有鼠标）" if (hk_kb or hk_ms) else "否")))
    emit("  · 回调链路：%s" % ("通（自检事件已回来）" if (st and st.get("events_seen")) else "未验证"))
    emit("  · 注入位可判读：%s" % ("是" if (st and st.get("flag_injected")) else "否/未验证"))
    emit("  · 观测到的注入事件：%d 条；物理事件：%d 条"
         % (len(inj), len(evs) - len(inj)))
    emit("  · 闲置刻度 GetLastInputInfo：before=%s after=%s" % (idle_tick_before, idle_tick_after))
    emit("  · 判决口径见设计稿《归因判据表》；判据骨架见 tests/_P1_输入归因_判据.py")
    emit()
    emit("=" * 78)
    emit("探针结束（已退出，无常驻进程）。")
    emit("原始输出留：%s" % _rel(args.out))

    if args.json:
        payload = {"events": evs, "self_test": st, "timeline": info, "reconcile": recs,
                   "hooks": {"keyboard": bool(hk_kb), "mouse": bool(hk_ms)},
                   "context": ctx,
                   "idle_tick": {"before": idle_tick_before, "after": idle_tick_after}}
        lines.append("---JSON---")
        lines.append(json.dumps(payload, ensure_ascii=False, indent=2))

    _write_out(args.out, lines)
    return 0


def _write_out(path: str, lines: list[str]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("!! 原始输出写盘失败：%s" % e, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
