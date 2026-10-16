# -*- coding: utf-8 -*-
r"""cli.py —— 命令行传输层（第三态）

用法：
    python -m agentctl.cli status
    python -m agentctl.cli lease --action acquire --actor DSH --scopes B
    python -m agentctl.cli timeline --limit 20
    python -m agentctl.cli shot --hwnd 1234
    python -m agentctl.cli --list                 # 列工具
★ 与 HTTP/MCP 一样调 core.dispatch() —— 行为必须一致（三态同源）。
  参数一律 --k v；值会尝试按 JSON 解析（数字/数组/布尔），解析不了就当字符串。

★ 退出码语义（2026-10-14 明确。以前这里混着，害「按退出码判成败」的脚本读错）：
    0            = 成功
    2            = **用法错误**（没给子命令）或 `-h/--help` 之外的东西
                   —— ★ 与「调用失败」分开：命令行写错了不该等同调用失败
    15           = **服务内部错误**（W4-B：单工具 help 读不到契约 = 部署坏了，
                   不是用法错误 ⇒ 不许回 0/2，那会让脚本/CI 把坏部署当成功）
    1..255       = **调用失败**，取信封 `code`；拿不到码（None/0）时落 1
  ⇒ ⚠️ 别把 2 当成某个信封码：**信封码里没有 2**（码表 = `gate.py` 那一张 / `contract\tools.json`）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentctl import core                                            # noqa: E402

ALIAS = {
    "status": "ctl_status", "lease": "ctl_lease", "brake": "ctl_brake",
    "timeline": "ctl_timeline", "verify": "ctl_verify", "witness": "ctl_witness",
    "windows": "ctl_windows", "shot": "ctl_shot", "tree": "ctl_tree",
    "find": "ctl_find",           # 2026-10-07：别名表漏了它，CLI 就调不到
    "click": "ctl_click", "type": "ctl_type", "fill": "ctl_fill",
    "authorize": "ctl_authorize", "replay": "ctl_replay",
    "record": "ctl_record", "browser": "ctl_browser",
}


# ── W4-B（2026-10-14）：单工具参数表，**从契约生成**，一个字都不手抄 ──────
#
# ★ 为什么必须在 dispatch **之前**拦截：原来 `parse_args()` 不认 `--help`，
#   它被当成普通参数 `help=True` 塞进 dispatch ⇒ `cli click --help` 会**真的走完
#   动作路径**（有有效通行证时真点一下；`t_click` 忽略这个多余的键）。
#   这不只是「帮助不好看」，是**自伤**。所以 help 一律在 main() 里前置拦截。
HELP_FLAGS = ("-h", "--help")

# 契约里 JSON Schema 的 type → 命令行占位符（照着填就知道该给什么）
TYPE_PLACEHOLDER = {
    "string": "<str>", "integer": "<int>", "number": "<num>",
    "boolean": "<true|false>", "array": "<json数组>", "object": "<json对象>",
}
DESC_MAX = 160   # 契约里有的 description 动辄三五行；终端里截一下，全文去契约看


def _contract_tools():
    """读 contract\\tools.json，返回 (工具表 dict, 错误对象)。

    ★ 路径必须走 core.ROOT：core.py 顶部已经为冻结态（PyInstaller）算好了 ROOT
      （认 sys._MEIPASS，见 core.py:32-36）。在 cli.py 里自己再推一遍路径 =
      把那个「冻结后找不到契约」的已修 bug 重新引入一遍。
    ★ 读不到就返回 (None, 异常)，**绝不塞一条假工具**（core.tool_list 那样做过）：
      「契约读不到」是部署坏了，「工具不存在」是用法错误，混起来会把坏部署
      报成用法错误 —— 调用方再也分不出该找谁。
    """
    p = os.path.join(core.ROOT, "contract", "tools.json")
    try:
        with open(p, encoding="utf-8") as f:
            spec = json.load(f)
        return {t["name"]: t for t in spec["tools"]}, None
    except Exception as e:      # 文件不在 / JSON 坏了 / tools 键没了 —— 一律算读不到
        return None, (p, e)


def _type_text(sch) -> str:
    """一个参数的类型占位符。enum 优先（有 enum 时值就那么几个，写出来最有用）。"""
    if not isinstance(sch, dict):
        return "<value>"
    if sch.get("enum"):
        return "{" + "|".join(str(x) for x in sch["enum"]) + "}"
    ph = TYPE_PLACEHOLDER.get(sch.get("type"), "<value>")
    if sch.get("type") == "array" and isinstance(sch.get("items"), dict):
        ph += "[" + TYPE_PLACEHOLDER.get(sch["items"].get("type"), "值") + ", ...]"
    return ph


def render_help(t: dict, alias: str) -> str:
    """单个工具的参数表。全部取自契约 ⇒ 契约改过这里自动跟，不用改第二处。"""
    sch = t.get("input_schema") or {}
    props = sch.get("properties") or {}
    req = list(sch.get("required") or [])
    L = []
    L.append("%s  %s" % (t["name"], t.get("title", "")))
    L.append("命令行写法：python -m agentctl.cli %s --参数名 值" % alias)
    desc = (t.get("description") or "").strip()
    if desc:
        L.append("说明：" + desc)
    L.append("闸门：%s（没通行证/次数用尽会被拦在动作之前）" % t.get("gate", "（契约未声明）"))
    L.append("必填：" + ("、".join(req) if req else "（无）"))
    L.append("额外参数：" + ("拒绝（additionalProperties: false）"
                              if sch.get("additionalProperties") is False else "接受"))
    if props:
        width = max(len(k) for k in props)
        L.append("参数（★ 顺序就是契约里的顺序，也就是推荐调用顺序）：")
        for k, v in props.items():
            mark = "*" if k in req else " "
            typ = _type_text(v)
            tail = ""
            if "default" in v:
                tail += "  默认：%s" % json.dumps(v["default"], ensure_ascii=False)
            d = (v.get("description") or "").strip().replace("\n", " ")
            if len(d) > DESC_MAX:
                d = d[:DESC_MAX] + "…（全文见 contract\\tools.json）"
            L.append("  %s--%-*s  %-18s %s%s"
                     % (mark, width, k, typ, d, tail))
        L.append("  （带 * 的是必填；参数名里的下划线写成连字符也一样认）")
    else:
        L.append("参数：无")
    if t.get("returns"):
        L.append("返回（data 部分）：" + str(t["returns"]))
    L.append("★ --help / -h 已被本 CLI 拦截，永远不会真的去执行动作；")
    L.append("  但如果某个参数的值恰好写成字面量 --help，也会被当成求帮助（契约里没有 help 这个参数）。")
    return "\n".join(L)


def parse_args(argv):
    args, i = {}, 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("--"):
            i += 1
            continue
        k = a[2:].replace("-", "_")
        v = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else "true"
        try:
            v = json.loads(v)
        except Exception:
            pass
        args[k] = v
        i += 2 if (i + 1 < len(argv) and not argv[i + 1].startswith("--")) else 1
    return args


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        # ★ 用法错误 = 2（不是 1）。1 留给「调用了但失败且拿不到码」——
        #   以前两者都回 1，脚本就没法区分「我命令行写错了」和「服务真的报错了」。
        print(__doc__)
        print("可用子命令:", ", ".join(sorted(ALIAS)))
        return 2
    if argv[0] in ("-h", "--help"):
        # 用户主动求帮助 = 正常请求，回 0（不是用法错误）。
        print(__doc__)
        print("可用子命令:", ", ".join(sorted(ALIAS)))
        return 0
    if argv[0] == "--list":
        for t in core.tool_list():
            flag = "" if t.get("_status") != "not_implemented" else "  [未实现]"
            print(f"  {t['name']:<18} {t.get('description', '')[:56]}{flag}")
        return 0
    cmd = argv[0]
    tool = ALIAS.get(cmd, cmd if cmd.startswith("ctl_") else "ctl_" + cmd)
    # ★ W4-B：单工具参数表。**必须在 dispatch 之前**拦截 ——
    #   以前 `click --help` 会真的走完动作路径（有证时真点一下），这是自伤。
    #   子命令之后任何位置出现 help 标志都触发；`--hwnd 1` 这类实参不解析。
    if any(a in HELP_FLAGS for a in argv[1:]):
        tools, err = _contract_tools()
        if err is not None:
            path, exc = err
            print("★ 读不到契约 %s：%r" % (path, exc), file=sys.stderr)
            print("  契约读不到 = **部署坏了**（打包漏了 contract\\ 也会这样），", file=sys.stderr)
            print("  不是命令行写错 ⇒ 退出码 15（内部错误），别拿 0/2 把它当成功。", file=sys.stderr)
            return 15
        if tool not in tools:
            print(__doc__)
            print("可用子命令:", ", ".join(sorted(ALIAS)))
            print("契约里没有这个工具：%s（可用工具：%s）"
                  % (tool, ", ".join(sorted(tools))), file=sys.stderr)
            return 2        # 工具名不存在 = 用法错误（与「部署坏了」分开）
        print(render_help(tools[tool], cmd))
        return 0            # 主动求帮助 = 正常请求
    args = parse_args(argv[1:])
    r = core.dispatch(tool, args, actor=os.environ.get("AGENTCTL_ACTOR", "cli"))
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if r.get("ok"):
        return 0
    # 失败：退出码 = 信封码。★ 码可能是 0/None（例如 ok=False 却忘了归码），
    #   那种情况落 1 —— 但**不静默**：S8 已给这种信封加了 data.uncategorized 标记，
    #   看上面打印出来的那段 JSON 就知道。拿不准就用 `ctl_status` 或看 warning。
    try:
        c = int(r.get("code") or 0)
    except (TypeError, ValueError):
        c = 0
    return c if c > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
