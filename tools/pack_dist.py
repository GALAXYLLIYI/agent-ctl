# -*- coding: utf-8 -*-
r"""打分发包：把仓库裁成 dist/agentctl.zip（install.ps1 吃的那种）。

存在的唯一理由是**别把不该发的东西发到 GitHub 上**：仓库里有
baseline/tools_.../_auth/（授权状态、通行证、日志），还有一堆
一次性调试脚本、备份文件、会话记录。手工 zip -r 一定会漏掉其中之一，
所以走白名单 + 敏感名闸门。

用法：
    python -X utf8 tools/pack_dist.py
    python -X utf8 tools/pack_dist.py --out D:/x.zip
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
# ★ 从**已净化**的 legacy_tools/ 取，不是从 baseline/。
#   baseline/ 是另一个项目的快照，里面有真实手机号和 _auth 授权状态，
#   而且被 .gitignore 排除了 —— 从那里打包等于把隐私发到 GitHub Releases。
LEGACY_SRC = ROOT / "legacy_tools"

# 进包的目录（白名单，不在表里的一律不发）
DIRS = ["src", "contract", "stubs", "vendor"]

# 进包的单文件（白名单）
FILES = [
    "requirements.txt",
    "install.ps1",
    "uninstall.ps1",
    "README.md",
    "LICENSE",
]

# 进包的文档（docs/ 太大，只挑对外的那几份）
DOCS = ["AGENT接口.md"]

ALWAYS_EXCLUDE_DIRS = {
    "__pycache__", ".git", "work", "dist", "build",
    ".pytest_cache", ".mypy_cache", "_auth", "_digest",
}
ALWAYS_EXCLUDE_EXT = {".pyc", ".pyo", ".pyd", ".log"}

# 隐私扫描（与 tools/make_legacy_dir.py 同一套判据）——
# 打包是**最后一道闸**，不假设上游一定干净。
PAT_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
PAT_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
PAT_SECRET_ASSIGN = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"][^'\"]{6,}")

# 敏感名闸门：命中就中止，绝不「警告后照发」
SECRET_RE = re.compile(
    r"(credential|secret|token|password|passwd|\.pem$|\.pfx$|\.key$|id_rsa)",
    re.I,
)
SECRET_NAMES = {
    "auth_pass.json", "lease.json", "capture.json", "gate.lock",
    ".credentials.yaml", "config.yaml",
}

# legacy_tools 里这些是控制链路，少一个就废
LEGACY_REQUIRED = ["perceive.py", "act.py", "gate.py", "auth_popup.py"]


def guard(path: Path, rel: Path, keep_private: bool = False) -> str:
    """敏感检查：命中就直接 SystemExit，不给「照发」的选项。"""
    # ★ src/ 下**不能**按「_ 开头 = 一次性脚本」过滤。
    #   第一版一刀切，把 src/agentctl/__init__.py 和 _bundled_deps.py 一起排掉了，
    #   装完一跑就 ImportError: cannot import name '_bundled_deps' from 'agentctl'
    #   （包被降级成 namespace package，症状是 "unknown location"，不查到底会以为是环境问题）。
    #
    #   注意判断用的是**显式参数**而不是路径嗅探：rel 是相对被遍历的那个目录算的，
    #   遍历 src/ 时 parts[0] 是 "agentctl" 而不是 "src" ——
    #   第一版写成 `rel.parts[0] == "src"`，条件永远为假，缺件照旧被排掉。
    #   判据能通过「先造一个已知真阳性」验证：改完 __init__.py 就进了包，核验脚本也独立确认了。
    # 顺序有讲究：**先看目录排除**。_auth/ 整个目录本来就不发，
    # 里面的 auth_pass.json 属于「被排除」而不是「被拦截」——
    # 分清这两种，日志才不会把一次正常的打包说成事故。
    if rel.parts and set(rel.parts) & ALWAYS_EXCLUDE_DIRS:
        return "本机运行数据/缓存"
    if path.suffix.lower() in ALWAYS_EXCLUDE_EXT:
        return "编译产物/日志"
    if ".bak" in path.name:
        return "备份文件"
    if path.name.startswith("_") and not keep_private:
        return "一次性脚本"
    if path.name.startswith("session-"):
        return "会话记录"
    if path.name.lower() in SECRET_NAMES:
        raise SystemExit("[X] 敏感文件拦下了（不发）：%s" % path)
    if SECRET_RE.search(path.name):
        raise SystemExit("[X] 名字像密钥，拦下（不发）：%s" % path)
    return ""


def add_dir(z: zipfile.ZipFile, src: Path, arc: str, st: dict, keep_private: bool = False) -> None:
    if not src.is_dir():
        return
    for p in sorted(src.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(src)
        why = guard(p, rel, keep_private)
        if why:
            st["skip"].append((str(rel), why))
            continue
        data = p.read_bytes()
        z.writestr("%s/%s" % (arc, rel.as_posix()), data)
        st["files"] += 1
        st["bytes"] += len(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "agentctl.zip"))
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    st = {"files": 0, "bytes": 0, "skip": []}
    tmp = out.with_suffix(".zip.tmp")

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in DIRS:
            add_dir(z, ROOT / d, "app/" + d, st, keep_private=(d == "src"))
        for name in DOCS:
            p = ROOT / "docs" / name
            if p.is_file():
                z.write(p, "app/docs/" + name)
                st["files"] += 1
                st["bytes"] += p.stat().st_size
        for f in FILES:
            p = ROOT / f
            if not p.is_file():
                continue
            data = p.read_bytes()
            z.writestr("app/" + f, data)
            st["files"] += 1
            st["bytes"] += len(data)

        # legacy_tools：控制链路的底座（窗口枚举/键鼠注入/OCR 都在里面）
        if not LEGACY_SRC.is_dir():
            raise SystemExit("[X] 找不到 legacy 脚本源目录：%s" % LEGACY_SRC)
        got = set()
        for p in sorted(LEGACY_SRC.rglob("*")):
            if p.is_dir():
                continue
            rel = p.relative_to(LEGACY_SRC)
            why = guard(p, rel)
            if why:
                st["skip"].append((str(rel), why))
                continue
            data = p.read_bytes()
            # 隐私复扫：命中就中止，不给「照发」选项。
            try:
                txt = data.decode("utf-8")
            except UnicodeDecodeError:
                txt = ""
            bad = []
            if PAT_MOBILE.search(txt):
                bad.append("手机号")
            if PAT_IDCARD.search(txt):
                bad.append("身份证号")
            if PAT_SECRET_ASSIGN.search(txt):
                bad.append("密钥赋值")
            if bad:
                raise SystemExit("[X] 隐私闸门拦下（不发）：%s —— %s" % (rel, "、".join(bad)))
            z.writestr("app/legacy_tools/" + rel.as_posix(), data)
            got.add(rel.name)
            st["files"] += 1
            st["bytes"] += len(data)

        missing = [n for n in LEGACY_REQUIRED if n not in got]
        if missing:
            raise SystemExit("[X] 控制链路缺件，不能发包：" + ", ".join(missing))

        # 指针文件：gate.py 的 _resolve_home 读它
        z.writestr("app/build/local_legacy_home.txt", "legacy_tools")

    os.replace(tmp, out)

    print("打包完成：%s" % out)
    print("  文件 %d 个，原始 %.0f KB" % (st["files"], st["bytes"] / 1024))
    print("  zip 大小 %.0f KB" % (out.stat().st_size / 1024))
    print("  MD5 %s" % hashlib.md5(out.read_bytes()).hexdigest())
    print("  跳过 %d 项（临时脚本/备份/运行数据）" % len(st["skip"]))
    for why, n in Counter(w for _, w in st["skip"]).most_common(8):
        print("    %s: %d" % (why, n))
    print()
    print("发布：git tag v1.2.0 && gh release create v1.2.0 dist/agentctl.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
