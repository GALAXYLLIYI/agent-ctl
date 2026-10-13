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
