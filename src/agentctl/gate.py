# -*- coding: utf-8 -*-
r"""gate.py —— AgentCtl 的安全内核：闸门（原子）＋ 急停 ＋ 单写者租约 ＋ 观测-动作绑定

设计依据：详细设计 §7.1 / §7.2 / §15.3
铁律 11（不可协商）：每次控制前用户弹窗授权；急停双通道；档位 A→B→C→D 只升不跳。

★ 三条设计取舍，先讲清楚：
 1) **通行证仍是 `_auth\auth_pass.json`（唯一真相源）** —— 老脚本 `gate.py`、授权弹窗 `auth_popup.py`
    一行都不用改。我们**只加原子性**，不换存储。换存储会让"用户授权"这条链断掉，得不偿失。
 2) **原子性靠文件锁**（`msvcrt.locking`）包住"读→校验→计数→写回"整段。
    原来的 `gate.check()` 与 `require()` 是**两步**，两个进程并发会同时通过 → **超发**。
 3) **范围口径在 API 层统一成 A/B/C/D**，内部翻译成闸门用的完整串（"A级 只读感知"…）。
    这条是接入文档自足性测试逼出来的（原契约里 authorize 用完整串、lease 用短代号，两套词汇表）。

用法：
    import gate
    gate.require("B", "点击保存")            # 不过就抛 GateDenied
    lic = gate.acquire_lease("DSH", ["B"], 600)
    gate.release_lease(lic)
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import secrets
import sys
import time

IS_WIN = os.name == "nt"
if IS_WIN:
    import msvcrt

# ── 输出被重定向时 Windows 用 GBK → 打印 emoji 会抛 UnicodeEncodeError 打死脚本 ──
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── legacy_tools 定位（分层查找，**没有本机绝对路径兜底**）─────────
# 2026-10-04 DSH 审查交付包时否掉了我原来的「三段回退」方案，原话：
#   「留一段本机绝对路径当兜底，等于这次交付**仍然带着本机路径**，
#     只是被藏到了最后——A 项要消灭的就是它；而且它是**静默**兜底：
#     别台机器上指向不存在的目录，**又变成『看起来有默认值，其实是坏的』**。」
# 照它说的改：逐层找，**找不到就是 None**（不假装有默认值），
# 谁真的要用老脚本谁调 require_home() 拿明确报错。
#
# ★ 为什么不在模块顶层直接 raise（我第一版就是这么写的，当场自己否掉了）★
#   顶层 raise ⇒ 任何 import gate 的模块**全部**起不来，包括只读面板 ——
#   面板只显示急停状态、根本不碰老脚本，却因为「老脚本不在」而开不了窗。
#   **那才是把 A 项修出新事故。**
#   所以：定位失败 = 降级（HOME=None），真要用 = require_home() 抛明确异常。
#
# 查找顺序（先命中先用）：
#   1) 环境变量 AGENTCTL_LEGACY_HOME —— 测试 / 多实例
#   2) 冻结态 _MEIPASS\legacy_tools（打包时把白名单 legacy 放这儿）
#   3) 源码态：从本文件所在目录向上最多 3 级，找 legacy_tools\
#   4) 本机开发指针 build\local_legacy_home.txt（一行目录路径）
#      —— 用**指针文件**而不是硬编码：硬编码就是 DSH 说的「把本机路径藏到最后」；
#         指针文件至少是显式的、可 grep 到的，而且**打包带不进去**
#         （只有源码态读得到它，冻结态 _MEIPASS 里没有 build\ 这一层）。
_LEGACY_MARKER = "auth_popup.py"   # legacy 工具集的标志文件（老脚本里唯一管弹窗的）
_LEGACY_PROBE = 3                   # 源码态向上找几级：src/agentctl → src → 项目根

class LegacyToolsMissing(RuntimeError):
    """老脚本目录不在预期位置。**只有真要调老脚本时才抛**（见上面那段）。"""

def _missing_hint() -> str:
    return ("找不到 legacy 脚本目录（找过：环境变量 AGENTCTL_LEGACY_HOME、"
            "包内 legacy_tools\\、源码树向上 %d 级、build\\local_legacy_home.txt）。"
            "解决办法二选一：① 设 AGENTCTL_LEGACY_HOME 指向老脚本目录；"
            "② 把老脚本目录放成任一层级的 legacy_tools\\（里面要有 %s）。"
            % (_LEGACY_PROBE, _LEGACY_MARKER))

def _project_roots() -> list[str]:
    here = os.path.dirname(os.path.abspath(__file__))     # .../src/agentctl
    roots, cur = [], here
    for _ in range(_LEGACY_PROBE + 1):
        roots.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return roots

def _resolve_home() -> str | None:
    env = os.environ.get("AGENTCTL_LEGACY_HOME")
    if env:
        return env
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = os.path.join(meipass, "legacy_tools")
        if os.path.isdir(cand):
            return cand
    for root in _project_roots():
        cand = os.path.join(root, "legacy_tools")
        if os.path.isdir(cand):
            return cand
    for root in _project_roots():
        ptr = os.path.join(root, "build", "local_legacy_home.txt")
        if not os.path.isfile(ptr):
            continue
        try:
            with open(ptr, encoding="utf-8-sig") as f:
                val = f.read().strip()
        except Exception:
            val = ""
        if val and os.path.isdir(val):
            return val
    # ★ 不返回本机绝对路径 ★ 找不到就是找不到
    return None

HOME = _resolve_home()

def require_home() -> str:
    """要调老脚本时用这个：拿不到就**明确报错**，不静默失败、不猜路径。"""
    if not HOME:
        raise LegacyToolsMissing(_missing_hint())
    return HOME

AUTHDIR = os.path.join(HOME, "_auth") if HOME else None
PASSFILE = os.path.join(AUTHDIR, "auth_pass.json") if AUTHDIR else None
LOCKFILE = os.path.join(AUTHDIR, "gate.lock") if AUTHDIR else None
LEASE_DB = os.path.join(AUTHDIR, "lease.json") if AUTHDIR else None
CAPTURE_DB = os.path.join(AUTHDIR, "capture.json") if AUTHDIR else None
LOG = os.path.join(AUTHDIR, "gate_agentctl.log") if AUTHDIR else None

# ── 急停状态：文件挪出桌面 ──────────────────────────────────
# 为什么挪：用户 2026-10-04「桌面那些急停文件就不用了」。
# 之前写死在 桌面\_急停.flag 有两个问题：
#   ① **换机器就废**（DSH 审查交付包时点的 A 项缺陷，6 处硬编码之一）；
#   ② 桌面上摆个 flag 让人以为要去管它，实际用户只用面板按钮和热键。
# 现在放 %LOCALAPPDATA%\AgentCtl\：桌面干净了，跨机器也跟得走。
#
# ⚠ 文件本身**必须保留**，不是"多余的可以删"：
#   急停状态要能持久化——面板会关、服务会停，
#   状态只存在内存里的话，一关窗口「已停」就凭空消失了，
#   那比放在桌面危险得多。文件是**内部状态载体**，不是要用户操心的东西。
#   用户能碰到的是两个：面板的「⏹ 终止 / ▶ 解除急停」按钮，和全局热键
#   Ctrl+Alt+Home（见 brake_daemon）。
#
# 环境变量仍然优先 —— 做实验时要能把它指到临时文件，
# 不能为了跑测试去拉用户的真急停（gate.py 的老规矩，保留）。
APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl")
BRAKE_FLAG = os.environ.get(
    "AGENTCTL_BRAKE_FLAG", os.path.join(APP_DIR, "_急停.flag"))

# ── 范围口径：短代号 ↔ 闸门完整串（§1.4）──
SCOPES = {
    "A": "A级 只读感知",
    "B": "B级 点击滚动",
    "C": "C级 打字快捷键",
    "D": "D级 拖拽",
}
FULL_TO_SHORT = {v: k for k, v in SCOPES.items()}
PERCEIVE = ("A", "B", "C", "D")          # 感知是所有级别的底座
ACT_LIMIT = {"A": 200, "B": 20, "C": 20, "D": 10}

# 颜色短代号常量。2026-10-07 补）
# 之前没有，所以 `gate.A` 会抛 AttributeError（已有 gate.B 同类坑的前案，那次是代码崩了被 dispatch 包成「内部错误」，
#   表面看起来像「闸门拒绝」，实际是崩了）。
# 本次一并补全，并把两个已知使用点换成常量。
A, B, C, D = "A", "B", "C", "D"

# 错误码（与契约 tools.json 对齐）
CODE_OK, CODE_DENY_WHITELIST, CODE_NO_CTRL, CODE_UNSTABLE = 0, 3, 4, 5
CODE_NO_CHANGE, CODE_UNAUTH, CODE_LEASE_HELD, CODE_BRAKE, CODE_STALE_CAPTURE = 8, 9, 10, 11, 12

def short(scope: str) -> str:
    """把 'B' / 'B级 点击滚动' 两种写法都归一成 'B'。"""
    s = (scope or "").strip()
    if s in SCOPES:
        return s
    if s in FULL_TO_SHORT:
        return FULL_TO_SHORT[s]
    # 容错：'B级 点击滚动' 去掉空格 / 'B级' 开头
    for k, v in SCOPES.items():
        if s.replace(" ", "") in (v.replace(" ", ""), v.split(" ")[0]):
            return k
    return s

def parse_scopes(text: str) -> list[str]:
    """把用户**自由输入**的范围解析成标准列表。

    ★ 为什么要有这个（2026-10-06 用户提问）★
    用户问「授权时直接输入 ABCD 行不行」。实测当时的行为：
        'A' / 'B' / 'B级'      → 可以
        'ABCD' / 'AB'          → **不行**，short() 原样返回不认识的东西
        'A,B,C'（逗号）        → 可以（老脚本按逗号切，每段再走 short）
    所以「连写 = 全都要」这个直觉当时是**不被支持**的 ——
    那是缺陷，不是设计。现在补上。

    支持的写法（大小写均可）：
        A                    → [A]
        AB / ab / ABCD       → [A, B, C, D]   连写＝全都要
        A,B,C                → [A, B, C]
        A B C                → [A, B, C]      空格也算分隔
        B级 点击滚动           → [B]
        B,A级,C              → [B, A, C]      混合写法

    ★ 为什么用连写要展开，而不是当成一个词 ★
    范围是**分级累加**的（只读 → 点击 → 打字 → 拖拽），
    用户写 ABCD 时想的是「四档都要」。当成一个词会得到一个
    永远匹配不上的非法范围，而**非法范围不会报错，只会一直拒绝**——
    那种失败最难查。（闸门铁律 1：不猜不编，解析不了要明确报错。）

    解析不出来就抛 ValueError，由调用方弹窗报错，**绝不静默放行**。
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("没输入范围")
    # ★ 先试整段 ★（2026-10-06 实测抓到的 bug）
    #   SCOPES 的完整串是「B级 点击滚动」——**中间带空格**。
    #   如果先按空格切，它会被切成 'B级' + '点击滚动'，后者不认识 → 误拒。
    #   所以顺序必须是：整段能归一就直接收，否则才去切。
    whole = short(raw)
    if whole in SCOPES:
        return [whole]
    # 先按逗号 / 空白切，再逐段归一
    chunks = [c for c in raw.replace("，", ",").replace("、", ",").split()
              if c]
    if len(chunks) == 1 and "," in chunks[0]:
        chunks = [c for c in chunks[0].split(",") if c]
    out: list[str] = []
    bad: list[str] = []
    for c in chunks:
        one = short(c)
        if one in SCOPES:
            if one not in out:
                out.append(one)
            continue
        # 连写字母：ABCD / abcd / AB。**只认单个合法字母**，长度超 4 直接拒绝
        letters = one.replace(" ", "")
        if letters and len(letters) <= len(SCOPES) and all(
                ch.upper() in SCOPES for ch in letters):
            for ch in letters:
                u = ch.upper()
                if u not in out:
                    out.append(u)
            continue
        bad.append(c)
    if bad:
        raise ValueError(
            "看不懂的范围：%s。写法：单字母 A / 连写 ABCD / 逗号 A,B,C / "
            "或照抄「B级 点击滚动」。合法档位：%s"
            % ("、".join(bad), "、".join(SCOPES.values())))
    if not out:
        raise ValueError("没解析出任何档位")
    return out

class GateDenied(Exception):
    def __init__(self, why: str, code: int = CODE_UNAUTH):
        super().__init__(why)
        self.why = why
        self.code = code

def log(msg: str):
    if not LOG:
        # 没定位到闸门状态目录时**不能静默**：写 stderr，排障的人得看得见
        try:
            sys.stderr.write("[gate] %s\n" % msg)
        except Exception:
            pass
        return
    try:
        os.makedirs(AUTHDIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass

# ────────────────────── 原子文件锁 ──────────────────────
@contextlib.contextmanager
def _exclusive(path: str | None = None, timeout: float = 10.0):
    r"""跨进程互斥。Windows 用 msvcrt.locking；其它平台退化为 O_EXCL 自旋。

    ⚠️ 为什么必须加锁：原来的 gate.check() 与 require() 是分离的两步，
       两个进程会**同时**读到 acts=19（limit=20）并双双通过 → 超发一次。
       实测（tests\s3_accept_gate.py）会用 40 个进程去打同一张 10 次的证来验证。
    """
    path = path or LOCKFILE
    if not path:
        raise LegacyToolsMissing(_missing_hint() + "（闸门状态无处安放，本次操作拒绝执行）")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    t0 = time.time()
    got = False
    try:
        while True:
            try:
                if IS_WIN:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if time.time() - t0 > timeout:
                    raise GateDenied(f"闸门锁等待超时（{timeout}s）——有别的进程卡住了", CODE_UNSTABLE)
                time.sleep(0.005)
        yield
    finally:
        try:
            if got:
                os.lseek(fd, 0, 0)
                if IS_WIN:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)

def _load(path, default):
    if not path:
        raise LegacyToolsMissing(_missing_hint() + "（读不到闸门状态，已按拒绝处理）")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save(path, obj):
    if not path:
        raise LegacyToolsMissing(_missing_hint() + "（闸门状态无处可写，已按拒绝处理）")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

# ────────────────────── 急停 ──────────────────────
def brake_engaged() -> bool:
    return os.path.exists(BRAKE_FLAG)

def set_brake(why: str = "未注明") -> tuple[bool, str]:
    """拉下急停。**存在即停**，并且必须留痕（谁拉的、什么时候、为什么）。

    写文件用「先写 .tmp 再 os.replace」：并发的 gate 检查可能正好在读，
    读到半截内容会当成已停——宁可多一次原子替换，也不要这种假阳性。

    ★ `.tmp` 带 pid + 有界重试（2026-10-05，WorkBuddy 004 的 W5）★
    起因是它实测的三档并发数据（4 进程 × 200 轮 = 800 次 os.replace，全在临时目录）：

        固定 tmp 名        失败 780/800  = 97.50%
        tmp 加 pid         失败   9/800  =  1.12%
        pid + 有界重试     失败   0/800  =  0.00%

    两点要讲清楚：
      ① 加 pid 把冲突从「tmp 文件互抢」挪到了「os.replace 目标文件」，
         **残留 1.12% 就是挪过去的，不是消除干净的** ——
         所以「加 pid 就够了」是错的，必须配重试。
      ② 重试是**有界**的：最多 5 次、递增 sleep。
         急停写不进去时必须**明确失败**，不能死循环把问题掩盖掉 ——
         「拉闸失败」比「拉闸慢」危险得多，前者没人知道，后者只是卡。
    """
    try:
        os.makedirs(os.path.dirname(BRAKE_FLAG), exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        body = ("急停已生效\n"
                "时间：%s\n"
                "来源：%s\n"
                "说明：本文件存在期间，一切自动控制必须停止。\n"
                "解除：面板「▶ 解除急停」按钮，或命令行 "
                "AgentCtl.exe brake release。\n"
                "（本文件是内部状态载体，不需要手工创建或删除。）\n"
                % (stamp, why))
        # pid 进 tmp 名：并发进程不再抢同一个中间文件
        tmp = "%s.tmp.%d" % (BRAKE_FLAG, os.getpid())
        last = None
        for attempt in range(5):
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(body)
                os.replace(tmp, BRAKE_FLAG)
                last = None
                break
            except OSError as e:          # 多为 WinError 5/32：文件被另一个进程占着
                last = e
                try:
                    os.remove(tmp)        # 半截文件必须清掉，否则下次又是它
                except OSError:
                    pass
                time.sleep(0.02 * (attempt + 1))   # 递增退避，最长 5×0.1s
        if last is not None:
            return False, "拉急停失败（重试 5 次仍不成功）：%s" % last
        return True, "已拉下急停（%s）" % BRAKE_FLAG
    except Exception as e:
        return False, "拉急停失败：%s" % e

def clear_brake() -> tuple[bool, str]:
    """解除急停。

    ⚠ **这里刻意不做二次确认**——确认在调用方（面板按钮）做，
    而且确认框本身是给用户看的，函数层再挡一层会变成"要点两次"。
    """
    try:
        if not os.path.exists(BRAKE_FLAG):
            return True, "急停本来就未拉下"
        os.remove(BRAKE_FLAG)
        return True, "已解除急停"
    except Exception as e:
        return False, "解除失败：%s" % e

# ────────────────────── 闸门 ──────────────────────
def _check_locked(p: dict, sc: str, *, skip_count: bool = False) -> tuple[bool, str, int]:
    """**必须在持有锁的情况下调用。** 返回 (放行?, 原因, 错误码)。"""

    # skip_count=True = read-only perception (A level, zero side effect); never counted.

    skip_count = skip_count or sc == A
    # ★ skip_count：不计数**也不受次数上限卡**。
    #   只跳加加不够 —— 一旦写动作把额度用完，
    #   感知会被「动作次数已用尽」误杀。两个都必须跳过。
    if brake_engaged():
        return False, f"刹车已拉下（急停文件存在：{BRAKE_FLAG}）—— 一切自动控制停止", CODE_BRAKE
    if not p:
        return False, "没有通行证（需先由用户在弹窗里授权）", CODE_UNAUTH
    if p.get("consumed"):
        return False, "通行证已被使用（一次性，重新授权）", CODE_UNAUTH
    if time.time() > p.get("expires_at", 0):
        return False, f"通行证已过期（{p.get('expires')}）", CODE_UNAUTH
    allowed = [short(x) for x in (p.get("scopes") or ([p["scope"]] if p.get("scope") else []))]
    if sc not in allowed:
        return False, f"范围不符：本次持 {'、'.join(allowed)}，本动作要「{sc}」", CODE_UNAUTH
    if skip_count:
        # 只读感知：A 级零副作用。铁律 11⑥：「20 次门槛只卡有副作用的层级」。
        return True, f"范围「{SCOPES.get(sc, sc)}」有效至 {p.get('expires')}（只读感知，不计数）", CODE_OK
    n = int(p.get("acts", 0))
    limit = int(p.get("acts_limit") or ACT_LIMIT.get(sc, 10))
    if n >= limit:
        return False, f"本张通行证动作次数已用尽（{n}/{limit}）", CODE_UNAUTH
    return True, f"范围「{SCOPES.get(sc, sc)}」有效至 {p.get('expires')}，已用 {n}/{limit} 次", CODE_OK

def check(scope: str) -> tuple[bool, str, int]:
    """只读检查（不计数）。要动手请用 require()。"""
    sc = short(scope)
    with _exclusive():
        p = _load(PASSFILE, {})
        return _check_locked(p, sc)

def require(scope: str, note: str = "", count: bool = True) -> str:
    """**原子的**过闸：校验 + 计数在同一个锁里完成。不过就抛 GateDenied。"""
    sc = short(scope)
    with _exclusive():
        p = _load(PASSFILE, {})
        ok, why, code = _check_locked(p, sc, skip_count=not count)
        if not ok:
            log(f"⛔ 拒绝（{note or sc}）：{why}")
            raise GateDenied(why, code)
        if count and sc != A:
            p["acts"] = int(p.get("acts", 0)) + 1
            p["last_act"] = f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S} {note or sc}"
            _save(PASSFILE, p)
        log(f"✅ 放行（{note or sc}）：{why}")
        return why

def require_any(scopes, note: str = "", count: bool = True) -> str:
    """任一范围有效即可（感知这类所有级别共用的动作）。整段在同一把锁里。"""
    shortlist = [short(s) for s in scopes]
    with _exclusive():
        p = _load(PASSFILE, {})
        reasons = []
        for sc in shortlist:
            ok, why, code = _check_locked(p, sc, skip_count=not count)
            if ok:
                if count and sc != A:
                    p["acts"] = int(p.get("acts", 0)) + 1
                    p["last_act"] = f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S} {note or sc}"
                    _save(PASSFILE, p)
                log(f"✅ 放行（{note or sc}）：{why}")
                return why
            reasons.append(f"{sc} → {why}")
        log(f"⛔ 拒绝（{note or '任一范围'}）：" + "；".join(reasons))
        raise GateDenied("；".join(reasons), CODE_UNAUTH)

def end_session(why: str = "主动结束") -> tuple[bool, str]:
    with _exclusive():
        p = _load(PASSFILE, {})
        if not p or p.get("consumed"):
            return False, "没有可用会话"
        p["consumed"] = True
        p["consumed_at"] = f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S}"
        p["ended_why"] = why
        _save(PASSFILE, p)
        log(f"🔚 会话结束（{why}）")
        return True, f"会话结束：范围「{p.get('scope')}」，动作 {p.get('acts', 0)} 次"

# ────────────────────── 单写者租约 ──────────────────────
# 单 agent 场景下租约不用于"仲裁多方"，而是**防止同一个 agent 自己的并发调用/残留进程互相踩**
def acquire_lease(actor: str, scopes, ttl_sec: int = 600) -> dict:
    ttl = max(1, min(int(ttl_sec), 600))
    now = time.time()
    with _exclusive():
        L = _load(LEASE_DB, {})
        holder = L.get("holder")
        if holder and L.get("expires_at", 0) > now and holder != actor:
            raise GateDenied(
                f"租约被 {holder} 持有，剩余 {int(L['expires_at']-now)} 秒", CODE_LEASE_HELD)
        L = {"lease_id": secrets.token_hex(8), "holder": actor,
             "scopes": sorted({short(s) for s in scopes}),
             "acquired": f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S}",
             "expires_at": now + ttl}
        _save(LEASE_DB, L)
        log(f"🔑 租约签发：{actor} 范围 {'、'.join(L['scopes'])} 有效 {ttl}s holder={holder or '(空)'}→{actor}")
        return L

def renew_lease(lease_id: str, ttl_sec: int = 600) -> dict:
    with _exclusive():
        L = _load(LEASE_DB, {})
        if not L or L.get("lease_id") != lease_id:
            raise GateDenied("租约不存在或已失效", CODE_LEASE_HELD)
        L["expires_at"] = time.time() + max(1, min(int(ttl_sec), 600))
        _save(LEASE_DB, L)
        return L

def release_lease(lease_id: str = "") -> bool:
    with _exclusive():
        L = _load(LEASE_DB, {})
        if not L:
            return False
        if lease_id and L.get("lease_id") != lease_id:
            raise GateDenied("租约 id 不符，拒绝释放", CODE_LEASE_HELD)
        L["released"] = f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S}"
        L["expires_at"] = 0
        _save(LEASE_DB, L)
        log(f"🔓 租约释放：{L.get('holder')}")
        return True

def lease_status() -> dict:
    L = _load(LEASE_DB, {}) if LEASE_DB else {}
    if not L:
        return {"holder": None}
    remain = int(max(0, L.get("expires_at", 0) - time.time()))
    L2 = dict(L)
    L2["remaining_sec"] = remain
    L2["active"] = remain > 0
    return L2

def require_lease(actor: str) -> dict:
    """写动作前校验：必须有一个**未过期且持有者是自己**的租约。"""
    L = lease_status()
    if not L.get("active"):
        raise GateDenied("没有有效租约（写动作必须先 ctl_lease acquire）", CODE_LEASE_HELD)
    if L.get("holder") != actor:
        raise GateDenied(f"租约被 {L.get('holder')} 持有（剩余 {L.get('remaining_sec')}s）", CODE_LEASE_HELD)
    return L

# ────────────────────── 观测-动作绑定（capture_id）──────────────────────
# 治本机头号事故："拿旧截图去点新画面"（布局漂移 60px / 肉眼估坐标偏 100~170px / DPI 两套坐标系）
CAPTURE_TTL = 15.0

def issue_capture(hwnd: int, rect, image_sha256: str, foreground: bool,
                  ttl: float = CAPTURE_TTL) -> str:
    tok = secrets.token_hex(12)
    with _exclusive():
        C = _load(CAPTURE_DB, {})
        now = time.time()
        C = {k: v for k, v in C.items() if v.get("expires_at", 0) > now}   # 顺手清过期的
        C[tok] = {"hwnd": int(hwnd), "rect": list(rect), "image_sha256": image_sha256,
                  "foreground": bool(foreground), "issued_at": now,
                  "expires_at": now + float(ttl)}
        _save(CAPTURE_DB, C)
    return tok

def check_capture(tok: str, *, hwnd: int | None = None, rect=None,
                  image_sha256: str | None = None, foreground: bool | None = None) -> dict:
    """校验观测凭据。任一关键量变了 → 抛 GateDenied(code=12)，要求重新观测。"""
    with _exclusive():
        C = _load(CAPTURE_DB, {})
        rec = C.get(tok)
        if not rec:
            raise GateDenied("capture_id 不存在或已被清理", CODE_STALE_CAPTURE)
        if time.time() > rec.get("expires_at", 0):
            C.pop(tok, None); _save(CAPTURE_DB, C)
            # ⚠️ 修：原来这里写死 CAPTURE_TTL（15s），而实际签发时可能用了别的 ttl ——
            #    报错信息报错数字等于没报。改成按本条记录算真实寿命。
            life = float(rec.get("expires_at", 0)) - float(rec.get("issued_at", 0))
            raise GateDenied(f"capture_id 已过期（本条 TTL {life:.0f}s）", CODE_STALE_CAPTURE)
        if hwnd is not None and int(hwnd) != int(rec["hwnd"]):
            raise GateDenied(f"目标窗口变了：观测 {rec['hwnd']} → 现在 {hwnd}", CODE_STALE_CAPTURE)
        if rect is not None and [int(x) for x in rect] != [int(x) for x in rec["rect"]]:
            raise GateDenied(f"窗口位置/大小变了：观测 {rec['rect']} → 现在 {list(rect)}", CODE_STALE_CAPTURE)
        if foreground is not None and bool(foreground) != bool(rec["foreground"]):
            raise GateDenied("前台状态变了（观测时 %s）" % ("前台" if rec["foreground"] else "非前台"),
                             CODE_STALE_CAPTURE)
        if image_sha256 is not None and rec["image_sha256"] and image_sha256 != rec["image_sha256"]:
            # 注意 rec["image_sha256"] 为空表示"签发时没拿到图像哈希"（例如 ctl_tree 只给控件树），
            # 这时**跳过**图像比对，而不是一律判失败 —— 否则会变成永远过不去的假拦。
            raise GateDenied("画面哈希变了（画面已经不是你看的那一张）", CODE_STALE_CAPTURE)
    return rec

def consume_capture(tok: str) -> None:
    """一次性：用过即废（防止把一个观测反复拿来点）。"""
    with _exclusive():
        C = _load(CAPTURE_DB, {})
        if tok in C:
            C.pop(tok)
            _save(CAPTURE_DB, C)

def status() -> dict:
    # 定位不到老脚本目录时**照样返回** —— status 是给人看的状态面板，
    # 「查不到」本身就是一条状态，不该在这里抛异常把面板也一起打死。
    p = _load(PASSFILE, {}) if PASSFILE else {}
    allowed = [short(x) for x in (p.get("scopes") or ([p["scope"]] if p.get("scope") else []))]
    return {
        "brake": {"engaged": brake_engaged(), "flag": BRAKE_FLAG},
        # ★ 老脚本目录没定位到时这里是 None —— 「为什么闸门不可用」得能一眼看见
        "auth_dir": AUTHDIR,
        "legacy_home": HOME,
        "pass": {
            "present": bool(p), "consumed": bool(p.get("consumed")),
            "scopes": allowed,
            "scopes_full": [SCOPES.get(s, s) for s in allowed],
            "expires": p.get("expires"), "acts": p.get("acts", 0),
            "acts_limit": p.get("acts_limit"), "last_act": p.get("last_act"),
        },
        "lease": lease_status(),
        "captures": len(_load(CAPTURE_DB, {})) if CAPTURE_DB else 0,
        "scopes": SCOPES,
    }
