# -*- coding: utf-8 -*-
r"""tests\_通道保真_判据.py —— 门禁 3「通道保真」的判据（本地 agent 2026-10-17）

必要条件 3 原话：
    屏幕类判据要有【枚举清单 + 每条的 截图→sha256→结论 manifest + 采摘规程】；
    **API 读数只作辅助**。

本判据**不改采集**（采集会跑 4 个屏幕类判据，不能在判据里再跑一遍），只做三件核对：
  [1] **枚举完整**：屏幕类判据 = {w3, wI, P0, 端到端}；每条都必须存在于 `tests\`，
      且**在 `run_all.CRITERIA` 里**（或在显式 NOT_WIRED 里，并给理由）——漏一条就红。
  [2] **manifest 齐全**：`work\_通道保真_manifest_<今天>.md` 必须存在，且：
      · 表格里**每条**屏幕类判据都有一行；· 表里不许有屏幕类之外的判据混进来；
      · 每行都带 64 位 sha256 与截图路径；· **文件真实存在且 sha256 重算一致**（截图即证据）。
  [3] **规程在场**：manifest 里必须有「采摘规程」段（抓图 API / 时机 / 哈希 / A·B·C 分级），
      且必须写明「API 读数只作辅助」。

退出码：0=绿；1=红；2=**验不了**（今天还没采集，或采集工具缺失）—— 2 不许读成绿。
"""
import argparse
import hashlib
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("AGENTCTL_ROOT") or os.path.dirname(HERE)
MANIFEST = os.path.join(ROOT, "work", "_通道保真_manifest_%s.md" % __import__("time").strftime("%Y%m%d"))
COLLECTOR = os.path.join(ROOT, "work", "_通道保真_采集.py")

# 屏幕类判据 = **建窗/抓屏/看前台**才算（在文件系统里转的不算）
SCREEN = {
    "w3": "_T1_窗3_自证.py",
    "wI": "_急停弹窗判据.py",
    "P0": "_P0_自指保护判据.py",
    "端到端": "_急停端到端判据.py",
}
NOT_WIRED_OK = {  # 允许"不接 run_all"，但必须给理由（这里写明；元2 也会核）
    "端到端": "要真 A+B 通行证 + 真桌面 + 真拉闸，不能进全量自动跑（人工/受控执行）",
}

rec = []
fails = []
verdicts = []


def rec(key, title, ok, detail="", skip=False):
    verdicts.append((key, title, "验不了" if skip else ("绿" if ok else "红"), detail))
    print("  [%s] %s%s" % ("验不了" if skip else ("绿" if ok else "红"), title,
                           ("  ← " + detail) if detail else ""), flush=True)


def load_criteria_keys():
    p = os.path.join(HERE, "run_all.py")
    t = io.open(p, encoding="utf-8", errors="replace").read()
    keys = set(re.findall(r'\(\s*"([^"]+)"\s*,\s*"[^"]+\.py"', t))
    return keys


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def parse_manifest():
    """从 manifest 表格里抓 {判据: [(sha, png_rel)]}。"""
    t = io.open(MANIFEST, encoding="utf-8", errors="replace").read()
    rows = {}
    for ln in t.splitlines():
        if not ln.startswith("| **"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 8:
            continue
        # 单元格形如 `**w3** \`_T1_窗3_自证.py\`` ⇒ 去掉 markdown 记号再取第一个词
        key = re.sub(r"[`*]", "", cells[0]).strip().split()[0]
        sha = cells[6].strip("` ")
        png = cells[5].strip("` ")
        rows.setdefault(key, []).append((sha, png))
    return t, rows


def main():
    ap = argparse.ArgumentParser(description="门禁 3：通道保真判据")
    ap.add_argument("--list", action="store_true", help="只打印枚举，不判")
    a = ap.parse_args()

    if a.list:
        for k, f in SCREEN.items():
            print("%-6s %s" % (k, f))
        return 0

    keys = load_criteria_keys()

    # [1] 枚举完整
    print("[1] 枚举完整（屏幕类判据一个不漏，且都在跑）")
    for k, f in SCREEN.items():
        p = os.path.join(HERE, f)
        if not os.path.isfile(p):
            rec("1", "屏幕类判据 %s 的文件存在（%s）" % (k, f), False, "文件不在")
            continue
        rec("1", "屏幕类判据 %s 的文件存在（%s）" % (k, f), True)
        if k in keys:
            rec("1", "屏幕类判据 %s **接在 run_all.CRITERIA 里**" % k, True)
        elif k in NOT_WIRED_OK:
            rec("1", "屏幕类判据 %s 明写不接 run_all（理由：%s）" % (k, NOT_WIRED_OK[k][:40]), True,
                "已登记理由，不算漏接")
        else:
            rec("1", "屏幕类判据 %s **接在 run_all.CRITERIA 里**" % k, False,
                "既没接、也没登记理由 ⇒ 屏幕类判据漏跑")

    # [2] manifest 齐全
    if not os.path.isfile(MANIFEST):
        rec("2", "今天的通道保真 manifest 存在（%s）" % os.path.basename(MANIFEST), False,
            "还没采集 ⇒ 跑：python -X utf8 work\\_通道保真_采集.py", skip=True)
        return finish()
    if not os.path.isfile(COLLECTOR):
        rec("2", "采集工具在场（work\\_通道保真_采集.py）", False, "工具不在 ⇒ 无法复现", skip=True)
        return finish()

    txt, rows = parse_manifest()
    rec("2", "manifest 里每条屏幕类判据都有一行", set(rows) >= set(SCREEN),
        "缺：%s" % (set(SCREEN) - set(rows)) if set(rows) < set(SCREEN) else "%d 条齐" % len(rows))
    extra = set(rows) - set(SCREEN)
    rec("2", "manifest 里没有屏幕类之外的判据混进来", not extra, "多出：%s" % (extra or "无"))

    ok_sha = True
    ok_hex = True
    for k, items in rows.items():
        for sha, png in items:
            if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
                ok_hex = False
                print("    红·%s 的 sha256 不是 64 位十六进制：%r" % (k, sha))
                continue
            full = os.path.join(ROOT, png.replace("/", os.sep))
            if not os.path.isfile(full):
                ok_sha = False
                print("    红·%s 的截图不存在：%s" % (k, png))
                continue
            if sha256_file(full) != sha:
                ok_sha = False
                print("    红·%s 的 sha256 重算不一致：%s" % (k, png))
    rec("2", "每行都带 64 位 sha256", ok_hex)
    rec("2", "截图文件真实存在，且 **sha256 重算一致**（截图即证据）", ok_sha)

    # [3] 规程在场
    print("[3] 采摘规程在场")
    need = ["采摘规程", "ImageGrab", "sha256", "API 读数只作辅助", "A 级", "B 级"]
    missing = [w for w in need if w not in txt]
    rec("3", "manifest 写明采摘规程（API/时机/哈希/分级/「API 只作辅助」）",
        not missing, ("缺：%s" % "、".join(missing)) if missing else "要素齐")

    return finish()


def finish():
    print()
    print("=" * 70)
    red = [v for v in verdicts if v[2] == "红"]
    skip = [v for v in verdicts if v[2] == "验不了"]
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(verdicts) - len(red) - len(skip), len(red), len(skip)))
    for k, t, s, d in verdicts:
        if s != "绿":
            print("  [%s] %s —— %s" % (s, t, d))
    if red:
        print("结论：【红】通道保真不成立")
        return 1
    if skip:
        print("结论：【验不了】—— 有检查项这次没做成（原因见上）；★ 不许读成绿")
        return 2
    print("结论：【绿】枚举完整 + manifest 齐全（截图↔sha256 重算一致）+ 规程在场")
    return 0


if __name__ == "__main__":
    sys.exit(main())
