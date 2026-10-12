# -*- coding: utf-8 -*-
"""从老线真源生成 legacy_tools（打包源），不再手工维护。

T14 阶段 2。设计要点：

1. **老线是唯一真源**（`dsh-viewer-plugin\\tools`）。功能一律取老线全量版，
   打包版缺的 T2/T3/T4/T5 整代功能与 targets 检查随之补齐。
2. **净化只有一类**：把写死的本机绝对路径改成环境变量可注入。
   实测老线代码行里只有 7 处绝对路径，全部列在 `RULES` 里，逐条精确替换。
   替换不上就**报错退出** —— 静默跳过会让人以为打包干净了。
3. 阶段 1 裁决为「废」的三段（`_force_foreground` 强抢前台、`approve_target`
   第二套放行账本、auth_popup 文案差异）**不得搬进来**，那等于把老线的
   fail-closed 拆掉。以老线为准天然就不会有，无需额外排除。
4. 落一份**源→包 md5 映射台账**，随包留档。

用法：
    python -X utf8 gen_legacy.py --out <目录> --verify <现有 legacy_tools 目录>
    python -X utf8 gen_legacy.py --out <目录> --dry-run
"""
import argparse
import hashlib
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = r"<工作区>\Documents\dsh-viewer-plugin\tools"

# 要搬的文件（与 pack_dist.py 的过滤口径对齐）
# ★ D18：`gate_lock.py` 必须在这里登记 —— 它是 gate.py / auth_popup.py 的**伴生模块**
#   （跨进程锁的唯一定义处），不登记的话包内缺件 ⇒ 两个脚本 import 失败。
#   注意**不能**加前导下划线：pack_dist.guard 会把 `_` 开头的当临时脚本丢掉。
FILES = ["gate.py", "gate_lock.py", "act.py", "perceive.py", "auth_popup.py", "brake.py",
         "cdp.py", "coord.py", "option_locator.py", "selftest.py", "guardian.py",
         "whitelist.json", "click_option.py", "formfill.py", "padd2.py",
         "preflight.py", "next_question.py", "minidump_probe.py",
         "minidump_stack.py", "pe_export_lookup.py", "radio_finder.py",
         "win_list.py", "zoomgrid.py",
         # ↓ 老线 act.py 的 load_post_compat() 要读它，打包缺了就是断链。
         #   旧 legacy_tools 里**没有**这个文件 —— T14 阶段 2 顺手补上。
         "post_compat.json",
         "probe.py", "probe2.py", "probe3.py", "probe4.py", "probe5.py", "probe6.py",
           # ↓ T17 A3-(a)（2026-10-12 用户拍板）：动作可信闭环三件。
           #   它们是【新线】工具，但用户选了"随 gen_legacy 走"这条单一机制 ——
           #   于是必须登记在这里，否则打包时取不到、装出去的那份里没有。
           #   ★ 配套改动（缺一不生效）：core.py::_verify_py() 要加 legacy_tools\ 候选，
           #     因为它们会落在 legacy_tools\ 而不是 tools\。
           "验动作.py", "闭环.py", "bench.py"]

# ── 净化规则：逐条精确替换，不做模糊匹配 ───────────────────────────────
# 每条 = (文件名, 旧文本, 新文本, 为什么)
APP_DIR_BLOCK = (
    "# ★ 默认写进 %LOCALAPPDATA%\\AgentCtl\\shots，不写死本机路径。\n"
    "#   平台进程会用 AGENTCTL_SHOTS_DIR（或直接注入 SHOTDIR）指过来，\n"
    "#   这里只是「独立跑这个脚本时」的兜底，指向同一个地方。\n"
    "_APP_DIR = os.environ.get(\"AGENTCTL_APP_DIR\") or os.path.join(\n"
    "    os.environ.get(\"LOCALAPPDATA\", os.path.expanduser(\"~\")), \"AgentCtl\")\n"
)

RULES = [
    ("perceive.py",
     'SHOTDIR = r"<工作区>\\Documents\\agent-manual\\_监控\\shots"',
     APP_DIR_BLOCK + 'SHOTDIR = os.environ.get("AGENTCTL_SHOTS_DIR") or os.path.join(_APP_DIR, "shots")',
     "截图目录：不写死本机路径"),
    ("perceive.py",
     'DIFF_CACHE_DIR = r"<工作区>\\Documents\\agent-manual\\_监控\\perceive_cache"',
     'DIFF_CACHE_DIR = os.environ.get("AGENTCTL_CACHE_DIR") or os.path.join(_APP_DIR, "perceive_cache")',
     "树差量缓存目录：同上"),
    ("selftest.py",
     'SHOTS = r"<工作区>\\Documents\\agent-manual\\_监控\\shots"',
     APP_DIR_BLOCK + 'SHOTS = os.environ.get("AGENTCTL_SHOTS_DIR") or os.path.join(_APP_DIR, "shots")',
     "自测读截图目录：同上"),
    ("cdp.py",
     '"--user-data-dir=F:\\\\<用户>\\\\Documents\\\\_edge_cdp"',
     '"--user-data-dir=" + os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl", "_edge_cdp")',
     "浏览器 profile：不写死本机路径"),
    ("coord.py",
     'import sys; sys.path.insert(0, r"<工作区>\\Documents\\dsh-viewer-plugin\\tools")',
     'import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))',
     "自插老线路径：包内要指向自己"),
    ("option_locator.py",
     'r"<工作区>\\Documents\\agent-manual\\_监控\\shots\\*_win2754900.png"',
     'os.path.join(os.environ.get("AGENTCTL_SHOTS_DIR", ""), "*_win2754900.png")',
     "选图目录：同上"),
    ("selftest.py",
     'os.path.join(r"<工作区>\\Documents\\agent-manual", f)',
     'os.path.join(os.environ.get("AGENTCTL_MANUAL_DIR", ""), f)',
     "手册目录（铁律 10 的页脚自测）：不写死本机路径；打包后没有手册就整段跳过"),
]


def md5(b):
    return hashlib.md5(b).hexdigest()


def apply_rules(name, data):
    """对该文件应用净化规则。返回 (新 bytes, [(规则说明, 是否命中)])。"""
    hit = []
    try:
        txt = data.decode("utf-8")
    except UnicodeDecodeError:
        return data, [(r[3], "跳过（非 utf-8）") for r in RULES if r[0] == name]
    for fn, old, new, why in RULES:
        if fn != name:
            continue
        if old in txt:
            txt = txt.replace(old, new)
            hit.append((why, "命中"))
        else:
            # 已净化过也算通过 —— 生成器要能对老线重跑而不报错
            if new.split("\n")[-1] in txt or "AGENTCTL" in txt:
                hit.append((why, "已是净化态（跳过）"))
            else:
                hit.append((why, "★未命中★"))
    data = txt.encode("utf-8")

    # 净化可能把硬编码串换成 os.environ.get(...) —— 那就凭空多出一个 os 用法。
    # 真源里 os 只在别的函数内 import 过（本函数用不到），所以这里就地补齐，
    # 否则包一装上就跑不了（NameError）。只补 os/subprocess 这两个净化会用到的。
    data, added = fix_missing_imports(data)
    if added:
        hit.append(("净化引入的 os/subprocess 用法：就地补 import %s" % "、".join(added), "已补"))
    return data, hit


def fix_missing_imports(data):
    """函数内用到 os/subprocess 却没导入的，就地补一行 import。

    用 AST 定位「哪个函数用了、缺什么」，再按行号插回源码 —— 不用正则猜作用域。
    找不到需要补的（绝大多数文件）时**原样返回，一个字节都不动**。
    """
    import ast

    try:
        src = data.decode("utf-8")
        tree = ast.parse(src)
    except (UnicodeDecodeError, SyntaxError):
        return data, []

    top_imported = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            top_imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            top_imported.add(n.module.split(".")[0])

    L = src.splitlines()
    inserts = []   # (行号, 需要补的模块名)
    added = []
    for n in tree.body:
        if not isinstance(n, ast.FunctionDef):
            continue
        # ★ 必须**两遍**：先把本函数里已有的 import 收齐，再看缺什么。
        #   一遍里同时 add/discard 是错的 —— ast.walk 是广度优先，函数体里
        #   import 的层级比深层表达式里的 Name 浅，于是 discard 先发生、add 后发生，
        #   结果「已导入」的名字又被当成「缺失」补一遍（2026-10-09 真踩过：
        #   cdp.py 两个函数各多出一组重复 import，pyflakes 报 redefinition）。
        already = set()
        for sub in ast.walk(n):
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                for a in sub.names:
                    already.add((a.asname or a.name).split(".")[0])
        want = set()
        for sub in ast.walk(n):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id in ("os", "subprocess") and sub.id not in top_imported:
                    want.add(sub.id)
        want -= already          # 本函数已经 import 过的，不再补
        if not want:
            continue
        # 插在**文档字符串之后**（否则 __doc__ 会变成 None），缩进与该函数一致
        first = n.body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            rest = n.body[1:]
            if not rest:
                continue          # 只有 docstring，用不到 os/subprocess，判据本身也矛盾
            first = rest[0]
        ln = getattr(first, "lineno", n.lineno) - 1
        indent = " " * (len(L[ln]) - len(L[ln].lstrip())) if ln < len(L) else "    "
        inserts.append((ln, indent, sorted(want)))
        added.extend(sorted(want))

    if not inserts:
        return data, []
    for ln, indent, names in sorted(inserts, reverse=True):
        for m in names:
            L.insert(ln, "%simport %s" % (indent, m))
    return ("\n".join(L) + ("\n" if src.endswith("\n") else "")).encode("utf-8"), sorted(set(added))


def check_parallel_copies():
    """反平行副本检查：`tools\\` 下若出现与 FILES 同名的文件，直接报错退出。

    ★ C3（2026-10-12）。为什么这是**主防线**而不只是顺手清理：
      1. `.gitignore` 里是 `tools/*` + 少量 `!` 白名单，所以白名单一撤，
         gen_legacy 重建出来的文件会在 `git status` 里**隐形** —— 改动不再被人看见。
      2. 历史上 `tools\\` 长期留着 `验动作.py` / `闭环.py` 两份 **tracked** 副本，
         那就是**第二真源**，而第二真源正是这个项目反复出病的根。
      3. 撤白名单只是拆引信；只要 tools\\ 下还能再出现 FILES 同名文件，
         「顺手改一份、忘了回写真源」的老病就会复发。
    ⇒ 所以生成前先扫一遍，发现就停下 —— 宁可不发包，也不要安静地生成第二份。
    """
    hits = []
    for f in FILES:
        p = os.path.join(ROOT, "tools", f)
        if os.path.exists(p):
            hits.append(p)
    return hits


def _report_parallel_copies(hits):
    print()
    print("[X] 反平行副本检查：tools\\ 下存在与 FILES 同名的文件 —— 拒绝生成。")
    for p in hits:
        print("      - %s" % p)
    print("    这些名字的真源是 %s，产物应落在 legacy_tools\\（生成物，不进仓）。" % SRC)
    print("    在 tools\\ 下再留一份 = 第二真源。请删掉这些文件后重跑。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "legacy_gen"))
    ap.add_argument("--verify", help="与该目录逐字节对比（自证模式）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不落盘")
    a = ap.parse_args()

    print("源（老线真源）: %s" % SRC)
    print("输出          : %s" % a.out)
    print()

    # ★ C3：反平行副本检查（生成前第一道闸）
    dup = check_parallel_copies()
    if dup:
        _report_parallel_copies(dup)
        return 1

    ledger = ["# 源→包 md5 映射台账", "",
              "- 生成器：`tools\\gen_legacy.py`（老线真源 → 打包源，自动生成，勿手工改）",
              "- 净化只做一件事：绝对路径 → 环境变量可注入", "",
              "| 文件 | 源 md5 | 源字节 | 包 md5 | 包字节 | 净化 |",
              "|---|---|---:|---|---:|---|"]
    problems = []
    made = {}

    for f in FILES:
        src = os.path.join(SRC, f)
        if not os.path.exists(src):
            print("  [缺失] %s（老线没有，跳过）" % f)
            problems.append(f)
            continue
        raw = open(src, "rb").read()
        data, hits = apply_rules(f, raw)
        desc = "；".join("%s=%s" % (w, s) for w, s in hits) if hits else "无规则"
        for w, s in hits:
            if s == "★未命中★":
                problems.append("%s: %s" % (f, w))
        ledger.append("| `%s` | `%s` | %d | `%s` | %d | %s |"
                      % (f, md5(raw), len(raw), md5(data), len(data), desc))
        made[f] = data
        flag = "★未命中★" in desc and "无规则" not in desc
        print("  %s %-20s %6d → %6d B  %s"
              % ("[X]" if flag else "[OK]", f, len(raw), len(data), desc))

    if problems:
        print()
        print("[X] 有 %d 处问题：" % len(problems))
        for p in problems:
            print("    - %s" % p)

    if not a.dry_run:
        os.makedirs(a.out, exist_ok=True)
        for f, data in made.items():
            open(os.path.join(a.out, f), "wb").write(data)
        led = os.path.join(a.out, "_md5台账.md")
        open(led, "w", encoding="utf-8").write("\n".join(ledger) + "\n")
        print()
        print("已写出 %d 个文件 + md5 台账 → %s" % (len(made), a.out))

    # 自证模式：与现有手工维护版逐字节对比
    if a.verify:
        print()
        print("=" * 70)
        print("自证：生成结果 vs 现有 %s" % a.verify)
        print("=" * 70)
        same = diff = only_new = only_old = 0
        for f, data in sorted(made.items()):
            op = os.path.join(a.verify, f)
            if not os.path.exists(op):
                print("  [新增] %-20s 现有目录里没有" % f)
                only_new += 1
                continue
            od = open(op, "rb").read()
            if od == data:
                same += 1
            else:
                diff += 1
                print("  [不同] %-20s 现有 %d B / 生成 %d B  md5 %s vs %s"
                      % (f, len(od), len(data), md5(od)[:12], md5(data)[:12]))
        for f in sorted(os.listdir(a.verify)):
            fp = os.path.join(a.verify, f)
            if os.path.isfile(fp) and f not in made and not f.startswith("_"):
                print("  [仅现有] %-18s 本次未生成" % f)
                only_old += 1
        print()
        print("  逐字节相同 %d / 不同 %d / 仅生成 %d / 仅现有 %d" % (same, diff, only_new, only_old))

    return 1 if problems else 0


def main_for_pack(out_dir):
    """给 pack_dist.py 调用的入口：生成到 out_dir，有未命中规则就返回非 0。

    与 main() 的区别只是不做 verify（打包时没有"现有手工版"可比），
    但**未命中规则一律当作失败** —— 净化规则失效还照发包，等于把本机路径发出去。
    """
    print("从老线真源生成 legacy 打包源：%s" % SRC)
    # ★ C3：同一条检查，打包路径上也要有（pack_dist 走的是这个入口）
    dup = check_parallel_copies()
    if dup:
        _report_parallel_copies(dup)
        return 1
    os.makedirs(out_dir, exist_ok=True)
    ledger = ["# 源→包 md5 映射台账", "",
              "- 生成器：`tools\\gen_legacy.py`（老线真源 → 打包源，自动生成，勿手工改）",
              "- 净化只做一件事：绝对路径 → 环境变量可注入", "",
              "| 文件 | 源 md5 | 源字节 | 包 md5 | 包字节 | 净化 |",
              "|---|---|---:|---|---:|---|"]
    problems = []
    n = 0
    for f in FILES:
        src = os.path.join(SRC, f)
        if not os.path.exists(src):
            problems.append("%s：老线没有这个文件" % f)
            continue
        raw = open(src, "rb").read()
        data, hits = apply_rules(f, raw)
        desc = "；".join("%s=%s" % (w, s) for w, s in hits) if hits else "无规则"
        for w, s in hits:
            if s == "★未命中★":
                problems.append("%s：%s 未命中" % (f, w))
        open(os.path.join(out_dir, f), "wb").write(data)
        ledger.append("| `%s` | `%s` | %d | `%s` | %d | %s |"
                      % (f, md5(raw), len(raw), md5(data), len(data), desc))
        n += 1
    open(os.path.join(out_dir, "_md5台账.md"), "w", encoding="utf-8").write("\n".join(ledger) + "\n")
    print("  生成 %d 个文件 + md5 台账" % n)
    if problems:
        print("  [X] %d 处问题：" % len(problems))
        for p in problems:
            print("      - %s" % p)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
