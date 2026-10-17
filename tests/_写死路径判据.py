# -*- coding: utf-8 -*-
r"""_写死路径判据.py —— 写死路径白名单门禁（窗W · 2026-10-17 · 派单：DSH）

重跑命令：
    python -X utf8 tests\_写死路径判据.py                 # 验真（当前仓）
    python -X utf8 tests\_写死路径判据.py --inject        # 自证：注入必须红、未改必须绿
    python -X utf8 tests\_写死路径判据.py --out 文件      # 全量明细落盘

口径（与 work\_写死路径白名单_20261017.md §一 一致，正则/跳过清单照抄
work\_写死路径扫描_20261017.py——两处只许同步改，不许各漂各的）：
  · 规则 = 任意 `X:\…` 盘符绝对路径与 `\\` UNC；
  · 范围 = src/ tools/ tests/ contract/ verify/ build/ legacy_tools/ docs/ ＋ 仓根一层文件；
    跳过 .git/__pycache__/dist/node_modules 与二进制；work/ 不在代码面（留痕材料）。
  · 本判据**纯静态**：不建窗、不抢前台、不起子进程（--inject 只写 %TEMP% 副本）。

四类断言（任一红 ⇒ rc=1）：
  [1] 仓根清零（必要条件 2a）：tests/ src/ contract/ 下命中行含**本仓根路径** ⇒ 红。
      （tools/ 与 docs/证据/ 的仓根写死在白名单 B/D 类豁免，见白名单 §三/§六。）
  [2] 白名单外新命中：命中文件不落 A~G 七类 ⇒ 红点名（防偷偷加本机路径）。
  [3] 条数自证：实测命中文件数/行数 必须等于白名单文档 §二 写的数 —— 文档从本文件
      现场解析（单一真相源），文档漂了也是病；DSH 更新文档后本判据自动跟随，无需改码。
  [4] --inject 自证：仓内 0 命中文件复制到 %TEMP% 副本 —— 未改=绿；塞一行
      `F:\SomeMachine\x` = 红**并点名字**。证明检测器活着。

退出码：0=绿；1=红；2=环境不满足（拿不到仓/白名单文档/正则崩）——★ 2 必须带原因。
可移植性边界：[1] 的仓根判定用本机 ROOT（casefold），跨机器的老残留由
`_M_可移植性判据.py`（wM，复制仓到 %TEMP% 从副本实跑）负责，两判据分工不重叠。
"""
import argparse
import os
import re
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # tests\ 的上一级 = 仓根
WHITELIST_MD = os.path.join(ROOT, "work", "_写死路径白名单_20261017.md")
INJECT_DIR = os.path.join(tempfile.gettempdir(), "agentctl_zcode_wZ_inject")

# ── 口径（照抄 work\_写死路径扫描_20261017.py，两处只许同步改）─────────────
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", "dist"}
BIN_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".ico", ".zip", ".7z", ".exe", ".dll",
           ".pyd", ".pyc", ".pfx", ".cer", ".db", ".woff", ".woff2", ".ttf"}
PAT = re.compile(r"[A-Za-z]:\\[^\s\"'<>|，。；）)]{3,}|\\\\[A-Za-z0-9_.-]+\\")
SCOPE = ["src", "tools", "tests", "contract", "verify", "build", "legacy_tools", "docs"]
CLEAN_DIRS = ("tests", "src", "contract")         # 必要条件 2a：仓根写死必须清零的目录

RESULTS = []


def rec(key, desc, verdict, detail=""):
    RESULTS.append((key, desc, verdict))
    print("  [%s] %s%s" % (verdict, desc, ("  <- " + detail) if detail else ""))


def classify(rel):
    """A~G 七类（理由为白名单 §四 表格的短句）。返回 None = 白名单外。"""
    b = rel.replace("\\", "/")
    low = b.lower()
    name = b.rsplit("/", 1)[-1].lower()
    if ".bak" in name or "_bak_" in name or name.endswith("_bak"):
        return ("A", "备份回滚材料（收口后删除；§五保留 1 个作证据）")
    if "/证据/" in b:
        return ("B", "历史证据留档：记录当天在哪台机器跑出了什么，改了=篡改证据")
    # ★ 2026-10-17 修（DSH 亲跑发现）：这里原来用大小写敏感的 b 比 "readme.md"，
    #   而仓里那份叫 `README.md` ⇒ 每次都判「白名单外」。改用 low。
    if low == "readme.md" or low == "contract/tools.json" or low == "install.ps1" \
            or low == ".gitignore" or low.startswith("build/"):
        return ("G", "契约转义示例/安装器天职/忽略规则/打包配置")
    # ★ 2026-10-17（A 档：证据文档进仓）：说明/索引/判据清单里的盘符路径是**示例或历史引用**，
    #   运行期不读、也不参与构建 ⇒ 归 G 类（契约/文档）。新增文档不用再逐条改判据。
    if low.startswith("docs/"):
        return ("G", "文档里的示例/历史路径，运行期不读")
    if b.startswith("legacy_tools/"):
        return ("C", "生成产物：提示文案或产品默认值；受 CHK-A 守，要改必须改真源")
    if b.startswith("tools/"):
        return ("D", "一次性运维脚手架：不进 run_all、不被 import、不随包发")
    if b.startswith("tests/"):
        return ("E", "历史验收脚本的仓外夹具（真源/见证图有意放仓外）")
    if b.startswith("src/"):
        return ("F", "产品代码注释/帮助文案，运行期不读")
    return None


def scan(root, subdirs=None):
    """返回 {rel: [(行号, 命中串)]}。口径照抄扫描脚本。"""
    rows = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        if subdirs is not None and os.path.abspath(dp) == os.path.abspath(root):
            dns[:] = [d for d in dns if d in subdirs]   # 仓根只下钻 scope；仓根文件全算
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in BIN_EXT:
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            hits = []
            for i, line in enumerate(txt.splitlines(), 1):
                for m in PAT.finditer(line):
                    hits.append((i, m.group(0)[:90]))
            if hits:
                rows[rel] = hits
    return rows


def parse_expect():
    """从白名单文档 §二 现场解析期望值（单一真相源；文档漂了=红，不改码）。"""
    if not os.path.isfile(WHITELIST_MD):
        raise RuntimeError("白名单文档不存在：%s" % WHITELIST_MD)
    txt = open(WHITELIST_MD, encoding="utf-8", errors="replace").read()
    mf = re.search(r"\|\s*命中文件\s*\|\s*\*\*(\d+)\*\*", txt)
    ml = re.search(r"\|\s*命中行\s*\|\s*\*\*(\d+)\*\*", txt)
    if not mf or not ml:
        raise RuntimeError("白名单文档 §二 缺「命中文件/命中行」表格行（解析失败）")
    return int(mf.group(1)), int(ml.group(1))


def run_main():
    exp_f, exp_l = parse_expect()          # rc=2 异常在调用方兜
    hits = scan(ROOT, subdirs=SCOPE)
    n_files, n_lines = len(hits), sum(len(v) for v in hits.values())
    root_low = str(ROOT).casefold()

    print("[1] 仓根清零（必要条件 2a：%s 下命中行含本仓根 ⇒ 红）" % "/".join(CLEAN_DIRS))
    cz = []
    for rel in sorted(hits):
        top = rel.split("/", 1)[0]
        if top not in CLEAN_DIRS:
            continue
        for ln, s in hits[rel]:
            if root_low in s.casefold():
                cz.append((rel, ln, s))
    for rel, ln, s in cz:
        print("    红·清零 %s:%d  %s" % (rel, ln, s))
    rec("1", "仓根写死在 tests/src/contract 的残留 = 0", "绿" if not cz else "红",
        "%d 条" % len(cz) if cz else "实测 0 条")

    print("[2] 白名单归类（A~G）")
    unknown = []
    buckets = {}
    for rel in sorted(hits):
        c = classify(rel)
        if c is None:
            unknown.append(rel)
            for ln, s in hits[rel][:3]:
                print("    红·白名单外 %s:%d  %s" % (rel, ln, s))
        else:
            buckets.setdefault(c[0], 0)
            buckets[c[0]] += len(hits[rel])
    for k in sorted(buckets):
        print("    %s 类 %d 行" % (k, buckets[k]))
    rec("2", "命中文件全部落在白名单 A~G", "绿" if not unknown else "红",
        ("白名单外 %d 个：%s" % (len(unknown), "、".join(unknown[:6]))) if unknown
        else "%d 个文件全归类" % len(hits))

    print("[3] 条数自证（白名单文档 §二 = 单一真相源）")
    ok_f, ok_l = n_files == exp_f, n_lines == exp_l
    print("    白名单文档写：文件 %d / 行 %d" % (exp_f, exp_l))
    print("    实测扫出　　：文件 %d / 行 %d" % (n_files, n_lines))
    rec("3", "实测命中数 == 白名单文档 §二 所写", "绿" if (ok_f and ok_l) else "红",
        "文档漂了也是病：若 bak 删除/回灌重生成等已改变命中数，请 DSH 重跑扫描脚本并更新 §二")

    return 1 if (cz or unknown or not (ok_f and ok_l)) else 0


def run_inject():
    """自证：未改副本=绿；塞 F:\\SomeMachine\\x=红并点名。两种输出都打在本趟。"""
    src = os.path.join(ROOT, "tests", "run_all.py")     # 当前扫描 0 命中的仓内文件
    if not os.path.isfile(src):
        src = os.path.join(ROOT, "contract", "tools.json") if os.path.isfile(
            os.path.join(ROOT, "contract", "tools.json")) else src
    if not os.path.isfile(src):
        raise RuntimeError("找不到可复制的仓内文件（tests/run_all.py 不存在）")
    if os.path.isdir(INJECT_DIR):
        shutil.rmtree(INJECT_DIR)
    os.makedirs(INJECT_DIR)
    dst = os.path.join(INJECT_DIR, os.path.basename(src))
    shutil.copy2(src, dst)

    print("  [阶段1] 副本未改（%s）" % dst)
    h0 = scan(INJECT_DIR)
    ok0 = len(h0) == 0
    rec("I1", "未改副本命中=0（载体选对了：仓内 0 命中文件）", "绿" if ok0 else "红",
        "命中 %d 文件" % len(h0) if h0 else "0 命中")

    print("  [阶段2] 副本塞一行 F:\\SomeMachine\\x")
    with open(dst, "a", encoding="utf-8") as f:
        f.write('\nx_injected = "F:\\\\SomeMachine\\\\x"\n')
    h1 = scan(INJECT_DIR)
    # ★ 2026-10-17 修（ZCode 自测发现）：named 原来匹配 "F:\SomeMachine\x"（单反斜杠），
    #   而写进副本的是源码转义后的双反斜杠形态 ⇒ 字符串永不相等、注入明明被抓却判红。
    #   改按稳定子串 "SomeMachine" 匹配，并在 detail 里打印命中原文。
    named = [(rel, ln, s) for rel, v in h1.items() for ln, s in v if "SomeMachine" in s]
    rec("I2", "注入后被判红并点名 F:\\SomeMachine\\x", "绿" if (h1 and named) else "红",
        ("命中 %s" % ("；".join("%s:%d %s" % (r, l, s) for r, l, s in named[:3]))) if named
        else "命中 %d 文件 / %d 行" % (len(h1), sum(len(v) for v in h1.values())))

    shutil.rmtree(INJECT_DIR, ignore_errors=True)
    print("  [收尾] 注入副本已清（%s）" % INJECT_DIR)
    return 0 if (ok0 and h1 and named) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="写死路径白名单门禁（窗W）")
    ap.add_argument("--inject", action="store_true", help="自证模式：注入红/未改绿")
    ap.add_argument("--out", help="验真模式全量明细落盘")
    a = ap.parse_args(argv)

    print("=" * 78)
    print("写死路径门禁判据 · %s · 仓 %s" % (
        "inject 自证" if a.inject else "验真", ROOT))
    print("=" * 78)
    try:
        if a.inject:
            rc = run_inject()
        else:
            rc = run_main()
    except RuntimeError as e:
        print("[rc=2 环境不满足] %s" % e)
        return 2
    except Exception as e:                                # 正则/IO 崩 ⇒ 2 带原因，不许绿
        print("[rc=2 环境不满足] 判据自身异常：%s: %s" % (type(e).__name__, e))
        return 2

    print("\n" + "=" * 78)
    g = sum(1 for _, _, v in RESULTS if v == "绿")
    r = sum(1 for _, _, v in RESULTS if v == "红")
    print("汇总：绿 %d / 红 %d" % (g, r))
    print("结论：%s（rc=%d）" % ("【绿】" if rc == 0 else "【红】", rc))

    if a.out and not a.inject:
        # 全量明细（独立再扫一次落盘，保证与打印同源）
        hits = scan(ROOT, subdirs=SCOPE)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write("写死路径门禁判据 · 验真全量明细（实测文件 %d / 行 %d）\n"
                     % (len(hits), sum(len(v) for v in hits.values())))
            for rel in sorted(hits):
                fh.write("# %s（%d 行）\n" % (rel, len(hits[rel])))
                for ln, s in hits[rel]:
                    fh.write("  %6d  %s\n" % (ln, s))
        print("全量明细已落盘：%s" % a.out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
