# -*- coding: utf-8 -*-
r"""独立核验 dist 包：不看打包脚本怎么写的，只解出来逐个查。

为什么要独立核验而不是复用 pack_dist.py 的判断：同一份代码既是运动员
又是裁判，出错就一起错。核验脚本必须自己重新实现一遍「什么算敏感」，
而且判据要更严（宁可误报）。
"""
from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ZIP = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/agentctl.zip")

# 独立写的敏感判据（比打包脚本更严）
BAD_NAMES = {
    "auth_pass.json", "lease.json", "capture.json", "gate.lock",
    ".credentials.yaml", "credentials.yaml", "id_rsa", ".env",
    "gate_agentctl.log", "act.log",
}
PAT = re.compile(r"credential|secret|token|password|passwd|api[_-]?key|\.pem$|\.pfx$|\.key$", re.I)

MUST = [
    "app/src/agentctl_main.py",
    "app/src/agentctl/__init__.py",
    "app/src/agentctl/_bundled_deps.py",
    "app/src/agentctl/core.py",
    "app/src/agentctl/gate.py",
    "app/src/agentctl/widget.py",
    "app/contract/tools.json",
    "app/requirements.txt",
    "app/install.ps1",
    "app/uninstall.ps1",
    "app/build/local_legacy_home.txt",
    "app/legacy_tools/perceive.py",
    "app/legacy_tools/act.py",
    "app/legacy_tools/gate.py",
    "app/legacy_tools/auth_popup.py",
]

bad: list[str] = []
warn: list[str] = []

with zipfile.ZipFile(ZIP) as z:
    names = z.namelist()
    for n in names:
        low = n.lower()
        parts = low.split("/")
        base = parts[-1]
        if base in BAD_NAMES:
            bad.append("敏感文件：%s" % n)
        elif PAT.search(base):
            bad.append("名字像密钥：%s" % n)
        if any(p in ("_auth", "_digest") for p in parts):
            bad.append("含本机运行数据目录：%s" % n)
        if low.endswith((".pyc", ".pyd", ".log")):
            warn.append("编译产物/日志：%s" % n)
        if ".bak" in base or base.startswith("_"):
            warn.append("临时/备份：%s" % n)

miss = [m for m in MUST if m not in names]

print("包：%s" % ZIP.resolve())
print("  条目 %d，MD5 %s" % (len(names), hashlib.md5(ZIP.read_bytes()).hexdigest()))
print()
print("== 结论 ==")
for b in bad:
    print("  [X] " + b)
for w in warn[:10]:
    print("  [!] " + w)
if len(warn) > 10:
    print("  [!] …… 另有 %d 条同类" % (len(warn) - 10))
for m in miss:
    print("  [X] 缺件：%s" % m)

if bad or miss:
    print()
    print("不通过。")
    raise SystemExit(1)
print("  通过：无敏感文件、关键条目齐全（%d 项必备）。" % len(MUST))
raise SystemExit(0)
