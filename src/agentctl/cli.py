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
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        print("可用子命令:", ", ".join(sorted(ALIAS)))
        return 1
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
    return 0 if r.get("ok") else (int(r.get("code") or 1) or 1)


if __name__ == "__main__":
    raise SystemExit(main())
