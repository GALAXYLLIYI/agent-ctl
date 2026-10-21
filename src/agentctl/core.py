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
import hashlib
import json
import os
import re
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
# ★ P2（2026-10-16）：动作面异常刹车。判据逻辑全在 action_guard 里，
#   core 只在**动作出口**调一次 observe() —— 不在这里写任何阈值、不在这里判该不该停。
from agentctl import action_guard                        # noqa: E402
from agentctl import popup_slot                          # noqa: E402（单例弹窗仲裁器）

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


# ── T1 窗2（D1 治本）：eid ↔ 观测绑定用的旁表 ─────────────────────────
# 病根：eid 是**遍历当场编的序号**（uia_tree.py:280  "e%d" % len(nodes)），
#   只有那一次遍历的口径下才有意义。而本文件里三条出口的口径本来就不同：
#     ctl_tree  roles=("interactive","text")  max_nodes=600   ← 签发侧
#     ctl_find  roles=("all",)                max_nodes=3000  ← 签发侧
#     t_click   roles=("all",)                max_nodes=600   ← 改前重解析侧
#   同一个 e34 在两张表里必然指不同节点 —— **窗口完全静止也一样错**（确定性错位，不是抖动）。
# 治法**不是**加 tree_sha256：签发侧只算命中那几十条、校验侧走全树，指纹永远不等
#   ⇒ 永远 code=12 ⇒ 等于造一个「怎么点都点不了」的新坑。
# 治法是**旁表 + 身份复核**：签发时把「那批节点的 eid → 元素特征」记在 capture_id 名下，
#   点击时按**当初那批**取回坐标，再用「类型 + 名字 + 矩形」复核它还在不在原位。
def _eid_db_path():
    """旁表落盘位置。跟 capture.json 同目录（同一把锁、同一套原子写、同一个 15 秒寿命）。

    AGENTCTL_EID_SESSIONS 是给验收脚本/沙箱用的重定向口（口径同 AGENTCTL_SHOTS_DIR）——
    旁表**不是契约入参**（ctl_click.additionalProperties=false），所以逃生阀只能是环境变量。
    """
    p = os.environ.get("AGENTCTL_EID_SESSIONS")
    if p:
        return p
    return os.path.join(gate.AUTHDIR, "eid_sessions.json") if gate.AUTHDIR else None


def _eid_strict() -> bool:
    """严格模式默认**开**。AGENTCTL_EID_STRICT=0 恢复改前行为（重走一遍按编号取坐标）。

    ⚠ 回退开关，默认值 1（严格）。拔除条件：T1 全线验收通过、且 §三 全部判据转绿之后，
      由 DSH 决定是否连同本函数一起删（删前须先确认没有依赖旧行为的脚本）。
    之所以**读调用时**而不是 import 时：闸门状态目录在 import 之后才由环境定下来，
      读一次会把它冻在模块加载那一刻。
    """
    return os.environ.get("AGENTCTL_EID_STRICT", "1") != "0"


def _eid_load(path, default):
    """读旁表。读不到就当空 —— 与 gate._load 同一口径。

    ⚠ 注意这**不是** fail-open 于「点错」：没有记录时 t_click 会**拒**（code=12），
      不是「查不到就照旧点」。fail-open 的只有「表里没有这个号」这一种事实陈述。
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _eid_save(path, obj):
    """原子写。**与 gate._save 逐行同款**（临时文件 + os.replace），

    只是不复用它的 `_` 私有名 —— DSH 只授权在 gate.py 加一行公开别名（exclusive_lock）。
    """
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _eid_sessions_put(tok: str, hwnd: int, nodes: list, walk: dict | None,
                      无明细: bool = False) -> bool:
    """把「这一批节点的 eid → 元素特征」写进旁表。**键就是 capture_id**。

    只在**真的返回了节点明细**时写：ctl_tree 默认 detail=False 时「节点」是空列表
    （uia_tree.py:435），旁表就**没有内容** —— 这时 agent 拿树文本里的 eid 来点，
    会拿到一句人话（见 t_click 里的处方），而不是默默点歪。

    walk 记下签发那次的遍历口径，点击时**按同一口径重走**做身份复核 ——
    口径不同就等于拿不同的坐标系比坐标，复核会假失败。
    """
    p = _eid_db_path()
    if not p or not tok:
        return False
    now = time.time()
    eids = {}
    for n in nodes:
        e = n.get("eid")
        if e is None:
            continue
        eids[str(e)] = {"type": n.get("type"), "name": n.get("name"),
                        "rect": list(n.get("rect") or ()),
                        "center": list(n.get("center") or ()),
                        "enabled": n.get("enabled", None)}
    if not eids and not 无明细:
        return False
    try:
        with gate.exclusive_lock():          # ← 公开别名（gate.py 唯一新增的那一行）
            S = _eid_load(p, {})
            S = {k: v for k, v in S.items() if float((v or {}).get("expires_at", 0)) > now}
            S[str(tok)] = {"hwnd": int(hwnd), "issued_at": now,
                           "expires_at": now + float(gate.CAPTURE_TTL),
                           "walk": walk or {}, "eids": eids,
                           # ★ 窗G3b：记下**这次观测是哪个工具发起的**。
                           #   拒绝时要说得出「来自 ctl_screen」还是「来自 ctl_tree」——
                           #   笼统说「轻量模式」对从 ctl_screen 来的人是误导
                           #   （他压根没用过 ctl_tree，也没少传什么参数）。
                           "工具": str((walk or {}).get("tool") or ""),
                           "节点明细": bool(eids)}
            _eid_save(p, S)
        return True
    except Exception:
        # 旁表写不进去**不许**降级成「照样点」—— 点错比点不了坏得多。
        return False


def _eid_lookup(tok, eid):
    """按 capture_id 取回「当初那批」里这个 eid 的元素特征。

    返回 (feat, entry, how)：
      feat  找到了才有；entry 是那条观测记录；how ∈ {"精确", "旁查", "缺失"}。
    「旁查」= 没给 capture_id，但旁表里**别的**观测签发过这个号 ——
      这种必须要求把 from_capture 一起带回来（eid 跨观测不能单独用）。
    """
    p = _eid_db_path()
    if not p:
        return None, None, "缺失"
    try:
        S = _eid_load(p, {})
    except Exception:
        return None, None, "缺失"
    now = time.time()
    if tok:
        entry = S.get(str(tok))
        if not entry or float((entry or {}).get("expires_at", 0)) <= now:
            return None, None, "缺失"
        feat = (entry.get("eids") or {}).get(str(eid))
        if feat is not None:
            return feat, entry, "精确"
        # 这条观测**存在**，但它没有元素明细（ctl_tree 默认轻量模式）⇒
        #   返回「空快照」而不是「缺失」，好让 t_click 给出对症的处方。
        return None, entry, ("空快照" if not entry.get("节点明细", True) else "缺失")
    for entry in S.values():
        if float((entry or {}).get("expires_at", 0)) <= now:
            continue
        if str(eid) in (entry.get("eids") or {}):
            return None, entry, "旁查"
    return None, None, "缺失"


def store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store(TIMELINE_DIR)
    return _STORE


import threading

# ★★ P0-2（2026-10-21）：「这次调用是**人**发起的吗」这条标记。
#   为什么用 thread-local 而不是塞进 args：
#     ① args 会过契约校验（additionalProperties: false）⇒ 塞私字段会被当成非法参数；
#     ② args 会被原样落进事件流 ⇒ 留痕里出现 "_human": true 对查账的人毫无意义。
#   为什么需要它：解除急停（ctl_brake --action release）**必须由人做** ——
#   agent 用自己唯一的工具接口解掉自己触发的急停，等于「刹车能被被刹者拆掉」。
_call_ctx = threading.local()


def _is_human_call() -> bool:
    """本次调用是不是人发起的（人路：面板按钮 / 命令行显式确认）。"""
    return bool(getattr(_call_ctx, "human", False))


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
    # ★ S8-改动1：把「忘了归码」这件事**变显性**（治本；不新造码、不改默认值、不动放行口径）。
    #   根因就在上面那行默认值：code 默认 CODE_NO_CTRL(=4)，于是**凡是忘了显式传 code 的
    #   失败分支，都会静悄悄落成 4** —— 事后没人分得清「这条压根没归码」和
    #   「归过码、就是真的找不到控件」，排查时会被 code 领到错误方向。
    #   所以这里刻意**不改 code 数值**，只在「失败且调用处没传 code」时：
    #     · data 里加机读标记 uncategorized=True
    #     · warning 末尾补一句短说明
    #   判据：没传 code ⇒ data["uncategorized"] is True；
    #         传了 code ⇒ 这个**键不存在**（不是 False、不是 None）。
    #   数据侧：data 用新 dict 拼，绝不就地改调用方传进来的那一个（_act 等处会复用）。
    #   ★ 2026-10-14 裁决（有人问「现在有 15 了，默认值要不要改成 15」）：**不改，仍是 4**。
    #     理由三条：① 改默认值 = 改【放行口径】，S8 放行明写「不改任何 code 数值」；
    #     ② 「忘了归码」既不等于「找不到控件」、也不等于「服务内部错」—— 拿 15 当默认只是
    #        换一个同样不准确的断言；真要治就得**再开一个「未分类」码**，那是另一张单；
    #     ③ 这一类的可观测性已经由上面的 `uncategorized` 标记提供了（别指望默认值自己说话）。
    #     实测面：全 src 目前【零处】「ok=False 但没传 code」⇒ 改了也看不见效果（只影响将来）。
    if not d["ok"] and "code" not in head:
        _why = ("（⚠️ 本次失败**未归码**：调用处没传 code，落默认 4 CODE_NO_CTRL"
                " —— 别把它读成「真的找不到控件」）")
        _d = d["data"]
        d["data"] = {**(_d if isinstance(_d, dict) else {}), "uncategorized": True}
        d["warning"] = (d["warning"] + _why) if d["warning"] else _why
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
            return env(code=gate.CODE_INTERNAL,
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
    # ★ 2026-10-19 修「两本账」：把 core 认定的那本账**塞进子进程**，
    #   这样老线 gate（不管解析到哪份）读的都是同一个 auth_pass.json。
    if getattr(gate, "PASSFILE", None):
        env["AGENTCTL_AUTHDIR"] = os.path.dirname(gate.PASSFILE)
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

    ⚠️ 2026-10-16（窗K · P0）**自指保护判据升级，与本函数无关，但读它的人该知道**：
      老线的**防自授权闸门**（act.py 的 check_target）原来只按一个标题字符串拦。
      现在改成身份优先的三重匹配：属主进程名（python.exe / consent.exe /
      SystemSettings.exe 硬拒；终端那 7 个进程默认拦）→ 窗口类名 → 标题兜底。
      ⇒ **对助手的两条直接影响**：
        ① 往终端窗口发动作（cmd / powershell / pwsh / conhost / wt / WindowsTerminal /
           OpenConsole）现在**默认被拒**，返回 code=7（要人点头），不是拒之门外的死路；
        ② 授权弹窗、UAC 框、系统设置页**永远**不许碰，连「用户确认放行」这条路都没有 ——
           那条路只对终端开放，且限时 30 分钟、按 (进程, 类名)、写事件、到点自动失效。
      判据：tests\_P0_自指保护判据.py（红验证见 work\_2h_wK\ 下两份原始输出）。

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


# ── 指示层：默认常开（2026-10-18 用户拍板「没影响就默认开」）──────────────────
# 设计本意：**边框/角标**，不是遮罩 —— 薄边、点击穿透（HTTRANSPARENT）、带 WDA 不进截图，
#   所以它既不挡视野，也不污染像素判据。真正会"看不见"的场景，恰恰是它没被拉起。
# 关闭方式：AGENTCTL_BORDER=0（单条开关；以后可再拆 border / marks / badge 三个）。
def _border_running():
    """屏上是否已有可见的边框/角标窗（幂等用，不靠 pid 文件 —— 强杀后 pid 文件会撒谎）。"""
    try:
        import ctypes                                      # noqa: PLC0415
        from ctypes import wintypes                        # noqa: PLC0415
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(h, _l):
            cls = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(h, cls, 64)
            if "AgentCtlCtrlBorder" in cls.value and u32.IsWindowVisible(h):
                found.append(int(h))
            return True
        u32.EnumWindows(_cb, 0)
        return found[0] if found else 0
    except Exception:
        return 0


def _ensure_border():
    """会话级常开指示层；返回 {spawned, pid/hwnd, 原因}。**失败不抛**（提示层坏了不该阻断动作）。"""
    if str(os.environ.get("AGENTCTL_BORDER", "1")).strip().lower() in ("0", "off", "false", "no"):
        return {"spawned": False, "原因": "AGENTCTL_BORDER=0（显式关闭）"}
    ex = _border_running()
    if ex:
        return {"spawned": False, "已有边框窗": ex, "原因": "已在跑（幂等）"}
    try:
        import subprocess as _sp                             # noqa: PLC0415
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        if not os.path.isdir(src):
            src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = _sp.Popen([sys.executable, "-X", "utf8", "-m", "agentctl.ctrl_border",
                       "--follow"],
                      cwd=src,
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                      creationflags=getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0))
        return {"spawned": True, "pid": p.pid, "形态": "边框/角标（穿透 + WDA，不挡视野）"}
    except Exception as e:
        return {"spawned": False, "原因": "起不来：%s: %s" % (type(e).__name__, e)}



def t_status(args: dict) -> dict:
    st = gate.status()
    st["service"] = {"running": True, "schema_version": SCHEMA_VERSION,
                     "legacy_home": LEGACY, "timeline": TIMELINE_DIR,
                     "frozen": bool(getattr(sys, "frozen", False)),
                     "integrity": _my_integrity()}      # ★ 见该函数注释
    st["recorder"] = {"recording": False, "profile": "not_implemented"}
    st["timeline"] = {"head_event_id": store().head[0], "head_hash": store().head[1][:16]}
    # ★ T1 窗2 · W2-D：**只有一行指针 + 最小三步命令**。
    #   ⛔ 不在这里写第二份完整 SOP —— 完整 SOP 的唯一定义处是 contract + docs\AGENT接口.md
    #   （WorkBuddy U9「第二真源」：同一段话写两遍，迟早各改各的漂了）。
    #   这里存在的理由只有一个：agent 接到 code=12 时不必去翻文档就知道下一步敲什么。
    st["写动作三步"] = (
        "1) ctl_lease --actor <你> --action acquire --scopes BC"
        "  2) ctl_find --hwnd <hwnd> --query <要找的字>  → 拿 capture_id（%g 秒内有效）"
        "  3) ctl_click --actor <你> --hwnd <hwnd> --xy <center 原样> --from_capture <capture_id>"
        "  ⛔ xy 是屏幕物理像素，与 ctl_find 的 center 同口径，**原样传，不要减窗口原点**；"
        "eid 是**那次快照的取件钥匙**，用它必须带 --from_capture。"
        "  完整口径见 docs/AGENT接口.md（本字段只是指针，不是第二份 SOP）" % gate.CAPTURE_TTL)
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
        # ★★ P0-2（2026-10-21）：**解除急停必须由人做**。
        #   事故（2026-10-21 现场）：agent 用自己的工具接口（ctl_brake --action release）
        #   连续三次解掉自己触发的急停 —— 「刹车能被被刹者拆掉，就不叫刹车」。
        #   改法：**工具面（MCP / HTTP）只允许 stop 与 status**；
        #     release 只走两条人路：① 面板「▶ 解除急停」按钮 ② 命令行显式确认
        #     （AgentCtl.exe brake --action release --confirm-release）。
        #   ★ 诚实边界：这一条堵的是「顺手自解锁」——agent 唯一顺手的接口没了。
        #     shell 层面直接删旗子文件（%LOCALAPPDATA%\AgentCtl\_急停.flag）不是本条能挡的，
        #     那是 OS 权限问题；但那条路会**绕过留痕**，所以本条只承诺「工具面不许自解锁」。
        if not _is_human_call():
            return env(code=gate.CODE_UNAUTH,
                       data={"engaged": gate.brake_engaged(), "flag": gate.BRAKE_FLAG,
                             "human_paths": ["面板的「▶ 解除急停」按钮",
                                             "命令行：AgentCtl.exe brake --action release --confirm-release"]},
                       warning="⛔ 解除急停是**人的动作**，agent 侧只允许 ctl_brake 的 stop / status。"
                               "要解除请让用户在面板上点「▶ 解除急停」，"
                               "或运行：AgentCtl.exe brake --action release --confirm-release"
                               "（两边都会留痕）。")
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
def _issue_capture_from_tree(hwnd: int, raw_out: str,
                             walk: dict | None = None) -> str | None:
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
            tok0 = gate.issue_capture(hwnd, rect0, "",
                                      bool(_is_foreground(hwnd)))
            # ★ T1 窗2：旁表**只在有节点明细时**才有内容，但这张「空记录」也要写 ——
            #   否则 agent 拿 ctl_tree（默认轻量模式）文本里的 eid 来点时，
            #   会拿到一句「这个号不存在」，而真因是「这次观测压根没交出节点明细」。
            #   空记录让 t_click 能分辨这两者，给出对症的处方（见 _eid_refuse_missing）。
            _eid_sessions_put(tok0, hwnd, [], walk, 无明细=True)
            return tok0
        sig = [(n.get("type"), (n.get("name") or "")[:40], tuple(n.get("rect") or ()))
               for n in nodes]
        blob = json.dumps(sig, ensure_ascii=False, sort_keys=True).encode("utf-8")
        tree_sha = _hl2.sha256(blob).hexdigest()
        tok = gate.issue_capture(hwnd,
                                  rect=_win_rect(hwnd) or [0, 0, 0, 0],
                                  image_sha256="",
                                  foreground=bool(_is_foreground(hwnd)),
                                  tree_sha256=tree_sha)
        # ★ T1 窗2（D1）：把「这批节点的 eid → 元素特征」记在这次 capture_id 名下。
        #   这是 D1 的治本处 —— 改前点击侧是**重新编号**（roles/max_nodes 口径都不同），
        #   现在点击侧按当初那批取回坐标，编号不再被第二次遍历重新定义。
        #   写不进去**不降级**：_eid_sessions_put 返回 False 时，t_click 会拒（见那里）。
        _eid_sessions_put(tok, hwnd, nodes, walk)
        return tok
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
#   它给「只读感知」也在扣 acts 计数器；换成我们的 gate 之后 A 级走 skip_count 早退、
#   不再计数 —— 也就是 A 级从此**没有次数上限**。
#   ★ 2026-10-15（窗D·P5）第二次订正：这里注入的闸门发证时按**已授予档位里最紧的一档**
#     取 acts_limit（gate.effective_act_limit），**不再**按 scopes[0] 的排列巧合取。
#     改前 `ACT_LIMIT.get(short(scopes[0]), 20)` 会让「A 打头」的证盖上 200，
#     于是那张证上 B/C/D 的上限实际是 200。完整口径与理由见 gate.py 的 ACT_LIMIT 注释。
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
        # ★ 2026-10-17（端到端判据抓出来的）：**刹车优先** —— 不许把"急停拉着"
        #   说成"去拿通行证"。原来这里把 GateDenied 一律映射成 code=9 + 泛化文案，
        #   于是真急停下的拒绝理由变成「未获授权 —— 请先 ctl_authorize 拿通行证」，
        #   看的人会去反复申请授权，真正的原因（刹车已拉下）被吞掉了。
        if gate.brake_engaged():
            return env(code=gate.CODE_BRAKE, data={"raw": str(e)[:400]},
                       warning="刹车已拉下（急停文件存在）—— 一切自动控制停止"
                               "（原始拒绝：%s）" % str(e)[:200])
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
        return env(code=gate.CODE_INTERNAL, warning="进程内感知失败 %s: %s" % (type(e).__name__, e))
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
        # ★ 2026-10-17：同 892 那条 —— 急停拉着时拒绝理由必须点名刹车，不许说成"去拿通行证"。
        if gate.brake_engaged():
            return env(code=gate.CODE_BRAKE, data={"raw": out.strip()[:400]},
                       warning="刹车已拉下（急停文件存在）—— 一切自动控制停止"
                               "（老脚本 rc=9：%s）" % out.strip()[:160])
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
                   # ★ 2026-10-19（判据31⑤）：原来这里也是裸复读 f"老脚本返回码 {rc}"。
                   #   2026-10-13 的 S3-B2「未知 rc 不许静默兜底」**只修了 _map_rc 那条路**，
                   #   这条路被漏掉了 —— 同一刀砍了一半。
                   # ⚠️ 诚实边界：本条只把「复读」改掉，**没有动「所有未知 rc 都落 code=4」
                   #   这个落码口径**（超出判据范围，语义归属另议）。
                   warning="没找到目标控件（老脚本 rc=%d）—— 先 ctl_windows / ctl_tree 看现状；"
                           "若 rc 属于 act.py 没约定的码，先看 data.raw 原文再判断" % rc)
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


# ══════════════════════════════════════════════════════════════════════
# P0 神经层（窗Z · 2026-10-18）—— 归属 / 代际 / 因果判词
#
# 依据：work\dispatch_Z_神经层P0.md ＋ work\_三方审议_神经层_合流_20261018.md
#   · 根因不是「缺眼睛」，是缺**绑定**：像素↔结构化身份（这点归谁）、
#     动作↔世界增量（我做的和我看到的对不对得上）；
#   · 归属由**感知层报事实**（WindowFromPoint 只作证据，拿它当决策就是重犯老坑）；
#   · 动作层只报「发了」（因果/预期），不许把「发了」冒充「成了」；
#   · 协议必须**三态**：存在 / 确认缺席（只在覆盖区内成立）/ **未知**；
#   · 决策前必须一次**原子快照对账**（轮询是真相，钩子只提示；本期不做 WinEventHook）。
# ⛔ 本块只落在 core.py：不动 gate.py / legacy_tools\（另一窗独占）。
#    只用 gate 的**既有**常量与函数，不新增 gate 侧定义。
# ══════════════════════════════════════════════════════════════════════
WIN_GEN = {"n": 0, "sig": ""}     # 窗口快照代际：**服务端签发、append-only（只增不减）**


def _win_text_cls(hwnd):
    """窗口的 (title, class)。取不到就给空串 —— **不编占位值**。"""
    try:
        import win32gui                                    # noqa: PLC0415
        return (win32gui.GetWindowText(int(hwnd)) or "",
                win32gui.GetClassName(int(hwnd)) or "")
    except Exception:
        pass
    try:
        import ctypes                                      # noqa: PLC0415
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        t = ctypes.create_unicode_buffer(512)
        c = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(int(hwnd), t, 512)
        u32.GetClassNameW(int(hwnd), c, 256)
        return (t.value or "", c.value or "")
    except Exception:
        return ("", "")


def _windows_snapshot():
    """**同步原子快照**：一次 EnumWindows 走完，逐窗取 rect / z 序 / 前台 / 最小化 / cloaked。

    ★ 为什么是「快照」不是「事件流」：钩子只能当唤醒提示，决策前必须**对一次账**。
      快照就是那一次对账（「无事件 = 没变」是本项目明令禁止的推理）。
    ★ 事件清单要含**移动 / 最小化 / 飞出屏外**（`-21333` 那条现场就是「移动」，
      既不是新建也不是改尺寸）⇒ rect 原样进快照，飞出屏外的窗**照收**。
    ★ 取不到 ⇒ None。**观测失败不编** —— 绝不返回一张空列表冒充「桌面上没有窗口」。
    """
    try:
        import ctypes                                      # noqa: PLC0415
        from ctypes import wintypes                        # noqa: PLC0415
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    except Exception:
        return None

    hwnds = []
    _CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(h, _l):
        hwnds.append(int(h))
        return True

    try:
        if not u32.EnumWindows(_CB(_cb), 0):
            return None
    except Exception:
        return None
    try:
        fg = int(u32.GetForegroundWindow() or 0)
    except Exception:
        fg = 0
    out = []
    for z, h in enumerate(hwnds):
        try:
            if not u32.IsWindowVisible(wintypes.HWND(h)):
                continue
        except Exception:
            continue
        try:
            r = wintypes.RECT()
            if not u32.GetWindowRect(wintypes.HWND(h), ctypes.byref(r)):
                continue
            rect = [int(r.left), int(r.top), int(r.right), int(r.bottom)]
        except Exception:
            continue
        try:
            mini = bool(u32.IsIconic(wintypes.HWND(h)))
        except Exception:
            mini = False
        cl = 0
        try:
            v = wintypes.DWORD(0)
            # DWMWA_CLOAKED = 14（UWP/虚拟桌面隐藏的窗：看得见「存在」，但不在屏上）
            if dwm.DwmGetWindowAttribute(wintypes.HWND(h), wintypes.DWORD(14),
                                         ctypes.byref(v), ctypes.sizeof(v)) == 0:
                cl = int(v.value or 0)
        except Exception:
            cl = 0
        t, c = _win_text_cls(h)
        out.append({"hwnd": int(h), "pid": 0, "class": c, "title": t,
                    "rect": rect, "z_index": int(z),
                    "is_foreground": bool(fg and h == fg),
                    "minimized": mini, "cloaked": bool(cl)})
    return out


def _windows_sig(wins) -> str:
    """快照签名：只吃「事实字段」，不吃回显顺序（否则每调一次都像变了）。"""
    import hashlib as _hl
    blob = json.dumps([[w.get("hwnd"), w.get("class"), w.get("title"),
                        list(w.get("rect") or []), int(w.get("z_index") or 0),
                        bool(w.get("is_foreground")), bool(w.get("minimized")),
                        bool(w.get("cloaked"))] for w in (wins or [])],
                      ensure_ascii=False, sort_keys=True).encode("utf-8")
    return _hl.sha256(blob).hexdigest()[:16]


def _windows_gen_bump(wins) -> int:
    """签一代：快照签名变了 ⇒ 进位一（**append-only**，从不回退）。

    ★ 为什么必须单调：坐标/rect 都带 `gen N`，当前 gen 已 bump ⇒ 老坐标**直接作废**。
      没有代际机制，加多少事件流都救不了「窗口动过、缓存坐标全死」这件事。
    """
    sig = _windows_sig(wins)
    if sig != WIN_GEN.get("sig"):
        WIN_GEN["n"] = int(WIN_GEN.get("n") or 0) + 1
        WIN_GEN["sig"] = sig
    return int(WIN_GEN.get("n") or 0)


def _win_at_point(x, y):
    """发送瞬间的归属**事实**：`WindowFromPoint(x,y)` → `GetAncestor(GA_ROOT)`。

    ★ 只报事实，不作决策（三方共识第 3 条：归属由感知层报）。
    ★ 我们自己的边框/高亮层是 `WS_EX_TRANSPARENT + HTTRANSPARENT`（w3 用例 13 实测穿透）
      ⇒ `WindowFromPoint` **会自动跳过提示层**，不会污染归属判定。
    ★ 取不到 ⇒ None。**不编一个「没有遮挡」** ——「没看见」不等于「没有」。
    返回 {hwnd, root, class, title, chain}；chain = 从顶层到命中窗的那一串
    （agent 一眼看出「该关哪个窗」）。
    """
    try:
        import ctypes                                      # noqa: PLC0415
        from ctypes import wintypes                        # noqa: PLC0415
        u32 = ctypes.WinDLL("user32", use_last_error=True)

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        h = int(u32.WindowFromPoint(POINT(int(x), int(y))) or 0)
        if not h:
            return None
        root = int(u32.GetAncestor(wintypes.HWND(h), 2) or h)      # GA_ROOT = 2
        chain, cur, guard = [], h, 0
        while cur and guard < 16:
            t, c = _win_text_cls(cur)
            chain.append({"hwnd": int(cur), "class": c, "title": t})
            if int(cur) == root:
                break
            cur = int(u32.GetParent(wintypes.HWND(cur)) or 0)
            guard += 1
        chain.reverse()                                    # 顶层在前
        t0, c0 = _win_text_cls(h)
        return {"hwnd": int(h), "root": root, "class": c0, "title": t0, "chain": chain}
    except Exception:
        return None


def _rect_xyxy(r, *, wh: bool = False):
    """归一化成 (x0,y0,x1,y1)。`wh=True` 时按 [x,y,w,h] 解（diff bbox / expect_region）。

    非法 / 空矩形 ⇒ None（**没有面积就没有归属可言**，不拿它当「全屏」）。
    """
    if not r or len(r) < 4:
        return None
    try:
        a, b, c, d = [int(v) for v in list(r)[:4]]
    except Exception:
        return None
    if wh:
        c, d = a + c, b + d
    x0, x1 = (a, c) if a <= c else (c, a)
    y0, y1 = (b, d) if b <= d else (d, b)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _rect_inter(a, b):
    """矩形求交（xyxy）。空 ⇒ None。"""
    if not a or not b:
        return None
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


CAUSAL_VERDICTS = ("met", "no_effect_in_region", "changed_outside_target", "unknown")


# ★★ P0-1 失败分类（2026-10-21；Codex 实跑 + MiniMax Code / ZCode 两份评审一致认定）：
#   把「失败」拆成三类 —— 这是 action_guard 的 fail_streak 能不能当信号的分水岭。
#     rejected_env   动作**根本没发出去**（没证 / 没租约 / 被别的窗挡 / 缺凭据 /
#                    前台不对 / 找不到控件）：重试没有意义（再点一次还是被挡），
#                    但**不该**被算成「agent 在乱点」。
#     no_effect     发出去了但画面没变（code=8）：这才是「点了但没点到」。
#     action_failed  真异常（服务内部错误、动作抛异常）：同样该急停。
#   ★ 2026-10-21 实测事故链（现场取证）：同一坐标连续 8 次**环境性**失败
#     （wrong_target / foreground_blocked / 缺 from_capture）
#     → fail_streak=6 拉急停
#     → 急停连带吊销租约 → 后续动作全被拒 → 更多失败（**正反馈**）。
#     病根不是阈值太低，是**失败没分类**：把「环境不满足」和「动作没生效」混在一个桶里。
FAILURE_CLASS_REJECTED_ENV = "rejected_env"
FAILURE_CLASS_NO_EFFECT = "no_effect"
FAILURE_CLASS_ACTION_FAILED = "action_failed"

#: 前置拒绝码 —— 共同点是**动作一个像素都没发出去**
_REJECTED_ENV_CODES = frozenset((3, 4, 5, 7, 9, 10, 11, 12, 13))
#: 发出去了但没变化
_NO_EFFECT_CODES = frozenset((8,))


def failure_class(r: dict) -> str | None:
    """把一次调用的失败归到三类之一；成功 ⇒ None。

    ★ **判不出来的一律按 action_failed（真失败）算** —— 宁可多算不可漏算：
      漏算 = 护栏松了（该急停没急停）；多算 = 只是保守（回到改前口径）。
    ★ 口径**只贴标签、不改判定**：不改任何 code 数值、不改放行条件。
    """
    if not isinstance(r, dict) or r.get("ok"):
        return None
    try:
        code = int(r.get("code"))
    except (TypeError, ValueError):
        return FAILURE_CLASS_ACTION_FAILED
    if code in _NO_EFFECT_CODES:
        return FAILURE_CLASS_NO_EFFECT
    if code in _REJECTED_ENV_CODES:
        return FAILURE_CLASS_REJECTED_ENV
    return FAILURE_CLASS_ACTION_FAILED


# ★ W5-B（2026-10-20，用户拍板「统一补写判词」）：**判词统一词表 + 统一写法**。
#   改前的毛病：判词**散在三个地方、各说各话**
#     · 因果判词（窗Z）  met / no_effect_in_region / changed_outside_target / unknown
#     · 闭环判词（T17）  satisfied / unsatisfied / snapshots-incomparable / unknown
#     · 输入判词         accepted / not_target / foreground_blocked / unknown
#   台账那边却只有一张表（met / no_effect / changed_outside_target / denied / unknown）
#   ⇒ 同一件事两栈两套读法，离线对不上账；而且判词**只回给调用方**、不落盘，
#     出了事没人查得到「当时到底判成了什么」。
#   修法（**只加不改**）：
#     ① 不动任何既有字段的语义 —— 顶层 verdict 仍是闭环判词、data.after.verdict
#        仍是因果判词（判据 36 读的就是它，动了就改判定输入）。
#     ② 只**加**一个统一块 `verdict_canonical`（折到台账那一张表），
#        并把同一份判词**顺手写进事件流（timeline）与证据落盘（evidence.json）**
#        —— 这才是「统一补写」的落点：判词从此有据可查，而不是只活在返回值里。
CANONICAL_VERDICTS = ("met", "no_effect", "changed_outside_target", "denied", "unknown")

_VERDICT_CANON = {
    # 因果判词（窗Z）
    "met": "met",
    "no_effect_in_region": "no_effect",
    "changed_outside_target": "changed_outside_target",
    "unknown": "unknown",
    # 闭环判词（T17）
    "satisfied": "met",
    "unsatisfied": "no_effect",
    "snapshots-incomparable": "unknown",
    # 输入判词
    "accepted": "met",
    "not_target": "denied",
    "foreground_blocked": "denied",
    # 闸门 / 台账原生词
    "denied": "denied",
    "no_effect": "no_effect",
}


def _canon_verdict(value, kind: str = "", reason: str = "", evidence=None) -> dict:
    """把各路判词折成**一张表**（台账口径），并记清它是从哪种判词折来的。

    折不出的一律落 unknown —— **不许猜**："折不出"本身就是一种可查的事实。
    """
    raw = str(value or "").strip()
    ev = evidence if isinstance(evidence, dict) else None
    if ev is not None:
        # 证据只留判词相关的几项（整个 diff 太大，落盘不是判词的事）
        ev = {k: ev.get(k) for k in ("region", "changed_bbox", "changed_outside_pct")
              if k in ev}
    return {"value": _VERDICT_CANON.get(raw, "unknown"),
            "raw": raw,
            "kind": kind or "unknown",
            "reason": str(reason or "")[:300],
            "evidence": ev}


def _causal_verdict(diff, *, hit_rect=None, expect_region=None) -> dict:
    """因果判词（后置验）：把「发了」与「成了」分开，**四态不许两态**。

    verdict：
      met                    变化落在「判定区」里 ⇒ 这次动作在**我声明的地方**见效
      no_effect_in_region    确认没变（`diff.changed is False`）—— 不是「没看见」
      changed_outside_target 画面**真变了**，但变化区与判定区求交为空（世界自己动了）
      unknown                **观测失败**（抓屏失败 / 全空白 / 没测出来）⇒ **≠ no_effect**

    ★ 判定区 = `expect_region`（给了就与命中窗求交）∩ 命中窗；没给就是命中窗本身。
    ★ 成本护栏：全帧 diff 照算、窗外变化照报；`expect_region` **只细化判词**，
      不许让「别处的大变化」消失（否则 agent 学会「少声明混过验证」）。
    """
    region = None
    er = _rect_xyxy(expect_region, wh=True) if expect_region else None
    hr = _rect_xyxy(hit_rect) if hit_rect else None
    if er and hr:
        region = _rect_inter(er, hr)
    elif er:
        region = er
    else:
        region = hr
    d = diff if isinstance(diff, dict) else {}
    ch = d.get("changed")
    bb = _rect_xyxy(d.get("bbox"), wh=True)
    ev = {"region": list(region) if region else None,
          "changed_bbox": list(d.get("bbox")) if d.get("bbox") else None,
          "changed_outside_pct": None,
          "channel_diag": ""}
    if ch is None:
        ev["channel_diag"] = ("观测失败：%s ⇒ **unknown（观测失败）≠ no_effect** —— "
                              "不许把「我没看见」写成「我看见了没变」（w3 12c 同款纪律）"
                              % (d.get("说明") or "没拍前后快照"))
        return {"verdict": "unknown", "verdict_evidence": ev}
    if ch is False:
        ev["changed_outside_pct"] = 0.0
        ev["channel_diag"] = "确认没变：%s" % (d.get("说明") or "逐像素比对无变化")
        return {"verdict": "no_effect_in_region", "verdict_evidence": ev}
    if not region:
        ev["channel_diag"] = ("画面真变了，但**拿不到判定区**（命中窗矩形不可读）⇒ "
                              "只能说 unknown，不能说 met")
        return {"verdict": "unknown", "verdict_evidence": ev}
    if bb and _rect_inter(bb, region):
        ev["changed_outside_pct"] = 0.0
        ev["channel_diag"] = "变化区与判定区有交 ⇒ 在我声明的地方见效"
        return {"verdict": "met", "verdict_evidence": ev}
    _ix = _rect_inter(bb, region) if bb else None
    _a_all = ((bb[2] - bb[0]) * (bb[3] - bb[1])) if bb else 0
    _a_in = ((_ix[2] - _ix[0]) * (_ix[3] - _ix[1])) if _ix else 0
    ev["changed_outside_pct"] = (round(100.0 * (1.0 - (float(_a_in) / float(_a_all))), 1)
                                 if _a_all else 100.0)
    ev["channel_diag"] = ("画面真变了，但变化区 %s 与判定区 %s 求交为空 ⇒ "
                          "**世界自己动了，不是我这次动作的后果**（不许报成功）"
                          % (ev["changed_bbox"], ev["region"]))
    return {"verdict": "changed_outside_target", "verdict_evidence": ev}


def _own_panel_blocks(hit: dict) -> bool:
    """落点命中的窗口链里，有没有**我们自己的面板**（agentctl 的悬浮面板）。

    ★ 与「敏感窗黑名单」是两件事，别混：
      · 黑名单：agent **不许点**自己的面板（防绕过授权）—— 红线，不动
      · 这里：面板**挡住了别人**时，让它让路（见 _panel_yield_and_retry）
    读不到面板登记 ⇒ False（宁可被拒，不许乱动）。
    """
    try:
        from agentctl import panel_yield                # noqa: PLC0415
        ph = panel_yield.panel_hwnd()
        if not ph:
            return False
        for n in (hit or {}).get("chain") or []:
            if int(n.get("hwnd") or 0) == int(ph):
                return True
        return False
    except Exception:
        return False


def _panel_yield_and_retry(timeout: float = 2.0) -> bool:
    """请自己的面板让位（收起来），并等它**真的**藏起来。成功返回 True。

    ★ 恢复不在这里做：统一放在 dispatch 出口（动作做完 ⇒ 弹回来），
      免得每个调用点各写一遍、漏一处就永远收着面板。
    ★ 面板没登记 / 让位失败 ⇒ False ⇒ 照旧按「被遮挡」拒（fail-safe）。
    """
    try:
        from agentctl import panel_yield                # noqa: PLC0415
        if not panel_yield.request_yield():
            return False
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            if not panel_yield.panel_visible():
                return True
            time.sleep(0.1)
        return False
    except Exception:
        return False


def _ownership_precheck(args: dict, hwnd: int, tool: str = "ctl_click"):
    """发送瞬间的**归属前置闸**：不过就不发（一个像素都不动）。

    返回 `(错误信封 | None, blind_spots 列表)`。三件，全在**发送之前**：

      ① 代际：`window_gen` 给了且 ≠ 当前快照代际 ⇒ 拒（窗口动过了，坐标全死）。
      ② 归属：`WindowFromPoint(x,y)` 的顶层 ∉ 目标窗树 ⇒ 拒，回执**点名遮挡者**
         （`{hwnd, class, title, chain}`，让 agent 一眼知道该关哪个窗）。
      ③ 区域声明：`expect_region` 与目标窗矩形求交为空 ⇒ 拒（声明写到了别人窗里）。

    ⛔ 不做「事后小票」：点击的激活副作用在调用方看到回执之前就已经发生
       （ZCode 顶回 opencode 的那一条）。
    ★ 观测不到就直接说「**未判定**」并进 blind_spots，绝不编「没有遮挡」——
      「没看见」不等于「没有」（三态纪律）。production 上 `_win_at_point` 是能取到的。
    """
    if args.get("dry_run"):
        return None, []
    blind = []

    # ── ① 代际（坐标/rect 带的 gen 必须等于当前 gen）────────────────────
    want = args.get("window_gen")
    if want in (None, ""):
        want = args.get("gen")
    if want not in (None, ""):
        snap = _windows_snapshot()
        cur = _windows_gen_bump(snap) if snap is not None else None
        if cur is None:
            blind.append("窗口快照取不到 ⇒ **代际未判定**（不是「代际没变」）；"
                         "本次坐标上带的 window_gen=%s 无从核对" % want)
        elif str(want) != str(cur):
            return env(code=gate.CODE_STALE_CAPTURE,
                       data={"blocked_by": "stale_window_gen",
                             "gen_校验": {"window_gen": want, "当前gen": cur,
                                          "判定": "不匹配"},
                             "gen_口径": ("gen 由服务端签发、append-only；窗口清单一动"
                                          "（移动/最小化/新建/销毁/飞出屏外）就进位。"
                                          "带旧 gen 的坐标**直接作废**，不许硬用。")},
                       warning=("坐标上带的 window_gen=%s 与**当前**桌面快照代际 %s 对不上 "
                                "⇒ 窗口清单动过了（移动/最小化/新建/销毁）。这份坐标已经作废，"
                                "本次**零像素**。重新 ctl_windows 拿新的 gen 与 rect 再动手。"
                                % (want, cur))), []
    # ── ② 归属（发送瞬间；WindowFromPoint 只作证据）─────────────────────
    xy = args.get("xy")
    if xy:
        x, y = _xy(xy)
        w_xyxy = _rect_xyxy(_win_rect(hwnd))
        if w_xyxy is None:
            blind.append("归属**未判定**：读不到目标窗 hwnd=%s 的矩形（窗口已销毁/夹具）"
                         "⇒ 本次没做归属前置闸，但**这不等于落点归目标**" % int(hwnd))
        elif not (w_xyxy[0] <= x <= w_xyxy[2] and w_xyxy[1] <= y <= w_xyxy[3]):
            blind.append("归属**未判定**：落点 (%s,%s) 不在目标窗矩形 %s 内 ⇒ 无从比对归属"
                         "（act.py 自己的包含校验会拦这种点）" % (x, y, list(w_xyxy)))
        else:
            hit = _win_at_point(x, y)
            # ★★ 2026-10-20 新增：落点被**我们自己的面板**挡住 ⇒ 先让位再判一次。
            #   为什么不是直接拒：面板是常驻挂件，经常正好盖在目标窗上，
            #   agent 每每撞「wrong_target」却无路可走；人手动就是「把面板收起来再点」。
            #   ⚠ 让位**不是**放开敏感窗：点自己的面板照样被黑名单拒，这里只解决「挡视线」。
            if hit is not None and _own_panel_blocks(hit):
                if _panel_yield_and_retry():
                    hit = _win_at_point(x, y)          # 重新取归属事实（面板已经让开）
                    if hit is None:
                        blind.append("面板让位后取不到落点 (%s,%s) 上的窗口 ⇒ 归属**未判定**" % (x, y))
            if hit is None:
                blind.append("归属**未判定**：WindowFromPoint 取不到落点 (%s,%s) 上的窗口 "
                             "⇒ 「没看见」不等于「没有遮挡」" % (x, y))
            else:
                chain_hwnds = [int(n.get("hwnd") or 0) for n in (hit.get("chain") or [])]
                if (int(hit.get("root") or 0) != int(hwnd)
                        and int(hwnd) not in chain_hwnds):
                    occ = {"hwnd": hit.get("hwnd"), "class": hit.get("class"),
                           "title": hit.get("title")}
                    return env(code=gate.CODE_STALE_CAPTURE,
                               data={"blocked_by": "wrong_target",
                                     "归属": {"判定": "wrong_target",
                                              "目标hwnd": int(hwnd),
                                              "遮挡者": occ,
                                              "chain": list(hit.get("chain") or []),
                                              "落点": [x, y],
                                              "口径": ("WindowFromPoint(落点) → "
                                                       "GetAncestor(GA_ROOT) 与目标 hwnd 比对；"
                                                       "**发送之前**判，不归目标就不发。"
                                                       "提示层是 WS_EX_TRANSPARENT|"
                                                       "HTTRANSPARENT，WindowFromPoint 天然跳过它。")}},
                               warning=("落点 (%s,%s) 现在归属 hwnd=%s（class=%r title=%r）"
                                        "——**不是**目标窗 hwnd=%d。目标窗被它**遮挡**了，"
                                        "本次**零像素**：先在发送之前定位归属，不归目标就不发。"
                                        "该关的窗：title=%r class=%r hwnd=%s。"
                                        "先把它关掉/移开，重新 ctl_screen 拿新的 capture_id，"
                                        "再用新的坐标点。"
                                        % (x, y, occ.get("hwnd"), occ.get("class"),
                                           occ.get("title"), int(hwnd),
                                           occ.get("title"), occ.get("class"),
                                           occ.get("hwnd")))), []
    # ── ③ expect_region 必须在目标窗里 ────────────────────────────────
    er_raw = args.get("expect_region")
    if er_raw:
        er = _rect_xyxy(er_raw, wh=True)
        wr = _rect_xyxy(_win_rect(hwnd))
        if er is None:
            return env(code=gate.CODE_UNSTABLE,
                       data={"blocked_by": "expect_region_malformed",
                             "expect_region": er_raw},
                       warning=("expect_region 要 [x,y,w,h]（与 xy 同口径、屏幕物理像素），"
                                "这次收到 %r，没法判 —— 本次**零像素**" % (er_raw,))), []
        if wr is not None and not _rect_inter(er, wr):
            return env(code=gate.CODE_STALE_CAPTURE,
                       data={"blocked_by": "expect_region_outside_target",
                             "expect_region_校验": {"expect_region": list(er),
                                                    "目标窗rect": list(wr),
                                                    "判定": "与目标窗求交为空"}},
                       warning=("expect_region=%s 与目标窗 hwnd=%d 的矩形 %s 求交为空 —— "
                                "你声明「会变的那块」压根不在目标窗里（写到别的窗/桌面上去了）。"
                                "本次**零像素**。要么改 expect_region，要么目标窗选错了。"
                                % (list(er), int(hwnd), list(wr)))), []
    return None, blind


def t_windows(args: dict) -> dict:
    """窗口清单（**同步原子快照 + 代际**）。

    ★ 代际 `gen` 与 `screen_version` 同一套纪律：**服务端签发、append-only**；
      调用方可以把上次的 gen 回传（`gen=N`）做对照，对不上 ⇒ code=12（清单动过了）。
    ★ 快照取不到 ⇒ `windows=null`，**不返回空列表冒充「桌面上没有窗口」**。
    """
    limit = int(args.get("limit") or 40)
    r = _perceive(args, ["--windows", "--limit", str(limit)])
    if not isinstance(r, dict):
        r = env(ok=False, code=gate.CODE_INTERNAL, warning="感知层没有返回信封")
    d0 = r.get("data") if isinstance(r.get("data"), dict) else {}
    snap = _windows_snapshot()
    if snap is None:
        r["data"] = {**d0, "windows": None, "gen": None}
        r["blind_spots"] = list(r.get("blind_spots") or []) + [
            "窗口快照没取到 ⇒ 本次**没有归属/代际基线**（不是「桌面上没有窗口」）—— "
            "别拿它当「窗口都在这了」"]
        return r
    gen = _windows_gen_bump(snap)
    r["data"] = {**d0, "windows": list(snap)[:limit], "gen": gen,
                 "gen_口径": ("gen 由服务端签发、append-only：窗口清单一动"
                              "（移动/最小化/新建/销毁/飞出屏外）就进位。"
                              "坐标动作可带 window_gen=这个值；对不上 ⇒ code=12")}
    want = args.get("gen")
    if want not in (None, "") and str(want) != str(gen):
        return env(code=gate.CODE_STALE_CAPTURE,
                   data={"blocked_by": "stale_window_gen",
                         "gen_校验": {"gen": want, "当前gen": gen, "判定": "不匹配"},
                         "windows": r["data"]["windows"], "gen": gen,
                         "gen_口径": r["data"]["gen_口径"]},
                   warning=("你回传的 gen=%s 与当前代际 %s 对不上 ⇒ 窗口清单动过了。"
                            "这份 rect/坐标已经作废，拿本次的 gen 与 windows 重新取坐标。"
                            % (want, gen)))
    return r


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


def _uia_gate(args: dict, tool: str, fn, walk_override: dict | None = None):
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
        return env(code=gate.CODE_INTERNAL, warning=str(e),
                   blind_spots=["UIA 不可用 ≠ 界面上没有；Canvas/游戏/Electron 本来就瞎"])
    except Exception as e:
        return env(code=gate.CODE_INTERNAL,
                   warning="读元素树失败 %s: %s" % (type(e).__name__, e))
    res["窗口状态"] = st
    d = {"结果": res}
    # 元素树也是一次观测 —— 而且是比截图**更严**的观测。签发凭据，
    # 这样走坐标的点击照样受「必须绑定最近一次观测」这条规矩管。
    # ★ T15：res 是 **dict**（uia_tree.tree/find 的返回值）⇒ 走元素指纹版
    # ★ T1 窗2：把**签发那次的遍历口径**一起记进去。点击侧要按同一口径重走做身份复核 ——
    #   口径必须来自签发侧（core.py 里 t_tree/t_find 各自的默认值），不能现场重算一遍。
    # ★ F4（窗B1）：walk_override 让 ctl_screen 把**自己那趟遍历的真实口径**记进去。
    #   旁表里的 walk 是点击侧重走时用来对坐标的；口径不同就等于拿不同的坐标系比坐标。
    _walk = dict(walk_override or {
        "roles": list(args.get("roles") or (("interactive", "text")
                                            if tool == "ctl_tree" else ("all",))),
        "max_nodes": int(args.get("max_nodes") or
                         (uia_tree.DEFAULT_MAX_NODES if tool == "ctl_tree"
                          else uia_tree.FIND_MAX_NODES)),
        "max_depth": int(args.get("max_depth") or uia_tree.DEFAULT_MAX_DEPTH)})
    # ★ 窗G3b：口径里带上**这次观测是哪个工具发起的** —— 旁表拒绝时要说得出
    #   「这次观测（来自 ctl_screen）没有元素明细」，而不是笼统一句「轻量模式」。
    _walk.setdefault("tool", tool)
    # ★ 窗G3b · (a) 病根与治法。
    #   病根：fn 自己签好的 capture 被**忽略**，这里拿 res **再签一张**。
    #     而 ctl_screen 的 res 里**没有** 节点/命中 键（它交的是 objects/texts，
    #     不是原始节点表）⇒ _issue_capture_from_tree 走的是**空快照**分支。
    #     于是：屏幕交出了 eid、旁表却查无此号，真门路上永远 code=12。
    #     （DSH 2026-10-18 真 Edge 实跑：ctl_screen ok=True eid=e43，ctl_click 报
    #      「这次观测没有元素明细」。上一单判据全绿是因为它取的是内层那张。）
    #   治法：**fn 已经签好的就用它**。ctl_screen 本来就做了一次全量 walk，
    #     明细是**顺带**的（见 _screen_build 里的 eid 签发段）。
    #   ⛔ 这**不是放宽校验**：旁表该有什么还是什么，查不到照样拒。
    #     改的只是「别用一张废的**盖掉**本来带明细的那张」。
    pre = res.get("capture_id") if isinstance(res, dict) else None
    tok = pre if (isinstance(pre, str) and pre) else _issue_capture_from_tree(h, res, _walk)
    if tok:
        d["capture_id"] = tok
        d["capture_ttl_sec"] = gate.CAPTURE_TTL
        # ★ 2026-10-10 名副实（Codex 实测抓到「节点: [] 两个指纹都空还自称元素树」）：
        #   默认轻量模式不采集节点 ⇒ 没有元素指纹可算（凭据只绑定 hwnd/rect/前台）。
        #   改前这里一律写「元素树（不是图像）」，冒领了没采集到的东西。照实说。
        if pre or res.get("节点") or res.get("命中"):
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
        return env(code=gate.CODE_INTERNAL, warning=f"出片失败 {type(e).__name__}: {e}")
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
    _border = _ensure_border()   # 指示层：默认开（AGENTCTL_BORDER=0 可关）
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
        # ★ S8-改动2：**只改措辞，不改 code 数值**（9 仍是 9，改数值越界）。
        #   为什么措辞要紧：这是一个**信封**，agent 拿到 code=9 会照
        #   docs/AGENT接口.md 的处方去「停下、向用户申请通行证」——
        #   而这里 parse_scopes 抛 ValueError 是**范围/参数本身解析失败**，
        #   ⛔ **根本不是通行证问题、别去申请授权**：此刻一张通行证都还没申请过，
        #   去 ctl_authorize 只是把同一个写错的 scopes 串原样再送一遍，转不出来。
        #   该做的是核对 scopes 字符串本身（合法短代号只有 A/B/C/D，"ABCD" 会被展开）。
        return env(code=gate.CODE_UNAUTH,
                   warning=("范围/参数解析失败：parse_scopes 拒绝了这个 scopes 串：%s —— "
                            "⚠️ 这**不是通行证问题、别去申请授权**（此刻还没申请过通行证，"
                            "ctl_authorize 只会把同一个写错的 scopes 再送一遍）。"
                            "该做的是核对 scopes 本身：只能是 A/B/C/D 短代号。"
                            % e))
    scopes = keys
    full = [gate.SCOPES[k] for k in keys]
    # ⚠️ 必须用**半角逗号**拼接：auth_popup.py 是按 "," 切分范围的（见其 line 152）。
    #    我原来写成 "、"（顿号），结果两个范围被当成**一个不存在的范围名**存进通行证 ——
    #    证是真的、期是真的，但任何检查都报"范围不符"，表现就是**用户点了授权也没用**。
    scope_str = ",".join(full)
    minutes = int(args.get("minutes") or 10)
    # ★ 2026-10-15（窗D·P5，用户裁定）：默认次数上限取「已授予档位里最紧的那一档」。
    #   改前是 `gate.ACT_LIMIT.get(gate.short(scopes[0]), 20)` —— scopes[0] 只是**输入顺序**，
    #   不是授权强度；于是「A 打头」的证一律盖上 200，而闸门放行时读的就是证上这个数
    #   ⇒ 该证上 B/C/D 的上限实际是 200（实测证据：work\_2h_wB_回执.md §3.1）。
    #   现在统一走 gate.effective_act_limit（单源），A 不参与取最小（A 本就不计数）。
    #   ⚠️ 发证能拿到的只有「申请到的 scopes」—— auth_popup.py 不做减法，用户点同意就是
    #      全给（legacy_tools\auth_popup.py:248-252 原样落盘），所以「申请到的」==「授予的」，
    #      这里不存在「按申请算、实际授得更多」的缺口。
    # ★ 2026-10-19 用户定：**一个任务一次签、取消动作次数上限**。
    #   约定：acts 显式传 0（或负数）= 不封顶；不传 = 用档位默认（旧语义保留）。
    #   安全边界没变：有效期照旧 + 急停一票否决 + 敏感窗永久拒 —— 用户原话「我看到有问题我会急停」。
    _acts_raw = args.get("acts")
    if _acts_raw is None:
        acts = int(gate.effective_act_limit(scopes, 20))
    else:
        acts = int(_acts_raw)
    if acts < 0:
        acts = 0
    # ⚠️ 上下界钳制（2026-10-15 GLM/ZCode 复检点出"无钳制"；与 acquire_lease 的 ttl `max(1,min(,600))` 同思路）。
    #   下界 1、上界 **10000**。两点如实说明：
    #   ① **显式 acts=0 会被上面的 `or` 吞掉**、回退成默认值 —— 这是**改前既有语义，本次不改**：
    #      0 按"未指定"处理更安全（真想要"零动作"就别申请，而不是靠发证侧截断）；
    #   ② 上界取 10000 而不是 600：任何档位上限都 ≤200，长会话顶多几百 ⇒ 10000 只为挡**手滑**
    #      （例如误传 1e9），不打算限制正常工作流；
    #   ③ 这不构成放宽：闸门仍按**证上写的** acts_limit 卡（gate.py:794-796 + 同处双轨注释），
    #      本处只把要写进证里的数字收进合理区间。
    # ★ 2026-10-19 用户定「一个任务一次签、取消次数上限」：
    #   实测踩到——上面解析出 0 之后，**原来这行 `max(1, ...)` 又把 0 抬成 1**
    #   （证上真写着 acts_limit=1，作者亲签验证抓到的）。现在 0 原样保留，只钳上界。
    acts = 0 if acts <= 0 else min(acts, 10000)
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
    # ★ 2026-10-21（Codex）：**单例弹窗仲裁** —— 同一时刻屏幕上只允许一个授权弹窗。
    #   病根：并发 ctl_authorize 各自拉一个弹窗进程（现场证据：闸门日志里
    #   2026-10-21 00:25:27 / 00:26:25 / 00:28:10 三笔请求重叠，第一笔 00:29:10 才被点掉
    #   ⇒ 那一刻屏幕上最多同时有 3 个弹窗；用户原话「为什么会有四个闸口弹窗访问我」）。
    #   语义不变：还是要人亲自在弹窗里输密钥；只是**后到的请求排队**，
    #   等前一个弹窗的结果 —— 证已经覆盖本次范围就直接放行，**不再弹第二个**。
    #   等不到 ⇒ fail-closed（拒绝），绝不 fail-open。
    _res = popup_slot.authorize(
        gate.AUTHDIR, gate.PASSFILE, full,
        popup_fn=lambda: _legacy_cmd(cmd, timeout=timeout),
        wait=float(timeout) + 30.0,
        log=gate.log)
    rc, out, err = int(_res["rc"]), _res["out"], _res["err"]
    granted = _res["outcome"] in ("granted_pass", "granted_popup")
    if _res["outcome"] == "busy":
        # ★ fail-closed：等不到槽就明确拒绝，不绕过去硬弹（否则又变成满屏弹窗）。
        _ev("authorize_denied", actor=args.get("actor", ""), tool="ctl_authorize",
            payload={"scopes": scopes}, ok=False, code=gate.CODE_UNAUTH)
        gate.log("授权未通过：排队等不到弹窗槽（fail-closed）")
        return env(code=gate.CODE_UNAUTH,
                   data={"granted": False, "scopes": full, "rc": rc,
                         "reason": "popup_slot_busy", "detail": _res["detail"]},
                   warning=_res["detail"])
    if granted:
        _ev("authorize_granted", actor=args.get("actor", ""), tool="ctl_authorize",
            payload={"scopes": scopes}, ok=True, code=0)
        gate.log("通行证已放行：%s，有效至 %s（%d 分钟 / %d 次）"
                 % (scope_str, args.get("_expires") or "见面板", minutes, acts))
        return env(ok=True, code=0, data={"granted": True, "scopes": full,
                                          "minutes": minutes, "acts_limit": acts,
                                          "via": _res["outcome"],
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
                                       payload=_REC["autostop"], ok=False,
                                       code=gate.CODE_RESOURCE_LIMIT)
                    except Exception:
                        pass
                    break
                try:
                    r.tick()
                except Exception as e:
                    _REC["autostop"] = f"{type(e).__name__}: {str(e)[:80]}"
                    try:
                        # ★ S8-改动3：**只加注释**。
                        #   ⚠️ 下面这行是 **事件行**（store().append），**不是 env() 信封** ——
                        #      所以它没有 warning 字段，也拿不到改动1的 uncategorized 标记。
                        #      上面的 payload（类型名 + 消息前 80 字）是**诚实的**，不改。
                        #   ⚠️ code=9 在这里的含义是**语义错位**：真相是「录制线程 tick() 抛异常」，
                        #      不是「未获授权」；9 在这一列仅供 timeline 检索用。
                        #      正确归属（内部错误）在 gate.py 的码表里**没有** ⇒ 不在本单职权内改，
                        #      也不拿 CODE_RESOURCE_LIMIT(13) 硬套（那是「资源保护停录」，语义不同）。
                        #      要改得同时动 gate.py 码表 + tools.json + docs ⇒ 超出本单边界。
                        #   ★ 2026-10-14 后记：**现在有了** —— `CODE_INTERNAL = 15` 已进码表，
                        #      gate.py / contract\tools.json / docs\AGENT接口.md / 本文件四处同步。
                        #      本行仍是【事件行】（不是信封），所以它照旧用 9；但上面那句
                        #      「码表里没有」已经过时，别再照它推理。
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
    # ★ P0-A：坐标路径的凭据现在有**两种**（契约 §二）：
    #   from_capture（截图凭据，ctl_shot / ctl_tree 签发）或 frame_id（屏版凭据，ctl_screen 签发）。
    #   二者**至少一个**；都缺仍然 code=12 —— 这条铁律不动（旧坐标点新画面）。
    fid = str(args.get("frame_id") or "").strip()
    if not tok and not fid:
        return env(code=gate.CODE_STALE_CAPTURE,
                   warning="走坐标的动作必须带一个凭据："
                           "**from_capture**（先 ctl_shot 或 ctl_tree 拿 capture_id）"
                           "**或 frame_id**（先 ctl_screen 拿 screen_version）。"
                           "两个都缺 ⇒ 无法判断这个坐标是否还对当前画面，"
                           "本次一个像素都没点。"
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
    # ★ 只有带了 from_capture 才走这道四重校验；
    #   只带 frame_id 的路径由 t_click 拿它去对 screen_version（那是 frame_id 的本义）。
    if tok:
        try:
            gate.check_capture(tok, hwnd=hwnd, rect=rect, foreground=fg, image_sha256=img)
        except gate.GateDenied as e:
            return env(code=e.code, warning=f"{e.why} —— 重新 ctl_shot 拿新的 capture_id 再动手")
    return None


def _act_precheck(args: dict, scope: str = "B") -> dict | None:
    """写动作前置①②：**通行证（非消耗）→ 租约 → hwnd 齐了没**。成功返回 None。

    ★ 为什么从 _act 里抽出来、且**唯一定义处就这里一处** ★
      t_click 在**定位元素之前**就必须先过这两道闸。原因有二：
      ① 项目纪律（_act 的注释）：「先报要通行证再报要租约」——
         顺序反过来，agent 会先被叫去申请租约、拿到租约后才发现没通行证，白跑两趟。
         而 eid/name 新线要读元素树、还要发一次 walk；不先过闸，
         「没通行证」就会被「读元素树失败」盖住 ⇒ s3_accept_gapfix 的
         G2（断言 code=9）与 G3（断言 code=10）当场红。
      ② 闸门判定**只能有一处**。这里抽出来给 _act 和 t_click 共用，
         就是为了让「同一套判据」真的只有一份，而不是在 t_click 里抄一遍。
    ★ 非消耗：gate.check 不扣次数，_require_write 只校验不发放，
      所以 _act 里再跑一遍是安全的（两遍结论必然一致）。
    """
    try:
        ok, why, code = gate.check(scope)
        if not ok:
            return env(code=code,
                       warning=f"{why} —— 写动作需要「{scope}」级通行证，先 ctl_authorize")
    except gate.GateDenied as e:
        return env(code=e.code, warning=e.why)
    err = _require_write(args.get("actor", ""))
    if err:
        return err
    if not int(args.get("hwnd") or 0):
        return env(code=gate.CODE_NO_CTRL, warning="缺 hwnd")
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

    ★ S3-B2（2026-10-13）新增：**表里没有的 rc 不许静默兜底**。
      改前是 `m.get(rc, CODE_NO_CTRL)`，rc=2 / rc=99 这类「老脚本出了没约定的码」
      会被说成 code=4「找不到控件」，而《AGENT接口.md》给 code=4 的处方是
      「重新 ctl_windows + ctl_tree 看现状」—— agent 照着只会绕圈。
      现在早退成独立一支：ok=False、落现有码 5、warning 明说「未知返回码」，
      data.unknown_rc 给机读判据（与 C4 的 blocked_by 同一套路）。
      **不新造错误码** —— 码表在 gate.py:427-430，改它越界。
    """
    txt = (out + err).strip()
    if rc == 0:
        return env(ok=True, code=0, data={"rc": rc, "raw": out.strip()[:3000]})
    m = {3: gate.CODE_DENY_WHITELIST, 7: gate.CODE_NEEDS_ASK, 9: gate.CODE_UNAUTH,
         4: gate.CODE_NO_CTRL,
         # ← C4：rc=6 是 act.py 的「被模态对话框挡住」，落 code=5；不再兜底成「找不到控件」
         5: gate.CODE_UNSTABLE, 6: gate.CODE_UNSTABLE,
         8: gate.CODE_NO_CHANGE, 127: gate.CODE_NO_CTRL}
    # ★ S3-B2：未知 rc 单独早退，**必须放在下面那张 warn 表之前** ——
    #   那张表是按 code 取的，未知 rc 落 code=5 会命中「坐标/前台/窗口稳定性拒绝」，
    #   与真实原因（老脚本出了 act.py 没约定的码）完全不对。
    #   ok 保持 False（env() 默认），绝不算成功。
    if rc not in m:
        return env(code=gate.CODE_UNSTABLE,
                   data={"rc": rc, "raw": txt[:3000], "unknown_rc": True},
                   warning=("老脚本返回**未知返回码 %d** —— act.py 没约定这个码，"
                            "不许当成功、也不许当成「找不到控件」。先看 data.raw "
                            "里的原文再判断（老脚本：%s）" % (rc, LEGACY)))
    code = m[rc]          # ★ 由 m.get(rc, ...) 改成 m[rc]：第二道保险 ——
                         #   以后谁往 m 里加了键又忘了同步上面的早退分支，当场 KeyError，
                         #   总比又悄悄兜底成「找不到控件」强。
    warn = {8: "动作发出去了，但页面/画面没有变化 —— ⛔ **不许当成功**",
            9: "未获授权 —— 先 ctl_authorize 拿通行证",
            3: "白名单/敏感窗口拒绝",
            7: "这个窗口用户还没点头 —— 需要先在弹窗里确认",
            5: "坐标/前台/窗口稳定性拒绝",
            # ★ 2026-10-19（判据31①）：code=4 原来**没在这张表里**，于是落到下面那个
            #   「老脚本返回码 {rc}」的复读默认值 —— agent 拿到的回执等于没有回执，
            #   跟 D1「恒定报没带 actor」是同一病根（失败态长得跟「什么都没拿到」一样）。
            #   语义有既有出处：m 表 4→CODE_NO_CTRL，且本函数 docstring 里就写着
            #   「rc=6 … 掉进兜底变成 code=4『找不到控件』」+ 契约 §2.1 给 code=4 的处方。
            4: "没找到目标控件（老脚本 rc=4）—— 重新 ctl_windows + ctl_tree 看现状再点"
            # ★ 默认值也不再复读：未知 rc 在上面已早退，走到这里必是**有 code 但漏了表**；
            #   那属于「映射表与 warn 表不同步」，必须显形，不许悄悄复读。
            }.get(code, "返回码已映射但缺专属文案（映射表与文案表不同步，请补）")
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


# ════════════════════════════════════════════════════════
# 动作信封 · after.diff（P0-A · 2026-10-18）
# ════════════════════════════════════════════════════
# 契约 §二：data.after.diff = { changed:bool, bbox:[x,y,w,h]|null, crop_path?:str }
#   before/after 落 work\_actions\<frame_id>\，只留最近 20 组。
#
# ★ 为什么必须在**动作前**就拍：事后补拍无法归因 ——
#   那和「截图发现界面变了」不能证明「是这个动作造成的」是同一个道理
#   （同 T17 闭环开头那条教训）。
# ★ 为什么阈值化再取 bbox：光标闪烁 / 字体抗齿齿会让 getbbox 永远非空 ⇒
#   每一次动作都报 changed=true，等于这个字段没有信息量。
#   所以先把小于阈值的差抹掉，剩下的才算「画面真的变了」。

ACT_DIFF_KEEP = 20        # work/_actions 下只留最近 N 组（契约 §二：20）
ACT_DIFF_STEP = 2         # 每通道最大差 < 2 视为噪声（0=完全相同，255=黑白反转）
# ★ 接线单 §二-4：变化区的忽略噪声口径。
#   光标闪烁是一条 1~2px 宽的竖条，靠像素比对会让每一次动作都报变了。
#   caret_px=8 是窄边上限：单个字符大概 9~11px 宽，7a84边 9 > 8 就不会被当成光标。
ACT_DIFF_IGNORE_DEFAULT = {"caret_px": 8, "min_area_px": 24}


def _diff_ignore_cfg(args) -> dict:
    """读 diff_ignore（部分支持），永远返回一份**完整**配置。

    ★ 为什么不直接拼进去：调用方只传了一半（只传 caret_px）时，
    另一个输入就会变成 None → 比对时报 TypeError。缺的项必须补齐。
    """
    _cfg = dict(ACT_DIFF_IGNORE_DEFAULT)
    _raw = (args or {}).get("diff_ignore")
    if isinstance(_raw, dict):
        for _k in _cfg:
            try:
                _v = int(_raw.get(_k))
            except (TypeError, ValueError):
                continue
            if _v >= 0:
                _cfg[_k] = _v
    return _cfg


def _act_fid(fid) -> str:
    """把**外部传的** frame_id 洗成能安全当目录名的一段。

    ★ 为什么要洗：frame_id 是调用方给的字符串，直接拼路径等于
    把一个「../../..」当目录名用。这里只因为落盘名字，不因为判定。
    """
    s = re.sub(r"[^0-9A-Za-z_.\-]", "_", str(fid or "")).strip("._")[:64]
    return s or "nofid"


def _act_diff_blocks(mask) -> list:
    """把阈值化的变化 mask 切成**连通块**，返回 [(宽, 高, 面积, (x0,y0,x1,y1))]。

    ★ 为什么不能只看包围盒：光标闪烁 + 区域选取会在一条横线上留下**一串互不相连的碎片**，
      它们的包围盒很宽（整行那么宽），可里面真正变了的像素加起来还不如一个字符多 ——
      只用包围盒面积必然误报。分块后才能区分「光标」与「字符」。

    实现参考：先按行抽连续段（用 bytes.find 跳过零段，大图也不慢），
    再用并查集合并「上下相邻且 x 区间相交」的段。
    """
    try:
        w, h = mask.size
        data = mask.tobytes()
    except Exception:
        return []
    runs = []                       # [y, x0, x1]（按 y 递增，行内亦按 x 递增）
    for y in range(h):
        row = data[y * w:(y + 1) * w]
        x = 0
        while True:
            x = row.find(255, x)
            if x < 0:
                break
            x2 = row.find(0, x)
            if x2 < 0:
                x2 = w
            runs.append([y, x, x2 - 1])
            x = x2
    if not runs:
        return []
    parent = list(range(len(runs)))

    def _find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    # 只比较**相邻两行**的段（双指针），不做 O(n^2) 全比
    i = 0
    n = len(runs)
    while i < n:
        _y0 = runs[i][0]
        j = i
        while j < n and runs[j][0] == _y0:
            j += 1
        k = j
        while k < n and runs[k][0] == _y0 + 1:
            k += 1
        for a in range(i, j):
            for b in range(j, k):
                if runs[a][1] <= runs[b][2] and runs[b][1] <= runs[a][2]:
                    _union(a, b)
        # ★ i = j（而不是 k）：下一轮要从「刚合并过的那行」继续，
        #   否则变成**成对**合并（20-21、22-23…），第 21 行和第 22 行永远接不上 ——
        #   一块 20 行高的字符块会被切成 10 个 2 行高的碎块，每块都得不到 caret_px 而被当成光标丢掉。
        i = j
    agg = {}
    for _idx, (_y, _x0, _x1) in enumerate(runs):
        _r = _find(_idx)
        _a = agg.get(_r)
        if _a is None:
            agg[_r] = {"area": _x1 - _x0 + 1, "x0": _x0, "x1": _x1, "y0": _y, "y1": _y}
        else:
            _a["area"] += _x1 - _x0 + 1
            _a["x0"] = min(_a["x0"], _x0)
            _a["x1"] = max(_a["x1"], _x1)
            _a["y1"] = max(_a["y1"], _y)
    return [(_a["x1"] - _a["x0"] + 1, _a["y1"] - _a["y0"] + 1, _a["area"],
             (_a["x0"], _a["y0"], _a["x1"], _a["y1"])) for _a in agg.values()]


def _act_grab(hwnd: int):
    """截目标窗当前画面 → PIL.Image。

    **取不到返回 None，绝不返回一张空白图冒充。**
    """
    try:
        r = _win_rect(int(hwnd))
        if not r or r[2] <= r[0] or r[3] <= r[1]:
            return None
        import mss                                          # noqa: PLC0415
        from PIL import Image                               # noqa: PLC0415
        with mss.mss() as sct:
            shot = sct.grab({"left": r[0], "top": r[1],
                             "width": r[2] - r[0], "height": r[3] - r[1]})
        return Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        return None


def _act_diff_of(before, after, win_off=None, ignore=None) -> dict:
    """两帧求差。返回 {changed, bbox, 说明}。

    changed 三值：True 真变了 / False 确认没变 / None **没测出来**。
    ⛔ None 不许当成 False ——「我没看见」和「我看见了没变」是两回事，
    混起来就是把「我没看见」说成了「我看见了没变」。
    bbox 用**屏幕绝对坐标**（与项目☄1 go xy/rect 同口径），
    而 crop 裁图用窗口内坐标 —— 两者不同源，正好对得上。
    """
    if before is None or after is None:
        return {"changed": None, "bbox": None,
                "说明": "取不到画面（窗口不可读 / 截图失败）⇒ 没测出来，不等于没变"}
    if before.size != after.size:
        _w = max(before.size[0], after.size[0])
        _h = max(before.size[1], after.size[1])
        _ox, _oy = win_off or (0, 0)
        return {"changed": True, "bbox": [_ox, _oy, _w, _h],
                "说明": "动作前后窗口尺寸变了（%s → %s）" % (before.size, after.size)}
    try:
        from PIL import ImageChops                        # noqa: PLC0415
        _d = ImageChops.difference(before, after).convert("L")
        _mask = _d.point(lambda v: 255 if v >= ACT_DIFF_STEP else 0)
        _bb = _mask.getbbox()
    except Exception as e:
        return {"changed": None, "bbox": None, "说明": "求差失败：%s" % e}
    if not _bb:
        return {"changed": False, "bbox": None,
                "说明": "逐像素比对无变化（阈值 %d/255）" % ACT_DIFF_STEP}
    x0, y0, x1, y1 = _bb
    _ox, _oy = win_off or (0, 0)
    # ★ 接线单 §二-4：changed 是**必要非充分**。
    #   真变了 => changed 必须 true（光标闪烁不算变了）；零变化 => 必须 false。
    #   中间那个「只是光标在闪」现代成 false。
    _ig = ignore if isinstance(ignore, dict) else ACT_DIFF_IGNORE_DEFAULT
    _caret = int(_ig.get("caret_px", 8) or 0)
    _minarea = int(_ig.get("min_area_px", 24) or 0)
    # ★ ★ changed 只在「diff_ignore 之外确实有面积 ≥ min_area_px 的差异」时为 True。
    #   改前只看**包围盒**宽高与面积，地址是：光标闪烁 + 区域选取在同一行留下一串
    #   互不相连的碎片 —— 包围盒横跃整行，**面积很大**，但真正变了的像素很少。
    #   改成分块后才能回答「到底变了多少」这个问题。
    _blocks = _act_diff_blocks(_mask)
    _live = [_b for _b in _blocks if (not _caret) or min(_b[0], _b[1]) > _caret]
    _sum = sum(_b[2] for _b in _live)
    if (not _live) or (_minarea and _sum < _minarea):
        _drop = len(_blocks) - len(_live)
        return {"changed": False, "bbox": None, "忽略为": True,
                "说明": ("共 %d 块变化（其中 %d 块窄边≤%d 判为光标类），"
                        "剩余面积 %d px2 ≤ min_area_px=%d ⇒ 判为没变"
                        % (len(_blocks), _drop, _caret, _sum, _minarea)) if _drop
                       else ("变化面积 %d px2 ≤ min_area_px=%d ⇒ 判为没变" % (_sum, _minarea))}
    x0 = min(_b[3][0] for _b in _live)
    y0 = min(_b[3][1] for _b in _live)
    x1 = max(_b[3][2] for _b in _live)
    y1 = max(_b[3][3] for _b in _live)
    # ★ 块的 (x0,y0,x1,y1) 是**闭区间**（含末端像素），所以宽高要 +1。
    #   改前用 Image.getbbox()，它返回的是**右开区间**，宽就是 x1-x0 ——
    #   换成闭区间后若不 +1，60px 的块会报成 59（K4b 实测报 59 键出的就是这个），
    #   裁图还会少切一列。口径不能在重写后对不齐。
    _w = x1 - x0 + 1
    _h = y1 - y0 + 1
    # _local 是窗口内坐标，给 _act_diff_store 裁图用；PIL crop 是**右开区间**，所以右下标 +1。
    return {"changed": True, "bbox": [_ox + x0, _oy + y0, _w, _h],
            "_local": (x0, y0, x1 + 1, y1 + 1),
            "说明": ("变化区（窗口内 %d,%d 起 %dx%d；%d 块合计 %d px2）"
                    % (x0, y0, _w, _h, len(_live), _sum))}


def _act_diff_store(fid, before, after, diff) -> dict:
    """before/after/diff 落 work\\_actions\\<fid>\\；只留最近 ACT_DIFF_KEEP 组。

    返回 {dir, crop_path} 对 diff 做不可变补充（落盘失败不能连带 diff 本身丢了）。
    ⛔ 整个落盘都包在 try 里：落盘是**附带证据**，不是动作本身成功的前提。
    """
    _out = {}
    try:
        _dir = os.path.join(ROOT, "work", "_actions", _act_fid(fid))
        os.makedirs(_dir, exist_ok=True)
        for _nm, _im in (("before.png", before), ("after.png", after)):
            if _im is not None:
                _im.save(os.path.join(_dir, _nm))
        _loc = diff.get("_local")
        if diff.get("changed") and _loc and after is not None:
            # crop_path = 「变了的那块直接看」—— 采用 after 的对应区（而不是差分图）
            _cp = os.path.join(_dir, "crop.png")
            after.crop(_loc).save(_cp)
            _out["crop_path"] = _cp
        _out["dir"] = _dir
    except Exception as e:
        _out["落盘失败"] = str(e)
    try:
        _base = os.path.join(ROOT, "work", "_actions")
        _ds = [os.path.join(_base, d) for d in os.listdir(_base)]
        _ds = [d for d in _ds if os.path.isdir(d)]
        if len(_ds) > ACT_DIFF_KEEP:
            for _old in sorted(_ds, key=lambda p: os.path.getmtime(p))[:len(_ds) - ACT_DIFF_KEEP]:
                import shutil                                # noqa: PLC0415
                shutil.rmtree(_old, ignore_errors=True)
    except Exception:
        pass
    return _out


def _mk_action_evidence(action, hwnd, diff, store_ret, verdict, before, after,
                         canon=None) -> dict:
    """★ A2：把一次动作的**证据**拼成一个字典（回执顶层 `evidence` 键）。

    独立成键的三个理由：
      ① 刻意**不塞进 data.after** —— data.after.verdict 是判据 36 在读的字段，
         往里加东西就改了它的判定输入（判据不是靠约定活的）。
      ② 落盘与回执**同源**：evidence.json 里就是回执里那份，不是另算一遍。
      ③ 独立成模块级函数 ⇒ 离线验证脚本能**直接调真源**跑三态，不用发真动作。
    只加字段：不动 r["code"] / r["ok"] / r["data"] 的任何既有语义。
    """
    diff = diff if isinstance(diff, dict) else {}
    st = store_ret if isinstance(store_ret, dict) else {}
    vd = verdict if isinstance(verdict, dict) else {}
    bb = diff.get("bbox")
    win = _win_rect(hwnd) or [0, 0, 0, 0]
    ar = None
    if bb and len(bb) == 4 and win[2] > win[0] and win[3] > win[1]:
        wa = float(win[2] - win[0]) * float(win[3] - win[1])
        if wa > 0:
            # area_ratio = 变化包围盒面积 / 窗口面积（**包围盒口径**，非逐像素占比）
            ar = round(float(bb[2]) * float(bb[3]) / wa, 6)
    dirx = st.get("dir")
    return {
        "action": action,
        "hwnd": int(hwnd or 0),
        "changed": diff.get("changed"),
        "bbox": bb,
        "area_ratio": ar,
        "verdict": vd.get("verdict"),
        "verdict_evidence": vd.get("verdict_evidence"),
        # ★ W5-B：统一判词块也进证据落盘 ⇒ 离线只看 evidence.json 就能读懂判词。
        "verdict_canonical": canon,
        "before_png": (os.path.join(dirx, "before.png")
                       if dirx and before is not None else None),
        "after_png": (os.path.join(dirx, "after.png")
                      if dirx and after is not None else None),
        "crop_png": st.get("crop_path"),
        "dir": dirx,
        "说明": diff.get("说明"),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _write_action_evidence(ev: dict) -> str | None:
    """把证据字典写进 **动作同目录** 的 evidence.json；返回写入路径。

    ⛔ 落盘失败不许连带动作失败 —— 证据是附带品，动作本身才是主线。
    """
    d = (ev or {}).get("dir")
    if not d:
        return None
    p = os.path.join(d, "evidence.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)
    return p


def _act(action: str, args: dict, argv: list[str], scope: str = "B") -> dict:
    """统一：**先验证通行证（非消耗）→ 再验租约 → 再验观测绑定 → 调 act.py → 成功后消费 capture_id**。

    ★ 顺序很讲究：**先报"要通行证"再报"要租约"**。
      反过来的话，agent 会先被叫去申请租约、拿到租约后再被告知"没通行证"—— 白跑两趟。
      通行证是用户签的、更根本，所以先验它。（旧的 act.py 仍然会在执行时再验一次，双保险。）
    """
    err = _act_precheck(args, scope)
    if err:
        return err
    hwnd = int(args.get("hwnd") or 0)
    err = _bind_capture(args, hwnd)
    if err:
        return err

    # ★ P0 神经层 · ①（窗Z）：**发送瞬间的归属前置闸**。
    #   位置就在这里（通行证→租约→观测绑定**之后**、任何动作**之前**）：
    #   ① 项目的拒绝顺序不能乱（先报要通行证、再报要租约、再报凭据，最后才报归属）；
    #   ② 归属是「凭据之后」的事实，必须早于**任何**动作 —— 点击的激活副作用
    #      在调用方看到回执之前就已经发生，事后回执救不了（ZCode 钉的那条）。
    _oerr, _oblind = _ownership_precheck(args, hwnd)
    if _oerr:
        return _oerr

    # ★ T17 闭环前半（2026-10-12）：声明了 verify_expect ⇒ 先确认判定器在，再拍「前」快照。
    #   必须在**动作之前**拍 —— 事后补拍无法归因（这是 T3 换来的教训）。
    #   判定器缺失时【拒绝执行】而不是静默跳过：声明了要验、却验不了，不算完成。
    #   ★ 键名为什么带 verify_ 前缀：`expect` 这个键 **t_click 早已占用**，含义是
    #     「你看到的元素名字」（点 eid 前核对名字变没变）。同名不同义会造成静默误判 ——
    #     实测过：拿 expect 当谓词传进去，动作被当成"元素改名了"直接拒绝不点。
    exp = (args.get("verify_expect") or "").strip()
    watch = (args.get("verify_watch") or "").strip()
    _cl = None
    # ★ W5-B：统一判词块（只在**有判词**时才写，见下方各写点）。
    _canon = None
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

    # ★ P0-A：动作**前**拍一张。当前哪些动作需要快照？
    #   ① 带 frame_id 的（契约 §二：before/after 落 work\_actions\<frame_id>\）；
    #   ② t_type 的 mode=focus（契约 §二：「发了但画面没变 ⇒ code=8」，
    #      —— 那条口径本身就需要快照才判得出，与 frame_id 无关。
    # ⛔ 不启用时**一张都不拍** ⇒ 输出与改前逐字节一致（非破坏性）。
    _need_diff = bool(args.get("_need_diff") or args.get("frame_id"))
    # ★ A2（2026-10-20 派单）：**动作一律留前后证据** ——
    #   改前只有显式要 frame_id 的动作才拍前后图，于是「判不出」的那一大类
    #   **连证据都没落盘**，事后无从复核（台账导出实测：diff.bbox / area_ratio / crop
    #   在历史数据里全为 null，根因就是这里）。
    #   ⚠ 只补证据，**不改任何判定语义**：下面 _need_diff 分支里的 code=8/5 与
    #     changed_outside_target 判定原样保留 —— 这里只是把证据补齐。
    _before = _act_grab(hwnd)

    rc, out, e = _legacy("act.py", argv, timeout=int(args.get("timeout_sec") or ACT_TIMEOUT_SEC))
    r = _map_rc(rc, out, e)
    # ★ P0 神经层 · ①：归属前置闸「未判定」的那些一并如实交代（没看见 ≠ 没有）。
    if _oblind and isinstance(r, dict):
        r["blind_spots"] = list(r.get("blind_spots") or []) + list(_oblind)

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

    # ★ P0-A：动作后拍一张、求差、落盘、写进信封。
    # ⛔ 信封的 data.after **合并**而不是覆盖：t_click 已经把 uid_state 放在
    #   data.after 里（窗G3），覆盖会把那个证据抹掉。
    if not _need_diff:
        # ★ A2：没显式要 diff 的动作，**照样**留证据（求差 + 落盘 + 判词）。
        #   ⚠ 只产出证据，**不动 r["code"] / r["ok"]**（语义留给调用方判据看）。
        _after = _act_grab(hwnd)
        _ev_wr = _win_rect(hwnd) or [0, 0, 0, 0]
        _df = _act_diff_of(_before, _after, (int(_ev_wr[0]), int(_ev_wr[1])),
                           _diff_ignore_cfg(args))
        _fid = "%s_%d" % (action, time.time_ns())
        # ★ 2026-10-20 修：这里原来写的是 `_ev_after`（**没有这个变量**）
        #   ⇒ NameError ⇒ 非 diff 路径（绝大多数动作）的 A2 证据整段炸掉。
        #   改成 `_after`（就是上面刚抓的那张）。
        _st = _act_diff_store(_fid, _before, _after, _df)
        _pub = {k: v for k, v in _df.items() if not k.startswith("_")}
        for _k in ("crop_path", "dir", "落盘失败"):
            if _st.get(_k):
                _pub[_k] = _st[_k]
        _vd = _causal_verdict(_df, hit_rect=_win_rect(hwnd),
                              expect_region=args.get("expect_region"))
        _canon = _canon_verdict(_vd.get("verdict"), "causal",
                                (_vd.get("verdict_evidence") or {}).get("channel_diag"),
                                _vd.get("verdict_evidence"))
    if _need_diff:
        _after = _act_grab(hwnd)
        _wr = _win_rect(hwnd) or [0, 0, 0, 0]
        _df = _act_diff_of(_before, _after, (int(_wr[0]), int(_wr[1])),
                           _diff_ignore_cfg(args))
        # 没带 frame_id 的（focus 模式）用作用名+时间戳当目录名 —— 同一次动作仍落在同一个目录里。
        _fid = args.get("frame_id") or ("%s_%d" % (action, time.time_ns()))
        _st = _act_diff_store(_fid, _before, _after, _df)
        _pub = {k: v for k, v in _df.items() if not k.startswith("_")}
        for _k in ("crop_path", "dir", "落盘失败"):
            if _st.get(_k):
                _pub[_k] = _st[_k]
        _d0 = r.get("data") or {}
        _a0 = _d0.get("after") or {}
        # ★ P0 神经层 · ②（窗Z）：**因果判词**。四态、结构化，进信封。
        #   命中窗 = 目标窗本身（归属前置闸已保证落点归它）；expect_region 只**细化**判词。
        _vd = _causal_verdict(_df, hit_rect=_win_rect(hwnd),
                              expect_region=args.get("expect_region"))
        _canon = _canon_verdict(_vd.get("verdict"), "causal",
                                (_vd.get("verdict_evidence") or {}).get("channel_diag"),
                                _vd.get("verdict_evidence"))
        r["data"] = {**_d0, "after": {**_a0, "diff": _pub, **_vd}}

        # ★ 契约 §二 · mode=focus 的第二种失败：「发了但画面没变 ⇒ code=8」。
        #   ⛔ **绝不当成功**。因为这一款就是「不要用「动作发出去了」冒充
        #   「事情发生了」」的最后一道护栏，放过它就等于把该红的那部分白白赊了。
        #   分三态：False=确认没变→ code=8；None=没测出来→ code=5（不冒充「没变」，
        #   也不报成功；True=真变了→ 就是成功。
        if r.get("ok") and args.get("_need_diff") and _df.get("changed") is not True:
            if _df.get("changed") is False:
                _w8 = ("★ 字符已发出来，但**动作前后画面逐像素比对没变**（阈值 %d/255）。"
                       "本次**不报成功** —— 「键发出去了」不等于「字边确实出现了」。"
                       "可能原因：焦点不在目标窗、目标窗不在前台、对方拒收输入、或字段不可编辑。"
                       "先确认前台与焦点（ctl_windows 看当前前台），再重试。" % ACT_DIFF_STEP)
                r = env(code=gate.CODE_NO_CHANGE,
                        data=r.get("data"),
                        warning=_w8)
            else:
                r = env(code=gate.CODE_UNSTABLE,
                        data=r.get("data"),
                        warning=("★ 字符已发出来，但**没能比对画面**（%s）。"
                                 "本次不报成功 —— 不能把「我没看见」写成「我看见了没变」。"
                                 "请把目标窗到前台后重试，并用 ctl_screen 目测确认。" % _df.get("说明", "")))

        # ★ P0 神经层 · ②（窗Z）：变化**全在判定区之外** ⇒ changed_outside_target。
        #   「世界自己动了」（别的窗在动/桌面在动）**不许报成功** —— 这正是
        #   「变了」被拿去冒充「我做的」的那个洞。仍落 code=8 伞码（向后兼容）。
        if r.get("ok") and _vd.get("verdict") == "changed_outside_target":
            _d9 = r.get("data") or {}
            _ev9 = _vd.get("verdict_evidence") or {}
            _w9 = ("★ 画面**确实变了**，但变化区 %s 与判定区 %s 求交为空 ⇒ "
                   "`changed_outside_target`：**这是世界自己动的，不是我这次动作的后果**。"
                   "本次**不报成功**（code=8 伞码）。要么把 expect_region 声明到真正会变的"
                   "那块，要么先确认这个动作到底有没有落到目标上（ctl_screen 复核）。"
                   % (_ev9.get("changed_bbox"), _ev9.get("region")))
            r = env(code=gate.CODE_NO_CHANGE, data=_d9, warning=_w9)

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
        # 闭环判词**优先**于因果判词（它是独立判定器给的，更权威）
        _canon = _canon_verdict(verdict, "closed_loop",
                                (_vdetail.get("judge_tail") or
                                 _vdetail.get("snap_error") or ""),
                                _vdetail)
        if r.get("ok") and verdict != "satisfied":
            _code = gate.CODE_NO_CHANGE if verdict == "unsatisfied" else gate.CODE_UNSTABLE
            # ★ 顶层也带上 verdict/verify：成功路径是挂在 r["verdict"] 上的，
            #   失败路径若只放进 data，agent 就得记两个位置 —— 同一件事必须只有一个读法。
            return env(code=_code, verdict=verdict, verdict_canonical=_canon,
                       verify={"expect": exp, "watch": watch, "action_ok": True, **_vdetail},
                       # ★ P0-A：合并 r["data"] 而不是新造 —— 动作确实发出去了，
                       #   那些已经拿到手的证据（after.uid_state / after.diff）不能因为
                       #   闭环不过就丢掉；新填的五个键只覆写自己那部分。
                       data={**(r.get("data") or {}),
                             "rc": (r.get("data") or {}).get("rc"),
                             "action_ok": True, "verdict": verdict,
                             "expect": exp, **_vdetail},
                       warning="★ 动作执行了，但【独立验证没通过】：verdict=%s。"
                               "两个结果都在（动作 rc 在 data.rc、判定 verdict 在本条）—— 别只看一个。"
                               "expect=%s" % (verdict, exp))
    # ── ★ A2（2026-10-20 派单）：动作证据**独立成键 + 落盘** ──
    #   回执多一个顶层 `evidence` 键：{changed, bbox, area_ratio, verdict,
    #   before_png, after_png, crop_png, dir, 说明}，同一份内容写进
    #   work/_actions/<fid>/evidence.json（**与动作同目录**）。
    #   ⛔ **只加字段**：不碰 r["code"] / r["ok"] / r["data"] 的任何既有语义。
    #     刻意**独立成键**而不塞进 data.after —— data.after.verdict 是判据 36
    #     （文案与数据一致）在读的字段，往里加会改它的判定输入。
    try:
        _evx = _mk_action_evidence(action, hwnd, _df, _st, _vd, _before, _after, _canon)
        r["evidence"] = _evx
        # ★ W5-B：顶层也带一份统一判词（**只加键**；顶层 verdict 仍是闭环判词语义）。
        if _canon:
            r["verdict_canonical"] = _canon
        _write_action_evidence(_evx)
    except Exception as _e:
        r["evidence_error"] = "%s: %s" % (type(_e).__name__, _e)

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
                "class": win32gui.GetClassName(h) or "",
                # ★ A1（2026-10-20 派单）：pid 是窗口身份的一部分（hwnd 会被复用，pid 不会）
                "pid": int(tid) if tid else 0}
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


def _eid_recheck(hwnd: int, feat: dict, walk: dict):
    """身份复核：按**签发那次的遍历口径**重走一遍，看这个元素还在不在原位。

    返回 (当前节点, 补充说明)；找不到 ⇒ (None, 原因)。

    ★ 复核用「类型 + 名字 + 矩形」三重全等，**不用编号**：
      编号正是 D1 的病根，拿它复核等于「用错的尺子量错的布」。
    ★ 口径取自旁表里记的那次签发（roles/max_depth 一致）；
      max_nodes 只往上放宽 —— 放宽会多扫出节点，但**不会**让匹配失败
      （匹配靠特征不靠序号），而不放宽就有「签发时它在第 800 位、复核预算只有 600
      ⇒ 假失败」的风险（那正是被否决的方案①挖的坑）。
    """
    roles = tuple((walk or {}).get("roles") or ("all",))
    mdep = int((walk or {}).get("max_depth") or uia_tree.DEFAULT_MAX_DEPTH)
    mnod = max(int((walk or {}).get("max_nodes") or 0), uia_tree.FIND_MAX_NODES)
    try:
        # ★ 窗G3（A2 接线要求，复核这趟**显式取超集**）：复核必须 include_offscreen=True。
        #   签发那次（ctl_screen）固定用超集口径，复核若只扫视口内，就会把
        #   「签发时它在视口内、点之前滚了一下」误报成「元素不见了」——
        #   **假报元素消失**比点不中更坏。取超集只会多扫候选，不会让匹配失败：
        #   匹配靠「类型+名字+矩形」三重全等，不靠编号。
        nodes, trunc = uia_tree.walk(hwnd, roles=roles, max_nodes=mnod, max_depth=mdep, include_offscreen=True)
    except Exception as e:
        return None, ("重走元素树失败 %s: %s" % (type(e).__name__, e)), None
    want_rect = [int(x) for x in (feat.get("rect") or ())]
    hits = [n for n in nodes
            if n.get("type") == feat.get("type")
            and (n.get("name") or "") == (feat.get("name") or "")
            and [int(x) for x in (n.get("rect") or ())] == want_rect]
    note = ("（复核时元素树被 max_nodes 截断了 —— 扫到 %d 个就停了，判据在边界上）"
            % len(nodes)) if trunc else ""
    if not hits:
        return None, ("现在扫到 %d 个节点，没有『类型 %s / 名字 %s / 矩形 %s』"
                      "同时对得上的那个%s"
                      % (len(nodes), feat.get("type"), feat.get("name"), want_rect, note)), None
    # ★ 窗G3：把这趟复核树的**结构键表**带回去 —— 调用方要拿它算 uid
    #   （expect_uid 复核、动作后 after.uid_state 都用），**不必再走第二趟**。
    #   ⚠ 用**身份**（is）定位而不是 list.index 的**值相等**：一屏里完全相同的
    #   节点（同类型/同名/同矩形）能有好几个，值相等会命中**头一个**，位置就错了。
    idx = next(i for i, n in enumerate(nodes) if n is hits[0])
    ctx = {"struct_keys": _screen_struct_keys(nodes), "index": idx, "nodes": nodes, "口径": {"roles": list(roles), "max_nodes": int(mnod), "max_depth": int(mdep), "include_offscreen": True}}
    return hits[0], note, ctx


def _screen_uid_at(ctx, node) -> str:
    """用**复核那趟树**的结构键，给一个节点算出 uid（与 _screen_build 同口径）。

    ★ 口径说明：_screen_build 里 exempted = volatile and not watched（声明制压过豁免）；
      点击侧没有声明上下文，按 exempted = volatile 算 —— 这正是「没声明任何东西」那一档。
    ★ ctx 为空 / 结构键缺失 ⇒ 返回空串，调用方据此报「**无法判定**」而不是硬猜。
    """
    try:
        sk = (ctx or {}).get("struct_keys") or []
        i = int((ctx or {}).get("index") or -1)
        node = node or {}
        role = str(node.get("type") or "")
        label = str(node.get("name") or "").strip()
        if i < 0 or i >= len(sk) or not role:
            return ""
        volatile = _screen_is_volatile(role, label)
        return "%s@%s" % (sk[i], _screen_text_key(label, volatile))
    except Exception:
        return ""


def _click_after_state(hwnd: int, hit: dict, pre_ctx=None) -> dict:
    """动作后回读：把**被点的那个对象**在动作后的新一屏里落到哪一档。

    三档（§⑫）：OK = 结构与文字都没变；TEXT_CHANGED = 同一个对象、内容变了
    （**动作达成的证据**）；STALE = 结构变了 / 这个对象在新一屏里压根不在了
    （可能点错了对象）。读不到（走树失败、算不出 uid）⇒ `unknown`，
    并写清「**不等于动作成功**」—— **证据不足不是成功**。

    ★ 基线用**点之前那一趟树**（`_eid_recheck` 已经走过的复核树，pre_ctx），
      不另扫一遍：① 省一趟 walk；② 更要紧 —— 那一趟就是**点前那一瞬**的事实，
      比事后回忆靠谱。
    ★ 归因配对顺序（与 `_screen_uid_state_against` 同一套道理）：
      先找**同名字**的后继（同一个对象还在，只是字变了或没变）；
      找不到同名，说明它**连名字都改了** ⇒ 退到「同结构位置 + 矩形还压着」
      （SCREEN_SAME_SLOT_IOU）才算 TEXT_CHANGED —— 那是「同一个位置上的东西换了内容」；
      再不成立 ⇒ STALE（结构变了 / 对象没了 / 别的对象顶了上来）。
    """
    out = {"uid_state": "unknown", "screen_version": None, "uid": str(hit.get("uid") or ""),
           "eid": str(hit.get("eid") or ""), "动作后uid": "", "依据": "",
           "口径": "OK/TEXT_CHANGED/STALE 三档同 §⑫；unknown = 没读回来，**不等于动作成功**。"
                   "配对：先同名字，再同结构位置+矩形重叠（IoU≥%.2f），否则 STALE"
                   % SCREEN_SAME_SLOT_IOU}
    uid0 = str(hit.get("uid") or "")
    pre = pre_ctx or {}
    pre_nodes = pre.get("nodes") or []
    pre_sk = pre.get("struct_keys") or []
    # ── ★ 窗G3c · (b) 整屏变化清单。**先算它**，再算被点对象那一档 ——
    #   顺序不能反：changed_objects 只靠「点前那屏基线 vs 动作后这屏」，
    #   压根不需要知道被点对象的 uid。而 uid 只有**给了 expect_uid** 时才算得出来 ——
    #   早先把归因放在前面、没 uid 就直接 return，顺手把整屏清单也带没了
    #   （实测：不带 expect_uid 点一次，after 里 changed_objects 永远是空的）。
    pre_seen = _screen_seen_view(_screen_snap_get(int(hwnd)))
    try:
        #   _不发capture：这次只借它签一个 screen_version，不该往旁表里塞一张没人用的凭据。
        #   _不推进seen：动作内部的读不许改写「调用方上次看到的那一版」（见下面 _screen_snap_put_ex）。
        fresh = _screen_build(int(hwnd), {"top": 200, "cdp": False, "ocr": False,
                                          "_不发capture": True,
                                          "_不推进seen": True})
        out["screen_version"] = fresh.get("screen_version")
        out["新屏对象数"] = len(fresh.get("objects") or [])
        ch = []
        for o in (fresh.get("objects") or []) if pre_seen else ():
            stt = _screen_uid_state_against(
                pre_seen, o.get("uid"),
                {"label": o.get("label"), "role": o.get("role"),
                 "rect": o.get("rect")})
            if stt != "OK":
                ch.append({"label": o.get("label"), "uid": o.get("uid"),
                           "uid_state": stt})
        out["changed_objects"] = ch[:SCREEN_AFTER_CHANGED_CAP]
        out["changed_objects数"] = len(ch)
        out["changed_objects_口径"] = (
            ("拿**点击前**那一屏的基线（调用方上次 ctl_screen 看到的那版）"
             "给动作后的屏逐对象打三档，只收非 OK 的（OK=没变）；最多 %d 条。"
             "★ 这份回读**不推进**调用方的 since 基线 —— 随后再带 since 调 "
             "ctl_screen，仍能看到同样的变化。" % SCREEN_AFTER_CHANGED_CAP)
            if pre_seen else
            ("没有可比的基线（本进程 %d 秒内没扫过这扇窗，或基线已过期）"
             "⇒ 不给变化清单，**不是「这一屏没变化」。" % int(SCREEN_SNAP_TTL_SEC)))
    except Exception as e:
        #   ⚠ 不许静默：回读失败 = 「读不到」，调用方有权知道**为什么**读不到。
        #   静默吞掉的话，after 会长得跟「一切正常」一模一样 —— 那是最坏的一种坏法。
        out["screen_version"] = None
        out["changed_objects"] = []
        out["changed_objects_口径"] = ("动作后回读失败：%s: %s —— **读不到不等于没变化**"
                                      % (type(e).__name__, e))
    if not uid0:
        out["依据"] = ("点之前没算出 uid（没给 expect_uid，或走的不是 eid 复核路径），"
                       "没法给**被点对象**归那一档（整屏变化清单见 changed_objects）")
        return out
    if not pre_nodes:
        out["依据"] = "没有点之前那一趟树做基线，没法给被点对象归那一档"
        return out
    # ── 点前那一趟：把被点对象自己的自述抠出来（名字 / 矩形 / 结构位置）。
    pre_label, pre_rect, pre_slot = "", [], ""
    for i, n in enumerate(pre_nodes):
        u0 = _screen_uid_at({"struct_keys": pre_sk, "index": i}, n)
        if u0 != uid0:
            continue
        pre_label = str(n.get("name") or "")
        pre_rect = list(n.get("rect") or [])
        pre_slot = str((_screen_split_uid(u0) or [""])[0])
        break
    if not pre_label and not pre_slot:
        out["依据"] = "点前那趟树里找不到这个 uid（口径对不上），没法归因"
        return out
    # ── 点后那一趟：同口径重走一遍（与复核一样取超集，屏外的也算）。
    口 = hit.get("口径") or {}
    try:
        nodes, _t = uia_tree.walk(hwnd, roles=tuple(口.get("roles") or ("all",)),
                                  max_nodes=int(口.get("max_nodes") or uia_tree.FIND_MAX_NODES),
                                  max_depth=int(口.get("max_depth") or uia_tree.DEFAULT_MAX_DEPTH),
                                  include_offscreen=True)
    except Exception as e:
        out["依据"] = "动作后重走元素树失败：%s: %s" % (type(e).__name__, e)
        return out
    post_sk = _screen_struct_keys(nodes)
    post = []
    for i, n in enumerate(nodes):
        u1 = _screen_uid_at({"struct_keys": post_sk, "index": i}, n)
        if u1:
            post.append((u1, n))
    # ── ① 同名字的后继：它还在那儿，只是字变了或没变。
    same_name = [(u, n) for u, n in post if str(n.get("name") or "") == pre_label]
    if same_name:
        u1, n1 = same_name[0]
        out["uid_state"] = screen_uid_state(uid0, u1)
        out["动作后uid"] = u1
        out["依据"] = ("动作后仍有同名对象『%s』，uid 由 %s 变成 %s（结构位置%s）⇒ %s"
                       % (pre_label, uid0, u1,
                          "没变" if out["uid_state"] != "STALE" else "变了", out["uid_state"]))
        return out
    # ── ② 连名字都改了 ⇒ 只有「同结构位置 + 还压在同一块地方」才算换个内容。
    best, best_iou = None, 0.0
    for u, n in post:
        if str((_screen_split_uid(u) or [""])[0]) != pre_slot:
            continue
        iou = _screen_rect_iou(n.get("rect"), pre_rect)
        if iou > best_iou:
            best, best_iou = (u, n), iou
    if best is not None and best_iou >= SCREEN_SAME_SLOT_IOU:
        out["uid_state"] = "TEXT_CHANGED"
        out["动作后uid"] = best[0]
        out["依据"] = ("动作后原位置（矩形重叠 IoU=%.2f）上的对象从『%s』变成『%s』⇒ "
                       "同一个对象换了内容" % (best_iou, pre_label, str(best[1].get("name") or "")))
        return out
    # ── ③ 兜底：结构变了 / 对象不见了 ⇒ STALE（可能点错了对象）。
    out["uid_state"] = "STALE"
    out["依据"] = ("动作后这一屏里既没有同名对象『%s』，原结构位置 %s 上也没有压着的对象"
                   "（最高 IoU=%.2f）⇒ 结构变了，可能点错了对象"
                   % (pre_label, pre_slot, best_iou))
    return out

def _eid_refuse_missing(eid, how, entry) -> dict:
    """旁表查无此号的**统一处方**。

    措辞里必须带「不存在」和「重新」两个词 ——
      bench.py::b11_minimized_window_not_misread 第二段（可见窗口 + 假 eid）
      断言的正是这两个词；改成「必须带 from_capture」会把那条覆盖面顶掉。
    """
    if how == "旁查":
        return env(code=gate.CODE_STALE_CAPTURE,
                   warning=("元素 %s 确实存在，但它是在**另一次**观测里签发的，"
                            "eid 不能跨观测单独用 —— 把那次 ctl_find/ctl_tree 返回的 "
                            "capture_id 用 --from_capture 一起传回来。"
                            "想重新定位就重新 ctl_find 一次，拿新的 capture_id。" % eid))
    if how == "空快照":
        # ★ 窗G3b：文案必须**点名这次观测是谁发起的**。
        #   改前只说「ctl_tree 默认是轻量模式」—— 对从 ctl_screen 来的调用方是误导：
        #   他压根没用过 ctl_tree，也没少传什么参数，照着改只会越改越懵。
        来自 = str((entry or {}).get("工具") or "") or "某个工具"
        if 来自 == "ctl_tree":
            缘由 = ("ctl_tree 默认是轻量模式，只给文本树、不把节点明细交出来，"
                    "拿树文本里的 e1 来点是拿不到钥匙的（⚠️ 别怪号不存在）。")
            出路 = ("① 用 ctl_find --hwnd <hwnd> --query <要找的字>（默认就带命中明细）；"
                    "② 或者 ctl_tree 同一窗口再来一次，带 detail=true，"
                    "然后用它的 capture_id + eid + --from_capture。")
        elif 来自 == "ctl_screen":
            缘由 = ("ctl_screen 这次观测没有元素明细（它交的是 objects/texts，"
                    "不是原始节点表），所以里面没有 eid 可用 —— "
                    "⚠️ **这不是你少传了什么参数**，别去调 ctl_tree 的开关。")
            出路 = ("① 重新 ctl_screen 一次，拿新的 capture_id 与 objects[].eid 一起点；"
                    "② 或者改用 ctl_find --hwnd <hwnd> --query <要找的字>（默认带命中明细）。")
        else:
            缘由 = ("这次观测没有元素明细（来源：%s），所以里面根本没有 eid 可用 —— "
                    "⚠️ 别怪号不存在。" % 来自)
            出路 = ("① 重新 ctl_find --hwnd <hwnd> --query <要找的字>（默认就带命中明细）；"
                    "② 或者带 detail=true 的那次观测（ctl_tree/ctl_screen 都行）重来。")
        return env(code=gate.CODE_STALE_CAPTURE,
                   warning=("这次观测**没有元素明细**（来自 %s），"
                            "所以里面根本没有 eid 可用。%s"
                            "两条出路：%s"
                            "（顺便一说：坐标路径不受影响 —— "
                            "ctl_find 给的 center 原样发 --xy 就行。）"
                            % (来自, 缘由, 出路)))
    if not _eid_db_path():
        return env(code=gate.CODE_INTERNAL,
                   warning=("元素 %s 查不到：eid 旁表没有落盘位置（闸门状态目录定位失败）"
                            "—— 这不是你操作的问题，别重试，先报告用户" % eid))
    return env(code=gate.CODE_STALE_CAPTURE,
               warning=("元素 %s 不存在 —— 它不是任何一次观测签发出来的号，"
                        "或者那次观测已经超过 %g 秒了。"
                        "⚠️ eid 是**那次快照的取件钥匙**，不是元素本身："
                        "重新 ctl_find --query <要找的字> 拿新的 capture_id 和 eid，"
                        "再带着 --from_capture 一起点。"
                        % (eid, float(gate.CAPTURE_TTL))))


def _click_eid_legacy(hwnd: int, eid, args: dict):
    """★ 回退开关 AGENTCTL_EID_STRICT=0 专用：**逐字复刻改前的旧行为**。

    这条路径**带着 D1 的病**（重走一遍、按新编号取坐标），只为了 T1 全线验收期间
    不让依赖旧流程的脚本全线报废。默认**不启用**；拔除条件见 _eid_strict() 的注释。
    """
    try:
        st = uia_tree.window_state(hwnd)
    except Exception:
        st = {}
    if st.get("最小化") or not st.get("根矩形宽"):
        return env(code=gate.CODE_NO_CTRL,
                   warning="点 eid 之前窗口不可读（%s），先把窗口叫醒"
                           % st.get("说明", "未知"))
    try:
        nodes, _ = uia_tree.walk(hwnd, roles=("all",))
    except Exception as e:
        return env(code=gate.CODE_INTERNAL,
                   warning="重新解析 eid 失败 %s: %s" % (type(e).__name__, e))
    hit = None
    for n in nodes:
        if n["eid"] == str(eid):
            hit = n
            break
    if hit is None:
        return env(code=gate.CODE_NO_CTRL,
                   warning="元素 %s 现在不存在了（扫到 %d 个节点）。界面变了，重新 ctl_find 再点。"
                           % (eid, len(nodes)))
    want = args.get("expect")
    if want and str(want).strip() and str(want).strip() not in (hit["name"] or ""):
        return env(code=gate.CODE_NO_CTRL,
                   warning="元素 %s 现在叫『%s』，不是你看到的『%s』—— 不点"
                           % (eid, hit["name"], want))
    _en = hit.get("enabled", None)
    if _en is False:
        return env(code=gate.CODE_NO_CTRL,
                   warning="元素 %s（%s）是禁用状态，不点" % (eid, hit["name"]))
    walk = {"roles": ["all"], "max_nodes": uia_tree.FIND_MAX_NODES,
            "max_depth": uia_tree.DEFAULT_MAX_DEPTH}
    tok = _issue_capture_from_tree(hwnd, {"命中": [hit]}, walk)
    if not tok:
        return env(code=gate.CODE_INTERNAL,
                   warning="eid 解析到了，但观测凭据签发失败 —— 宁可不点，也不点一个绑不住的动作")
    return {**args, "xy": list(hit["center"]), "from_capture": tok,
            "__resolved__": True,
            "_eid_hit": {"eid": eid, "name": hit["name"], "type": hit["type"],
                         "rect": hit["rect"], "enabled": _en, "capture_id": tok,
                         "来源": "★ 旧行为（AGENTCTL_EID_STRICT=0）：重新编号取坐标，带 D1 缺陷"},
            **({"_enabled_unknown": True} if _en is None else {})}

def _click_by_name(hwnd: int, name: str, args: dict):
    """--name 走新线（uia_tree.find），并与 eid **共用同一套护栏**。

    改前 --name 原样透传给 act.py 的老扫描（find_control，max_depth=10），
    而 Chromium 页面内容普遍深于 10 层、外壳很浅 ⇒ 只可能命中外壳
    （D2 的真因是**深度不够**，不是「看不见页面」）。

    ⚠ 回退开关 AGENTCTL_CLICK_NAME_LEGACY=1 ⇒ 照旧透传给 act.py（返回 None）。
      老线的 name 路径**优先 UIA.Invoke**（不发鼠标事件、不依赖坐标），
      对某些控件比坐标更稳，一刀切砍掉是功能倒退；
      但**默认不再走**（网页场景下它只会给出误导性的「找不到」）。
    """
    if os.environ.get("AGENTCTL_CLICK_NAME_LEGACY", "0") == "1":
        return None, None
    try:
        res = uia_tree.find(hwnd, name, max_nodes=uia_tree.FIND_MAX_NODES,
                            limit=40, detail=False)
    except Exception as e:
        # ★ 新线**跑不起来**（UIA 不可用 / 窗口读不到 / COM 抽风）时**退回老线**，
        #   而不是硬报 15。理由：这是基础设施故障，不是「找不到这个控件」；
        #   退回老线 = 退回改前行为（不倒退），硬报 15 则会让一条本来能点的控件点不了。
        #   ⚠️ 但必须**如实说**：老线在网页场景本来就只看得见外壳（D2），别让调用方误以为它更全。
        return None, ("⚠️ 新元素线读不了这个窗口（%s: %s），本次 --name 已**退回老线**"
                      "（act.py 的 find_control，深度上限 10）—— "
                      "老线在网页上只看得见浏览器外壳；页面元素请改用 "
                      "ctl_find --hwnd %s --query <要找的字> 再按 center 发 --xy。"
                      % (type(e).__name__, e, hwnd))
    hits = res.get("命中") or []
    if not hits:
        return (env(code=gate.CODE_NO_CTRL,
                   data={"扫过节点": res.get("扫过节点")},
                   warning=("在新元素树里找不到叫『%s』的元素（扫过 %s 个节点）。"
                            "三件事分清楚："
                            "① 窗口是不是最小化/不可读 —— 看 ctl_windows 的 rect 与 ctl_status；"
                            "② 名字是不是真这么写 —— ctl_find --hwnd %s --detail "
                            "看真实名字与类型；"
                            "③ 换更短的词再找一次（UIA 名字常带快捷键后缀）。"
                            "⛔ 别退回老线：老线深度上限只有 10，页面元素它本来就看不见。"
                            % (name, res.get("扫过节点"), hwnd)),
                   blind_spots=["找不到元素**不能**据此说界面上没有这个东西 —— "
                                "UIA 看不见的一律瞎：Canvas 自绘、游戏画面、"
                                "没开无障碍的 Electron"]), None)
    if len(hits) > 1:
        lines = []
        cands = []
        for i, h in enumerate(hits[:10]):
            en = h.get("enabled")
            st = "可点" if en else ("禁用" if en is False else "启用性未知")
            lines.append("  %d. %s | %s | rect=%s | %s"
                         % (i + 1, (h.get("name") or "")[:40], h.get("type"),
                            h.get("rect"), st))
            cands.append({"名字": h.get("name"), "类型": h.get("type"),
                          "rect": h.get("rect"), "启用": en})
        return (env(code=gate.CODE_NO_CTRL,
                   data={"命中数": len(hits), "候选": cands},
                   warning=("名字『%s』有 **%d 个命中**，不猜、不替你挑 —— 请二选一：\n%s\n"
                            "① 缩小范围：ctl_find --hwnd %s --query <更长的词>\n"
                            "② 或者直接按上面某一条的 center 发 --xy"
                            "（原样传，屏幕物理像素，不要减窗口原点）"
                            % (name, len(hits), "\n".join(lines), hwnd))), None)
    hit = hits[0]
    en = hit.get("enabled", None)
    if en is False:
        return (env(code=gate.CODE_NO_CTRL,
                   warning="元素『%s』（%s）是禁用状态，不点"
                           % (hit.get("name"), hit.get("type"))), None)
    # name 路径的这次 find **本身就是一次新鲜观测** —— 就地签发并记旁表，
    # 于是它和 eid 路径共用「观测 → 绑定 → 点」同一套护栏（不是绕过它）。
    walk = {"roles": ["all"], "max_nodes": uia_tree.FIND_MAX_NODES,
            "max_depth": uia_tree.DEFAULT_MAX_DEPTH}
    tok = _issue_capture_from_tree(hwnd, {"命中": [hit]}, walk)
    if not tok:
        return (env(code=gate.CODE_INTERNAL,
                   warning="按名字定位到了，但观测凭据签发失败 —— 宁可不点，也不点一个绑不住的动作"), None)
    return ({**args, "xy": list(hit["center"]), "from_capture": tok,
             "__resolved__": True,
             "_eid_hit": {"eid": None, "name": hit.get("name"), "type": hit.get("type"),
                          "rect": hit.get("rect"), "enabled": en, "capture_id": tok,
                          "来源": "ctl_find 唯一命中（新线）"},
            **({"_enabled_unknown": True} if en is None else {})}, None)



# ── 帧过期（TTL）· 0 号证据链最小裁判的第一步（2026-10-18，三方商议 §㊾）──────
# 为什么需要：screen_version 只能抓住「画面变了」；抓不住「你拿着一张十分钟前的
# 旧帧去点」——画面没变、版本没变，但**你的判词会挂在一张早就不代表现在的帧上**。
# ZCode 原话：证据必须绑 frame_id + TTL，否则判词对不上账。

# ── 输入/事件通道判词（2026-10-18 §㊾ 的 A 项）─────────────────────────────
# 治的病：`code=8`（发出去了但画面没变）现在**分不清**是哪种原因——
#   ① 根本没点到目标（被挡/前台被别人抢/坐标落在别的窗）  ← 输入通道的问题
#   ② 点到了，但界面本来就不该有可见变化（焦点/光标/延迟渲染） ← 感知判词的问题
# 所以发送前先把输入侧事实记下来，随回执一起返回 input{...} + input_verdict。
def _input_snapshot(hwnd: int, xy=None):
    """抓发送前的输入侧事实：前台、光标、落点归属。**失败一律 None**（不假装知道）。"""
    snap = {"fg_ok": None, "hit_ok": None, "cursor": None,
            "fg_hwnd": None, "hit_hwnd": None}
    try:
        import ctypes                                        # noqa: PLC0415
        u32 = ctypes.windll.user32
        fg = int(u32.GetForegroundWindow() or 0)
        snap["fg_hwnd"] = fg
        snap["fg_ok"] = (fg == int(hwnd))
        pt = ctypes.wintypes.POINT()
        if u32.GetCursorPos(ctypes.byref(pt)):
            snap["cursor"] = [int(pt.x), int(pt.y)]
    except Exception:
        pass
    try:
        if xy:
            hit = _win_at_point(float(xy[0]), float(xy[1]))
            if hit is not None:
                snap["hit_hwnd"] = int(hit)
                snap["hit_ok"] = (int(hit) == int(hwnd))
    except Exception:
        pass
    if snap["hit_ok"] is False or snap["fg_ok"] is False:
        verdict = "not_target" if snap["hit_ok"] is False else "foreground_blocked"
    elif snap["fg_ok"] is True:
        verdict = "accepted"
    else:
        verdict = "unknown"
    snap["verdict"] = verdict
    return snap


def _frame_ttl_ms(args: dict) -> int:
    """帧有效期（毫秒）：调用方 > 环境变量 > 默认 30000。≤0 表示不判过期。"""
    for raw in (args.get("frame_ttl_ms"), os.environ.get("AGENTCTL_FRAME_TTL_MS")):
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return int(float(str(raw).strip()))
        except Exception:
            continue
    return 30000


def t_click(args: dict) -> dict:
    hwnd = int(args.get("hwnd") or 0)
    # ★ 窗G3：点前那一趟复核树的上下文（nodes/struct_keys/口径）。
    #   只有 eid 严格路径才有；name 路径与老线兜底留 None ⇒ after 如实报「没法归因」。
    rctx = None

    # ★★ T1 窗2 · D1 治本（2026-10-14）。这一段整块重写，理由逐条：
    #   改前是「拿 eid 重新走一遍树、按**新编号**取坐标」——
    #     而三条出口的遍历口径本来就不同（ctl_tree = interactive/text + 600、
    #     ctl_find = all + 3000、t_click = all + 600）⇒ **同一个 eid 必然指不同节点**。
    #     这不是抖动，是确定性错位：窗口完全静止也照样点错。
    #   改后是「eid 只当**取件钥匙**：按当初那张快照的元素特征取回坐标，
    #     再按同一口径重走一遍，复核它还在不在原位」。
    #   ⛔ 不引入 tree_sha256：签发侧只算命中那几十条、校验侧走全树 ⇒ 指纹永远不等
    #     ⇒ 永远 code=12 ⇒ 等于造一个「怎么点都点不了」的新坑。
    eid = args.get("eid")
    user_xy = args.get("xy")
    name = args.get("name")
    _given = {"eid": eid, "name": name, "xy": user_xy}
    _locators = [k for k in ("eid", "name", "xy") if _given[k]]

    # Q3 裁定：--eid 与 --xy 同时给 ⇒ **拒绝**。
    #   改前 eid 分支静默覆盖 xy —— 那是一条「以为点 A、实际点 B」的暗门：
    #   调用方以为自己传了坐标兜底，其实坐标被扔了。
    #   code=5（契约：「坐标/前台/窗口稳定性拒绝」）：本次拒绝的正是**坐标路径**；
    #   4 的契约含义是「找不到控件」，在这里是假的（控件找得到，是参数打架）。
    if eid and user_xy:
        return env(code=gate.CODE_UNSTABLE,
                   warning=("--eid 与 --xy 只能二选一：eid 说的是「上次看到的那批里的第几个」，"
                            "xy 说的是「屏幕上的这个点」，两个都传时没法判断你真正想点哪个，"
                            "所以这次**一个都没点**。"
                            "① 要点元素：只留 --eid + --from_capture；"
                            "② 要点坐标：只留 --xy + --from_capture。"
                            "（另：--xy 是**屏幕物理像素**，与 ctl_find 的 center 同口径，"
                            "原样传，不要减窗口原点。）"))
    if name and (eid or user_xy):
        return env(code=gate.CODE_UNSTABLE,
                   warning=("--name / --eid / --xy 三者只能选一个来定位，"
                            "这次同时收到了 %s ⇒ 一个都没点。"
                            % "、".join(_locators)))

    if eid and _eid_strict():
        # ── ①窗口可读性。**顺序不许动** —— 放最前面是 b11-A 的覆盖面
        #   （最小化窗口必须先报「不可读/叫醒」，而不是先报租约或 eid 不存在）。
        #   注意 window_state 是**取事实**、不是闸门：它不消耗、不改状态，
        #   且「窗口最小化」这个事实比「你有没有租约」更值得先说 —— 叫醒窗口是
        #   所有后续动作的前提，先说它 agent 才知道下一步干什么。
        try:
            st = uia_tree.window_state(hwnd)
        except Exception:
            st = {}
        if st.get("最小化") or not st.get("根矩形宽"):
            return env(code=gate.CODE_NO_CTRL,
                       warning="点 eid 之前窗口不可读（%s），先把窗口叫醒"
                               % st.get("说明", "未知"))

    if eid and _eid_strict():
        # ── ②按 capture_id 从旁表取回「当初那批」里这个 eid 的元素特征。
        #   这一步**只读一个本地 json**，是纯事实查询，不消耗、不改状态 ——
        #   所以它排在闸门**前面**：bench b11-B 断言的正是「可见窗口 + 假 eid」
        #   要报「不存在/重新」，而那次调用根本没带 actor。若把租约闸放到它前面，
        #   b11-B 会被一条「没带 actor」顶掉（实测踩过，已回退到现在的顺序）。
        tok = args.get("from_capture") or args.get("capture_id")
        feat, entry, how = _eid_lookup(tok, eid)
        if feat is None:
            return _eid_refuse_missing(eid, how, entry)

    # ── ③通行证 + 租约。到这里为止只做过「取事实」（窗口状态、本地 json 查询），
    #   下面才是**会因基础设施故障而误报**的活：check_capture、重走元素树、uia_tree.find。
    #   不先过闸，「你没通行证」就会被「读元素树失败」盖住 ⇒ agent 白跑两趟
    #   （tests 目录下的 s3_accept_gapfix.py，其 G2/G3 断言的就是这个顺序：
    #     9 先于 10 先于别的）。
    err = _act_precheck(args, "B")
    if err:
        return err

    if eid and _eid_strict():
        # ── ④capture 四重基础校验（窗口 / 位置 / 前台 / TTL）。
        #   ⛔ **不传 tree_sha256**：那正是被否决的方案①（签发算命中、校验走全树，
        #     指纹永远不等 ⇒ 永远 code=12）。
        #   与 _bind_capture 同一口径（hwnd + rect + foreground），图像比对本就不参与。
        if tok:
            try:
                gate.check_capture(tok, hwnd=hwnd, rect=_win_rect(hwnd),
                                   foreground=_is_foreground(hwnd))
            except gate.GateDenied as e:
                return env(code=e.code,
                           warning="%s —— 重新 ctl_find 拿新的 capture_id 再点" % e.why)

        # ── ⑤身份复核（按签发口径重走一遍，特征三重全等，**不看编号**）。
        cur, why, rctx = _eid_recheck(hwnd, feat, (entry or {}).get("walk"))
        if cur is None:
            return env(code=gate.CODE_STALE_CAPTURE,
                       warning=("元素 %s『%s』已经不在原位了 —— %s。"
                                "重新 ctl_find --query <要找的字> 拿新的 capture_id 重新定位，"
                                "不要拿旧号硬点。" % (eid, feat.get("name"), why)))

        # ★ 窗G3 · expect_uid：eid 说「上次那批里的哪个」，uid 说「还是不是那个对象」。
        #   两者**不许互当前别名**：eid 会被重新编号，uid 是结构+文本的指纹。
        #   给了 expect_uid 就**严格执行**：本屏重算一次，对不上就拒（STALE_SCREEN 语义）。
        expect_uid = str(args.get("expect_uid") or "").strip()
        expect_verdict = "未给"
        cur_uid = ""
        if expect_uid:
            cur_uid = _screen_uid_at(rctx, cur)
            if not cur_uid:
                判 = {"expect_uid": expect_uid, "判定": "无法判定", "重算uid": "", "说明": "复核树没给出结构键，算不出 uid —— 不点"}
                return env(code=gate.CODE_STALE_CAPTURE, warning=("给了 expect_uid=%s，但这趟复核取不到结构键，**无法判定是不是同一个对象** —— 宁可不算数也不点。" % expect_uid), data={"expect_uid_校验": 判})
            expect_verdict = "匹配" if cur_uid == expect_uid else "不匹配"
            口 = "uid = struct_key@text_key（与 ctl_screen 同口径）；不匹配 ⇒ 对象已变（STALE_SCREEN）"
            args = {**args, "_expect_uid_校验": {"expect_uid": expect_uid, "重算uid": cur_uid, "判定": expect_verdict, "口径": 口}}
            if expect_verdict != "匹配":
                return env(code=gate.CODE_STALE_CAPTURE, warning=("这个元素现在算出来的 uid 是 %s，与你给的 expect_uid %s 不是同一个 —— **对象已经变了**（STALE_SCREEN），这次**一个像素都没点**。建议重扫一次 ctl_screen，拿新的 uid/eid 再点。" % (cur_uid, expect_uid)), data={"expect_uid_校验": args["_expect_uid_校验"]})
        want = args.get("expect")
        if want and str(want).strip() and str(want).strip() not in (cur.get("name") or ""):
            return env(code=gate.CODE_NO_CTRL,
                       warning="元素 %s 现在叫『%s』，不是你看到的『%s』—— 不点"
                               % (eid, cur.get("name"), want))

        # ── ⑥S2b 三态守卫（False 拒 / None 放行但三通道留痕 / True 正常）。
        _en = cur.get("enabled", None)
        if _en is False:
            return env(code=gate.CODE_NO_CTRL,
                       warning="元素 %s（%s）是禁用状态，不点" % (eid, cur.get("name")))
        args = {**args, "xy": list(cur["center"]),
                "_eid_hit": {"eid": eid, "name": cur.get("name"), "type": cur.get("type"),
                             "rect": cur.get("rect"), "enabled": _en, "capture_id": tok,
                             "来源": "capture 旁表 + 身份复核",
                             "uid": cur_uid,
                             "口径": (rctx or {}).get("口径") or {},
                             "expect_uid_校验": args.get("_expect_uid_校验"),},
                **({"_enabled_unknown": True} if _en is None else {})}
    elif eid:
        got = _click_eid_legacy(hwnd, eid, args)
        if not got.get("__resolved__"):
            return got
        args = got
    elif name:
        got, name_warn = _click_by_name(hwnd, str(name), args)
        if got is None:
            # 显式老线兜底（回退开关 / 新线跑不起来）：argv 里才带 --name。
            # ⚠️ 退回**一定**带 warning —— 不许让调用方以为「名字在新线上找到了」。
            if name_warn:
                args = {**args, "_name_fallback_warn": name_warn}
        elif not got.get("__resolved__"):
            return got                   # _click_by_name 直接给了错误信封
        else:
            args = got

    # ★ P0-A · frame_id 凭据校验（契约 §二）：
    #   frame_id 必须与**当前** screen_version 对得上，不符 ⇒ code=12 且**零像素**。
    #   位置在这里（门禁之后、argv 组装之前）是有意为的：
    #   门禁先报「你没通行证」再报「你给的凭据已经过期」（项目一致口径）；
    #   凭据校验则必须早于**任何动作**，否则「零像素」就是一句空话。
    fid = str(args.get("frame_id") or "").strip()
    if fid:
        _cur = str((_SCREEN_SEEN.get(int(hwnd)) or {}).get("screen_version") or "")
        if not _cur:
            return env(code=gate.CODE_STALE_CAPTURE,
                       warning=("给了 frame_id=%s，但**本机还没有这个窗的屏版本基线**（没过 ctl_screen）。"
                               "无法判断凭据是新是旧 ⇒ 本次**一个像素都没点**。"
                               "先 ctl_screen --hwnd %d 拿到 screen_version，再把它当 frame_id 传回来。" % (fid, hwnd)))
        if fid != _cur:
            return env(code=gate.CODE_STALE_CAPTURE,
                       data={"frame_id_校验": {"frame_id": fid, "当前 screen_version": _cur,
                                             "判定": "不匹配"}},
                       warning=("frame_id=%s 与当前 screen_version=%s **对不上** ⇒ 画面已经变了（或屏片过期）。"
                               "本次**零像素**，一个都没点。"
                               "重新 ctl_screen --hwnd %d 拿新的 screen_version（它就是新的 frame_id），"
                               "核对坐标还在不在原位再点。" % (fid, _cur, hwnd)))
        # ★ 0 号最小裁判：版本对得上，还要判**帧龄**（画面没变 ≠ 这张帧还新鲜）
        _rec = _SCREEN_SEEN.get(int(hwnd)) or {}
        _t0 = float(_rec.get("t") or 0)
        _age_ms = int(max(0.0, time.time() - _t0) * 1000) if _t0 else 0
        _ttl = _frame_ttl_ms(args)
        if _ttl > 0 and _t0 and _age_ms > _ttl:
            return env(code=gate.CODE_STALE_CAPTURE,
                       data={"frame_id_校验": {"frame_id": fid, "当前 screen_version": _cur,
                                             "判定": "过期", "帧龄ms": _age_ms, "TTLms": _ttl}},
                       warning=("frame_id=%s 虽然与当前 screen_version 一致，但这张帧已经 **%d ms** 了"
                                "（TTL=%d ms）⇒ 判词不能挂在旧帧上，本次**零像素**。"
                                "重新 ctl_screen --hwnd %d 拿新帧再点；"
                                "确需放宽可传 frame_ttl_ms 或设 AGENTCTL_FRAME_TTL_MS。"
                                % (fid, _age_ms, _ttl, hwnd)))
        args = {**args, "frame_id_校验": {"frame_id": fid, "当前 screen_version": _cur,
                                       "判定": "匹配", "帧龄ms": _age_ms, "TTLms": _ttl}}

    argv = ["click", "--hwnd", str(hwnd)]
    if args.get("name") and not args.get("_eid_hit"):
        # 只有**老线兜底**才会走到这里：新线 name 路径的 argv 里不再有 --name，
        # 只有 --xy + --coord —— 它已经用同一套护栏解析出坐标了。
        argv += ["--name", str(args["name"])]
    if args.get("xy"):
        # ★ xy 是**屏幕物理像素**，与 ctl_tree / ctl_find 返回的 rect / center **同源同口径**
        #   （contract.tools.json → conventions.coordinates：「一律物理像素（DPI 感知），
        #     屏幕坐标」；act.py:1447 的 SetCursorPos(x,y) 点的是屏幕绝对坐标）。
        #   ⛔ **不要减窗口原点**、不要乘缩放比 —— 那样算出来的点仍会落在窗口矩形内，
        #     不会被 act.py:1813 的包含校验拦住 ⇒ 静默点错，比直接崩更难查。
        x, y = _xy(args["xy"])
        argv += ["--xy", f"{x},{y}", "--coord"]
    if "retry" in args:
        argv += ["--retry", str(int(args["retry"]))]
    if args.get("dry_run"):
        argv += ["--dry-run"]
    # ★ 接线单 §二 5：敏感窗终判在**spawn 之前**。命中 ⇒ code=3，argv 不会被送出去。
    #   位置在参数校验（eid/name/xy/frame_id）之后、组 argv 之后，但**在 _act 之前**——所以 argv 已组好了却没出去。
    _derr = _deny_sensitive_target(hwnd, "ctl_click", "点击")
    if _derr:
        return _derr
    # ★ A 项：发送**之前**抓输入侧事实（发送之后前台可能已经变了，抓了就没意义）
    _inp = _input_snapshot(hwnd, _xy(args["xy"]) if args.get("xy") else None)
    r = _act("click", args, argv)
    # ★ W5-B：输入判词（accepted / not_target / foreground_blocked）也并进统一块。
    #   它和因果判词、闭环判词讲的是**三件不同的事**，但以前散在三个地方，
    #   离线读判词的人得自己拼。统一块里各带各的 kind，**不混成一句**。
    if isinstance(r.get("verdict_canonical"), dict) and _inp.get("verdict"):
        r["verdict_canonical"]["input_verdict"] = _canon_verdict(
            _inp.get("verdict"), "input", _inp.get("fg_ok") is False and "前台不对" or "")
    # ★ P0-A：frame_id 校验结论在**动作之后立即**回显，不只在 ok 时。
    #   动作本身失败但**凭据是对的**（例如点击发出去了但画面没变 → code=8）时，
    #   这条信息正好最值钱 —— 只在成功时才看得见的设计，恰好在需要它的场合隐身。
    r["data"] = {**(r.get("data") or {}), "input": _inp}
    if args.get("frame_id_校验"):
        r["data"] = {**(r.get("data") or {}), "frame_id_校验": args["frame_id_校验"]}
    if r.get("ok") and args.get("_eid_hit"):
        # ★ 窗G3 · 动作后回读：「点下去了」不等于「点对了」。
        #   after{uid_state, screen_version}：文字变了 = TEXT_CHANGED（动作达成的证据）；
        #   结构变了/对象不见了 = STALE（可能点错对象）；什么都没变 = OK；
        #   **读不到就写 unknown，不许当成功**。
        #   基线 = 点之前那一趟复核树（rctx），不必另扫一遍，也更贴近「点前那一瞬」。
        #   ★ P0 神经层（窗Z）：这里**合并**而不是覆盖 —— `_act` 刚写进 after 的
        #     `diff`（前后快照比对）与 `verdict`（因果判词）不能被这一行吃掉。
        _a_old = (r.get("data") or {}).get("after") or {}
        r["data"] = {**(r.get("data") or {}),
                      "after": {**_a_old, **_click_after_state(hwnd, args.get("_eid_hit") or {}, rctx)}}
    if r.get("ok"):
        r["data"] = {**(r.get("data") or {}), "frame_before": None, "frame_after": None,
                     "note": "按控件名/eid 点击不依赖观测；走坐标时已校验 capture_id",
                     "点到": args.get("_eid_hit"),
                     "expect_uid_校验": args.get("_expect_uid_校验"),
                     # ★ P0-A：成功路径也要回显凭据校验结论 —— 拒绝路径在 data.frame_id_校验 里。
                     #   只在拒绝时报、成功时不报，那就是让调用方在成功下也看不到自己凭据到底比对过没。
                     "frame_id_校验": args.get("frame_id_校验"),
                     # ★ W2-C：让程序能自证坐标系，不必靠人记得 SOP 怎么写。
                     "坐标口径": "屏幕物理像素（与 ctl_find/ctl_tree 的 rect/center 同源；"
                                  "不要减窗口原点）"}
        if args.get("_enabled_unknown"):
            # ★ S2b：未知时**三个通道一起留痕**（DSH 放行 O-1：两个都挂）。
            #   ① data.enabled_unverified —— 机读，给程序判断
            #   ② blind_spots —— 项目口径「本次结论的盲区（不许隐藏）」（contract/tools.json:53），
            #      给人和模型看（docs/AGENT接口.md:127「必须一并转述」）
            #   ③ warning —— 最显眼处，提醒这次点击是在「不知道能不能点」下发出的
            #   措辞照项目口径写：**不据此断言它可用**。
            # 不动 ok / code：这是一次成功的动作，只是附带一条不确定提示
            #   （先例：core.py:479 的 t_brake 就是在 ok=True 上挂 warning）。
            _hit = args.get("_eid_hit") or {}
            _eid = _hit.get("eid") or _hit.get("name") or "元素"
            r["data"]["enabled_unverified"] = True
            r["blind_spots"] = list(r.get("blind_spots") or []) + [
                "元素 %s 的启用性未验证（UIA 读 IsEnabled 失败）—— 不据此断言它可用" % _eid]
            _w = ("元素 %s 的启用性未验证（UIA 读 IsEnabled 失败）⇒ 这次点击是在"
                  "「不知道能不能点」的状态下发出的；点完请核对界面是否真的响应了" % _eid)
            r["warning"] = _w if not r.get("warning") else (str(r["warning"]) + "；" + _w)
        if args.get("_name_fallback_warn"):
            _fw = str(args["_name_fallback_warn"])
            r["warning"] = _fw if not r.get("warning") else (str(r["warning"]) + "；" + _fw)
    return r


def _deny_sensitive_target(hwnd: int, tool: str, what: str):
    """动作时敏感窗终判（接线单 §二 5 的 core 侧接线）。命中返回错误信封，否则 None。

    ★ 位置是硬要求：**参数校验之后、spawn 之前**。
      命中却照旧 spawn 的话，act.py 那边虽然也会拒，但白跑一个子进程，
      更要命的是信封里读不出「到底是谁拦的」——拒绝发生在 spawn 之后就说不清了。

    ⛔ **fail-closed**：判定器自身出错也拒，不放行。
      “判不出来就当没事”在点击这事上等于直接放行——这正是敏感窗终判要阻的事。
    """
    _info = _win_info_for(hwnd)
    try:
        gate.deny_target(exe=_info.get("process") or None,
                         cls=_info.get("class") or None,
                         title=_info.get("title") or None)
    except gate.GateDenied as e:
        return env(code=e.code,
                   data={"blocked_by": "sensitive_window", "窗口": _info},
                   warning=("%s —— %s。本次**没发出任何动作**："
                            "拦在 spawn 之前，act.py 根本没被调起（argv 为空）。"
                            "这类窗口属于用户自己的东西（授权弹窗 / UAC / 系统设置页那一类），"
                            "点它等于替用户越权。" % (what, e.why)))
    except Exception as e:
        return env(code=gate.CODE_UNSTABLE,
                   data={"blocked_by": "sensitive_window_judge_failed", "异常": str(e)[:200]},
                   warning=("%s 的敏感窗终判**自身出错**（%s）。"
                            "本次**没发出任何动作**（fail-closed）—— "
                            "判定器不可用时不当没事。原因：%s"
                            % (what, type(e).__name__, e)))
    return None


def _type_focus_gate(args: dict, hwnd: int):
    """focus 模式的前台门禁（契约 §二）。成功返回 None。

    ★ 与 _allow_foreground 的差别（这里是故意的）：
      _allow_foreground（t_scroll / t_keys 那两处）是**无条件**要 allow_foreground，
      因为那两个动作本身就会主动置前，它们不需要区分「已在前台」。
      而 focus 模式只是**往前台焦点窗发字——目标窗已在前台时不会打断使用者，
      就不应该拒绝。所以这里先看前台，真的不在前台才要 allow_foreground。
    """
    if _is_foreground(hwnd):
        return None
    return _allow_foreground(args, "ctl_type", "focus 模式直接往前台焦点窗打字")


def t_type(args: dict) -> dict:
    hwnd = int(args.get("hwnd") or 0)
    mode = str(args.get("mode") or "element").strip().lower()
    if mode not in ("element", "focus"):
        return env(code=gate.CODE_NO_CTRL,
                   warning="mode 只认 %s，收到 %r。"
                           "① element（默认，回退兼容）：找 UIA 元素后对它打字。"
                           "② focus（新）：不找元素，直接往前台焦点窗逐字符发。"
                           % ("/".join(("element", "focus")), mode))
    argv = ["type", "--hwnd", str(hwnd), "--text", str(args.get("text") or "")]
    if args.get("force_send"):
        argv += ["--force-send"]
    if args.get("dry_run"):
        argv += ["--dry-run"]
    if mode == "focus":
        # ① 不找 UIA 元素：透传 --focus 给 act.py。
        #   这个开关的底层实现（逐字符 SendInput KEYEVENTF_UNICODE）归 Codex-2 的 act.py，
        #   core 这里只负责拿控参与失败口径。
        argv += ["--focus"]
        # ② 前台门禁：没前台 ⇒ code=5（可置前后解决）。
        err = _type_focus_gate(args, hwnd)
        if err:
            return err
        # ③ 强制快照差分：契约 §二的第二种失败「发了但画面没变 ⇒ code=8」
        #   就是靠这张快照判的，不启用它那条口径就根本不成立。
        args = {**args, "_need_diff": True}
    # ★ 接线单 §二 5：敏感窗终判在**spawn 之前**。命中 ⇒ code=3，argv 不会被送出去。
    _derr = _deny_sensitive_target(hwnd, "ctl_type", "打字")
    if _derr:
        return _derr
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

# ────────────── 滚动：方向口径的**唯一定义处**（2026-10-17 · 窗A1）──────────────
# ★ 为什么要提成常量：act.py 的 do_scroll 有两条分支，符号口径分散写在两处
#   （UIA 分支、滚轮分支）。任何一处被人改反都不会有测试响 ——
#   症状是「数值一点不动」，非常难查。口径必须**有且只有一处**能被读到。
#
# ★ 结论：源码里那两行看着像矛盾，其实**不是矛盾**（取证见 work/_A1_receipt.md 第二节）。
#     UIA 分支：amt = LargeIncrement if amount < 0 else LargeDecrement
#       ScrollAmount.LargeIncrement ≡ PageDown（向下）
#       ScrollAmount.LargeDecrement ≡ PageUp（向上）
#     滚轮分支：wheel(120 * amount)
#       mouseData 的**正值 = 向前滚 = 向上**
#     两条分支**各自独立**算出来的结论是同一条：**amount < 0 ⇒ 向下**。
#     滚轮分支上方那句注释「负值 = 向下」是对的。之所以容易被读成矛盾，
#     是因为把 Increment / Decrement 当成了「上 / 下」——
#     它们讲的是**滚动偏移的增减**，不是屏幕上的方向。
#
# ★ 本项目对外口径（契约、判据、回执一律按这一条）：
#     amount > 0 ⇒ 向上（看更靠前的内容）
#     amount < 0 ⇒ 向下（看更靠后的内容）
#     core.py **原样透传**，绝不取反 —— 取反就正好落进 act.py 注释里点名的反向 bug。
SCROLL_NEGATIVE_IS_DOWN = True


def _scroll_direction(amount: int) -> str:
    """把 amount 翻成中文方向词。口径只从 SCROLL_NEGATIVE_IS_DOWN 取，不就地重写符号。"""
    is_down = (amount < 0) == SCROLL_NEGATIVE_IS_DOWN
    return "向下" if is_down else "向上"


# ────────────── 按键：键名白名单（2026-10-17 · 窗A1）──────────────
# ★ 为什么不直接收任意 vk 整数：虚拟键码是个 16 位空间，其中绝大多数是
#   「切窗口 / 截屏 / 锁屏 / 重启」这类组合键。全量放行等于把它们直接递出去。
#   白名单只收**导航与编辑类**的键 —— 而它们正是「读长页面」场景真正需要的那些。
# ★ 反查表由白名单生成、不手写：同一份事实手写两遍，迟早对不上（老教训）。
KEY_WHITELIST = {
    "PageDown": 0x22, "PageUp": 0x21, "Home": 0x24, "End": 0x23,
    "ArrowUp": 0x26, "ArrowDown": 0x28, "ArrowLeft": 0x25, "ArrowRight": 0x27,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "Enter": 0x0D, "Tab": 0x09, "Esc": 0x1B, "Escape": 0x1B,
    "Space": 0x20, "Backspace": 0x08, "Delete": 0x2E, "Insert": 0x2D,
}
KEY_VK_WHITELIST = {vk: name for name, vk in KEY_WHITELIST.items()}
_KEY_NAMES = "、".join(sorted(KEY_WHITELIST))

# ★ 接线单 §二-1：**键名表只留真源 act.py 一份**。
#   改前 core 自带一份白名单并把 a~z/0~9 挡在外面 —— 真源明明认得的 v，
#   在 core 这道闸就被拒了，用户拿到的处方是「认不出」，而真源认得。
#   两份表早晚对不上，所以 core 不再维护键名表：只判「形状」与「组合」。
#
# ⛔ 数值 vk 仍要护栏（见 KEY_WHITELIST 上方），但**字母与数字键放行**：
#   接线单原话「KEY_WHITELIST 不再拦字母/数字」。
_VK_ALNUM = (set(range(0x30, 0x3A))        # 0-9
             | set(range(0x41, 0x5B))      # A-Z
             | set(range(0x61, 0x7B)))     # a-z
_VK_SAFE = set(KEY_WHITELIST.values()) | _VK_ALNUM

# ★ 接线单 §二-2：ctrl 自由布尔会放出 Ctrl+W / Ctrl+F4 这类破坏性组合。
#   只放行编辑类五件套：A 全选 / C 复制 / V 粘贴 / X 剪切 / Z 撤销。
CTRL_COMBO_ALLOWED = ("A", "C", "V", "X", "Z")
_KEYNAME_RE = re.compile(r"^[A-Za-z0-9]+$")   # 键名形状：字母 + 数字（v / PageDown / F13）
# ★ 数值的认定：**0x 前缀**，或**≥2 个数字**。
#   单个数字不算数值 —— 它是**键名**（真源 act.py 就是这么定义的）。
_NUMERIC_RE = re.compile(r"^(?:0[xX][0-9a-fA-F]+|[0-9]{2,})$")


def _keys_spec(keys, ctrl: bool) -> tuple:
    """把 keys 收成给 act.py 的 --vk 原文。

    返回 (原文 or None, 说明, 归一键名 or None, 错误信封 or None)。

    ★ 认不认得**不在这儿判**（那是真源的事）。core 只做两件必须自己做的事：
      ① 形状 —— 不是「键名」也不是「数值」的，连 spawn 都不值 → 直接 code=4；
      ② 组合 —— ctrl 只放行 §二-2 那五个（接线单 §二-3：拒绝也是 code=4）。
    ⛔ 所有拒绝都用 **code=4（NO_CTRL）**：「键名不合法 / 找不到控件」同语，
      不再混用 code=5（UNSTABLE，含义是「当前不稳定」）—— 读起来会让 agent 去重新观测。
    """
    s = str(keys or "").strip()
    if not s:
        return None, "", None, env(
            code=gate.CODE_NO_CTRL,
            warning="keys 不能为空 —— 传键名（如 v / PageDown / F5）"
                    "或数值（如 0x56 / 86）")

    _name = None
    _why = ""
    # ★ ★ 单个数字是**键名**，不是数值 —— 这个顺序纻倒会静默发错键：
    #   int("8", 0) = 8 = **0x08 = VK_BACKSPACE**；int("9", 0) = 9 = **0x09 = VK_TAB**。
    #   改前 agent 要打字面上的「8」，core 抢在真源前面当成数值，
    #   真源忠实地发了**退格**，且会报成功 —— 键发出去了、回执也绿的，
    #   只有回读才发现打的是退格而不是「8」。
    #   真源 act.py 的 KEY_NAME_TO_VK 里明确有 str(_d) = 0x30 + _d（act.py 的 0~9 循环），
    #   那就是**键名**；core 抢跑就把它截胡了。
    if _NUMERIC_RE.match(s):
        # ★ 不用 int(s, 0)：base=0 会把「012」当八进制拿成 10。按 16/10 分开写。
        _num = int(s, 16) if s[:2].lower() == "0x" else int(s, 10)
        if _num not in _VK_SAFE:
            return None, "", None, env(
                code=gate.CODE_NO_CTRL,
                warning=("vk=0x%X（%d）不放行 —— 这个 16 位空间里绝大多数是"
                         "切窗口 / 截屏 / 锁屏这类组合键，numeric 写法不放行。"
                         "字母/数字键可以直接写键名（如 v、F5、PageDown）。" % (_num, _num)))
        _why = "数值 %s ⇒ vk=0x%X" % (s, _num)
        _name = chr(_num) if 0x20 < _num < 0x7F else KEY_VK_WHITELIST.get(_num)
    else:
        if not _KEYNAME_RE.match(s):
            return None, "", None, env(
                code=gate.CODE_NO_CTRL,
                warning=("keys=%r 不是键名也不是数值 —— 键名只收「字母+数字」（v / A / F13 / PageDown），"
                         "数值写法收 0x41 / 86。本次**一个键都没发**。" % s))
        _why = "键名 %s（原样透传真源）" % s
        _name = s

    # ② 组合 allowlist —— 按键**字母**判，不看是键名写的还是数值写的。
    if ctrl:
        _letter = str(_name or "").upper()[:1]
        if _letter not in CTRL_COMBO_ALLOWED:
            return None, "", None, env(
                code=gate.CODE_NO_CTRL,
                warning=("Ctrl+%s 不在允许的组合内 —— 只放行 %s。"
                         "控制键自由布尔会放出 Ctrl+W 这类破坏性组合。"
                         "本次**一个键都没发**，也没拦截屏幕。"
                         % (s, " / ".join("Ctrl+" + c for c in CTRL_COMBO_ALLOWED))))
        _why += "（Ctrl 组合在允许清单内）"
    return s, _why, _name, None


def _resolve_vk(keys) -> tuple:
    """⛔ **已不再被任何地方调用（接线单 §二-1 后 t_keys 改用 _keys_spec）。**

    保留是为了不碰别人可能引用它，**不再是生效口径**。
    明写在这里是为了防一种具体的误导：看到这个函数就会当「键名白名单」在还很多人眼里有效，
    而真实是它一份都没有。真正的判定在上面的 _keys_spec：键名原样透传真源、数值才走护栏。
    删掉它需确认全仓无引用者；本单不做这个判定。
    """

    """把 keys（键名或 vk 数值）解成虚拟键码。返回 (vk, 说明)；解不出时 vk 为 None。

    收两种写法：白名单里的键名（"PageDown"），或该键的十进制 / 十六进制数值
    （"34" / "0x22"）。数值**必须落在白名单的码集合里**，理由见 KEY_WHITELIST 上方。
    """
    s = str(keys or "").strip()
    if not s:
        return None, "keys 不能为空 —— 传一个键名（如 PageDown）或白名单里的 vk 数值"
    if s in KEY_WHITELIST:
        return KEY_WHITELIST[s], "键名 %s" % s
    is_hex = s[:2].lower() == "0x"
    try:
        vk = int(s[2:], 16) if is_hex else int(s, 10)
    except ValueError:
        return None, ("keys 认不出：%r。可用键名 %s，"
                      "或它们的十进制 / 十六进制数值（如 34 / 0x22）。" % (s, _KEY_NAMES))
    if vk not in KEY_VK_WHITELIST:
        return None, ("vk=0x%X（%d）不在白名单里 —— 这个 16 位空间里绝大多数是"
                      "切窗口 / 截屏 / 锁屏这类组合键，不放行。可用键名：%s"
                      "（或它们的十进制 / 十六进制数值）。"
                      % (vk, vk, _KEY_NAMES))
    return vk, "数值 %s ⇒ vk=0x%X（%s）" % (s, vk, KEY_VK_WHITELIST[vk])


def _allow_foreground(args: dict, tool: str, what: str):
    """会**主动切前台**的动作必须显式放行，且放行要留痕。成功返回 None。

    ★ 为什么不给默认值：act.py 的 ensure_foreground 在目标不在前台时会
      **主动把窗口切到前台**。这是会打断使用者正在做的事的动作 ——
      静默做，等于控制系统替使用者抢注意力（本项目派单明令禁止）。
    ★ 判据用 `is True` 而不是「假值即拒」：字符串 "true"、数字 1 都不算数，
      只有 JSON 里的真布尔才放行 —— 否则 CLI 与 MCP 两条传输的口径会漂移。
    """
    hwnd = int(args.get("hwnd") or 0)
    if args.get("allow_foreground") is True:
        _ev("action_allow_foreground", tool=tool,
            payload={"hwnd": hwnd, "动作": what, "放行前是否已在前台": _is_foreground(hwnd)})
        return None
    return env(code=gate.CODE_UNSTABLE,
               warning=("%s 需要把目标窗口切到前台才发得出去（老脚本有「动作前自动置前」这一步）。"
                        "本次**没有**放行 ⇒ 一个键 / 一格滚轮都没发出去。"
                        "确实要打断当前操作，就显式传 allow_foreground=true —— "
                        "放行会写一条 action_allow_foreground 事件留痕。" % what))


def t_scroll(args: dict) -> dict:
    """滚动（B 级）。**不重新实现输入** —— 只做服务层的校验、口径标注与回报解析。

    ★ 为什么不自造滚轮：act.py 的 do_scroll 已有两条成熟分支
      （UIA ScrollPattern / SendInput 滚轮）＋ 模态前置 ＋ 通行证 ＋ 额度 ＋ 证据。
      服务层再写一份就等于出现第二个「判定逻辑定义处」—— 正是本项目反复出病的根。
    """
    hwnd = int(args.get("hwnd") or 0)
    amount = int(args.get("amount") or 0)
    if amount == 0:
        return env(code=gate.CODE_UNSTABLE,
                   warning=("amount=0 什么都不会动（滚轮增量为 0、Scroll 也不动），"
                            "而它的表现和「已经滚到头」一模一样 —— 正是老脚本注释里"
                            "点名过的那个难查故障。向上滚传正数、向下滚传负数，至少 ±1。"))

    # ── at：只做**事实校验**，不传给老脚本 ──────────────────────────────
    #   act.py 的 do_scroll 不收落点参数，实际落点是它自己按控件中心 / 窗口中心算的。
    #   这里提前拒掉明显越界的 at，是为了让调用方**别以为 at 决定滚到哪**；
    #   真正落在哪由老脚本的回报回填（见下面的「落点实际」与 blind_spots）。
    #   ⚠ 位置在闸门**前面**：取窗口矩形是纯事实查询（不消耗、不改状态），
    #     与 t_click 的①窗口可读性同一口径 —— 一个明显越界的落点不该先被
    #     「你有没有租约」顶掉，那只会让调用方白跑一趟拿通行证。
    at = args.get("at")
    if at:
        try:
            ax, ay = _xy(at)
        except Exception as e:
            return env(code=gate.CODE_UNSTABLE,
                       warning="at 要写成 x,y 两个整数（%s）" % e)
        rect = _win_rect(hwnd)
        if not rect:
            return env(code=gate.CODE_UNSTABLE,
                       warning="取不到 hwnd=%s 的窗口矩形 ⇒ 无法判断 at 越不越界。"
                               "先 ctl_windows 确认窗口还在（已关掉就重开）。" % hwnd)
        if not (rect[0] <= ax <= rect[2] and rect[1] <= ay <= rect[3]):
            return env(code=gate.CODE_UNSTABLE,
                       warning=("at (%d,%d) 不在 hwnd=%s 的窗口矩形 %s 内 ⇒ 拒绝，一格都没滚。"
                                "再强调一次：at 只是**校验用**，老脚本的滚动落点由它自己按"
                                "控件中心算，不听 at 的。要滚哪段内容请用 amount"
                                "（正=向上、负=向下）。" % (ax, ay, hwnd, rect)))

    err = _allow_foreground(args, "ctl_scroll", "滚动")
    if err:
        return err

    argv = ["scroll", "--hwnd", str(hwnd), "--amount", str(amount)]
    if args.get("dry_run"):
        argv += ["--dry-run"]
    r = _act("scroll", args, argv, scope="B")
    if not r.get("ok"):
        return r
    raw = str((r.get("data") or {}).get("raw") or "")
    m = re.search(r"光标置于 \((\d+),\s*(\d+)\)", raw)
    r["data"] = {**(r.get("data") or {}),
                  "方向": _scroll_direction(amount),
                  "amount": amount,
                  "落点请求": [ax, ay] if at else None,
                  "落点实际": [int(m.group(1)), int(m.group(2))] if m else None,
                  "走哪条分支": ("UIA_Scroll" if "走 UIA Scroll" in raw
                                else ("SendInput_滚轮" if "SendInput 滚轮" in raw else None))}
    r["blind_spots"] = list(r.get("blind_spots") or []) + [
        "at 只做了越界校验，实际落点由老脚本自算（本次落在 %s）；"
        "且「动作发出去了」不等于「内容滚到位了」—— 滚完自己核对一眼。"
        % (r["data"]["落点实际"] or "回报里回填不出，见 data.raw")]
    return r


def t_keys(args: dict) -> dict:
    """按键（C 级）。**键名原样透传真源**（接线单 §二-1）＋ ctrl 组合 allowlist（§二-2）。

    ★ 不再重新实现输入，也**不再自带一份键名表**：认键名是真源 act.py 的事。
      改前 core 的白名单拦字母，真源认得的 v 到了 core 这里就被拒了。
    ★ 老脚本 do_keys 只有 ctrl 一路组合（shift / alt 要改 act.py，越界）。
      所以契约的 modifiers 只声明 ctrl —— **不声明一个永远报错的参数**。
    """
    hwnd = int(args.get("hwnd") or 0)
    _spec, why, _kname, kerr = _keys_spec(args.get("keys"), bool(args.get("ctrl")))
    if kerr:
        return kerr
    times = int(args.get("times") or 1)
    if not 1 <= times <= 50:
        return env(code=gate.CODE_UNSTABLE,
                   warning="times 取 1..50（收到 %d）—— 连按上百次多半是调用方的循环写错了。"
                           % times)

    err = _allow_foreground(args, "ctl_keys", "按键")
    if err:
        return err

    argv = ["keys", "--hwnd", str(hwnd), "--vk", _spec, "--times", str(times)]
    if args.get("ctrl"):
        argv += ["--ctrl"]
    if args.get("scan_code"):
        argv += ["--scan-code"]
    if args.get("dry_run"):
        argv += ["--dry-run"]
    # ★ 接线单 §二 5：敏感窗终判在**spawn 之前**。命中 ⇒ code=3，argv 不会被送出去。
    err = _deny_sensitive_target(hwnd, "ctl_keys", "按键")
    if err:
        return err
    r = _act("keys", args, argv, scope="C")
    # ★ 接线单 §二-3：真源 resolve_vk 认不出键名时走 rc=2（act.py:3043）。
    #   改前这个码不在 core 的表里 ⇒ 落到「未知返回码」一支，报 code=5（UNSTABLE）。
    #   而 code=5 的处方是「重新观测」 —— 对「键名写错了」这件事没任何意义 ⇒ 跟着转圈。
    #   改成 code=4（NO_CTRL），与「找不到控件 / 键名不合法」同语。零新码。
    if not r.get("ok") and (r.get("data") or {}).get("rc") == 2:
        _raw = str((r.get("data") or {}).get("raw") or "").strip()
        r["code"] = gate.CODE_NO_CTRL
        r["ok"] = False
        r["warning"] = ("keys=%r，真源 act.py 也认不出这个键名 ⇒ 本次**一个键都没发**。"
                        "可用键名：a~z / A~Z / 0~9 / F1~F12 / Space·Enter·Tab·Esc·Backspace·Delete·Insert"
                        "·Home·End·PageUp·PageDown / 方向键；或数值 0x41 / 65。%s"
                        % (args.get("keys"), ("\n原文：" + _raw[:200]) if _raw else ""))
    if r.get("ok"):
        r["data"] = {**(r.get("data") or {}),
                     "vk": _spec,
                     "键名": _kname,
                     "解析": why,
                     "ctrl": bool(args.get("ctrl")),
                     "次数": times}
        r["blind_spots"] = list(r.get("blind_spots") or []) + [
            "按键发出去不等于界面响应了 —— 老脚本的证据是前后截图，"
            "没有 UI 变化时它会报「页面无变化」，那种情况别当成功。"]
    return r


def t_browser(args: dict) -> dict:
    """浏览器 CDP（包壳调 cdp.py）。★ 浏览器场景**优先走它，不要看像素**（§21 第⑤档）。

    ⚠️ 2026-10-02 验收抓到的 bug：原来这条**没做通行证前置检查**，无证时报的是
       code=4（老脚本自己报错）而不是 code=9（未授权）—— 错误码会误导 agent。
       只读级（A）也要先验一遍通行证。
    """
    action0 = (args.get("action") or "pages").lower()
    # ★ 窗G1：launch/close 会**拉起/杀掉一个进程** ⇒ 归 B 级（点击滚动那一档），
    #   别混在「只读感知」的 A 级里 —— 闸门分级失真比功能缺失更难查。
    scope = "B" if action0 in ("launch", "close") else "A"
    try:
        ok, why, code = gate.check(scope)
        if not ok:
            return env(code=code, warning="%s —— 浏览器通道需要「%s」级%s通行证，先 ctl_authorize"
                       % (why, scope, "（launch/close 会起/杀进程）" if scope == "B" else ""))
    except gate.GateDenied as e:
        return env(code=e.code, warning=e.why)
    if action0 == "launch":
        return _cdp_launch(args)
    if action0 == "close":
        return _cdp_close(args)
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
    # ★ 窗E2：这里原来**把 port 丢了** —— ctl_browser pages/eval/text... 传了
    #   port=9333，cdp.py 却只会去连默认 9222。契约里「与本工具的 port 传同一个值
    #   即可」（窗B 写的那句）就是这么不成立的。⇒ 同样**参数优先、默认 9222**。
    argv += ["--port", str(int(args.get("port") or CDP_DEFAULT_PORT))]
    rc, out, e = _legacy("cdp.py", argv, timeout=int(args.get("timeout_sec") or 90))
    return _map_rc(rc, out, e)


# ==========================================================================
# 窗G1 · CDP 通道**接线**（R1 真判定 + 不碰用户标签页的独立实例）
# ==========================================================================
# ★ 这段**不重写 CDP**：DOM 那一侧仍然走现役的 `legacy_tools\cdp.py`
#   （它内部用 websocket-client；本机只有 websocket，没有 websockets/requests）。
#   这里只补它没有的两件事：
#     ① 一个**独立 profile + 自己的调试端口**的实例启动器
#        （绝不去 attach 用户正在用的浏览器）；
#     ② 把「端口可达 / 目标页在不在 / DOM 命中」变成**可判定的事实**，
#        好让 R1 = wired ∧ reachable ∧ dom_hit 真能成立，而不是恒 false。
# ★ 接缝只有下面两个（判据 monkeypatch 这两个，其余全是真代码）：
#     _cdp_http(path, port, timeout)  —— /json/version、/json/list 两个 HTTP 端点
#     _cdp_eval(js, port, timeout)    —— 经 cdp.py eval 在目标页里跑 JS 取 DOM
CDP_DEFAULT_PORT = 9222
CDP_HTTP_TIMEOUT_SEC = 3.0
CDP_EVAL_TIMEOUT_SEC = 25
SCREEN_DOM_MAX_OBJECTS = 60       # dom 段上限（派单：≤60 条）
SCREEN_DOM_ASK_OBJECTS = 400      # 向页面要多少条（多要，再在本地裁 —— 裁剪口径得在我们这边）
CDP_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_CDP_PROC = {}                    # port -> {pid, profile, exe, ws}（**只记我们自己起的**）


def _cdp_http(path: str, port: int = CDP_DEFAULT_PORT,
              timeout: float = CDP_HTTP_TIMEOUT_SEC) -> tuple:
    """GET ``http://127.0.0.1:<port><path>``。返回 (状态码, 正文)；连不上返回 (0, 原因)。

    ★ 只用标准库 urllib —— 本机没有 requests（派单明说，别用）。
    ★ 这两个端点（/json/version、/json/list）与 cdp.py 的 ``http_get`` 完全一致，
      不是另起一套协议。
    """
    url = "http://127.0.0.1:%d%s" % (int(port), path)
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=float(timeout)) as r:
            return (int(getattr(r, "status", 200) or 200),
                    (r.read() or b"").decode("utf-8", "replace"))
    except Exception as e:
        return 0, "%s: %s" % (type(e).__name__, e)


def _cdp_json(path: str, port: int = CDP_DEFAULT_PORT,
              timeout: float = CDP_HTTP_TIMEOUT_SEC):
    code, body = _cdp_http(path, port, timeout)
    if code != 200:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _cdp_version(port: int = CDP_DEFAULT_PORT) -> dict:
    """/json/version —— 端口上有没有 CDP 实例。取不到就是空 dict（**不假装有**）。"""
    return _cdp_json("/json/version", port) or {}


def _cdp_pages(port: int = CDP_DEFAULT_PORT, url_contains: str = "") -> list:
    lst = _cdp_json("/json/list", port) or []
    pages = [t for t in lst if isinstance(t, dict) and t.get("type") == "page"]
    kw = str(url_contains or "").strip().lower()
    if kw:
        pages = [p for p in pages
                 if kw in (str(p.get("url") or "") + str(p.get("title") or "")).lower()]
    return pages


def _cdp_eval(js: str, port: int = CDP_DEFAULT_PORT, timeout: int = CDP_EVAL_TIMEOUT_SEC,
              url_contains: str = "") -> tuple:
    """在目标页里跑一段 JS。返回 (rc, 解析后的值 or None, 原始输出)。

    ★ 走 cdp.py 的 ``eval`` 子命令 —— WebSocket 那段是现役真代码，本单不重写。
    ★ 窗E2（2026-10-18）：cdp.py **已经认 --port**（窗B 改真源 + gen_legacy，
      CHK-A 两腿 35/35 绿，提交 1abcdff）=> 这里把 port **原样透传**过去，
      不再对 port≠9222 硬拒。以前那句硬拒的病根是「cdp.py 写死 9222」；
      闸门拆了之后若还留着，只会让 ctl_browser launch port=9223 起的实例
      的 DOM 段**永远**报不可用 —— 正是窗B 回执第 8 节自报的那个开口。
    ★ 兼容：port 缺省仍是 CDP_DEFAULT_PORT(9222) => cdp.py 收到 --port 9222，
      与改前（不传参、cdp.py 内部默认 9222）**逐字等效**。
    """
    _port = int(port or CDP_DEFAULT_PORT)
    argv = ["eval", "--js", str(js), "--port", str(_port)]
    if url_contains:
        argv += ["--url", str(url_contains)]
    rc, out, e = _legacy("cdp.py", argv, timeout=int(timeout))
    txt = (out or "").strip()
    if rc != 0:
        return int(rc), None, (e or out or "").strip()
    try:
        return 0, json.loads(txt), txt
    except Exception:
        return 0, None, txt


# dom 段的取数脚本。刻意做得**笨**：不做可见性精算、不做语义融合、不排序 ——
# 「先看得见，再谈合并」。要多少条由本地裁（__ASK__ → SCREEN_DOM_MAX_OBJECTS）。
SCREEN_DOM_JS = r"""(function(){
  var out = [], all = document.body ? document.body.querySelectorAll('*') : [];
  for (var i = 0; i < all.length && out.length < __ASK__; i++) {
    var el = all[i], r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    var name = (el.getAttribute('aria-label') || el.getAttribute('title') ||
                (el.tagName === 'INPUT' ? el.value : '') || (el.innerText || ''));
    name = name.replace(/\s+/g, ' ').trim().slice(0, 120);
    if (!name) continue;
    var path = [], n = el;
    for (var k = 0; n && n.nodeType === 1 && k < 6; k++, n = n.parentElement) {
      var seg = n.tagName.toLowerCase();
      if (n.id) { path.unshift(seg + '#' + n.id); break; }
      var p = n.parentElement;
      var sib = p ? Array.prototype.filter.call(p.children,
              function(c){ return c.tagName === n.tagName; }) : [];
      if (sib.length > 1) seg += ':nth-of-type(' + (sib.indexOf(n) + 1) + ')';
      path.unshift(seg);
    }
    out.push({role: el.tagName.toLowerCase(), name: name,
              rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
              selector: path.join(' > ')});
  }
  return {url: location.href, title: document.title, scanned: all.length, objects: out};
})()"""


def _screen_dom(*, url_contains: str = "", port: int = CDP_DEFAULT_PORT,
                timeout: int = CDP_EVAL_TIMEOUT_SEC) -> dict:
    """DOM 通道（缩水版）：body 下可见元素，**最多 SCREEN_DOM_MAX_OBJECTS 条**。

    ★ 不做 UIA×DOM 的融合排序 —— 两边各自带 ``source``，谁也不冒充谁（派单原话）。
    ★ 上限在**本地**裁：页面那边多给，裁剪口径留在我们这边，才说得清
      「omitted.total 是多少、为什么被裁」。
    """
    js = SCREEN_DOM_JS.replace("__ASK__", str(int(SCREEN_DOM_ASK_OBJECTS)))
    rc, val, raw = _cdp_eval(js, port, timeout, url_contains)
    head = {"wired": False, "page_url": "", "page_title": "", "objects": [],
            "omitted": {"total": 0, "returned": 0, "cap": SCREEN_DOM_MAX_OBJECTS,
                        "scanned": 0},
            "口径": "dom 段是**缩水版**：只取 body 下有名字的可见元素，最多 %d 条；"
                    "与 UIA 各自带 source，不做融合排序。" % SCREEN_DOM_MAX_OBJECTS}
    if rc != 0 or not isinstance(val, dict):
        head["不可用原因"] = (str(raw or ""))[:200] or ("rc=%s" % rc)
        return head
    got = [o for o in (val.get("objects") or []) if isinstance(o, dict)]
    keep = got[:SCREEN_DOM_MAX_OBJECTS]
    head["wired"] = True
    head["page_url"] = str(val.get("url") or "")
    head["page_title"] = str(val.get("title") or "")
    head["omitted"]["total"] = len(got)
    head["omitted"]["scanned"] = int(val.get("scanned") or 0)
    head["objects"] = [{
        "role": str(o.get("role") or ""),
        "name": str(o.get("name") or ""),
        "rect": list(o.get("rect") or [0, 0, 0, 0]),
        "selector": str(o.get("selector") or ""),
        "source": "cdp",
        "source_confidence": dict(SCREEN_SOURCE_CONF["cdp"]),
    } for o in keep]
    head["omitted"]["returned"] = len(head["objects"])
    return head


def _cdp_probe(*, port: int = CDP_DEFAULT_PORT, url_contains: str = "",
               query: str = "", want_dom: bool = True) -> dict:
    """R1 三要素的事实基础：wired（端口可达）/ reachable（有目标页）/ dom_hit（DOM 命中）。

    ★ 拿不到就**如实拿不到**：不可达时 ``wired=False`` 且带一句原因，
      绝不把「没查成」写成「查过了，没有」。
    """
    out = {"wired": False, "reachable": False, "dom_hit": False,
           "page_url": "", "browser": "", "端口": int(port), "原因": "", "dom": None}
    ver = _cdp_version(port)
    if not ver:
        out["原因"] = "调试端口 %s 上没有 CDP 实例（/json/version 连不通）" % port
        return out
    out["wired"] = True
    out["browser"] = str(ver.get("Browser") or "")
    pages = _cdp_pages(port, url_contains)
    if not pages:
        out["原因"] = ("端口可达，但没有匹配的页面标签页（url_contains=%r）—— R1 不成立"
                       % (url_contains or ""))
        return out
    out["reachable"] = True
    out["page_url"] = str(pages[0].get("url") or "")
    if not want_dom:
        return out
    dom = _screen_dom(url_contains=url_contains, port=port)
    out["dom"] = dom
    if not dom.get("wired"):
        out["原因"] = "页面在，但 DOM 取不到：%s" % str(dom.get("不可用原因") or "")[:160]
        return out
    q = str(query or "")
    # ★ 2026-10-18（DSH 真浏览器实测抓到的崩溃）：原来只在 `if q:` 里写 `命中节点数`，
    #   下一行却**直接下标**读它 ⇒ `query` 为空（正常调用形态！）时抛 KeyError('命中节点数')，
    #   被闸门包成 code=15「读元素树失败」。判据没抓到是因为假的 cdp 每次都带了 query。
    #   修法：先给 0，再按需覆盖。
    out["命中节点数"] = 0
    if q:
        out["命中节点数"] = sum(1 for o in dom.get("objects") or []
                                 if _screen_query_hit(q, str(o.get("name") or "")) > 0)
    out["dom_hit"] = bool(out["命中节点数"])
    return out


# ── 独立实例：起 / 关（不碰用户现有的浏览器）──────────────────────────────
# ── 窗G2 · OCR（R3）接线 ────────────────────────────────────────────────────
def _ocr_module():
    """拿 agentctl.ocr。**拿不到就说拿不到**（没装 / 导入炸了），不许假装有这条通道。

    ★ 判据就靠这个模块对象 monkeypatch（ocr_window / engine_ok），所以这里**按模块取**、
      不在 core 里另抄一份实现。
    """
    try:
        from agentctl import ocr as m
        return m
    except Exception:
        return None


def _ocr_norm(s) -> str:
    return _screen_norm_text(str(s or ""))


# ★ 2026-10-18（DSH 真页实测补）：Windows OCR 把中文**逐字切开并加空格**
#   （"剩余可选" → "剩 余 可 选"）⇒ 直接用 `q in blob` 判**多字中文永远不中**。
#   实测证据：真 Edge 窗口 ocr_window 返回的 text 是 "119 ： 56 剩 余 可 选 3 项 · 第 7 / 20 步"，
#   而 `"剩余" in text` 为 False、`"剩" in text` 为 True。
#   这里把「CJK 之间的空白」吃掉再比；**拉丁词之间不动**（否则 "not allowed" 会粘成 "notallowed"）。
_CJK_RANGE = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"


def _ocr_tight(s) -> str:
    t = _ocr_norm(s)
    return re.sub(r"(?<=[%s])\s+(?=[%s])" % (_CJK_RANGE, _CJK_RANGE), "", t)


def _screen_ocr(hwnd: int, *, query: str = "", rect=None,
                 lang: str = "zh-Hans-CN", max_ms: int = 1500) -> dict:
    """R3 三要素的事实来源：wired（引擎起得来）/ hit（像素里认出了目标）/ usable（框能当锚）。

    ★ **命中判定必须按整段文本，不能只按单个 word**：实测 Windows OCR 会把中文逐字切开
      （「剩余 00:42」认成 剩|余|00|：|42），只比单词的话多字关键词永远命中不了。
    """
    out = {"wired": False, "hit": False, "usable": False, "ok": False,
           "命中": [], "原因": "", "res": None, "端口无关": True}
    m = _ocr_module()
    if m is None:
        out["原因"] = "agentctl.ocr 导入失败 —— 本机没有可用的 OCR 模块"
        return out
    try:
        eng = m.engine_ok(lang)
    except Exception as e:
        eng = {"ok": False, "原因": "%s: %s" % (type(e).__name__, e)}
    out["wired"] = bool(eng.get("ok"))
    out["引擎"] = str(eng.get("lang") or "")
    out["可用语言"] = list(eng.get("langs") or [])
    if not out["wired"]:
        out["原因"] = str(eng.get("原因") or "OCR 引擎不可用")
        return out
    try:
        res = m.ocr_window(int(hwnd), rect=rect, lang=lang, max_ms=int(max_ms))
    except Exception as e:
        out["原因"] = "ocr_window 抛异常：%s: %s" % (type(e).__name__, e)
        return out
    out["res"] = res
    out["ok"] = bool(res.get("ok"))
    if not out["ok"]:
        # ★ 超时/引擎抽风都走这一条：**如实报 false + 原因**，绝不返回半截 words 当命中。
        #   wired 也一起落 false —— 它指的是「**这一屏**拿到了像素这条证据」，
        #   不是「引擎进程在不在」。引擎本身起没起得来另看 `引擎`/`可用语言` 两个字段。
        out["wired"] = False
        out["原因"] = str(res.get("原因") or "OCR 没跑成")
        return out
    words = res.get("words") or []
    # ★ 用 `_ocr_tight`（吃掉 CJK 间空白）而不是 `_ocr_norm`：Windows OCR 把中文逐字切开，
    #   用 norm 时多字中文查询永远不中（真页实测：query="剩余" 命中 False，而逐字都在）。
    q = _ocr_tight(query)
    blob = _ocr_tight(" ".join([str(res.get("text") or "")]
                               + [str(x.get("t") or "") for x in words]))
    if q and q in blob:
        out["hit"] = True
    hits = [x for x in words if q and _ocr_norm(x.get("t")) and _ocr_norm(x.get("t")) in q]
    if not hits:
        hits = [x for x in words if q and q in _ocr_norm(x.get("t"))]
    out["命中"] = hits[:8]
    # usable：框落在客户区内 **且** 宽高都大于 OCR_MIN_BOX_PX（太小当锚会点偏）
    geom = res.get("客户区") or {}
    cw = int(geom.get("width") or 0)
    ch = int(geom.get("height") or 0)
    min_px = int(getattr(m, "OCR_MIN_BOX_PX", 4))
    slack = 2
    usable = []
    for wd in (out["命中"] or hits[:8]):
        try:
            x, y = int(wd.get("x") or 0), int(wd.get("y") or 0)
            bw, bh = int(wd.get("w") or 0), int(wd.get("h") or 0)
        except Exception:
            continue
        if bw <= min_px or bh <= min_px:
            continue
        if cw and ch and (x < -slack or y < -slack or x + bw > cw + slack or y + bh > ch + slack):
            continue
        usable.append(wd)
    out["usable_boxes"] = usable
    out["usable"] = bool(usable)
    out["客户区"] = geom
    if out["hit"] and not out["usable"]:
        out["原因"] = ("认出了「%s」，但它的框落在客户区外或太小（> %d px 才算能当锚），"
                        "R3 不成立" % (query, min_px))
    return out


def _cdp_browser_exe() -> str:
    """找浏览器可执行文件。找不到就**说找不到**，不瞎编一个路径。"""
    env_exe = str(os.environ.get("AGENTCTL_BROWSER_EXE") or "").strip()
    cands = ([env_exe] if env_exe else []) + list(CDP_BROWSER_CANDIDATES)
    for c in cands:
        if c and os.path.exists(c):
            return c
    return ""


def _cdp_spawn(argv: list) -> int:
    """**接缝**：拉起浏览器进程。判据替换它，真跑时才会真起进程。"""
    return int(subprocess.Popen([str(a) for a in argv], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL).pid)


def _cdp_kill_tree(pid: int) -> tuple:
    """**接缝**：taskkill /T 连子进程一起收（浏览器是一棵进程树）。"""
    r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    return int(r.returncode), ((r.stdout or "") + (r.stderr or "")).strip()


def _pid_alive(pid: int) -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15)
        return str(int(pid)) in (r.stdout or "")
    except Exception:
        return False


def _cdp_launch(args: dict) -> dict:
    """起一个**我们自己的**浏览器实例：独立 profile + 自己的调试端口。

    ⛔ **端口被别人占着就不抢**。理由不是礼貌：那个端口后面是**用户正在用的浏览器会话**，
    我们抢了等于替用户关掉他所有标签页。抢不到就明说，并给下一步（换端口）。
    """
    port = int(args.get("port") or CDP_DEFAULT_PORT)
    exe = str(args.get("exe") or _cdp_browser_exe())
    if not exe or not os.path.exists(exe):
        return env(code=4, warning=("找不到浏览器可执行文件（试过 %s）—— "
                                    "用 exe= 明确指定，或设环境变量 AGENTCTL_BROWSER_EXE"
                                    % ("、".join(CDP_BROWSER_CANDIDATES),)))
    mine = _CDP_PROC.get(port) or {}
    ver = _cdp_version(port)
    if ver:
        if mine.get("pid") and _pid_alive(int(mine["pid"])):
            return env(ok=True, code=0, data={"pid": int(mine["pid"]), "port": port,
                             "profile": mine.get("profile"),
                             "ws": str(ver.get("webSocketDebuggerUrl") or ""),
                             "复用": True,
                             "说明": "这个端口上的实例就是本进程之前起的，直接复用"})
        return env(code=4,
                   warning=("端口 %d 已被**别的**实例占用（Browser=%s，/json/version 有应答）"
                            "—— 本调用**拒绝接管**：它不是我们起的，动手脚等于毁掉别人的"
                            "浏览器会话。下一步：换个 port=<别的端口>，或先请用户自己关掉那个实例。"
                            % (port, str(ver.get("Browser") or "?"))),
                   data={"端口": port, "占用者": str(ver.get("Browser") or ""),
                         "ws": str(ver.get("webSocketDebuggerUrl") or ""),
                         "我们起的": False, "建议": "ctl_browser launch port=9223"})
    profile = str(args.get("profile") or os.path.join(
        os.environ.get("TEMP") or os.environ.get("TMP") or ".",
        "agentctl_cdp_%d_%d" % (os.getpid(), int(time.time()))))
    os.makedirs(profile, exist_ok=True)
    # ★ 2026-10-18（DSH 亲测修）：原来这里**写死 about:blank**，`url` 参数被忽略 ⇒
    #   launch 之后 /json/list 里只有 about:blank，`url_contains` 永远 reachable=false。
    #   真实验证据：同参数手工起 Edge 打开 file:///…lab_volatile.html 时 pages=7 里有 page，
    #   而走本函数起的实例里一条 page 都没有。
    argv = [exe, "--user-data-dir=%s" % profile,
            "--remote-debugging-port=%d" % port,
            # ★ 2026-10-18（DSH 亲测补）：Edge/Chrome 111+ 会**按 Origin 拒 WebSocket**，
            #   不放这个旗标时客户端握手直接 403（实测报错：
            #   "Rejected an incoming WebSocket connection from the http://127.0.0.1:9222 origin"）
            #   ⇒ 本机起的实例连自己的 CDP 都连不上。只放行本机这个端口，不用 `*`。
            "--remote-allow-origins=http://127.0.0.1:%d" % port,
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            str(args.get("url") or "about:blank")]
    pid = _cdp_spawn(argv)
    ws = ""
    ver = {}
    for _ in range(60):                       # 最多等 ~15s 让调试端口起来
        time.sleep(0.25)
        ver = _cdp_version(port)
        if ver:
            ws = str(ver.get("webSocketDebuggerUrl") or "")
            break
    if not ver:
        return env(code=4, warning=("已拉起 pid=%s，但 15 秒内 /json/version 没应答 —— "
                                    "这个浏览器可能不支持调试端口；profile=%s"
                                    % (pid, profile)),
                   data={"pid": pid, "port": port, "profile": profile})
    _CDP_PROC[port] = {"pid": int(pid), "profile": profile, "exe": exe, "ws": ws}
    return env(ok=True, code=0, data={"pid": int(pid), "port": port, "profile": profile,
                     "ws": ws, "浏览器": str(ver.get("Browser") or ""),
                     "启动参数": argv,
                     "说明": "独立 profile，与用户日常浏览器互不相干；"
                             "用 action=close 关掉（taskkill /T 并核对退出）"})


def _cdp_close(args: dict) -> dict:
    """关掉**我们起的**那个实例：taskkill /T，然后**核对它真的退了**。"""
    port = int(args.get("port") or CDP_DEFAULT_PORT)
    mine = _CDP_PROC.get(port) or {}
    pid = int(args.get("pid") or mine.get("pid") or 0)
    if not pid:
        return env(code=4, warning=("不知道要关哪个实例：本进程没在 %d 端口起过浏览器，也没给 pid"
                                    % port))
    rc, out = _cdp_kill_tree(pid)
    for _ in range(30):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    alive = _pid_alive(pid)
    port_free = not bool(_cdp_version(port))
    _CDP_PROC.pop(port, None)
    return env(ok=True, code=0,
               data={"pid": pid, "taskkill_rc": rc, "taskkill": out,
                     "进程已退出": not alive, "端口已释放": port_free},
               warning=("pid=%s 还在，端口 %d 也没松 —— 关不掉，别当成已关" % (pid, port)
                        if (alive or not port_free) else None))


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


# ==========================================================================
# F4 语义屏 · ctl_screen（窗B1 · 2026-10-17）
# ==========================================================================
# 一句话：**一次调用给 agent 一段裁剪过、带身份、带新鲜度的屏幕**，而不是一整棵树。
#
# ★ 五条设计修正（GLM 评审，本单必须照做；判据逐条盯着）：
#   修正① uid 拆两段 —— struct_key（role + 路径）与 text_key（规范化文本）分开；
#          volatile=true 的节点（时间/计数类）**不参与 text_key 比对**。
#          病根：计时器每帧都换字，若把文字折进 uid，它每帧都是「新节点」⇒ STALE 风暴。
#   修正② screen_version **服务端签发**。调用方只能把「上次见过的」原样回传（since）
#          用于对照；**永远不能指定**。传了 version/screen_version 也一律忽略并明说。
#   修正③ **不要 confidence 字段**，改 source_confidence；多来源聚合 **取 min**。
#          病根：取平均/取最大都会高估 —— 有一条弱证据就该被看见。
#   修正④ name_trust：Chromium/网页类窗口一律 low —— 网页的无障碍 name 不可信
#          （超星选择题字母与实际选项错位就是实例），别让 agent 把它当权威文本。
#   修正⑤ channels 段把 must_vlm = ¬R1 ∧ ¬R2 ∧ ¬R3 写成**真判定**而不是注释。
#          本版 R1(CDP)/R3(OCR) **只判定不接线** —— 没接线就报 false，不许假装有。
#
# ★ 注意力打分**必须可解释、可调**：见 SCREEN_SCORE_WEIGHTS（改那个 dict 就改口径）。
# ★ **必进保证**：`interactive 且看得见` 的节点**永不裁掉**，哪怕超过 top-N。
#   这不是「优化」，是**正确性**：裁掉一个当前可点的按钮，agent 只会以为界面上没有它
#   —— 那比多给几个文本节点坏得多。多给的代价是 token，不是安全。

SCREEN_DEFAULT_TOP = 40
SCREEN_DEFAULT_MAX_NODES = 1200       # 语义屏要「结构骨架完整」，比 ctl_tree 宽一档
SCREEN_MAX_NODES_CEIL = 5000
SCREEN_STRUCT_KEY_SEGS = 6            # struct_key 只留末 6 段：深树前面几段是公共外壳
SCREEN_DELTA_LIST_CAP = 60            # delta 段最多列多少个 uid（超出只报数，不刷屏）

# ★ 打分权重 —— **可调口径的唯一入口**，调参与判据都改这里。
SCREEN_SCORE_WEIGHTS = {
    "in_view": 0.35,      # 在这一屏看得见
    "interactive": 0.25,  # 能点/能填
    "query": 0.20,        # 命中调用方给的 query（题干/关键词）
    "area": 0.10,         # 面积占比（相对客户区，钳到 1.0）
    "delta": 0.10,        # 相对上次快照是新面孔
}

# ★ 修正③（**Z2 二次修正：拆属性级**）：每个来源的置信度**按属性分档**，不是一个数。
#   bbox = 「点得准不准」（点哪儿）  text = 「读得准不准」（读到什么）  existence = 「在不在」
#   三者**错误后果不同级**：bbox 错会点错地方（事故），text 错会读错内容（幻觉），
#   existence 错会凭空多一个或少一个对象。合成一个数就会掩盖这个差别。
# ★ 为什么 bbox **不进**聚合：对象级取 min 会让 UIA 的精确坐标被 OCR 的低分文本拖低
#   ⇒ 一个「坐标很准、只是字没认全」的对象被说成「定位也不准」⇒ 误伤定位（Z2 的原话）。
#   所以聚合口径 = min(existence, text)（「读得准不准」），**bbox 单列**。
SCREEN_CONF_ATTRS = ("bbox", "text", "existence")
SCREEN_SOURCE_CONF = {
    #               bbox   text   existence
    "uia": {"bbox": 1.00, "text": 1.00, "existence": 1.00},  # 无障碍树：控件自己的自述，最强
    # ★ 窗G1：CDP 这档**已接线**（dom 段逐条带它，R1 命中时也报它）。
    #   0.90 的口径：DOM 是浏览器的**自述**结构，比 UIA 的原生控件弱一档（可能含被 CSS 藏起来的），
    #   但比 OCR 强得多（它是真结构，不是像素猜测）。
    "cdp": {"bbox": 0.90, "text": 0.90, "existence": 0.90},  # DOM 结构
    # OCR：像素只证明「那儿有块墨」，**证明不了那是个可点的控件** ⇒ existence 单独压低；
    # 而它给的框是像素框（点下去大概率偏）⇒ bbox 也不高，但**与 text 分开记**。
    # ★ 窗G2：这一档搬去 `ocr.OCR_CONF` 了（一份真值，core 不再抄一遍，抄两份迟早会漂）。
    "ocr": {"bbox": 0.55, "text": 0.55, "existence": 0.40},
}

# ★ 时间/计数类节点的识别。命中 ⇒ volatile=true ⇒ **不参与 text_key 比对**（修正①）。
#   只认「时刻」与「带单位的量」，不认裸数字 —— 裸数字可能是订单号/题号，变了就是变了。
SCREEN_VOLATILE_RE = re.compile(
    r"(\d{1,3}:\d{1,2}(?::\d{1,2})?)"                              # 12:34 / 01:02:03
    # ★ 2026-10-17（Z3 守门）：单位表补单字母 s 与 min —— 注释早就宣称命中 `3.5s`，
    #   而实现里没有 `s` ⇒ 注释与实现漂移（实测 3.5s 不命中）。
    r"|(\d+(?:\.\d+)?\s*(?:ms|s|秒|毫秒|min|分钟|分|fps|hz|kb|mb|gb|%))"   # 3.5s / 5 min / 42%
    # ★ 裸关键词删掉 `剩余`：`剩余 3 项` / `剩余 5 min` 里，前者**承载任务信息、不该豁免**，
    #   后者靠单位（min）照样命中 ⇒ 只删关键词不会漏掉时限类。
    r"|(倒计时|已用时|用时|elapsed|remaining|countdown|timer|clock)",
    re.IGNORECASE)
# ★ 2026-10-17（Z3 守门）：**SpinnerControl 移出豁免名单** —— 它在 SCREEN_ACTIONS 里
#   有 click/set，是可交互节点；既豁免又能交互 ⇒ 动作后核验对它全盲（Z2 的"路径 C"现状）。
#   交互节点永不豁免（这条同时是任务声明制与必进保证的前提）。
SCREEN_VOLATILE_TYPES = frozenset({"ProgressBarControl"})

# 角色 → 可用动作。**按角色推**，不问 Pattern（每节点 8 次 COM 往返买不起）。
SCREEN_ACTIONS = {
    "ButtonControl": ["click"],
    "RadioButtonControl": ["click"],
    "CheckBoxControl": ["click", "toggle"],
    "TabItemControl": ["click"],
    "ListItemControl": ["click"],
    "TreeItemControl": ["click", "expand"],
    "MenuItemControl": ["click"],
    "HyperlinkControl": ["click"],
    "DataItemControl": ["click"],
    "EditControl": ["click", "type", "fill"],
    "ComboBoxControl": ["click", "select"],
    "SliderControl": ["click", "set"],
    "SpinnerControl": ["click", "set"],
}

# 修正④：网页/Chromium 类窗口的 class 名片段与进程名片段。
SCREEN_WEB_CLS_HINTS = ("chrome_widgetwin", "mozillawindowclass", "electron")
SCREEN_WEB_PROC_HINTS = ("msedge", "chrome", "firefox", "electron", "iexplore",
                         "brave", "vivaldi", "opera", "360chrome", "360se")

# R2（UIA 通道）判定阈值：可交互节点数到这个量级，才认为「UIA 认得这个界面」。
SCREEN_R2_MIN_INTERACTIVE = 5


def screen_must_vlm(r1: bool, r2: bool, r3: bool) -> bool:
    """修正⑤的核心判定式：**must_vlm = not R1 and not R2 and not R3**。

    三条结构化通道（CDP+DOM / UIA / OCR）**没有一条能定位到目标**时，
    才轮得到视觉模型。写成函数而不是注释，是为了让它**能被判据说打红**。
    """
    return (not r1) and (not r2) and (not r3)

def screen_channels(*, interactive_count: int, target_hit: bool,
                    name_trust: str = "high",
                    cdp_wired: bool = False, cdp_reachable: bool = False,
                    dom_hit: bool = False,
                    ocr_wired: bool = False, ocr_hit: bool = False,
                    ocr_bbox_usable: bool = False) -> dict:
    """三条通道各自的命中情况 + must_vlm 判定（修正⑤ + **Z2 二次修正：R2 加门槛**）。

    ★ 窗G1 起：**R1(CDP) 已接线** —— wired/reachable/dom_hit 都是真探测来的事实
      （见 `_cdp_probe`：/json/version 有没有应答、有没有目标页、DOM 里命不命中）。
      探测不到就**如实报 false**，绝不把「没查成」写成「查过了，没有」——
      否则 agent 会以为「DOM 已经查过了，没有」而漏掉真正存在的东西。
    ★ R3(OCR) 仍是**只判定不接线**（wired 恒 false），理由同上。

    ★ **R2 = 可交互数 ≥ 阈值 ∧ 目标命中 ∧ name_trust ≠ "offset-suspect"**。
      最后那半截是 Z2 加的门槛：网页选项「疑似错位」（控件自称的字母与它在屏幕上的位置
      对不上；超星选择题就是实例）时，R2 会**错位命中**从而假通过 ——
      而这种页面本来就该落到 R3/VLM 去用眼睛看，别让 UIA 的自述一手遮天。
    """
    trust = str(name_trust or "high")
    trust_ok = (trust != "offset-suspect")
    # ★ 窗G1：R1 的命中必须是 **DOM 命中**，不是 UIA 的那个 ——
    #   两者共用一个参数会让「UIA 命中」冒充「DOM 命中」，R1 就成了假绿。
    r1 = bool(cdp_wired and cdp_reachable and dom_hit)
    r2 = bool(int(interactive_count) >= SCREEN_R2_MIN_INTERACTIVE
              and target_hit and trust_ok)
    r3 = bool(ocr_wired and ocr_hit and ocr_bbox_usable)
    why = ["R1(CDP+DOM)=%s%s" % ("命中" if r1 else "不成立",
                                  "" if cdp_wired else "（CDP 不可达，接不上一条事实）"),
          "R2(UIA)=%s（可交互 %d，阈值 %d，目标%s，name_trust=%s%s）"
          % ("命中" if r2 else "不成立", int(interactive_count),
             SCREEN_R2_MIN_INTERACTIVE, "命中" if target_hit else "未命中",
             trust, "" if trust_ok else "⇒offset-suspect 门槛不通过"),
          "R3(OCR+bbox)=%s%s" % ("命中" if r3 else "不成立",
                                  "" if ocr_wired else "（本版未接线）")]
    return {
        "R1_cdp": {"wired": bool(cdp_wired),
                    "reachable": bool(cdp_reachable and cdp_wired),
                    "dom_hit": bool(dom_hit and cdp_wired and cdp_reachable),
                    "hit": r1},
        "R2_uia": {"wired": True, "interactive_count": int(interactive_count),
                    "threshold": SCREEN_R2_MIN_INTERACTIVE,
                    "target_hit": bool(target_hit),
                    "name_trust": trust,
                    "name_trust_门槛通过": bool(trust_ok),
                    "hit": r2},
        "R3_ocr": {"wired": bool(ocr_wired), "hit": r3,
                    "bbox_usable": bool(ocr_bbox_usable and ocr_wired)},
        "must_vlm": screen_must_vlm(r1, r2, r3),
        "must_vlm_式": "must_vlm = not R1 and not R2 and not R3",
        "must_vlm_why": "；".join(why),
    }


def _screen_conf_attrs(value) -> dict:
    """把一条证据的置信度**归一成属性级三档** {bbox,text,existence}。

    兼容三种写法：完整 dict / 部分 dict（缺的按 0.0 补，不许凭空乐观）/ 裸标量（三档同值）。
    ★ 旧写法（对象级一个数）喂进来不炸，但它天然**丢掉了属性级分辨率** ——
      想验「bbox 不被 text 拖低」，就必须给出三档分开的值。
    """
    if isinstance(value, dict):
        out = {}
        for k in SCREEN_CONF_ATTRS:
            try:
                out[k] = float(value.get(k, 0.0))
            except (TypeError, ValueError):
                out[k] = 0.0
        return out
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    return {k: v for k in SCREEN_CONF_ATTRS}


def _agg_source_conf_attrs(items) -> dict:
    """**属性级**聚合：每个属性各自取 min；再算聚合口径 agg = min(existence, text)。

    返回 {bbox, text, existence, agg, sources}。
    ★ 每个属性取 min 而不是 mean/max：min = 「只要有一条弱证据，就按弱证据信」——
      对「我看见了，所以可以点」这类断言，这是唯一不会高估的合成法。
      mean 会把「UIA 说它在、OCR 没看见」平均成一个中间值，把「两条证据打架」抹平；
      max 则是直接撒谎：一路错认就能把置信度顶满。
    ★ **bbox 不进 agg**：见 SCREEN_SOURCE_CONF 上方的理由（会误伤定位）。
    空证据表全报 0.0 —— **没有来源就说没有**，不报 1.0。
    """
    vals = {k: [] for k in SCREEN_CONF_ATTRS}
    names = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        v = it.get("source_confidence")
        if v is None:
            continue
        a = _screen_conf_attrs(v)
        for k in SCREEN_CONF_ATTRS:
            vals[k].append(a[k])
        names.append(str(it.get("source") or "?"))
    if not names:
        return {"bbox": 0.0, "text": 0.0, "existence": 0.0,
                "agg": 0.0, "sources": []}
    out = {k: min(vals[k]) for k in SCREEN_CONF_ATTRS}
    out["agg"] = min(out["existence"], out["text"])   # 「读得准不准」
    out["sources"] = names
    return out


def _agg_source_confidence(items) -> tuple:
    """旧口径入口：返回 (min(existence, text), 来源名列表)。**保留是为了不打断既有调用方。**"""
    a = _agg_source_conf_attrs(items)
    return a["agg"], list(a["sources"])


def _screen_extra_evidence(uid: str, node: dict) -> list:
    """**扩展口**：真接上 CDP / OCR 之后，把它们的证据 append 到这里即可。

    本版 **不接线**，恒返回空列表 —— 判据靠替换这个函数造出一条 OCR 证据，
    来验「聚合取 min」这条规则真的被执行（而不是只写在注释里）。
    """
    return []


def _screen_evidence(uid: str, node: dict) -> list:
    """一条对象的全部来源证据 = UIA 自述 + 扩展口给的（CDP / OCR …）。

    ★ 每条证据的 source_confidence 都是**属性级三档** {bbox,text,existence}。
      扩展口写标量也行（会被摊成三档同值），但那样就没有属性级分辨率了。
    """
    ev = [{"source": "uia", "source_confidence": dict(SCREEN_SOURCE_CONF["uia"])}]
    try:
        extra = _screen_extra_evidence(uid, node)
    except Exception:
        extra = []
    for e in (extra or []):
        if not isinstance(e, dict) or not e.get("source"):
            continue
        raw = e.get("source_confidence")
        if raw is None:
            continue
        ev.append({"source": str(e.get("source")),
                   "source_confidence": _screen_conf_attrs(raw)})
    return ev


def _screen_norm_text(s: str) -> str:
    """text_key 的规范化：NFKC → 去零宽 → 折叠空白 → casefold。

    NFKC 管全角/半角与兼容字符，casefold 管大小写；两者都只动**写法**不动**词** ——
    写法变了不该让 uid 变，词变了才该。
    """
    if not s:
        return ""
    import unicodedata as _ud
    t = _ud.normalize("NFKC", str(s))
    t = t.replace("\u200b", "").replace("\ufeff", "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", t).strip().casefold()


def _screen_is_volatile(role: str, label: str) -> bool:
    """这个节点是不是「时刻/计数类」（改了字也不该算换了对象）。"""
    if role in SCREEN_VOLATILE_TYPES:
        return True
    return bool(SCREEN_VOLATILE_RE.search(label or ""))


def _screen_text_key(label: str, volatile: bool) -> str:
    """修正①的后半段：volatile 节点的 text_key 恒为 `~`（**不参与比对**）。

    非 volatile 才有 t+哈希。哈希而不是原文：uid 要短、要能安全当 key 传。
    """
    if volatile:
        return "~"
    norm = _screen_norm_text(label)
    if not norm:
        return "-"                  # 没字：无可比对，靠 struct_key 定位
    import hashlib as _hl
    return "t" + _hl.sha1(norm.encode("utf-8")).hexdigest()[:10]


# ══ 任务声明制 watch_text（窗D1 · Z3 §4）═════════════════════════════════
# 一句话：agent 显式说「这几串是我这个任务要盯的」⇒ 它们**不再被 volatile 豁免吞掉**。
#   病根：豁免是按**串**判的 —— 长得像倒计时就豁免，可它承载的是任务信息。
#   一旦豁免，它换多少字 uid 都不变（text_key 恒 ~）⇒ agent 永远看到旧值 ⇒ 假绿。
#   框架 §⑯ 的裁决：**保留豁免**（假 STALE 的代价可量化）+ **声明制兜住任务信息**。
# ★ 声明的**生命周期 = 本调用**：服务端不留声明状态，agent 每次扫描都带上同一份
#   （它是任务边界的一部分，像 boundary 一样写进任务习惯）。
WATCH_TEXT_MAX = 32            # 声明条数上限（派单规则4：超了拒绝并说明，不静默截断）
WATCH_TEXT_WIDTH_MAX = 8       # 单条声明命中节点数上限（窗E1 · Z4 F2）：宽串=没声明；
                               # 超宽拒绝整调用并点名串与命中数。挂起救不了超宽（它已命中）。


def _screen_watch_parse(args) -> tuple:
    """解析 watch_text。返回 (decls, err)；decls = [(声明原文, 规范化串), ...]。

    ★ 规范化用**与 text_key 同一口径**的 _screen_norm_text（NFKC + 折叠空白 + casefold），
      所以子串比对不受全角/半角与大小写干扰。
    ★ 空声明是陷阱：空串是**任何** label 的子串 ⇒ 等于「声明了这一屏的所有节点」。明确拒绝。
    """
    raw = args.get("watch_text")
    if raw is None or raw == [] or raw == ():
        return [], None                     # 缺省/空数组 = 不声明（契约写明）
    if isinstance(raw, str):
        if not raw.strip():
            # 窗E1 · F7：空串以前被静默当「没声明」，与数组内空串的明确拒绝不一致 ——
            # 静默方向错在「该响不响」：agent 以为声明挂上了，实际啥都没盯。
            return [], ("watch_text 是空字符串 —— 要么传字符串数组，要么别传这个参数"
                        "（空数组/缺省 = 不声明；空**串**按形态错误拒绝，不静默）")
        return [], "watch_text 必须是**字符串数组**，不是单个字符串"
    if not isinstance(raw, (list, tuple)):
        return [], "watch_text 必须是字符串数组（拿到 %s）" % type(raw).__name__
    items = list(raw)
    if len(items) > WATCH_TEXT_MAX:
        return [], ("watch_text 给了 %d 条，超过上限 %d —— 声明是**任务边界**，不是关键词轰炸；"
                    "请合并成更少、更具体的串" % (len(items), WATCH_TEXT_MAX))
    decls, empty = [], []
    for it in items:
        s = "" if it is None else str(it)
        n = _screen_norm_text(s)
        if not n:
            empty.append(s)
            continue
        decls.append((s, n))
    if empty:
        return [], ("watch_text 里有空声明（%s）—— 空串会匹配一屏所有节点，等于没声明"
                    % json.dumps(empty, ensure_ascii=False)[:120])
    return decls, None


def _screen_watch_matches(label, decls) -> list:
    """label 命中的**所有**声明（规范化后**子串**比对，容忍前后缀噪音）。没命中返回 []。

    ★ 全收语义（窗E1 · 修 Z4 F1）：一个节点可以同时命中多条声明，**逐条**记账。
      旧版只返回第一条 ⇒ 第二条重叠声明被互蔽成 unmatched ⇒ 整调用误拒
      （Z4 实测：声明 ["剩余可选","第 7/20 步"] 盯同一行 T2，第二条被吞）。
      ⛔ 严禁改回「只取第一条」—— 那是假绿家族的手法。
      一条声明命中多少个节点的账也在同一份 hits 里（F2 宽度上限与 F1 共用，Z4 提醒）。
    """
    lab = _screen_norm_text(label)
    if not lab or not decls:
        return []
    return [orig for orig, n in decls if n in lab]


def _screen_watch_report(decls, hits, *, pending_ok: bool) -> dict:
    """watch_校验 段：matched / unmatched / pending **逐条列明**（不许静默吞声明）。"""
    matched = [orig for orig, _ in decls if hits.get(orig)]
    unmatched = [orig for orig, _ in decls if not hits.get(orig)]
    return {"声明条数": len(decls), "上限": WATCH_TEXT_MAX,
            "matched": matched,
            "unmatched": [] if pending_ok else unmatched,
            "pending": list(unmatched) if (pending_ok and unmatched) else [],
            "命中节点数": {k: int(v) for k, v in (hits or {}).items() if v},
            "口径": "规范化后子串匹配 label。matched=命中到节点的声明；"
                    "unmatched=一条都没命中（默认 ⇒ **整个调用拒绝**，不静默）；"
                    "pending=显式 watch_pending=true 允许挂起的那几条"}


def _screen_watch_reject(out: dict, why: str, report: dict, reason: str) -> dict:
    """声明校验失败的拒绝载荷（窗E1 · Z4 F3）：带**屏上下文**，让 agent 分得清
    「声明写错 / 白屏 / 错窗口 / 扫描预算没扫到」这四类失败。**不回 objects** ——
    那会被当成「声明已兑现」。

    reason: "unmatched"（声明不存在）| "width"（命中太宽）—— t_screen 按它给
    对口的 blind_spots 下一步建议。
    """
    total = (out.get("omitted") or {}).get("total")
    return {"why": why, "watch_校验": report, "拒因": reason,
            "screen_version": out.get("screen_version"),
            "window": out.get("window"),
            "对象总数": total,
            "性质": ("这是 watch_text **校验失败**，不是屏幕空 —— 本屏候选对象共 %s 个"
                     "（screen_version=%s）；别把拒绝当成「屏幕没内容」。"
                     % (total, out.get("screen_version")))}

# ── ② uid 三档（OK / TEXT_CHANGED / STALE）───────────────────────────────
#   病根：只分「变了 / 没变」一档是错的 —— struct_key 变（可能压根不是同一个对象）
#   与 text_key 变（同一个对象、内容变了）是**两种方向相反**的事：
#   前者必须**拒绝寻址**（拿旧坐标去点新控件＝事故），后者恰恰是**动作成功的证据**。
#   混成一档 STALE ⇒ 点完按钮后「它变了」也被判成过期 ⇒ agent 只好反复重扫（盲扫准风暴）。
SCREEN_UID_STATES = ("OK", "TEXT_CHANGED", "STALE")


def _screen_split_uid(uid) -> tuple:
    """uid 拆两段：(struct_key, text_key)。拆不开（没有 @ 或某段为空）⇒ (None, None)。"""
    s = str(uid or "")
    if s.count("@") != 1:
        return (None, None)
    sk, tk = s.split("@", 1)
    if not sk or not tk:
        return (None, None)
    return sk, tk


def screen_uid_state(prev_uid, cur_uid) -> str:
    """uid 三档判定。**写成函数是为了让判据能拿它打红**（写在注释里谁也验不了）。

    - struct_key 变 ⇒ **STALE**：可能压根不是同一个对象，寻址**拒绝**。
    - struct_key 同、text_key 变 ⇒ **TEXT_CHANGED**：同一个对象、内容变了 ——
      动作核验**期待**这一档（「点完它变成 X 了」正是成功的证据）。
    - 两者都同 ⇒ **OK**。
    - 没有可比基线（prev 空 / uid 拆不开）⇒ **STALE**：认不出身份就别拿它当身份用。
    """
    ps, pt = _screen_split_uid(prev_uid)
    cs, ct = _screen_split_uid(cur_uid)
    if not (ps and pt and cs and ct):
        return "STALE"
    if ps != cs:
        return "STALE"
    if pt != ct:
        return "TEXT_CHANGED"
    return "OK"


def _screen_uid_state_against(prev_rec, cur_uid, cur_meta=None) -> str:
    """拿上一屏快照判当前 uid 落在哪一档。prev_rec 可以是快照记录，也可以是 uid 集合。

    ★ **配对顺序是关键**：先按 label（控件自述的内容）找前身，再按 struct_key 找。
      - 同 label、路径变了 ⇒ 它是**同一个对象挪了位置** ⇒ STALE（寻址必须拒绝）。
        ★ 只按路径配对会判错：在按钮队列前面插入一个之后，「选项格02」正好落在
          「选项格01」原来的路径上，只看路径会把它说成「同对象改了字」（TEXT_CHANGED），
          而它其实是另一个对象占了那个位置。判据 (8)③ 就是这么把它逮住的。
      - 同路径、label 不同、**矩形还重叠** ⇒ 同位置上的同一个东西换了内容 ⇒ TEXT_CHANGED。
      - 都没有 ⇒ STALE（这是个新来的对象）。
    """
    if isinstance(prev_rec, dict):
        prev_uids = list(prev_rec.get("uids_all") or ())
        meta = dict(prev_rec.get("meta") or {})
    else:
        prev_uids, meta = list(prev_rec or ()), {}
    if not prev_uids:
        return "STALE"
    if cur_uid in set(prev_uids):
        return "OK"                                  # 同一个 uid ⇒ struct/text 都同
    cs, _ct = _screen_split_uid(cur_uid)
    if not cs:
        return "STALE"
    cur_meta = cur_meta or {}
    cl = str(cur_meta.get("label") or "")
    same_label = sorted(u for u in prev_uids
                        if cl and str((meta.get(u) or {}).get("label") or "") == cl)
    if same_label:
        # 内容一模一样却出现在别的路径 ⇒ 同一个对象挪了位置（uid 变是路径变，不是内容变）
        return "OK" if cs == _screen_split_uid(same_label[0])[0] else "STALE"
    best, best_iou = None, -1.0
    for u in sorted(u for u in prev_uids if _screen_split_uid(u)[0] == cs):
        iou = _screen_rect_iou(cur_meta.get("rect"), (meta.get(u) or {}).get("rect"))
        if iou > best_iou:
            best, best_iou = u, iou
    if best is not None and best_iou >= SCREEN_SAME_SLOT_IOU:
        return screen_uid_state(best, cur_uid)        # 同位置同内容线 ⇒ TEXT_CHANGED
    return "STALE"


# 「还是原来那个东西」的最低重叠度（IoU）。同位置 + 内容变 + 还压在同一块地方，
SCREEN_SAME_SLOT_IOU = 0.5          #   才认「同对象换了内容」；只占同一个路径而地方不同 ⇒ 多半是换了新对象 ⇒ STALE。


def _screen_rect_iou(a, b) -> float:
    """两个 [l,t,r,b] 的交并比。缺坐标/零面积一律 0.0（判不出来就当「不像」）。"""
    def _norm(x):
        try:
            v = [float(i) for i in list(x or [])[:4]]
        except (TypeError, ValueError):
            return None
        return v if len(v) == 4 else None

    ra, rb = _norm(a), _norm(b)
    if not ra or not rb:
        return 0.0
    iw = max(0.0, min(ra[2], rb[2]) - max(ra[0], rb[0]))
    ih = max(0.0, min(ra[3], rb[3]) - max(ra[1], rb[1]))
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    aa = max(0.0, ra[2] - ra[0]) * max(0.0, ra[3] - ra[1])
    bb = max(0.0, rb[2] - rb[0]) * max(0.0, rb[3] - rb[1])
    union = aa + bb - inter
    return (inter / union) if union > 0 else 0.0


def _screen_struct_keys(nodes: list) -> list:
    """按 walk 给的 depth 还原祖先链，产出每个节点的**结构路径** struct_key。

    为什么能还原：uia_tree.walk 是先序 DFS，被丢掉的中间层**照样往下走、depth 照样加**，
    所以保留下来的节点里，「depth 小于我且最近的那个」就是我的祖先，一个栈就能还原链。

    ⚠ 口径：祖先链是在**保留下来的骨架**上还原的（被 uia_tree 按角色/零矩形丢掉的
      中间层不会出现）。骨架口径由 ctl_screen 自己钉死（roles=all、全量遍历），
      与 top / include_offscreen 无关 ⇒ 同一窗口状态下 uid 稳定。
    """
    out = []
    stack = []            # [(depth, 段名)]，先序 DFS 的祖先链
    # ★ counts[i] 是 **stack[i] 那个节点的子节点计数表**（同角色已数几个），
    #   不是「深度 i 的计数表」。差在哪：同父的兄弟必须**接着数**（PaneControl[1]），
    #   换父了才从头数。按深度建表会把兄弟编号清零 ⇒ 同一父下的同角色节点
    #   拿到**一模一样的 struct_key**（本机实测：三个并列题干文本的路径全等），
    #   那样两个同父同角色**同名**的控件 uid 就会撞车 —— 身份直接废了。
    counts = [{}]         # counts[0] 记「遍历根」的直接子节点
    for n in nodes:
        d = int(n.get("depth") or 0)
        while len(stack) > d:
            stack.pop()
            counts.pop()
        role = str(n.get("type") or "")
        kids = counts[-1]
        k = kids.get(role, 0)
        kids[role] = k + 1
        seg = role if k == 0 else "%s[%d]" % (role, k)
        stack.append((d, seg))
        counts.append({})
        out.append("/".join([x for _, x in stack[-SCREEN_STRUCT_KEY_SEGS:]]))
    return out


def _screen_query_hit(query: str, label: str) -> float:
    """query 命中度 ∈ {0.0, 1.0}。整串包含算 1.0；按空白分词后**全中**也算 1.0。"""
    q = _screen_norm_text(query)
    lab = _screen_norm_text(label)
    if not q or not lab:
        return 0.0
    if q in lab:
        return 1.0
    toks = [t for t in q.split(" ") if t]
    if toks and all(t in lab for t in toks):
        return 1.0
    return 0.0


def _screen_score(*, in_view: bool, interactive: bool, query_hit: float,
                  area_ratio: float, delta: bool) -> float:
    """**可解释的线性打分**。权重全在 SCREEN_SCORE_WEIGHTS，每一项值多少分一眼看得见。"""
    w = SCREEN_SCORE_WEIGHTS
    return round(w.get("in_view", 0.0) * (1.0 if in_view else 0.0)
                 + w.get("interactive", 0.0) * (1.0 if interactive else 0.0)
                 + w.get("query", 0.0) * float(query_hit)
                 + w.get("area", 0.0) * max(0.0, min(1.0, float(area_ratio)))
                 + w.get("delta", 0.0) * (1.0 if delta else 0.0), 4)


def _screen_select(cands: list, top: int, guarantee: bool = True) -> tuple:
    """注意力裁剪。返回 (保留的 id 集合, 被裁掉的条数)。

    ★ **必进保证**：guarantee=True 时，所有「能交互 且 这一屏看得见」的节点**先进集合**，
      剩下的名额才按分补 —— 所以返回条数**可以超过 top**。
      这是刻意的：裁掉一个现在能点的按钮，agent 只会得出「界面上没有它」，
      而那是个会让它放弃任务的错误结论。宁可多花点 token。
    ⚠ 判据用 `in_view is not False` 而不是 `is True`：客户区取不到时 in_view 是 None（未知），
      按 uia_tree 既定口径「判不出来就当看得见」处理，宁可多留。
    """
    # ★ 声明必进与「必进保证」是**并列**的两个 must 集合（Z3 §4.3）：
    #   必进保证保 interactive ∧ in_view；声明必进保 watched（不受 in_view 限制）。
    must = [o for o in cands if ((o["interactive"] and o["in_view"] is not False)
                                 or o.get("watched"))]
    ranked = sorted(cands, key=lambda o: (-o["score"], tuple(o["rect"]), o["id"]))
    budget = max(int(top), len(must)) if guarantee else max(int(top), 0)
    keep = set()
    if guarantee:
        keep.update(o["id"] for o in must)
    for o in ranked:
        if len(keep) >= budget:
            break
        keep.add(o["id"])
    return keep, max(0, len(cands) - len(keep))


# ── 新鲜度快照（delta 项 + STALE 判断的地基）────────────────────────────────
#   服务端内存，按 hwnd 记「上次这屏上都有哪些 uid」。
#   没有基线时 delta 一律记 0 —— 不假装「全都是新面孔」（那会让每帧凭空多 0.10）。
SCREEN_SNAP_TTL_SEC = 30.0
SCREEN_SNAP_MAX_HWNDS = 32
# ★ 窗G3c：click.after 里 changed_objects 的条数上限（体验加成，给个硬顶防信封膨胀）
SCREEN_AFTER_CHANGED_CAP = 8
_SCREEN_SEEN = {}


def _screen_snap_get(hwnd: int):
    rec = _SCREEN_SEEN.get(int(hwnd))
    if not rec:
        return None
    if time.time() - float(rec.get("t") or 0) > SCREEN_SNAP_TTL_SEC:
        return None
    return rec


def _screen_snap_put(hwnd: int, version: str, uids, meta, uids_all=None, nodes_n=0):
    return _screen_snap_put_ex(hwnd, version, uids, meta, uids_all, nodes_n, True)


def _screen_snap_put_ex(hwnd: int, version: str, uids, meta, uids_all=None,
                        nodes_n=0, caller_seen: bool = True):
    """★ 窗G3c：快照拆成**两套字段**，各管各的。

    - `uids` / `uids_all` / `meta` / `节点数`：**内部工作集**。
      暖机就绪判定的「与上次相比」用 `节点数`，其余供同一次调用内部配对。
    - `seen_version` / `seen_uids` / `seen_uids_all` / `seen_meta`：
      **调用方可见的基线** —— since 对照、version_match、每对象 uid_state、
      delta 新增/消失，**一律只读这一套**。

    为什么要分：`ctl_click` 动作后要回读一次（判被点对象的 uid_state）。
    那次回读以前会顺手把快照整份推进，于是调用方随后带 since 再比时，
    「这一屏发生过什么」已经被**点击自己的回读**吃掉了 —— 真窗口实测表现为
    `version_match=false` 却 `changed_objects=[]`、明明变了字的控件还报 OK。
    `caller_seen=False`（只有 after 回读走这条）：只刷内部工作集，
    `seen_*` **原样留着** —— 调用方看到的「上次那版」不被动作内部的读顶掉。

    ⚠ **不是清空快照**：老记录一律留着，只是 after 那趟不许写 seen_*。
    `t` 也原样保留 ⇒ 调用方基线的 30 秒寿命照旧从它自己那次 ctl_screen 起算。
    """
    now = time.time()
    if len(_SCREEN_SEEN) >= SCREEN_SNAP_MAX_HWNDS:
        for k in sorted(_SCREEN_SEEN, key=lambda x: _SCREEN_SEEN[x].get("t") or 0)[:8]:
            _SCREEN_SEEN.pop(k, None)
    allu = set(uids_all if uids_all is not None else uids)
    rec = {"screen_version": version,
           "uids": set(uids),
           # ★ uid_state 的基线用**全量**候选 uid（含视口外）：
           #   一个对象滑出视口再滑回来，不该被判成「不是上次那个」。
           #   delta 仍只用视口内的 uids —— 屏外的东西本来就不算「变了」。
           "uids_all": allu,
           # ★ 这一趟**扫到多少节点** —— 就绪判定依据①的「上一次」基线
           "节点数": int(nodes_n or 0),
           # ★ 每个 uid 的自述（label/role/rect）：uid_state 配对要用
           "meta": dict(meta or {})}
    old = _SCREEN_SEEN.get(int(hwnd)) or {}
    if caller_seen:
        rec.update({"t": now,
                    "seen_version": version,
                    "seen_uids": set(uids),
                    "seen_uids_all": set(allu),
                    "seen_meta": dict(meta or {})})
    else:
        # after 回读：seen_* 与 t 一律**沿用老记录**（没有老记录就是「没有基线」）。
        rec.update({"t": old.get("t", now),
                    "seen_version": old.get("seen_version", ""),
                    "seen_uids": set(old.get("seen_uids") or ()),
                    "seen_uids_all": set(old.get("seen_uids_all") or ()),
                    "seen_meta": dict(old.get("seen_meta") or {})})
    _SCREEN_SEEN[int(hwnd)] = rec


def _screen_seen_view(prev):
    """把快照记录里**调用方可见**的那一套摘出来，喂给 uid_state 配对 / delta 对照。"""
    if not prev:
        return None
    return {"uids_all": set(prev.get("seen_uids_all") or ()),
            "meta": dict(prev.get("seen_meta") or {}),
            "screen_version": str(prev.get("seen_version") or "")}


def _screen_forget(hwnd: int = 0):
    """丢掉快照基线（判据分阶段用；也留给将来「用户说『这屏重算』」）。"""
    if hwnd:
        _SCREEN_SEEN.pop(int(hwnd), None)
    else:
        _SCREEN_SEEN.clear()


def _screen_proc_name(pid: int) -> str:
    """进程可执行文件名。取不到就返回 ""（**不猜、不编**）。"""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                  wintypes.LPWSTR,
                                                  ctypes.POINTER(wintypes.DWORD)]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, int(pid))    # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return os.path.basename(buf.value)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return ""
    return ""


def _screen_window_info(hwnd: int) -> dict:
    """窗口身份：hwnd / title / cls / pid。**读不到就留空，不填占位值。**"""
    out = {"hwnd": int(hwnd), "title": "", "cls": "", "pid": 0}
    try:
        import win32gui
        import win32process
        out["title"] = win32gui.GetWindowText(int(hwnd)) or ""
        out["cls"] = win32gui.GetClassName(int(hwnd)) or ""
        try:
            out["pid"] = int(win32process.GetWindowThreadProcessId(int(hwnd))[1])
        except Exception:
            out["pid"] = 0
    except Exception:
        pass
    return out


# 修正④的第三档（**Z2 二次修正**）：网页选项「疑似错位」。
#   判据是**可观察的**：把像选项的节点按屏幕 y 排序，看字母序列是不是 A,B,C,… 的规范序；
#   乱序 / 重复 / 缺号就是**疑似**。它只负责把这一屏**踢去 VLM**（宁可多看一眼），
#   **不负责断言「一定错位」**。★ 只在网页窗口上生效 —— 原生窗口字母乱序多半是排版。
SCREEN_OPTION_LETTER_RE = re.compile(r"^[\s（(\[【]*([A-Za-z])\s*[.、)）:：．。]")
SCREEN_OPTION_ROLES = frozenset({"RadioButtonControl", "CheckBoxControl",
                                 "ListItemControl", "ComboBoxControl"})
SCREEN_OFFSET_MIN_OPTIONS = 3


def _screen_is_web_window(win: dict) -> bool:
    cls = str((win or {}).get("cls") or "").lower()
    if cls and any(k in cls for k in SCREEN_WEB_CLS_HINTS):
        return True
    proc = _screen_proc_name((win or {}).get("pid") or 0).lower()
    return bool(proc and any(k in proc for k in SCREEN_WEB_PROC_HINTS))


def _screen_offset_suspect(cands) -> dict:
    """网页选项「疑似错位」取证。返回 {suspect, 字母序列, 规范序, 样本, 依据}。"""
    rows = []
    for o in (cands or []):
        lab = str(o.get("label") or "").strip()
        m = SCREEN_OPTION_LETTER_RE.match(lab) if lab else None
        if not m:
            continue
        if (str(o.get("role") or "") not in SCREEN_OPTION_ROLES
                and not o.get("interactive")):
            continue
        rect = list(o.get("rect") or [0, 0, 0, 0])
        rows.append((int(rect[1]), m.group(1).upper(), lab))
    if len(rows) < SCREEN_OFFSET_MIN_OPTIONS:
        return {"suspect": False,
                "字母序列": [r[1] for r in rows],
                "依据": "像选项的节点只有 %d 个（< %d），够不上错位判据"
                        % (len(rows), SCREEN_OFFSET_MIN_OPTIONS)}
    rows.sort(key=lambda t: (t[0], t[1]))
    letters = [r[1] for r in rows]
    canon = [chr(ord("A") + i) for i in range(len(letters))]
    if letters == canon:
        return {"suspect": False, "字母序列": letters, "规范序": canon,
                "样本": [r[2][:24] for r in rows[:8]],
                "依据": "选项字母按屏幕顺序是 %s，与规范序一致" % "".join(letters)}
    return {"suspect": True, "字母序列": letters, "规范序": canon,
            "样本": [r[2][:24] for r in rows[:8]],
            "依据": "选项字母按屏幕顺序是 %s，规范序应为 %s（疑似 label 与位置错位）"
                    % ("".join(letters), "".join(canon))}


def _screen_name_trust_detail(win: dict, cands=None) -> tuple:
    """返回 (name_trust, 依据)。三档：**offset-suspect** / low / high。

    ★ 修正④的老口径一字未改（网页 low、原生 high、判不出来 low），
      **Z2 加的是第三档**：网页 + 选项疑似错位 ⇒ offset-suspect。
      它比 low 更重 —— low 只是「别当权威文本」，offset-suspect 是「**连寻址都不许信**」：
      于是 R2 门槛不通过，这一屏**必须**落到 R3/VLM（错位页面用 UIA 自述会假通过）。
    """
    win = win or {}
    if not str(win.get("cls") or "") and not _screen_proc_name(win.get("pid") or 0):
        return "low", "窗口身份读不到（cls/pid 都空）：判不出来按 low（保守）"
    if not _screen_is_web_window(win):
        return "high", "原生窗口（非网页/Chromium 类）：控件 name 可当权威文本用"
    off = _screen_offset_suspect(cands)
    if off.get("suspect"):
        return "offset-suspect", off.get("依据") or "网页选项疑似错位"
    return "low", "网页/Chromium 类窗口：name 来自 DOM/ARIA 映射，不是像素事实"


def _screen_name_trust(win: dict, cands=None) -> str:
    """name_trust 三档（只要判定那一半；依据那一半走 _screen_name_trust_detail）。"""
    return _screen_name_trust_detail(win, cands)[0]


def _screen_max_nodes(args: dict) -> int:
    try:
        n = int(args.get("max_nodes") or SCREEN_DEFAULT_MAX_NODES)
    except (TypeError, ValueError):
        n = SCREEN_DEFAULT_MAX_NODES
    return max(50, min(SCREEN_MAX_NODES_CEIL, n))

# ══ 暖机与就绪判定（窗F2 · 框架 §⑲/§⑳）═══════════════════════════════════
# 真页事实（不是推测）：Chromium 的无障碍树**懒启用** —— 开窗后 0.5 秒只拿到 18 个
# 节点的外壳，3 秒才稳定到 49 个；首扫 matched=[]（声明一条都匹配不上），
# 等 2 秒同一调用就中了。⇒ agent 第一次调 ctl_screen 很可能拿到**不完整的屏**就据此决策。
# 本段治的就是这件事：**要么暖到再交，要么如实说「本屏可能未热」**。
SCREEN_WARMUP_MAX_RETRY = 2      # 最多重试 2 次 ⇒ 一次调用最多采 3 趟（有界，不许无限等）
SCREEN_WARMUP_GAP_MS = 400        # 重试间隔（派单要求 300~500ms，取中值；确定性优先于抖动）
SCREEN_WARM_FAST_MS = 300         # 「采集太快」阈值：真树走 COM 不该这么快
SCREEN_SHELL_MAX_NODES = 24       # 「节点极少」阈值（真页外壳实测 18 个）
SCREEN_READY_SHRINK_RATIO = 0.6   # 节点数 < 上次的 60% ⇒ 疑心这趟是外壳
# ★ 窗F2b：网页类窗口的「热」门槛，按**可报对象数**（可交互或有 label 的节点）算。
#   ★ 出处是 2026-10-18 的**真 Chromium 页实测**（DSH，`work\_暖机真页复核_20261018.py`）：
#       冷扫 ≈ 18 个可报对象 / 60 个原始节点 / 372ms   ← 三条旧依据一条都没命中，被判成「已热」
#       热扫 ≈ 49 个可报对象 / 111 个原始节点 / 259ms
#     40 取在 18 与 49 正中间。**为什么按对象数而不是原始节点数**：
#     原始节点里大半是空壳 Pane（冷扫 60、热扫 111，60 并没低于 40 ⇒ 按节点数卡根本卡不住真页），
#     而 agent 真正吃的就是 objects —— 门槛必须卡在它最终看到的那个尺度上。
SCREEN_WEB_READY_MIN_OBJS = 40    # 网页类：可报对象 < 40 ⇒ 未热（不再看耗时）


def _screen_ready(nodes, stats, *, elapsed_ms, prev_nodes=0, is_web=False) -> tuple:
    """这棵树**热了没有**。返回 (ready: bool, 依据: list[str])。

    ★ 三条依据（写死、可调；任一命中即判未热）：
      ① 节点数明显少于**上一次同窗口扫描**（< 60%）—— 树被抽走了（懒启动的典型形状）；
      ② 可交互节点数 == 0 而窗口客户区非零 —— 一屏连个能点的都没有，多半只拿到外壳；
      ③ 采集耗时很短却节点极少（外壳特征）—— 冷启动时树还没长出来，走一趟当然快。
      ③b **网页类专属**：可报对象数 < SCREEN_WEB_READY_MIN_OBJS ⇒ 未热，**不看耗时**
          （真页冷扫是 60 节点/372ms，旧的「过快+极少」永远撞不上；见 3276 附近出处注释）。
    ★ 全部三条都只在「客户区非零」时才有意义：零尺寸窗口的 0 节点不是「未热」。
    ★ 宁可**保守报未热**：报「可能没热」的代价是多扫几趟；反过来（把外壳当热屏交出去）
      的代价是 agent 据此断定「页面上没有这个东西」—— 那是事实层面的假绿。
    """
    nodes = list(nodes or [])
    st = stats or {}
    client = st.get("视口") or None
    w = h = area = 0
    if client and len(client) == 4:
        w = max(0, int(client[2]) - int(client[0]))
        h = max(0, int(client[3]) - int(client[1]))
        area = w * h
    n = len(nodes)
    n_int = sum(1 for x in nodes
                if str(x.get("type") or "") in uia_tree.INTERACTIVE_TYPES)
    # 可报对象数：与 _screen_build 里筛候选的口径**逐字一致**（可交互 或 有 label）——
    # 门槛必须卡在 agent 最终真正看到的那个尺度上，否则测的是一棵没人吃的树。
    n_obj = sum(1 for x in nodes
                if (str(x.get("type") or "") in uia_tree.INTERACTIVE_TYPES)
                or str(x.get("name") or "").strip())
    why = []
    if area > 0:
        if prev_nodes and n < prev_nodes * SCREEN_READY_SHRINK_RATIO:
            why.append("节点数 %d 明显少于上一次的 %d（< %d%%）"
                        % (n, int(prev_nodes), int(SCREEN_READY_SHRINK_RATIO * 100)))
        if n_int == 0:
            why.append("可交互节点 0 个，而客户区非零（%d×%d）—— 多半只拿到外壳" % (w, h))
        if float(elapsed_ms) < SCREEN_WARM_FAST_MS and n <= SCREEN_SHELL_MAX_NODES:
            why.append("采集只用了 %.0fms 却只有 %d 个节点（外壳特征）" % (elapsed_ms, n))
        if is_web and n_obj < SCREEN_WEB_READY_MIN_OBJS:
            why.append("网页类窗口只有 %d 个可报对象（< %d）—— Chromium 无障碍树多半还没长全"
                        % (n_obj, SCREEN_WEB_READY_MIN_OBJS))
    if not why and is_web:
        # 热了也要说清是**按哪把尺子**量的 —— 否则调用方没法判断这个 true 可不可信。
        return True, ["网页类可报对象 %d 个（≥ %d），节点数/可交互/采集耗时三项也正常"
                      % (n_obj, SCREEN_WEB_READY_MIN_OBJS)]
    return (not why), why


def _screen_min_probes(*, warmup, is_web, first_scan) -> int:
    """一次调用**至少**要采几趟。

    ★ 窗F2b：**网页类窗口本进程首扫**（`_screen_snap_get` 为空 ⇒ 依据①没有基线可比）
      强制 2 趟 —— 树是懒启用的，第一趟往往只拿到外壳，而此时**三条依据一条都判不出来**：
      依据① 没基线跳过、依据② 冷屏仍有可交互外壳（≠0）、依据③ 真页是 372ms/60 节点撞不上阈值。
      真页实测（2026-10-18）：冷扫 18 对象被当成热屏交出去，热扫才有 49 个。
    ★ **非网页类维持 1 趟**：别给记事本这类原生窗口白加 400ms。
    ★ 上限仍由 SCREEN_WARMUP_MAX_RETRY 兜着（最多 3 趟），这里只抬**下限**。
    """
    return 2 if (warmup and is_web and first_scan) else 1


def _screen_build(hwnd: int, args: dict) -> dict:
    """语义屏本体。**只读**：不发点击、不 Invoke、不改窗口。"""
    import hashlib as _hl

    hwnd = int(hwnd)
    top = max(1, int(args.get("top") or SCREEN_DEFAULT_TOP))
    include_offscreen = bool(args.get("include_offscreen") or False)
    query = str(args.get("query") or "")
    since = args.get("since")
    # ★ 任务声明制：t_screen 进闸门**之前**已经先验过一次（超限/形态错直接拒），
    #   这里再解析一次只为拿到规范化后的声明表（纯函数、≤32 条，代价可忽略）。
    watch_decls, watch_err = _screen_watch_parse(args)
    watch_pending = bool(args.get("watch_pending") or False)
    watch_hits = {}

    win = _screen_window_info(hwnd)
    # ★ name_trust 改成**建完候选之后再算**：offset-suspect 那档得看着一屏的选项才判得出来
    #   （见 _screen_offset_suspect）。

    max_nodes = _screen_max_nodes(args)
    max_depth = int(args.get("max_depth") or uia_tree.DEFAULT_MAX_DEPTH)
    warm_raw = args.get("warmup")
    warmup = True if warm_raw is None else bool(warm_raw)

    # ★ 窗F2b：网页类（Chromium/FF/Electron）窗口 ⇒ 首扫要**强制探两次**（见 _screen_min_probes）。
    is_web = _screen_is_web_window(win)

    # ★ 固定口径：roles=("all",) + include_offscreen=True。
    #   语义屏要的是**完整结构骨架**（uid 的路径必须在全树上算才有意义），
    #   视口过滤与 top-N 放在**之后**做 —— 否则同一控件滚出屏幕就会换 uid。
    #
    # ★ 窗F2：**有界**暖机。prev 要在走之前读 —— 就绪判定依据①拿它当「上一次」的基线。
    prev = _screen_snap_get(hwnd)
    # ★ 窗G3c：prev_uids / uid_state 配对 / delta 对照一律走**调用方可见**那套
    #   （seen_*）。after 回读只刷内部工作集，不许顶掉调用方的基线。
    #   prev_nodes 仍读内部工作集（暖机要的是「上一次扫了多少节点」这个事实）。
    prev_seen = _screen_seen_view(prev)
    prev_uids = set(((prev or {}).get("seen_uids")) or ())
    prev_nodes = int((prev or {}).get("节点数") or 0)
    first_scan = not prev
    min_probes = _screen_min_probes(warmup=warmup, is_web=is_web, first_scan=first_scan)

    # ★ 窗G1：网页类窗口顺带把 **CDP 通道探一下**（R1 的三要素就在这儿落地）。
    #   非网页类不探 —— UIA 已经够了，为一个原生窗口去连调试端口纯属浪费。
    #   探不到就是探不到：下面 R1 会如实报 false，不假装「查过了，没有」。
    cdp_port = int(args.get("cdp_port") or CDP_DEFAULT_PORT)
    cdp = (_cdp_probe(port=cdp_port,
                      url_contains=str(args.get("cdp_url_contains") or ""),
                      query=str(args.get("query") or ""),
                      want_dom=bool(args.get("cdp_dom") is not False))
           if (is_web and args.get("cdp") is not False) else
           {"wired": False, "reachable": False, "dom_hit": False, "dom": None,
            "page_url": "", "browser": "", "端口": cdp_port,
            "原因": ("这一屏不是网页类窗口（或显式关了 cdp 参数），没去连调试端口"
                     if not is_web else "调用方显式关掉了 cdp 通道")})

    best = None
    attempts = 0
    waited_ms = 0.0
    n_tries_log = []
    while True:
        st = {}
        t_walk = time.time()
        walked, _trunc = uia_tree.walk(hwnd, max_nodes=max_nodes, max_depth=max_depth,
                                      roles=("all",), include_offscreen=True, stats=st)
        walk_ms = (time.time() - t_walk) * 1000.0
        attempts += 1
        n_tries_log.append(len(walked))
        # ★ 取**节点数更多**的那次（平手取先到的：同一屏两趟一样就没必要换）。
        if best is None or len(walked) > len(best["nodes"]):
            best = {"nodes": walked, "stats": st, "ms": walk_ms}
        ready, ready_why = _screen_ready(best["nodes"], best["stats"],
                                         elapsed_ms=best["ms"], prev_nodes=prev_nodes,
                                         is_web=is_web)
        # ★ 窗F2b：ready 也得先满足 min_probes（网页类首扫要探够两次）；
        #   上限不变 —— 最多仍是 3 趟，不许变成无界循环。
        if (not warmup or attempts > SCREEN_WARMUP_MAX_RETRY
                or (ready and attempts >= min_probes)):
            break
        time.sleep(SCREEN_WARMUP_GAP_MS / 1000.0)
        waited_ms += SCREEN_WARMUP_GAP_MS
    nodes, stats, walk_ms = best["nodes"], best["stats"], best["ms"]
    client = stats.get("视口") or None
    scroll = stats.get("滚动") or {}
    struct_keys = _screen_struct_keys(nodes)

    base_area = 0
    if client and len(client) == 4:
        base_area = (max(0, int(client[2]) - int(client[0]))
                     * max(0, int(client[3]) - int(client[1])))

    cands = []
    n_all_cands = 0
    n_view_dropped = 0
    used_ids = set()
    for i, n in enumerate(nodes):
        role = str(n.get("type") or "")
        label = (n.get("name") or "").strip()
        interactive = role in uia_tree.INTERACTIVE_TYPES
        if not (interactive or label):
            continue                      # 无交互也无字：既不是对象，也不进 texts
        # ★ 真实总数**含屏外**：屏外那些也是这屏上的对象，只是「现在看不见」。
        #   少算它们会让 total-returned 对不上「到底丢了多少」，那就不叫诚实报数了。
        n_all_cands += 1
        in_view = n.get("in_view")
        # ★ 规则2：声明压过视口过滤 —— 要盯的东西滚出屏幕**不能失去比对**
        #   （任务页要滚动）。in_view 字段仍**照实报 false**，不粉饰。
        watch_matched = _screen_watch_matches(label, watch_decls)
        watched = bool(watch_matched)
        if in_view is False and not include_offscreen and not watched:
            n_view_dropped += 1
            continue
        rect = list(n.get("rect") or [0, 0, 0, 0])
        # ★ 三件事**分开报**，别混成一个 volatile：
        #   volatile = 这串**长得像**时刻/计数类（事实，声明改不了它）
        #   exempted = 这次**真被豁免**了（声明压过一切豁免源 ⇒ 被声明了就恒为 false）
        #   watched   = 你声明过它（任务声明制）
        #   判据 S1 盯的就是这三者的组合：volatile=true ∧ watched=true ⇒ exempted=false。
        volatile = _screen_is_volatile(role, label)         # ← 修正①：事实
        exempted = bool(volatile and not watched)            # ← 规则1：声明即不豁免
        for orig in watch_matched:               # 全收记账（F1）：命中几条声明就记几条
            watch_hits[orig] = watch_hits.get(orig, 0) + 1
        skey = struct_keys[i] if i < len(struct_keys) else role
        uid = "%s@%s" % (skey, _screen_text_key(label, exempted))   # ← 修正①：两段
        oid = "o" + _hl.sha1(uid.encode("utf-8")).hexdigest()[:6]
        if oid in used_ids:               # 哈希撞了就加后缀，不许两个对象同号
            k = 2
            while "%s-%d" % (oid, k) in used_ids:
                k += 1
            oid = "%s-%d" % (oid, k)
        used_ids.add(oid)
        area = max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])
        area_ratio = (float(area) / float(base_area)) if base_area else 0.0
        cf = _agg_source_conf_attrs(_screen_evidence(uid, n))    # ← 修正③（属性级）
        is_new = bool(prev_uids) and uid not in prev_uids
        obj = {
            "id": oid,
            # ★ 窗G3：eid 与 uid **并存、不互当前别名** ——
            #   eid 说「上次那批里的哪个」（点哪），uid 说「还是不是那个对象」（身份）。
            #   eid 是**遍历当场编的号**（ctl_tree/ctl_find 同一套旁表），
            #   必须带着 from_capture 一起用；uid 是结构+文本指纹，跨调用可比。
            "eid": (n.get("eid") if isinstance(n, dict) else None),
            "uid": uid,
            "label": label[:120],
            "role": role,
            "rect": rect,
            "in_view": bool(in_view is not False),
            "interactive": bool(interactive),
            # ★ 不可交互的节点**不承诺任何动作**：它是题干/备注这类文字，
            #   报 actions=["click"] 等于凭空许一个承诺（实测踩过：模型照着去点文字）。
            "actions": list(SCREEN_ACTIONS.get(role, ["click"])) if interactive else [],
            "source": "+".join(cf["sources"]) if cf["sources"] else "none",
            # ★ 修正③（属性级）：聚合口径 = min(existence, text)（「读得准不准」），
            #   **bbox 单列** —— UIA 的精确坐标不会被 OCR 的低分文本拖低。
            "source_confidence": cf["agg"],
            "conf_bbox": cf["bbox"],
            "conf_text": cf["text"],
            "conf_existence": cf["existence"],
            "volatile": bool(volatile),
            "exempted": bool(exempted),      # ← 规则1：这次真被豁免了没有（声明压过豁免）
            "watched": bool(watched),        # ← 任务声明制：这条被你声明过
            "score": _screen_score(in_view=in_view is not False,
                                   interactive=interactive,
                                   query_hit=_screen_query_hit(query, label),
                                   area_ratio=area_ratio, delta=is_new),
        }
        # ★ ② uid 三档：**只有给了 since 才产出** —— 没有可比基线就没有「变没变」可判。
        if since:
            obj["uid_state"] = _screen_uid_state_against(
                prev_seen, uid, {"label": label, "role": role, "rect": rect})
        cands.append(obj)

    # ★ 修正④第三档要**看着一屏的选项**才判得出来 ⇒ name_trust 在这里算，然后逐个盖上。
    trust, trust_ev = _screen_name_trust_detail(win, cands)
    for o in cands:
        o["name_trust"] = trust

    total_cands = n_all_cands
    keep, n_topn_dropped = _screen_select(cands, top, guarantee=True)     # ← 必进保证
    ranked = sorted(cands, key=lambda o: (-o["score"], tuple(o["rect"]), o["id"]))
    objects = [o for o in ranked if o["id"] in keep]

    # ── 修正②：**服务端签发**。这个 blob 里装的全是服务端自己算出来的事实；
    #    since / version / screen_version 一概**不进来** ⇒ 调用方没有指定版本的入口。
    viewport = {"client": list(client) if client else None,
                "scroll_y": scroll.get("scroll_y"),
                "doc_height": scroll.get("doc_height"),
                "scroll_unknown": bool(scroll.get("scroll_unknown", True))}
    version_blob = json.dumps({
        "hwnd": hwnd, "title": win.get("title"), "cls": win.get("cls"),
        "pid": win.get("pid"), "client": viewport["client"],
        "scroll_y": viewport["scroll_y"], "doc_height": viewport["doc_height"],
        "uids": sorted(o["uid"] for o in cands if o["in_view"] or o.get("watched")),
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    screen_version = "sv1-" + _hl.sha256(version_blob).hexdigest()[:16]

    # 新鲜度只在**视口内**的对象之间比：屏外的东西本来就不算「变了」。
    in_view_now = set(o["uid"] for o in cands if o["in_view"])
    _screen_snap_put_ex(
        hwnd, screen_version, in_view_now,
        {o["uid"]: {"label": o["label"], "role": o["role"],
                    "rect": list(o["rect"])} for o in cands},
        uids_all=set(o["uid"] for o in cands), nodes_n=len(nodes),
        # ★ 窗G3c：`_不推进seen` 只有动作后回读那趟带 —— 它不该改写
        #   「调用方上次看到的那一版」，否则动作内部的读会把对外的差分吃掉。
        caller_seen=not args.get("_不推进seen"))

    texts = [{"id": "t%d" % k, "text": o["label"], "rect": o["rect"]}
             for k, o in enumerate(objects) if o["label"]]

    n_interactive = sum(1 for o in cands if o["interactive"] and o["in_view"])
    hit_labels = " ".join(o["label"] for o in cands if o["in_view"])
    channels = screen_channels(interactive_count=n_interactive,
                               target_hit=bool(_screen_query_hit(query, hit_labels)),
                               name_trust=trust,      # ← ③ R2 的 name_trust 门槛
                               # ← 窗G1：R1 用**真**的 CDP 三要素（DOM 命中，不是 UIA 的那个）
                               cdp_wired=bool(cdp.get("wired")),
                               cdp_reachable=bool(cdp.get("reachable")),
                               dom_hit=bool(cdp.get("dom_hit")))
    # R1 命中时把 DOM 侧的证据摆出来：命中几个节点、按什么置信度算的。
    channels["R1_cdp"]["命中节点数"] = int(cdp.get("命中节点数") or 0)
    channels["R1_cdp"]["page_url"] = str(cdp.get("page_url") or "")
    channels["R1_cdp"]["端口"] = int(cdp.get("端口") or cdp_port)
    channels["R1_cdp"]["source_confidence"] = dict(SCREEN_SOURCE_CONF["cdp"])
    # ★ 窗G2 · OCR（R3）。**三态**：
    #   ocr=true  强制跑；ocr=false 不跑；不给 = auto ——
    #   **只有当 R1、R2 都没命中目标、且调用方给了 query 时才自动跑**。
    #   理由：OCR 要截图+识别，是三条通道里最贵的一条；而它唯一不可替代的场景是
    #   「像素里有字、UIA 瞎了」（Canvas / 游戏 / Electron），那正是 auto 要救的场合。
    ocr_arg = args.get("ocr")
    want_ocr = (ocr_arg is not None and bool(ocr_arg)) or (
        ocr_arg is None
        and bool(query)
        and not (channels.get("R1_cdp") or {}).get("hit")
        and not (channels.get("R2_uia") or {}).get("hit")
        and int(base_area) > 0)
    ocr_off = {"wired": False, "hit": False, "usable": False,
               "原因": "调用方关掉了 OCR 通道"}
    ocr = (_screen_ocr(hwnd, query=query,
                       lang=str(args.get("ocr_lang") or "zh-Hans-CN"),
                       max_ms=int(args.get("ocr_max_ms") or 1500))
           if want_ocr else ocr_off)
    if want_ocr:
        # ★ CDP 与 OCR **同时在**时两条通道都要报出来 —— 第二次调用只补 OCR 三个字段，
        #   R1/R2 的判定在 screen_channels 里是彼此独立的，不会互相覆盖。
        channels = screen_channels(
            interactive_count=n_interactive,
            target_hit=bool(_screen_query_hit(query, hit_labels)),
            name_trust=trust,
            cdp_wired=bool(cdp.get("wired")), cdp_reachable=bool(cdp.get("reachable")),
            dom_hit=bool(cdp.get("dom_hit")),
            ocr_wired=bool(ocr.get("wired")), ocr_hit=bool(ocr.get("hit")),
            ocr_bbox_usable=bool(ocr.get("usable")))
        channels["R1_cdp"]["命中节点数"] = int(cdp.get("命中节点数") or 0)
        channels["R1_cdp"]["page_url"] = str(cdp.get("page_url") or "")
        channels["R1_cdp"]["端口"] = int(cdp.get("端口") or cdp_port)
        channels["R1_cdp"]["source_confidence"] = dict(SCREEN_SOURCE_CONF["cdp"])
    ch3 = channels.setdefault("R3_ocr", {})
    ch3["命中节点数"] = len(ocr.get("命中") or [])
    ch3["dpi_scale"] = (ocr.get("res") or {}).get("dpi_scale")
    ch3["source_confidence"] = dict(SCREEN_SOURCE_CONF["ocr"])

    # ★ 窗G3 · ①：把这一屏的 eid 签进旁表，**口径必须就是这次 walk 的口径**。
    #   口径不对 = 拿不同的坐标系比坐标 ⇒ ctl_click 复核必然假失败（D1 那条教训）。
    #   include_offscreen=True 是**超集**，_eid_recheck 复核那趟也显式取超集（A2 要求），
    #   于是「签发在视口内、点之前滚了一下」不会被误报成「元素不见了」。
    #   tool 也记进去：旁表拒绝时要说得出「这次观测来自 ctl_screen」（见 _eid_refuse_missing）。
    eid_walk = {"tool": "ctl_screen", "roles": ["all"], "include_offscreen": True,
               "max_nodes": int(max_nodes), "max_depth": int(max_depth)}
    screen_capture_id = None
    # ★ 窗G3b：动作后回读那次**纯为拿 screen_version**，没人会拿它的 capture_id 去点 ——
    #   白签一张就是往旁表里塞垃圾（15 秒寿命、还带全量明细），不许。
    if objects and any(o.get("eid") for o in objects) and not args.get("_不发capture"):
        screen_capture_id = _issue_capture_from_tree(hwnd, {"命中": nodes}, eid_walk)

    out = {
        "screen_version": screen_version,                 # ← 修正②
        # ★ 窗F2：本屏**热了没有**。false 是「证据不足」，**不是错误码** ——
        #   信封照样 ok=true/code=0，调用方不许拿它当「页面上没有那个东西」。
        "screen_ready": bool(ready),
        "ready_证据": {"节点数": len(nodes),
                     "与上次相比": ("%+d" % (len(nodes) - prev_nodes)) if prev_nodes
                                   else "基线（本进程没有上一次扫描）",
                     "等待毫秒": int(waited_ms), "重试": max(0, attempts - 1),
                     "各次节点数": list(n_tries_log), "采集毫秒": int(walk_ms),
                     "窗口类别": "网页类" if is_web else "非网页类",
                     "首扫强制探测": min_probes >= 2,
                     "重试上限": SCREEN_WARMUP_MAX_RETRY, "间隔毫秒": SCREEN_WARMUP_GAP_MS,
                     "判定": ready_why or ["节点数、可交互数、采集耗时三项都正常"],
                     "口径": "依据① 节点数<上次60% / ②可交互=0而客户区非零 / ③采集过快而节点极少；"
                           "任一命中即判未热。screen_ready=false ⇒ **证据不足**，不是"
                           "「没有」—— 别据此断定页面上没有那个东西。"},
        "window": {"hwnd": win.get("hwnd"), "title": win.get("title"),
                   "cls": win.get("cls"), "pid": win.get("pid")},
        "viewport": viewport,
        "objects": objects,
        "texts": texts,
        # ★ 窗G3：点哪一把钥匙 + 它是怎么签发的（口径写清楚，agent 才知道能怎么用）
        "capture_id": screen_capture_id,
        "eid_口径": {"capture_id": screen_capture_id, "签发口径": eid_walk,
                     "用法": "ctl_click --eid <eid> --from_capture <capture_id> --expect_uid <uid>；uid 可选，给了就严格核对，对不上拒（STALE_SCREEN）",
                     "eid 与 uid": "并存、不互当前别名：eid 定位，uid 认身份",
                     "旁表": "与 ctl_tree/ctl_find 同一套（eid_sessions.json）",
                     "复核口径": "_eid_recheck 显式 include_offscreen=True 取超集，滚出视口仍可点"},
        "channels": channels,                              # ← 修正⑤
        # ★ 窗G1：DOM 通道（**缩水版**，不做 UIA×DOM 融合 —— 先看得见，再谈合并）。
        #   没连上时是 {wired:false, objects:[]} 并带一句原因，**不假装有通道**。
        "dom": dict(cdp.get("dom") or {"wired": False, "page_url": "", "objects": [],
                                        "omitted": {"total": 0, "returned": 0,
                                                    "cap": SCREEN_DOM_MAX_OBJECTS},
                                        "不可用原因": str(cdp.get("原因") or "")[:200]}),
        # ★ 窗G2 · OCR 段。没跑/跑不成时是 {wired:false, words:[]} + 一句原因，
        #   **不假装有像素这条证据**（同 CDP/未热那几条纪律）。
        "ocr": {"wired": bool(ocr.get("wired")), "hit": bool(ocr.get("hit")),
                "usable": bool(ocr.get("usable")),
                "跑了": bool(want_ocr),
                "原因": str(ocr.get("原因") or "")[:200],
                "引擎": str(ocr.get("引擎") or ""),
                "dpi_scale": (ocr.get("res") or {}).get("dpi_scale"),
                "words": list((ocr.get("res") or {}).get("words") or [])[:80],
                "omitted": dict((ocr.get("res") or {}).get("omitted")
                                or {"total": 0, "returned": 0, "cap": 80}),
                "ms": (ocr.get("res") or {}).get("ms")},
        "omitted": {
            "total": total_cands,                          # 真实候选总数（含屏外；视口过滤/top-N 之前）
            "returned": len(objects),
            "reason": ("attention_topn" if n_topn_dropped
                       else ("viewport_filter" if n_view_dropped else "none")),
            "top": top,
            "必进节点": sum(1 for o in cands
                            if o["interactive"] and o["in_view"] is not False),
            "breakdown": {
                "attention_topn": n_topn_dropped,
                "viewport_filter": n_view_dropped,
                "扫描预算丢弃（下界：树里还剩多少没人知道）": int(stats.get("因上限丢弃") or 0),
                "扫过节点": int(stats.get("扫过节点") or 0),
            },
            "说明": "total 是本口径下的候选对象数；「扫描预算丢弃」>0 时，"
                    "真实对象数**只可能更多**，这个数是下界。",
        },
        "打分口径": dict(SCREEN_SCORE_WEIGHTS),
        "必进保证": "interactive 且看得见的节点永不裁掉；因此 returned 可以超过 top。",
        "conf_口径": "source_confidence = min(conf_existence, conf_text)（「读得准不准」）；"
                     "conf_bbox 单列（「点得准不准」），**不被 conf_text 拖低**",
        "name_trust_口径": "high = 原生窗口；low = 网页/Chromium（name 来自 DOM/ARIA 映射，"
                           "判不出来也按 low）；offset-suspect = 网页且选项字母疑似与位置错位 "
                           "⇒ R2 门槛不通过、这一屏必须落到 VLM",
        "name_trust_依据": trust_ev,
        "uid_口径": "uid = struct_key@text_key；volatile 且未被声明（exempted=true）的节点"
                    "text_key 恒为 ~（不参与比对）；被声明（watched=true）的节点 text_key 照常实算",
        "uid_state_口径": "uid_state ∈ OK / TEXT_CHANGED / STALE（**只有给了 since 才有**）："
                          "struct_key 变 ⇒ STALE（可能不是同一对象，寻址拒绝）；"
                          "struct_key 同而 text_key 变 ⇒ TEXT_CHANGED（同对象内容变，"
                          "动作核验期待这一档）；两者都同 ⇒ OK。★ 配对顺序：先按 label "
                          "找前身（内容一样却换了路径 ⇒ 挪了位置 ⇒ STALE），再按 "
                          "struct_key 找同位置的且要矩形重叠（同位置同内容线才算换了内容）。",
        "actions_口径": "按角色推；interactive=false 的节点 actions 为空（不承诺它能点）",
    }

    # since / watch：**只用于对照**。回显 + 差分，绝不回写进 screen_version。
    # ★ 窗G3c：版本应答也读**调用方可见**那套 —— after 回读不许把版本顶掉，
    #   否则调用方回传自己真见过的 since 却拿到 version_match=false。
    prev_sv = str((prev or {}).get("seen_version") or "")
    if since or bool(args.get("watch")):
        new = sorted(in_view_now - set(prev_uids)) if prev else []
        gone = sorted(set(prev_uids) - in_view_now) if prev else []
        out["delta"] = {
            # ★ ④ 版本应答三件套：原样回传 / 对不对得上 / **本次新签发的**。
            "since_echo": str(since) if since else "",
            "version_match": bool(since) and bool(prev_sv) and str(since) == prev_sv,
            "screen_version_新值": screen_version,
            "since_回显": str(since) if since else "",
            "since_是本进程签发的": bool(since) and str(since) == prev_sv,
            "有基线": bool(prev),
            "稳定": bool(prev and str(prev.get("seen_version") or "") == screen_version),
            "新增": new[:SCREEN_DELTA_LIST_CAP], "新增数": len(new),
            "消失": gone[:SCREEN_DELTA_LIST_CAP], "消失数": len(gone),
            "口径": "基线 = 本进程对该窗口的上一屏快照（%d 秒内）；换进程重新计基线"
                    % int(SCREEN_SNAP_TTL_SEC),
        }
    if since:
        # ★ ④ 顶层也放一份（同一个值，不另算）：agent 不用钻进 delta 才看得到版本应答。
        out["since_echo"] = out["delta"]["since_echo"]
        out["version_match"] = out["delta"]["version_match"]
        out["screen_version_新值"] = screen_version
        out["版本口径"] = ("screen_version 由**服务端签发**，客户端只能把上次见到的值"
                      "**原样回传**到 since（仅用于对照），**永远不能指定**；本次新签发的值见 "
                      "screen_version / screen_version_新值。version_match=false 说明你回传的 "
                      "since 本进程没见过（换过进程或值被改过）—— 那就当没有基线，"
                      "别拿旧 uid 盲扫。")
        st = {"OK": 0, "TEXT_CHANGED": 0, "STALE": 0}
        for o in objects:
            k = str(o.get("uid_state") or "STALE")
            st[k] = st.get(k, 0) + 1
        out["delta"]["uid_state_统计"] = st
        out["delta"]["uid_state_统计_口径"] = "只数本次**返回**的 objects（被 top-N 裁掉的没数进去）"

    # ── 任务声明制：校验段（规则3 首扫必拒 / 规则5 挂起须显式）───────────────
    if watch_err:
        # 兜底：t_screen 本该在进闸门前就拒了；走到这里说明有人绕过它直调 _screen_build。
        out["_watch_拒绝"] = {"why": "ctl_screen " + watch_err,
                              "watch_校验": {"声明条数": 0, "上限": WATCH_TEXT_MAX,
                                          "matched": [], "unmatched": [],
                                          "pending": []}}
    elif watch_decls:
        report = _screen_watch_report(watch_decls, watch_hits, pending_ok=watch_pending)
        out["watch_校验"] = report
        out["watch_口径"] = ("声明命中的节点（objects[].watched=true）**不参与 volatile 豁免**："
                          "text_key 照常实算、参与 screen_version 进位与 delta，变更必报 "
                          "TEXT_CHANGED（objects[].exempted=false 就是豁免被压掉的证据）；"
                          "且**不受视口过滤**（滚出屏幕也照比）、**不受 top 预算**裁剪（声明必进）。"
                          "记账是**全收**的：一个节点命中几条声明就记几条、一条声明命中几个节点"
                          "就报几个（watch_校验.命中节点数）；单条声明命中节点数上限 %d，"
                          "超宽**拒绝整调用**。声明只在**本调用**生效，服务端不留声明状态 "
                          "—— 每次扫描都要带上同一份。" % WATCH_TEXT_WIDTH_MAX)
        # 规则4½（窗E1 · F2）：某条声明命中节点数超宽度上限 ⇒ 拒，点名串与命中数。
        #   ★ 宽度拒绝**不受 watch_pending 影响**：它现在就命中了，不存在「等它出现」。
        # 规则3：首扫校验 —— 有声明一条都没命中 ⇒ **整个调用拒绝**（rc≠0），不许静默吞。
        #   watch_pending=true 时改为挂起（规则5），并逐条标 pending。
        #   ★ 只有 unmatched 才拒：pending 是 watch_pending=true 下**被显式允许**的挂起
        #     （报告里 pending 非空时 unmatched 恒为空 —— 两处都列就是重复报）。
        #   两条拒绝路径都走 _screen_watch_reject（F3：带屏上下文，不回 objects）。
        wide = [(orig, int(watch_hits.get(orig, 0)))
                for orig, _ in watch_decls
                if int(watch_hits.get(orig, 0)) > WATCH_TEXT_WIDTH_MAX]
        if wide:
            out["_watch_拒绝"] = _screen_watch_reject(
                out,
                ("watch_text 里有 %d 条声明命中节点数超过宽度上限 %d（%s）—— "
                 "一条声明吞半屏等于没声明；请把串改具体（加前后文）。"
                 % (len(wide), WATCH_TEXT_WIDTH_MAX,
                    json.dumps(["%s × %d" % (o, c) for o, c in wide],
                               ensure_ascii=False)[:200])),
                report, "width")
        elif report["unmatched"]:
            bad = report["unmatched"]
            out["_watch_拒绝"] = _screen_watch_reject(
                out,
                ("watch_text 里有 %d 条声明在这屏上**一条都没匹配到**（%s）—— 本调用拒绝，"
                 "不静默丢弃。要等它稍后出现，显式传 watch_pending=true 挂起（返回里逐条标 pending）。"
                 % (len(bad), json.dumps(bad, ensure_ascii=False)[:200])),
                report, "unmatched")
    return out


def t_screen(args: dict) -> dict:
    """F4 语义屏（窗B1）。走 A 级只读闸门，与 ctl_tree 同一套。"""
    max_nodes = _screen_max_nodes(args)
    max_depth = int(args.get("max_depth") or uia_tree.DEFAULT_MAX_DEPTH)
    # ★ 规则4（防滥用上限）与形态校验**在进闸门之前**就拒：省一趟真扫描，也不占观测配额。
    _decls, watch_err = _screen_watch_parse(args)
    if watch_err:
        return env(code=gate.CODE_NO_CTRL, warning="ctl_screen " + watch_err,
                   data={"watch_校验": {"声明条数": 0, "上限": WATCH_TEXT_MAX,
                                         "matched": [], "unmatched": [],
                                         "pending": []}},
                   blind_spots=["watch_text 声明没生效 ⇒ 这一屏「要盯的文本」没被兑现；"
                                "别拿这一屏的 objects 当作声明已兑现"])
    r = _uia_gate(args, "ctl_screen", lambda h: _screen_build(h, args),
                  walk_override={"tool": "ctl_screen", "roles": ["all"],
                                 "include_offscreen": True,
                                 "max_nodes": max_nodes, "max_depth": max_depth})
    if r.get("ok") and isinstance(r.get("data"), dict):
        # ★ 照实说：语义屏交出的 objects 是**裁剪+压缩后**的（id 是屏内对象号），
        #   但每个对象**另带 eid**（窗G3）—— 那是旁表钥匙，配 capture_id 能直接点。
        #   所以这里不许沿用「本次未采集节点」那句 —— 那会把「有对象但不可点」
        #   说成「压根没采集」，agent 会由此得出错误的下一步。
        if r["data"].get("capture_id"):
            r["data"]["capture_指纹"] = (
                "语义屏元素指纹（不是图像）；⚠ objects[].id 是**屏内对象号**，不是 eid；"
                "要动作请用 objects[].eid ＋ 本屏 capture_id（可再带 expect_uid 严核身份）")
        if args.get("version") or args.get("screen_version"):
            r["blind_spots"] = list(r.get("blind_spots") or []) + [
                "screen_version 由服务端签发：你传的 version/screen_version 已被忽略；"
                "只能通过 since 回传『你上次见过的』用于对照"]
        # ★ 规则3：首扫校验不过 ⇒ **明确拒绝**（rc≠0 + unmatched 逐条），不许静默返回 0。
        #   这一档只回 watch_校验，不回 objects —— 既然「要盯的文本」有一半不存在，
        #   这一屏的 objects 就不该被当成「声明已兑现」来用。
        res = r["data"].get("结果")
        # ★ 窗F2：未热不是错误，是**证据不足** ⇒ 信封不动（仍 ok=true/code=0），
        #   只在 blind_spots 里点名，并给一条可执行的下一步。
        if isinstance(res, dict) and res.get("screen_ready") is False:
            ev = res.get("ready_证据") or {}
            r["blind_spots"] = list(r.get("blind_spots") or []) + [
                "本屏可能未热（%s）—— **证据不足，不等于「页面上没有它」**："
                "本屏扫到 %s 个节点，重试 %s 次仍未见完整树。Chromium 的无障碍树是懒启用的，"
                "首扫常只拿到外壳。下一步：稍候 1~3 秒再调一次 ctl_screen（别改声明串），"
                "或改用 ctl_shot/ctl_find 交叉确认；**在这一屏上没看到某个东西，"
                "不要当成页面上没有**。"
                % ("；".join(ev.get("判定") or []) or "依据不足",
                   ev.get("节点数"), ev.get("重试"))]
        # ★ 窗G1：网页类窗口但 **CDP 没接上** ⇒ R1 不成立，同样**只进 blind_spots、不动信封**
        #   （和 F2 的「未热≠拒绝」同一条纪律：通道不可用是**证据不足**，不是「没有」）。
        if isinstance(res, dict):
            dom = res.get("dom") or {}
            ch1 = ((res.get("channels") or {}).get("R1_cdp") or {})
            if not dom.get("wired") and _screen_is_web_window(res.get("window") or {}):
                r["blind_spots"] = list(r.get("blind_spots") or []) + [
                    "CDP 通道没接上（R1 不成立）：%s —— **这不等于「页面上没有它」**，"
                    "只是这一屏没有 DOM 这条证据。下一步：ctl_browser action=launch 起一个"
                    "独立实例（或把 cdp_port 指到你已在调试的浏览器），再调一次。"
                    % (str(dom.get("不可用原因") or "端口 %s 上没有 CDP 实例" % ch1.get("端口"))[:160],)]
            # ★ 窗G2：OCR 跑了但没成立（引擎不可用 / 超时 / 框不可用）——
            #   同样是**证据不足**，只进 blind_spots，信封不动。
            oc = res.get("ocr") or {}
            if oc.get("跑了") and not (res.get("channels") or {}).get("R3_ocr", {}).get("hit"):
                r["blind_spots"] = list(r.get("blind_spots") or []) + [
                    "OCR 通道没成立（R3 不成立）：%s —— **这不等于「页面上没有它」**，"
                    "只是这一屏没有像素这条证据（UIA 也可能正瞎着）。下一步：确认窗口已在前台且没最小化，"
                    "或调大 ocr_max_ms（默认 1500ms），或改用 ctl_shot 让模型自己看。"
                    % (str(oc.get("原因") or "没认出来")[:160],)]
        if isinstance(res, dict) and isinstance(res.get("watch_校验"), dict):
            # ★ 与拒绝路径对齐：成功时 watch_校验 也抬到 data 上（拒绝时那里没有「结果」段）。
            #   否则同一个字段两个位置，调用方得记住「成功去哪儿找、失败去哪儿找」。
            r["data"]["watch_校验"] = res["watch_校验"]
            # ★ 窗E1 · F5：挂起项进 blind_spots —— 「以后会出现」不能变成永久静默；
            #   每次放行都点名这几条还没盯上，别让 agent 把 pending 当成已兑现。
            pend = (res["watch_校验"] or {}).get("pending") or []
            if pend:
                r["blind_spots"] = list(r.get("blind_spots") or []) + [
                    "有 %d 条声明这次一条都没命中，被 watch_pending=true 显式挂起：%s —— "
                    "这几条**还没盯上**，别当成已兑现；要么改串，要么继续带同一份声明观察"
                    % (len(pend), json.dumps(pend, ensure_ascii=False)[:160])]
        rej = res.get("_watch_拒绝") if isinstance(res, dict) else None
        if rej:
            # ★ 窗E1 · F3：拒绝信封带**屏上下文**（screen_version/window/对象总数），
            #   blind_spots 明写「校验失败≠屏幕空」—— 声明写错/白屏/错窗/预算没扫到
            #   四类失败现在分得清。**仍不回 objects**：那会被当成「声明已兑现」。
            # ★ 窗E2（2026-10-18 · DSH 实测发现）：`拒因` 以前**只存在于 warning**，
            #   data 里没有 => agent 拿到的 data.拒因 是 None，只能去解析中文。
            #   => 提到 data 顶层，给**机器可读**的短码：
            #     "unmatched"（声明一条都没命中）| "width"（命中节点数超上限）
            #   中文 why 仍留在 warning / blind_spots（人读用），两份并存不冲突。
            data_rej = {"watch_校验": rej.get("watch_校验") or {},
                        "拒因": rej.get("拒因")}
            for k in ("screen_version", "window", "对象总数"):
                if rej.get(k) is not None:
                    data_rej[k] = rej[k]
            bs = [str(rej.get("性质") or "")]
            if rej.get("拒因") == "width":
                bs.append("有声明命中节点数超过宽度上限（见 watch_校验.命中节点数）—— "
                          "把串改具体（加前后文）再试；挂起救不了超宽")
            else:
                bs.append("有一条声明在这屏上一条都没匹配到 ⇒ 你以为在盯它，"
                          "其实这一屏没有它；要么改声明串，要么显式 "
                          "watch_pending=true 挂起")
            return env(code=gate.CODE_NO_CTRL, warning=rej["why"],
                       data=data_rej,
                       blind_spots=[b for b in bs if b])
    return r

def t_not_implemented(name: str):
    def f(args: dict) -> dict:
        return env(code=gate.CODE_NO_CTRL,
                   warning=f"{name} 尚未实现（属于后续 S3/S4 范围）",
                   blind_spots=[f"{name} 当前不可用"])
    return f


# ── preflight 体检（2026-10-18 §㊾ 的 B 项）──────────────────────────────
# 目的：把"动手之后才发现"的坑（没通行证/敏感窗/UIA 读不到/DOM 连不上/帧太旧）
# 全部**前移**成开干前 30 秒就能看到的一页结论。
def t_preflight(args: dict) -> dict:
    hwnd = int(args.get("hwnd") or 0)
    out = {"window": None, "sensitive": None, "channels": {}, "frame": None,
           "auth": {}, "verdict": None, "advice": None}
    # ① 窗口身份
    if hwnd:
        try:
            import ctypes                                      # noqa: PLC0415
            import ctypes.wintypes as wt                       # noqa: PLC0415
            u32 = ctypes.windll.user32
            cls = ctypes.create_unicode_buffer(128)
            u32.GetClassNameW(hwnd, cls, 128)
            ttl = ctypes.create_unicode_buffer(300)
            u32.GetWindowTextW(hwnd, ttl, 300)
            rc = wt.RECT()
            u32.GetWindowRect(hwnd, ctypes.byref(rc))
            pid = wt.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out["window"] = {"hwnd": hwnd, "pid": int(pid.value), "class": cls.value,
                             "title": ttl.value, "visible": bool(u32.IsWindowVisible(hwnd)),
                             "rect": [rc.left, rc.top, rc.right, rc.bottom],
                             "foreground": int(u32.GetForegroundWindow() or 0) == hwnd}
        except Exception as e:
            out["window"] = {"hwnd": hwnd, "error": "%s: %s" % (type(e).__name__, e)}
    # ② 敏感窗
    try:
        d = _deny_sensitive_target(hwnd, "ctl_preflight", "注入") if hwnd else None
        out["sensitive"] = {"denied": bool(d), "why": (d or {}).get("warning") if d else None}
    except Exception as e:
        out["sensitive"] = {"denied": None, "why": "%s: %s" % (type(e).__name__, e)}
    # ③ 通道
    try:
        nodes = 0
        if hwnd:
            _tr = uia_tree.tree(hwnd, max_nodes=400, detail=False)
            _nodes = getattr(_tr, "nodes", None)
            if _nodes is None:
                _nodes = getattr(_tr, "count", None)
            if _nodes is None:
                try:
                    _nodes = len(_tr)
                except Exception:
                    _nodes = 0
            nodes = int(_nodes or 0)
        out["channels"]["uia"] = {"nodes": nodes, "usable": nodes > 0}
    except Exception as e:
        out["channels"]["uia"] = {"nodes": None, "usable": None, "error": str(e)[:80]}
    try:
        import importlib                                       # noqa: PLC0415
        importlib.import_module("agentctl.ocr")
        out["channels"]["ocr"] = {"usable": True}
    except Exception as e:
        out["channels"]["ocr"] = {"usable": False, "error": str(e)[:80]}
    port = args.get("cdp_port") or os.environ.get("AGENTCTL_CDP_PORT")
    if port:
        try:
            import urllib.request                              # noqa: PLC0415
            with urllib.request.urlopen("http://127.0.0.1:%s/json/version" % port, timeout=2) as f:
                info = json.loads(f.read().decode("utf-8", "replace"))
            out["channels"]["cdp"] = {"port": int(port), "usable": True,
                                      "browser": str(info.get("Browser") or "")[:60]}
        except Exception as e:
            out["channels"]["cdp"] = {"port": int(port), "usable": False,
                                      "error": str(e)[:80]}
    # ④ 帧
    _rec = _SCREEN_SEEN.get(hwnd) or {}
    _t0 = float(_rec.get("t") or 0)
    age = int(max(0.0, time.time() - _t0) * 1000) if _t0 else None
    ttl = _frame_ttl_ms(args)
    out["frame"] = {"screen_version": _rec.get("seen_version") or None,
                    "age_ms": age, "ttl_ms": ttl,
                    "fresh": (age is not None and (ttl <= 0 or age <= ttl))}
    # ⑤ 通行证 / 租约 / 急停
    try:
        st = t_status({"actor": args.get("actor") or "preflight"})
        d = st.get("data") or {}
        d = d.get("结果") if isinstance(d.get("结果"), dict) else d
        out["auth"] = {"pass": (d.get("pass") or {}), "lease": (d.get("lease") or {}),
                       "brake": (d.get("brake") or {})}
    except Exception as e:
        out["auth"] = {"error": "%s: %s" % (type(e).__name__, e)}
    # ⑥ 结论
    if not hwnd:
        out["verdict"], out["advice"] = "no_window", "先 ctl_windows 拿到 hwnd"
    elif not (out["window"] or {}).get("visible"):
        out["verdict"], out["advice"] = "no_window", "窗口不可见：先把它显示出来再动手"
    elif (out.get("sensitive") or {}).get("denied"):
        out["verdict"], out["advice"] = "blocked_sensitive", "敏感窗：注入永久拒绝，只读也受限"
    elif not out["frame"]["fresh"]:
        out["verdict"], out["advice"] = "stale_frame", "先 ctl_screen 拿新帧（frame_id 会过期）"
    elif not (out["channels"].get("uia") or {}).get("usable") and not (
            out["channels"].get("cdp") or {}).get("usable"):
        out["verdict"], out["advice"] = "blind", "UIA 读不到且没有 CDP：只能像素+OCR，风险高"
    else:
        out["verdict"], out["advice"] = "ready", "可以开工"
    return env(ok=True, code=0, data={"结果": out}, warning=None)


def _t_quiz(a):
    """超星/学习通 客观题通道（**只答不提交**）。

    ★ 惰性导入：quiz 依赖 core（要复用它的 _cdp_eval/_cdp_pages）,
      core 顶层 import quiz 会成环 ⇒ 这里在调用时才 import。
    ★ 契约是唯一真源：登记在 contract/tools.json ⇒ CLI 与 MCP 两侧自动可见。
    """
    from agentctl import quiz                                      # noqa: PLC0415
    return quiz.t_quiz(a)


TOOLS = {
    "ctl_status": t_status,
    "ctl_preflight": t_preflight,
    "ctl_lease": t_lease,
    "ctl_brake": t_brake,
    "ctl_timeline": t_timeline,
    "ctl_wait": t_wait,
    "ctl_verify": t_verify,
    "ctl_witness": t_witness,
    "ctl_windows": t_windows,
    "ctl_shot": t_shot,
    "ctl_tree": t_tree,
    "ctl_screen": t_screen,
    "ctl_find": t_find,
    "ctl_replay": t_replay,
    "ctl_authorize": t_authorize,
    "ctl_record": t_record,
    "ctl_click": t_click,
    "ctl_type": t_type,
    "ctl_fill": t_fill,
    "ctl_scroll": t_scroll,
    "ctl_keys": t_keys,
    "ctl_focus": t_focus,
    "ctl_browser": t_browser,
    "ctl_quiz": _t_quiz,
    "ctl_task": lambda a: tasks_dispatch.handle(a, store=store, env=env,
                                                 gate=gate, timeline_dir=TIMELINE_DIR),
}


# ★ W5（2026-10-20）：**动作类**工具 —— 它们的事件要带窗口身份。
#   只读感知类（ctl_shot/ctl_tree/ctl_find/…）的事件不带：那些不是桌面动作，
#   而且一次调用可能涉及多个窗口，硬塞一个标题反而是假事实。
_ACTION_TOOLS = frozenset(("ctl_click", "ctl_type", "ctl_fill", "ctl_keys",
                           "ctl_scroll", "ctl_focus"))


def _ev(kind: str, *, actor="", tool="", payload=None, ok=True, code=0):
    try:
        return store().append(kind, actor=actor, tool=tool, payload=payload or {},
                              ok=ok, code=code)["seq"]
    except Exception:
        return None


def dispatch(tool: str, args: dict, *, actor: str = "", human: bool = False) -> dict:
    """**唯一的入口。** 三种传输都调它。"""
    t0 = time.perf_counter()
    args = args or {}
    # ★ D1 修复（2026-10-19，Codex 实跑发微信时踩出）
    #   症状：MCP 传输（agentctl.mcp）下**所有写动作**一律 code=10，
    #         报「本次调用**没带 actor**，请加 --actor」—— 而包装器
    #         .codex\mcp\run_agentctl.cmd 里明写 `set "AGENTCTL_ACTOR=codex"`。
    #   真因：actor 由 mcp.py 从**环境变量**取出来交给 dispatch 的**关键字参数**，
    #         但 dispatch 只把 args 往下传（TOOLS[tool](args)）；
    #         闸门读的是 args.get("actor")（见 _act_precheck），
    #         ⇒ **actor 在 dispatch 里被算出来然后丢掉**，写动作永远拿不到身份。
    #         CLI 传输不受影响，因为 act.py 走 --actor 会把 actor 放进 args。
    #   修法：在唯一出口把 actor **补进 args**。不改任何判定语义，只补一条本该有的传递。
    #   ⚠ 只在 args 里没有 actor 时才补 ⇒ 调用方显式传的一直优先，
    #     不会出现「环境变量覆盖显式指定」这种反直觉行为。
    if actor and not args.get("actor"):
        args = dict(args, actor=actor)
    if tool not in TOOLS:
        r = env(code=gate.CODE_NO_CTRL, warning=f"未知工具：{tool}")
    else:
        # ★ P0-2：标上「这次是人发起的」（解除急停只走人路）。
        #   用 try/finally 复原：HTTP 传输是多线程的，不复原会串到别的请求上。
        _prev_human = getattr(_call_ctx, "human", False)
        _call_ctx.human = bool(human) or _prev_human
        try:
            r = TOOLS[tool](args)
        except gate.GateDenied as e:
            r = env(code=e.code, warning=e.why)
        except Exception as e:
            r = env(code=gate.CODE_INTERNAL, warning=f"内部错误 {type(e).__name__}: {e}")
        finally:
            _call_ctx.human = _prev_human

    # ── ★ P2 动作面异常刹车（2026-10-16）─────────────────────────────
    #   放在**这里**（每种传输都走的唯一出口）而不是 t_click/t_type 各自一份：
    #   三条判据盯的是「这个 actor 在干什么」，而 actor / 成功失败只有出口这一处拿得准；
    #   在 t_click 里单独埋 ⇒ ctl_type 的连打就永远看不见。
    #   ⚠ 它**只观察、不拦阻**：判定与拉闸都在 action_guard 里，
    #     本次动作该成还成（一级命中本来就只是提示），真要停也是闸门那边拦后续动作。
    #   ⚠ 判据自己崩了不许把动作带崩 —— 但**不许静默**：把错挂在返回值上，
    #     否则就是「安全机制静默失效」，比没有安全机制更坏（项目老教训）。
    # ★ P0-1（2026-10-21）：把这次失败**贴上性质标签**再交给刹车判据。
    #   以前只传 bool ok ⇒ 「环境不满足（被挡/没证/缺凭据）」与
    #   「动作发出去了但没生效」在 fail_streak 里是同一个桶，
    #   于是环境性失败会把「agent 在乱点」这个信号灌满（实测：8 次 → 急停 → 租约被吊销）。
    #   现在 rejected_env 不再计入 fail_streak（只留痕），no_effect / action_failed 照旧计。
    _fc = failure_class(r)
    if _fc:
        r["failure_class"] = _fc
    try:
        _anom = action_guard.guard().observe(
            actor=actor, tool=tool, args=args, ok=bool(r.get("ok")),
            failure_class=_fc)
        if _anom:
            r["action_anomaly"] = _anom
    except Exception as _e:
        r["action_guard_error"] = "%s: %s" % (type(_e).__name__, _e)

    r["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)

    # ★★ 2026-10-20：动作做完了 ⇒ 请自己的面板弹回来。
    #   放在 dispatch 出口（三态传输唯一出口）而不是 t_click/t_keys 各写一遍：
    #   漏一处就永远收着面板（用户看不到挂件），统一在这儿收口最稳。
    #   只读调用走到这儿 is_yielded() 为 False ⇒ 什么都不做。
    try:
        from agentctl import panel_yield                # noqa: PLC0415
        if panel_yield.is_yielded():
            panel_yield.request_back()
    except Exception:
        pass
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
        _evp = {k: v for k, v in args.items() if k != "token"}
        # ★ W5（2026-10-20）：动作类事件**补窗口身份**（标题/进程名/窗口类名）。
        #   改前事件流**只落 hwnd** ⇒ 事后离线无法把一次动作归因到具体应用。
        #   台账导出实测：1124 次写面动作里 634 次（56.4%）归因不到应用，
        #   根因就是这里缺字段（不是解析漏读）。
        #   ★ 身份必须在**动作发生的当下**取：事后靠 hwnd 反查不行 ——
        #     hwnd 会被 Windows 复用，标题也会变，事后拿到的是别的窗口。
        #   ★ 取不到（窗口已关/无权访问）就**不写这个键**，不留空壳假装有。
        # ★ W5-B（2026-10-20）：窗口身份**不只给动作事件**——
        #   感知类事件（截图/OCR/读树）也带 hwnd，一并补上身份，
        #   离线才能把「看到了什么」和「哪个应用的窗口」挂上钩。
        #   ⚠ 身份字段与取值口径**与动作事件完全一致**（同一段代码、同一套键）。
        _h = args.get("hwnd")
        try:
            _h = int(_h) if _h else 0
        except Exception:
            _h = 0
        if _h > 0:
            _wi = _win_info_for(_h)
            _t = _wi.get("title") or ""
            _evp["win"] = {
                "hwnd": _h,
                "exe": _wi.get("process") or "",
                "cls": _wi.get("class") or "",
                # ★ 标题**只存摘要**（sha256 前 16 位）：标题里常有用户自己的文字
                #   （文档名、会话标题），明文落盘不该。归因用 exe/cls，够用。
                #   ★ 与台账 schema.md 口径一致（老栈 act.log 的标题也只落摘要）。
                "title_hash": (hashlib.sha256(_t.encode("utf-8")).hexdigest()[:16]
                               if _t else ""),
                "pid": _wi.get("pid") or 0}
        # ★ W5-B：**统一补写判词**——判词从此进事件流（台账的真相源之一）。
        #   改前判词只回给调用方（返回值），**不落盘** ⇒ 事后无人能查
        #   「当时判成了什么」；台账里的新栈动作只能拿返回码反推，
        #   于是和「动作发出但画面没变」混成 unknown。
        #   ⛔ 只加键：事件原有的 args / ok / code 一个字没改。
        _cv = r.get("verdict_canonical")
        # ★ W5-B：动作类事件**一律**要有一份判词 —— 连没走到求差那一步的
        #   （闸门拒了、没通行证、目标不对）也得有，才叫「统一补写」。
        #   口径与台账 CODE_TABLE 一致：拒=denied、没变=no_effect、判不出=unknown。
        if (not isinstance(_cv, dict) or not _cv) and tool in _ACTION_TOOLS:
            _rc = int(r.get("code") or 0)
            _cv = _canon_verdict(
                {0: "met", 8: "no_effect", 5: "unknown"}.get(
                    _rc, "denied" if _rc in (3, 4, 7, 9, 10, 11, 12, 13, 15)
                    else "unknown"),
                "gate_code", "回执返回码 %d" % _rc)
            r["verdict_canonical"] = _cv
        if isinstance(_cv, dict) and _cv:
            _evp["verdict_canonical"] = _cv
        _ex = r.get("evidence")
        if isinstance(_ex, dict) and _ex:
            _evp["evidence"] = {k: _ex.get(k) for k in
                                 ("changed", "bbox", "area_ratio", "verdict",
                                  "verdict_canonical", "before_png", "after_png",
                                  "crop_png", "dir")}
        ev = _ev(tool.replace("ctl_", ""), actor=actor, tool=tool,
                 payload=_evp,
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
