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
def _issue_capture_for(hwnd: int, raw_out: str) -> str | None:
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
    #   老子进程的 stdout 是「授权成功那行 + JSON」，而 _issue_capture_for 对整个 out 做 json.loads()
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
    if hwnd and mode == "shot":
        tok = _issue_capture_for(int(hwnd), out)
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
    data = {"rc": rc, "raw": out.strip()[:4000]}
    # ★ 签发 capture_id —— 只读工具也签，这样后续的坐标动作才有得绑（§1.4）
    hwnd = args.get("hwnd")
    if hwnd:
        tok = _issue_capture_for(int(hwnd), out)
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
    return _perceive(args, ["--tree", str(int(args["hwnd"])),
                            "--nodes", str(int(args.get("max_nodes") or 400))])


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
    from agentctl import recorder as _rec                 # noqa: F401（真用）

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
        return env(code=e.code,
                   warning=f"{e.why} —— 写动作必须先 ctl_lease acquire（actor={actor}）")


def _bind_capture(args: dict, hwnd: int) -> dict | None:
    """写动作前置②：**走坐标就必须带 from_capture**，并校验窗口没动、前台没变、没过期。

    返回错误信封或 None。校验通过后**不消费** —— 由调用方在动作成功后消费
    （失败不消费，便于重试；这条是刻意的）。
    """
    xy = args.get("xy")
    if not xy:
        return None
    tok = args.get("from_capture")
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
            from PIL import Image                            # noqa: PLC0415
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


def _map_rc(rc: int, out: str, err: str) -> dict:
    """把老脚本的返回码映射成契约错误码（0/3/4/5/8/9）。"""
    txt = (out + err).strip()
    if rc == 0:
        return env(ok=True, code=0, data={"rc": rc, "raw": out.strip()[:3000]})
    m = {3: gate.CODE_DENY_WHITELIST, 9: gate.CODE_UNAUTH, 4: gate.CODE_NO_CTRL,
         5: gate.CODE_UNSTABLE, 8: gate.CODE_NO_CHANGE, 127: gate.CODE_NO_CTRL}
    code = m.get(rc, gate.CODE_NO_CTRL)
    warn = {8: "动作发出去了，但页面/画面没有变化 —— ⛔ **不许当成功**",
            9: "未获授权 —— 先 ctl_authorize 拿通行证",
            3: "白名单/敏感窗口拒绝",
            5: "坐标/前台/窗口稳定性拒绝"}.get(code, f"老脚本返回码 {rc}")
    if rc == 127:
        warn = f"找不到老脚本（LEGACY={LEGACY}）"
    return env(code=code, data={"rc": rc, "raw": txt[:3000]}, warning=warn)


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
    rc, out, e = _legacy("act.py", argv, timeout=int(args.get("timeout_sec") or 120))
    r = _map_rc(rc, out, e)
    if r.get("ok") and args.get("from_capture"):
        try:
            gate.consume_capture(args["from_capture"])
        except Exception:
            pass
    return r


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
                     "note": "按控件名点击不依赖观测；走坐标时已校验 capture_id"}
    return r


def t_type(args: dict) -> dict:
    hwnd = int(args.get("hwnd") or 0)
    argv = ["type", "--hwnd", str(hwnd), "--text", str(args.get("text") or "")]
    if args.get("force_send"):
        argv += ["--force-send"]
    if args.get("dry_run"):
        argv += ["--dry-run"]
    return _act("type", args, argv)


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
    return _act("fill", args, argv)


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
