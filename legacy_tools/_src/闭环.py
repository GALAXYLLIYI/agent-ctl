# -*- coding: utf-8 -*-
r"""闭环.py —— 给任何动作套上「期望 → 动作 → 独立验证 → 三值判定 → 只追加账本」

为什么有这个东西
--------------------------------------------------------------------------------
GUI 动作**没有返回值**：点一下只是把一条消息塞进队列，"接口说成功"根本不等于"事情发生了"。
本项目已实测过：对 Chromium 窗口用后台方式点击，接口返回「已投递成功」，而**控件毫无反应**。
所以：**动作成没成，只能靠"目标状态变了没有"来判**，不能靠动作自己的回执。

本脚本把那条链固定下来，并且**以判定结果作为自己的退出码** ——
调用方（任何能跑 shell 的 agent / 脚本 / CI）只要看退出码，就不会把"没生效"当成"完成"。

用法
--------------------------------------------------------------------------------
    python 闭环.py --action "点保存并落盘" --tier B \
        --expect "file-changed:D:\x\靶子.txt" \
        -- python act.py click --hwnd 12345 --eid e3

    · --expect 必填：不给期望就没有"可验证"可言（宁可拒绝，也不假装闭环）
    · --paths 可省：省了就【自动从 file-* 期望里抽路径】当快照盯防范围
      （这一条很重要 —— 前后快照路径集不一致时，验动作.py 会拒绝判定、退出 5）
    · -- 之后是要跑的命令，原样转发，输出实时透传

退出码（★ 与 验动作.py 同口径）
--------------------------------------------------------------------------------
    0  satisfied     期望全部满足
    3  unsatisfied   有期望不满足    ← 动作没生效，**不许当成功**
    4  unknown       有变化但判不了  ← 同上，**不许当成功**
    5  快照不可比     判定无效（没记账）
    6  用法错误（没给 --expect / 没给命令）
    ★ 若动作自身返回非 0 而判定又不是 satisfied，**两个结果都会打印**，退出码取更"坏"的那个。

★ 三条回执可信度硬规矩（2026-10-12 实测换来）
--------------------------------------------------------------------------------
  1) 后台投递（background）的动作，**回执一律不作数** —— 它可能返回成功而控件零反应。
  2) 读窗口之前先确认它 **on-screen** —— 最小化/移出可见区的窗口 UIA 树会塌成 0 节点，
     看上去像"这应用没开无障碍"，其实只是没在屏上。
  3) **坐标只许来自放大取景**，不许目测（实测目测误差 60+ 像素，正好点在按钮外）。
"""
import argparse
import datetime
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 判定器本体，唯一定义处。AGENTCTL_VERIFY_PY 可覆盖 —— 与 core.py::_verify_py() 同一个变量名，
#   于是"指向某一份候选判定器"在主路 / 闭环入口 / bench 三处口径一致，做对照实验也方便。
VERIFY = (os.environ.get("AGENTCTL_VERIFY_PY")
          or os.path.join(HERE, "验动作.py"))
PY = sys.executable

EXIT_USAGE = 6


def _run(args, timeout=None):
    """跑一条命令，输出实时透传，返回 (rc, 合并输出)。"""
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out


def paths_from_expect(expect):
    """从 file-* 期望里抽路径（自动定快照盯防范围，免得前后不一致被判 5）。"""
    got = []
    for pred in re.split(r"[;,]", expect or ""):
        pred = pred.strip()
        m = re.match(r"^(file-[a-z]+|dir-[a-z]+):(.+)$", pred)
        if not m:
            continue
        arg = m.group(2).split("|")[0].strip()      # file-contains:路径|子串 → 只要路径
        if arg and arg not in got:
            got.append(arg)
    return ";".join(got)


def main():
    ap = argparse.ArgumentParser(
        description="给任何动作套上闭环：期望→动作→独立验证→三值判定→只追加账本")
    ap.add_argument("--action", required=True, help="动作名（写进账本）")
    ap.add_argument("--tier", default="?", help="动作档位 A/B/C/D")
    ap.add_argument("--expect", required=True,
                    help="★必填。期望谓词，分号分隔（与 验动作.py 同一套）")
    ap.add_argument("--paths", default="",
                    help="快照盯防路径（分号分隔）。省略则自动从 file-* 期望里抽")
    ap.add_argument("--allow-path-mismatch", action="store_true",
                    help="★放宽前后快照路径集不一致（会被记进账本 note，不推荐）")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后：要跑的命令")
    a = ap.parse_args()

    cmd = [x for x in a.cmd if x != "--"]
    if not cmd:
        print("✗ 没给要跑的命令。用法：闭环.py --action X --expect Y -- <命令…>")
        return EXIT_USAGE
    if not os.path.exists(VERIFY):
        print("✗ 找不到判定器：%s" % VERIFY)
        return EXIT_USAGE

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag_b = "闭环_%s_before" % stamp
    tag_a = "闭环_%s_after" % stamp

    paths = a.paths or paths_from_expect(a.expect)
    if not paths:
        print("  ⚠ 没给 --paths，也没能从期望里抽出文件路径 —— 前后快照可能不可比（会被判 5）。")
        print("     本动作若只看进程/窗口，可忽略本提示。")

    print("=" * 74)
    print("  闭环开始　动作=%s　档位=%s" % (a.action, a.tier))
    print("  期望      : %s" % a.expect)
    print("  盯防路径  : %s" % (paths or "(无)"))
    print("  要跑的命令: %s" % " ".join(cmd))
    print("=" * 74)

    # ── 1. 动作前快照（★ 期望必须在动作【之前】声明，否则不可归因）──
    sb = [PY, "-X", "utf8", VERIFY, "snap", "--tag", tag_b, "--paths", paths, "--expect", a.expect]
    rc, out = _run(sb, timeout=300)
    if rc != 0:
        print("✗ 动作前快照失败（rc=%d）：\n%s" % (rc, out[-800:]))
        return EXIT_USAGE
    print("  [1/4] 动作前快照 OK（%s）" % tag_b)

    # ── 2. 做动作（原样转发，输出实时透传）──
    print("-" * 74)
    act_rc, act_out = _run(cmd, timeout=None)
    sys.stdout.write(act_out if act_out.endswith("\n") or not act_out else act_out + "\n")
    print("-" * 74)
    print("  [2/4] 动作用完　动作自身 rc = %d" % act_rc)

    # ── 3. 动作后快照（不带 --expect：判定器会取 before 上的）──
    rc, out = _run([PY, "-X", "utf8", VERIFY, "snap", "--tag", tag_a, "--paths", paths],
                   timeout=300)
    if rc != 0:
        print("✗ 动作后快照失败（rc=%d）：\n%s" % (rc, out[-800:]))
        return EXIT_USAGE
    print("  [3/4] 动作后快照 OK（%s）" % tag_a)

    # ── 4. 判定（其退出码就是判定结论）──
    ck = [PY, "-X", "utf8", VERIFY, "check", "--tag", tag_a, "--before", tag_b,
          "--action", a.action, "--tier", a.tier]
    if a.allow_path_mismatch:
        ck.append("--allow-path-mismatch")
    print("  [4/4] 判定中…")
    vrc, vout = _run(ck, timeout=300)
    print(vout)

    verdict = {0: "satisfied", 3: "unsatisfied", 4: "unknown", 5: "快照不可比"}.get(vrc, "其它(%d)" % vrc)
    print("=" * 74)
    print("  闭环结论：%s" % verdict)
    print("  ★ 两个结果都报，互不掩盖：动作自身 rc = %d ｜ 独立判定 = %s" % (act_rc, verdict))
    if vrc in (3, 4):
        print("  ★★ 动作【没被证实生效】—— 调用方不许把它当完成。")
    print("=" * 74)

    # 退出码：判定不过 ⇒ 以判定码退出；判定过了但动作自己失败 ⇒ 透传动作的 rc
    if vrc != 0:
        return vrc
    return act_rc


if __name__ == "__main__":
    sys.exit(main())
