# -*- coding: utf-8 -*-
r"""扫 legacy_tools/ 里不宜开源的内容：本机绝对路径、真实姓名、密钥样式。

为什么单独扫一遍（make_legacy_dir 已经扫过手机号/身份证）：
那两道闸门管的是**数字型隐私**，管不住
  · <工作区>\... 这类**本机绝对路径**（泄露目录结构、用户名 <用户>）
  · dsh / 用户 / 豆包 / TRAE 之类的**内部人名与项目代号**
  · 写着「用户」「本机实测」的**私人叙述**
这些不影响功能，但开源后会很尴尬。

输出是**清单**，不自动改 —— 改法涉及语义，自动改容易把代码改坏。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "legacy_tools"

PAT_PATH = re.compile(r"[A-Z]:\\[A-Za-z0-9_\\.一-鿿-]+|<用户>|dsh-viewer-plugin|dsh-viewer")
PAT_NAME = re.compile(r"用户|豆包|WorkBuddy|WorkBuddy|TRAE|trae|dsh\b")
PAT_WECHAT = re.compile(r"1323779\d{4}|微信|weixin")

rows: list[tuple[str, str, str]] = []
for p in sorted(TARGET.rglob("*")):
    if not p.is_file():
        continue
    try:
        t = p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        continue
    rel = p.relative_to(TARGET).as_posix()
    if PAT_WECHAT.search(t):
        rows.append((rel, "手机号/微信", PAT_WECHAT.search(t).group(0)[:20]))
    if PAT_PATH.search(t):
        rows.append((rel, "本机路径/用户名", PAT_PATH.search(t).group(0)[:60]))
    if PAT_NAME.search(t):
        m = PAT_NAME.search(t)
        rows.append((rel, "内部人名/项目代号", m.group(0)))

print("扫描 %s" % TARGET.relative_to(ROOT))
if not rows:
    print("  干净：没有本机路径、人名、联系方式。")
    raise SystemExit(0)

by_file: dict[str, set] = {}
for rel, why, sample in rows:
    by_file.setdefault(rel, set()).add(why)
print()
print("== 命中 %d 处，涉及 %d 个文件 ==" % (len(rows), len(by_file)))
for rel in sorted(by_file):
    print("  %-42s %s" % (rel, "、".join(sorted(by_file[rel]))))
print()
print("这些不影响功能，但开源前该人工过一遍。")
raise SystemExit(1)
