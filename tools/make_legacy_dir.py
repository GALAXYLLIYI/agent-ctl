# -*- coding: utf-8 -*-
r"""从 baseline/tools_.../ 抽一份**干净的** legacy_tools/ 进仓。

## 为什么必须多这一步

`.gitignore` 把 `baseline/` 整个排除了 —— 那里有**真实手机号**、
对话记录、`_auth` 授权状态，绝不能发到 GitHub。
但 `perceive.py` / `act.py` / `gate.py` 这些是控制链路的底座，
**不在仓库里就装不上**（install.ps1 第 3 步会直接 exit 6）。

所以：从 baseline 抽一份只含生产代码的子集，放成 `legacy_tools/`，进仓。
判据与 pack_dist.py 一致，另加一道隐私扫描（手机号 / 身份证 / 密钥样式）。

## 隐私扫描是真会拦人的

第一版只按文件名过滤，跑出来 103 个文件里有 2 个含 11 位手机号字面量。
所以扫描结果不能只当提示，命中即中止。
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "baseline" / "tools_20261002_1140"
DST = ROOT / "legacy_tools"

SKIP_DIRS = {"_auth", "_digest", "__pycache__"}
SKIP_EXT = {".pyc", ".pyo", ".pyd", ".log"}

# 隐私判据：11 位手机号、18 位身份证、常见密钥赋值
PAT_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
PAT_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
PAT_SECRET = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"][^'\"]{6,}")
PAT_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# ★ clip.py 不在这里：它是平台自己的模块（src/agentctl/clip.py），
#   不是 legacy 老脚本。第一版把它列进来，脚本报「控制链路缺件」——
#   报的是假缺件。清单只列**确实来自 baseline 的文件**。
CONTROL_CHAIN = ["perceive.py", "act.py", "gate.py", "auth_popup.py",
                 "win_list.py", "preflight.py", "cdp.py", "coord.py",
                 "brake.py", "click_option.py", "option_locator.py",
                 "radio_finder.py", "selftest.py", "whitelist.json"]

# ★ 永久排除名单。分两类：
#   ① 私人数据 —— 真实手机号/身份证，绝不能外流
#   ② 不是 AgentCtl 的东西 —— 属于 DSH 项目或用户的具体业务
#   第②类更隐蔽：它们功能上没坏，放进开源仓库也不会报错，
#   但会把**另一个项目**的路径、策略、以及用户的真实业务场景（个税师实操、
#   微信考试页）一起公开。所以按「不属于本项目」排除，不等闸门报错。
#
#   列进来还有个实际好处：脚本每次都返回 1 的话，看起来像坏了，
#   其实只是在重复报同一件已经决定过的事。
PERMANENT_DROP = {
    # ① 私人数据
    "case_data.json": "真实手机号/身份证，私人数据",
    # ② DSH 项目的东西（路径全指向 <另一项目> / <工作手册>）
    "★远程命脉保护清单.md": "DSH 的远程保护清单，与本项目无关",
    "guardian_policy.json": "DSH 的 guardian 策略配置",
    "handover_check.py": "DSH 的交接检查",
    "monitor_resources.py": "输出写死到 agent-manual/_监控",
    "up.bat": "本机启动脚本，写死了本机 python 路径",
    "up.py": "本机启动脚本，急停路径写死到桌面",
    # ③ 用户的具体业务场景（个税师实操、案例数据、公众号考试页）
    "fjm_add.py": "个税师业务脚本",
    "fjm_bonus.py": "个税师业务脚本",
    "fjm_fill.py": "个税师业务脚本",
    "drill.py": "个税师刷题业务脚本",
    "card.py": "个税师业务脚本",
    "person_add.py": "个税师业务脚本",
    "prep_exam.py": "个税师考试环境脚本",
    "case_open.py": "用户案例数据入口",
    "append_to_manual.py": "往用户手册里写东西",
    "local_model.py": "本机本地模型接线",
    # ④ 旧契约与旧文档：主仓库已有 contract/tools.json 与 README.md
    "agent_tools.json": "旧工具契约，已被 contract/tools.json 取代",
    "README.md": "DSH 那份项目说明（含本机路径与真实经历），本项目另有 README",
}


def scan(p: Path, data: bytes) -> list[str]:
    try:
        t = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    hits = []
    if PAT_MOBILE.search(t):
        hits.append("手机号")
    if PAT_IDCARD.search(t):
        hits.append("身份证号")
    if PAT_SECRET.search(t):
        hits.append("密钥赋值")
    if PAT_EMAIL.search(t):
        hits.append("邮箱")
    return hits


def main() -> int:
    if not SRC.is_dir():
        raise SystemExit("[X] 源目录不存在：%s" % SRC)
    if DST.exists():
        shutil.rmtree(DST)

    copied, skipped, blocked = 0, [], []
    for p in sorted(SRC.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(SRC)
        if rel.parts and set(rel.parts) & SKIP_DIRS:
            skipped.append((str(rel), "运行数据/缓存"))
            continue
        if p.suffix.lower() in SKIP_EXT:
            skipped.append((str(rel), "编译产物/日志"))
            continue
        if rel.name in PERMANENT_DROP:
            skipped.append((str(rel), "永久排除（私人数据）：" + PERMANENT_DROP[rel.name]))
            continue
        if ".bak" in p.name or p.name.startswith("_") or p.name.startswith("session-"):
            skipped.append((str(rel), "临时脚本/会话记录/备份"))
            continue
        data = p.read_bytes()
        hits = scan(p, data)
        if hits:
            blocked.append((str(rel), "、".join(hits)))
            continue
        out = DST / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        copied += 1

    got = {p.name for p in DST.glob("*")}
    missing = [n for n in CONTROL_CHAIN if n not in got]

    print("抽出 %d 个文件到 %s" % (copied, DST.relative_to(ROOT)))
    print("跳过 %d 项" % len(skipped))
    if blocked:
        print()
        print("== 隐私闸门拦下 %d 个（**没有复制**）==" % len(blocked))
        for rel, why in blocked:
            print("  [X] %s —— %s" % (rel, why))
    if missing:
        print()
        print("[X] 控制链路缺件：" + ", ".join(missing))
    if blocked or missing:
        print()
        print("不通过。上面的文件要人工看过再决定放不放进开源仓库。")
        return 1
    print()
    print("通过：无隐私命中、控制链路齐全。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
