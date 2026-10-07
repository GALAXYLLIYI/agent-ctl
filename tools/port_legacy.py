# -*- coding: utf-8 -*-
r"""把 legacy_tools/ 里写死的本机路径改成跟机器无关的写法。

## 为什么这是**功能**修复，不是清洁工作

1. 换台机器、换个用户名，这些路径直接不存在，脚本当场崩。
2. 更要命的是 brake.py 和 gate.py 里的急停 flag：
   平台自己用的是 %LOCALAPPDATA%\AgentCtl\_急停.flag（src/agentctl/gate.py），
   而 legacy 脚本写死 <工作区>\桌面\_急停.flag。
   两边看的是**两个不同的文件** ——「面板里拉急停」和「命令行 brake stop」
   会各拉各的，两边都显示成功，急停其实没全局生效。
   这种 bug 在本机上永远看不出来（路径都在），一换机就露馅。

## 改法统一成一条规则

    环境变量 > %LOCALAPPDATA%\AgentCtl（与 gate.APP_DIR 一致）> 脚本自身目录

用环境变量做最高优先级，是为了做实验时不至于去动用户的真急停
（src/agentctl/gate.py 一直有这个老规矩）。

脚本幂等：已经改过的地方会跳过，可以反复跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

T = Path(__file__).resolve().parent.parent / "legacy_tools"

FLAG_LINES = [
    "# ★ 不写死本机路径：换机器/换用户名就废（开源的最低要求）。",
    "#   而且**必须**和 src/agentctl/gate.py 的 BRAKE_FLAG 指向同一个文件，",
    "#   否则「面板里拉急停」和「命令行 brake stop」各拉各的，",
    "#   两边都显示成功，急停却没全局生效 —— 本机永远看不出来，一换机就露馅。",
    '_APP_DIR = os.environ.get("AGENTCTL_APP_DIR") or os.path.join(',
    '    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl")',
    'FLAG = os.environ.get("AGENTCTL_BRAKE_FLAG") or os.path.join(_APP_DIR, "_急停.flag")',
]

SHOTS_LINES = [
    "# ★ 默认写进 %LOCALAPPDATA%\\AgentCtl\\shots，不写死本机路径。",
    "#   平台进程会用 AGENTCTL_SHOTS_DIR（或直接注入 SHOTDIR）指过来，",
    "#   这里只是「独立跑这个脚本时」的兜底，指向同一个地方。",
    '_APP_DIR = os.environ.get("AGENTCTL_APP_DIR") or os.path.join(',
    '    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl")',
    'SHOTDIR = os.environ.get("AGENTCTL_SHOTS_DIR") or os.path.join(_APP_DIR, "shots")',
]


def flag_block(name: str) -> str:
    return "\n".join(FLAG_LINES).replace("FLAG = os.environ", name + " = os.environ")


def shots_block(name: str) -> str:
    return "\n".join(SHOTS_LINES).replace("SHOTDIR =", name + " =")


# (文件, 原文, 替换文)
EDITS = [
    ("brake.py", 'FLAG = r"<工作区>\\桌面\\_急停.flag"', flag_block("FLAG")),
    ("gate.py", 'BRAKE_FLAG = r"<工作区>\\桌面\\_急停.flag"', flag_block("BRAKE_FLAG")),
    ("perceive.py", 'SHOTDIR = r"<工作区>\\Documents\\agent-manual\\_监控\\shots"', shots_block("SHOTDIR")),
    ("selftest.py", 'SHOTS = r"<工作区>\\Documents\\agent-manual\\_监控\\shots"', shots_block("SHOTS")),
    ("coord.py",
     'sys.path.insert(0, r"<工作区>\\Documents\\dsh-viewer-plugin\\tools")',
     "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))"
     "   # ★ 跟着脚本走，不写死本机路径（换机器就 import 不到）"),
    ("cdp.py",
     '"--user-data-dir=F:\\\\<用户>\\\\Documents\\\\_edge_cdp"',
     '"--user-data-dir=" + os.path.join('
     'os.environ.get("LOCALAPPDATA", ""), "AgentCtl", "_edge_cdp")'),
    ("option_locator.py",
     'glob.glob(r"<工作区>\\Documents\\agent-manual\\_监控\\shots\\*_win2754900.png")',
     'glob.glob(os.path.join('
     'os.environ.get("AGENTCTL_SHOTS_DIR", ""), "*_win2754900.png"))'),
]


def main() -> int:
    if not T.is_dir():
        raise SystemExit("[X] 找不到 legacy_tools/，先跑 make_legacy_dir.py")
    ok = bad = 0
    for name, old, new in EDITS:
        f = T / name
        if not f.is_file():
            print("[X] %-18s 文件不在（跳过）" % name)
            bad += 1
            continue
        txt = f.read_bytes().decode("utf-8")
        n = txt.count(old)
        if n == 0:
            if "AGENTCTL_BRAKE_FLAG" in txt or "AGENTCTL_SHOTS_DIR" in txt or "os.path.abspath(__file__)" in txt:
                print("[OK] %-18s 已修过，跳过" % name)
                ok += 1
            else:
                print("[X] %-18s 没找到待替换原文：%s" % (name, old[:70]))
                bad += 1
            continue
        f.write_text(txt.replace(old, new), encoding="utf-8", newline="")
        print("[OK] %-18s 替换 %d 处" % (name, n))
        ok += 1
    print()
    print("改好 %d 个，待处理 %d 个" % (ok, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
