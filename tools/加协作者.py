# -*- coding: utf-8 -*-
"""加协作者（maintain）—— 带前置校验的批量脚本。

为什么要有这个脚本：直接把 `gh api -X PUT .../collaborators/<名字>` 贴到
PowerShell 里，**占位符没替换就会发出一次无效请求**（返回 404，
看起来像"没权限"，其实是"用户名不存在"）。本脚本先查账号、再发邀请、
再回读确认，三步都出声。

用法（三种）：
    1) 交互式：直接跑，会逐个问你要账号名
         python -X utf8 tools\加协作者.py

    2) 命令行传名：
         python -X utf8 tools\加协作者.py 用户名1 用户名2

    3) 从文件读（一行一个，# 开头为注释）：
         python -X utf8 tools\加协作者.py --file contributors.txt

退出码：0 = 全部成功；1 = 至少一个失败（逐条报错，不中断其余）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = "GALAXYLLIYI/computer-use-gate"
PERMISSION = "maintain"


def gh(args: list[str]) -> tuple[int, str, str]:
    """跑 gh，返回 (退出码, stdout, stderr)。"""
    p = subprocess.run(["gh", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def check_user(login: str) -> tuple[bool, str]:
    """前置校验：账号是否存在、是不是机器人。返回 (可邀请, 说明)。"""
    rc, out, err = gh(["api", f"users/{login}",
                       "--jq", '"type=" + .type + " name=" + (.name // "-") + " created=" + .created_at'])
    if rc != 0:
        if "Not Found" in err:
            return False, "账号不存在（GitHub 上没有这个用户名）"
        if "rate limit" in err.lower():
            return False, "触发 API 限流，等几分钟再试"
        return False, f"查询失败：{err.splitlines()[0] if err else '未知错误'}"
    return True, out


def invite(login: str) -> tuple[bool, str]:
    """发协作者邀请。返回 (成功, 说明)。"""
    rc, out, err = gh(["api", "-X", "PUT",
                       f"repos/{REPO}/collaborators/{login}",
                       "-f", f"permission={PERMISSION}"])
    if rc == 0:
        return True, "邀请已发出（对方需在 7 天内接受）"
    low = err.lower()
    if "already" in low:
        return True, "已经是协作者，无需邀请"
    if "validation failed" in low:
        # ★ 这条消息有多种成因，不能一律说成「仓库限制」——
        #   最常见的一种是**目标就是仓库所有者本人**（GitHub 不允许邀请 owner）。
        if _is_owner(login):
            return False, "这个账号就是仓库所有者（admin），GitHub 不允许邀请自己 —— 无需操作"
        return False, "GitHub 拒绝：目标不能被邀请（可能是仓库所有者本人，或仓库被限制为仅限已定成员）"
    return False, f"发邀请失败：{err.splitlines()[0] if err else out}"


def _is_owner(login: str) -> bool:
    """目标是否就是仓库所有者。"""
    rc, out, _ = gh(["api", f"repos/{REPO}", "--jq", ".owner.login"])
    return rc == 0 and out.strip().lower() == login.lower()


def verify() -> str:
    rc, out, err = gh(["api", f"repos/{REPO}/collaborators",
                       "--jq", '.[] | "\\(.login)  admin=\\(.permissions.admin)  maintain=\\(.permissions.maintain)  push=\\(.permissions.push)"'])
    return out if rc == 0 else f"（回读失败：{err}）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("usernames", nargs="*", help="GitHub 用户名（可多个）")
    ap.add_argument("--file", help="从文件读账号名（一行一个，# 开头为注释）")
    args = ap.parse_args()

    names = list(args.usernames)
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        names.append(line)
        except OSError as e:
            print(f"读文件失败：{e}")
            return 1

    if not names:
        print("没有指定账号名。逐个输入，空行结束：")
        while True:
            try:
                s = input("GitHub 用户名 > ").strip()
            except EOFError:
                break
            if not s:
                break
            names.append(s)

    if not names:
        print("没有要邀请的账号，退出。")
        return 1

    # 去重、保持顺序
    seen, uniq = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)

    print(f"仓库：{REPO}    权限：{PERMISSION}    共 {len(uniq)} 个账号\n")
    failed = []
    for n in uniq:
        if _is_owner(n):
            print(f"[跳过] {n} —— 这就是仓库所有者本人（admin），不需要邀请自己")
            continue
        ok, info = check_user(n)
        if not ok:
            print(f"[跳过] {n} —— {info}")
            failed.append(n)
            continue
        ok, msg = invite(n)
        print(f"[{'已发' if ok else '失败'}] {n} —— {info} → {msg}")
        if not ok:
            failed.append(n)

    print("\n—— 当前协作者名单 ——")
    print(verify())
    if failed:
        print(f"\n有 {len(failed)} 个没成功：{', '.join(failed)}")
        return 1
    print("\n全部成功。提醒对方：https://github.com/" + REPO + "/invitations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
