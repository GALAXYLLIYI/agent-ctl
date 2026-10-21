# -*- coding: utf-8 -*-
"""auth_popup.py —— 电脑控制的第 2 步：用户亲自授权的弹窗闸门

设计红线（准则 铁律 11）：
  * 密钥由用户**自己在弹窗里输入**，助手永远看不到明文；
  * 本地只存**加盐 SHA-256 校验值**，不存明文、不写日志、不回显（输入框打点）；
  * 每次申请都要用户**当场点一次**，通过后只发一张**限时 + 限定范围**的通行证；
  * 输错 → 锁定 60 秒（锁定状态持久化，重启弹窗也绕不过）；
  * 全程留痕：谁在什么时候、为哪个范围、授权还是拒绝。

**诚实说明**：这是「人必须在场」的确认闸，不是防御同用户恶意进程的密码学边界
（助手与用户同一 Windows 账户，能读的文件助手也能读）。真正的边界是**用户的手指**和`急停`。

用法：
  python auth_popup.py --set                      # 首次设置/修改密钥（输两遍）
  python auth_popup.py --request "A级 只读感知"    # 申请一次授权（默认 30 分钟 / 60 次）
  python auth_popup.py --status                   # 看当前通行证
  python auth_popup.py --check "A级 只读感知"      # 校验通行证是否可用（供控制脚本调用）

T5 新增（可选，最小改动）：
  python auth_popup.py --request "B级 点击滚动" \
      --targets '{"pids":[1234],"title_contains":["记事本"],"actions":["click"]}'
    → 把这张证**再收紧到具体目标**。三项都可选：
        pid/pids           只认这些 pid
        title_contains     只认窗口标题含这些片段之一的
        action/actions     只认这些动作名
    → **不填 --targets 就完全不写 targets 键** —— 与改前的证逐字节一致。
    → 校验（谁按目标放行）在 gate.py；本文件只管「签发时把目标写进证里」。

── P1-A 新增（窗WB2-2 · 2026-10-18，分工书 §三 WorkBuddy-2 + 接线单 §二 7 + L 卡） ──
  ① 默认范围 ABCD 一次给齐（--request 空/缺省时）；默认 30 分钟 / 60 次；
  ② 候选窗口 = EnumWindows 枚举（当前前台等）+ 调用方 --windows 声明，
     合并去重后写入通行证 windows 列表（身份键 = exe+cls+created，hwnd 只作展示）；
  ③ 写入改「读现有 → merge → 写回」（gate_lock 同一把锁，禁整文件替换）：
     windows 只追加不去重丢、scopes 不被窄请求覆盖/抬高、acts 计数不重置、
     父过期/急停 ⇒ 子全灭（写入时把每个 windows.exp 夹到父 exp）；
     窗口追加模式（--window-ack，或 note 命中 rc=7 话术的过渡兜底）只动 windows。
  ★ 待接 authstate：Codex-3 的 authstate.py 落地后，merge_* 系列改调它
    （本文件按同 schema 实现并预留此注记，不建第二套账）。
  ★ 需要别人改的（越界项，已上报 DSH）：core.py 的 _ask_then_retry 传 --window-ack
    与 --windows；gate.py 读 pass.windows 做窗口放行终判（否则 windows 只记录不拦）。

── 第三轮 C（2026-10-18 用户拍板）：**弹窗外观回到改前**，只留参数与合并逻辑 ──
  弹窗**不再有任何新增可见控件**：A/B/C/D 勾选框、D 警示 Label、「其它申请项」标签、
  候选窗口 Listbox、「窗口追加模式」标签、「请勾选本次授权范围」这类引导文案**全部撤掉**。
  弹窗只显示**纯文本摘要**；两个按钮之外没有任何可操作控件（set 模式另有原有的密钥输入框
  与原有的「显示密钥」勾选，那是改前就有的，不动）：
    · 本次授权范围（由 --request + --no-d 归一化得出：默认 ABCD，--no-d 时不含 D）；
    · 有效期 / 动作数；
    · --window-ack（或调用方 --windows 声明）时，将追加/纳入的窗口清单（多行文本，不可勾选）。
  ⇒ 用户**不能在此选档位或选窗口**：档位由 --request/--no-d 定，窗口由 --windows/--window-ack 定。
  后端口径一字不动：三个参数、候选窗口枚举、_merge_candidates/_win_identity/_merge_windows/
  merge_pass/clamp_exp、scope 归一化全部保留；fail-closed（范围为空 ⇒ 拒绝签发）仍在。
"""
import argparse
import ctypes
import ctypes.wintypes as _wt
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time

# ★ D18：跨进程锁**只 import 共享模块，不复制**（T14 整单就是在消灭双份漂移，复制等于造新债）。
#   锁文件与 gate.py 同源：`<AUTHDIR>/gate.lock` —— 两侧共用同一份 auth_pass.json。
from gate_lock import LockTimeout, lock_path  # noqa: E402
from gate_lock import exclusive as _lock_exclusive  # noqa: E402
from gate_lock import save_json  # noqa: E402
# ★ P1-A：档位全名从 gate.py 取（唯一真相源），不在本文件复制第二份。
import gate  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
AUTHDIR = os.path.join(BASE, "_auth")
KEYFILE = os.path.join(AUTHDIR, "auth_key.json")
PASSFILE = os.path.join(AUTHDIR, "auth_pass.json")
LOCKFILE = lock_path(AUTHDIR)
LOGFILE = os.path.join(AUTHDIR, "auth.log")
LOCK_SECONDS = 60
MIN_LEN = 4
PBKDF_ITER = 200_000
TITLE = "DSH电脑控制授权"          # 截图/感知层见到这个标题必须拒绝读取（见 perceive.py 黑名单）

# ────────────────────── P1-A：默认值与口径常量 ──────────────────────
# ① 分工书/L 卡：默认 ABCD 一次给齐、30 分钟、60 次。core.py 总是显式传参，
#   这些默认值作用于缺省/手工 CLI；显式传参仍按传的来（旧行为不变）。
DEFAULT_MINUTES = 30
DEFAULT_ACTS = 60
SCOPE_LETTERS = ("A", "B", "C", "D")
SCOPE_FULL = {"A": gate.A, "B": gate.B, "C": gate.C, "D": gate.D}
# 第三轮 C：D 的**风险文案与勾选框一并撤掉**（弹窗回到改前，范围只作纯文本展示）；
# --no-d 的口径仍在 _initial_scope_states 里生效：该开关打开时 D 不计入本次范围。
WINDOW_CANDIDATE_MAX = 40          # 候选窗口列表上限（再多就让他先关几个窗）
WINDOW_CHILD_MAX_MIN = 30 * 60     # ③ 子寿命：min(自身 30min, 父 exp)（L 卡 ③）

# ③ 敏感窗口黑名单：与 act.py check_target 的硬拒层**同口径**
#   （出处：dsh-viewer-plugin\tools\act.py:617-688 —— BLOCKED_CLASSES / BLOCKED_CLASSES_HARD_EXTRA
#   / BLOCKED_PROCESSES_HARD。终端类（ConsoleWindowClass 等）**不在**本表：
#   act.py 的语义是「默认拒 + 可经人当场确认 30 分钟临时放行」，故终端保留在候选里、只打标记。）
BLOCKED_CLASSES_HARD = ("AgentCtlBrakePopup", "TkTopLevel")
BLOCKED_EXE_HARD_LC = ("python.exe", "agentctl.exe", "consent.exe", "systemsettings.exe")
TERMINAL_CLASSES = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS",
                    "UAC Input Indicator", "UAC_InputIndicatorOverlayWnd")
TERMINAL_EXE_LC = ("cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
                   "wt.exe", "windowsterminal.exe", "openconsole.exe")

# note 命中这些 ⇒ 过渡兜底判为「窗口追加模式」（core 的 rc=7 话术：
#   "要操作窗口属主进程是 …" / "要操作一个新窗口"；待 core 传 --window-ack 后本兜底可退场）
_WINDOW_ACK_HINT = re.compile(r"要操作.{0,8}窗口|不在白名单")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    os.makedirs(AUTHDIR, exist_ok=True)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(f"[{_now()}] {msg}\n")
    print(f"[{_now()}] {msg}")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    # ★ D18·R1：原子写（唯一临时名 + 有界重试替换）统一在共享模块 gate_lock.save_json；
    #   本函数只留老名字转发。改前它与 gate._save 各写一份、且已开始漂移（一份有 makedirs、一份没有）。
    save_json(path, obj)


def verifier(key, salt):
    """加盐 + 拉伸 —— 存这个，不存密钥。
    拉伸（PBKDF2 20 万次）是为了短密钥：离线字典攻击从"瞬间"变成"要花时间"。"""
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"),
                               bytes.fromhex(salt), PBKDF_ITER).hex()


# ────────────────────── T5：可选「目标三元组」targets ──────────────────────
# 签发侧只做三件事：解析 --targets、在弹窗上如实显示、**填了才写进证里**。
# 判定（谁按目标放行）在 gate.py —— 别在这里重复一套，免得好好的两套口径各自跑偏。
_TARGET_ALIAS = {
    "pids": "pids", "pid": "pids",
    "title_contains": "titles",
    "actions": "actions", "action": "actions",
}


def _norm_popup_targets(raw):
    """把 --targets 的 JSON 串规整成 gate 侧认的三元组。
    没给 → None。给得不对 → 抛 ValueError（**宁可不发证，也不发一张含糊的证**）。"""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        obj = json.loads(raw)
    except Exception as e:
        raise ValueError(f"--targets 不是合法 JSON：{e}")
    if not isinstance(obj, dict):
        raise ValueError(f"--targets 必须是 JSON 对象，本次是 {type(obj).__name__}")
    norm = {"pids": [], "titles": [], "actions": []}
    for k, v in obj.items():
        ks = str(k)
        if ks.startswith("_"):
            continue                       # 下划线开头 = 注释，不参与判定（与 gate.py 同规矩）
        g = _TARGET_ALIAS.get(ks)
        if not g:
            raise ValueError(f"--targets 里有闸门不认识的键「{ks}」"
                             "（只认 pid/pids、title_contains、action/actions）")
        vals = v if isinstance(v, (list, tuple)) else [v]
        norm[g].extend(str(x).strip() for x in vals if str(x).strip())
    if not (norm["pids"] or norm["titles"] or norm["actions"]):
        raise ValueError("--targets 里没有可判定的项（三项都空）"
                         "—— 空声明不等于不限制，拒绝签发")
    out = {}
    if norm["pids"]:
        pids = []
        for x in norm["pids"]:
            try:
                pids.append(int(float(x)))
            except Exception:
                raise ValueError(f"--targets 里的 pid「{x}」不是整数")
        out["pids"] = pids
    if norm["titles"]:
        out["title_contains"] = norm["titles"]
    if norm["actions"]:
        out["actions"] = norm["actions"]
    return out


def _sum_targets(tg):
    """一行话把三元组说清（弹窗上给用户当场核对）。"""
    parts = []
    if tg.get("pids"):
        parts.append("pid " + "/".join(str(x) for x in tg["pids"]))
    if tg.get("title_contains"):
        parts.append("窗口标题含 " + "/".join(f"「{x}」" for x in tg["title_contains"]) + " 之一")
    if tg.get("actions"):
        parts.append("动作名 " + "/".join(tg["actions"]) + " 之一")
    return " · ".join(parts)


def targets_note():
    """通行证带 targets 时补一句提醒：按目标放行是 gate.py 判的。
    不带 targets 时返回空串 ⇒ --status/--check 的输出与改前逐字节一致。"""
    p = _load(PASSFILE, {})
    if isinstance(p, dict) and "targets" in p:
        return ("\n   ⚠ 本证带目标三元组 targets：本命令只查范围/时限/次数，"
                "真正「按目标放行」由 gate.py 判"
                "（guard 侧只做 pid+动作名，窗口标题项不实现）")
    return ""


# ────────────────────── P1-A ①：范围归一化（默认 ABCD / --no-d 不含 D） ──────────────────────

def _letter_of(item):
    """把一个 scope 项解析成 A/B/C/D 字母；认不出返回 None。
    认 'A' / 'A级 只读感知' / 'a'（ strip 后首字母 + 后面跟 '级' 或结尾）。"""
    s = str(item or "").strip()
    if not s:
        return None
    up = s.upper()
    if up in SCOPE_LETTERS:
        return up
    if up[0] in SCOPE_LETTERS and (len(up) == 1 or up[1] in "级 "):
        return up[0]
    return None


def _split_scope(req):
    """--request 串 → 项列表（与改前一致：按逗号切）。"""
    return [s.strip() for s in str(req or "").split(",") if s.strip()]


def _initial_scope_states(items, no_d, existing_scopes=None):
    """(每字母的初始状态, 非标准项列表)。
    默认规则：--request 给的算；没给（空）⇒ ABCD 全给（分工书 ①）；
    --no-d ⇒ D 不计入（接线单 §二 7）。
    第三轮 C：返回值仍叫 states（＝本次范围内含哪些字母），只是弹窗不再用勾选框承载它 ——
    它直接由参数算出，展示与落盘用同一份。

    ★★ 单 B（2026-10-21 用户拍板：**方案 A「并集，一个概念」**）★★
      本次范围 = **现有证的范围 ∪ 本次要的**。
      病根：改前 `merge_pass` 是 `base["scopes"] = new["scopes"]`（**覆盖**，见本文件 :465-467）
            ⇒ 一张「只要 A」的小请求会把你**已经批过的 B、C 抹掉**。
      为什么并集不是「放宽」：并集里的每一项，要么你**早已批过**（existing_scopes），
            要么你**此刻正在批**（本次要的）⇒ 它是「**不撤销你已有的授权**」，
            不是「凭空多给一项」。
      ★ existing_scopes 只能由**有效**旧证提供（未消费、未过期）——
        过期的证什么都没给，拿它并集等于凭一张废证把权限恢复回来。
      ★ --no-d **优先**：既有 D 也不并进来（否则并集把 --no-d 这个产品开关破掉）。
      ★ 窗口追加模式（window_ack）**不并集**：那条路只动窗口，范围/时限/次数一律不碰。
    """
    std = [it for it in items if _letter_of(it)]
    have = set()
    for it in (existing_scopes or []):
        L = _letter_of(it)
        if L:
            have.add(L)
    if items and not std:
        # 全是非常规项：一个字母都不认识 ⇒ 不预设任何**请求来的**范围；
        # 但你**早已批过**的那些仍然算数（并集口径），没有旧证时就是全不勾。
        states = {L: (L in have) for L in SCOPE_LETTERS}
    elif not items:
        # 空请求 ⇒ ABCD 全给（默认口径不变；并集是 ABCD 的子集，不影响这一档）
        states = {L: True for L in SCOPE_LETTERS}
    else:
        want = {_letter_of(it) for it in std} | have
        states = {L: (L in want) for L in SCOPE_LETTERS}
    if no_d:
        states["D"] = False
    extra = [it for it in items if not _letter_of(it)]
    return states, extra


def _existing_valid_scopes():
    """现有证里**还有效**的范围（未消费、未过期）。读不到/坏文件 ⇒ 空表。

    ★ 单 B：并集的来源。**只有有效证才配进并集** ——
      过期的证什么都没给，拿它并集等于凭一张废证把权限恢复回来。
    ★ 窗口追加模式不用它（那条路只动窗口，范围一律不碰）。
    """
    try:
        with _lock_exclusive(AUTHDIR):
            p = _load(PASSFILE, {})
    except Exception:
        return []
    if not isinstance(p, dict) or not p:
        return []
    try:
        if p.get("consumed"):
            return []
        if time.time() > float(p.get("expires_at") or 0):
            return []
    except Exception:
        return []
    return list(p.get("scopes") or [])


# ────────────────────── P1-A ②：候选窗口枚举（只读，ctypes） ──────────────────────

def _proc_exe_and_created(pid):
    """(exe 名, 主窗创建时间 ISO)。读不到返回 '?' —— **不猜**。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k = ctypes.windll.kernel32
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return "?", "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        sz = _wt.DWORD(1024)
        if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
            return "?", "?"
        exe = os.path.basename(buf.value)
        created = _wt.FILETIME()
        exit_t = _wt.FILETIME()
        kernel_t = _wt.FILETIME()
        user_t = _wt.FILETIME()
        if not k.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exit_t),
                                 ctypes.byref(kernel_t), ctypes.byref(user_t)):
            return exe, "?"
        # FILETIME（100ns 自 1601）→ ISO
        v = (created.dwHighDateTime << 32) | created.dwLowDateTime
        try:
            iso = (datetime.datetime(1601, 1, 1)
                   + datetime.timedelta(microseconds=v / 10)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            iso = "?"
        return exe, iso
    except Exception:
        return "?", "?"
    finally:
        k.CloseHandle(h)


def _enum_candidate_windows(limit=WINDOW_CANDIDATE_MAX):
    """枚举可见顶层窗口 → 候选行（不含硬拒敏感窗）。
    每行 {hwnd,title,cls,pid,exe,created,foreground,terminal,declared}。
    纯只读（EnumWindows/GetWindowText/GetClassName/进程查询），不驱动桌面。"""
    user32 = ctypes.windll.user32
    rows = []

    def _cb(hwnd, _lparam):
        if len(rows) >= limit * 3:
            return False
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, 4):          # GW_OWNER：有属主的不是顶层
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            title = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, title, n + 1)
            cloaked = _wt.DWORD()
            try:                                    # UWP 隐藏窗（ cloaked ）跳过
                ctypes.windll.dwmapi.DwmGetWindowAttribute(
                    _wt.HWND(hwnd), 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
                if cloaked.value:
                    return True
            except Exception:
                pass
            cls = ctypes.create_unicode_buffer(257)
            user32.GetClassNameW(hwnd, cls, 257)
            cls = cls.value
            pid = _wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe, created = _proc_exe_and_created(pid.value)
            # ③ 敏感窗硬拒层（与 act.py 同口径）—— L 卡红线：不进候选
            if cls in BLOCKED_CLASSES_HARD or (exe or "?").lower() in BLOCKED_EXE_HARD_LC:
                return True
            rows.append({
                "hwnd": int(hwnd), "title": title.value, "cls": cls, "pid": int(pid.value),
                "exe": exe, "created": created,
                "foreground": int(hwnd) == user32.GetForegroundWindow(),
                "terminal": (cls in TERMINAL_CLASSES
                             or (exe or "?").lower() in TERMINAL_EXE_LC),
                "declared": False,
            })
        except Exception:
            return True                          # 单个窗口读失败不能拖垮整个枚举
        return True

    proto = ctypes.WINFUNCTYPE(_wt.BOOL, _wt.HWND, _wt.LPARAM)
    try:
        user32.EnumWindows(proto(_cb), 0)
    except Exception:
        pass
    rows = rows[:limit]
    for r in rows:
        r["declared"] = False
    return rows


def _declared_windows(raw):
    """--windows 的 JSON（调用方声明）→ 候选行补充。形态错 ⇒ ValueError（宁缺毋滥）。"""
    if raw is None or str(raw).strip() == "":
        return []
    try:
        obj = json.loads(raw)
    except Exception as e:
        raise ValueError(f"--windows 不是合法 JSON：{e}")
    if isinstance(obj, dict):
        obj = [obj]
    if not isinstance(obj, list):
        raise ValueError("--windows 必须是 JSON 数组（或单个对象）")
    out = []
    for it in obj:
        if not isinstance(it, dict):
            raise ValueError("--windows 数组里必须是对象")
        hwnd = it.get("hwnd")
        try:
            hwnd = int(hwnd) if hwnd is not None else 0
        except Exception:
            hwnd = 0
        out.append({"hwnd": hwnd, "title": str(it.get("title") or "（调用方声明·无标题）"),
                    "cls": str(it.get("cls") or it.get("class") or "?"),
                    "pid": 0,
                    "exe": str(it.get("exe") or it.get("process") or "?"),
                    "created": str(it.get("created") or "?"),
                    "foreground": False, "terminal": False, "declared": True})
    return out


def _merge_candidates(enumerated, declared):
    """枚举结果 + 调用方声明：按 hwnd 去重（同 hwnd 以枚举行为准，只补 declared 标记）。"""
    out = [dict(r) for r in enumerated]
    by_hwnd = {r["hwnd"]: r for r in out if r["hwnd"]}
    by_title = {r["title"]: r for r in out if r["title"]}
    for d in declared:
        hit = by_hwnd.get(d["hwnd"]) if d["hwnd"] else None
        if hit is None:
            hit = by_title.get(d["title"])
        if hit is not None:
            hit["declared"] = True
            continue
        out.append(d)
    out.sort(key=lambda r: (not r["foreground"], not r["declared"], r["hwnd"]))
    return out


# ────────────────────── P1-A ③：读-改-写合并（禁整文件替换） ──────────────────────

def _win_identity(w):
    """窗口身份键（L 卡 ④）：(exe, cls, created)。hwnd 不进身份，只作展示。"""
    return (str(w.get("exe")), str(w.get("cls")), str(w.get("created")))


def _merge_windows(existing, new, *, clamp_exp):
    """windows 只追加：按身份键去重合并，hwnds/titles 取并集；exp 一律夹到 clamp_exp
    （⇒ 父过期/缩短，子全灭；L 卡 ③）。"""
    out = []
    index = {}
    for w in list(existing or []) + list(new or []):
        if not isinstance(w, dict):
            continue
        k = _win_identity(w)
        if k in index:
            cur = index[k]
            cur["hwnds"] = sorted(set(cur.get("hwnds") or []) | set(w.get("hwnds") or []))
            cur["titles"] = sorted(set(cur.get("titles") or []) | set(w.get("titles") or []))
        else:
            cur = {"exe": k[0], "cls": k[1], "created": k[2],
                   "exp": int(w.get("exp") or clamp_exp),
                   "hwnds": sorted(set(w.get("hwnds") or [])),
                   "titles": sorted(set(w.get("titles") or []))}
            index[k] = cur
            out.append(cur)
    for w in out:
        w["exp"] = int(min(int(w.get("exp") or clamp_exp), clamp_exp))
    return out


def merge_pass(existing, new, *, window_ack):
    """**读-改-写**合并：返回合并后的通行证 dict；拒绝时返回 None（附原因到 new['_why']）。

    window_ack=True（窗口追加）：只动 windows；scope/scopes/expires/acts/acts_limit/nonce/
      consumed 一律不碰；没有有效父证（不存在/已消费/已过期）⇒ 拒绝（fail-closed，
      不借机签出一张窄证 —— 那正是 L 卡确诊的覆盖 bug）。
    window_ack=False（任务授权）：refreshes issued/expires/acts_limit/nonce、consumed=False；
      **scopes 一律取本次弹窗批准集**（勾选框是用户当场看得见的决定——用户取消勾选 D，
      证就不能再含 D；"窗口放行不覆盖任务证"由 window_ack 模式负责，不靠这里的 ⊆ 试探）；
      **acts 计数器原样保留（不重置）**；windows 只追加。
      targets：传了才写；没传则保留现有（不静默放宽）。
    """
    base = dict(existing) if isinstance(existing, dict) else {}
    now = time.time()
    old_exp = float(base.get("expires_at") or 0)
    old_valid = bool(base) and not base.get("consumed") and old_exp > now

    if window_ack:
        if not old_valid:
            new["_why"] = ("窗口追加模式：没有可追加的**有效任务通行证**"
                           "（不存在 / 已一次性消费 / 已过期）—— 不借机签发新证，拒绝")
            return None
        base["windows"] = _merge_windows(base.get("windows"), new.get("windows"),
                                         clamp_exp=int(base["expires_at"]))
        base["windows_updated"] = _now()
        return base

    new_scopes = list(new["scopes"])
    base["scopes"] = new_scopes
    base["scope"] = "、".join(new_scopes)
    base["acts_limit"] = int(new["acts_limit"])
    # ★★ D4 修复（2026-10-19，Codex 实跑时踩出）：**新签发 ⇒ acts 从 0 起算**。
    #   改前是「只保留，不重置、不新建」，实跑后果是**新证一出生就作废**：
    #     用户批「200 次」，回执却是「动作次数已用尽（104/200）」——
    #     而且只有把上限签到**比历史累计还大**才放行（实测 20 不行 → 60 不行 → 200 才过）。
    #     也就是说：用户每批一次，弹窗上写的那个次数**说了不算**。
    #
    #   为什么不改坏「防续额度」：
    #     原来那行的意图是防**模型自己给自己续额度**（铁律 11④：明文密钥永不接触模型，
    #     模型够不到弹窗）。但**这一行只有人在弹窗里亲手输了密钥才走得到** ——
    #     模型够不着，防的是一个够不着的威胁；代价是**人的决定被静默丢弃**。
    #     而 nonce/expiry 在同一段里**本来就换新**了 ⇒ 这里本来就是「这是一张新证」的位置。
    #   ⇒ 计数器跟着新证一起归零，语义回到弹窗上写的那句话：
    #     「**本次授权**的动作次数上限」。
    #
    #   ★ 审计不丢：旧计数原样留在 `acts_carried`。
    #     ⇒「这一轮之前已经花掉多少」查得到，只是不再**拿它去卡新证**。
    #   ★ window_ack（窗口追加）模式在更上面就 return 了，走不到这里 ——
    #     追加模式仍然**一分额度都不给**，口径没被这次修复碰到。
    _old_acts = int(base.get("acts") or 0)
    if _old_acts:
        base["acts_carried"] = _old_acts
    base["acts"] = 0
    base["issued"] = new["issued"]
    base["expires_at"] = new["expires_at"]
    base["expires"] = new["expires"]
    base["nonce"] = new["nonce"]
    base["consumed"] = False
    base.pop("consumed_at", None)
    if new.get("targets") is not None:
        base["targets"] = new["targets"]    # 没传 targets ⇒ 保留现有（不静默放宽）
    base["windows"] = _merge_windows(base.get("windows"), new.get("windows"),
                                     clamp_exp=int(base["expires_at"]))
    return base


def _windows_from_selection(rows, sel_idx, exp_at):
    """候选行 → windows 条目（身份键 exe+cls+created；hwnds/titles 展示用）。
    第三轮 C：弹窗不再有勾选框，调用方传**全集下标**（覆盖哪些窗口全由参数决定）；
    本函数本身没动 —— 判定口径仍在这里，只换了「谁来定 sel_idx」。"""
    out = []
    for i in sel_idx:
        if not (0 <= i < len(rows)):
            continue
        r = rows[i]
        out.append({"exe": r["exe"], "cls": r["cls"], "created": r["created"],
                    "exp": int(exp_at), "hwnds": [r["hwnd"]] if r["hwnd"] else [],
                    "titles": [r["title"]] if r["title"] else []})
    return out


def _sum_window_lines(rows):
    """候选窗口行 → 弹窗上的**多行纯文本**清单（不可勾选；纯只读展示）。

    第三轮 C：原来的 Listbox 撤掉了，但「要授权哪些窗口」必须让用户**看得见、能核对** ——
    这里就是那一眼。每行形如：`〔前台〕[123456] 记事本 — notepad.exe／Notepad`。
    与 `_windows_from_selection` 同源同序，**显示的就是将要写进证里的那一份**。"""
    lines = []
    for r in rows or []:
        marks = []
        if r.get("foreground"):
            marks.append("前台")
        if r.get("declared"):
            marks.append("调用方声明")
        if r.get("terminal"):
            marks.append("终端")
        mark = ("〔" + "·".join(marks) + "〕") if marks else ""
        lines.append("  · %s[%s] %s — %s／%s"
                     % (mark, r.get("hwnd"), r.get("title"), r.get("exe"), r.get("cls")))
    return "\n".join(lines)


# ────────────────────────── 弹窗 ──────────────────────────
def issue_pass(scope_states, scope_extra, minutes, acts, targets=None,
               win_rows=None, window_ack=False):
    r"""**签发通行证**（算范围 → 合并 → 落盘）。返回 (merged, why)。

    ★ 为什么抽出来（单 B，2026-10-21）：改前这段逻辑**长在 tkinter 的闭包 `submit` 里**，
      于是「批准之后证里到底装什么」**没法在用户不在场时验证** ——
      弹窗要真桌面、还要真密钥（PBKDF2）才能走到这一步。
      抽成模块级函数后，判据可以直接打这条**真签发路径**（tests\_P1_通行证并集_判据.py），
      单 B 的行为才**可无人值守地验证**。
    ★ **密钥校验不在这里**：它留在 `submit` 里（本函数是「用户已按了授权」之后的那一段）。
      判据调本函数等于跳过密钥那一步 —— 判的是**范围与合并语义**，不是密钥闸。
    ★ fail-closed 不变：一个范围都拿不到 ⇒ **不签发**（返回 (None, 原因)）。
    """
    checked = [L for L in SCOPE_LETTERS if scope_states and scope_states.get(L)]
    # ★ 空白的范围项**不算一个范围**（不许签出 scopes=[""] 这种废证）。
    #   正常流程里 `_split_scope` 已经滤掉了空串；这里再挡一道 ⇒ 纵深防御。
    extra = [s for s in (scope_extra or []) if str(s or "").strip()]
    if not checked and not extra:
        return None, "本次没有可授权的范围（参数为空）—— 拒绝签发"
    granted = [SCOPE_FULL[L] for L in checked] + list(extra)
    expires = time.time() + minutes * 60
    rows = win_rows or []
    windows = _windows_from_selection(rows, range(len(rows)), expires)
    new = {"scopes": granted, "acts_limit": acts, "issued": _now(),
           "expires_at": expires,
           "expires": datetime.datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S"),
           "nonce": secrets.token_hex(8), "windows": windows}
    if targets is not None:
        # ★ 只有用户真填了目标才写这个键。绝不能写成 targets=None ——
        #   gate.py 用 `"targets" in p` 判存在，写了键就等于「声明了目标」，
        #   会把一张不设限的老证变成受限证。
        new["targets"] = targets
    # ── P1-A ③：读-改-写在**同一把锁**里完成（禁整文件替换）──
    try:
        with _lock_exclusive(AUTHDIR):
            existing = _load(PASSFILE, {})
            merged = merge_pass(existing, new, window_ack=window_ack)
            if merged is None:
                return None, (new.get("_why") or "拒绝签发")
            _save(PASSFILE, merged)
    except LockTimeout as e:
        return None, "%s，本次拒绝（不越权）" % e
    return merged, ""


def _carried_letters(existing_scopes, items):
    """并集里**从旧证带进来**的字母（= 本次没要、但旧证里有、于是被并进来的那些）。

    ★ 为什么单列出来（单 B，2026-10-21）：
      调用方算 `acts`（动作次数上限）时，看的是**它申请的那几个范围**；
      并集带进来的范围**不在那个计算里** ⇒ 若不夹紧，
      一张含 D（上限 10）的并集证可能带着 20 的额度 ⇒ 比 D 自己的上限宽松。
      ⇒ 带进来的范围必须按**它自己的上限**夹一次（见 main()）。
      ★ A 不参与：A 级零副作用、不计数（与新线 gate.effective_act_limit 同一口径）。
    """
    want = set()
    for it in (items or []):
        L = _letter_of(it)
        if L:
            want.add(L)
    carried = set()
    for it in (existing_scopes or []):
        L = _letter_of(it)
        if L and L not in want:
            carried.add(L)
    return carried


#: 额度表**读不出来**时的兜底上限（保守）：取各档里最紧的那一档（D=10）的口径。
#: ★ 只在「表认不出」时用；正常路径一律走 gate.ACT_LIMIT（单一真相源）。
_FALLBACK_ACT_LIMIT = 10


def _clamp_acts(acts, carried):
    """把动作次数上限按「带进来的范围」夹紧。返回夹紧后的数字。

    ★ 口径与新线 `gate.effective_act_limit` 一致：**已授予档位里最紧的一档**，
      且 A 不参与取最小（A 本就不计数）。
    ★ acts ≤ 0 = **不封顶**（用户 2026-10-19 定：取消动作次数上限，急停兜底）
      ⇒ 不夹（0 保持 0）。
    ★ 没有带进来任何范围 ⇒ 原样返回（默认行为不变）。
    ★ ★ 额度表的键**是全名**（旧线 gate.ACT_LIMIT = {"A级 只读感知":200,…}），
      不是字母 ⇒ 必须经 SCOPE_FULL 翻译。
      ★★ 认不出（表口径变了/键对不上）⇒ **不许悄悄不夹**：按固定兜底上限夹。
        「静默不夹」= 并集顺带放宽额度 = 事故。
    """
    try:
        acts = int(acts)
    except Exception:
        return acts
    if acts <= 0:
        return acts
    limits = []
    unknown = []
    for L in (carried or ()):
        if L == "A":
            continue                  # A 零副作用、不计数（与新线 effective_act_limit 同口径）
        full = SCOPE_FULL.get(L)
        v = gate.ACT_LIMIT.get(full) if full else None
        if v is None:
            unknown.append(L)
        else:
            try:
                limits.append(int(v))
            except Exception:
                unknown.append(L)
    if unknown:
        # 额度表认不出 ⇒ 保守：按**固定兜底上限**夹（各档里最紧的那一档，D=10 的口径）。
        # ★ 不用「当前表里的最小值」当兜底 —— 表都认不出了，它里面的数未必还是上限。
        limits.append(_FALLBACK_ACT_LIMIT)
    if not limits:
        return acts
    return min(acts, min(limits))


def _popup(kind, scope="", minutes=10, acts=20, note="", targets=None,
           scope_states=None, scope_extra=None, no_d=False,
           candidates=None, window_ack=False):
    """kind: 'set' 设置密钥 / 'request' 申请授权。返回 (结果, 密钥文本或 None)。

    第三轮 C（2026-10-18 用户拍板）：**弹窗外观回到改前** —— 本函数不再放任何新增可见控件，
    只把**参数决定的结果**作纯文本只读展示：
      · 本次授权范围（由 scope_states / scope_extra 定；默认 ABCD，--no-d 时不含 D）；
      · 有效期 / 动作数；
      · --window-ack（或调用方 --windows 声明）时，将追加/纳入的窗口清单（多行文本）。
    用户不能在此选档位或选窗口 —— 只有原有的「允许 / 拒绝」两个按钮与原有的密钥输入框。
    参数与合并逻辑一字不动（no_d 仍按 P1-A 口径生效，取值在 _initial_scope_states）。
    """
    import tkinter as tk
    state = {"result": None, "key": None}
    root = tk.Tk()
    root.title(TITLE)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root.configure(bg="#1e1e1e")
    W = 560

    def label(txt, size=11, fg="#dddddd", pady=4):
        tk.Label(root, text=txt, font=("Microsoft YaHei UI", size), fg=fg,
                 bg="#1e1e1e", wraplength=W - 40, justify="left").pack(pady=pady, padx=20, anchor="w")

    if kind == "set":
        label("设置电脑控制授权密钥", 13, "#ffffff", 12)
        label("密钥只在本机做加盐拉伸校验（PBKDF2 20 万次），不保存明文、不进日志。\n"
              "只输一次——请点「显示」核对没打错。\n"
              "唯一硬要求：别用你的名字、别用本机文件或对话里出现过的词。", 10, "#aaaaaa")
    else:
        label("电脑控制授权申请", 13, "#ffffff", 12)
        if note:
            # 2026-10-07 加：窗口审批要用。note 只**显示**，绝不混进 scope ——
            # scope 会被切分后存成通行证范围，塞别的东西进去会让范围对不上。
            label(note, 11, "#7ee787", 8)
        # ── 第三轮 C ①：范围 → **纯文本摘要**（没有勾选框；这里与 submit 同一口径）──
        #   范围由参数决定：--request 里的 A/B/C/D + --no-d（不含 D）+ 非标准项原样保留。
        granted_preview = [SCOPE_FULL[L] for L in SCOPE_LETTERS
                           if scope_states and scope_states.get(L)]
        granted_preview += list(scope_extra or [])
        label("本次授权范围：" + ("、".join(granted_preview) or "（无）")
              + "（由参数决定，本窗不可改）", 11, "#ffd479")
        label(f"有效期：{minutes} 分钟（过期或撤销即失效）\n"
              f"动作次数上限：{acts} 次（用完即止）", 11, "#ffd479")
        if targets:
            label("本次限定目标（targets）：\n" + _sum_targets(targets)
                  + "\n目标对不上、或拿不到目标信息 → 一律拒绝", 10, "#8fd3f4")
        # ── 第三轮 C ②：候选窗口 → **多行纯文本清单**（没 Listbox、不可勾选）──
        #   窗口集合同样由参数决定：EnumWindows 枚举 ∪ 调用方 --windows 声明（合并去重）。
        #   写进证里的就是这里显示的这一份（都是 cands 全集），用户无从挑选 ——
        #   所以只在真正要动窗口的两种情形下才占版面（默认路径外观＝改前）。
        cands = candidates or []
        declared_rows = [r for r in cands if r.get("declared")]
        win_rows = cands if (window_ack or declared_rows) else []
        if window_ack or declared_rows:
            label(("本次将追加的窗口清单" if window_ack else "本次将纳入的窗口清单")
                  + "（%d 个；身份 = exe + 类名 + 创建时间，本窗不可勾选）：\n" % len(win_rows)
                  + (_sum_window_lines(win_rows) or "  （无候选窗口）"), 10, "#dddddd")

    lock = _load(KEYFILE, {})
    locked_until = lock.get("locked_until", 0)
    remain = int(locked_until - time.time())
    if remain > 0:
        label(f"⚠ 已锁定，请等 {remain} 秒后再试（连续输错触发）", 11, "#ff7b72", 10)

    tk.Label(root, text="密钥：", font=("Microsoft YaHei UI", 11), fg="#dddddd",
             bg="#1e1e1e").pack(pady=(10, 0), padx=20, anchor="w")
    e1 = tk.Entry(root, show="●", width=42, font=("Consolas", 11))
    e1.pack(pady=4, padx=20)

    show_var = tk.BooleanVar(value=False)

    def toggle_show():
        e1.config(show="" if show_var.get() else "●")

    if kind == "set":
        tk.Checkbutton(root, text="显示密钥（确认没打错）", variable=show_var, command=toggle_show,
                       bg="#1e1e1e", fg="#aaaaaa", selectcolor="#1e1e1e",
                       activebackground="#1e1e1e", activeforeground="#ffffff",
                       font=("Microsoft YaHei UI", 10)).pack(pady=(0, 4), padx=20, anchor="w")

    tip = tk.Label(root, text="", font=("Microsoft YaHei UI", 10), fg="#ff7b72", bg="#1e1e1e")
    tip.pack(pady=4)

    def submit(_=None):
        key = e1.get()
        if kind == "set":
            if len(key) < MIN_LEN:
                tip.config(text=f"太短了，至少 {MIN_LEN} 位")
                return
            salt = secrets.token_hex(16)
            _save(KEYFILE, {"salt": salt, "verifier": verifier(key, salt),
                            "algo": f"pbkdf2-sha256-{PBKDF_ITER}", "created": _now(),
                            "locked_until": 0, "fail_count": 0,
                            "weak": len(key) < 8})
            state["result"] = "set"
            log("密钥已设置（只存校验值，未存明文）")
            e1.delete(0, "end")
            root.destroy()
            return
        # request
        rec = _load(KEYFILE, {})
        if not rec.get("verifier"):
            tip.config(text="还没有密钥，请先运行 --set")
            return
        if time.time() < rec.get("locked_until", 0):
            tip.config(text="锁定中，稍后再试")
            return
        if hmac.compare_digest(verifier(key, rec["salt"]), rec["verifier"]):
            # ── 第三轮 C：范围/窗口**全从参数取**（弹窗上已无可点的档位/窗口控件）；
            #    fail-closed 不变：一个范围都拿不到 ⇒ 拒绝签发 ──
            # ★ 单 B：算范围 → 合并 → 落盘抽到模块级 `issue_pass`（可被判据无人值守地打）
            #   —— 密钥校验仍在本函数里（这一行之前），密钥没过就根本走不到这里。
            merged, why = issue_pass(scope_states, scope_extra, minutes, acts,
                                     targets, win_rows, window_ack)
            if merged is None:
                tip.config(text=why or "拒绝签发")
                return
            rec["fail_count"] = 0
            _save(KEYFILE, rec)
            scope_txt = merged.get("scope", "")
            state["result"] = "granted"
            log(f"✅ 授权通过：范围「{scope_txt}」 {minutes} 分钟，动作上限 {acts} 次"
                + (f"，限定目标〔{_sum_targets(targets)}〕" if targets is not None else "")
                + (f"，覆盖窗口 {len(merged.get('windows') or [])} 个（读-改-写合并）"
                   if merged.get("windows") else "（未限定窗口）")
                + ("〔窗口追加模式〕" if window_ack else ""))
            e1.delete(0, "end")
            root.destroy()
        else:
            rec["fail_count"] = rec.get("fail_count", 0) + 1
            rec["locked_until"] = time.time() + LOCK_SECONDS
            _save(KEYFILE, rec)
            log(f"❌ 密钥错误（第 {rec['fail_count']} 次），锁定 {LOCK_SECONDS} 秒")
            e1.delete(0, "end")
            tip.config(text=f"密钥不对，已锁定 {LOCK_SECONDS} 秒")
            root.destroy()

    def deny():
        state["result"] = "denied"
        log(f"⛔ 用户拒绝授权：范围「{scope}」")
        root.destroy()

    bar = tk.Frame(root, bg="#1e1e1e")
    bar.pack(pady=12)
    tk.Button(bar, text="授 权", width=12, command=submit,
              bg="#2d7d46", fg="white", font=("Microsoft YaHei UI", 11)).pack(side="left", padx=8)
    tk.Button(bar, text="拒 绝", width=12, command=deny,
              bg="#6b2b2b", fg="white", font=("Microsoft YaHei UI", 11)).pack(side="left", padx=8)
    root.bind("<Return>", submit)
    root.bind("<Escape>", lambda e: deny())
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{W}x{root.winfo_reqheight()}+{(sw - W) // 2}+{(sh - root.winfo_reqheight()) // 3}")
    e1.focus_force()
    root.mainloop()
    return state["result"]


# ────────────────────────── 命令 ──────────────────────────
def check_pass(scope=None, consume=False):
    """★ D18·R3：整段「读 → 判定 → 写回」进**跨进程锁**（与 gate.require 同一把锁）。

    改前这里不取锁，而 gate.require 已取锁 ⇒ 后者能把前者的 `consumed` 覆盖回去：
    实测（把窗口拉到确定顺序）stale 写回后 `consumed=False`、`consumed_at` 消失
    ⇒ **已消费的证复活**。等锁超时按本模块契约返回 `(False, 原因)`，不抛异常。
    """
    try:
        with _lock_exclusive(AUTHDIR):
            p = _load(PASSFILE, {})
            if not p:
                return False, "没有通行证"
            if p.get("consumed"):
                return False, "通行证已被使用"
            if time.time() > p.get("expires_at", 0):
                return False, f"通行证已过期（{p.get('expires')}）"
            if scope and p.get("scope") != scope:
                return False, f"通行证范围不符：持「{p.get('scope')}」，要用「{scope}」"
            if consume:
                p["consumed"] = True
                p["consumed_at"] = _now()
                _save(PASSFILE, p)
            return True, f"范围「{p.get('scope')}」有效至 {p.get('expires')}"
    except LockTimeout as e:
        return False, f"{e}，本次拒绝（不越权）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="store_true")
    ap.add_argument("--request")
    ap.add_argument("--minutes", type=int, default=DEFAULT_MINUTES)
    ap.add_argument("--acts", type=int, default=DEFAULT_ACTS, help="本张通行证的动作用量上限")
    ap.add_argument("--note", default="", help="只显示在弹窗上的一句话（不进通行证范围）")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check")
    ap.add_argument("--consume", action="store_true")
    ap.add_argument("--targets", help="T5 可选：把这张证再收紧到具体目标，JSON 形如 "
                                      '{"pids":[1234],"title_contains":["记事本"],"actions":["click"]}'
                                      "（只在 --request 时生效；不填则不写 targets 键）")
    # ── P1-A 新增三个开关 ──
    ap.add_argument("--no-d", action="store_true",
                    help="产品开关未启用 D：本次授权范围默认不含 D（接线单 §二 7；"
                         "与默认 ABCD 全给是同一口径的两面）")
    ap.add_argument("--window-ack", action="store_true",
                    help="窗口追加模式：只把本次候选窗口（--windows 声明 ∪ 枚举）追加进现有通行证，"
                         "不动范围/时限/次数（L 卡 ②③；没有有效父证则拒绝签发）。"
                         "core 的 rc=7 路径将来传它")
    ap.add_argument("--windows",
                    help="调用方声明的候选窗口 JSON（P1-A ②）："
                         '[{"hwnd":123,"title":"记事本","exe":"notepad.exe","cls":"Notepad"}]'
                         "；与 EnumWindows 枚举结果合并去重")
    a = ap.parse_args()

    if a.targets and a.request is None:
        print("⚠ --targets 只在 --request（签发新证）时才有意义，本次忽略")
    if a.windows and a.request is None:
        print("⚠ --windows 只在 --request 时才有意义，本次忽略")

    if a.set:
        return 0 if _popup("set") == "set" else 5
    if a.status:
        ok, why = check_pass()
        print((("✅ " + why) if ok else ("— " + why)) + targets_note())
        return 0 if ok else 3
    if a.check is not None:
        ok, why = check_pass(a.check or None, a.consume)
        print((("✅ " + why) if ok else ("⛔ " + why)) + targets_note())
        return 0 if ok else 3
    if a.request is not None:
        try:
            tg = _norm_popup_targets(a.targets)
            declared = _declared_windows(a.windows)
        except ValueError as e:
            print(f"⛔ 不签发：{e}")
            return 4
        req = a.request or ""
        items = _split_scope(req)
        cands = _merge_candidates(_enum_candidate_windows(), declared)
        # ③ 窗口追加模式：显式开关，或 note 命中 rc=7 话术（过渡兜底，core 接开关后可退场）
        #    ★ 顺序要紧：**先判 window_ack** —— 那条路只动窗口、范围一律不碰 ⇒ 不并集。
        window_ack = bool(a.window_ack) or bool(_WINDOW_ACK_HINT.search(a.note or ""))
        # ★ 单 B（方案 A「并集」）：本次范围 = 现有证的有效范围 ∪ 本次要的。
        #   现有范围只从**有效**旧证取（未消费、未过期）——过期的证什么都没给。
        existing_scopes = [] if window_ack else _existing_valid_scopes()
        # ① 缺省/空 request ⇒ ABCD 一次给齐（分工书 ①）
        states, extra = _initial_scope_states(items, a.no_d, existing_scopes)
        # ★ 单 B：并集**带进来**的范围要按它自己的上限夹一次动作次数
        #   （调用方算 acts 时看的是它申请的范围，不含这些带进来的）。
        acts = _clamp_acts(a.acts, _carried_letters(existing_scopes, items))
        r = _popup("request", req, a.minutes, acts, a.note, targets=tg,
                   scope_states=states, scope_extra=extra, no_d=a.no_d,
                   candidates=cands, window_ack=window_ack)
        if r == "granted":
            ok, why = check_pass()
            print("通行证已签发：" + why)
            if tg:
                print("   └ 已写入限定目标：" + _sum_targets(tg))
            p = _load(PASSFILE, {})
            ws = p.get("windows") or []
            if ws:
                print("   └ 覆盖窗口 %d 个（身份=exe+cls+created；追加合并）：" % len(ws))
                for w in ws:
                    print("      · %s / %s / %s" % (w.get("exe"), w.get("cls"), w.get("created")))
            if window_ack:
                print("   └ 本次为窗口追加模式：范围/时限/次数未动")
            return 0
        print("未授权：" + (r or "窗口被关闭"))
        return 3
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
