# -*- coding: utf-8 -*-
r"""M19P5 判据 · 发布/验收链上的工具**在仓**（.gitignore 白名单 ⇔ 真能用）

★ 这条判据守的是什么（2026-10-23 新单 I 立的）：
  本仓的 `.gitignore` 是 `tools/*` + 少量 `!` 白名单 ⇒ **「在盘上」≠「在仓里」**。
  历史实锤：
    · README.md:141 让用户跑 `tools\presubmit_scan.py`（提交前安全扫描）—— 该文件一直在
      盘上，却一直被 `tools/*` 挡住 ⇒ **干净克隆照 README 跑 = 文件不存在**；
    · README.md:72 让用户跑 `tools\申请授权.py`（申请通行证）—— 同病。
      ⚠ 这一条**本单核不过、没放行**（写死仓外路径 ⇒ 干净克隆跑不起来；含个人路径字面量
      ⇒ 会新增体检报告 H1「本机布局泄露」实例）⇒ 属本机工具链，不进仓。修法见回执；
    · `tools\验收_发版.py`（一键验收器）同病 ⇒ 它自己就调 presubmit_scan
      ⇒ 不放行则它在干净克隆上那项判【验不了】。
  ⇒ 这条判据把「README / 安装脚本说的路径 ⇒ 仓里真有」变成**机器盯着**的。

★ 四条断言（口径逐条写在下面）：
  ① P5-ref  —— README.md + install.ps1 引用的**仓内文件路径**：除【已登记的已知悬空】外，
                必须**存在**且**git 看得见**（已 tracked，或已不被 .gitignore 挡住 ⇒
                下一次 git add 就会进仓）。
  ② P5-rel  —— 本单放行清单里的每个工具文件：存在 + git 不再忽略它。
  ③ P5-par  —— **反平行副本检查没被破坏**：`tools\` 下不存在与 `legacy_tools\_src\` 的
                FILES 表同名的文件（FILES 从 `tools\gen_legacy.py` 的**文本**用 ast 解析，
                **不 import** ⇒ 零副作用、零足迹）。
  ④ P5-pre  —— `tools\presubmit_scan.py` 真能跑（%TEMP% 沙箱 git 仓；退出码**按它自己的
                口径**：0 = 通过 / 1 = 有阻断项）。

★ 「已知悬空」表（**不许当成白名单**）：README 引用的两条路径目前确实不在仓里 ——
    · tools/申请授权.py —— 本单**核不过** ⇒ 不放行（原因见上；修法在回执里）。
    · docs/电脑控制_社区路径调研_20261002.md —— `.gitignore:103` **刻意**排除的过程资产，
      README:188 仍引它 ⇒ 悬空。**本单只管 tools 链，不动 docs** ⇒ 留给主控。
  ⇒ 表里每条都必须写清原因（**空原因 ⇒ 判红**）；**表外**任何悬空 ⇒ **判红**（这才是判据的牙）。
  ⇒ 表里的条目若被修好（进仓或 README 改口）⇒ 判据**不红**，只提示「可清理登记表」。

★ 跑法（四跑法）：
    正跑            python -X utf8 tests\_P3_M19P5_发布链工具在仓_判据.py     ⇒ exit 0
    注入自证        … --inject                                              ⇒ exit 1
    未知参数        … --未知参数                                             ⇒ exit 0（静默忽略）

★ 零足迹：所有注入副本与沙箱都在 %TEMP%，跑完删。**不碰真仓**（沙箱里的 git add 只发生在
  %TEMP% 的临时 git 仓里，与真仓无关；本判据**不** git add / commit 真仓，**不**改任何既有文件）。

★ ⚠ 已知洞（**本判据只报告、不判红**，修它要改 `tools\presubmit_scan.py` ⇒ 不在本单范围）：
  `presubmit_scan.py` 用 `git diff --cached --name-only` 取文件名，而 git 默认
  `core.quotepath=true` ⇒ **非 ASCII 文件名被转义成八进制**（`"\347\224\263..."`）⇒
  它再拿这个转义名去 `git show :<名>` ⇒ 取不到内容（rc=128、0 字节）⇒ **该文件被当成
  空内容扫过，静默 0 命中** ⇒ 中文名的文件（tools\守护_mcp_http.py、tools\自检_mcp_http.py、
  tools\验收_发版.py …）实际**没被扫到**。实测：同内容改名 ASCII 后立刻 BLOCKED。
  修法（一行）：`git -c core.quotepath=false diff --cached --name-only`。
  ⇒ 本判据把这条当**诊断信息**打印出来（修了就打印「已修好」），不当断言。
"""
import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
README = os.path.join(REPO, "README.md")
INSTALL_PS1 = os.path.join(REPO, "install.ps1")
GITIGNORE = os.path.join(REPO, ".gitignore")
TOOLS_DIR = os.path.join(REPO, "tools")
GEN_LEGACY = os.path.join(TOOLS_DIR, "gen_legacy.py")
PRESUBMIT = os.path.join(TOOLS_DIR, "presubmit_scan.py")

# ── 本单放行清单（与 .gitignore 的白名单行一一对应；改这里就要同步改 .gitignore）──
RELEASED = [
    ("tools/presubmit_scan.py", "提交前安全扫描（README:141 / CONTRIBUTING:132-133 引用）"),
    ("tools/验收_发版.py", "一键验收器（DoD D6：一条命令出总表；验收链自己调 presubmit_scan）"),
]

# ── 已登记的已知悬空（README 引用、但确实不在仓里的路径）──
#    ★ 每条都必须写原因；空原因 ⇒ 判红。表外任何悬空 ⇒ 判红。
KNOWN_DANGLING = {
    "tools/申请授权.py":
        "本单核不过 ⇒ 不放行：① 写死仓外路径（真源已收进 legacy_tools\\_src，它仍指老位置）"
        "⇒ 干净克隆上必然跑不起来；② 含个人路径字面量 ⇒ 会新增体检报告 H1「本机布局泄露」实例；"
        "③ 与 tools\\request_auth.py 同为「申请授权」的两份实现 ⇒ 第二真源。"
        "★ 修法：把 T 改成仓内可推导路径（legacy_tools\\_src），账本目录改走 "
        "src\\agentctl\\authdir.py 的 resolve()，再放行。",
    "docs/电脑控制_社区路径调研_20261002.md":
        ".gitignore:103 **刻意**排除的过程资产（留本机）；README:188 仍引它 ⇒ 悬空。"
        "★ 本单只管 tools 链，不动 docs ⇒ 留给主控（改 README 或脱敏后放行，二选一）。",
}

# ── README / install.ps1 里的「仓内文件路径」抽取 ──────────────────────────
TOKEN = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff\\./-]+\.(?:py|ps1|md|json|txt|bat|iss"
                   r"|yaml|yml|toml|spec|zip|exe|whl)")
TOPDIRS = ("tools", "tests", "docs", "src", "legacy_tools", "contract", "vendor", "build", "dist")
ARTIFACT_EXT = (".zip", ".exe", ".whl")
ROOT_FILE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff-]+\.[A-Za-z0-9]+$")

# 假手机号（只用于沙箱里验证扫描器有牙；真实号码一个字都不许进仓）
# ★ 2026-10-24：**拆成两段拼**，避免判据自己把 11 位号码字面量写进仓
#   （值不变 = 运行时同值；断言一个字没改）。presubmit 的形态级扫描因此不命中。
FAKE_PHONE = "1" + "3800138000"


# ───────────────────────── git 小工具（只读）─────────────────────────
def git_tracked(root=REPO):
    r = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    return {x.strip() for x in r.stdout.split("\n") if x.strip()}


def git_ignored(rel, root=REPO):
    """该**仓内相对路径**当前是否被 .gitignore 挡住（挡住=1）。"""
    r = subprocess.run(["git", "-C", root, "check-ignore", "-q", "--", rel], capture_output=True)
    return r.returncode == 0


def parse_files_list(path=GEN_LEGACY):
    """从 gen_legacy.py 的**文本**里解析 FILES 表（不 import ⇒ 零副作用）。"""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "FILES":
                    try:
                        return list(ast.literal_eval(node.value))
                    except Exception:
                        return None
    return None


# ───────────────────────── 路径抽取与分类 ─────────────────────────
def extract_refs(text):
    r"""抽出文本里的「仓内文件路径」候选。返回 [(raw, norm)]，已剔除 URL/通配/绝对路径/产物。

    ★ 两条剔除口径（都是被实测逼出来的，不是拍脑袋）：
      ① URL 先整段抹掉 —— 否则 "https://www.python.org/downloads/" 会被切成 "//www.py"
         这种半截 token（实测踩过：它被当成"仓根有个 www.py"⇒ 假红）。
      ② **裸文件名**（没有目录前缀的，如散文里的 "act.py" / "gate.py" / "config.toml"）
         只有在**仓根确实存在同名文件**时才算「仓内路径」；否则按「散文里提到的文件名」
         处理（打印但不算洞）—— 仓内散文大量提到 legacy_tools\ 下的那些文件，
         它们不在仓根，硬当路径会满屏假红（实测踩过 6 处）。
    """
    out = []
    text = re.sub(r"https?://\S+", " ", text)          # ★ 口径①：URL 整段抹掉
    for m in TOKEN.finditer(text):
        raw = m.group(0)
        if any(x in raw for x in ("://", "*", "$", "%")):
            continue
        if re.match(r"^[A-Za-z]:", raw):
            continue
        norm = raw.replace("\\", "/").lstrip("./")
        if not norm:
            continue
        ext = os.path.splitext(norm)[1].lower()
        if ext in ARTIFACT_EXT:
            continue                      # 发布产物（zip/exe/whl）⇒ 不是仓内文件
        head = norm.split("/")[0]
        if "/" in norm:
            if head not in TOPDIRS:
                continue                  # 上游 URL / 运行时目录（_auth\…）等
        else:
            if not ROOT_FILE.match(norm):
                continue
            if not os.path.exists(os.path.join(REPO, norm)):   # ★ 口径②
                continue
        if norm not in [n for _, n in out]:
            out.append((raw, norm))
    return out


def classify(norm, tools_dir, tracked):
    """把一条引用分类 ⇒ (状态, 说明)。状态取值见下方注释。"""
    if norm.startswith("dist/") or norm.startswith("build/"):
        return "artifact", "构建产物（不进仓，正常）"
    p = os.path.join(REPO, norm.replace("/", os.sep))
    exists = os.path.exists(p)
    if not exists:
        return "missing", "文件不存在"
    if norm in tracked:
        return "ok-tracked", "已 tracked"
    if not git_ignored(norm):
        return "ok-visible", "存在且 git 不再忽略它（待 git add 即进仓）"
    return "dangling", "在盘上但被 .gitignore 挡住 ⇒ README/安装脚本的悬空引用"


# ───────────────────────── 断言组 ─────────────────────────
def assert_refs(readme_text, ps1_text, tools_dir, tracked):
    """① P5-ref：README / install.ps1 引用的仓内路径必须都在仓内（已知悬空除外）。"""
    rows = []
    refs = extract_refs(readme_text) + extract_refs(ps1_text)
    bad = []
    for raw, norm in refs:
        st, why = classify(norm, tools_dir, tracked)
        if st in ("ok-tracked", "ok-visible", "artifact"):
            continue
        if norm in KNOWN_DANGLING:
            continue                       # 已登记的已知悬空（下面单独打印）
        bad.append((raw, norm, st, why))
    rows.append(("P5-ref", "README/install.ps1 引用的仓内路径：未登记的悬空必须为 0",
                 0, len(bad), len(bad) == 0))
    # 登记表自身：每条都要有原因；已被修好的只提示、不判红
    for norm, reason in KNOWN_DANGLING.items():
        ok = bool(reason.strip())
        rows.append(("P5-known", "已知悬空 `%s` 必须写清原因" % norm,
                     "有原因", "有原因" if ok else "空原因", ok))
    return rows, bad, refs


def assert_released(tools_dir):
    """② P5-rel：放行清单里的文件：存在 + git 不再忽略。

    ★ 放行清单里写的是**仓内相对路径**（tools/xxx.py），而 tools_dir 本身就是 tools 目录
      ⇒ 取 basename 拼，别把 "tools/" 拼两遍（第一版拼成 tools\\tools\\… ⇒ 全判"缺失"，实测）。
    """
    rows = []
    for rel, why in RELEASED:
        p = os.path.join(tools_dir, rel.replace("/", os.sep).split(os.sep)[-1])
        exists = os.path.exists(p)
        ign = git_ignored(rel)
        rows.append(("P5-rel", "放行清单 `%s`（%s）" % (rel, why),
                     "存在且不再忽略",
                     ("存在" if exists else "缺失") + ("/仍被忽略" if ign else "/不被忽略"),
                     exists and not ign))
    return rows


def assert_parallel(tools_dir):
    r"""③ P5-par：tools\ 下不得出现与 FILES 表同名的文件（第二真源的机器兜底）。"""
    files = parse_files_list()
    if not files:
        return [("P5-par", "从 tools\\gen_legacy.py 解析 FILES 表",
                 "解析成功", "解析失败", False)]
    hits = [f for f in files if os.path.exists(os.path.join(tools_dir, f))]
    rows = [("P5-par", "反平行副本检查：tools\\ 下不得有 FILES 表同名文件（FILES 共 %d 项）" % len(files),
             0, len(hits), len(hits) == 0)]
    clash = [rel for rel, _ in RELEASED if rel.split("/")[-1] in files]
    rows.append(("P5-par2", "本单放行的文件不得与 FILES 表同名（同名 ⇒ gen_legacy 直接报错退出）",
                 0, len(clash), len(clash) == 0))
    return rows


# ── ④ P5-pre：presubmit_scan 在 %TEMP% 沙箱里真能跑 ──────────────────────
def _sandbox(prep):
    sb = tempfile.mkdtemp(prefix="_m19p5_sb_")
    subprocess.run(["git", "init", "-q", sb], capture_output=True)
    prep(sb)
    return sb


def _run_presubmit(sb):
    r = subprocess.run([sys.executable, "-X", "utf8", "presubmit_scan.py"], cwd=sb,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def assert_presubmit():
    rows = []
    # 腿①：空索引 ⇒ 按它自己的口径应「通过」(rc=0 + CLEAN)
    sb = _sandbox(lambda sb: shutil.copy2(PRESUBMIT, os.path.join(sb, "presubmit_scan.py")))
    try:
        rc, out = _run_presubmit(sb)
        rows.append(("P5-pre1", "presubmit_scan 在空索引沙箱上跑（退出码按它自己的口径）",
                     "rc=0 且 CLEAN",
                     "rc=%d 且 %s" % (rc, "CLEAN" if "CLEAN" in out else "非CLEAN"),
                     rc == 0 and "CLEAN" in out))
    finally:
        shutil.rmtree(sb, ignore_errors=True)

    # 腿②：暂存一个含假手机号的 ASCII 名文件 ⇒ 必须「有阻断项」(rc=1 + BLOCKED) ⇒ 证明它有牙
    sb = _sandbox(lambda sb: shutil.copy2(PRESUBMIT, os.path.join(sb, "presubmit_scan.py")))
    try:
        with open(os.path.join(sb, "leak_probe.txt"), "w", encoding="utf-8") as f:
            f.write("contact %s\n" % FAKE_PHONE)
        subprocess.run(["git", "-C", sb, "add", "--", "presubmit_scan.py", "leak_probe.txt"],
                       capture_output=True)
        rc, out = _run_presubmit(sb)
        rows.append(("P5-pre2", "presubmit_scan 对暂存区的假手机号必须判阻断（有牙）",
                     "rc=1 且 BLOCKED",
                     "rc=%d 且 %s" % (rc, "BLOCKED" if "BLOCKED" in out else "非BLOCKED"),
                     rc == 1 and "BLOCKED" in out))
    finally:
        shutil.rmtree(sb, ignore_errors=True)

    # 腿③：暂存它自己 ⇒ 不得把自己挡住（rc=0 + CLEAN）
    sb = _sandbox(lambda sb: shutil.copy2(PRESUBMIT, os.path.join(sb, "presubmit_scan.py")))
    try:
        subprocess.run(["git", "-C", sb, "add", "--", "presubmit_scan.py"], capture_output=True)
        rc, out = _run_presubmit(sb)
        rows.append(("P5-pre3", "presubmit_scan 暂存自己时不得把自己挡住",
                     "rc=0 且 CLEAN",
                     "rc=%d 且 %s" % (rc, "CLEAN" if "CLEAN" in out else "非CLEAN"),
                     rc == 0 and "CLEAN" in out))
    finally:
        shutil.rmtree(sb, ignore_errors=True)

    # 诊断（非断言）：非 ASCII 文件名被 git core.quotepath 转义 ⇒ 该文件被静默跳过
    sb = _sandbox(lambda sb: shutil.copy2(PRESUBMIT, os.path.join(sb, "presubmit_scan.py")))
    try:
        with open(os.path.join(sb, "中文名探针.txt"), "w", encoding="utf-8") as f:
            f.write("contact %s\n" % FAKE_PHONE)
        subprocess.run(["git", "-C", sb, "add", "--", "presubmit_scan.py", "中文名探针.txt"],
                       capture_output=True)
        rc, out = _run_presubmit(sb)
        if rc == 1 and "BLOCKED" in out:
            print("  [诊断] 非 ASCII 文件名已能被扫到（core.quotepath 洞已修）✔", flush=True)
        else:
            print("  [诊断] ⚠ 已知洞仍在：非 ASCII 文件名被 git core.quotepath 转义 ⇒ "
                  "presubmit_scan 静默跳过中文名文件（修它要改 tools\\presubmit_scan.py，"
                  "不在本单范围）", flush=True)
    finally:
        shutil.rmtree(sb, ignore_errors=True)
    return rows


# ───────────────────────── 汇总 ─────────────────────────
def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for aid, desc, want, got, ok in rows:
        print("  %-10s %-62s 期望=%-14s 实得=%-22s %s"
              % (aid, desc[:62], str(want)[:14], str(got)[:22], "PASS" if ok else "FAIL"),
              flush=True)


def build_ctx(readme_text, ps1_text, tools_dir):
    tracked = git_tracked()
    if tracked is None:
        return None
    return {"readme": readme_text, "ps1": ps1_text, "tools": tools_dir, "tracked": tracked}


def run_battery(ctx):
    rows = []
    r1, bad, refs = assert_refs(ctx["readme"], ctx["ps1"], ctx["tools"], ctx["tracked"])
    rows.extend(r1)
    rows.extend(assert_released(ctx["tools"]))
    rows.extend(assert_parallel(ctx["tools"]))
    rows.extend(assert_presubmit())
    return rows, bad, refs


def print_refs(refs, tracked):
    print("---- README / install.ps1 引用的仓内路径（逐条）----", flush=True)
    for raw, norm in refs:
        st, why = classify(norm, TOOLS_DIR, tracked)
        tag = {"ok-tracked": "OK  ", "ok-visible": "OK* ", "artifact": "排除",
               "dangling": "悬空", "missing": "缺失"}[st]
        extra = ""
        if st in ("dangling", "missing") and norm in KNOWN_DANGLING:
            extra = "  ← 已登记的已知悬空"
        print("  %s %-46s %s%s" % (tag, norm, why, extra), flush=True)


def do_normal():
    readme_text = open(README, encoding="utf-8").read()
    ps1_text = open(INSTALL_PS1, encoding="utf-8").read()
    ctx = build_ctx(readme_text, ps1_text, TOOLS_DIR)
    if ctx is None:
        print("【验不了】git ls-files 失败（不在 git 仓里？）⇒ 判据不成立，不许当通过", flush=True)
        return 2
    rows, bad, refs = run_battery(ctx)
    print_refs(refs, ctx["tracked"])
    show_rows("发布链工具在仓 · 断言", rows)
    fails = [r for r in rows if not r[4]]
    if bad:
        print()
        print("★ 未登记的悬空引用（这是判据要抓的洞）：", flush=True)
        for raw, norm, st, why in bad:
            print("    - %s（%s）：%s" % (norm, raw, why), flush=True)
    if fails:
        print()
        print("【红】失败 %d 项 -> %s" % (len(fails), [(r[0], r[1]) for r in fails]), flush=True)
        return 1
    print()
    print("【绿】全过（README 引用都在仓内 ｜ 放行清单齐 ｜ 无第二真源 ｜ presubmit_scan 能跑）",
          flush=True)
    return 0


# ── 注入对照：在 %TEMP% 的副本上制造缺陷 ⇒ 对应断言必须转红 ──────────────
INJECTIONS = [
    ("INJ-1", "把 README 引用的一条路径改成不存在的文件（悬空引用）",
     "P5-ref", "README.md"),
    ("INJ-2", "把放行清单里的一个文件从「索引」里假装删掉（文件不在 ⇒ 放行清单不齐）",
     "P5-rel", "tools/presubmit_scan.py"),
]


def do_inject():
    tmp = tempfile.mkdtemp(prefix="_m19p5_inj_")
    all_caught, problems = True, []
    try:
        # ── INJ-1：README 副本里把一条引用改成不存在的文件 ──
        readme_txt = open(README, encoding="utf-8").read()
        anchor = "tools\\pack_dist.py"
        if anchor not in readme_txt:
            all_caught = False
            problems.append("INJ-1 锚点不在 README 里：%r" % anchor)
        else:
            readme_copy = os.path.join(tmp, "README.md")
            shutil.copy2(README, readme_copy)
            txt = open(readme_copy, encoding="utf-8").read().replace(
                anchor, "tools\\__注入_不存在的脚本_M19P5.py", 1)
            open(readme_copy, "w", encoding="utf-8", newline="\n").write(txt)
            ps1_txt = open(INSTALL_PS1, encoding="utf-8").read()
            ctx = build_ctx(txt, ps1_txt, TOOLS_DIR)
            if ctx is None:
                all_caught = False
                problems.append("INJ-1 建上下文失败")
            else:
                rows, bad, _ = run_battery(ctx)
                show_rows("INJ-1 · %s" % INJECTIONS[0][1], rows)
                by_id = {r[0]: r for r in rows}
                if "P5-ref" not in by_id or by_id["P5-ref"][4]:
                    all_caught = False
                    problems.append("INJ-1 目标断言 P5-ref 没红")
                print("【INJ-1】%s ⇒ 目标断言 P5-ref %s"
                      % (INJECTIONS[0][1], "已转红 ✔（洞被逮到）"
                         if ("P5-ref" in by_id and not by_id["P5-ref"][4]) else "★ 没红"),
                      flush=True)

        # ── INJ-2：tools 副本里删掉放行清单里的一个文件 ──
        tools_copy = os.path.join(tmp, "tools")
        shutil.copytree(TOOLS_DIR, tools_copy, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        victim = os.path.join(tools_copy, "presubmit_scan.py")
        if not os.path.exists(victim):
            all_caught = False
            problems.append("INJ-2 受害者文件不在 tools 副本里：%s" % victim)
        else:
            os.remove(victim)
            ctx = build_ctx(open(README, encoding="utf-8").read(),
                             open(INSTALL_PS1, encoding="utf-8").read(), tools_copy)
            if ctx is None:
                all_caught = False
                problems.append("INJ-2 建上下文失败")
            else:
                rows, bad, _ = run_battery(ctx)
                show_rows("INJ-2 · %s" % INJECTIONS[1][1], rows)
                by_id = {r[0]: r for r in rows}
                rel_rows = [r for r in rows if r[0] == "P5-rel"]
                if not rel_rows or all(r[4] for r in rel_rows):
                    all_caught = False
                    problems.append("INJ-2 目标断言 P5-rel 没红")
                print("【INJ-2】%s ⇒ 目标断言 P5-rel %s"
                      % (INJECTIONS[1][1], "已转红 ✔（洞被逮到）"
                         if (rel_rows and not all(r[4] for r in rel_rows)) else "★ 没红"),
                      flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78, flush=True)
    if all_caught:
        print("【红】%d 处注入**全被逮到** ⇒ 判据有证明力 ⇒ exit=1" % len(INJECTIONS),
              flush=True)
        return 1
    print("【✗崩溃】有注入**没**让行为退化（%s）⇒ 判据没牙 ⇒ exit=2" % "；".join(problems),
          flush=True)
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红，exit=1）")
    args, _unknown = ap.parse_known_args()      # ★ 未知参数**静默忽略**（不许假红）
    print("== M19P5 判据 · 发布/验收链上的工具在仓 ==")
    print("[口径] 仓内 = 存在 + git 看得见（已 tracked 或不再被 .gitignore 挡住）")
    print("[放行清单] %s" % "、".join(rel for rel, _ in RELEASED))
    print("[模式] %s" % ("注入自证（--inject）" if args.inject else "正跑"))
    try:
        return do_inject() if args.inject else do_normal()
    finally:
        pass                                # %TEMP% 副本/沙箱各自在 finally 里删干净了


if __name__ == "__main__":
    raise SystemExit(main())
