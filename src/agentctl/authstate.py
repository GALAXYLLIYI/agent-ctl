# -*- coding: utf-8 -*-
r"""authstate.py —— 授权状态父子模型（P1-B · 窗口D-3）

解决的问题（dispatch_L 开单原话）：
  今天一次 QQ 任务让用户点了 6 次授权（4 次任务授权 + 2 次窗口放行），
  而**窗口放行把 A/B/C 覆盖成 C-only**，之后点击报 code=9 本次持 C，本动作要 B
  —— 这是**已确诊 bug**。本模块把它从制度上堵死。

模型（分工书 §三 窗口D-3 冻结）：
    task{scopes:[A,B,C,D], exp, acts_used, acts_max, windows:[{exe, cls, created, exp}]}

四条铁律（每条都对应自证脚本里的一条断言）：

  ① **窗口放行只追加**：windows 并上 {(exe, cls, created)}；scopes **永不抬高**；
     acts_used **不重置**（任务总账）。
     —— 身份键**不用 pid**（Electron 多进程 + pid 复用双重坑），用
        (进程 exe 名, 窗口类名, 主窗创建时间)；hwnd 只用于画边框展示，不进身份。

  ② **父子寿命**：子 exp = min(自身 30min, 父 exp)（clip，不是并存）；
     父过期 / **急停拉下** ⇒ **读时立刻判子全灭**。

  ③ **同一把文件锁 + 读-改-写**，禁整文件替换。
     前科：通行证**单例覆盖竞态**（A/B/C 被 B 证踩掉）—— 两个入口写同一份状态，
     各锁各的等于没锁。这里锁**不是自己造的**，是 import 共享模块 gate_lock
     （唯一定义处，见 legacy_tools\gate_lock.py 头注的 D18 纪律），
     锁文件与 gate.py / auth_popup.py **同一个** <AUTHDIR>/gate.lock。

  ④ API：grant_task / allow_window / check / revoke_all
     （另附 consume —— acts_used 没有消耗路径就是死字段，「不重置」无从证伪）。

★ **为什么不写进 auth_pass.json**：老线 gate.py / auth_popup.py 读的那份
  schema 是 scopes:[<中文全称>, …]（**全称**）、单任务、无 windows 字段。
  本模块的 scopes:["A","B","C","D"]（**代号**）+ 父子结构两者不兼容，
  硬写进去会让老线 p.get("scopes") 拿到不认的串 ⇒ 静默判错档位。
  故 **数据文件隔离**（<AUTHDIR>/authstate.json），**锁不隔离**（仍是同一把 gate.lock）——
  「锁同源、数据隔离」是当前唯一不破坏在跑系统的姿势。
  将来并入 auth_pass.json 由 本地 agent 在集成顺序 ④ 统合决策，不在本单范围。

★ **急停怎么判**：默认惰性调 agentctl.gate.brake_engaged()（读 BRAKE_FLAG，
  可用环境变量 AGENTCTL_BRAKE_FLAG 覆盖）。**判不出（import 失败）⇒ 一律拒绝**
  （fail-closed）：状态不确定时宁可这次不放行，也不越权。

边界（分工书 §一 唯一硬规矩）：本模块**只读不写** core.py / gate.py / auth_popup.py，
对它们只 import，不复制其锁逻辑、不复制其判定逻辑。
"""
from __future__ import annotations

import importlib.util
import json
import os
import secrets
import sys
import time

SCHEMA = 1
STATE_NAME = "authstate.json"

SCOPE_ORDER = ("A", "B", "C", "D")
# dispatch_L ①：默认 ABCD 一次给齐（用户只想点一次）
DEFAULT_SCOPES = ("A", "B", "C", "D")
DEFAULT_TTL = 30 * 60          # 子寿命上限 30 分钟
DEFAULT_ACTS_MAX = 60          # 动作上限 60 次
LOCK_TIMEOUT = 10.0

# 档位只认**字母代号**（A/B/C/D）。
# ★ 这里**刻意不写任何中文动作字样**，两条理由：
#   ① D 级绊线判据（tests\_2h_wB_D级绊线判据.py）用「中文动作词 ⇒ 存在 D 级动作实现」
#     的启发式扫 src\agentctl —— 带引号的中文动作词会被判成「可转给老脚本的动作串」
#     （A2.2），且算「新出现的 D 级动作面」（A2.3）。
#     （本段注释自己也不许出现那个词：绊线扫的是**全文**，注释一样中招 ——
#       2026-10-18 第一次只删表里的字样，注释里留了一句解释，A2.3 当场又红了。）
#     wB 是**有意不解锁 D 级**的机械绊线；给它加白名单等于把那道绊线拆了，
#     所以正解是**改这里**（2026-10-18 窗口D-3 接到 wB 红后照此办）。
#   ② 更根本：档位**代号**是契约（分工书冻结 scopes:[A,B,C,D]），中文名只是各处的
#     **展示**。展示归展示、契约归契约；契约表里混进展示字样，两边迟早各漂各的。
# 老线 auth_pass.json 存的是中文全称，那是老线自己的口径；本模块**不读那份文件**
# （数据隔离见头注），跨口径请显式传字母。
_SCOPE_ALIASES = {
    "a": "A", "b": "B", "c": "C", "d": "D",
}


class AuthStateUnavailable(Exception):
    """拿不到锁 / 拿不到闸门目录。调用方应按自己的契约翻译成拒绝。"""


class AuthStateDenied(Exception):
    """授权状态层面的拒绝（额度用尽等）。与闸门拒绝分开，便于归因。"""


# ────────────────────── 共享模块加载（不复制锁逻辑）──────────────────────
# ★ 为什么 import 真源而不是自己写一把锁：
#   gate_lock.py 是跨进程互斥的**唯一定义处**（D18 从老线 gate.py 抽出来），
#   头注写着「gate.py 与 auth_popup.py 一律 import 本模块，不许复制一份锁逻辑过去
#   —— T14 整单就是在消灭双份漂移，复制等于造新债」。
#   本单是那个纪律的**新受益者**，不是例外。
_GATE_LOCK = None
_GATE_LOCK_TRIED = False


def _gate_lock():
    """加载真源 gate_lock（唯一定义处）。拿不到就抛，绝不静默降级成不安全实现。"""
    global _GATE_LOCK, _GATE_LOCK_TRIED
    if _GATE_LOCK is not None:
        return _GATE_LOCK
    if _GATE_LOCK_TRIED:
        raise AuthStateUnavailable(
            "找不到共享锁模块 gate_lock.py —— 已拒绝执行（不复制锁逻辑）")
    _GATE_LOCK_TRIED = True
    try:
        from agentctl import gate as _gate          # 惰性：只在真的要写时解析 HOME
        home = getattr(_gate, "HOME", None)
    except Exception as e:                          # gate import 本身可能体检失败
        raise AuthStateUnavailable(
            "agentctl.gate 不可用（%s）—— 闸门状态无处安放，拒绝执行" % e)
    if not home:
        raise AuthStateUnavailable(
            "找不到合格的 legacy home（gate_lock.py 随它走）—— 闸门状态无处安放，拒绝执行")
    path = os.path.join(home, "gate_lock.py")
    if not os.path.isfile(path):
        raise AuthStateUnavailable("legacy home 下没有 gate_lock.py：%s —— 拒绝执行" % path)
    spec = importlib.util.spec_from_file_location("agentctl._gate_lock_shared", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _GATE_LOCK = mod
    return mod


def _exclusive(authdir, timeout=LOCK_TIMEOUT):
    """跨进程排他锁 —— 委托共享模块，锁文件仍是 <authdir>/gate.lock。"""
    gl = _gate_lock()
    try:
        return gl.exclusive(authdir, timeout=timeout)
    except gl.LockTimeout as e:
        raise AuthStateUnavailable("闸门锁等待超时 —— 有别的进程卡住了（%s）" % e)


# ────────────────────── 路径与 scope 归一 ──────────────────────

def default_state_path():
    """生产默认：<AUTHDIR>/authstate.json（AUTHDIR 与 gate.py 同源解析）。"""
    from agentctl import gate as _gate
    authdir = getattr(_gate, "AUTHDIR", None)
    if not authdir:
        raise AuthStateUnavailable("闸门状态目录未定 —— 拒绝执行")
    return os.path.join(authdir, STATE_NAME)


def _resolve(path):
    return path or default_state_path()


def norm_scope(s):
    """档位归一：'A'/'a' ⇒ 'A'，'b' ⇒ 'B'……只认字母代号，认不出即抛（fail-closed）。"""
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    if t in SCOPE_ORDER:
        return t
    hit = _SCOPE_ALIASES.get(t) or _SCOPE_ALIASES.get(t.lower())
    if hit:
        return hit
    raise ValueError("认不出的档位：%r（本模块只认 A/B/C/D 代号）" % s)


def norm_scopes(scopes):
    if scopes is None:
        return list(DEFAULT_SCOPES)
    if isinstance(scopes, str):
        scopes = [scopes]
    out = []
    for s in scopes:
        n = norm_scope(s)
        if n and n not in out:
            out.append(n)
    # 按 A→D 排序，存进文件的口径才稳定（diff 干净、比对容易）
    return [x for x in SCOPE_ORDER if x in out]


def _norm_created(v):
    """主窗创建时间归一成 float。

    ★ 为什么必须归一（自证第 5 条实测踩出来的）：来源方给什么类型都有 ——
      PowerShell / COM 给 float，UIA 给 long，命令行 argv 给的是**字符串**。
      同一扇窗的 111111 与 111111 在 (exe, cls, created) 键上不相等
      ⇒ 会被当成两扇窗 ⇒ 放行记录增殖。
      身份键要能比较，先得能归一（dispatch_L ④ 不要用 pid，因为 pid 也会复用；
      created 不归一同样会把同一扇窗拆成两条）。
    """
    if v is None or isinstance(v, float):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _key(w):
    """窗口身份键：(exe, cls, created)。**刻意不含 pid**（dispatch_L ④）。"""
    return (str(w.get("exe", "")), str(w.get("cls", "")),
            _norm_created(w.get("created")))


# ────────────────────── 敏感窗终判（读侧）──────────────────────
# ★ 为什么要有「动作时终判」这一层：
#   敏感窗拒绝原先**只在弹窗层**（授权弹窗的候选列表里不放它们）。
#   但坐标点击（ctl_click --xy）不经过候选窗口那一步 —— 用户即使没在候选里
#   看到它们，仍可能被直接点到授权弹窗 / UAC 框 / 系统设置页。
#   接线单 §二 5 的裁定：**动作时服务端终判**，每次都读黑名单，命中即拒。
#   两道防线是**独立**的：弹窗层管"不问"，终判管"不问也碰不到"。
#
# ★ 第二轮 B：黑名单源解析**多根回退**（名单仍是 act.py 的唯一定义处，不抄）。
#   生产口径是 (a) legacy home 里的 act.py；但 legacy home 可能是**纯状态目录**
#   （验收脚本惯例：AGENTCTL_LEGACY_HOME 只指向一个只有 _auth\ 的临时目录），
#   那里根本没有 act.py。此时**不该**把整条动作通道都判死 —— 名单还在仓里，
#   换一根读它即可。依次尝试，任一根「有 act.py 且五张表齐全」就用它：
#     (a) legacy home             —— 生产口径（gate.HOME）
#     (b) 仓内 legacy_tools\       —— 从 cwd 上溯找仓内的 legacy_tools
#     (c) 包相对 ../../legacy_tools\ —— 从本文件位置推（src\agentctl → 仓根）
#   ★ 用非 (a) 根时**必须**发 warning 写明用的是哪个根（不静默换源）。
#   ★ 三处都没有可用 act.py ⇒ AuthStateUnavailable（**fail-closed 不放宽**）。
#   ★ 换根只换「名单从哪读」；判定口径（deny_window 的比对逻辑）一个字没动。

_DENY_SRC = None
_DENY_TRIED = False
_DENY_ROOT = None          # {"根": 标签, "路径": path, "回退": bool}
_DENY_CANDIDATES = []      # 本次解析尝试过的根（诊断用，deny_source() 读）

# act.py 里必须齐备的五张表 —— 缺任一项视为「这一根不合格」（不猜、不用兜底表）。
_ACT_DENY_NEED = ("BLOCKED_PROCESSES_HARD_LC", "BLOCKED_PROCESSES_TERMINAL_LC",
                  "BLOCKED_CLASSES_HARD_ALL", "BLOCKED_CLASSES_TERMINAL",
                  "BLOCKED_TITLES")


def _cwd_legacy_tools():
    """(b) 仓内 legacy_tools\\：从进程 cwd 上溯找**含 act.py 的** legacy_tools 目录。"""
    cur = os.path.abspath(os.getcwd())
    for _ in range(6):
        cand = os.path.join(cur, "legacy_tools")
        if os.path.isfile(os.path.join(cand, "act.py")):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def _pkg_legacy_tools():
    """(c) 包相对 ../../legacy_tools\\（本文件在 src\\agentctl ⇒ 仓根\\legacy_tools）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "legacy_tools"))


def _deny_candidate_roots():
    """按 (a)(b)(c) 顺序给出候选根 [(标签, 目录) …]（同路径去重、保序）。

    ★ 候选只回答「去哪儿找」；能不能用由 _deny_src() 按
      「有 act.py + 五张表齐」逐个体检决定。
    """
    out, seen = [], set()

    def _add(label, path):
        if not path:
            return
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        out.append((label, path))

    # (a) legacy home —— 生产口径。gate 起不来（或 HOME=None）只是**少一个候选**，
    #     不因此停：还有 (b)(c) 两个仓内根可试。
    try:
        from agentctl import gate as _gate
        _add("legacy-home", getattr(_gate, "HOME", None))
    except Exception:
        pass
    # (b) 仓内 legacy_tools\
    _add("repo-legacy_tools", _cwd_legacy_tools())
    # (c) 包相对 ../../legacy_tools\
    _add("pkg-relative", _pkg_legacy_tools())
    return out


def _warn_deny_root(label, path):
    """非 (a) 根被启用时的 warning —— 审计留痕 + stderr 一定看得见（不静默换源）。"""
    msg = ("⚠️ 敏感窗黑名单源回退：legacy home 里没有可用的 act.py，"
           "改用『%s』根：%s（判据口径未放宽；来源见 authstate.deny_source()）"
           % (label, path))
    try:
        from agentctl import gate as _gate
        _gate.log(msg)                       # 有 AUTHDIR 时进 gate_agentctl.log
    except Exception:
        pass
    try:
        sys.stderr.write("[authstate] %s\n" % msg)
    except Exception:
        pass


def _deny_src():
    """加载真源 act.py，**读它的黑名单常量**（不抄一份）。

    ★ 刻意不把名单搬过来：老线 act.py 才是这份名单的唯一定义处
      （进程名硬拒/终端、类名硬拒/终端、标题兜底五张表），抄一份就是造第二真源
      —— T14 整单消灭的就是这种双份漂移。
    ★ 多根回退（第二轮 B）：依次找 (a) legacy home / (b) 仓内 legacy_tools\\ /
      (c) 包相对 ../../legacy_tools\\；任一根可用即用，并在 warning 里写明用的哪个根。
    三处都读不到 ⇒ **fail-closed 抛错**：判不出有没有命中时，宁可这次不放行。
    """
    global _DENY_SRC, _DENY_TRIED, _DENY_ROOT, _DENY_CANDIDATES
    if _DENY_SRC is not None:
        return _DENY_SRC
    if _DENY_TRIED:
        raise AuthStateUnavailable(
            "敏感窗黑名单源 act.py 三处都读不到 —— 已拒绝执行（fail-closed）")
    _DENY_TRIED = True
    cands = _deny_candidate_roots()
    _DENY_CANDIDATES = [{"根": l, "路径": p} for l, p in cands]
    tried = []
    for label, root in cands:
        path = os.path.join(root, "act.py")
        if not os.path.isfile(path):
            tried.append("%s（无 act.py：%s）" % (label, path))
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                "agentctl._act_deny_src", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            tried.append("%s（加载失败 %s: %s）" % (label, type(e).__name__, e))
            continue
        missing = [n for n in _ACT_DENY_NEED if not hasattr(mod, n)]
        if missing:
            tried.append("%s（缺黑名单常量 %s）" % (label, "、".join(missing)))
            continue
        _DENY_SRC = mod
        _DENY_ROOT = {"根": label, "路径": path, "回退": label != "legacy-home"}
        if _DENY_ROOT["回退"]:
            _warn_deny_root(label, path)
        return mod
    raise AuthStateUnavailable(
        "敏感窗黑名单源 act.py 三处都读不到 —— 已拒绝执行（fail-closed）。试过：%s"
        % "；".join(tried))


def deny_source():
    """诊断口：当前黑名单源用的是哪个根；未加载则给候选表。

    ★ 不抛异常（排障用）。结构：{"根","路径","回退","候选":[…]}。
    """
    if _DENY_SRC is not None and _DENY_ROOT is not None:
        return {**_DENY_ROOT, "候选": list(_DENY_CANDIDATES)}
    cands = _DENY_CANDIDATES or [{"根": l, "路径": p}
                                 for l, p in _deny_candidate_roots()]
    return {"根": None, "路径": None, "回退": None, "候选": list(cands)}


def deny_window(exe=None, cls=None, title=None):
    """动作时终判。返回 (拒?, 原因, 类别)。

    类别两种，语义不同、不能混：
      · "hard" —— 授权弹窗 / 急停弹窗 / UAC 框 / 系统设置页 / 任何 python.exe 拥有的窗。
        ⇒ **永久拒**，连「用户点一下确认放行」这条路都没有（与 act.py 同口径）。
      · "terminal" —— 终端族（cmd / powershell / pwsh / conhost / wt / WindowsTerminal /
        OpenConsole）。⇒ **默认拒**，但 act.py 另有「人当场确认的 30 分钟临时放行」
        通路；**那条路不在这里裁决**，这里只负责"默认拒"。

    比对口径跟老线 act.py 完全一致：
      · 进程名统一小写后再比（同一台机器上实测读出过 cmd.exe 与 PING.EXE 两种写法，
        按 == 精确比大小写等于给自己埋一个漏）；
      · 类名按精确相等；
      · 标题按**子串**。
    """
    m = _deny_src()
    e = str(exe or "").strip().lower()
    c = str(cls or "").strip()
    t = str(title or "")

    if e and e in m.BLOCKED_PROCESSES_HARD_LC:
        return True, ("属主进程 %s 属**永久拒绝族**（授权弹窗 / 急停弹窗 / UAC 框 / "
                      "系统设置页）—— 连人工确认放行这条路都没有" % (exe or "")), "hard"
    if c and c in m.BLOCKED_CLASSES_HARD_ALL:
        return True, "窗口类名 %s 属**永久拒绝族**" % c, "hard"
    for b in (m.BLOCKED_TITLES or ()):
        if b and b in t:
            return True, "标题命中**永久拒绝**项「%s」" % b, "hard"

    if e and e in m.BLOCKED_PROCESSES_TERMINAL_LC:
        return True, ("终端族 %s **默认拒**（要放行只能走 act.py 那条"
                      "「人当场确认 + 30 分钟」的临时通路）" % (exe or "")), "terminal"
    if c and c in m.BLOCKED_CLASSES_TERMINAL:
        return True, "终端窗口类 %s **默认拒**（同上）" % c, "terminal"

    return False, "", ""



# ────────────────────── 读写（禁整文件替换）──────────────────────

def _empty():
    return {"schema": SCHEMA, "task": None, "windows": []}


def _normalize_state(st):
    st.setdefault("schema", SCHEMA)
    st.setdefault("task", None)
    st.setdefault("windows", [])
    return st


def read(path=None):
    """只读快照（无锁；用于面板显示与自证断言）。写路径一律走 _Txn。"""
    p = _resolve(path)
    try:
        with open(p, encoding="utf-8") as f:
            return _normalize_state(json.load(f))
    except FileNotFoundError:
        return _empty()
    except Exception:
        # 坏文件不许被当成「无授权」（fail-closed）：交回空态，调用方按无证处理
        return _empty()


def _read_locked(path):
    try:
        with open(path, encoding="utf-8") as f:
            return _normalize_state(json.load(f))
    except FileNotFoundError:
        return _empty()
    except Exception:
        return _empty()


def _write_locked(path, st):
    """原子写 —— 同样委托共享模块（唯一临时名 mkstemp + 有界重试 replace）。

    ★ 这一步**只能**在锁内调。锁外的「读→改→写」就是 D16 那个
      「两个写者共用同一临时名 ⇒ 3000 次写里 1198 次异常、并把 auth_pass.json
      写成非法 JSON」的事故复现路径。
    """
    _gate_lock().save_json(path, st)


class _Txn:
    """读-改-写事务：**进入即取锁**，退出即落盘。自证里的并发用例靠它。"""

    def __init__(self, path, timeout=LOCK_TIMEOUT):
        self.path = path
        self.authdir = os.path.dirname(os.path.abspath(path))
        self.timeout = timeout
        self.state = None
        self._lock = None

    def __enter__(self):
        self._lock = _exclusive(self.authdir, self.timeout)
        self._lock.__enter__()
        try:
            self.state = _read_locked(self.path)
        except BaseException:
            self._lock.__exit__(None, None, None)
            self._lock = None
            raise
        return self.state

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                _write_locked(self.path, self.state)
        finally:
            if self._lock is not None:
                self._lock.__exit__(exc_type, exc, tb)
        return False


def _txn(path=None, timeout=LOCK_TIMEOUT):
    return _Txn(_resolve(path), timeout=timeout)


# ────────────────────── 判定：父在不在 / 急停没拉 ──────────────────────

def _brake_engaged(brake=None):
    """急停判定。brake 可注入（自证用假值，绝不碰用户真急停）。

    判不出 ⇒ True（当作拉下了）。这是 fail-closed：
    gate.py 的老规矩是「不能为了跑测试去拉用户的真急停」，同理不能因为
    「判不出来」就放行。
    """
    if brake is not None:
        return bool(brake())
    try:
        from agentctl import gate as _gate
        return bool(_gate.brake_engaged())
    except Exception:
        return True


def _parent_alive(task, now):
    return bool(task) and float(task.get("exp", 0)) > float(now)


def _live_windows(st, now):
    """读时裁：父亡 ⇒ 子全灭（铁律②）。返回 [(idx, window, key)]。"""
    task = st.get("task") or {}
    if not _parent_alive(task, now):
        return []
    out = []
    for i, w in enumerate(st.get("windows") or []):
        if float(w.get("exp", 0)) > float(now):
            out.append((i, w, _key(w)))
    return out


def _now_str(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# ────────────────────── API ④ ──────────────────────

def grant_task(scopes=None, ttl=None, acts_max=None, path=None, now=None):
    """**任务授权**入口（用户点一次的那次弹窗）。

    · scopes=None ⇒ 默认 ABCD 一次给齐（dispatch_L ①）
    · scopes 只增不减：已发过 ABCD 再 authorize(A) **不会**把 B/C/D 砍掉
      （授权只能给多不能给少，这是「永不抬高」的对偶，也是防呆）
    · acts_used 归零：这是**新的一次任务授权**，总账重开
      （注意与铁律①区分：窗口放行**永不**重置，只有任务授权才重开）
    """
    now = float(now if now is not None else time.time())
    ttl = DEFAULT_TTL if ttl is None else float(ttl)
    want = norm_scopes(scopes)
    with _txn(path) as st:
        old = st.get("task") or {}
        merged = norm_scopes(list(old.get("scopes") or []) + want)
        amax = int(acts_max if acts_max is not None
                   else (old.get("acts_max") or DEFAULT_ACTS_MAX))
        st["schema"] = SCHEMA
        st["task"] = {
            "scopes": merged,
            "exp": now + ttl,
            "acts_used": 0,
            "acts_max": amax,
            "issued": _now_str(now),
            "nonce": secrets.token_hex(8),
        }
        # 父重签 ⇒ 老窗口的 exp 重新裁到新父上（clip 到 min），不缩窗也不扩权
        for w in st.get("windows") or []:
            w["exp"] = min(float(w.get("exp", 0)) or (now + DEFAULT_TTL), now + ttl)
        return dict(st)


def allow_window(exe, cls, created, ttl=None, path=None, now=None):
    """**窗口放行**入口 —— 铁律①的全部约束都在这一个函数里兑现：

      · 只往 windows 追加，**绝不**整份覆盖
      · **绝不**碰 task.scopes（⇒ 不抬高档位）
      · **绝不**碰 task.acts_used（⇒ 动作总账不重置）
      · 身份键 (exe, cls, created) 去重 ⇒ 同一窗重复放行不增殖
      · 子 exp = min(now + ttl, 父 exp)（clip，不是并存）
    """
    now = float(now if now is not None else time.time())
    ttl = DEFAULT_TTL if ttl is None else float(ttl)
    ent = {"exe": str(exe), "cls": str(cls), "created": _norm_created(created),
           "issued": _now_str(now)}
    k = _key(ent)
    with _txn(path) as st:
        task = st.get("task")
        if not task:
            # 没有父任务就没有可挂靠的档位 —— 拒绝凭空造子（fail-closed）
            raise AuthStateDenied("没有任务授权，先 grant_task 再放行窗口")
        # ① 追加 + 去重：已存在则不重复追加（幂等），但不覆盖原有条目
        windows = st.setdefault("windows", [])
        hit = None
        for w in windows:
            if _key(w) == k:
                hit = w
                break
        clip = min(now + ttl, float(task.get("exp", 0)))
        if hit is None:
            ent["exp"] = clip
            windows.append(ent)
        return dict(st)


def check(scope, window=None, path=None, now=None, brake=None):
    """判一次档位是否可用。返回 dict（ok 布尔 + reason + 诊断字段）。

    window=None 只判任务档位；给了 window={'exe','cls','created'} 则**额外**要求
    该窗口在册且未过期 —— 「窗口放行」这个动作的本质就是这一条。

    判定顺序（先便宜后贵，任一不过即拒，全程 fail-closed）：
      1. 急停？拉下 ⇒ 父与子**一并**判灭（铁律②）
      2. 有父任务？exp 未到？（父亡 ⇒ 子全灭）
      3. 该档位在 task.scopes 里？
      4. 给了 window 的话：它在 windows 里且自己也没过期？
    """
    now = float(now if now is not None else time.time())
    want = norm_scope(scope)
    st = read(path)
    task = st.get("task") or {}
    out = {"ok": False, "reason": "", "scope": want, "now": now,
           "task_exp": task.get("exp"), "window_exp": None, "deny_kind": None}

    if _brake_engaged(brake):
        out["reason"] = "急停已拉下（父与子一并判灭）"
        return out
    if not task:
        out["reason"] = "没有任务授权"
        return out
    if not _parent_alive(task, now):
        out["reason"] = "任务授权已过期（子授权同时失效）"
        return out
    if want not in (task.get("scopes") or []):
        out["reason"] = "本次持 %s，档位 %s 未授权" % (
            "、".join(task.get("scopes") or []) or "（无）", want)
        return out
    if window is not None:
        # ★ 动作时终判（接线单 §二 5）：敏感窗在这里被拒。
        #   与「弹窗层不进候选列表」是两道**独立**防线 ——
        #   弹窗层管"不问"，这里管"不问也碰不到"：
        #   坐标点击（ctl_click --xy）根本不经过候选窗口那一步。
        hit, why, kind = deny_window(window.get("exe"), window.get("cls"),
                                     window.get("title"))
        if hit:
            out["reason"] = why
            out["deny_kind"] = kind
            return out
        k = (str(window.get("exe", "")), str(window.get("cls", "")),
             _norm_created(window.get("created")))
        live = [w for _, w, _k in _live_windows(st, now) if _k == k]
        if not live:
            out["reason"] = "窗口未放行或已失效（需放行窗口）"
            return out
        out["window_exp"] = live[0].get("exp")
    out["ok"] = True
    out["reason"] = "放行"
    return out


def consume(n=1, scope=None, path=None, now=None, brake=None):
    """扣动作次数（任务总账）。附赠 API，但**不可省**：

    acts_used 若没有消耗路径就是死字段，「窗口放行不重置计数」这条断言
    也就无从证伪 —— 所以它必须有真扣减。
    额度用尽 ⇒ 抛 AuthStateDenied（绝不当成功）。
    """
    now = float(now if now is not None else time.time())
    with _txn(path) as st:
        task = st.get("task")
        if not task:
            raise AuthStateDenied("没有任务授权")
        if _brake_engaged(brake):
            raise AuthStateDenied("急停已拉下")
        if not _parent_alive(task, now):
            raise AuthStateDenied("任务授权已过期")
        if scope is not None:
            want = norm_scope(scope)
            if want not in (task.get("scopes") or []):
                raise AuthStateDenied("档位 %s 未授权" % want)
        used = int(task.get("acts_used", 0)) + int(n)
        amax = int(task.get("acts_max", DEFAULT_ACTS_MAX))
        task["acts_used"] = used
        task["last_act"] = _now_str(now)
        if used > amax:
            raise AuthStateDenied("动作次数已超上限（%d/%d）—— 本次拒绝" % (used, amax))
        return dict(st)


def revoke_all(path=None):
    """一键吊销：父与子**同时**清空。走锁，不留半截状态。"""
    with _txn(path) as st:
        st["schema"] = SCHEMA
        st["task"] = None
        st["windows"] = []
        return dict(st)

