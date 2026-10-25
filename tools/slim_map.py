# -*- coding: utf-8 -*-
r"""tools/slim_map.py —— core.py 瘦身用的**只读静态分析器**（W6 / 20261025）

做什么
  · 用 ``ast`` 扫 ``src/agentctl/core.py``，不 import core、不执行任何产品代码；
  · 列出名字含 cdp|browser|webkit|chrome|devtools 的**模块级**函数/常量/变量，
    给出行号、行数、同文件引用数、调用方、被调用的其它 core 名字；
  · 列出 ``tests/``（以及 ``src/`` 里 core 之外）引用这些名字的文件；
  · 给出「随迁闭包」——候选之外、但被候选直接/间接引用的 core 名字；
  · 给出扇入/扇出排行，回答「搬的时候谁最疼」。

用法
  python -X utf8 tools\slim_map.py
  python -X utf8 tools\slim_map.py --out work\slim_map.md
  python -X utf8 tools\slim_map.py --json work\slim_map.json
  python -X utf8 tools\slim_map.py --core <别的 core.py> --tests <别的 tests 目录>

只读：本脚本不写 core/tests，只会在显式给 ``--out`` / ``--json`` 时写那一个文件。
退出码：0 = 正常产出；2 = 输入文件不存在 / 语法解析失败。
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_CORE = os.path.join(ROOT, "src", "agentctl", "core.py")
DEFAULT_TESTS = os.path.join(ROOT, "tests")
SRC_DIR = os.path.join(ROOT, "src")

# 工单口径：名字含这些词才算 CDP/browser 一族（大小写不敏感）
FAMILY_RE = re.compile(r"cdp|browser|webkit|chrome|devtools", re.I)
# 代码引用口径：整词匹配，避免 _cdp_x 命中 _cdp_xy
WORD = r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])"


def read_text(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def word_hits(text, name):
    return len(re.findall(WORD % re.escape(name), text))


def refs_in(node):
    """一个 AST 子树里出现的所有标识符（Name 载入 + Attribute 属性名）。"""
    out = set()
    if node is None:
        return out
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def kind_of(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "函数"
    if isinstance(node, ast.ClassDef):
        return "类"
    val = getattr(node, "value", None)
    if isinstance(val, ast.Constant) and isinstance(val.value, (str, int, float, bool)):
        return "常量"
    if isinstance(val, ast.Tuple):
        try:
            ast.literal_eval(val)
            return "常量"
        except Exception:
            return "变量"
    # list / dict / set 是可变模块级状态（如 _CDP_PROC = {}），一律按变量看
    return "变量"


def collect_defs(tree):
    """模块级定义：名字 -> {node, kind, lineno, end_lineno, name_nodes}。"""
    defs = {}
    for stmt in tree.body:
        nodes = []
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nodes = [stmt]
        elif isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    nodes.append((t.id, stmt))
                elif isinstance(t, (ast.Tuple, ast.List)):
                    for e in t.elts:
                        if isinstance(e, ast.Name):
                            nodes.append((e.id, stmt))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            nodes.append((stmt.target.id, stmt))
        for item in nodes:
            name, owner = item if isinstance(item, tuple) else (item.name, item)
            defs[name] = {
                "name": name,
                "node": owner,
                "kind": kind_of(owner),
                "lineno": owner.lineno,
                "end_lineno": getattr(owner, "end_lineno", owner.lineno),
            }
    return defs


def collect_refs(tree):
    """每个模块级定义引用了哪些其它模块级名字（粗粒度：整棵子树）。"""
    defs = collect_defs(tree)
    names = set(defs)
    per_def = {}
    for name, d in defs.items():
        per_def[name] = sorted((refs_in(d["node"]) & names) - {name})
    return defs, per_def


def scan_files(root, names, skip=None, mode="grep"):
    """递归扫 .py，返回 名字 -> [(相对路径, 次数)]。

    mode="grep"：整词文本计数（判据里连 monkeypatch 的赋值也算，符合工单口径）；
    mode="ast" ：只算代码里的 Name(Load)/Attribute，注释与文档串不算（src 侧更准）。
    """
    hits = {n: [] for n in names}
    if not os.path.isdir(root):
        return hits
    for dp, dns, fns in os.walk(root):
        if "__pycache__" in dp or ".git" in dp:
            continue
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dp, fn)
            if skip and os.path.abspath(p) == os.path.abspath(skip):
                continue
            s = read_text(p)
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            code_refs = None
            if mode == "ast":
                try:
                    t = ast.parse(s, filename=p)
                    code_refs = {n: 0 for n in names}
                    for node in ast.walk(t):
                        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                            if node.id in code_refs:
                                code_refs[node.id] += 1
                        elif isinstance(node, ast.Attribute):
                            if node.attr in code_refs:
                                code_refs[node.attr] += 1
                except SyntaxError:
                    code_refs = None
            for n in names:
                if code_refs is not None:
                    c = code_refs.get(n, 0)
                else:
                    c = word_hits(s, n)
                if c:
                    hits[n].append((rel, c))
    return hits


def defines_name(text, name):
    """该文件自己有没有 def/class 同名对象（grep 命中 ≠ 引用 core）。"""
    pat = r"(?m)^\s*(?:def|class)\s+%s\s*\(" % re.escape(name)
    return bool(re.search(pat, text))


def mark(name):
    return "*" if FAMILY_RE.search(name) else "~"


def build(core_path, tests_dir):
    src = read_text(core_path)
    try:
        tree = ast.parse(src, filename=core_path)
    except SyntaxError as e:
        raise SystemExit("语法解析失败：%s:%s %s" % (core_path, e.lineno, e.msg))
    defs, per_def = collect_refs(tree)

    # 全文件代码引用（不含赋值目标）：Name(Load) + Attribute
    ref_count = {n: 0 for n in defs}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in ref_count:
                ref_count[node.id] += 1
        elif isinstance(node, ast.Attribute) and node.attr in ref_count:
            ref_count[node.attr] += 1

    # 调用方：模块级函数（含嵌套子树）+ 模块级非 def 语句（登记表等）记为 <module>
    funcs = {n: d for n, d in defs.items()
             if isinstance(d["node"], (ast.FunctionDef, ast.AsyncFunctionDef))}
    callers = {n: set() for n in defs}
    for fname, d in funcs.items():
        for ref in refs_in(d["node"]) & set(defs):
            if ref != fname:
                callers[ref].add(fname)
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for ref in refs_in(stmt) & set(defs):
            callers[ref].add("<module>")

    candidates = sorted(
        (n for n in defs if FAMILY_RE.search(n)),
        key=lambda n: defs[n]["lineno"],
    )

    # 随迁闭包：从候选沿 per_def 走，收集非候选的 core 名字（记最短跳数）
    closure = {}
    stack = deque((n, 0) for n in candidates)
    seen = set(candidates)
    while stack:
        cur, depth = stack.popleft()
        for nxt in per_def.get(cur) or []:
            if nxt in seen:
                continue
            seen.add(nxt)
            if not FAMILY_RE.search(nxt):
                closure[nxt] = {
                    "name": nxt,
                    "depth": depth + 1,
                    "line": defs[nxt]["lineno"],
                    "lines": defs[nxt]["end_lineno"] - defs[nxt]["lineno"] + 1,
                    "kind": defs[nxt]["kind"],
                    "fan_in": len(callers.get(nxt) or ()),
                    "callers": sorted(callers.get(nxt) or ()),
                }
            stack.append((nxt, depth + 1))

    # 外部引用（src/ 下除 core.py 只看代码引用；tests/ 按工单口径做整词文本计数）
    ext = scan_files(SRC_DIR, candidates, skip=core_path, mode="ast")
    tst = scan_files(tests_dir, candidates)
    tst_defined = {}
    if os.path.isdir(tests_dir):
        for dp, dns, fns in os.walk(tests_dir):
            if "__pycache__" in dp:
                continue
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dp, fn)
                s = read_text(p)
                rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
                for n in candidates:
                    if defines_name(s, n):
                        tst_defined.setdefault(n, set()).add(rel)

    fan_in = {n: len(callers.get(n) or ()) for n in candidates}
    fan_out = {n: len([x for x in (per_def.get(n) or [])]) for n in candidates}

    return {
        "core": core_path,
        "core_lines": src.count("\n") + (0 if src.endswith("\n") else 1),
        "candidates": candidates,
        "defs": defs,
        "per_def": per_def,
        "ref_count": ref_count,
        "callers": {n: sorted(callers.get(n) or ()) for n in candidates},
        "closure": sorted(closure.values(),
                          key=lambda x: (x["depth"], -x["fan_in"], x["line"])),
        "external": ext,
        "tests": tst,
        "tests_defined": {k: sorted(v) for k, v in tst_defined.items()},
        "fan_in": fan_in,
        "fan_out": fan_out,
    }


def disp(path):
    """仓内路径显示成相对路径，避免把本机盘符写进留痕。"""
    try:
        rel = os.path.relpath(path, ROOT)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


def table(data):
    out = []
    out.append("# core.py CDP/browser 瘦身依赖图（静态）")
    out.append("")
    out.append("- core: `%s`（%d 行）" % (disp(data["core"]), data["core_lines"]))
    out.append("- 口径：模块级名字、名字含 `cdp|browser|webkit|chrome|devtools`；"
               "引用数 = AST 里的 Name(Load)/Attribute 次数（注释、文档串不算）。")
    out.append("- 标记：`*` = 命中工单名字口径；`~` = 名字没命中、但在随迁闭包里；"
               "`†` = 该文件自己定义了同名函数/类（文本命中不等于引用 core）。")
    out.append("")
    out.append("## 候选清单")
    out.append("")
    out.append("| 名字 | 类型 | 行 | 行数 | 同文件引用 | 调用方 | 被调用（core） |")
    out.append("|---|---|---:|---:|---:|---|---|")
    for n in data["candidates"]:
        d = data["defs"][n]
        callees = [x for x in data["per_def"].get(n) or [] if x in data["defs"]]
        cal = ", ".join("%s%s" % (mark(x), x) for x in callees[:6])
        if len(callees) > 6:
            cal += ", …(+%d)" % (len(callees) - 6)
        cs = data["callers"][n]
        caller_txt = ", ".join(cs[:5]) + (", …(+%d)" % (len(cs) - 5) if len(cs) > 5 else "")
        out.append("| `%s` | %s | %d | %d | %d | %s | %s |"
                   % (n, d["kind"], d["lineno"], d["end_lineno"] - d["lineno"] + 1,
                      data["ref_count"][n], caller_txt or "—", cal or "—"))
    out.append("")
    out.append("## 外部引用")
    out.append("")
    for n in data["candidates"]:
        e = data["external"].get(n) or []
        t = data["tests"].get(n) or []
        if not e and not t:
            continue
        parts = []
        if e:
            parts.append("src(代码): " + ", ".join("%s(%d)" % x for x in e))
        if t:
            own = set(data["tests_defined"].get(n) or [])
            parts.append("tests(文本): " + ", ".join(
                "%s(%d%s)" % (f, c, "†" if f in own else "") for f, c in t))
        out.append("- `%s` ← %s" % (n, "；".join(parts)))
    out.append("")
    out.append("## 随迁闭包（候选之外、被候选直接/间接引用的 core 名字）")
    out.append("")
    out.append("| 名字 | 类型 | 行 | 行数 | 跳数 | 被几个 core 函数引用 | 引用者 |")
    out.append("|---|---|---:|---:|---:|---:|---|")
    for c in data["closure"]:
        cs = c["callers"]
        txt = ", ".join(cs[:4]) + (", …(+%d)" % (len(cs) - 4) if len(cs) > 4 else "")
        out.append("| `%s%s` | %s | %d | %d | %d | %d | %s |"
                   % (mark(c["name"]), c["name"], c["kind"], c["line"], c["lines"],
                      c["depth"], c["fan_in"], txt or "—"))
    out.append("")
    out.append("## 排行（只看候选）")
    out.append("")
    top_in = sorted(data["candidates"], key=lambda n: (-data["fan_in"][n], n))[:5]
    top_out = sorted(data["candidates"], key=lambda n: (-data["fan_out"][n], n))[:5]
    top_heat = sorted(data["candidates"],
                      key=lambda n: (-(data["fan_in"][n] + data["fan_out"][n]), n))[:5]
    out.append("- 扇入（被同文件几个函数依赖）前五：" +
               "、".join("`%s`(%d)" % (n, data["fan_in"][n]) for n in top_in))
    out.append("- 扇出（自己依赖几个 core 名字）前五：" +
               "、".join("`%s`(%d)" % (n, data["fan_out"][n]) for n in top_out))
    out.append("- 搬迁热度（扇入+扇出）前五：" +
               "、".join("`%s`(%d)" % (n, data["fan_in"][n] + data["fan_out"][n])
                         for n in top_heat))
    out.append("- 候选 %d 个（函数 %d / 常量 %d / 变量 %d），候选行数合计 %d。"
               % (len(data["candidates"]),
                  sum(1 for n in data["candidates"] if data["defs"][n]["kind"] == "函数"),
                  sum(1 for n in data["candidates"] if data["defs"][n]["kind"] == "常量"),
                  sum(1 for n in data["candidates"] if data["defs"][n]["kind"] == "变量"),
                  sum(data["defs"][n]["end_lineno"] - data["defs"][n]["lineno"] + 1
                      for n in data["candidates"])))
    return "\n".join(out) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--core", default=DEFAULT_CORE)
    ap.add_argument("--tests", default=DEFAULT_TESTS)
    ap.add_argument("--out", default="", help="把 markdown 写到这个文件（默认 stdout）")
    ap.add_argument("--json", default="", help="另外把原始数据写成 JSON")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.core):
        print("找不到 core：%s" % args.core, file=sys.stderr)
        return 2
    data = build(args.core, args.tests)
    md = table(data)
    if args.out:
        d = os.path.dirname(os.path.abspath(args.out))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        print("已写：%s（%d 行）" % (args.out, md.count("\n")))
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        sys.stdout.write(md)
    if args.json:
        with io.open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, default=str)
        print("已写：%s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
