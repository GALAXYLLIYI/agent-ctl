# -*- coding: utf-8 -*-
r"""check_live_sync.py —— **live 漂移自动闸**：live（本机真在跑的那份 legacy）↔ 仓内产物。

★ 这条闸治的是哪个病
  M19prime / M19P / M19P2 / M20 四轮改动，每一轮的收口动作都是**人肉核 MD5**：
  真源 `legacy_tools\_src\`、仓内产物 `legacy_tools\`、live（真在跑的那份）三处全等。
  人肉核的病：① 漏一次没人知道 —— 仓里**看不出来**，运行行为却已经漂了；
  ② 核的是"人当时记得核"的那几轮，不是每次改动。
  ⇒ 本工具把「人肉核 MD5」变成**一条命令**，并在发包前强制跑（见 `tools\pack_dist.py`）。

★ 三者的关系（先说清，否则比对口径没法定）
  · 真源     = `legacy_tools\_src\`（`tools\gen_legacy.py` 的 `SRC` 默认；2026-10-18 起从仓外收进仓内）
  · 仓内产物 = `legacy_tools\`（`gen_legacy.py --out legacy_tools` 的产物；发包时**再现场生成一份**）
  · live     = **本机真正在跑的那份** = `build\local_legacy_home.txt` 指针指向的目录
               （本机当前指向本机 live 目录，路径由指针层/环境变量决定；
                 指针文件本身 **未 tracked** —— 它是本机状态，不是仓内产物）

★ 为什么必须自动闸
  live 是**本机状态**：不在仓里、不受 git 保护 ⇒ 有人直接改了 live、或改了真源忘了同步回 live，
  仓里**完全看不出来**，但机器跑的行为已经变了。⇒ 只能在**发包前**拦。

★ 比对口径：**不另立一套**，直接用 `gen_legacy.md5n`（去 BOM、CRLF/CR→LF）
  —— 与双基线台账 A₀ / CHK-A 的口径**逐字一致**。
  两套口径 = 改个换行就能骗过其中一处，等于没有闸。
  ⇒ 本文件**不定义**自己的 `_norm`；gen_legacy 取不到 ⇒ 退出码 2（验不了），不退回自带实现。

★ live 的定位 —— **与运行时逐条对齐**（`src\agentctl\gate.py` 的 `_resolve_home_detailed()`）
  ★ 这条闸的意义就是「**你比的就是它跑的那份**」⇒ 定位口径必须一字不差地跟着运行时。
    运行时（gate.py:276-352）的层序是：
      ① 环境变量 `AGENTCTL_LEGACY_HOME`（gate.py:306-319）
      ② 冻结态 `sys._MEIPASS\legacy_tools`（gate.py:321-326）
      ③ 指针 `build\local_legacy_home.txt`（gate.py:328-343，**声明层**）
      ④ 源码态上探：多级 root 找 `legacy_tools\`（gate.py:345-349）
    本闸**照抄这四层**（**不 import gate.py** ⇒ 抄写会漂，靠下面的静态对齐自检兜住）。
  ★ 指针本身也**多级 root 上探**（gate.py:253-270）：从 `src\agentctl` 起往上最多 4 级，
    取**第一个有内容**的 `build\local_legacy_home.txt`（空 = 没写指针 ⇒ 继续往上找）。
  ★ **冲突不许藏**：环境变量与指针**同时存在且指向不同目录** ⇒ 输出与 `--json` 都报
    `conflict:true`、两个路径都列出来，且**判红（退出码 1）** ——
    因为运行时（环境变量优先）跑的那份，和「按指针比」比的那份，**根本不是同一份**。
  ★ `--live <目录>` 是判据造用例的口子（显式覆盖）；它与运行时选中的目录不一致时同样报冲突。
  ★ 都定位不到 ⇒ **明确报「无法定位 live」**（退出码 2），**不猜、不静默跳过**。

★ 退出码（三档，刻意分开）
  0 = live 与仓内产物**逐字节一致**
  1 = **有红**：内容不一致 / live 缺件 / 产物缺件 / ★**定位冲突**（env 与指针指向不同目录）
      / ★**口径抄写漂移**（本文件的抄写与 gate.py 对不上）
  2 = **验不了**：定位不到 live / live 目录不存在 / 仓内产物目录不存在 / 口径模块取不到
  ★ 2 与 1 必须分开：「查不到」≠「查出不一致」。
    混成一个码的后果很具体：别的机器装完就是"没 live" ⇒ 闸永远红 ⇒ 人加个 --force 绕过去 ⇒ 闸名存实亡。
    ⇒ 发包侧对 2 的处置是**跳过并提示**（不是红），对 1 才是**中止发包**。
  ★ 冲突（1）之所以**必须红**：闸的意义就是「比的就是它跑的那份」。
    运行时（gate.py:306-319）环境变量优先；env 与指针指向不同目录 ⇒ 运行时跑的那份
    和闸能比的那份**根本不是同一份** ⇒ 闸比过了也等于没比 ⇒ **判红，不许藏**。

★ 只读保证
  本工具对 live、对仓内产物**只读**：不写、不删、不改属性。
  `--inject` 也不例外 —— 它把 live **复制**到临时目录再改副本，live 本体一个字节都不动。

用法：
    python -X utf8 tools\check_live_sync.py             # 人读报告
    python -X utf8 tools\check_live_sync.py --json      # CI 消费（stdout 只有 JSON）
    python -X utf8 tools\check_live_sync.py --quiet     # 静默，只看退出码
    python -X utf8 tools\check_live_sync.py --inject    # 注入式跑红自证（改副本，期望红）
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

GEN_PATH = os.path.join(HERE, "gen_legacy.py")
PRODUCT_DIR = os.path.join(ROOT, "legacy_tools")
POINTER_NAME = os.path.join("build", "local_legacy_home.txt")

# ★ 运行时那份 gate.py 的位置 —— 本文件**照抄**它的定位口径（不 import 它）。
GATE_PY = os.path.join(ROOT, "src", "agentctl", "gate.py")

# ── 能力体检口径（**抄自** `src\agentctl\gate.py`，行号见括号）────────────
#   ⚠ 抄写就会漂 ⇒ 本文件自带**静态对齐自检**（_alignment_check()）：
#     它读 gate.py 的**源码文本**（不是 import）把下面这几个常量抠出来逐条比，
#     对不上就**判红 + 点名**。抠不出来（格式变了/读不到）只记 note，**不判红**。
LEGACY_PROBE = 3                                # 对齐 gate.py:124 `_LEGACY_PROBE = 3`
LEGACY_GATE_NAME = "gate.py"                    # 对齐 gate.py:125
LEGACY_VERSION_FILE = "gate_version.json"       # 对齐 gate.py:133
LEGACY_CAPS = {"targets": r"(?m)^def check_targets\("}   # 对齐 gate.py:129-131
LEGACY_REQUIRED = ("targets",)                  # 对齐 gate.py:132

EXIT_OK = 0        # 一致
EXIT_DRIFT = 1     # 有红：live 与仓内产物不一致 / **定位口径冲突** / 口径抄写漂移
EXIT_NOLIVE = 2    # 验不了：定位不到 live / 目录不存在 / 口径取不到

# 与 pack_dist.BACKUP_PAT 同形（备份文件识别）。这里独立写一份是为了**不与 pack_dist 互相 import**
# （pack_dist 要 import 本模块做闸 ⇒ 反向 import 会成环）。
BACKUP_PAT = re.compile(r"(\.bak($|[._])|_bak[._]|\.old($|[._])|\.orig($|[._])|\.tmp$|~$|\.swp$)", re.I)
COMPILED_EXT = {".pyc", ".pyo", ".pyd"}


# ────────────────────────── 基础工具 ──────────────────────────

def _reconfigure_stdio():
    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def load_gen():
    """按路径加载 gen_legacy —— 只为拿到**同一份**口径（md5n）与**同一份**登记清单（FILES）。

    ★ 不往 sys.path 里塞东西（免得起重名），也不在本文件里重写一份 FILES / _norm：
      复制第二份实现 = 自己给自己开「口径两套」那个洞。
    ★ 取不到就返回 None ⇒ 调用方退出码 2（验不了）—— **不退回自带实现**：
      口径取不到还硬比，比出来的结论没有意义。
    """
    if not os.path.isfile(GEN_PATH):
        return None
    try:
        spec = importlib.util.spec_from_file_location("_gen_legacy_for_live_sync", GEN_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        return None
    if not hasattr(mod, "md5n") or not hasattr(mod, "FILES"):
        return None
    return mod


def list_files(d):
    """顶层普通文件（**不递归**）。返回 {文件名: 绝对路径}。

    ★ 只取顶层：live 与仓内产物的对应关系是**同名即对应**，且都在顶层（扁平结构，已实测）。
      递归会把 __pycache__ / _auth / _digest 里的运行数据拖进来 —— 那些**永远不该比对**。
    """
    out = {}
    if not os.path.isdir(d):
        return out
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for name in names:
        p = os.path.join(d, name)
        try:
            if os.path.isfile(p):
                out[name] = p
        except OSError:
            pass
    return out


def list_dirs(d):
    out = []
    if not os.path.isdir(d):
        return out
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for name in sorted(names):
        p = os.path.join(d, name)
        try:
            if os.path.isdir(p):
                out.append(name)
        except OSError:
            pass
    return out


def classify_extra(name):
    """给「不在登记清单里的额外文件」一个**逐条可读**的判断。

    ★ 判断只有一种：**忽略（不判红）**。理由：
      登记清单（`gen_legacy.FILES`）才决定"什么进包"；清单外的文件不进包 ⇒
      它们与「live 与仓内产物是否一致」无关。判红 ⇒ 每台没部署老线的机器都会红 ⇒
      闸被 --force 绕过去 ⇒ 闸名存实亡。
    ★ 但**必须逐条列出来**：不列就等于"live 里混进了什么没人看得见"，
      而那正是这条闸要提供的可见性。
    """
    if name == "_md5台账.md":
        return "产物侧台账（生成物；live 不该有）"
    if name == "case_data.json":
        return "真人员数据（gen_legacy 故意不登记、不进包）"
    if BACKUP_PAT.search(name):
        return "备份文件（.bak/_bak_/…）"
    if os.path.splitext(name)[1].lower() in COMPILED_EXT:
        return "编译产物"
    if name.startswith("_"):
        return "一次性脚本/私有文件（_ 开头，不进包）"
    if name.lower().startswith("readme"):
        return "说明文档（不进包）"
    if name.endswith(".md"):
        return "说明文档（不进包）"
    return "老线自有文件（不进包）"


# ────────────────────────── live 定位（与运行时逐条对齐） ──────────────────────────
#
# ★★ 这一段是「闸与运行时同口径」的**全部**依据 ★★
#   运行时 = `src\agentctl\gate.py` 的 `_resolve_home_detailed()`（本文件**不 import**它：
#   它 import 时就算 HOME、算体检，副作用多、依赖也多 ⇒ 只能逐条抄写 + 静态自检兜底）。
#
#   ┌──┬────────────────────────────────────────────┬───────────────┬──────────────────┐
#   │# │ 规则（运行时怎么做的）                     │ gate.py 行号  │ 本文件对应实现   │
#   ├──┼────────────────────────────────────────────┼───────────────┼──────────────────┤
#   │1 │ root 列表：gate.py 所在目录起，往上最多 4 级 │ 242-251       │ _project_roots() │
#   │2 │ 指针查找：多级 root 里**第一个有内容**的     │ 253-270       │ _pointer_pick()  │
#   │3 │ 层序 ①env → ②meipass → ③指针 → ④源码上探   │ 276-352       │ resolve_live()   │
#   │4 │ ① env：有 gate.py ⇒ 体检；不合格 ⇒ **硬停** │ 306-319       │ 见 resolve_live()│
#   │5 │ ② meipass：发现层，不合格就跳过下一个       │ 321-326       │ 见 resolve_live()│
#   │6 │ ③ 指针（**声明层**）：目录不在/体检不合格     │ 328-343       │ 见 resolve_live()│
#   │   │    ⇒ **不回落**（绝不替你换一个目录）       │               │                  │
#   │7 │ ④ 源码态上探：逐个 root 找 legacy_tools\    │ 345-349       │ 见 resolve_live()│
#   │8 │ 能力体检（代码实测 + 可选版本戳 sha256 锚）  │ 168-182/184-240│ _legacy_gate_report()│
#   └──┴────────────────────────────────────────────┴───────────────┴──────────────────┘
#
#   ⚠ **声明层 vs 发现层**（gate.py:286-291）：① env、③ 指针是「有人指名道姓写下的」⇒
#     **要么成立、要么报错**，绝不悄悄换成另一个目录；② meipass、④ 上探是「就近找找看」
#     ⇒ 不合格就跳过、记进 rejected、继续找下一个。抄写时这条界线不能糊。
#
#   ⚠ **冲突不许藏**：env 与指针同时存在且指向不同目录 ⇒ 运行时跑的是 env 那份，
#     而「按指针比」比的是另一份 ⇒ 闸形同虚设 ⇒ 报 conflict + 两条路径 + **判红**。

def _abs(p):
    """把路径规成绝对路径（Windows 上把 / 换成 \\）。"""
    return os.path.abspath(str(p).replace("/", os.sep).replace("\\", os.sep))


def _same_dir(a, b):
    """两个路径是不是同一个目录（Windows 上大小写不敏感 ⇒ normcase）。"""
    try:
        return os.path.normcase(_abs(a)) == os.path.normcase(_abs(b))
    except Exception:
        return False


def _project_roots():
    """对齐 gate.py:242-251 `_project_roots()`。

    ★ gate.py 的起点是**它自己所在目录**（<仓根>\\src\\agentctl），往上最多
      `_LEGACY_PROBE + 1` = 4 级 ⇒ [src/agentctl, src, <仓根>, <仓根的上一级>]。
      本文件在 <仓根>\\tools\\ ⇒ 起点换算成同一仓根下的 src\\agentctl（gate.py 真身在那儿）；
      仓里没有 src\\agentctl 时退回本文件所在目录，并把这件事写进 live_info（不藏）。
    """
    start = os.path.join(ROOT, "src", "agentctl")
    if not os.path.isdir(start):
        start = HERE
    roots, cur = [], start
    for _ in range(LEGACY_PROBE + 1):
        roots.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return roots


def _read_pointer(path):
    """读指针文件（对齐 gate.py:264：utf-8-sig + strip）。读不出来 ⇒ None。"""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return fh.read().strip()
    except Exception:
        return None


def _pointer_pick(pointer_path=None):
    """对齐 gate.py:253-270 `_pointer_pick()`。返回 (指针文件路径|None, 内容|None)。

    ★ 规则：多级 root 里**第一个有内容**的指针文件；
      内容为空 ⇒ 与「没写指针」同义（**继续**往上一级找，**不停下**）—— 与 gate.py 逐字一致。
    ★ `pointer_path` 是判据造用例的口子（只查这一个文件，不上探）。
    """
    if pointer_path:
        if not os.path.isfile(pointer_path):
            return pointer_path, None
        return pointer_path, _read_pointer(pointer_path)
    for root in _project_roots():
        ptr = os.path.join(root, "build", "local_legacy_home.txt")
        if not os.path.isfile(ptr):
            continue
        val = _read_pointer(ptr)
        if val:
            return ptr, val
    return None, None


def _stamp_satisfies(stamp, sha256):
    """对齐 gate.py:168-182 `_stamp_satisfies()`。

    版本戳必须①声明全部所需能力 ②**锚住当前 gate.py 的 sha256**（旧文件配新戳不算数）。
    """
    if not isinstance(stamp, dict) or not sha256:
        return False
    if str(stamp.get("gate_sha256", "")).strip().lower() != sha256.lower():
        return False
    caps = stamp.get("caps")
    if not isinstance(caps, list):
        return False
    return all(c in caps for c in LEGACY_REQUIRED)


def _legacy_gate_report(home):
    """对齐 gate.py:184-240 `legacy_gate_report()`（只读、永不抛异常）。"""
    rep = {"home": home, "gate_py": None, "exists": False, "size": 0, "sha256": None,
           "sha256_16": None, "caps": {}, "missing": [], "ok": False, "via": "", "why": ""}
    if not home:
        rep["why"] = "没有 home 目录"
        return rep
    gp = os.path.join(home, LEGACY_GATE_NAME)
    rep["gate_py"] = gp
    if not os.path.isfile(gp):
        rep["why"] = "目录里没有 %s" % LEGACY_GATE_NAME
        return rep
    rep["exists"] = True
    try:
        with open(gp, "rb") as fh:
            raw = fh.read()
    except Exception as e:
        rep["why"] = "读不了 %s：%s: %s" % (gp, type(e).__name__, e)
        return rep
    rep["size"] = len(raw)
    rep["sha256"] = hashlib.sha256(raw).hexdigest()
    rep["sha256_16"] = rep["sha256"][:16]
    text = raw.decode("utf-8", "replace")
    rep["caps"] = {n: bool(re.search(p, text)) for n, p in LEGACY_CAPS.items()}
    rep["missing"] = [c for c in LEGACY_REQUIRED if not rep["caps"].get(c)]
    if not rep["missing"]:
        rep["ok"] = True
        rep["via"] = "code-probe"
        return rep
    # 代码实测不过 → 再看可选版本戳（且必须锚住当前字节）
    vp = os.path.join(home, LEGACY_VERSION_FILE)
    if os.path.isfile(vp):
        try:
            with open(vp, encoding="utf-8") as fh:
                stamp = json.load(fh)
        except Exception:
            stamp = None
        if _stamp_satisfies(stamp, rep["sha256"]):
            rep["ok"] = True
            rep["via"] = "version-stamp(sha256-anchored)"
            rep["missing"] = []
            return rep
    rep["why"] = ("缺少能力 [%s]（%d B / sha256 %s…）"
                  % ("、".join(rep["missing"]), rep["size"], rep["sha256_16"]))
    return rep


def _alignment_check():
    """静态对齐自检：把 gate.py **源码文本**里的口径常量抠出来，与本文件的抄写逐条比。

    ★ 为什么必须有它：本文件按要求**不许 import gate.py** ⇒ 只能逐条抄写 ⇒ 抄写就会漂。
      这一层把「漂了」变成**看得见的红**，而不是等下一次真漂移才发现。
    ★ 读不到 / 抠不出来 ⇒ 只记 note，**不判红**（不许因为解析不出来就误伤）。
    """
    out = {"gate_py": GATE_PY, "checked": False, "drift": False, "diff": [],
           "local": {"probe": LEGACY_PROBE, "gate_name": LEGACY_GATE_NAME,
                     "version_file": LEGACY_VERSION_FILE,
                     "required": list(LEGACY_REQUIRED), "caps": dict(LEGACY_CAPS)},
           "remote": {}, "note": ""}
    if not os.path.isfile(GATE_PY):
        out["note"] = "读不到 %s ⇒ 无法静态核对（不判红）" % GATE_PY
        return out
    try:
        with open(GATE_PY, encoding="utf-8") as fh:
            src = fh.read()
    except Exception as e:
        out["note"] = "读不了 %s：%s（不判红）" % (GATE_PY, e)
        return out
    remote = {}
    m = re.search(r"_LEGACY_PROBE\s*=\s*(\d+)", src)
    if m:
        remote["probe"] = int(m.group(1))
    m = re.search(r"_LEGACY_GATE_NAME\s*=\s*\"([^\"]+)\"", src)
    if m:
        remote["gate_name"] = m.group(1)
    m = re.search(r"_LEGACY_VERSION_FILE\s*=\s*\"([^\"]+)\"", src)
    if m:
        remote["version_file"] = m.group(1)
    m = re.search(r"_LEGACY_REQUIRED\s*=\s*\(([^)]*)\)", src)
    if m:
        remote["required"] = [x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()]
    m = re.search(r"_LEGACY_CAPS\s*=\s*\{(.*?)\}", src, re.S)
    if m:
        caps = {}
        for name, pat in re.findall(r"\"([^\"]+)\"\s*:\s*r?\"(.*?)\"\s*,?\s*$", m.group(1), re.M):
            caps[name] = pat
        if caps:
            remote["caps"] = caps
    out["remote"] = remote
    if not remote:
        out["note"] = ("从 %s 抠不出口径常量（那边格式变了）⇒ 不判红，但请人核一眼"
                       % GATE_PY)
        return out
    out["checked"] = True
    diff = []
    for key in ("probe", "gate_name", "version_file", "required", "caps"):
        if key in remote and remote[key] != out["local"][key]:
            diff.append("%s：gate.py=%r / 本文件=%r" % (key, remote[key], out["local"][key]))
    out["diff"] = diff
    out["drift"] = bool(diff)
    if diff:
        out["note"] = ("★ 口径漂移：gate.py 与本文件的抄写对不上（%s）—— 闸的定位口径已经不可信"
                       % "；".join(diff))
    else:
        out["note"] = "口径常量与 gate.py 逐条一致（probe/gate_name/version_file/required/caps）"
    return out


def resolve_live(live_override=None, pointer_path=None, env_home=None):
    r"""定位 live —— 与 `src\agentctl\gate.py` 的 `_resolve_home_detailed()` 逐条对齐。

    返回 (live_dir|None, layer, detail)。
      · live_dir = **闸要比的那份**（= 运行时选中的那份；`--live` 覆盖时 = 覆盖值）
      · layer    ∈ {"cli", "env", "meipass", "pointer", "source-tree", "none"}
      · detail   = 定位证据 + 冲突信息 + 对齐自检结果
    """
    detail = {
        "layer": None,               # 运行时选中的那一层
        "compare_layer": None,       # 闸比的是哪一层（--live 时 = "cli"）
        "runtime_live": None,        # 运行时真正在用的那份
        "compared_live": None,       # 闸要比的那份
        "pointer_file": None,
        "pointer_value": None,
        "pointer_resolved": None,
        "env_home": env_home or None,
        "env_resolved": None,
        "meipass": None,
        "source_tree_candidates": [],
        "rejected": [],
        "hard_stop": False,
        "conflict": False,
        "conflict_paths": {},
        "conflict_reason": "",
        "alignment": _alignment_check(),
        "note": "",
    }

    # ── ① 环境变量层（gate.py:306-319）────────────────────────────────
    env = env_home or ""
    if env:
        env_abs = _abs(env)
        detail["env_home"] = env
        detail["env_resolved"] = env_abs
        if not os.path.isfile(os.path.join(env_abs, LEGACY_GATE_NAME)):
            # 目录里没有 gate.py ⇒ gate.py:312-313 豁免体检（被当状态目录用）⇒ 原样返回
            detail["runtime_live"] = env_abs
            detail["layer"] = "env"
            detail["note"] = ("环境变量 AGENTCTL_LEGACY_HOME=%s —— 目录里没有 %s ⇒ "
                              "按 gate.py:312-313 豁免体检、原样返回"
                              % (env_abs, LEGACY_GATE_NAME))
        else:
            rep = _legacy_gate_report(env_abs)
            if rep["ok"]:
                detail["runtime_live"] = env_abs
                detail["layer"] = "env"
                detail["note"] = ("环境变量 AGENTCTL_LEGACY_HOME=%s —— 体检合格（%s）"
                                  % (env_abs, rep["via"]))
            else:
                # 声明层硬停：不合格 ⇒ 报错，**不回落**（gate.py:316-319）
                rep["layer"] = "env"
                detail["rejected"].append(rep)
                detail["hard_stop"] = True
                detail["note"] = ("环境变量 AGENTCTL_LEGACY_HOME=%s 体检不合格（%s）—— "
                                  "声明层硬停：**不回落**到指针/上探（gate.py:316-319）"
                                  % (env_abs, rep["why"]))

    # ── ② 冻结态 meipass 层（gate.py:321-326）────────────────────────
    if detail["runtime_live"] is None and not detail["hard_stop"]:
        meipass = getattr(sys, "_MEIPASS", None)
        detail["meipass"] = meipass
        if meipass:
            cand = os.path.join(meipass, "legacy_tools")
            if os.path.isdir(cand):
                rep = _legacy_gate_report(cand)
                if rep["ok"]:
                    detail["runtime_live"] = cand
                    detail["layer"] = "meipass"
                    detail["note"] = "冻结态 meipass\\legacy_tools（体检合格：%s）" % rep["via"]
                else:
                    rep["layer"] = "meipass"
                    detail["rejected"].append(rep)

    # ── ③ 指针层（gate.py:328-343，**声明层**）────────────────────────
    #    ★ 指针**本身**也要多级 root 上探（gate.py:253-270）——
    #      无论前面哪层命中，指针都要找出来（冲突检测要用）。
    ptr_file, ptr_val = _pointer_pick(pointer_path)
    detail["pointer_file"] = ptr_file
    detail["pointer_value"] = ptr_val
    if ptr_val:
        p = _abs(ptr_val)
        detail["pointer_resolved"] = p
        if detail["runtime_live"] is None and not detail["hard_stop"]:
            if not os.path.isdir(p):
                detail["rejected"].append({
                    "home": p, "layer": "pointer", "ok": False,
                    "why": "指针文件 %s 写的目录不存在（%s）—— 声明层失效；**不回落**"
                           % (ptr_file, p)})
                detail["hard_stop"] = True
                detail["note"] = ("指针 %s → %s（目录不存在）—— 声明层硬停：**不回落**"
                                   "（gate.py:331-338）" % (ptr_file, p))
            else:
                rep = _legacy_gate_report(p)
                if rep["ok"]:
                    detail["runtime_live"] = p
                    detail["layer"] = "pointer"
                    detail["note"] = ("指针 %s → %s（体检合格：%s）"
                                      % (ptr_file, p, rep["via"]))
                else:
                    rep["layer"] = "pointer"
                    detail["rejected"].append(rep)
                    detail["hard_stop"] = True
                    detail["note"] = ("指针 %s → %s 体检不合格（%s）—— 声明层硬停：**不回落**"
                                       "（gate.py:342-343）" % (ptr_file, p, rep["why"]))

    # ── ④ 源码态上探层（gate.py:345-349）─────────────────────────────
    if detail["runtime_live"] is None and not detail["hard_stop"]:
        for root in _project_roots():
            cand = os.path.join(root, "legacy_tools")
            entry = {"root": root, "candidate": cand, "verdict": "跳过", "why": ""}
            detail["source_tree_candidates"].append(entry)
            if not os.path.isdir(cand):
                entry["why"] = "目录不在"
                continue
            rep = _legacy_gate_report(cand)
            if rep["ok"]:
                entry["verdict"] = "命中"
                entry["why"] = "体检合格（%s）" % rep["via"]
                detail["runtime_live"] = cand
                detail["layer"] = "source-tree"
                detail["note"] = "源码态上探命中 %s（体检合格：%s）" % (cand, rep["via"])
                break
            entry["verdict"] = "不合格"
            entry["why"] = rep["why"]
            rep["layer"] = "source-tree"
            detail["rejected"].append(rep)

    if detail["layer"] is None:
        detail["layer"] = "none"
        detail["note"] = detail["note"] or (
            "AGENTCTL_LEGACY_HOME 未设、指针没有内容、源码态上探也没有合格目录"
            "（gate.py:351-352：找不到就是不猜）")

    # ── 冲突检测（★ 本闸新增；闸的意义就是「比的就是它跑的那份」）────
    #  ① 环境变量 vs 指针：同时存在且指向不同目录 ⇒ 运行时用 env 那份，
    #    「按指针比」比的是另一份 ⇒ 闸形同虚设 ⇒ 报 conflict + 两条路径 + 判红。
    if detail["env_resolved"] and detail["pointer_resolved"] \
            and not _same_dir(detail["env_resolved"], detail["pointer_resolved"]):
        detail["conflict"] = True
        detail["conflict_paths"] = {
            "runtime_live": detail["runtime_live"],
            "env_live": detail["env_resolved"],
            "pointer_live": detail["pointer_resolved"],
            "pointer_file": detail["pointer_file"],
        }
        detail["conflict_reason"] = (
            "AGENTCTL_LEGACY_HOME=%s 与指针 %s=%s 指向**不同目录** —— 运行时"
            "（gate.py:306-319 环境变量优先）跑的是前者，按指针比比的是后者，"
            "**不是同一份**" % (detail["env_resolved"], detail["pointer_file"],
                              detail["pointer_resolved"]))

    # ── 比哪一份：`--live` 是判据造用例的口子（显式覆盖）────────────
    if live_override:
        detail["compared_live"] = _abs(live_override)
        detail["compare_layer"] = "cli"
        detail["note"] = ("命令行显式指定（--live %s）" % detail["compared_live"])
        if detail["runtime_live"] and not _same_dir(detail["compared_live"],
                                                    detail["runtime_live"]):
            detail["conflict"] = True
            detail["conflict_paths"].update({
                "runtime_live": detail["runtime_live"],
                "compared_live": detail["compared_live"],
            })
            detail["conflict_reason"] = (
                "闸比的目录（--live %s）与运行时选中的目录（%s）**不是同一份**"
                % (detail["compared_live"], detail["runtime_live"]))
    else:
        detail["compared_live"] = detail["runtime_live"]
        detail["compare_layer"] = detail["layer"]

    return detail["compared_live"], detail["compare_layer"], detail


# ────────────────────────── 比对主体 ──────────────────────────

def compare(gen, live_dir, product_dir):
    """逐文件比对 live ↔ 仓内产物。返回报告 dict（不含退出码）。

    ★ 口径：`gen.md5n`（去 BOM、CRLF/CR→LF）—— 与台账 A₀ / CHK-A 同一口径。
    ★ 登记清单 `gen.FILES` 是「必须一致」的**唯一依据**（谁是产品文件只有这一处定义）。
    """
    files_l = list_files(live_dir)
    files_p = list_files(product_dir)
    registered = list(getattr(gen, "FILES", []) or [])

    consistent = []
    inconsistent = []
    live_missing = []      # 产物有、live 没有 ⇒ live 落后
    product_missing = []    # live 有、产物没有 ⇒ 产物落后
    both_missing = []      # 两边都没有（登记了但没生成）⇒ 产品侧问题，**不判红**

    for name in registered:
        pl = os.path.join(live_dir, name)
        pp = os.path.join(product_dir, name)
        in_l = os.path.isfile(pl)
        in_p = os.path.isfile(pp)
        if in_l and in_p:
            try:
                ml = gen.md5n(open(pl, "rb").read())
            except OSError as e:
                ml = None
                inconsistent.append({"file": name, "live_path": pl, "product_path": pp,
                                     "live_md5": None, "product_md5": None,
                                     "reason": "live 读不出来：%s" % e})
                continue
            try:
                mp = gen.md5n(open(pp, "rb").read())
            except OSError as e:
                mp = None
                inconsistent.append({"file": name, "live_path": pl, "product_path": pp,
                                     "live_md5": ml, "product_md5": None,
                                     "reason": "仓内产物读不出来：%s" % e})
                continue
            if ml == mp:
                consistent.append({"file": name, "live_path": pl, "product_path": pp,
                                   "live_md5": ml, "product_md5": mp})
            else:
                inconsistent.append({"file": name, "live_path": pl, "product_path": pp,
                                     "live_md5": ml, "product_md5": mp,
                                     "reason": "内容不一致（live ≠ 仓内产物）"})
        elif in_p:
            live_missing.append({"file": name, "live_path": pl, "product_path": pp,
                                 "live_md5": None, "product_md5": None,
                                 "reason": "live 缺件（仓内产物有、live 没有）⇒ live 落后了"})
        elif in_l:
            product_missing.append({"file": name, "live_path": pl, "product_path": pp,
                                    "live_md5": None, "product_md5": None,
                                    "reason": "仓内产物缺件（live 有、产物没有）⇒ 产物落后了"})
        else:
            both_missing.append(name)

    reg = set(registered)
    live_only = [{"name": n, "path": files_l[n], "verdict": "忽略",
                  "why": classify_extra(n)} for n in sorted(files_l) if n not in reg]
    product_only = [{"name": n, "path": files_p[n], "verdict": "忽略",
                     "why": classify_extra(n)} for n in sorted(files_p) if n not in reg]

    red = len(inconsistent) + len(live_missing) + len(product_missing)
    return {
        "live_dir": os.path.abspath(live_dir),
        "product_dir": os.path.abspath(product_dir),
        "norm": "gen_legacy.md5n（去 BOM、CRLF/CR→LF）—— 与双基线台账 A₀ 同一口径",
        "registered_count": len(registered),
        "live_dirs_ignored": list_dirs(live_dir),
        "product_dirs_ignored": list_dirs(product_dir),
        "counts": {
            "consistent": len(consistent),
            "inconsistent": len(inconsistent),
            "live_missing": len(live_missing),
            "product_missing": len(product_missing),
            "both_missing": len(both_missing),
            "live_only": len(live_only),
            "product_only": len(product_only),
            "red": red,
        },
        "consistent": consistent,
        "inconsistent": inconsistent,
        "live_missing": live_missing,
        "product_missing": product_missing,
        "both_missing": both_missing,
        "live_only": live_only,
        "product_only": product_only,
    }


# ────────────────────────── 注入式自证 ──────────────────────────

def do_inject(gen, live_dir, product_dir):
    r"""把 live **复制**到临时目录并改坏一个字节 ⇒ 闸必须变红并点名那个文件。

    ★ live 本体一个字节都不动（复制到 %TEMP% 再改副本）—— 这条闸不许改 live。
    ★ 目标文件：优先 act.py（与 `tests\_legacy台账对账.py` 同款约定），否则取登记清单里第一个。
    """
    tmp = tempfile.mkdtemp(prefix="agentctl_live_sync_inject_")
    try:
        dst = os.path.join(tmp, "live")
        os.makedirs(dst, exist_ok=True)
        for name, src in list_files(live_dir).items():
            shutil.copy2(src, os.path.join(dst, name))
        target = None
        for cand in ("act.py",):
            if os.path.isfile(os.path.join(dst, cand)):
                target = cand
                break
        if target is None:
            for name in (getattr(gen, "FILES", []) or []):
                if os.path.isfile(os.path.join(dst, name)):
                    target = name
                    break
        if target is None:
            return EXIT_NOLIVE, {"error": "live 副本里找不到任何登记文件 ⇒ 注入自证做不了",
                                  "injected": None}
        p = os.path.join(dst, target)
        try:
            os.chmod(p, 0o666)
        except OSError:
            pass
        with open(p, "ab") as fh:
            fh.write(b"\n# injected-defect\n")
        rep = compare(gen, dst, product_dir)
        rep["injected"] = {"file": target, "temp_live": dst,
                           "note": "改的是**副本**，live 本体未动"}
        names = [x["file"] for x in rep["inconsistent"]]
        rep["inject_caught"] = target in names
        return (EXIT_DRIFT if rep["inject_caught"] else EXIT_OK), rep
    finally:
        # ★ 必须先去掉只读再删：live 的文件是**只读**的（`-ar--`），
        #   shutil.copy2 会把只读属性一起复制过来 ⇒ Windows 上 rmtree 直接 PermissionError。
        #   第一版就是因此把临时目录留在了 %TEMP%（实测：inject 跑完目录还在）。
        #   ⇒ 先 chmod 放开只读，再删；删不掉也不许吞掉（这里 print 出来，不假装删了）。
        for dp, _dns, fns in os.walk(tmp):
            for fn in fns:
                try:
                    os.chmod(os.path.join(dp, fn), 0o666)
                except OSError:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.isdir(tmp):
            print("[!] 临时副本删不掉（残留 %s）—— 请手工清掉。" % tmp)


# ────────────────────────── 人读输出 ──────────────────────────

def print_report(rep, live_info, injected=None):
    print("=" * 78)
    print("live 漂移自动闸 · live ↔ 仓内产物")
    print("=" * 78)
    print("live    ：%s" % rep.get("live_dir", live_info.get("layer")))
    print("定位层  ：%s（%s）" % (live_info.get("compare_layer") or live_info.get("layer"),
                                  live_info.get("note") or ""))
    if live_info.get("runtime_live"):
        print("运行时  ：%s（gate.py 选中的那一层：%s）"
              % (live_info["runtime_live"], live_info.get("layer")))
    if live_info.get("pointer_file"):
        print("指针    ：%s%s"
              % (live_info["pointer_file"],
                 (" = %s" % live_info["pointer_value"]) if live_info.get("pointer_value")
                 else "（无内容 ⇒ 等于没写指针）"))
    if live_info.get("env_home"):
        print("环境变量：AGENTCTL_LEGACY_HOME=%s" % live_info["env_home"])
    al = live_info.get("alignment") or {}
    if al.get("note"):
        print("口径自检：%s" % al["note"])
    if live_info.get("conflict"):
        cp = live_info.get("conflict_paths") or {}
        print("★ 冲突（判红）：%s" % live_info.get("conflict_reason"))
        print("         运行时在用：%s" % (cp.get("runtime_live")
                                          or live_info.get("runtime_live") or "（运行时也定位不到）"))
        if cp.get("env_live"):
            print("         环境变量  ：%s" % cp["env_live"])
        if cp.get("pointer_live"):
            print("         指针      ：%s" % cp["pointer_live"])
        if cp.get("compared_live"):
            print("         闸比的    ：%s" % cp["compared_live"])
        print("         ⇒ 到底哪份在跑存疑 ⇒ 判红（退出码 1），**不藏**。")
    print("仓内产物：%s" % rep.get("product_dir"))
    print("口径    ：%s" % rep.get("norm"))
    if injected:
        print("注入    ：%s（改的是副本：%s）" % (injected.get("file"), injected.get("temp_live")))
    c = rep["counts"]
    print()
    print("--- 必须一致（登记产品文件 %d 个）---" % rep["registered_count"])
    print("一致 %d 个 / 不一致 %d 个 / live 缺件 %d 个 / 产物缺件 %d 个"
          % (c["consistent"], c["inconsistent"], c["live_missing"], c["product_missing"]))
    for kind, key in (("红", "inconsistent"), ("红", "live_missing"), ("红", "product_missing")):
        for item in rep[key]:
            print("  [%s] %s" % (kind, item["file"]))
            print("       live ：%s" % item["live_path"])
            print("       产物 ：%s" % item["product_path"])
            print("       live md5 = %s" % (item.get("live_md5") or "N/A"))
            print("       产物 md5 = %s" % (item.get("product_md5") or "N/A"))
            print("       原因：%s" % item["reason"])
    if rep.get("both_missing"):
        print("  [黄] 两边都没有（登记了但没生成）：%s" % "、".join(rep["both_missing"]))
    if rep.get("live_dirs_ignored"):
        print("  忽略的 live 子目录：%s（运行数据/缓存，永不比对）" % "、".join(rep["live_dirs_ignored"]))
    if rep.get("product_dirs_ignored"):
        print("  忽略的产物子目录：%s" % "、".join(rep["product_dirs_ignored"]))

    for label, key in (("live 独有", "live_only"), ("仓内产物独有", "product_only")):
        items = rep.get(key) or []
        print()
        print("--- %s（%d 个；不在登记清单里 ⇒ 不判红，逐条给判断）---" % (label, len(items)))
        groups = {}
        for it in items:
            groups.setdefault(it["why"], []).append(it["name"])
        for why in sorted(groups):
            names = groups[why]
            print("  [忽略] %s：%d 个" % (why, len(names)))
            for n in names:
                print("      %s" % n)
    print()
    if live_info.get("conflict"):
        print("【红】★ 定位冲突（见上）—— 闸比的**不是**运行时在跑的那份 ⇒ 判红。")
    if c["red"]:
        print("【红】live 与仓内产物有 %d 处不一致 —— 仓里看不出来、运行行为却已经漂了。" % c["red"])
    elif not live_info.get("conflict"):
        print("【绿】live 与仓内产物逐字节一致（%d/%d 个登记文件）。" % (c["consistent"], rep["registered_count"]))


# ────────────────────────── 主流程 ──────────────────────────

def run(live=None, product=None, pointer=None, quiet=False, json_out=False,
        inject=False, env_home=None):
    """跑一次校验。返回 (退出码, 报告dict)。

    退出码：0 一致 / 1 有红 / 2 验不了（定位不到 live、目录不存在、口径取不到）。
    """
    if env_home is None:
        env_home = os.environ.get("AGENTCTL_LEGACY_HOME") or ""

    gen = load_gen()
    if gen is None:
        rep = {"error": "口径模块取不到：%s（拿不到 gen_legacy.md5n 就无法按同一口径比）" % GEN_PATH,
               "layer": None}
        if json_out:
            print(json.dumps({"tool": "check_live_sync", "exit_code": EXIT_NOLIVE, **rep},
                             ensure_ascii=False, indent=2))
        elif not quiet:
            print("[X] 口径模块取不到：%s" % GEN_PATH)
            print("    ⇒ 拿不到 gen_legacy.md5n 就没法按同一口径比 —— **验不了**，不退回自带实现。")
        return EXIT_NOLIVE, rep

    live_dir, layer, live_info = resolve_live(live_override=live, pointer_path=pointer,
                                               env_home=env_home)
    product_dir = os.path.abspath(product or PRODUCT_DIR)
    # ★ 两条「不许藏、判红」的硬条件：
    #   ① conflict     = 环境变量与指针指向不同目录 ⇒ 比的不是运行时在跑的那份
    #   ② align_drift  = 本文件抄写的口径常量与 gate.py 对不上 ⇒ 定位口径已不可信
    conflict = bool(live_info.get("conflict"))
    align = live_info.get("alignment") or {}
    align_drift = bool(align.get("drift"))

    if live_dir is None:
        rep = {"error": "无法定位 live：%s" % live_info.get("note"), "layer": layer,
               "conflict": conflict,
               "conflict_paths": live_info.get("conflict_paths") or {},
               "conflict_reason": live_info.get("conflict_reason") or "",
               "alignment": align,
               "live_info": live_info, "product_dir": product_dir}
        rc = EXIT_DRIFT if (conflict or align_drift) else EXIT_NOLIVE
        if json_out:
            print(json.dumps({"tool": "check_live_sync", "exit_code": rc, **rep},
                             ensure_ascii=False, indent=2))
        elif not quiet:
            print("[X] 无法定位 live：%s" % live_info.get("note"))
            print("    ⇒ 与运行时（src\\agentctl\\gate.py）**同一套口径**下也没定位到。")
            print("    ⇒ **不猜**（宁可不判，也不拿一个猜出来的目录当 live）。")
            for r in live_info.get("rejected") or []:
                print("    ⇒ 被刷掉的候选 [%s]：%s —— %s"
                      % (r.get("layer"), r.get("home"), r.get("why")))
            if conflict:
                print("    ⇒ ★ 而且有**冲突**（%s）⇒ 判红。" % live_info.get("conflict_reason"))
        return rc, rep

    if not os.path.isdir(live_dir):
        rep = {"error": "live 目录不存在：%s" % live_dir, "layer": layer,
               "conflict": conflict,
               "conflict_paths": live_info.get("conflict_paths") or {},
               "conflict_reason": live_info.get("conflict_reason") or "",
               "alignment": align,
               "live_info": live_info, "product_dir": product_dir}
        rc = EXIT_DRIFT if (conflict or align_drift) else EXIT_NOLIVE
        if json_out:
            print(json.dumps({"tool": "check_live_sync", "exit_code": rc, **rep},
                             ensure_ascii=False, indent=2))
        elif not quiet:
            print("[!] live 目录不存在：%s（%s）" % (live_dir, live_info.get("note")))
            print("    ⇒ 本机没部署 live ⇒ 本闸**没有结论**（不是「一致」）。")
        return rc, rep

    if not os.path.isdir(product_dir):
        rep = {"error": "仓内产物目录不存在：%s" % product_dir, "layer": layer,
               "conflict": conflict,
               "conflict_paths": live_info.get("conflict_paths") or {},
               "conflict_reason": live_info.get("conflict_reason") or "",
               "alignment": align,
               "live_info": live_info, "product_dir": product_dir}
        rc = EXIT_DRIFT if (conflict or align_drift) else EXIT_NOLIVE
        if json_out:
            print(json.dumps({"tool": "check_live_sync", "exit_code": rc, **rep},
                             ensure_ascii=False, indent=2))
        elif not quiet:
            print("[X] 仓内产物目录不存在：%s" % product_dir)
            print("    ⇒ 没有可比的一侧 ⇒ **验不了**。")
        return rc, rep

    if inject:
        rc, rep = do_inject(gen, live_dir, product_dir)
        rep["live_info"] = live_info
        rep["conflict"] = conflict
        rep["conflict_paths"] = live_info.get("conflict_paths") or {}
        rep["conflict_reason"] = live_info.get("conflict_reason") or ""
        rep["alignment"] = align
        if conflict or align_drift:
            rc = EXIT_DRIFT
        if json_out:
            print(json.dumps({"tool": "check_live_sync", "exit_code": rc, **rep},
                             ensure_ascii=False, indent=2))
        elif not quiet:
            print_report(rep, live_info, injected=rep.get("injected"))
            if rep.get("inject_caught"):
                print()
                print("【绿·自证】注入的 %s 被点名 ⇒ 这条闸抓得住漂移。" % rep["injected"]["file"])
                print("        （改的是副本；live 本体未动）")
            else:
                print()
                print("【红·自证】注入的 %s **没有**被点名 ⇒ 这条闸是死的。" % rep.get("injected", {}).get("file"))
        return rc, rep

    rep = compare(gen, live_dir, product_dir)
    rep["live_info"] = live_info
    rep["conflict"] = conflict
    rep["conflict_paths"] = live_info.get("conflict_paths") or {}
    rep["conflict_reason"] = live_info.get("conflict_reason") or ""
    rep["alignment"] = align
    rc = EXIT_DRIFT if (rep["counts"]["red"] or conflict or align_drift) else EXIT_OK
    if json_out:
        print(json.dumps({"tool": "check_live_sync", "exit_code": rc, **rep},
                         ensure_ascii=False, indent=2))
    elif not quiet:
        print_report(rep, live_info)
    return rc, rep


def main(argv=None):
    _reconfigure_stdio()
    ap = argparse.ArgumentParser(
        description="live 漂移自动闸：live（本机真在跑的那份 legacy）↔ 仓内产物 逐字节一致性")
    ap.add_argument("--live", help="显式指定 live 目录（仅用于判据造用例）")
    ap.add_argument("--product", help="显式指定仓内产物目录（默认 %s）" % PRODUCT_DIR)
    ap.add_argument("--pointer", help="显式指定指针文件路径（仅用于判据造用例）")
    ap.add_argument("--json", action="store_true", dest="json_out",
                    help="stdout 输出 JSON（CI 消费）")
    ap.add_argument("--quiet", action="store_true", help="静默：只看退出码")
    ap.add_argument("--inject", action="store_true",
                    help="注入式跑红自证：把 live 复制到临时目录并改坏一个字节 ⇒ 期望变红")
    a = ap.parse_args(argv)
    rc, _rep = run(live=a.live, product=a.product, pointer=a.pointer,
                   quiet=a.quiet, json_out=a.json_out, inject=a.inject)
    return rc


if __name__ == "__main__":
    sys.exit(main())
