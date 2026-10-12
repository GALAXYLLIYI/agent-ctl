# -*- coding: utf-8 -*-
r"""core.py —— AgentCtl 的**唯一调度核心**（三态同源的"同源"就在这里）

设计依据：详细设计 §6.1 / §6.2 / §6.3
  · 统一信封：{ok, code, data, event_id, frame_before, frame_after, session_id,
               t_mono, t_wall, elapsed_ms, blind_spots, warning}
  · HTTP / MCP / CLI 三种传输**都调本模块的 dispatch()** —— 行为必须一致。
    （若三处各写一套，那就不是"三态同源"，是三套会各自漂移的实现。）

★ 屏控类工具的执行策略：**包壳调用现有脚本**（详细设计 §10-2）
  现有 tools\perceive.py、act.py **自己就会过闸门**（它们 import gate）。
  服务再包一层，等于"闸门被过了两次" —— 多一层校验不会变松，只会更严。
  这样老工具**一行不用改**，新服务也能立刻用。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(SRC_DIR)

if getattr(sys, "frozen", False):
    # ⚠️ 2026-10-02 冻结验收抓到的 bug：冻结后 __file__ 在 _internal\agentctl 下，
    #    按三层 dirname 算出的 ROOT 是 dist\AgentCtl，而契约其实被 --add-data 放进了
    #    _internal\contract\ → 找不到契约，tools/list 只返回 1 条错误项。
    #    **冻结后必须认 _MEIPASS。**
    BASE = getattr(sys, "_MEIPASS", os.path.dirname(SRC_DIR))
    ROOT = BASE
else:
    BASE = os.path.dirname(SRC_DIR)          # .../agent-ctl/src
    ROOT = os.path.dirname(BASE)             # .../agent-ctl
TIMELINE_DIR = os.environ.get("AGENTCTL_TIMELINE",
                              os.path.join(ROOT, "work", "timeline"))

sys.path.insert(0, SRC_DIR)

from agentctl import gate                                # noqa: E402
from agentctl.store import Store                         # noqa: E402
from agentctl.clock import Clock, Witness                # noqa: E402
from agentctl import tasks_dispatch                     # noqa: E402
from agentctl import uia_tree                           # noqa: E402（不截图的识别通道）

# ★ 老脚本目录**只从 gate 拿**（A 项，2026-10-04）★
# 原来这里自己写死 <项目目录>
# 加上 eye.py / widget.py / agentctl_main.py 一共 4 处同一个硬编码 ——
# 4 份拷贝迟早会各自漂移（今天就已经漂了：gate 的急停搬到 %LOCALAPPDATA%，
# 那 4 处还写着老路径）。**单一真相源 = gate**，这里只做转发。
LEGACY = gate.HOME

# ═════ 截图落盘目录（2026-10-07 新增）════════════════════
# 原来 perceive.py 里写死了一个绝对路径（<工作手册>/监控/shots），两个问题：
#   ★ 换台机器直接崩（写死路径）。
#   ★ 我们的取证图往**用户手册库**里堆（那是另一个库，归属不对）。
#   那个目录历史已累 1309 个文件。
#
# 冻结态**不能用 ROOT**：那时 ROOT = _MEIPASS（PyInstaller 解包临时目录，
#   退出即清、可能不可写）。所以冻结态落到 LOCALAPPDATA/AgentCtl/shots ——
#   跟急停标志、心跳放一起，是本项目既有的数据目录约定。
if getattr(sys, "frozen", False):
    _shots_default = os.path.join(gate.APP_DIR, "shots")     # persistent, writable
else:
    _shots_default = os.path.join(ROOT, "work", "shots")      # source mode: work/ is gitignored
SHOTS_DIR = os.environ.get("AGENTCTL_SHOTS_DIR", _shots_default)

SCHEMA_VERSION = 1
_STORE: Store | None = None
_CLOCK = Clock()


def store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store(TIMELINE_DIR)
    return _STORE


def env(**head) -> dict:
    """统一信封"""
    now = time.time()
    d = {
        "ok": False, "code": gate.CODE_NO_CTRL, "data": None,
        "event_id": None, "frame_before": None, "frame_after": None,
        "session_id": "", "t_mono": round(time.perf_counter(), 6),
        "t_wall": _iso(now), "elapsed_ms": 0,
        "blind_spots": [], "warning": None,
    }
    d.update(head)
    return d


def _iso(ts: float) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="milliseconds")


# ────────────── 通知通道（2026-10-07）───────────────────────────
# 用户反馈的原话：「我授权了但没有反馈到你」。
# 事实是：授权事件一条不混写进 timeline.db，但**agent 没方法知道它发生了**。
# 轮询是不可行的：等 5 分钟就是十几次工具调用，而 agent 的回合是贵的。
#
# 为什么只挑这几种：授权、急停、用户确认 —— 都是**用户那边产生的事**。
# 自己做的 shot/click/type 自己就知道，放进来只是噪声，会把信号溟没。
NOTICE_KINDS = (
    "brake", "brake_release",
    "authorize_request", "authorize_granted", "authorize_denied",
    "task_verified", "task_failed",
)
_notice_cursor = {"seq": None}   # None = 本进程还没对齐过


def _notice_text(r: dict) -> str:
    """事件 → 一句人话。用户看的是中文，不是 kind 字符串。"""
    k = r.get("kind") or ""
    raw = r.get("payload")
    # ⚠ payload 在 events() 里是 **JSON 字符串**（canonical() 写的），不是 dict。
    #   不解析就拿不到 scopes，而且不报错、不猜。
    p = {}
    if isinstance(raw, str):
        try:
            import json as _j
            v = _j.loads(raw)
            if isinstance(v, dict):
                p = v
        except Exception:
            p = {}
    elif isinstance(raw, dict):
        p = raw
    sc = p.get("scopes")
    if isinstance(sc, list):
        sc = "、".join(str(x) for x in sc)
    if k == "brake":
        return "用户拉下了急停 —— 一切自动控制已停止"
    if k == "brake_release":
        return "急停已解除"
    if k == "authorize_request":
        return "用户开始授权：%s" % (sc or "范围未知")
    if k == "authorize_granted":
        return "用户已授权：%s" % (sc or "范围未知")
    if k == "authorize_denied":
        return "授权被拒（用户拒绝或超时）：%s" % (sc or "")
    if k == "task_verified":
        return "用户确认任务完成：%s" % (p.get("name") or p.get("task_id") or "")
    if k == "task_failed":
        return "任务失败：%s" % (p.get("why") or p.get("note") or "")
    return k


def _notices(max_n: int = 5):
    """取「上次调用之后」的要紧事件，并推进游标。

    返回 (list, ok)。ok=False 时调用方应当没这个字段看，不要当成「没事发生」。
    """
    try:
        rows = store().events(limit=300)
        head = store().head[0]
    except Exception:
        return [], False
    cur = _notice_cursor["seq"]
    if not rows and cur is None:
        # ⚠ 空库也要给「已同步」标记。
        #   早版这里 return [], False，结果是**新建 timeline 库上 notices 字段整个消失**，
        #   agent 于是以为自己不需要同步（它不会知道自己不知道）。
        #   但**只能在首次**：之后库还是空就说明没新事，应该返回空列表。
        _notice_cursor["seq"] = head
        return ([{"seq": head, "kind": "_synced", "t": "", "ok": None,
                  "note": "事件库还是空的，已对齐到 #%d" % head}], True)
    if not rows:
        return [], True          # 库空 + 已同步过 → 真的没新事件
    if cur is None:
        # 本进程第一次：先对齐到当前 head，不倒垃圾历史。
        # ⚠ 但必须**明说**「已同步到 #N」，否则 agent 会以为自己知道一切 ——
        #   而事实是授权可能早于它启动。不说就等于掩盖。
        _notice_cursor["seq"] = head
        # ★ 同时把**近期要紧事件**也给出来。
        #   新 agent 进程没有任何记忆，它不可能知道「自己启动前用户授过权」。
        #   今天贐的就是这个坑：用户 14:40 授权，agent 之后启动，会毫无字地把那条吞掉。
        #   但每次都倒整个历史也属自相矛盾，所以只给最近几条。
        # ★ events() 返回是**倒序**（最新在前）。
        #   早版写的是 for r in reversed(rows)，结果取到的是**最旧的那几条**，
        #   而不是最新的 —— 用户**刚授的权**被 10 月初的旧事件挤掉了（实测真发生）。
        #   正确做法：直接顺序扫（就是从最新往旧），取到 max_n 再反转展示。
        recent = []
        for r in rows:                       # rows 已经是最新在前
            if (r.get("kind") or "") not in NOTICE_KINDS:
                continue
            recent.append({"seq": r.get("seq"), "kind": r.get("kind"),
                           "t": (r.get("t_wall") or "")[:19], "actor": r.get("actor") or "",
                           "ok": r.get("ok"), "note": _notice_text(r)})
            if len(recent) >= max_n:
                break
        recent.reverse()                     # 展示时最旧在前，便于从上往下读
        out = [{"seq": head, "kind": "_synced", "t": "", "ok": None,
                "note": "已同步到事件 #%d；之后的用户动作会自动带上。"
                       "以下是启动前最近的要紧事件" % head}]
        out.extend(recent)
        return out, True
    oldest = rows[-1]["seq"]
    # ⚠ 差一个很容易错：进窗口的是 oldest 自身，所以漏掉的是 oldest-1 之前那段，
    #   而不是 oldest 之前。cur=0 / oldest=1 时中间一条都没漏，报了就是假告警。
    gap = (oldest - 1) > cur      # 游标落在窗口外 → 中间那段真的没看到，必须如实报告
    _notice_cursor["seq"] = head
    out = []
    for r in reversed(rows):     # 老的在前、新的在后
        if r.get("seq", 0) <= cur or (r.get("kind") or "") not in NOTICE_KINDS:
            continue
        out.append({"seq": r.get("seq"), "kind": r.get("kind"),
                    "t": (r.get("t_wall") or "")[:19], "actor": r.get("actor") or "",
                    "ok": r.get("ok"), "note": _notice_text(r)})
    if out:
        out = out[-max_n:]
    if gap:
        out.insert(0, {"seq": cur, "kind": "_gap", "t": "", "ok": None,
                       "note": "有事件漏看了（#%d~#%d），要全量请调 ctl_timeline" % (cur + 1, oldest - 1)})
    return out, True


def t_wait(args: dict) -> dict:
    """阻塞等一个事件 —— 不靠轮询烧轮次。

    用事实：agent 卡在「等用户授权」这个硬依赖上。
    轮询它也行，但等 5 分钟就是十几次工具调用。
    这里一次调用就占一个回合，等到事件到达立即醒。

    ★ 超时**不是错误**：超时是 agent 自己选的等待上限，ok=True + timed_out=true。
      把它报成 ok=False 就是重复了老问题——ok=False 有两种含义（正常拒绝 / 代码崩），判实必须看内容。
    """
    kind = args.get("kind")
    actor = args.get("actor") or None
    try:
        timeout = max(1, min(600, int(args.get("timeout_sec") or 120)))
    except Exception:
        timeout = 120
    # ★ 快照起点：只等「调用之后新到的」事件。
    #   否则会被调用前就存在的同类事件立刻唤醒 —— 那不是等待，是一开立刻返回。
    #   用户之前授过的权，不能被本次等待当成「刚授的」。
    try:
        start_seq = int(args.get("since_seq") or store().head[0])
    except Exception:
        start_seq = 0
    deadline = time.monotonic() + timeout
    polls = 0
    while True:
        polls += 1
        try:
            rows = store().events(limit=200, kind=kind)
        except Exception as e:
            return env(code=gate.CODE_NO_CTRL,
                       warning="等待失败（读事件流报错）：%s" % e)
        for r in rows:                      # rows 是倒序，第一条匹配的就是最新的
            if r.get("seq", 0) <= start_seq:
                continue
            if actor and (r.get("actor") or "") != actor:
                continue
            return env(ok=True, code=0, data={
                "woke": True, "timed_out": False, "polls": polls,
                "event": {"seq": r.get("seq"), "kind": r.get("kind"),
                          "t_wall": r.get("t_wall"), "actor": r.get("actor"),
                          "ok": r.get("ok"), "note": _notice_text(r)},
            })
        if time.monotonic() >= deadline:
            return env(ok=True, code=0, data={
                "woke": False, "timed_out": True, "polls": polls,
                "waited_sec": timeout, "head": store().head[0],
                "note": "等了 %d 秒没等到「%s」（不是错误，是你自己设的上限）" % (timeout, kind or "任何事件"),
            })
        time.sleep(0.25)          # 服务端内部轮询，不占 agent 回合


def _py_argv(script: str, args: list[str]) -> list[str]:
    r"""给出"用哪个解释器跑这个脚本"的正确命令行。

    ★★ 2026-10-02 打包验收抓到的**致命 bug**（"打得出来 ≠ 打出来能用"的典型）：
       原来一律写 `[sys.executable, "-X", "utf8", script]`。
       **打包后 `sys.executable` 是 `AgentCtl.exe`** —— 于是变成在跑
           `AgentCtl.exe -X utf8 perceive.py --windows`
       而 exe 不认 `-X`，它会把自己再当 CLI 跑一遍，实测报：
           "warning": "未知工具：ctl_-X"
       → **整个包的服务调不动任何老脚本**（ctl_windows / ctl_shot / ctl_tree / ctl_authorize 全废）。

       修法：冻结时走 exe 自己的 `legacy` 子命令（用**自带的解释器**执行脚本），
       非冻结时照旧用 python。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "legacy", script] + list(args)
    return [sys.executable, "-X", "utf8", script] + list(args)


def _legacy_env() -> dict:
    """spawn 老脚本时用的环境（2026-10-05，W1 治本那一步）。

    为什么要传 AGENTCTL_BRAKE_FLAG：
      老脚本 `act.py:30` 的 `import gate` 命中哪个 gate.py，取决于它自己
      的 sys.path —— **不一定是**老项目 tools 目录里那一个。
      所以「改老 gate 的常量」只是治标；把 flag 路径塞进子进程环境，
      才能保证**不管解析到哪个 gate，读的都是同一个文件**。
      这才是 WorkBuddy 说的「双保险里有一半是瞎的」的真正修法。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env["AGENTCTL_BRAKE_FLAG"] = gate.BRAKE_FLAG
    env.setdefault("AGENTCTL_LEGACY_HOME", LEGACY) if LEGACY else None
    return env


# ★ D17 · L1 止血（2026-10-09）：动作类老脚本调用的默认上限 120s → 15s。
#   依据：ZCode 三问答复（dialogue_log id=450）实测 —— 冷启动 import uiautomation
#   0.10~0.14s、顶层枚举 11 窗 0.10s、树采集 89 节点 0.07s、历史真实动作前后图全 1s 级
#   ⇒ 15s 有 7 倍以上余量；唯一超 15s 的场景是「无响应窗口的 COM 等待」，而那正是本单要切断的。
#   动机：单次卡住的上限会被 MCP 同步主循环放大成「整个平台堵住」
#   （见 框架\2026-10-09_给GLM的互评请求_ctltype挂起.md）。调用方仍可用
#   args["timeout_sec"] 逐次覆盖。
ACT_TIMEOUT_SEC = 15

# ★ P0 收口（2026-10-10，Codex 挖出外层缺口 / 用户批准一起解决 / ZCode 实现）★
#   rc=7 → _ask_then_retry 的弹窗等待原来默认 240s，且**不分渠道**：
#   ACT_TIMEOUT_SEC 管不到它，最坏账 = 15s(首次 act) + 240s(弹窗) + 15s(重试) ≈ 270s；
#   而 MCP 主循环是同步的（mcp.py `for line in sys.stdin`），一个 tools/call 卡住，
#   同一连接后续全部排队 —— 这正是 D17 立单时说的「单次卡住放大成整个平台堵住」，
#   弹窗这条路径是它想压那个值的 16 倍。
#   修法按「爆炸半径」定上限（B 的判据不用猜「有没有人在」，用渠道，可靠）：
#     - MCP 渠道：堵的是整个平台 ⇒ 默认 20s；
#     - CLI 渠道：只堵调用进程自己 ⇒ 保留 240s，给用户走到屏幕前的时间。
#   args["ask_timeout_sec"] 显式传值永远最优先。mcp.py 启动时把 CHANNEL 改成 "mcp"。
CHANNEL = "cli"
ASK_TIMEOUT_SEC_CLI = 240
ASK_TIMEOUT_SEC_MCP = 20
# rc=7 弹窗请求范围用：档位字母 → auth_popup 认的范围串（与 SCOPES 全名一致）
SCOPES_TXT = {"A": "A级 只读感知", "B": "B级 点击滚动",
              "C": "C级 打字快捷键", "D": "D级 拖拽"}


def _ask_timeout(args: dict) -> int:
    """弹窗等待上限：显式参数 > 渠道默认。"""
    return int(args.get("ask_timeout_sec")
               or (ASK_TIMEOUT_SEC_MCP if CHANNEL == "mcp" else ASK_TIMEOUT_SEC_CLI))


def _legacy(name: str, args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """调用现有脚本（它们自己会过闸门）。返回 (returncode, stdout, stderr)。"""
    if not LEGACY:
        # 老脚本目录没定位到：**说清楚为什么**，别只丢一句「找不到脚本」
        return 127, "", gate._missing_hint()
    p = os.path.join(LEGACY, name)
    if not os.path.exists(p):
        return 127, "", f"找不到老脚本 {p}"
    try:
        r = subprocess.run(_py_argv(p, args),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=LEGACY,
                           env=_legacy_env())
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"超时（{timeout}s）"
    except Exception as e:
        return 125, "", f"{type(e).__name__}: {e}"


# ────────────────────────── 工具实现 ──────────────────────────
_INTEGRITY_CACHE: dict = {}


def _my_integrity() -> dict:
    r"""报告**本服务进程自己**的完整性级别（结果缓存，只真正探测一次）。

    ★ 为什么要有这个：2026-10-02 我在这上面栽了一大圈 ——
      跨完整性级别时 UIPI 会拦掉 UIA 与合成输入，表现为"点击返回 `rc=0` 但界面没变"。
      我当时**没有先量自己**，把"对方比我高"错当成"对方是管理员"，还把错结论写进了三份文档。
      → 这类诊断的第一手数据就是"我在哪个级别"，所以让它随 `ctl_status` 一起报出来。

    ★★ 为什么改成"调 whoami"而不是自己解析 token 结构体：
      第一版用 ctypes 解析 TOKEN_MANDATORY_LABEL。问题在于 ——
      **在 Low 下那段代码永远提前返回（读不到自己的 token），"成功分支"从未被执行过**；
      而服务一旦以 Medium 启动，那段**未测代码会第一次被跑**。
      **未测路径就是最大的风险** —— 尤其在"服务刚起来就退出"这种情况下，它就是头号嫌疑。
      改用 `whoami /groups` 解析文本：慢几十毫秒，但**没有指针运算、没有结构体布局假设**，
      不会因为换了完整性级别就踩到没跑过的分支。**可靠性优先于花哨。**
    """
    if _INTEGRITY_CACHE:
        return _INTEGRITY_CACHE
    names = {0x0000: "Untrusted", 0x1000: "Low", 0x2000: "Medium",
             0x2100: "MediumPlus", 0x3000: "High", 0x4000: "System"}
    out: dict = {"rid": None, "name": "未探测"}
    try:
        r = subprocess.run(["whoami", "/groups"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
        txt = (r.stdout or "") + (r.stderr or "")
        for line in txt.splitlines():
            if "Mandatory Label" in line or "强制标签" in line:
                out["raw"] = line.strip()[:120]
                for rid, nm in names.items():
                    if f"S-1-16-{rid}" in line:
                        out["rid"] = f"0x{rid:04X}"
                        out["name"] = nm
                        break
                if out["rid"] is None:
                    for nm in names.values():
                        if nm in line:
                            out["name"] = nm
                            break
                break
    except Exception as e:
        out["name"] = f"探测失败 {type(e).__name__}"
    if out.get("rid"):
        low = int(out["rid"], 16) < 0x2000
        out["note"] = ("★ Low/Untrusted：**控制不了 Medium 窗口**（UIPI 会拦掉点击与 UIA）——"
                       "请改用普通完整性的终端启动本服务") if low else "同级别及以下的窗口可控"
    else:
        out["note"] = "读不到自己的完整性级别（whoami 输出里没找到 Mandatory Label）"
    _INTEGRITY_CACHE.update(out)
    return _INTEGRITY_CACHE


def t_status(args: dict) -> dict:
    st = gate.status()
    st["service"] = {"running": True, "schema_version": SCHEMA_VERSION,
                     "legacy_home": LEGACY, "timeline": TIMELINE_DIR,
                     "frozen": bool(getattr(sys, "frozen", False)),
                     "integrity": _my_integrity()}      # ★ 见该函数注释
    st["recorder"] = {"recording": False, "profile": "not_implemented"}
    st["timeline"] = {"head_event_id": store().head[0], "head_hash": store().head[1][:16]}
    return env(ok=True, code=0, data=st)


def t_lease(args: dict) -> dict:
    action = (args.get("action") or "").lower()
    actor = args.get("actor") or "unknown"
    if action == "status":
        return env(ok=True, code=0, data=gate.lease_status())
    if action == "acquire":
        try:
            L = gate.acquire_lease(actor, args.get("scopes") or ["B"], int(args.get("ttl_sec") or 600))
        except ValueError as e:
            # ★ 2026-10-10 修逗号 bug 的配套：范围串看不懂就显式拒，
            #   绝不把非法档位静默写进租约（改前实测 scopes: [",","B","C","D"]）
            return env(code=gate.CODE_NO_CTRL, warning=str(e))
        except gate.GateDenied as e:
            return env(code=e.code, data={"holder": gate.lease_status().get("holder")}, warning=e.why)
        _ev("lease_acquire", actor=actor, tool="ctl_lease", payload={"scopes": L["scopes"]})
        return env(ok=True, code=0, data=L)
    if action == "renew":
        try:
            L = gate.renew_lease(args.get("lease_id") or "", int(args.get("ttl_sec") or 600))
        except gate.GateDenied as e:
            return env(code=e.code, warning=e.why)
        return env(ok=True, code=0, data=L)
    if action == "release":
        try:
            ok = gate.release_lease(args.get("lease_id") or "")
        except gate.GateDenied as e:
            return env(code=e.code, warning=e.why)
        _ev("lease_release", actor=actor, tool="ctl_lease", payload={})
        return env(ok=True, code=0, data={"released": ok})
    return env(code=gate.CODE_NO_CTRL, warning=f"未知 action: {action}")


def t_brake(args: dict) -> dict:
    action = (args.get("action") or "status").lower()
    if action == "status":
        return env(ok=True, code=0, data={"engaged": gate.brake_engaged(), "flag": gate.BRAKE_FLAG})
    if action == "stop":
        # ★ 2026-10-05（W7，WorkBuddy 004）：这里原来是**第四份**急停写入实现，
        #   自己 open(...,'w') 直写，没有 .tmp + os.replace —— 和 gate.set_brake
        #   并存意味着「原子性只有走 ctl_brake 才有，走 t_brake 就没了」。
        #   安全机制有多份实现时，每次只记得改其中一份。
        #   → 现在统一调 gate.set_brake，顺带继承 W5 的 pid + 有界重试。
        why = args.get("why") or "未说明"
        ok, msg = gate.set_brake(f"接口 ctl_brake（actor={args.get('actor') or '?'}）：{why}")
        if not ok:
            # 拉闸失败是**明确失败**，不假装成功 —— 静默失败比慢危险得多
            return env(code=gate.CODE_NO_CTRL, warning=msg)
        _ev("brake", actor=args.get("actor", ""), tool="ctl_brake",
            payload={"why": why})
        return env(ok=True, code=gate.CODE_BRAKE,
                   data={"engaged": True, "flag": gate.BRAKE_FLAG},
                   warning="刹车已拉下（这是预期结果，不是错误）；%s" % msg)
    if action == "release":
        # 同 W7：解除也统一走 gate，别再自己 os.remove
        ok, msg = gate.clear_brake()
        if not ok:
            return env(code=gate.CODE_NO_CTRL, warning=msg)
        _ev("brake_release", actor=args.get("actor", ""), tool="ctl_brake", payload={})
        return env(ok=True, code=0, data={"engaged": False}, warning=msg)
    return env(code=gate.CODE_NO_CTRL, warning=f"未知 action: {action}")


def t_timeline(args: dict) -> dict:
    limit = int(args.get("limit") or 200)
    kind = args.get("kind")
    rows = store().events(limit=min(limit, 2000), kind=kind,
                          since_wall=args.get("since_wall"))
    return env(ok=True, code=0, data={"events": rows, "head": store().head[0]})


def t_verify(args: dict) -> dict:
    ok, bad, stats = store().verify(check_frames=bool(args.get("check_frames", True)))
    return env(ok=ok, code=0 if ok else gate.CODE_NO_CTRL,
               data={"first_bad": bad, "stats": stats},
               warning=None if ok else "时间线校验未通过")


def t_witness(args: dict) -> dict:
    """见证：对一张**已存在**的图做（不抓屏）。没有图就只能报"没图"。"""
    src = args.get("image") or ""
    if not src or not os.path.exists(src):
        # 合并搜 SHOTS_DIR 和旧目录：旧图还在，不能因为换了目录就找不到历史证据。
        cand = sorted(glob.glob(os.path.join(SHOTS_DIR, "*_full.png")))
        if not cand and LEGACY:
            cand = sorted(glob.glob(os.path.join(LEGACY, "..", "..", "agent-manual",
                                                                          "_监控", "shots", "*_full.png")))
        if not cand:
            return env(code=gate.CODE_NO_CTRL, warning="没有可用图（本接口不负责抓屏）")
        src = cand[-1]
    work = os.path.join(TIMELINE_DIR, "_witness_tmp.png")
    os.makedirs(TIMELINE_DIR, exist_ok=True)
    try:
        gate.require_any(gate.PERCEIVE, "见证（读图）", count=False)
    except gate.GateDenied as e:
        return env(code=e.code, warning=e.why)
    wb = Witness()
    r = wb.check(src, work, now_ts=args.get("now_ts"))
    _ev("witness", actor=args.get("actor", ""), tool="ctl_witness", payload=r,
        ok=(r["result"] in ("ok", "drift")))
    return env(ok=r["result"] in ("ok", "drift"), code=0, data=r,
               blind_spots=[] if r["result"] != "未识别" else ["见证 OCR 本次未识别"])


# ── 屏控类：包壳调用老脚本（老脚本自己会过闸门）──
def _issue_capture_from_tree(hwnd: int, raw_out: str) -> str | None:
    """从元素树签发 capture_id（2026-10-07 新增）。

    元素树**就是一次观测**，不比截图差 —— 而且指纹更准：
    截图哈希只能告诉你「画面变了」，元素指纹能告诉你「哪个按钮没了/多了」。
    没有截图文件，所以图像哈希留空（闸门会跳过图像比对，只校验 rect/前台/TTL），
    **但元素指纹照算，不许假装没有。**
    """
    # ★ T15（2026-10-09）：这个函数以前被下面那个截图版**同名覆盖**成死代码，
    #   于是元素指纹 capture 从 10-07 起从未生效。现在分流命名，两个版本各归各位：
    #     _issue_capture_from_tree ← ctl_tree / ctl_find / ctl_click 的 eid 解析
    #     _issue_capture_from_shot ← ctl_shot / ctl_windows（输出是子进程 stdout 字符串）
    import hashlib as _hl2
    try:
        nodes = raw_out.get("节点") or raw_out.get("命中") or []
        if not nodes:
            # ⚠ 不可直接 return None —— 那是**削弱**不是收紧。
            #   ctl_tree 默认不带 --detail 时 uia_tree.tree() 的「节点」就是空列表
            #   （uia_tree.py:415「"节点": nodes if detail else []」），
            #   ctl_find 没命中时也是空的。改前这些情况照样签发 capture
            #   （只是两个哈希都空），改后就静默不发 ⇒ 坐标动作会丢观测绑定。
            #   所以这里**照旧签一份没指纹的**，保住原有能力；
            #   有节点时才有真指纹 —— 那才是净收紧。
            rect0 = _win_rect(hwnd)
            if rect0 is None:
                return None
            return gate.issue_capture(hwnd, rect0, "",
                                      bool(_is_foreground(hwnd)))
        sig = [(n.get("type"), (n.get("name") or "")[:40], tuple(n.get("rect") or ()))
               for n in nodes]
        blob = json.dumps(sig, ensure_ascii=False, sort_keys=True).encode("utf-8")
        tree_sha = _hl2.sha256(blob).hexdigest()
        return gate.issue_capture(hwnd,
                                  rect=_win_rect(hwnd) or [0, 0, 0, 0],
                                  image_sha256="",
                                  foreground=bool(_is_foreground(hwnd)),
                                  tree_sha256=tree_sha)
    except Exception:
        return None


def _issue_capture_from_shot(hwnd: int, raw_out: str) -> str | None:
    """从 perceive/act 的输出里尽力取出帧文件路径，算 sha256，签发 capture_id。

    取不到就把图像哈希留空（闸门那边会跳过图像比对，只校验 rect／前台／TTL）—— **不许假装有**。
    """
    import hashlib as _hl
    img_sha = ""
    try:
        j = json.loads(raw_out)
        cand = []

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(v, str) and v.lower().endswith((".png", ".jpg", ".jpeg")):
                        cand.append(v)
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(j)
        for c in cand:
            p = c if os.path.isabs(c) else (os.path.join(LEGACY, c) if LEGACY else "")
            if os.path.exists(p):
                with open(p, "rb") as f:
                    img_sha = _hl.sha256(f.read()).hexdigest()
                break
    except Exception:
        pass
    rect = _win_rect(hwnd)
    fg = _is_foreground(hwnd)
    if rect is None:
        return None
    try:
        return gate.issue_capture(hwnd, rect, img_sha, bool(fg))
    except Exception:
        return None


# ═════ 进程内感知（2026-10-07）═════════════════════════
# 原来每次 ctl_shot / ctl_windows 都要 spawn 一个 Python 子进程去跑 perceive.py。
# 实测（2026-10-07 本机）：
#     整条 dispatch 中位 315 ms，而纯截图只要 22 ms —— 15 倍差部全是进程启动 + 管道开销：
#       空 python -c pass            28 ms
#       python + import agentctl.eye 139 ms
#       进程内截图           22 ms
# 新做法：在本进程里 import perceive.py 直接调它的函数。
#   第一次约 160 ms（一次性导入），之后每次约 25-40 ms。
#
# ★ 同时把 perceive.gate 换成 agentctl.gate（注入，不改别人的文件）
#   perceive.py import 的是 <另一项目>/tools/gate.py（另一个项目的独立副本，只有 6.8 KB），
#   它给「只读感知」也在扣 acts 计数器，把 ACT_LIMIT[A]=200 那个设计彻底废它。
#   在这里注入我们的 gate，就能用「单一真相源」的方式修好，不动 dsh 项目。
_perceive_mod = {"m": None, "tried": False}


def _load_perceive():
    """进程内加载 perceive.py，并把它用的 gate 换成 agentctl.gate。

    失败返回 None（调用方自动回退到子进程）—— 不为了一个快一点的优化把功能改坏。
    """
    if _perceive_mod["tried"]:
        return _perceive_mod["m"]
    _perceive_mod["tried"] = True
    try:
        if LEGACY and LEGACY not in sys.path:
            sys.path.insert(0, LEGACY)
        import perceive as _p                     # noqa: E402
        _p.gate = gate                            # 单一真相源：改掉它自己那份副本
        _p.SHOTDIR = SHOTS_DIR                   # shots dir, injected too
        _perceive_mod["m"] = _p
    except Exception as e:
        try:
            gate.log("进程内感知加载失败，回退子进程：%s: %s" % (type(e).__name__, e))
        except Exception:
            pass
        _perceive_mod["m"] = None
    return _perceive_mod["m"]


def _look_only(p):
    """只看一眼：截图存内存，算一个降采样指纹，**不写磁盘**。

    返回的是一个“屏幕变了什么”的指纹，不是证据。
    要证据（如果要给坐标动作绑 capture_id）必须用默认的完整模式写文件。
    """
    import hashlib as _hl
    import numpy as _np
    from PIL import Image as _Im
    try:
        import mss as _mss
        with _mss.mss() as _sct:
            _mon = _sct.monitors[0]
            _arr = _np.asarray(_sct.grab(_mon))
    except Exception as e:
        return {"拒绝": "快照采集失败：%s" % e}
    _rgb = _arr[:, :, :3] if _arr.ndim == 3 and _arr.shape[2] >= 3 else _arr
    _full = _Im.fromarray(_rgb)
    _small = _full.resize((320, 200))
    return {"类型": "快照（不写文件）",
            "屏幕": [_full.size[0], _full.size[1]],
            "指纹尺寸": [320, 200],
            "指纹": _hl.sha256(_small.tobytes()).hexdigest()[:16],
            "说明": "只用于扫描；要取证据或给坐标动作绑 capture_id 请重新调一次不带 look 的"}


def _perceive_inproc(mode: str, hwnd, limit: int, why: str, look: bool = False):
    """进程内走感知。返回 env 或 None（None = 请回退子进程）。"""
    # ★ 输出是纯 JSON，不带那一行授权回显。
    #   老子进程的 stdout 是「授权成功那行 + JSON」，而 _issue_capture_from_shot 对整个 out 做 json.loads()
    #   → 解析失败 → 窗口截图的 capture_id 一直拿不到（异常被吞，回落成「图像哈希留空」）。
    #   进程内直接给纯 JSON，这个长期隐性缺陷一并修掉。
    p = _load_perceive()
    if p is None:
        return None
    try:
        got = gate.require_any(gate.PERCEIVE, why or "ctl_perceive", count=False)
    except gate.GateDenied as e:
        return env(code=gate.CODE_UNAUTH, data={"raw": str(e)[:400]},
                   warning="未获授权 —— 请先 ctl_authorize 拿通行证")
    res = {}
    try:
        if mode == "windows":
            ws = p.windows()
            ws.sort(key=lambda x: (not x["foreground"], x["minimized"], -x["size"][0]))
            res["窗口数"] = len(ws)
            res["窗口"] = ws[:limit]
        elif mode == "shot":
            if look:
                # look 模式：**不写文件**，只在内存里算一个指纹。
                # PNG 无损编码 2560x1600 要 191 ms（实测），而截图本身只要 46 ms。
                # 那 191 ms 是**证据的代价**，不是感知的代价：扫描时先看，决定要动了再留证据。
                res["截图"] = _look_only(p)
            else:
                res["截图"] = p.shot(hwnd)
        elif mode == "screen":
            res["屏幕"] = p.screen_info()
        else:
            return None                     # tree 输出格式特殊，仍走子进程
    except Exception as e:
        return env(code=gate.CODE_NO_CTRL, warning="进程内感知失败 %s: %s" % (type(e).__name__, e))
    out = json.dumps(res, ensure_ascii=False, indent=1)
    d = {"rc": 0, "raw": out[:4000], "inproc": True, "gate": got}
    # ★ T12：截断**必须可见**。原先静默砍断，客户端拿到半截 JSON 却毫无信号 ——
    #   那不是信息少，是信息假（会以为「窗口就这几个，我看完的就是全部」）。
    #   措辞与格式对齐 uia_tree.py:370-371 的既有做法，保持全仓一致。
    if len(out) > 4000:
        d["截断"] = True
        d["raw_full_len"] = len(out)
        d["截断说明"] = ("… 还有更多内容，被 4000 字符上限截断了"
                      "—— 要全的自己调 limit 或换更窄的查询")
    if hwnd and mode == "shot":
        # ★ T15：mode=="shot" ⇒ out 是**子进程 stdout 字符串** ⇒ 走截图版
        tok = _issue_capture_from_shot(int(hwnd), out)
        if tok:
            d["capture_id"] = tok
            d["capture_ttl_sec"] = gate.CAPTURE_TTL
    return env(ok=True, code=0, data=d)


def _perceive(args: dict, extra: list[str]) -> dict:
    # ★ 2026-10-07：优先**进程内**走（~25-40 ms），失败才回退子进程（~315 ms）。
    #   为什么解析 extra：三个调用点都是传 argv 形式，不想为了快而把它们的接口改掉；
    #   而乐意改接口就要一同改三处。保留 argv 形式后，子进程回退路径一字不变。
    mode = "screen"
    hwnd = None
    limit = 40
    for i, a in enumerate(extra):
        if a == "--windows":
            mode = "windows"
        elif a == "--shot":
            mode = "shot"
        elif a in ("--hwnd", "--tree") and i + 1 < len(extra):
            try:
                hwnd = int(extra[i + 1])
            except ValueError:
                hwnd = None
            if a == "--tree":
                mode = "tree"
        elif a == "--limit" and i + 1 < len(extra):
            try:
                limit = int(extra[i + 1])
            except ValueError:
                limit = 40
    if mode != "tree":
        r = _perceive_inproc(mode, hwnd, limit, args.get("why") or "ctl_perceive", bool(args.get("look")))
        if r is not None:
            if r.get("ok"):
                _ns, _ns_ok = _notices()
                if _ns_ok:
                    r["notices"] = _ns
            return r
    rc, out, err = _legacy("perceive.py", extra + ["--why", args.get("why") or "ctl_perceive"])
    if rc == 9:
        return env(code=gate.CODE_UNAUTH, data={"raw": out.strip()[:400]},
                   warning="未获授权 —— 请先 ctl_authorize 拿通行证")
    if rc == 127:
        # ⚠️ 别把"脚本不在"混成"没授权"：首版验收就是栽在这个映射上（报 code=4 而非 9）
        return env(code=gate.CODE_NO_CTRL, data={"raw": err.strip()[:300]},
                   warning=f"找不到老脚本（LEGACY={LEGACY}）—— 环境没配好，不是授权问题")
    if rc == 124:
        return env(code=gate.CODE_NO_CTRL, data={"rc": rc}, warning="老脚本超时")
    if rc != 0:
        return env(code=gate.CODE_NO_CTRL, data={"rc": rc, "raw": (out + err).strip()[:400]},
                   warning=f"老脚本返回码 {rc}")
    _o = out.strip()
    data = {"rc": rc, "raw": _o[:4000]}
    # ★ T12：同上，回退分支也必须让截断可见。
    if len(_o) > 4000:
        data["截断"] = True
        data["raw_full_len"] = len(_o)
        data["截断说明"] = ("… 还有更多内容，被 4000 字符上限截断了"
                          "—— 要全的自己调 limit 或换更窄的查询")
    # ★ 签发 capture_id —— 只读工具也签，这样后续的坐标动作才有得绑（§1.4）
    hwnd = args.get("hwnd")
    if hwnd:
        # ★ T15：这里 out 同样是**子进程 stdout 字符串** ⇒ 走截图版
        tok = _issue_capture_from_shot(int(hwnd), out)
        if tok:
            data["capture_id"] = tok
            data["capture_ttl_sec"] = gate.CAPTURE_TTL
    return env(ok=True, code=0, data=data)


def t_windows(args: dict) -> dict:
    return _perceive(args, ["--windows", "--limit", str(int(args.get("limit") or 40))])


def t_shot(args: dict) -> dict:
    extra = ["--shot"]
    if args.get("hwnd"):
        extra += ["--hwnd", str(int(args["hwnd"]))]
    return _perceive(args, extra)


def t_tree(args: dict) -> dict:
    """**不截图的识别通道**（2026-10-07 重写，走 uia_tree 而不是子进程）。

    老实现慢在两处：spawn 子进程 ~315 ms，加上每个节点穷问 8 个 Pattern
    （787 节点 × 8 = 6000+ 次 COM 往返）。新实现进程内，且 Pattern 只在 detail 时问。

    ★ 返回**紧凑文本树**，不是 JSON 字典 —— 每节点 9 个字段 × 787 个节点
      就是几万 token，刷屏等于没信息。
    ★ 每个可操作元素带 eid（e0、e1...），下一步用 ctl_click 的 eid 直接点，
      不用自己算坐标 —— 坐标算错就是点错按钮。
    """
    return _uia_gate(args, "ctl_tree", lambda h: uia_tree.tree(
        h,
        max_nodes=int(args.get("max_nodes") or uia_tree.DEFAULT_MAX_NODES),
        max_depth=int(args.get("max_depth") or uia_tree.DEFAULT_MAX_DEPTH),
        detail=bool(args.get("detail")),
        roles=tuple(args.get("roles") or ("interactive", "text")),
    ))


def t_find(args: dict) -> dict:
    """按文字/类型/自动化 id 找元素。**先想清楚要点什么就用这个，别拉全树。**

    实测本机 Edge：find("订阅") → 扫 534 节点、命中 7、558 ms，带 eid 和坐标。
    比拉全树省一个数量级的输出。
    """
    return _uia_gate(args, "ctl_find", lambda h: uia_tree.find(
        h, args.get("query") or "",
        max_nodes=int(args.get("max_nodes") or uia_tree.FIND_MAX_NODES),
        limit=int(args.get("limit") or 40),
        detail=bool(args.get("detail")),
    ))


def _uia_gate(args: dict, tool: str, fn):
    """UIA 读取走 A 级闸门（只读感知），进程内，不 spawn。"""
    h = int(args.get("hwnd") or 0)
    if not h:
        return env(code=gate.CODE_NO_CTRL, warning=tool + " 缺 hwnd")
    try:
        gate.require_any(gate.PERCEIVE, tool + " 读 UI 元素树", count=False)
    except gate.GateDenied as e:
        return env(code=gate.CODE_UNAUTH, warning="未获授权 —— " + str(e))
    try:
        st = uia_tree.window_state(h)
        bad = st["最小化"] or st["根矩形宽"] <= 0 or st["根矩形高"] <= 0
        if bad and not args.get("force"):
            return env(code=gate.CODE_NO_CTRL,
                       data={"窗口状态": st},
                       warning=tool + " 读不了这个窗口：" + str(st["说明"]),
                       blind_spots=["拿不到元素树时**不能**据此说界面上没有这个东西 —— "
                                    "换个窗口状态（叫醒/切前台）再试，或退回 ctl_shot"])
        res = fn(h)
    except uia_tree.UiaUnavailable as e:
        return env(code=gate.CODE_NO_CTRL, warning=str(e),
                   blind_spots=["UIA 不可用 ≠ 界面上没有；Canvas/游戏/Electron 本来就瞎"])
    except Exception as e:
        return env(code=gate.CODE_NO_CTRL,
                   warning="读元素树失败 %s: %s" % (type(e).__name__, e))
    res["窗口状态"] = st
    d = {"结果": res}
    # 元素树也是一次观测 —— 而且是比截图**更严**的观测。签发凭据，
    # 这样走坐标的点击照样受「必须绑定最近一次观测」这条规矩管。
    # ★ T15：res 是 **dict**（uia_tree.tree/find 的返回值）⇒ 走元素指纹版
    tok = _issue_capture_from_tree(h, res)
    if tok:
        d["capture_id"] = tok
        d["capture_ttl_sec"] = gate.CAPTURE_TTL
        # ★ 2026-10-10 名副实（Codex 实测抓到「节点: [] 两个指纹都空还自称元素树」）：
        #   默认轻量模式不采集节点 ⇒ 没有元素指纹可算（凭据只绑定 hwnd/rect/前台）。
        #   改前这里一律写「元素树（不是图像）」，冒领了没采集到的东西。照实说。
        if res.get("节点") or res.get("命中"):
            d["capture_指纹"] = "元素树（不是图像）"
        else:
            d["capture_指纹"] = ("本次未采集节点（默认轻量模式），元素指纹不可用 —— "
                                 "凭据只绑定窗口/位置/前台；要元素指纹请带 --detail 重新采集")
    return env(ok=True, code=0, data=d)


def t_replay(args: dict) -> dict:
    """出片：把某段时间（或某次动作前后）的帧导成 clip.avi ＋ 联络表 ＋ manifest。

    ★ 这个工具也是**打包的关键**：`clip` 模块只有被真正 import 才会被 PyInstaller 收进包，
      光靠 --hidden-import 兜是治标。冻结自检第一版就是栽在这
      （"cannot import name 'clip' from 'agentctl'"）。
    """
    import datetime as _d
    from agentctl import clip as _clip                      # noqa: F401（真用，不是为了打包）

    fps = max(1, int(args.get("fps") or 8))
    pad = int(args.get("pad_sec") or 5)
    since = until = None

    if args.get("around_event"):
        evs = [e for e in store().events(limit=5000) if e["seq"] == int(args["around_event"])]
        if not evs:
            return env(code=gate.CODE_NO_CTRL, warning=f"找不到事件 {args['around_event']}")
        t0 = evs[0]["t_wall"]
        try:
            base = _d.datetime.fromisoformat(t0.replace("Z", "+00:00"))
        except Exception:
            return env(code=gate.CODE_NO_CTRL, warning=f"事件时间无法解析：{t0}")
        since = (base - _d.timedelta(seconds=pad)).isoformat(timespec="milliseconds")
        until = (base + _d.timedelta(seconds=pad)).isoformat(timespec="milliseconds")
    else:
        since, until = args.get("from"), args.get("to")

    fr = store().frames(limit=5000, since_wall=since, until_wall=until)
    if not fr:
        return env(code=gate.CODE_NO_CTRL,
                   warning="这段时间没有帧（可能没开录制，或该区间没有采集）",
                   blind_spots=["查询区间内无帧 —— 不能据此说『那段时间什么都没发生』"])
    items = []
    for f in fr:
        p = store().frame_abs(f["path"])
        if os.path.exists(p):
            items.append({"path": p, "frame_id": f["seq"], "t_wall": f["t_wall"]})
    if not items:
        return env(code=gate.CODE_NO_CTRL, warning="帧记录存在但文件都不在了（被留存策略清理过？）")

    out_dir = args.get("out_dir") or os.path.join(TIMELINE_DIR, "clips", f"clip_{_d.datetime.now():%Y%m%d_%H%M%S}")
    try:
        res = _clip.build_clip(out_dir, items, fps=fps, title=args.get("title") or "clip")
    except Exception as e:
        return env(code=gate.CODE_NO_CTRL, warning=f"出片失败 {type(e).__name__}: {e}")
    return env(ok=True, code=0, data={
        "clip": res["avi"]["path"], "clip_bytes": res["avi"]["bytes"],
        "sheet": res["sheet"]["path"], "manifest": res["manifest_path"],
        "frame_count": len(items), "duration_sec": res["avi"]["duration_sec"], "fps": fps,
        "window": {"from": since, "to": until},
    })


def t_authorize(args: dict) -> dict:
    r"""请求用户授权 —— **拉起独立的 tkinter 弹窗**，由用户亲手输密钥。

    铁律 11 的落地方式（一字不改）：
      · 密钥由用户在**独立弹窗进程**里输入，**本 agent 物理上接触不到明文**；
      · 本函数只拿到"通过／不通过"的结论；
      · 弹窗进程是既有的 `tools\auth_popup.py`（已验证的路径，不重写）；
      · 范围用 A/B/C/D 短代号，由这里翻译成闸门的完整串。

    ⚠️ 这条会**阻塞等人**（弹窗不点就一直等）→ 调用方应给足够超时，或放后台跑。
    """
    raw = args.get("scopes") or ["A"]
    if isinstance(raw, list):
        raw = ",".join(str(x) for x in raw)
    # ★ 2026-10-06：用统一的 parse_scopes 解析，别在这里自己拼 ★
    #   用户问「能不能直接输 ABCD」——之前这里只走 gate.short()，
    #   连写 'ABCD' 会被原样透传成一个**永远匹配不上的非法范围**，
    #   而非法范围不报错、只是一直拒绝 —— 那种失败最难查。
    #   parse_scopes 会把 ABCD 展开成 A,B,C,D，解析不了则明确报错。
    try:
        keys = gate.parse_scopes(str(raw))
    except ValueError as e:
        return env(code=gate.CODE_UNAUTH, warning="范围解析失败：%s" % e)
    scopes = keys
    full = [gate.SCOPES[k] for k in keys]
    # ⚠️ 必须用**半角逗号**拼接：auth_popup.py 是按 "," 切分范围的（见其 line 152）。
    #    我原来写成 "、"（顿号），结果两个范围被当成**一个不存在的范围名**存进通行证 ——
    #    证是真的、期是真的，但任何检查都报"范围不符"，表现就是**用户点了授权也没用**。
    scope_str = ",".join(full)
    minutes = int(args.get("minutes") or 10)
    acts = int(args.get("acts") or gate.ACT_LIMIT.get(gate.short(scopes[0]), 20))
    reason = args.get("reason") or ""
    timeout = int(args.get("timeout_sec") or 180)

    cmd = _py_argv(os.path.join(LEGACY, "auth_popup.py"),
                   ["--request", scope_str, "--minutes", str(minutes), "--acts", str(acts)])
    _ev("authorize_request", actor=args.get("actor", ""), tool="ctl_authorize",
        payload={"scopes": scopes, "minutes": minutes, "acts": acts, "reason": reason},
        ok=True, code=0)
    # ★ 同时写闸门日志（2026-10-07）★
    #   为什么：事件流（timeline.db）远程展示给 agent 看，
    #   面板的「最近动作」只读闸门日志（gate_agentctl.log）。两边都不写 = 用户在面板上点了授权，
    #   面板却像没发生过任何事——这就是 B2 那类「两个真相源没串起来」。
    gate.log("授权请求：%s（%d 分钟 / %d 次）" % (",".join(scopes), minutes, acts))
    rc, out, err = _legacy_cmd(cmd, timeout=timeout)
    granted = (rc == 0)
    if granted:
        _ev("authorize_granted", actor=args.get("actor", ""), tool="ctl_authorize",
            payload={"scopes": scopes}, ok=True, code=0)
        gate.log("通行证已放行：%s，有效至 %s（%d 分钟 / %d 次）"
                 % (scope_str, args.get("_expires") or "见面板", minutes, acts))
        return env(ok=True, code=0, data={"granted": True, "scopes": full,
                                          "minutes": minutes, "acts_limit": acts,
                                          "raw": out.strip()[:300]})
    _ev("authorize_denied", actor=args.get("actor", ""), tool="ctl_authorize",
        payload={"scopes": scopes}, ok=False, code=gate.CODE_UNAUTH)
    gate.log("授权未通过：%s（用户拒绝、超时未操作、或密钥错误）" % scope_str)
    return env(code=gate.CODE_UNAUTH,
               data={"granted": False, "scopes": full, "rc": rc, "raw": (out + err).strip()[:300]},
               warning="授权未通过（用户拒绝、超时未操作、或密钥错误）")


def _legacy_cmd(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """跑任意命令（用于拉起弹窗这类非"脚本+参数"的场景）。"""
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=LEGACY,
                           env=_legacy_env())
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"超时（{timeout}s）——用户可能没在弹窗上操作"
    except Exception as e:
        return 125, "", f"{type(e).__name__}: {e}"


# ────────────────────────── 录制（ctl_record）──────────────────────────
# ★ 这一块同时解决两件事：
#   ① 补上"尚未实现"的 ctl_record（服务原本无法开录制）；
#   ② 让 recorder 模块**静态可达** —— 否则 PyInstaller 收不进包
#      （`clip` 就是这么漏掉的：没人 import 就不被收集）。
_REC = {"obj": None, "thread": None, "stop": None, "paused": False,
        "autostop": None, "started": None}


def _mem_snapshot() -> dict:
    import ctypes
    class MSX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MSX(); m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    total = m.ullTotalPageFile / 1024**2
    used = (m.ullTotalPageFile - m.ullAvailPageFile) / 1024**2
    return {"avail_phys_mb": round(m.ullAvailPhys / 1024**2, 1),
            "commit_pct": round(100 * used / max(1.0, total), 1)}


def t_record(args: dict) -> dict:
    """录制开关。★ 与 guardian 联动：可用物理 < 3500 MB 或提交使用率 > 65% → **自动停录**并留痕。"""
    import threading
    from agentctl import recorder as _rec                 # 副作用 import：让 recorder 对 PyInstaller 静态可达（见上方注释②），运行期不直接引用，勿删

    action = (args.get("action") or "status").lower()
    profile = args.get("profile") or "default"
    r = _REC["obj"]

    if action == "status":
        mem = _mem_snapshot()
        st = r.status() if r else {"profile": None}
        st.update({"running": bool(_REC["thread"] and _REC["thread"].is_alive()),
                   "paused": _REC["paused"], "autostop_reason": _REC["autostop"],
                   "started": _REC["started"], "mem": mem,
                   "thresholds": {"avail_phys_mb": 3500, "commit_pct": 65}})
        return env(ok=True, code=0, data=st)

    if action in ("start", "resume"):
        if action == "start" and not r:
            _REC["obj"] = r = _Recorder(store(), TIMELINE_DIR, profile=profile)
        if not r:
            return env(code=gate.CODE_NO_CTRL, warning="还没开过录制，无法 resume")
        _REC["paused"] = False
        if _REC["thread"] and _REC["thread"].is_alive():
            return env(ok=True, code=0, data={"resumed": True, "profile": r.profile})
        stop = threading.Event()
        _REC["stop"] = stop

        def loop():
            while not stop.is_set():
                if _REC["paused"]:
                    time.sleep(0.5)
                    continue
                m = _mem_snapshot()
                if m["avail_phys_mb"] < 3500 or m["commit_pct"] > 65:
                    _REC["autostop"] = f"资源保护：可用物理 {m['avail_phys_mb']} MB / 提交 {m['commit_pct']}%"
                    try:
                        store().append("recorder_autostop", actor="recorder", tool="ctl_record",
                                       payload=_REC["autostop"], ok=False, code=13)
                    except Exception:
                        pass
                    break
                try:
                    r.tick()
                except Exception as e:
                    _REC["autostop"] = f"{type(e).__name__}: {str(e)[:80]}"
                    try:
                        store().append("recorder_autostop", actor="recorder", tool="ctl_record",
                                       payload=_REC["autostop"], ok=False, code=gate.CODE_UNAUTH)
                    except Exception:
                        pass
                    break
                time.sleep(1.0)

        th = threading.Thread(target=loop, name="agentctl-recorder", daemon=True)
        _REC["thread"] = th
        _REC["autostop"] = None
        _REC["started"] = time.time()
        th.start()
        _ev("record_start", actor=args.get("actor", ""), tool="ctl_record",
            payload={"profile": r.profile}, ok=True, code=0)
        return env(ok=True, code=0, data={"recording": True, "profile": r.profile,
                                          "params": r.params()})

    if action == "pause":
        _REC["paused"] = True
        _ev("record_pause", actor=args.get("actor", ""), tool="ctl_record", payload={})
        return env(ok=True, code=0, data={"paused": True})

    if action == "stop":
        if _REC["stop"]:
            _REC["stop"].set()
        th = _REC["thread"]
        if th:
            th.join(timeout=5)
        sess = r.end_session() if r else {}
        _REC["thread"] = None
        _ev("record_stop", actor=args.get("actor", ""), tool="ctl_record", payload=sess)
        return env(ok=True, code=0, data={"recording": False, "session": sess,
                                          "autostop_reason": _REC["autostop"]})

    return env(code=gate.CODE_NO_CTRL, warning=f"未知 action: {action}")


def _Recorder(*a, **kw):
    from agentctl.recorder import Recorder
    return Recorder(*a, **kw)


# ────────────────────── 写动作的公共前置：租约 ＋ 观测绑定 ──────────────────────
# ★ 这两条是**服务层新增**的（老 act.py 里没有），依据基线文档 §1.4 / §15.3：
#   ① 写动作必须持租约（防同一个 agent 自己的并发调用互踩）；
#   ② **走坐标的动作必须绑定最近一次观测**（治本机头号事故"拿旧截图点新画面"）。

def _win_rect(hwnd: int):
    """物理像素口径的窗口矩形。优先用老 coord.py（它导入即设 DPI 感知），退回 win32gui。"""
    try:
        if LEGACY not in sys.path:
            sys.path.insert(0, LEGACY)
        import coord                                   # noqa: PLC0415
        return list(coord.win_rect(int(hwnd)))
    except Exception:
        try:
            import ctypes                                  # noqa: PLC0415
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
            import win32gui                                # noqa: PLC0415
            return list(win32gui.GetWindowRect(int(hwnd)))
        except Exception:
            return None


def _is_foreground(hwnd: int):
    try:
        import win32gui                                    # noqa: PLC0415
        return win32gui.GetForegroundWindow() == int(hwnd)
    except Exception:
        return None


def _require_write(actor: str) -> dict | None:
    """写动作前置①：必须有本人持有的有效租约。**成功返回 None**，失败返回错误信封。

    ⚠️ 2026-10-02 验收抓到的 bug：原来成功时 `return gate.require_lease(...)` ——
       那返回的是**租约字典**（真值），而调用方写的是 `if err: return err`，
       于是**把租约字典当成结果返回了**，后面真正调 act.py 的代码根本没走到
       （表现：ok=None / code=None，且命令行映射测试也抓不到 act.py 调用）。
       修：成功**必须显式返回 None** —— "返回 None 表示没问题"这种约定最容易写错，
       所以这里写死并留注释。
    """
    if os.environ.get("AGENTCTL_REQUIRE_LEASE", "1") == "0":
        return None
    try:
        gate.require_lease(actor or "unknown")       # ← 不返回它，只是校验
        return None
    except gate.GateDenied as e:
        # ★ T12：actor 为空时**说清缺的是什么**并给可复制命令。
        #   原样拼出来是「（actor=）」空值，实测完全看不出该传什么。
        #   ⚠ 判定逻辑一个字没动 —— actor 空仍然拒绝（fail-closed 不变）。
        if not actor:
            _w = ("%s —— 写动作必须先 ctl_lease acquire；且本次调用**没带 actor**，"
                  "请加 --actor <你的名字>（或设 AGENTCTL_ACTOR 环境变量），"
                  "例：ctl_click --actor <你的名字> --hwnd X --xy <x>,<y>" % e.why)
        else:
            _w = "%s —— 写动作必须先 ctl_lease acquire（actor=%s）" % (e.why, actor)
        return env(code=e.code, warning=_w)


def _bind_capture(args: dict, hwnd: int) -> dict | None:
    """写动作前置②：**走坐标就必须带 from_capture**，并校验窗口没动、前台没变、没过期。

    返回错误信封或 None。校验通过后**不消费** —— 由调用方在动作成功后消费
    （失败不消费，便于重试；这条是刻意的）。
    """
    xy = args.get("xy")
    if not xy:
        return None
    # ★ T12：收参两个名字都认 —— 返回字段叫 capture_id，收参却叫 from_capture，
    #   照字面传 --capture_id 会**静默失效**（code=12），实测踩过两次。
    #   ★ 两者都要过 verify_capture 的窗口/前台/TTL 三重校验，多认不放宽任何约束。
    tok = args.get("from_capture") or args.get("capture_id")
    if not tok:
        return env(code=gate.CODE_STALE_CAPTURE,
                   warning="走坐标的动作必须带 from_capture（先 ctl_shot 或 ctl_tree 拿 capture_id）"
                           "—— 见《AGENT接口.md》§3：这台机器的头号事故就是拿旧截图点新画面")
    rect = _win_rect(hwnd)
    fg = _is_foreground(hwnd)
    img = None
    if args.get("verify_image"):
        try:
            import mss                                       # noqa: PLC0415
            import hashlib                                   # noqa: PLC0415
            r = rect
            with mss.mss() as sct:
                shot = sct.grab({"left": r[0], "top": r[1], "width": r[2] - r[0], "height": r[3] - r[1]})
                img = hashlib.sha256(shot.rgb).hexdigest()
        except Exception:
            img = None
    try:
        gate.check_capture(tok, hwnd=hwnd, rect=rect, foreground=fg, image_sha256=img)
    except gate.GateDenied as e:
        return env(code=e.code, warning=f"{e.why} —— 重新 ctl_shot 拿新的 capture_id 再动手")
    return None


def _modal_reason(txt: str) -> str:
    """从老脚本 rc=6 的输出里拉出「到底被什么挡着」那一句。

    act.py `require_no_modal` 打的那行长这样（act.py:810）：
      ⛔ 拒绝点击：目标进程上有可见对话框：类名=#32770 标题=「保存」hwnd=12345（共 1 个）（模态对话框会吞掉动作，先关掉它）
    真原因就在「⛔ 拒绝xx：」与尾巴那句固定叮嘱之间。改前它只埋在 data.raw 里，
    agent 得自己刨——刨不出来就等于没告诉它为什么失败。
    拉不出来时返回空串：调用方照常给话，`data.raw` 仍是完整现场。
    """
    for ln in (txt or "").splitlines():
        ln = ln.strip()
        if not ln.startswith("⛔") or "：" not in ln:
            continue
        body = ln.split("：", 1)[1].rsplit("（模态", 1)[0].strip()
        if body:
            return body
    return ""


def _map_rc(rc: int, out: str, err: str) -> dict:
    """把老脚本的返回码映射成契约错误码。

    ★ 2026-10-07 新增 rc=7：不是拒绝，是「这个窗口用户还没点头，去问一声」。
      用户指示「不整名单限制，每次都问」—— 静态 whitelist.json 废掉了，
      改成第一次碰某个窗口时弹窗问，同意后本次任务内放行。

    ★ C4 缺口 A（2026-10-12）新增 rc=6：老脚本 act.py 的 `require_no_modal` 判定
      「这个窗口上有模态对话框挡着」（act.py:811 是 rc=6 在整个仓里的**唯一**出处，
      已逐句核对过）。改前它不在表里 ⇒ 掉进兜底变成 code=4「找不到控件」，
      而《AGENT接口.md》§2.1 对 code=4 开的处方是「重新 ctl_windows + ctl_tree 看现状」
      —— 对话框根本不在控件树里，agent 照着走只会绕圈。
      ⇒ 落成 **code=5**（现有码；语义「坐标/前台/窗口不稳定 → 重新观测」，
      与「目标窗口当前状态不稳定」对得上），**不新造错误码**。
      改前改后都是 ok=False，零放行口径变化。
    """
    txt = (out + err).strip()
    if rc == 0:
        return env(ok=True, code=0, data={"rc": rc, "raw": out.strip()[:3000]})
    m = {3: gate.CODE_DENY_WHITELIST, 7: gate.CODE_NEEDS_ASK, 9: gate.CODE_UNAUTH,
         4: gate.CODE_NO_CTRL,
         # ← C4：rc=6 是 act.py 的「被模态对话框挡住」，落 code=5；不再兜底成「找不到控件」
         5: gate.CODE_UNSTABLE, 6: gate.CODE_UNSTABLE,
         8: gate.CODE_NO_CHANGE, 127: gate.CODE_NO_CTRL}
    code = m.get(rc, gate.CODE_NO_CTRL)
    warn = {8: "动作发出去了，但页面/画面没有变化 —— ⛔ **不许当成功**",
            9: "未获授权 —— 先 ctl_authorize 拿通行证",
            3: "白名单/敏感窗口拒绝",
            7: "这个窗口用户还没点头 —— 需要先在弹窗里确认",
            5: "坐标/前台/窗口稳定性拒绝"}.get(code, f"老脚本返回码 {rc}")
    if rc == 127:
        warn = f"找不到老脚本（LEGACY={LEGACY}）"
    if rc == 6:
        # ★ C4：上面那张表是**按 code 取**的，而 rc=5 与 rc=6 现在共用 CODE_UNSTABLE ——
        #   不在这里按 rc 再覆盖一次，模态被挡就会显示成「坐标/前台/窗口稳定性拒绝」，
        #   与改前的「老脚本返回码 6」一样等于什么都没说。
        #   这一层只给**不依赖具体文本**的短版（_ask_then_retry 也走这里）；
        #   主写路径 _act 会再用 act.py 的原文把「到底被什么挡着」补全。
        warn = ("目标窗口被模态对话框挡住，动作**没有发出来**（老脚本 rc=6）"
                " —— 先关掉那个对话框（模态框会吞掉一切点击/输入）再重试")
    _data = {"rc": rc, "raw": txt[:3000]}
    if rc == 6:
        # blocked_by 让调用方能**机读地**区分「被模态挡住」与 code=5 的其它两种情形
        # （坐标类 / 快照类）—— 它们共用 code=5，只有这个标记能把它们分开。
        _data["blocked_by"] = "modal_dialog"
    return env(code=code, data=_data, warning=warn)


# ────────────── T17 闭环：动作的独立验证（2026-10-12）──────────────
# 为什么：GUI 动作**没有返回值**，「接口回执说成功」不等于「事情发生了」。
# 本项目实测过：对 Chromium 窗口后台点击，接口返回成功、**控件零反应**。
# 所以调用方可以**在动作【前】**声明一个机器可判的期望（谓词与 legacy_tools/验动作.py 同一套），
# 由 _act 在动作前后各拍一次快照、独立判定；判定不过就**不报 ok**。
# ★ 只以子进程调脚本、**不 import**：验动作.py 模块级会在缺 psutil 时 sys.exit(3)，
#   import 它会连累本进程一起退出。子进程也保持"判定逻辑唯一定义处"。
# ★ C3（2026-10-12）：判定器**只可能在 legacy_tools\**。
#   它的真源是 `dsh-viewer-plugin\tools\`，产物由 gen_legacy 落到 legacy_tools\。
#   原来这里同时找 tools\ 和 legacy_tools\ 两处，是因为 tools\ 还留着第二份 tracked 副本；
#   那份已按 C3 删除 —— 第二真源正是本项目反复出病的根，留着它迟早又会被改坏而没人回写真源。
#   ⇒ 收口后只该有一边，故砍掉 tools\ 分支。gen_legacy 的「反平行副本检查」是配套的主防线。
VERIFY_REL = os.path.join("legacy_tools", "验动作.py")


def _verify_py():
    """定位判定器。AGENTCTL_VERIFY_PY 优先，其次仓库根 / _MEIPASS / src 上一级。"""
    for p in (os.environ.get("AGENTCTL_VERIFY_PY"),
              os.path.join(ROOT, VERIFY_REL),
              os.path.join(getattr(sys, "_MEIPASS", ROOT), VERIFY_REL),
              os.path.join(BASE, VERIFY_REL)):
        if p and os.path.exists(p):
            return p
    return None


def _cl_run(py: str, argv: list, timeout: int = 300):
    """跑一次判定器，返回 (rc, 合并输出)。判定口径由脚本自己负责，这里不解释。"""
    p = subprocess.run([sys.executable, "-X", "utf8", py] + list(argv),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _act(action: str, args: dict, argv: list[str], scope: str = "B") -> dict:
    """统一：**先验证通行证（非消耗）→ 再验租约 → 再验观测绑定 → 调 act.py → 成功后消费 capture_id**。

    ★ 顺序很讲究：**先报"要通行证"再报"要租约"**。
      反过来的话，agent 会先被叫去申请租约、拿到租约后再被告知"没通行证"—— 白跑两趟。
      通行证是用户签的、更根本，所以先验它。（旧的 act.py 仍然会在执行时再验一次，双保险。）
    """
    actor = args.get("actor", "")
    try:
        ok, why, code = gate.check(scope)
        if not ok:
            return env(code=code, warning=f"{why} —— 写动作需要「{scope}」级通行证，先 ctl_authorize")
    except gate.GateDenied as e:
        return env(code=e.code, warning=e.why)
    err = _require_write(actor)
    if err:
        return err
    hwnd = int(args.get("hwnd") or 0)
    if not hwnd:
        return env(code=gate.CODE_NO_CTRL, warning="缺 hwnd")
    err = _bind_capture(args, hwnd)
    if err:
        return err

    # ★ T17 闭环前半（2026-10-12）：声明了 verify_expect ⇒ 先确认判定器在，再拍「前」快照。
    #   必须在**动作之前**拍 —— 事后补拍无法归因（这是 T3 换来的教训）。
    #   判定器缺失时【拒绝执行】而不是静默跳过：声明了要验、却验不了，不算完成。
    #   ★ 键名为什么带 verify_ 前缀：`expect` 这个键 **t_click 早已占用**，含义是
    #     「你看到的元素名字」（点 eid 前核对名字变没变）。同名不同义会造成静默误判 ——
    #     实测过：拿 expect 当谓词传进去，动作被当成"元素改名了"直接拒绝不点。
    exp = (args.get("verify_expect") or "").strip()
    watch = (args.get("verify_watch") or "").strip()
    _cl = None
    if exp:
        _vy = _verify_py()
        if not _vy:
            return env(code=gate.CODE_UNSTABLE,
                       warning="声明了 verify_expect，但找不到判定器 legacy_tools/验动作.py —— "
                               "无法独立验证就不能算完成（fail-closed）。"
                               "可用环境变量 AGENTCTL_VERIFY_PY 指定路径，或去掉 verify_expect。")
        _tagbase = "cl_%d_%d" % (os.getpid(), time.time_ns())
        _cl = {"py": _vy, "before": _tagbase + "_b", "after": _tagbase + "_a"}
        _rcv, _outv = _cl_run(_vy, ["snap", "--tag", _cl["before"],
                                    "--paths", watch, "--expect", exp])
        if _rcv != 0:
            return env(code=gate.CODE_UNSTABLE, data={"raw": _outv[-800:]},
                       warning="动作【前】快照失败 ⇒ 拒绝执行（没拍到「前」，事后无法归因）")

    rc, out, e = _legacy("act.py", argv, timeout=int(args.get("timeout_sec") or ACT_TIMEOUT_SEC))
    r = _map_rc(rc, out, e)

    # ★ C4 缺口 A（2026-10-12）：把 rc=6 的**真原因**提到台面上。
    #   改前这条路径给的是 code=4「找不到控件」+ 一句「老脚本返回码 6」，
    #   真原因埋在 data.raw 里没人看 ⇒ 动作确实被 act.py 挡住了，用户/agent 却拿到错误处方。
    #   分工：_map_rc 负责「翻成哪个码」，这里负责「说清为什么、下一步做什么」。
    #   ⚠ 这里**不加任何探测**：是不是有模态，判定权仍然只在 act.py 的 require_no_modal 一处
    #     （本项目纪律：判定逻辑唯一定义处）。本段只翻译它已经给出的结论。
    #   ⚠ 位置为什么在 _legacy **之后**：信号来自子进程的返回码，_bind_capture 与 _legacy
    #     之间若再插一道检查，那就是第二道护栏 —— 明令禁止，且会与 act.py 判定打架。
    if rc == 6:
        _why = _modal_reason((out or "") + (e or ""))
        r["warning"] = ("目标窗口被模态对话框挡住，动作**没有发出去**%s"
                        "—— 先让用户关掉那个对话框（模态框会吞掉一切点击/输入），"
                        "关掉后重新 ctl_shot 拿新的 capture_id 再动手。"
                        "⛔ 这**不是**「找不到控件」，别去重扫控件树绕圈。"
                        % (("：" + _why) if _why else ""))
        if not isinstance(r.get("data"), dict):
            r["data"] = {}
        r["data"].setdefault("blocked_by", "modal_dialog")

    # ★ rc=7：老脚本说「这个窗口你没点头过」。那就**替你问** ——
    #   用户指示的是「每次都问」，不是「拒绝」，所以这一下必须真的弹窗，
    #   不能把决定权丢回给 agent（agent 自己会给自己点头）。
    #   同意 → 记一次放行（默认 30 分钟）→ 原样重试一次。
    #   ★ 2026-10-10 补（今晚实测踩到）：弹窗必须按**本动作真实需要的档位**请求 ——
    #     原来写死「B级」，type/fill 要的是 C：用户批了弹窗也只签出一张 B 证，
    #     重试必然再吃 rc=9，还把共享的通行证文件覆盖掉（今晚 22:33/22:37 连废三张）。
    if rc == 7 and not args.get("_asked"):
        r2 = _ask_then_retry(args, argv, out, scope=scope)
        if r2 is not None:
            r = r2

    # ★ T12：与 _bind_capture 同步双读。漏了这条，用 capture_id 传入的观测
    #   **永远不会被消费** ⇒ 同一张 capture_id 可被无限次复用（安全性回归）。
    _cap = args.get("from_capture") or args.get("capture_id")
    if r.get("ok") and _cap:
        try:
            gate.consume_capture(_cap)
        except Exception:
            pass

    # ★ T17 闭环后半（2026-10-12）。四条硬约束：
    #   ① 不声明 expect 时，**一个字段都不加** ⇒ 输出与改前逐字节一致（非破坏性）
    #   ② unknown / 快照不可比 都【不算成功】—— 与 验动作.py 同口径
    #   ③ 动作自身的 rc 与判定 verdict **两个都报**，互不掩盖
    #   ④ expect 必须在动作前声明（见上面 _cl）
    if _cl:
        _rcv, _outv = _cl_run(_cl["py"], ["snap", "--tag", _cl["after"], "--paths", watch])
        if _rcv != 0:
            verdict = "unknown"
            _vdetail = {"snap_error": _outv[-500:]}
        else:
            _rcj, _outj = _cl_run(_cl["py"], ["check", "--tag", _cl["after"],
                                              "--before", _cl["before"],
                                              "--action", action, "--tier", scope])
            verdict = {0: "satisfied", 3: "unsatisfied", 4: "unknown",
                       5: "snapshots-incomparable"}.get(_rcj, "unknown")
            _vdetail = {"judge_rc": _rcj, "judge_tail": _outj[-700:]}
        r["verdict"] = verdict
        r["verify"] = {"expect": exp, "watch": watch,
                       "action_ok": bool(r.get("ok")), **_vdetail}
        if r.get("ok") and verdict != "satisfied":
            _code = gate.CODE_NO_CHANGE if verdict == "unsatisfied" else gate.CODE_UNSTABLE
            # ★ 顶层也带上 verdict/verify：成功路径是挂在 r["verdict"] 上的，
            #   失败路径若只放进 data，agent 就得记两个位置 —— 同一件事必须只有一个读法。
            return env(code=_code, verdict=verdict,
                       verify={"expect": exp, "watch": watch, "action_ok": True, **_vdetail},
                       data={"rc": (r.get("data") or {}).get("rc"),
                             "action_ok": True, "verdict": verdict,
                             "expect": exp, **_vdetail},
                       warning="★ 动作执行了，但【独立验证没通过】：verdict=%s。"
                               "两个结果都在（动作 rc 在 data.rc、判定 verdict 在本条）—— 别只看一个。"
                               "expect=%s" % (verdict, exp))
    return r


def _ask_then_retry(args, argv, first_out, scope: str = "B"):
    """弹窗问用户要不要操作这个窗口。同意 → 放行 → 重试一次。

    返回重试后的信封。★ 2026-10-10 P0：超时/拒绝不再静默返回 None ——
    那会让调用方只看到一句含糊的 rc=7「还没点头」，分不清「没弹出来」「没人应答」
    「用户拒绝了」三种处境，也没下一步。现在：
      弹窗根本没弹出来（参数/环境错）      → None（保留原 rc=7，不发明新语义）
      弹窗等满上限没人应答（rc=124）        → code=7 + ask=timeout + waited_sec + NEXT_STEP
      弹窗被关闭/拒绝                       → code=7 + ask=declined + NEXT_STEP
    code 都仍是 CODE_NEEDS_ASK —— 调用方的处理逻辑不变，只是把话说全。
    ★ scope：本动作真实需要的档位（_act 传入），弹窗按它请求；
      args["ask_scope"] 仍可显式覆盖。写死「B级」会让 type/fill 的重试必死（见 _act 注）。
    **最多问一次**（args["_asked"] 防循环）—— 用户拒绝了就不该反复骚扰。
    """
    hwnd = int(args.get("hwnd") or 0)
    scope_txt = args.get("ask_scope") or SCOPES_TXT.get(scope, scope)
    why = ""
    for ln in (first_out or "").splitlines():
        if "要操作窗口" in ln or "不在白名单" in ln:
            why = ln.strip()
            break

    def pop(reason):
        try:
            # 注意：这里**不能**再把脚本名塞进参数里 —— _legacy 自己会加，
            # 多写一次 argparse 会报「unrecognized arguments: auth_popup.py」，
            # 弹窗压根不弹，只留一句看不懂的 stderr。
            argv2 = ["--request", scope_txt,
                     "--minutes", str(int(args.get("ask_minutes") or 30)),
                     "--acts", str(int(args.get("ask_acts") or 20)),
                     "--note", reason]
            return _legacy("auth_popup.py", argv2, timeout=_ask_timeout(args))
        except Exception:
            return None

    pr = pop(why or "要操作一个新窗口")
    if pr is None:
        # 弹窗本身没弹出来 —— 保留原 rc=7 原样，让「弹窗坏了」和「用户没点头」可区分
        return None
    if pr[0] == 124:
        return env(code=gate.CODE_NEEDS_ASK,
                   data={"rc": 7, "ask": "timeout", "waited_sec": _ask_timeout(args),
                         "raw": (first_out or "")[:1500]},
                   warning="弹窗已弹，等了 %ds 没人应答 —— 需人工授权。NEXT_STEP: "
                           "用户回到屏幕前时重试本动作；或由用户在终端跑 auth_popup 线下签证，"
                           "或经用户把该窗口加进白名单" % _ask_timeout(args))
    if pr[0] != 0:
        return env(code=gate.CODE_NEEDS_ASK,
                   data={"rc": 7, "ask": "declined", "popup_rc": pr[0],
                         "raw": ((pr[1] or "") + (pr[2] or ""))[:1500]},
                   warning="弹窗被关闭/拒绝 —— 用户没点头。NEXT_STEP: 不要原样重试同一窗口，"
                           "先跟用户线下确认要不要放行")

    try:
        info = _win_info_for(hwnd)
        _legacy_approve_target(info)
    except Exception:
        return None

    a2 = dict(args)
    a2["_asked"] = 1
    rc, out, e = _legacy("act.py", argv, timeout=int(args.get("timeout_sec") or ACT_TIMEOUT_SEC))
    rr = _map_rc(rc, out, e)
    _cap2 = args.get("from_capture") or args.get("capture_id")
    if rr.get("ok") and _cap2:
        try:
            gate.consume_capture(_cap2)
        except Exception:
            pass
    rr["data"] = {**(rr.get("data") or {}), "ask": "用户已口头点头，本次任务内放行"}
    return rr


def _win_info_for(hwnd: int) -> dict:
    """取窗口的 (标题, 进程, 类名) —— 放行记录和弹窗说明都要用。"""
    try:
        import win32gui                                  # noqa: PLC0415
        import win32process                              # noqa: PLC0415
        import psutil                                    # noqa: PLC0415
        h = int(hwnd)
        tid = win32process.GetWindowThreadProcessId(h)[1]
        return {"title": win32gui.GetWindowText(h) or "",
                "process": psutil.Process(tid).name() if tid else "",
                "class": win32gui.GetClassName(h) or ""}
    except Exception as e:
        return {"title": "", "process": "", "class": "", "error": str(e)}


def _legacy_approve_target(info: dict) -> bool:
    """让老脚本把这个窗口记进「本次任务已放行」。"""
    try:
        rc, out, e = _legacy("act.py", ["approve", "--process", info.get("process") or "",
                                        "--class", info.get("class") or ""],
                             timeout=20)
        return rc == 0
    except Exception:
        return False


def _xy(xy):
    r"""把坐标**归一化**成 [x, y]。接受三种形态：
         [100, 200]        —— Python / JSON 调用
         "100,200"         —— 命令行（★ 就是这个形态被漏测，导致 int(',') 崩）
         "100，200"        —— 中文全角逗号（粘贴时常见）
    ⚠️ 教训：`for v in "100,200"` 遍历的是**字符**，会把 ',' 也拿去 int()。
       凡是"可能来自命令行"的参数，都必须先归一化再迭代。
    """
    if xy is None:
        return None
    if isinstance(xy, (list, tuple)):
        return [int(v) for v in xy]
    parts = [p.strip() for p in str(xy).replace("，", ",").replace(" ", ",").split(",")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        raise ValueError(f"坐标要写成 x,y 两个数，收到的是 {xy!r}")
    return [int(parts[0]), int(parts[1])]


def t_click(args: dict) -> dict:
    hwnd = int(args.get("hwnd") or 0)

    # ★ 2026-10-07 加 eid：**不把 eid 当缓存坐标用**，点之前重新走一遍树解析，
    #   还要核对名字 —— 界面变了就拒绝，而不是照着旧坐标戳下去。
    #   本机头号事故「拿旧截图点新画面」在这里同理有复发机会，就堵在这。
    eid = args.get("eid")
    if eid:
        try:
            st = uia_tree.window_state(hwnd)
        except Exception:
            st = {}
        if st.get("最小化") or not st.get("根矩形宽"):
            return env(code=gate.CODE_NO_CTRL, warning="点 eid 之前窗口不可读（%s），先把窗口叫醒" % st.get("说明", "未知"))
        try:
            nodes, _ = uia_tree.walk(hwnd, roles=("all",))
        except Exception as e:
            return env(code=gate.CODE_NO_CTRL, warning="重新解析 eid 失败 %s: %s" % (type(e).__name__, e))
        hit = None
        for n in nodes:
            if n["eid"] == str(eid):
                hit = n
                break
        if hit is None:
            return env(code=gate.CODE_NO_CTRL, warning="元素 %s 现在不存在了（扫到 %d 个节点）。界面变了，重新 ctl_find 再点。" % (eid, len(nodes)))
        want = args.get("expect")
        if want and str(want).strip() and str(want).strip() not in (hit["name"] or ""):
            return env(code=gate.CODE_NO_CTRL, warning="元素 %s 现在叫『%s』，不是你看到的『%s』—— 不点" % (eid, hit["name"], want))
        if not hit.get("enabled", True):
            return env(code=gate.CODE_NO_CTRL, warning="元素 %s（%s）是禁用状态，不点" % (eid, hit["name"]))
        args = {**args, "xy": list(hit["center"]), "_eid_hit": {"eid": eid, "name": hit["name"], "type": hit["type"], "rect": hit["rect"]}}

        # ★ 2026-10-07 实机跑出来的 bug：eid 路径被要求另外交 from_capture，拿到 code=12。
        #   这自相矛盾 —— 上面刚重新走了一遍树，那就是一次**新鲜**的观测，
        #   再要一个 capture 等于把同一件事要两遍。
        #
        #   而且让 agent 交「上一次」ctl_find 的 capture_id 反而**更弱**：
        #   那是窗口还没变时的旧观测，而我们手里这个是点之前那一刻的。
        #
        #   所以：eid 解析即观测绑定，这里就地签一份替 agent 补上。
        #   「先看再动」这条规矩没被削弱 —— 被绑的是更近的那一次。
        # ★ T15：元素命中 ⇒ 走元素指纹版
        tok = _issue_capture_from_tree(hwnd, {"命中": [hit]})
        if not tok:
            return env(code=gate.CODE_NO_CTRL,
                       warning="eid 解析到了，但观测凭据签发失败 —— 宁可不点，也不点一个绑不住的动作")
        args = {**args, "from_capture": tok}
        args["_eid_hit"] = {**args["_eid_hit"], "capture_id": tok}

    argv = ["click", "--hwnd", str(hwnd)]
    if args.get("name"):
        argv += ["--name", str(args["name"])]
    if args.get("xy"):
        x, y = _xy(args["xy"])
        argv += ["--xy", f"{x},{y}", "--coord"]
    if "retry" in args:
        argv += ["--retry", str(int(args["retry"]))]
    if args.get("dry_run"):
        argv += ["--dry-run"]
    r = _act("click", args, argv)
    if r.get("ok"):
        r["data"] = {**(r.get("data") or {}), "frame_before": None, "frame_after": None,
                     "note": "按控件名/eid 点击不依赖观测；走坐标时已校验 capture_id",
                     "点到": args.get("_eid_hit")}
    return r


def t_type(args: dict) -> dict:
    hwnd = int(args.get("hwnd") or 0)
    argv = ["type", "--hwnd", str(hwnd), "--text", str(args.get("text") or "")]
    if args.get("force_send"):
        argv += ["--force-send"]
    if args.get("dry_run"):
        argv += ["--dry-run"]
    # ★ 2026-10-10：type 老线要 C 级（act.py do_type gate.require(C)），
    #   新线预检与 rc=7 弹窗都必须按 C 走 —— 传 B 会让弹窗签出没用的 B 证。
    return _act("type", args, argv, scope="C")


def t_fill(args: dict) -> dict:
    hwnd = int(args.get("hwnd") or 0)
    x, y = _xy(args.get("xy") or [0, 0])
    argv = ["fill", "--hwnd", str(hwnd),
            "--xy", f"{x},{y}",
            "--text", str(args.get("text") or "")]
    if args.get("clear") is False:
        argv += ["--no-clear"]
    if args.get("dry_run"):
        argv += ["--dry-run"]
    # ★ 2026-10-10：fill 老线同样要 C 级（do_fill gate.require(C)），理由同 t_type
    return _act("fill", args, argv, scope="C")


def t_browser(args: dict) -> dict:
    """浏览器 CDP（包壳调 cdp.py）。★ 浏览器场景**优先走它，不要看像素**（§21 第⑤档）。

    ⚠️ 2026-10-02 验收抓到的 bug：原来这条**没做通行证前置检查**，无证时报的是
       code=4（老脚本自己报错）而不是 code=9（未授权）—— 错误码会误导 agent。
       只读级（A）也要先验一遍通行证。
    """
    try:
        ok, why, code = gate.check("A")
        if not ok:
            return env(code=code, warning=f"{why} —— 浏览器通道需要「A」级通行证，先 ctl_authorize")
    except gate.GateDenied as e:
        return env(code=e.code, warning=e.why)
    action = (args.get("action") or "pages").lower()
    argv = [action]
    if action in ("text", "html", "find", "eval", "shot", "click", "fill", "goto", "open", "wait"):
        if args.get("selector"):
            argv += [str(args["selector"])]
        elif args.get("js") and action == "eval":
            argv += ["--js", str(args["js"])]
    if action == "eval" and args.get("js") and "--js" not in argv:
        argv += ["--js", str(args["js"])]
    if args.get("url_contains"):
        argv += ["--url", str(args["url_contains"])]
    if args.get("value") is not None:
        argv += [str(args["value"])]
    rc, out, e = _legacy("cdp.py", argv, timeout=int(args.get("timeout_sec") or 90))
    return _map_rc(rc, out, e)


def t_focus(args: dict) -> dict:
    """把窗口切到前台（必要时从最小化恢复）。**打字/点击的前置条件**，属动作 → B 级。

    ★ 2026-10-02 补：做验收时发现服务里**没有这个工具**，而"窗口被最小化"正是
      最常见的真实起点（`ctl_shot` 会明确拒绝最小化窗口）。缺了它，agent 连
      "把用户说的那个窗口调出来"都做不到 —— 这是能力缺口，不是锦上添花。
    """
    hwnd = int(args.get("hwnd") or 0)
    argv = ["focus", "--hwnd", str(hwnd)]
    if args.get("dry_run"):
        argv += ["--dry-run"]
    r = _act("focus", args, argv, scope="B")
    if r.get("ok"):
        r["data"] = {**(r.get("data") or {}), "foreground": _is_foreground(hwnd)}
    return r


def t_not_implemented(name: str):
    def f(args: dict) -> dict:
        return env(code=gate.CODE_NO_CTRL,
                   warning=f"{name} 尚未实现（属于后续 S3/S4 范围）",
                   blind_spots=[f"{name} 当前不可用"])
    return f


TOOLS = {
    "ctl_status": t_status,
    "ctl_lease": t_lease,
    "ctl_brake": t_brake,
    "ctl_timeline": t_timeline,
    "ctl_wait": t_wait,
    "ctl_verify": t_verify,
    "ctl_witness": t_witness,
    "ctl_windows": t_windows,
    "ctl_shot": t_shot,
    "ctl_tree": t_tree,
    "ctl_find": t_find,
    "ctl_replay": t_replay,
    "ctl_authorize": t_authorize,
    "ctl_record": t_record,
    "ctl_click": t_click,
    "ctl_type": t_type,
    "ctl_fill": t_fill,
    "ctl_focus": t_focus,
    "ctl_browser": t_browser,
    "ctl_task": lambda a: tasks_dispatch.handle(a, store=store, env=env,
                                                 gate=gate, timeline_dir=TIMELINE_DIR),
}


def _ev(kind: str, *, actor="", tool="", payload=None, ok=True, code=0):
    try:
        return store().append(kind, actor=actor, tool=tool, payload=payload or {},
                              ok=ok, code=code)["seq"]
    except Exception:
        return None


def dispatch(tool: str, args: dict, *, actor: str = "") -> dict:
    """**唯一的入口。** 三种传输都调它。"""
    t0 = time.perf_counter()
    args = args or {}
    if tool not in TOOLS:
        r = env(code=gate.CODE_NO_CTRL, warning=f"未知工具：{tool}")
    else:
        try:
            r = TOOLS[tool](args)
        except gate.GateDenied as e:
            r = env(code=e.code, warning=e.why)
        except Exception as e:
            r = env(code=gate.CODE_NO_CTRL, warning=f"内部错误 {type(e).__name__}: {e}")
    r["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    # ★ 通知搭载（2026-10-07）：每次调用都带上"用户那边发生了什么"。
    #   零额外调用 —— 用户在面板上点了授权，下次我调任何工具就带回来。
    #   失败时不得看成"没事发生"，而是面信件本身没了 —— 那就是静默失效，比没个字段更坏。
    _ns, _ns_ok = _notices()
    if _ns_ok:
        r["notices"] = _ns
    # 下面这些不落事件（否则 ctl_wait 每调一次就写一条，颞不发事流，
    #   还会把 notices 的游标自己推进去，引发“自己消费的额外轮询”）
    if tool in ("ctl_status", "ctl_timeline", "ctl_verify", "ctl_wait"):
        return r
    if tool in ("ctl_status", "ctl_timeline", "ctl_verify"):
        return r
    try:
        ev = _ev(tool.replace("ctl_", ""), actor=actor, tool=tool,
                 payload={k: v for k, v in args.items() if k != "token"},
                 ok=bool(r.get("ok")), code=int(r.get("code") or 0))
        r["event_id"] = ev
    except Exception:
        pass
    return r


def tool_list() -> list[dict]:
    """给 MCP tools/list 与 CLI --help 用。schema 以 contract\tools.json 为唯一口径。"""
    p = os.path.join(ROOT, "contract", "tools.json")
    out = []
    try:
        spec = json.load(open(p, encoding="utf-8"))
        for t in spec["tools"]:
            if t["name"] in TOOLS:
                out.append({"name": t["name"], "description": t.get("description", ""),
                            "inputSchema": t.get("input_schema", {"type": "object"}),
                            "annotations": t.get("annotations", {})})
            else:
                out.append({"name": t["name"], "description": t.get("description", ""),
                            "inputSchema": t.get("input_schema", {"type": "object"}),
                            "annotations": t.get("annotations", {}),
                            "_status": "not_implemented"})
    except Exception as e:
        out.append({"name": "_error", "description": f"读契约失败: {e}"})
    return out
