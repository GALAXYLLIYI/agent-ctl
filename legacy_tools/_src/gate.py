# -*- coding: utf-8 -*-
r"""gate.py —— 唯一闸门：任何"看屏幕/动键鼠"的动作都必须先过这里

过闸条件（全部满足才放行）：
  1. 刹车松开（`%LOCALAPPDATA%\\AgentCtl\\_急停.flag` 不存在）
  2. 有一张**没过期、没被用过、范围完全匹配**的通行证（`_auth/auth_pass.json`，由 auth_popup 签发）
  3. 动作范围与通行证范围**字符串完全一致**（A/B/C/D 级不通用，一字之差都不行）

为什么要有这个文件：光有三件套不够，**干活的脚本得真的去问它**。
绕过闸门的脚本 = 闸门不存在。所以 perceive.py 与 act.py 一律先 require()。

用法（脚本里）：
    import gate
    gate.require(gate.A, "读控件树")      # 不过就抛 GateDenied，脚本不许继续
★ **已收编（S2 · 2026-10-23）**—— formerly「两本账」：本模块原按 `HERE/_auth` 找通行证，
  签发方原写死 `BASE/_auth`，新线原走 `HOME/_auth` ⇒ **三方各读各的**。
  现已收编为**单一解析**：`authdir.py`（规则正文的唯一定义处，由 gen_legacy **生成**到本目录）
  ⇒ 三方**同一本账**；`AGENTCTL_AUTHDIR` 不再只覆盖 1/3，而是**三方通吃**。
  判据：tests\_authdir一致_判据.py（J1）+ tests\_authdir注入必红_判据.py（J2）。
★ 本文件在发布包里是**随包执行层**（被 MCP 调用），仓库里是**真源**；改真源后必须刷新运行时并留 md5。

用法（命令行）：
    python gate.py --check "A级 只读感知"
    python gate.py --end                    # 主动结束会话（消费掉通行证）
"""
import contextlib
import datetime
import json
import os
import sys
import time

# ★ D18（2026-10-10）：跨进程锁的**定义处**抽到共享模块 gate_lock.py —— 因为 auth_popup.py
#   也要用同一把锁（同一个 _auth/auth_pass.json）。本文件只 import、**不复制**；
#   LOCKFILE / LOCK_TIMEOUT 仍以本模块的名字对外可见，老调用零变化。
from gate_lock import LOCK_TIMEOUT, LockTimeout  # noqa: E402
from gate_lock import exclusive as _lock_exclusive  # noqa: E402
from gate_lock import lock_path as _lock_path  # noqa: E402
from gate_lock import save_json  # noqa: E402

# 输出被重定向/捕获时，Windows 下 Python 默认用 locale(GBK) 编码 → 打印 ⛔/✅ 会抛
# UnicodeEncodeError 把脚本打死（实测：gate.py --check 就这样崩过）。强制 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ S2（2026-10-23）：AUTHDIR 收编为**单一解析** —— 规则正文的**唯一**定义处是
#   `src\agentctl\authdir.py`；本文件只 import、**不复制**（与 D18 把锁的定义抽到
#   gate_lock.py 同一个纪律：定义一处，引用它）。
#   规则（顺序固定）：
#     1) `AGENTCTL_AUTHDIR` 非空 ⇒ 用它（绝对化）；
#     2) 否则默认 `%LOCALAPPDATA%\AgentCtl\_auth`（与 BRAKE_FLAG 同一个 _APP_DIR）。
#   改前这里是 `env or HERE/_auth`（**认** env），而签发方 auth_popup.py 写死
#   `BASE/_auth`（**不认** env）、新线走 `HOME/_auth`（**不认** env）
#   ⇒ env 覆盖只覆盖三方中的 1/3（工单 §8.3 实测）⇒「签了证这边说没有」随时复现。
#   ★ 本文件是「随包执行层」，**不能**反过来依赖 `src\` ⇒ `authdir.py` 由
#     `tools\gen_legacy.py` **生成**到本目录与 legacy_tools\ 根下（生成物，不是人手写的
#     第二份）⇒ 两边永远逐字节一致，台账 + CHK-A 照常盯它（P8 的机器盯防）。
#   ⇒ 改这一处之前先看 src\agentctl\authdir.py；改完必须跑 tests\_authdir一致_判据.py。
# ★ 伴生模块的可达性：authdir.py 与本文件**同目录** ⇒ 先把它加进 sys.path 再 import
#   （与 coord.py「自插老线路径：包内要指向自己」同一个手法）——
#   否则「按文件路径 import 本文件」的场景（判据、工具）会因为**当前工作目录**不同而找不到它。
_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)
import authdir  # noqa: E402  （同目录的**生成件**：authdir.py；缺它 ⇒ 本模块导不进来）
AUTHDIR = authdir.resolve()
PASSFILE = os.path.join(AUTHDIR, "auth_pass.json")
GATELOG = os.path.join(AUTHDIR, "gate.log")
# ★ 跨进程锁文件：路径由共享模块 gate_lock 统一给出（`<AUTHDIR>/gate.lock`）。
#   **必须与新线 agentctl.gate 用同一个路径**（两侧共用同一份 auth_pass.json：
#   新线 HOME 实测解析到本目录，PASSFILE 是同一个文件）—— 各锁各的等于没锁。
LOCKFILE = _lock_path(AUTHDIR)
IS_WIN = os.name == "nt"
# ── 急停文件位置（T10 治本：四方口径统一）────────────────────
#
# 改动前的病：**两套闸门各读一份急停文件** —— 这个老 gate 读桌面路径，
# 而 agentctl.gate 读 %LOCALAPPDATA%\AgentCtl\_急停.flag，两者互相看不见；
# 后果是热键/面板拉下的急停，老脚本 act.py 里那 8 处 gate.require 一无所知
# （双保险里有一半是瞎的）。2026-10-05 先用「环境变量 + 保留旧默认」缓解，
# 2026-10-09（T10）直接把默认值也统一过来，治本完成。
#   后果：热键/面板拉下新路径的急停，老脚本 act.py 里那 8 处
#   gate.require **一无所知**（双保险里有一半是瞎的）。
#
# 为什么是「环境变量 + 保留 fallback」而不是「直接换默认值」：
#   直接改默认值 = 老 bat、老入口**静默改指向** —— 那些还没迁移的
#   旧调用方会以为还在管桌面那个文件，而实际已经不管了，比双轨更难查。
#   改成读环境变量后：**新调用方传了就统一，没传就保持老行为**，
#   可回退、可逐步迁移。治本的那一步在 agentctl/core.py：
#   它 spawn 老脚本时把 AGENTCTL_BRAKE_FLAG 塞进子进程环境，
#   于是不管老脚本解析到哪个 gate，读的都是同一个文件。
# ★ T10 治本（2026-10-09）：与老线 brake.py / legacy_tools / 新线 gate 四方口径一致。
# ★ S2（2026-10-23）：这段 `_APP_DIR` 与 `authdir.app_dir()` 是**同一个口径**
#   （同一个公式）—— 账本默认位置由 authdir.py 给，急停由这里给。
#   ★ 改一边就要改另一边（判据 J1 的第 ③ 档用例就是查这个：两边都认 AGENTCTL_APP_DIR）。
_APP_DIR = os.environ.get("AGENTCTL_APP_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl")
BRAKE_FLAG = os.environ.get("AGENTCTL_BRAKE_FLAG") or os.path.join(_APP_DIR, "_急停.flag")

A = "A级 只读感知"
B = "B级 点击滚动"
C = "C级 打字快捷键"
D = "D级 拖拽"
# 感知是所有级别的底座：手里拿着 B 证却读不了控件树，是自相矛盾（点之前总得先看）
PERCEIVE_SCOPES = (A, B, C, D)

# 会话内允许重复动作的级别（A 只读可多看几次；B 以上高危，每张证限定动作次数）
ACT_LIMIT = {A: 200, B: 20, C: 20, D: 10}


class GateDenied(Exception):
    pass


def log(msg):
    os.makedirs(AUTHDIR, exist_ok=True)
    with open(GATELOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    # ★ D18·R1：原子写（唯一临时名 + 有界重试替换）已**统一到共享模块 gate_lock.save_json**；
    #   本函数只留老名字转发 —— 防的是老病：两处 _save 各写一遍，还各漂一点。
    save_json(path, obj)


def _load_checked(path):
    """★ D18·R2：把「确实没有证」与「读失败」分开。返回 (对象, 错因)。

    `_load()` 的 `except: return {}` 会把**瞬时读失败**伪装成「没有通行证」；
    而 require() 的计数写回会拿这份空 dict 当状态**写回去** ⇒ 整张证被打残
    （只剩 `acts`，`scope`/`acts_limit`/`expires_at` 全丢）。实测压力下瞬时读失败约 48%。
    错因取值：`""` 正常 / `"missing"` 文件不存在 / 其它＝读失败原因。
    """
    try:
        if not os.path.exists(path):
            return {}, "missing"
        with open(path, encoding="utf-8") as f:
            return json.load(f), ""
    except FileNotFoundError:
        return {}, "missing"        # 与并发写抢窗口：等同"这会儿没有证"
    except Exception as e:
        return {}, "%s: %s" % (type(e).__name__, e)


# ────────────────────── 跨进程互斥（D16 引入 → D18 抽到共享模块）──────────────────────
# 为什么必须加锁（实测，不是推测）：
#   `check()` 读盘判定 与 `require()` 计数写回 原本是**两步**，中间没有互斥 ——
#   40 个进程同时打一张「上限 5 次」的证，5 轮累计**放行 48 次、超发 26 次**
#   （台架 work\_本地 agent_20261009\d16_race.py，改前台账 d16_race_改前.json）。
#   ⇒ 症状是「用户签了 5 次，实际能点 13 次」。
# ★ D18：定义处已搬进 gate_lock.py（auth_popup 也要用它）。本函数只剩**翻译层**：
#   把共享模块的 LockTimeout 翻成闸门的 GateDenied —— 老调用与台架都按 GateDenied 接。
@contextlib.contextmanager
def _exclusive(timeout=None):
    """跨进程互斥（定义在 gate_lock.py；此处保留老名字与老语义）。"""
    if timeout is None:
        timeout = LOCK_TIMEOUT      # 读本模块属性 ⇒ `gate.LOCK_TIMEOUT = x` 这个老旋钮仍有效
    try:
        with _lock_exclusive(AUTHDIR, timeout):
            yield
    except LockTimeout as e:
        raise GateDenied("%s，本次拒绝（不越权放行）" % e)


def brake_engaged():
    return os.path.exists(BRAKE_FLAG)


# ── T11 留痕②：闸门因急停拒绝时补记 brake.log ──────────────────
# 与 brake.py 的轮询留痕**分工**：
#   brake.py（候选①）记的是「谁/什么时候动了急停这个文件」（状态翻转才记一次）；
#   本函数（候选②）记的是「这次拦截确实由急停造成」（每个被拒动作都记）。
# 两者来源字段不同，可分辨，不会互相冒充。
#
# ★ fail-safe：写日志失败**绝不能**影响拒绝本身 —— 拒绝永远优先于留痕。
BRAKELOG = os.path.join(AUTHDIR or HERE, "brake.log")


def _log_brake_refusal(scope):
    """因急停拒绝时补记 brake.log。**永不抛异常**（排障用的东西不能把系统带崩）。"""
    try:
        d = os.path.dirname(BRAKELOG)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(BRAKELOG, "a", encoding="utf-8") as f:
            f.write("[%s] ⛔ 闸门拦截（来源=gate拒绝补记）：刹车已拉下，拒绝 %s\n"
                    % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), scope))
    except Exception:
        pass




# ───────────────────── T5：通行证可选「目标三元组」targets ─────────────────────
#
# 一张证可以**可选地**多带一层 targets —— 用户授权时把放行收紧到具体目标：
#     "targets": {"pids": [1234], "title_contains": ["记事本"], "actions": ["click"]}
# 三个子项都可选，填了哪项就按哪项判：
#     pid / pids            动作必须交得出 pid，且**落在列表里**
#     title_contains        动作必须交得出窗口标题，且**含列表里任一片段**
#     action / actions      动作必须交得出动作名，且**在白名单里**
#
# ★ 口径（fail-closed）：
#   · 证里**有** targets → 动作必须交得出目标且**逐项对齐**；交不出、或任一项对不上
#     → 一律拒绝。「拿不到目标信息」不是放行理由，恰恰是拒绝的理由。
#   · 证里**没有** targets → 下面这段整体不进入，行为与本文件改前**逐字节一致**。
#
# 为什么判据写 `"targets" in p` 而不是 `p.get("targets")`：
#   空 dict 在 Python 里是**假值**。若用 get，`"targets": {}` 会被当成「没带 targets」
#   而**静默放行** —— 那是 fail-open，正好反了。声明了字段就必须能判，判不了就拒。
#
# ★ guard 侧不实现「窗口标题片段」这一项（dsh-computer-use-guard/index.js）：
#   guard 是**同步**的，起不了子进程/发不了 HTTP，而 pid→标题要**实时枚举**窗口，
#   Node 侧同步做不到。那边只做 pid + 动作名，遇到声明了 title_contains 的证一律拒。
#   想用标题项，就得走本文件（Python 侧做得到）。
_TARGET_ALIAS = {
    "pids": "pids", "pid": "pids",
    "title_contains": "titles",
    "actions": "actions", "action": "actions",
}


def _as_list(x):
    """把 targets 的子项规整成字符串列表；空/None 非列表一律按单元素处理。"""
    if x is None:
        return []
    if not isinstance(x, (list, tuple)):
        x = [x]
    return [str(v).strip() for v in x if str(v).strip()]


def _norm_targets(tg):
    """规整三元组。返回 (norm, err)。err 非空 = 判不了，调用方必须拒绝。"""
    if not isinstance(tg, dict):
        return None, f"targets 必须是对象（pid/窗口标题片段/动作名 三选一以上），本次是 {type(tg).__name__}"
    norm = {"pids": [], "titles": [], "actions": []}
    for k, v in tg.items():
        ks = str(k)
        if ks.startswith("_"):
            continue                              # 下划线开头 = 写给人看的注释，不参与判定
        g = _TARGET_ALIAS.get(ks)
        if not g:
            return None, (f"targets 里有闸门不认识的键「{ks}」"
                          "（只认 pid/pids、title_contains、action/actions）"
                          "—— 判不了，按最严处理拒绝")
        norm[g].extend(_as_list(v))
    return norm, ""


def _pids_of(vals):
    """把证里的 pid 项转成 int 列表。返回 (pids, err)。err 非空 = 判不了。"""
    out = []
    for x in vals:
        try:
            out.append(int(float(str(x).strip())))
        except Exception:
            # ★ 必须在**判定之前**先验，不能等到"动作交了 pid"才去转 ——
            #   否则动作一旦交了 pid 就会在这里抛 ValueError（自测里真踩到过），
            #   变成"闸门自己崩"而不是"闸门拒绝"。
            return None, (f"目标三元组·pid 不符：证里写的 pid「{x}」不是整数"
                          " —— 判不了，按最严处理拒绝")
    return out, ""


def check_targets(p, target=None):
    """证里带 targets 时的目标校验。返回 (是否通过, 原因)。

    ★ 这层**永不抛异常**：里面出任何岔子都转成「拒绝」（fail-closed）。
      理由与 guard 侧同款 —— 闸门自检出错的正确结局是拒绝，不是让动作继续。
    """
    try:
        return _check_targets_inner(p, target)
    except Exception as e:
        return False, (f"目标三元组判定内部出错（{type(e).__name__}: {e}）"
                       " —— 判不了，按最严处理拒绝")


def _check_targets_inner(p, target):
    """真正干活的。原因串**必须写明三元组里是哪一项不符**（pid / 窗口标题 / 动作名），
    好让拒绝日志一眼看出是「打错了窗口」还是「动作类型不对」。"""
    norm, err = _norm_targets(p.get("targets"))
    if err:
        return False, f"目标三元组格式不对：{err}"
    want_titles, want_actions = norm["titles"], norm["actions"]
    want_pids, err = _pids_of(norm["pids"])
    if err:
        return False, err
    if not (want_pids or want_titles or want_actions):
        return False, ("目标三元组为空：证里声明了 targets，但 pid/窗口标题片段/动作名 三项都没填 "
                       "—— 空声明不等于不限制，按最严处理拒绝")
    t = target if isinstance(target, dict) else {}
    want_pids_s = "/".join(str(x) for x in want_pids)

    # ① pid
    if want_pids:
        got = t.get("pid")
        if got is None or str(got).strip() == "":
            return False, (f"目标三元组·pid 不符：本证限定 pid {want_pids_s}，"
                           "但本次动作**没提供 pid**（拿不到目标信息即拒）")
        try:
            got_i = int(float(str(got).strip()))
        except Exception:
            return False, (f"目标三元组·pid 不符：本证限定 pid {want_pids_s}，"
                           f"本次动作给的 pid「{got}」不是整数")
        if got_i not in want_pids:
            return False, (f"目标三元组·pid 不符：本证限定 pid {want_pids_s}，"
                           f"本次动作打的是 pid {got_i}")

    # ② 窗口标题片段
    if want_titles:
        got = t.get("title")
        if got is None or str(got).strip() == "":
            return False, (f"目标三元组·窗口标题不符：本证限定标题含 {'/'.join(want_titles)} 之一，"
                           "但本次动作**没提供窗口标题**（拿不到目标信息即拒）")
        if not any(w in str(got) for w in want_titles):
            return False, (f"目标三元组·窗口标题不符：本证限定标题含 {'/'.join(want_titles)} 之一，"
                           f"本次动作的窗口标题是「{got}」")

    # ③ 动作名
    if want_actions:
        got = t.get("action")
        if got is None or str(got).strip() == "":
            return False, (f"目标三元组·动作名不符：本证只允许 {'/'.join(want_actions)}，"
                           "但本次动作**没提供动作名**（拿不到目标信息即拒）")
        if str(got).strip() not in want_actions:
            return False, (f"目标三元组·动作名不符：本证只允许 {'/'.join(want_actions)}，"
                           f"本次动作是「{str(got).strip()}」")

    return True, ""


TARGETS_HELP = """gate.py —— 通行证「目标三元组」targets（T5 新增，2026-10-08）

一张证可以在授权时可选地带上 targets，把放行收紧到**具体目标**：

  "targets": {
    "pids":           [1234, 5678],       # 只认这些 pid（窗口属主进程）
    "title_contains": ["记事本"],          # 只认标题含这些片段之一的窗口
    "actions":        ["click", "keys"]   # 只认这些动作名
  }
三个子项都可选（也接受 pid / action 单数写法）。可另加 "_关于" 之类下划线开头的键当注释。

判定（fail-closed）：
  * 证里**有** targets → 动作必须交得出目标且逐项对齐；交不出 / 对不上任一项 → 拒绝。
  * 证里**没有** targets → 行为与本文件旧版本逐字节一致。
  * targets 为空对象、格式不对、或含不认识的键 → 一律拒绝（怕的是「声明了却没生效」）。

命令行（供自测 / 排查）：
  python gate.py --check "B级 点击滚动" --target-pid 1234 --target-title 记事本 --target-action click
"""


def _target_from_argv(argv):
    """从命令行尾巴上摘 --target-pid / --target-title / --target-action。
    一个都没给 → 返回 None（老调用路径，行为不变）。"""
    keymap = {"--target-pid": "pid", "--target-title": "title", "--target-action": "action"}
    t = {}
    i = 0
    while i < len(argv):
        k = argv[i]
        if k in keymap and i + 1 < len(argv):
            t[keymap[k]] = argv[i + 1]
            i += 2
            continue
        i += 1
    return t or None


def check(scope, target=None):
    """返回 (是否放行, 原因)。target 只有证里带 targets 时才用得上。"""
    if brake_engaged():
        _log_brake_refusal(scope)
        return False, "刹车已拉下（急停文件存在）—— 一切自动控制停止"
    p, err = _load_checked(PASSFILE)
    if err and err != "missing":
        # ★ D18·R2：读失败**不能**伪装成「没有通行证」—— 那会误导人以为没授权、跑去重新签发。
        return False, f"闸门状态读不了（{err}）—— 本次拒绝（不写回）"
    if not p:
        # ★ D3 修复（2026-10-19，窗口D 实跑时踩出）：**报错必须报出查的是哪个目录**。
        #   本机实测：同一个 `act.py`，在 agent-ctl\legacy_tools 下跑报「没有通行证」，
        #   在 dsh-viewer-plugin\tools 下跑**通过** —— 因为这层 gate.py 用
        #   `AUTHDIR = HERE/_auth`（HERE = gate.py 所在目录），而 src\agentctl\gate.py
        #   用四层查找解析出的 HOME（= dsh-viewer-plugin/tools）——
        #   两者一旦分叉就是**两本账**（gate.py 顶部注释里早标了「已知分歧，未修」）。
        #   改前这句只说「没有通行证」，**不说自己查了哪儿** ⇒ 排查成本全推给人：
        #   人只能一个目录一个目录试。
        return False, ("没有通行证（需先由用户在弹窗里授权）—— 本次查的是 %s"
                       "；若你明明刚授权却报这句，多半是**跑错了这份 act.py 的目录**"
                       "（同机可能存在两本账），请对照上面这个路径看 _auth\auth_pass.json"
                       % AUTHDIR)
    if p.get("consumed"):
        return False, "通行证已被使用（一次性，重新授权）"
    if time.time() > p.get("expires_at", 0):
        return False, f"通行证已过期（{p.get('expires')}）"
    # 范围：用户可以在一次授权里**明确列出**多个（如 B+C+D），但**绝不隐式升权**
    allowed = p.get("scopes") or ([p["scope"]] if p.get("scope") else [])
    if scope not in allowed:
        return False, f"范围不符：本次持 {'、'.join(allowed)}，本动作要「{scope}」"
    n = p.get("acts", 0)
    # ★ 2026-10-19 用户定：**一个任务一次签、取消动作次数上限**（急停兜底）。
    _lim = p.get("acts_limit")
    limit = int(_lim) if _lim is not None else int(ACT_LIMIT.get(scope, 10))
    if limit <= 0:
        return True, "次数不封顶（急停兜底）"
    if n >= limit:
        return False, f"本张通行证动作次数已用尽（{n}/{limit}）"
    # T5：可选的目标三元组。用 `in` 判存在（空 dict 是假值，用 get 会 fail-open）。
    # 证里没有这个键 ⇒ 这段整体不进入 ⇒ 返回值与改前逐字节一致。
    if "targets" in p:
        ok_t, why_t = check_targets(p, target)
        if not ok_t:
            return False, why_t
    return True, f"范围「{scope}」有效至 {p.get('expires')}，已用 {n}/{limit} 次"


def require(scope, note="", count=True, target=None):
    """过闸；不过就抛 GateDenied。count=True 时记一次动作（会话动作计数）。

    target（T5 新增，关键词、默认 None）：动作要打的**目标**，形如
    `{"pid": 1234, "title": "无标题 - 记事本", "action": "click"}`。
    只有证里带了 targets 时才需要它；证里没有时常量地不生效。
    ★ act.py 本单**不改**，所以它调 require() 时不传 target ——
      一张带 targets 的证经过 act.py **必然被拒**，这正是要求的 fail-closed。

    ★ D16（2026-10-09）：整段「判定 → 计数 → 写回」现在**在同一把跨进程锁里**。
      改前这段没有互斥，40 进程并发打一张 5 次的证实测超发 26 次。
    """
    with _exclusive():
        ok, why = check(scope, target)
        if not ok:
            log(f"⛔ 拒绝放行（{note or scope}）：{why}")
            raise GateDenied(why)
        if count:
            # ★ D18·R2：check() 刚放行、这里却读不到状态 ⇒ **瞬时读失败**。
            #   改前这里**没有守卫**：`_load` 返回的 `{}` 会被当状态写回去 ⇒ 整张证被打残
            #   （只剩 acts，scope/acts_limit/expires_at 全丢；故障注入可复现）。
            #   现在一律**拒绝且不写回**（fail-closed）。
            p, err = _load_checked(PASSFILE)
            if err and err != "missing":
                log(f"⛔ 拒绝放行（{note or scope}）：闸门状态读不了（{err}）")
                raise GateDenied(
                    f"闸门状态读不了（{err}）—— 本次拒绝，且不写回（避免把通行证写残）")
            if not p:
                log(f"⛔ 拒绝放行（{note or scope}）：闸门状态瞬时读不到")
                raise GateDenied(
                    "闸门状态瞬时读不到（可能正撞上并发写窗口）—— 本次拒绝，且不写回"
                    "（避免把通行证写残）")
            p["acts"] = p.get("acts", 0) + 1
            p["last_act"] = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {note or scope}"
            _save(PASSFILE, p)
        log(f"✅ 放行（{note or scope}）：{why}")
    return why


def require_any(scopes, note="", count=None, target=None):
    """多个级别里任一有效即可放行（用于"感知"这种所有级别共用的动作）。

    ★ D18·A3-F1（治本）：`count=None`（默认）＝**自动**——
      `scopes` **覆盖全部感知档**时**不计数**（那就是"只看不动"的读操作），其余照常计数。
      显式传 `count=True/False` 仍然照办（保留逃生口）。

    为什么在共享入口改、而不是逐调用点加 `count=False`：
      逐点改必漏 —— 本单第一版就漏了 `act.py:1163`（读文档）与 `:1177`（列可点控件），
      结果"读操作不扣额度"只堵住了三条路径里的一条。规则放在入口，**新增调用点默认就是对的**。
    审计（`_本地 agent_20261009\\d18_a3f1_audit.py`，全量、不截断）：老线共 6 处感知入口，
      真实动作一律走单档 `require(A/B/C/D, …)` ⇒ 不受本条影响（那 6 条动作调用点仍计数）。
    """
    if count is None:
        count = not set(PERCEIVE_SCOPES).issubset(set(scopes))
    # ★ D18·R2：先做一次守卫读 —— 读不了就直接拒绝，别拿 4 个范围刷 4 条同样的话。
    _p, _err = _load_checked(PASSFILE)
    if _err and _err != "missing":
        log(f"⛔ 拒绝放行（{note or '任一范围'}）：闸门状态读不了（{_err}）")
        raise GateDenied(f"闸门状态读不了（{_err}）—— 本次拒绝（不写回）")
    reasons = []
    for s in scopes:
        ok, why = check(s, target)
        if ok:
            return require(s, note, count, target)
        reasons.append(f"{s} → {why}")
    log(f"⛔ 拒绝放行（{note or '任一范围'}）：" + "；".join(reasons))
    raise GateDenied("；".join(reasons))


def end_session(why="主动结束"):
    # D16：同样是读-改-写（consumed 置位），与 require() 的计数共用一把锁。
    # 改前它与计数写回并发时，后写的一方会把对方的字段覆盖掉。
    with _exclusive():
        p, err = _load_checked(PASSFILE)
        if err and err != "missing":
            return False, f"闸门状态读不了（{err}）—— 本次不结束（不写回）"
        if not p or p.get("consumed"):
            return False, "没有可用会话"
        p["consumed"] = True
        p["consumed_at"] = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
        p["ended_why"] = why
        _save(PASSFILE, p)
        log(f"🔚 会话结束（{why}）：范围「{p.get('scope')}」，动作 {p.get('acts', 0)} 次")
        return True, f"会话结束：范围「{p.get('scope')}」，动作 {p.get('acts', 0)} 次"


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 1
    if a[0] == "--targets-help":
        # 新增的独立开关：这样 --help 的输出保持**逐字节不变**（老调用零变化）
        print(TARGETS_HELP)
        return 0
    if a[0] == "--end":
        ok, why = end_session("命令行结束")
        print(("🔚 " if ok else "— ") + why)
        return 0 if ok else 3
    if a[0] == "--check":
        # 老调用是 `--check [范围]`；T5 起可再缀 --target-pid/--target-title/--target-action。
        # 只有下一个参数**不是选项**时才当范围 —— 老调用传的全是中文范围串，行为不变。
        scope = a[1] if len(a) > 1 and not a[1].startswith("--") else A
        ok, why = check(scope, _target_from_argv(a[1:]))
        print(("✅ " if ok else "⛔ ") + why)
        return 0 if ok else 3
    if a[0] == "--brake":
        print("🛑 刹车已拉下" if brake_engaged() else "✅ 刹车松开")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
