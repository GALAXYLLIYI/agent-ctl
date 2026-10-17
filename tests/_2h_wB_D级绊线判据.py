# -*- coding: utf-8 -*-
r"""P6 · 「D 级有意不解锁」机械绊线判据（窗B，2026-10-15）。

派单原话是「现在只靠人守」。本脚本把这句话拆成三条**能跑红**的断言：

  A1 契约面上没有面向 agent 的可调用拖拽工具
     —— 并且要能区分 contract/tools.json 里的【说明文字】与【可调用工具定义】。
  A2 源码里没有可被 MCP 调用的拖拽实现
     —— core.py 里那 1 处 drag 字样（范围词表字面量）显式白名单化，不假装它不存在。
  A3 拿一张带 D 的证去调动作工具 ⇒ 必须**拒绝**，不许静默降级成 C。

外加 P5 的口径钉子（P5.1-P5.8）。

★ 2026-10-15（窗D）P5.2 / P5.3 已**改钉新行为**。
  旧钉子钉的是「A 打头的证按 ACT_LIMIT['A']=200 生效」—— 那是改前的真实行为，
  但用户已裁定（P5 改）：上限按**已授予档位里最紧的一档**取，不许由 scopes[0] 的
  排列巧合决定。所以这两条改成钉「A 打头 ⇒ 20/10 生效（A 不参与取最小）」，
  并补 P5.6（真去调 ctl_authorize 发一张证，看它盖的数）、P5.7（把那张证喂进闸门
  看边界）、P5.8（源码里发证默认值不许再碰 scopes[0]）。
  旧钉子仍能跑红：拿 work\_wD_红基线_repo 当 AGENTCTL_ROOT 指过来，这几条必须红。

用法：
    python -X utf8 tests\_2h_wB_D级绊线判据.py
    AGENTCTL_ROOT=<别的一份仓> python -X utf8 tests\_2h_wB_D级绊线判据.py
       （跑红就靠这个：把 HEAD 导出、或把注入了桩的副本指过来，同一条判据原样跑）

退出码：0 = 三条断言全绿；1 = 有红（逐条打印为什么红）；2 = 判据自身跑不起来。

⚠ 本脚本**只读**：闸门状态、事件流一律重定向到临时沙箱，不碰真 auth_pass.json /
   真急停 flag / %LOCALAPPDATA%\AgentCtl。
⚠ 配色与 --thickness 冻结：判据里出现的 TIERS 颜色常量只**登记**，不改。
"""

from __future__ import annotations

import ast
import inspect
import io
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 窗M（2026-10-16）：--inject = 自证跑红。
#   从前红基线是 work\_wD_红基线_repo（不进仓 ⇒ 新机器复现不了「能跑红」）。
#   现在改成注入式：现役 src/ + contract/ 复制成**影子仓**落 %TEMP%，
#   再往影子仓的契约副本里塞一个可调用的 ctl_drag ⇒ A1 必然报红。
#   ★ 必须发生在 ROOT 计算**之前**（ROOT 一旦定下就驱动全部被测路径）。
_INJECT = "--inject" in sys.argv
if _INJECT:
    sys.path.insert(0, HERE)
    import _inject                                       # noqa: E402  本地小助手
    os.environ["AGENTCTL_ROOT"] = _inject.shadow_repo(_inject.tmpdir("_wB_注入红_"))
ROOT = os.path.abspath(os.environ.get("AGENTCTL_ROOT") or os.path.dirname(HERE))
PKG = os.path.join(ROOT, "src", "agentctl")
CONTRACT = os.path.join(ROOT, "contract", "tools.json")

if _INJECT:
    _inject.inject_drag_tool(CONTRACT)
    print("★ 自证跑红模式：被测仓 = **注入了缺陷的影子仓**（产品代码一个字节没动）")
    print("  注入内容：契约 tools 数组里塞进一个可调用的 ctl_drag ⇒ A1 必须红")

FAILS = []            # [(断言号, 为什么红)]


def ok(msg):
    print("  [绿] " + msg)


def red(aid, msg):
    FAILS.append((aid, msg))
    print("  [红] " + aid + " · " + msg)


def info(msg):
    print("  [注] " + msg)


def head(t):
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


# 「可执行」的拖拽痕迹：命中即怀疑是实现，不是词表/注释。
# ⚠ 故意**不**在 drag 前加 \b —— `_dragTo` / `do_drag` / `t_drag` 这些真名都带前缀，
#   加了 \b 就全都漏掉（实测踩过：M2 突变注入 `_dragTo` 时 A2.1 没响）。
DRAG_CODE_RE = re.compile(
    r"(?i)(drag\w*|mouse_?down|mouse_?up|mouseeventf_\w*down)")
DRAG_CN_RE = re.compile(r"拖拽")
# 工具**名**里的拖拽痕迹（不是描述 —— 描述出现「拖拽」可能只是在讲档位词表）
DRAG_NAME_RE = re.compile(r"(?i)drag|拖拽")
DRAG_TOKEN_RE = re.compile(r"(?i)['\"]drag['\"]|['\"]拖拽['\"]|\bdo_drag\b|\bdragTo\b")


# ══════════════════════════════════════════════════════════════════════
# A2.3 白名单：src/agentctl 里**每一处** drag/拖拽 字样的归属
#   （文件, 正则, 归类, 为什么它不是「MCP 能调的拖拽实现」）
#   规则：① 每处命中必须落在某一条上；② 每一条必须至少命中一处（漂移探测器）。
#         任何新增的拖拽字样没登记 ⇒ 红；登记了却已经不存在 ⇒ 也红。
# ══════════════════════════════════════════════════════════════════════
DRAG_WHITELIST = [
    ("core.py", r'"D":\s*"D级\s*拖拽"', "vocab", True,
     "SCOPES_TXT 范围词表里 D 档的中文名（core.py:490-491）。就是派单实测校正说的那 1 处 —— "
     "纯字面量，既不注册工具也不发动作。显式登记，不假装它不存在。"),
    ("gate.py", r'"D":\s*"D级\s*拖拽"', "vocab", True,
     "SCOPES 档位↔完整串映射里 D 档的中文名。同一张词表，和 core.py 那处同源。"),
    ("gate.py", r"打字\s*→\s*拖拽", "doc", True,
     "parse_scopes 的说明文字（分级累加顺序），纯 docstring。"),
    ("ctrl_border.py", r"D\s*拖拽", "frozen", True,
     "TIERS 上方的配色说明行。配色冻结（派单 §零.5）—— 本判据只登记，一个字节都不改。"),
    ("ctrl_border.py",
     r'"D":\s*\(\s*\(0x[0-9A-Fa-f]{2}\s*,\s*0x[0-9A-Fa-f]{2}\s*,\s*0x[0-9A-Fa-f]{2}\)\s*,\s*"D\s*级[·\.]拖拽"\s*\)',
     "frozen", True,
     "TIERS 里 D 档的颜色常量。配色冻结（派单 §零.5）—— 只登记。"),
    ("ctrl_border.py", r"^\s*#.*(?:拖拽|指示器骗人)", "doc", False,
     "P3（窗A）写进 decide() 说明块里的**注释**：'Debug' 被边框误判成 D 的实测反例。"
     "只登记（以 ^\\s*# 限定为注释行，不含可执行语句）。**strict=False**：这段注释归窗A 管，"
     "窗A 改写或删掉它都合法，所以它不存在时只报信息项、不判红。"),
    ("widget.py", r"^\s*drag\s*=\s*\{", "ui", True,
     "面板窗口自身的拖动状态字典（用户手动拖面板用），作用于 Tk 面板自己，"
     "不是对目标窗口的控制；widget.py 不在 core.TOOLS 里，agent 够不着。"),
    ("widget.py", r"drag\[['\"]", "ui", True,
     "同上，面板自身拖动的读/写点。"),
    ("widget.py", r"D级\s*拖拽", "vocab", True,
     "授权弹窗里的档位说明文字（给用户看 A/B/C/D 各是什么）。"),
    ("gate.py", r"D\s*只管拖拽", "doc", True,
     "窗D 新增：effective_act_limit 的 docstring 解释「为什么不是权限最大的那一档」。纯注释，不注册工具、不发动作。"
     "★ 2026-10-15 DSH 补登记（窗D 漏登记 ⇒ 绊线在自己家里红了）。"),
]


def strings_no_docstring(tree):
    """产出 AST 里的字符串字面量，跳过 docstring。"""
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(n, "body", None) or []
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add((body[0].value.lineno, body[0].value.end_lineno))
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if any(a <= n.lineno <= b for a, b in docs):
                continue
            yield n.lineno, n.value


def handler_drag_markers(fn):
    """一个 handler 的**代码**里有没有拖拽痕迹（注释与 docstring 不算）。"""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return []
    code = getattr(fn, "__code__", None)
    start = code.co_firstlineno if code else 1
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if nm and DRAG_CODE_RE.search(nm):
                hits.append(("调用", start + node.lineno, nm))
        elif isinstance(node, ast.Name) and DRAG_CODE_RE.search(node.id):
            hits.append(("标识符", start + node.lineno, node.id))
        elif isinstance(node, ast.arg) and DRAG_CODE_RE.search(node.arg):
            hits.append(("形参", start + node.lineno, node.arg))
    for lineno, val in strings_no_docstring(tree):
        if DRAG_TOKEN_RE.search(val) or DRAG_CN_RE.search(val):
            hits.append(("字符串", start + lineno, val[:60]))
    return hits


# ══════════════════════════════════════════════════════════════════════
# 起沙箱 + 导入
# ══════════════════════════════════════════════════════════════════════
SB = tempfile.mkdtemp(prefix="agentctl_p6_")
os.environ["AGENTCTL_APP_DIR"] = os.path.join(SB, "app")
os.environ["AGENTCTL_TIMELINE"] = os.path.join(SB, "timeline")
os.environ["AGENTCTL_REQUIRE_LEASE"] = "0"      # 只验闸门那一层，不掺租约
os.makedirs(os.path.join(SB, "auth"), exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from agentctl import core, gate                    # noqa: E402
except Exception as e:                                # pragma: no cover
    print("判据自身跑不起来：import agentctl 失败 —— %s: %s" % (type(e).__name__, e))
    print("ROOT = %s" % ROOT)
    raise SystemExit(2)

# 把闸门所有落盘点指进沙箱 —— 不碰真 auth_pass.json / 真急停 flag
gate.PASSFILE = os.path.join(SB, "auth", "auth_pass.json")
gate.LOCKFILE = os.path.join(SB, "auth", "gate.lock")
gate.LEASE_DB = os.path.join(SB, "auth", "lease.json")
gate.CAPTURE_DB = os.path.join(SB, "auth", "capture.json")
gate.LOG = os.path.join(SB, "auth", "gate.log")
gate.BRAKE_FLAG = os.path.join(SB, "_急停.flag")


def permit(scopes, acts=0, limit=None):
    p = {"scopes": list(scopes), "scope": scopes[0], "consumed": False,
         "expires_at": time.time() + 900, "acts": acts, "issued_at": time.time(),
         "expires": "2099-01-01 00:00:00"}
    if limit is not None:
        p["acts_limit"] = limit
    gate._save(gate.PASSFILE, p)
    return gate.PASSFILE


print("D 级绊线判据 · %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
print("ROOT    = %s" % ROOT)
print("沙箱     = %s（真闸门状态 / 真急停一律不碰）" % SB)


# ══════════════════════════════════════════════════════════════════════
# A1 · 契约面
# ══════════════════════════════════════════════════════════════════════
head("A1 · 契约面上没有面向 agent 的可调用拖拽工具")

spec = json.loads(read(CONTRACT))
declared = [t.get("name", "") for t in spec["tools"]]

# 「说明」与「工具」的分界就在这里：tools 是**数组**（MCP tools/list 的声明面），
# 其余顶层键是散文说明（notes / conventions / discipline），结构上就不可调用。
drags_in_tools = [n for n in declared if DRAG_NAME_RE.search(n)]
if drags_in_tools:
    red("A1.1", "contract/tools.json 的 tools 数组里出现拖拽工具：%s" % drags_in_tools)
else:
    ok("A1.1 tools 数组 %d 个工具，无一个名字带 drag/拖拽" % len(declared))

drags_in_dispatch = [k for k in core.TOOLS if DRAG_NAME_RE.search(k)]
if drags_in_dispatch:
    red("A1.2", "core.TOOLS（dispatch() 唯一认的键）里出现拖拽工具：%s" % drags_in_dispatch)
else:
    ok("A1.2 core.TOOLS %d 个键，无一个名字带 drag/拖拽" % len(core.TOOLS))

tl = core.tool_list()
drags_in_toollist = [t.get("name") for t in tl if DRAG_NAME_RE.search(t.get("name", ""))]
if drags_in_toollist:
    red("A1.3", "MCP tools/list 的真实输出里出现拖拽工具：%s" % drags_in_toollist)
else:
    ok("A1.3 core.tool_list()（tools/list 真输出）%d 项，无拖拽工具" % len(tl))


# A1.4 · 说明文字的去向：把散落在非 tools 区的 drag 说明逐条登记，
#      并**核对它们结构上确实不在可调用面**。这一步是「能区分说明与工具」的实证。
def walk_drag_notes(node, path=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += walk_drag_notes(v, path + "/" + str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += walk_drag_notes(v, path + "/%d" % i)
    elif isinstance(node, str) and DRAG_NAME_RE.search(node):
        out.append((path, node))
    return out


# 全契约扫一遍 drag 字样（含 tools 数组），再按**所在区**分类 ——
# 这就是「能区分说明与工具」的那一刀：tools 是数组（声明面），其余是散文（不可调用）。
all_notes = walk_drag_notes(spec)
ctl_drag_paths = [p for p, s in all_notes
                  if p.rstrip("/").split("/")[-1] == "ctl_drag" or s.strip() == "ctl_drag"]
in_tools = [p for p in ctl_drag_paths if p.strip("/").split("/")[0] == "tools"]
out_tools = [p for p in ctl_drag_paths if p.strip("/").split("/")[0] != "tools"]
if in_tools:
    red("A1.4", "ctl_drag 的条目混进了 tools 数组（= 变成了可调用工具）：%s" % in_tools)
if not in_tools and out_tools:
    for p in out_tools:
        info("A1.4 ctl_drag 的说明落在【非 tools 区】%s —— 结构上不可调用，这是对的" % p)
    ok("A1.4 ctl_drag 只有说明文字（%d 处，全在 tools 数组之外）" % len(out_tools))
if not ctl_drag_paths:
    ok("A1.4 契约里已无 ctl_drag 的任何条目（既没工具定义，也没说明文字）")

# A1.5 · 说明与实现必须同向：说明里说「不向 agent 暴露」时，结构上就得真的不可调用。
claims_hidden = [s for _p, s in all_notes if ("不向" in s and "agent" in s and "暴露" in s)]
if claims_hidden and not (drags_in_tools or drags_in_dispatch or drags_in_toollist):
    ok("A1.5 说明里「不向 agent 暴露」这句，与结构上的不可调用一致（%d 处声明）"
       % len(claims_hidden))
elif claims_hidden:
    red("A1.5", "说明说「不向 agent 暴露」，结构上却出现了可调用的拖拽工具")
else:
    info("A1.5 契约里已无「不向 agent 暴露」这类措辞，跳过一致性核对")

# A1.6 · HTTP 是**第三个入口**，前三条只盯了契约/dispatch/tools-list，漏了它。
#   server.ROUTES 把 URL 映射到 ctl_* 工具；只要有一条路由指向非 TOOLS 的东西
#   （比如直接指老脚本的 do_drag），就能绕过前面所有断言。必须显式钉住。
try:
    from agentctl import server as _server                    # noqa: E402
    routes = dict(getattr(_server, "ROUTES", {}) or {})
except Exception as e:
    routes = {}
    info("A1.6 读 server.ROUTES 失败（%s: %s）—— 本条跳过，不判红" % (type(e).__name__, e))
if routes:
    drags_in_routes = [(u, t) for u, t in routes.items()
                       if DRAG_NAME_RE.search(u) or DRAG_NAME_RE.search(str(t))]
    off_tools = [(u, t) for u, t in routes.items() if t not in core.TOOLS]
    if drags_in_routes:
        red("A1.6", "server.ROUTES 里出现指向拖拽的路由（HTTP 入口漏出去了）：%s" % drags_in_routes)
    elif off_tools:
        red("A1.6", "server.ROUTES 里有指向 core.TOOLS 之外的目标的路由（绕过 dispatch）：%s"
            % off_tools)
    else:
        ok("A1.6 server.ROUTES %d 条路由，全部指向 core.TOOLS 里的 ctl_* 工具，无拖拽入口"
           % len(routes))


# ══════════════════════════════════════════════════════════════════════
# A2 · 源码面
# ══════════════════════════════════════════════════════════════════════
head("A2 · 源码里没有可被 MCP 调用的拖拽实现")

# A2.1 · core.TOOLS 每个 handler 的代码里 0 处拖拽痕迹。
tool_hits = []
for name, fn in sorted(core.TOOLS.items()):
    for kind, lineno, what in handler_drag_markers(fn):
        tool_hits.append((name, kind, lineno, what))
if tool_hits:
    red("A2.1", "MCP 可调用的 handler 里出现拖拽痕迹：%s" % tool_hits)
else:
    ok("A2.1 %d 个 handler（core.TOOLS 的全部）代码里 0 处 drag/拖拽/mouseDown 类痕迹"
       % len(core.TOOLS))

# A2.2 · core 侧从不把 drag 动作转给 legacy act.py（act.py 确实有 do_drag，见 A2.4）。
pyfiles = sorted(f for f in os.listdir(PKG) if f.endswith(".py"))
forward = []
for f in pyfiles:
    lines = read(os.path.join(PKG, f)).splitlines()
    for i, ln in enumerate(lines):
        if "_legacy(" in ln or "_legacy_cmd(" in ln:
            window = " ".join(lines[i:i + 4])
            if DRAG_TOKEN_RE.search(window):
                forward.append((f, i + 1, ln.strip()[:90]))
        if re.search(r"['\"]drag['\"]|['\"]拖拽['\"]", ln):
            forward.append((f, i + 1, "独立 drag 动作串：" + ln.strip()[:90]))
if forward:
    red("A2.2", "src/agentctl 里出现可转给老脚本的 drag 动作串：%s" % forward)
else:
    ok("A2.2 src/agentctl 没有任何地方把 drag/拖拽 当动作串传给 legacy 脚本（扫了 %d 个 .py）"
       % len(pyfiles))

# A2.3 · 全包 drag/拖拽 字样逐条对白名单（顺带当漂移探测器）。
hit_map = []
for f in pyfiles:
    for i, ln in enumerate(read(os.path.join(PKG, f)).splitlines(), 1):
        if "drag" in ln or "拖拽" in ln:
            hit_map.append((f, i, ln))
unmatched = [(f, i, ln.strip()[:100]) for f, i, ln in hit_map
             if not any(wf == f and re.search(rx, ln) for wf, rx, _k, _st, _w in DRAG_WHITELIST)]
dead_strict = [(wf, rx, kind) for wf, rx, kind, strict, _why in DRAG_WHITELIST if strict
               and not any(hf == wf and re.search(rx, ln) for hf, _hl, ln in hit_map)]
dead_soft = [(wf, rx, kind) for wf, rx, kind, strict, _why in DRAG_WHITELIST if not strict
             and not any(hf == wf and re.search(rx, ln) for hf, _hl, ln in hit_map)]
if unmatched:
    red("A2.3", "以下 drag/拖拽 字样不在白名单里（= 新出现的拖拽面）：%s" % unmatched)
if dead_strict:
    red("A2.3", "白名单有 %d 条失效（登记了却不存在，改代码后没同步）：%s"
        % (len(dead_strict), dead_strict))
if dead_soft:
    info("A2.3 非强制的白名单条目已失效（归别的窗管，允许消失）：%s" % dead_soft)
if not unmatched and not dead_strict:
    ok("A2.3 全包 %d 处 drag/拖拽 字样，逐条落在 %d 条白名单上（0 未登记、0 强制条目失效）"
       % (len(hit_map), len(DRAG_WHITELIST)))
    info("A2.3 白名单里 core.py 那一条 = 派单实测校正指的那 1 处")

# A2.4 · 事实登记：老脚本层**有**完整拖拽实现（MCP 够不着，但别假装没有）。
act_path = os.path.join(ROOT, "legacy_tools", "act.py")
if os.path.exists(act_path):
    act = read(act_path)
    defs = re.findall(r"^def (do_drag|drag)\(.*$", act, re.M)
    if defs:
        info("A2.4 事实：legacy_tools/act.py 里有拖拽实现 %s —— A2.2 已证明 core 侧没有任何路"
             "能把它叫起来；这里只登记，不判红" % defs)


# ══════════════════════════════════════════════════════════════════════
# A3 · 行为面
# ══════════════════════════════════════════════════════════════════════
head("A3 · 拿带 D 的证去调动作工具 ⇒ 必须拒绝，不许静默降级成 C")

# 两个「不许崩」的包装：判据跑在**任意**源码上（跑红时是注入了桩的副本），
# 万一被测代码抛异常，结论必须是「红」，不能是一段 traceback 被当成别的意思。
def gcheck(sc):
    try:
        return gate.check(sc)
    except Exception as e:
        return None, "判据调用抛异常 %s: %s" % (type(e).__name__, e), None


def gdispatch(tool, args):
    try:
        return core.dispatch(tool, args)
    except Exception as e:
        return {"ok": None, "code": None, "warning": "判据调用抛异常 %s: %s" % (type(e).__name__, e)}


# 3.1 D 级证在 gate 层是通的（否则下面所有红都只是环境坏了）
permit(["D"])
okd, whyd, coded = gcheck("D")
if okd and coded == 0:
    ok("A3.1 D 级证本身有效（check('D') 放行）—— 后面的拒绝不是因为证是坏的")
else:
    red("A3.1", "D 级证自己就过不了闸（%s / code=%s），A3.2-3.5 无意义" % (whyd, coded))

# 3.2 / 3.3 D 证不得放行 C、也不得放行 B —— 这就是「不许静默降级」
for sc, aid in (("C", "A3.2"), ("B", "A3.3")):
    permit(["D"])
    o, w, c = gcheck(sc)
    if (not o) and c == gate.CODE_UNAUTH and "范围不符" in w:
        ok("%s 只带 D 的证 check('%s') ⇒ 拒绝（code=9 范围不符）：%s" % (aid, sc, w))
    else:
        red(aid, "只带 D 的证 check('%s') 的结论不对：ok=%s code=%s why=%s"
            "（降级放行，或拒绝理由不是范围不符）" % (sc, o, c, w))

# 3.4 走真正的入口 core.dispatch：必须拒绝，且拒绝理由是范围不符
permit(["D"])
r = gdispatch("ctl_click", {"hwnd": 1, "xy": [10, 10], "actor": "p6"})
if r.get("ok") is False and int(r.get("code") or 0) == gate.CODE_UNAUTH \
        and "范围不符" in str(r.get("warning") or ""):
    ok("A3.4 dispatch('ctl_click') 带 D 证 ⇒ ok=False code=9 范围不符：%s"
       % str(r.get("warning"))[:70])
else:
    red("A3.4", "dispatch('ctl_click') 带 D 证的结论不对：ok=%s code=%s warning=%s"
        % (r.get("ok"), r.get("code"), str(r.get("warning"))[:90]))

# 3.5 拒绝不得留下「用过了」的痕迹（不是先降级放行再回头报错）
before = read(permit(["D"]))
gdispatch("ctl_click", {"hwnd": 1, "xy": [10, 10], "actor": "p6"})
after = read(gate.PASSFILE)
if before == after:
    ok("A3.5 拒绝后通行证逐字节未变（acts / consumed 都没动）⇒ 不是「先放行后补救」")
else:
    red("A3.5", "拒绝后通行证被改了：\n      改前 %s\n      改后 %s" % (before, after))

# 3.6 正控制：B+C 的证 B/C 都得放行 —— 证明 3.2/3.3 的红不是因为闸门整体瘫了
permit(["B", "C"], limit=20)
ob, wb, cb = gcheck("B")
permit(["B", "C"], limit=20)
oc, wc, cc = gcheck("C")
if ob and cb == 0 and oc and cc == 0:
    ok("A3.6 正控制：B+C 的证 check('B')/check('C') 都放行 ⇒ A3.2/3.3 的拒绝确实是范围造成的")
else:
    red("A3.6", "正控制失败：B=%s/%s C=%s/%s —— 闸门整体有问题，A3.2/3.3 的结论不可信"
        % (ob, cb, oc, cc))

# 3.7 直接喊 ctl_drag：必须是「未知工具」的拒绝，不是放行、也不是崩
permit(["A", "B", "C", "D"], limit=200)
rd = gdispatch("ctl_drag", {"hwnd": 1, "from": [0, 0], "to": [1, 1]})
if rd.get("ok") is False and int(rd.get("code") or 0) == gate.CODE_NO_CTRL \
        and "未知工具" in str(rd.get("warning") or ""):
    ok("A3.7 dispatch('ctl_drag') 即使证齐了也说「未知工具」（code=4）：%s"
       % str(rd.get("warning"))[:60])
else:
    red("A3.7", "dispatch('ctl_drag') 的结论不对：ok=%s code=%s warning=%s"
        % (rd.get("ok"), rd.get("code"), str(rd.get("warning"))[:90]))

# 3.8 「不许静默降级」的正面钉子：D 级证次数用尽后也不能变成 C 能用
dlim = gate.ACT_LIMIT.get("D", 10)
permit(["D"], acts=dlim, limit=dlim)
od2, wd2, cd2 = gcheck("D")
permit(["D"], acts=dlim, limit=dlim)
oc2, wc2, cc2 = gcheck("C")
if (not od2) and cd2 == gate.CODE_UNAUTH and (not oc2) and cc2 == gate.CODE_UNAUTH:
    ok("A3.8 D 证用满次数后：D 被拒（次数用尽）、C 仍被拒（范围不符）—— 没有出现「D 用完就当 C」")
else:
    red("A3.8", "D 证用满次数后的结论不对：D=%s/%s C=%s/%s" % (od2, cd2, oc2, cc2))


# ══════════════════════════════════════════════════════════════════════
# P5 口径钉子 · 「A 级不计数」这句话本身也要机械可验
#   （这不是 P6 的三条断言，是给 P5 那条注释配的钉子 —— 注释里的断言必须能跑，
#     否则它和「只靠人守」没区别。）
# ══════════════════════════════════════════════════════════════════════
head("P5 口径钉子 · ACT_LIMIT['A'] 到底算不算 A 级的上限")

permit(["A"], acts=99999, limit=gate.ACT_LIMIT.get("A", 0) or 99999)
o5, w5, c5 = gcheck("A")
if o5 and c5 == 0:
    ok("P5.1 A 级证 acts 远超名义值仍放行 ⇒ ACT_LIMIT['A']=%r **不是 A 级的上限**"
       "（A 级走 skip_count 早退，本来就没有上限）"
       % gate.ACT_LIMIT.get("A"))
else:
    red("P5.1", "A 级证在 acts=%s 时被拒（code=%s）—— 「A 级不计数」这句已经不成立了，"
        "gate.py ACT_LIMIT 的注释必须跟着改" % (99999, c5))

# ══════════════════════════════════════════════════════════════════════
# P5.2-P5.3 · 发证口径的**冻结快照**与**反巧合**对照（窗D 改钉）
#
#   改前这三条钉的是「A 打头的证按 200 生效」。用户裁定 P5 改之后口径是
#   「已授予档位里最紧的一档」（A 不参与取最小），这两条随之改钉新行为。
#
#   ⚠ 期望值是**写死的表**，不是拿 gate 现算的 —— 拿 gate 现算等于
#     `gate.effective_act_limit == gate.effective_act_limit`，恒真，什么都拦不住。
#     谁把口径放开（比如把 A 又塞回 min、或改成「取权限最大的那一档」），这条照红。
# ══════════════════════════════════════════════════════════════════════

# 冻结快照：scopes ⇒ 这张证该盖的 acts_limit。改口径必须先改这张表（并写回执）。
#   推导口径见 gate.effective_act_limit 的 docstring；A 不参与取最小（A 本就不计数）。
EXPECTED_ACTS_LIMIT = {
    ("A",):             20,   # 只带 A ⇒ 没有有副作用的档 ⇒ 落兜底 20。
                                  # 这不是收紧：A 的动作走 skip_count 早退，压根读不到这个数，
                                  # 而这张证上的 B/C/D 会先被「范围不符」拒掉。可观测行为零变化。
    ("B",):             20,
    ("C",):             20,
    ("D",):             10,
    ("B", "C"):        20,
    ("B", "D"):        10,   # ★ 改前这格是 20（scopes[0]=='B'）—— 本单改掉的第一格
    ("C", "D"):        10,   # ★ 改前这格是 20（scopes[0]=='C'）—— 本单改掉的第二格
    ("A", "B"):        20,   # ★ 改前是 200（A 打头）—— 派单点名的那个
    ("A", "C"):        20,   # ★ 改前是 200
    ("A", "D"):        10,   # ★ 改前是 200 —— 最能说明问题的一格：
                                  #   A 打头 + D ⇒ 应该是 D 自己的 10，不是 200 也不是 20
    ("A", "B", "C"):   20,
    ("A", "B", "D"):   10,
    ("A", "C", "D"):   10,
    ("B", "C", "D"):   10,
    ("A", "B", "C", "D"): 10,
    ("A", "Debug"):    20,   # 认不出的档不参与取最小，也不放行
}

A_LIM = gate.ACT_LIMIT.get("A")

_eff = getattr(gate, "effective_act_limit", None)
if not callable(_eff):
    red("P5.2", "gate 里没有 effective_act_limit() —— 发证口径还是按 scopes[0] 的旧算法"
        "（`ACT_LIMIT.get(short(scopes[0]), 20)`）。本单要求上限按「已授予档位里最紧的"
        "一档」取；旧算法让 A 打头的证盖上 200，是派单点名的那个缺陷")
    red("P5.3", "没有 effective_act_limit() ⇒ 反巧合对照组构造不出来，「上限是不是由 scopes[0]"
        "的排列巧合决定」这条口径无法再验")
else:
    # —— P5.2：逐格对上写死的冻结快照。期望值不从 gate 现算，所以口径一放开就红。
    p52 = []
    for scopes, want in sorted(EXPECTED_ACTS_LIMIT.items()):
        try:
            got = _eff(list(scopes))
        except Exception as e:                       # 判据不许被异常顶掉
            p52.append((scopes, want, "抛异常 %s: %s" % (type(e).__name__, e)))
            continue
        if got != want:
            p52.append((scopes, want, got))
    if p52:
        red("P5.2", "发证口径与冻结快照不符（期望值写死在判据里，不从 gate 现算）：")
        for scopes, want, got in p52:
            print("        scopes=%-22s 期望 %-4s 实得 %s"
                  % (repr(list(scopes)), want, got))
    else:
        ok("P5.2 发证口径 = 冻结快照 %d 格全对（含 [A,D]⇒10、[B,D]⇒10 这两格改前的错值）"
           % len(EXPECTED_ACTS_LIMIT))

    # —— P5.3：反巧合。要证明「上限与 scopes[0] 无关」，得同时钉住两个方向。
    #   方向一（同一 scopes[0]，不同结果）：A 打头 ⇒ 结果必须随最紧档变。
    #   方向二（不同 scopes[0]，相同结果）：A 打头与别的档打头 ⇒ 该相等时必须相等。
    #   改前算法两个方向都过不了：它对 scopes[0]=='A' 一律给 200。
    p53 = []
    same_first_diff_limit = [(("A", "B"), ("A", "D"))]
    for x, y in same_first_diff_limit:
        gx, gy = _eff(list(x)), _eff(list(y))
        ex, ey = EXPECTED_ACTS_LIMIT[x], EXPECTED_ACTS_LIMIT[y]
        if not (gx != gy and gx == ex and gy == ey):
            p53.append("同 scopes[0]='%s'：%s⇒%s（期望 %s）、%s⇒%s（期望 %s），结果%s"
                       % (x[0], list(x), gx, ex, list(y), gy, ey,
                          "相同 ⇒ 上限仍由 scopes[0] 决定" if gx == gy else "与冻结表不符"))
    diff_first_same_limit = [(("A", "B"), ("C",)), (("A", "D"), ("D",))]
    for x, y in diff_first_same_limit:
        gx, gy = _eff(list(x)), _eff(list(y))
        ex, ey = EXPECTED_ACTS_LIMIT[x], EXPECTED_ACTS_LIMIT[y]
        if not (gx == gy == ex == ey):
            p53.append("不同 scopes[0]：%s⇒%s、%s⇒%s，期望都等于 %s"
                       % (list(x), gx, list(y), gy, ex))
    if p53:
        red("P5.3", "反巧合对照不成立 —— 上限还跟 scopes[0] 的排列有关：")
        for s in p53:
            print("        %s" % s)
    else:
        ok("P5.3 反巧合：同 scopes[0] 的两格结果不同（[A,B]⇒20 / [A,D]⇒10），"
           "不同 scopes[0] 该相等时相等 ⇒ 上限不再由 scopes[0] 决定")

# P5.4 · 不变量：gate.py 注释承诺的就是这个数，所以它也得被钉住
if A_LIM == 200:
    ok("P5.4 不变量 ACT_LIMIT['A'] == 200 成立（gate.py 注释承诺的就是这个数）")
else:
    red("P5.4", "ACT_LIMIT['A'] 现在是 %r（期望 200）—— 这是口径变更：要么改注释，"
        "要么走单独决策；**别默默改数字**" % (A_LIM,))

# P5.5 · 注释不许说反话：本单就是把「A 项彻底废了」这句订正掉的，得防它漂回来。
#   ⚠ 但订正句**本身要引用那句错话**（「别写成『彻底废了』」），所以不能一刀切按字符串命中：
#     带订正/禁令标记的行是在**引用**它，不是在**主张**它 —— 放行，只登记。
STALE_CLAIM = "彻底废"
META_MARKERS = ("订正", "别", "不许", "禁", "写成", "改成", "当成")
stale_hits, stale_quoted = [], []
for fn in ("gate.py", "core.py"):
    fp = os.path.join(PKG, fn)
    if not os.path.exists(fp):
        continue
    for i, ln in enumerate(read(fp).splitlines(), 1):
        if "ACT_LIMIT" not in ln or STALE_CLAIM not in ln:
            continue
        tag = "%s:%d %s" % (fn, i, ln.strip()[:80])
        if any(m in ln for m in META_MARKERS):
            stale_quoted.append(tag)
        else:
            stale_hits.append(tag)
if stale_hits:
    red("P5.5", "源码里还留着「ACT_LIMIT[A]=200 彻底废了」这种把 A 项说成死配置的措辞"
        "（实测它还活着，见 P5.2/P5.3）：%s" % stale_hits)
else:
    ok("P5.5 源码里没有把 ACT_LIMIT['A'] 说成死配置的措辞（注释与代码同向）")
    for t in stale_quoted:
        info("P5.5 引用了旧措辞但带禁令标记（是在禁止它，不是在主张它）：%s" % t)


# ══════════════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════════════
head("汇总")
if FAILS:
    print("结论：【红】共 %d 条断言不成立" % len(FAILS))
    for aid, why in FAILS:
        print("   - %s %s" % (aid, why))
    print("（沙箱 %s 可随时删）" % SB)
    raise SystemExit(1)
print("结论：【绿】A1 / A2 / A3 三条断言全部成立 —— D 级确实没被解锁，"
      "而且这条结论现在是机械可验的。")
print("（沙箱 %s 可随时删）" % SB)
raise SystemExit(0)


