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
        #   ① 目标就是仓库所有者本人（GitHub 不允许邀请 owner）；
        #   ② **目标不属于任何组织/企业 ⇒ GitHub 不允许给 maintain**（实测会报
        #      "Cannot assign <name> permission of maintain"）——
        #      这是最容易被误判成「权限配置有问题」的一种，实际是 GitHub 的
        #      账号类型限制：maintain/triage 只能给组织成员，个人小号最高 push。
        if _is_owner(login):
            return False, "这个账号就是仓库所有者（admin），GitHub 不允许邀请自己 —— 无需操作"
        if "permission of maintain" in low or "permission of triage" in low:
            return False, ("GitHub 不允许给该账号 maintain/triage 权限"
                           "（个人账号不属于任何组织/企业时，GitHub 只允许到 push）"
                           " ⇒ 请改用 push 权限重发")
        return False, "GitHub 拒绝：目标不能被邀请（可能是仓库所有者本人，或仓库被限制为仅限已定成员）"
    return False, f"发邀请失败：{err.splitlines()[0] if err else out}"


def _fallback_push(login: str) -> tuple[bool, str]:
    """maintain 被拒时的兜底：退到 push 再试一次。"""
    rc, out, err = gh(["api", "-X", "PUT",
                       f"repos/{REPO}/collaborators/{login}",
                       "-f", "permission=push"])
    if rc == 0:
        return True, "已用 **push** 权限发出邀请（maintain 被 GitHub 拒绝，已自动降级）"
    if "already" in err.lower():
        return True, "已经是协作者，无需邀请"
    return False, f"push 降级也失败：{err.splitlines()[0] if err else out}"


def _is_owner(login: str) -> bool:
    """目标是否就是仓库所有者。"""
    rc, out, _ = gh(["api", f"repos/{REPO}", "--jq", ".owner.login"])
    return rc == 0 and out.strip().lower() == login.lower()


def verify() -> str:
    """列出 push 及以上的成员（★ 注意：这个列表**不含 read 成员**）。

    ★ 别拿它当「对方有没有接受邀请」的判据 —— read 成员不在这里，
      会被误判成「没进来」。权威口径是 permission 端点，见 permission_of()。
    """
    rc, out, err = gh(["api", f"repos/{REPO}/collaborators",
                       "--jq", '.[] | "\\(.login)  admin=\\(.permissions.admin)  maintain=\\(.permissions.maintain)  push=\\(.permissions.push)"'])
    return out if rc == 0 else f"（回读失败：{err}）"


def permission_of(login: str) -> str:
    """★ 权威口径：查该账号在本仓的**实际**权限。

    为什么必须用它：`collaborators` 列表只列 push 及以上，
    一个只拿到 read 的账号根本不在里面 ⇒ 「列表里没有他」≠「他没进来」。
    """
    # owner 自己是 admin，但 permission 端点对 owner 也可能查不到 ⇒ 先判 owner。
    if _is_owner(login):
        return "admin  role=admin（仓库所有者）"
    rc, out, err = gh(["api", f"repos/{REPO}/collaborators/{login}/permission",
                       "--jq", '"\\(.permission)  role=\\(.role_name)"'])
    if rc != 0:
        return "查不到（多半是完全没进仓）"
    return out


def report(login: str) -> str:
    """一个账号的完整状态行：实际权限 + 邀请是否还挂着。"""
    perm = permission_of(login)
    rc, out, err = gh(["api", f"repos/{REPO}/invitations",
                        "--jq", f'.[] | select(.invitee.login == "{login}") | "邀请 id=\\(.id) 权限=\\(.permissions) 过期=\\(.expired)"'])
    inv = out if (rc == 0 and out) else "（没有待接受邀请）"
    verdict = ""
    if perm.startswith("push") or perm.startswith("maintain") or perm.startswith("admin"):
        verdict = "✅ 已生效"
    elif perm.startswith("read"):
        verdict = "⚠ 只拿到 read —— 邀请没被接受，让对方用正确账号去 /invitations 接受"
    else:
        verdict = "❌ 没进仓"
    return f"{perm}\n     邀请：{inv}\n     ⇒ {verdict}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("usernames", nargs="*", help="GitHub 用户名（可多个）")
    ap.add_argument("--file", help="从文件读账号名（一行一个，# 开头为注释）")
    ap.add_argument("--check", action="store_true",
                    help="只查状态，不发邀请：打印每个账号的【实际】权限 + 邀请是否还挂着")
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

    if args.check:
        # ★ 只查模式：用权威的 permission 端点，不发任何邀请。
        if not names:
            print("用法：python -X utf8 tools\\加协作者.py --check <用户名> [<用户名> ...]")
            return 1
        print(f"仓库：{REPO}\n")
        for n in names:
            print(f"● {n}")
            print(f"     {report(n)}\n")
        print("—— push 及以上的成员（★ 不含 read 成员，不是完整名单）——")
        print(verify())
        return 0

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
        if not ok and "maintain/triage" in msg:
            # ★ 自动降级重试：GitHub 只允许给「组织成员」maintain/triage，
            #   个人账号最高 push。直接改用 push 再发一次，别把事情停在这。
            ok2, msg2 = _fallback_push(n)
            print(f"[{'已发' if ok2 else '失败'}] {n} —— {info}")
            print(f"         maintain 被拒 → 降级重试：{msg2}")
            if not ok2:
                failed.append(n)
            continue
        print(f"[{'已发' if ok else '失败'}] {n} —— {info} → {msg}")
        if not ok:
            failed.append(n)

    print("\n—— 当前协作者名单 ——")
    print(verify())
    print("\n—— 待接受邀请 ——")
    rc, out, err = gh(["api", f"repos/{REPO}/invitations",
                        "--jq", '.[] | "\\(.invitee.login)  权限=\\(.permissions)  id=\\(.id)  (7 天内有效)"'])
    print(out if rc == 0 and out else "（无待接受邀请 —— 对方若已接受，名单里会多一行）")
    if failed:
        print(f"\n有 {len(failed)} 个没成功：{', '.join(failed)}")
        return 1
    print("\n全部成功。提醒对方：https://github.com/" + REPO + "/invitations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
