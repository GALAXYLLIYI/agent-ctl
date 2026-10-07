# -*- coding: utf-8 -*-
r"""agentctl_main.py —— 打包后的唯一入口

用法（打包后）：
    AgentCtl.exe serve            常驻 HTTP 服务
    AgentCtl.exe mcp              MCP stdio（⛔ 这个入口**绝不能用 -w 构建**）
    AgentCtl.exe status|lease|... CLI 子命令（等同 cli.py）
    AgentCtl.exe legacy <脚本> [参数...]   用**本 exe 自带的解释器**跑老脚本
                                          （这样安装包里不用再装一个 Python）

★ 为什么要有 legacy 子命令：
  服务现在是"包壳调用老脚本"，如果直接用系统 python 去跑，安装包就不自足了。
  让 exe 自带解释器去执行老脚本，安装包才是真正的"一个装完就能跑"。
"""
from __future__ import annotations

import os
import runpy
import sys

# ★★★ 输出编码兜底（冻结态才有这个问题，源码态 python -X utf8 已经好了）★★█
#     实测踩过：`AgentCtl.exe --help` 直接崩在打印「⛔」那行
#     —— UnicodeEncodeError 把整个程序打死，**装出来的 exe 点开就没反应**。
#     原因：中文 Windows 控制台是 GBK，打不出 emoji/生僻符号。
#     修法两步：① encoding='utf-8' 让 Python 按 UTF-8 输出；
#              ② errors='replace' 兜底 —— 万一某字符仍不可编，宁可显示成 ?，也不能崩。
for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 冻结后 sys.path[0] 是解包目录，源码模块在 _MEIPASS/agentctl 下；非冻结时在 src/
if getattr(sys, "frozen", False):
    BASE = sys._MEIPASS                                  # noqa: SLF001
else:
    BASE = os.path.dirname(os.path.abspath(__file__))

if BASE not in sys.path:
    sys.path.insert(0, BASE)

# ★★ 打包的关键一步：**必须让这个模块"可达"**，否则 PyInstaller 根本分析不到它。
#   我第一版只在注释里写了"打包时要用它"，结果 rebuild 后体积一点没变、13 个依赖照样缺 ——
#   因为入口没 import 它，静态分析就看不见里面的字面 import。
#   这里无条件 import（模块本身无副作用，不会真的去加载 win32gui 等）；
#   真正调用 load() 是在 legacy / deps 子命令里。
from agentctl import _bundled_deps                                # noqa: E402,F401


def _usage() -> int:
    print(__doc__)
    try:
        from agentctl import core
        print("可用工具：")
        for t in core.tool_list():
            flag = "" if t.get("_status") != "not_implemented" else "   [未实现]"
            print(f"  {t['name']:<18} {t.get('description', '')[:56]}{flag}")
    except Exception as e:
        print("（列工具失败：%s）" % e)
    return 1


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        return _usage()

    cmd = argv[0]

    if cmd == "serve":
        from agentctl.server import serve
        serve()
        return 0

    if cmd in ("panel", "widget"):
        # M1 悬浮窗（只读挂件）。**只显示状态，不消耗用户额度** ——
        # 理由见 agentctl/widget.py 的模块说明：1 fps 若走计数接口，
        # 200 次的 A 级额度 3 分钟就没了，而且挂件自己毫无察觉。
        from agentctl.widget import run as widget_run
        return widget_run()

    if cmd == "selftest":
        from agentctl.widget import selftest_widget
        bad, st = selftest_widget()
        print("只读挂件自检：%s" % ("通过" if not bad else "不通过"))
        print("读到的状态：%s" % st)
        for b in bad:
            print("  ⛔ %s" % b)
        return 0 if not bad else 4

    if cmd == "mcp":
        # ⛔ 打包时若加了 -w，stdout 就是 None，这里必须早点炸出来而不是静默死
        if sys.stdout is None or sys.stdin is None:
            sys.stderr.write("致命：本进程没有标准输入输出（是不是用 -w 打包了？）\n")
            return 2
        from agentctl.mcp import main as mcp_main
        return mcp_main()

    if cmd == "legacy":
        if len(argv) < 2:
            print("用法：AgentCtl.exe legacy <脚本路径> [参数...]")
            return 1
        script = argv[1]
        if not os.path.isabs(script):
            # ★ 老脚本目录**只从 gate 拿**（A 项，2026-10-04）。
            #   这里原来是自己第 4 次写死同一个 F:\... 路径 ——
            #   core / eye / widget 各一份，四份拷贝已经漂过一次（急停路径那次）。
            from agentctl import gate as _gate
            try:
                legacy_home = _gate.require_home()
            except _gate.LegacyToolsMissing as e:
                print("⛔ %s" % e)
                return 2
            cand = os.path.join(legacy_home, script)
            script = cand if os.path.exists(cand) else script
        if not os.path.exists(script):
            print(f"找不到脚本：{script}")
            return 2
        sys.argv = [script] + list(argv[2:])
        runpy.run_path(script, run_name="__main__")
        return 0

    if cmd in ("-h", "--help", "help"):
        return _usage()

    if cmd in ("brake-daemon", "brake_daemon"):
        # 全局热键 Ctrl+Alt+Home 的常驻监听。
        # 单独一个入口、独立一个进程 —— 理由见 brake_daemon 模块说明
        # （面板会关，热键不能跟着关；面板 selftest 也要保持「只读挂件」）。
        from agentctl.brake_daemon import run as brake_run
        return brake_run()

    if cmd == "brake":
        # 急停的手工通道（面板按钮之外的第三条）：
        #   AgentCtl.exe brake stop --why "命令行"
        #   AgentCtl.exe brake release
        if len(argv) < 2:
            print("用法：AgentCtl.exe brake stop|release [--why 原因]")
            return 1
        act = argv[1]
        if act == "stop":
            why = "命令行"
            if "--why" in argv:
                i = argv.index("--why")
                if i + 1 < len(argv):
                    why = argv[i + 1]
            from agentctl import gate as _g
            ok, msg = _g.set_brake(why)
        elif act == "release":
            from agentctl import gate as _g
            ok, msg = _g.clear_brake()
        else:
            print("brake 只能 stop 或 release，收到：%s" % act)
            return 1
        print(("OK " if ok else "[X] ") + msg)
        return 0 if ok else 4

    # 其余一律当 CLI 子命令
    sys.argv = ["agentctl"] + argv
    from agentctl.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
