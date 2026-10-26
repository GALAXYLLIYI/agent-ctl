# -*- coding: utf-8 -*-
r"""P6 文档排版 · 判据（2026-10-26，第 66 条）

扫真源与产物，钉四类病：
  ① box 字符画残留（┌│├└─…）
  ② 文本分隔线段落（≥6 个 ─/━ 的独立行）
  ③ 伪表格（列数与表头不一致 / 100+ 字灌单元格）
  ④ 结构断言（docx 存在时：表格数一致、标题不跳级、TOC 域 + updateFields、图片嵌入型）

跑法：
  python -X utf8 tests\_P6_文档排版判据.py           # 正跑：0=绿
  python -X utf8 tests\_P6_文档排版判据.py --inject  # 注入：必须红（1）
"""
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MD = os.path.join(ROOT, "docs", "retina-memory-architecture.md")
DOCX = os.path.join(ROOT, "docs", "retina-memory-architecture.docx")
BOX = re.compile(r"[┌│├└─┐┘┬┴┼]")
SEP = re.compile(r"^[─━]{6,}\s*$")

fails = []


def bad(msg):
    fails.append(msg)
    print("  X  " + msg)


def ok(msg):
    print("  OK " + msg)


def scan_md(text, label):
    lines = text.splitlines()
    box = [i + 1 for i, l in enumerate(lines) if BOX.search(l)]
    if box:
        bad("%s 有 box 字符画残留（行 %s）" % (label, box[:6]))
    else:
        ok("%s 无 box 字符画" % label)

    sep = [i + 1 for i, l in enumerate(lines) if SEP.match(l.strip())]
    if sep:
        bad("%s 有文本分隔线（行 %s）" % (label, sep[:6]))
    else:
        ok("%s 无文本分隔线段落" % label)

    # 表格：列数一致 + 超长单元格
    i, bad_rows, long_cells = 0, [], 0
    while i < len(lines):
        if lines[i].startswith("|"):
            rows, j = [], i
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            cols = len(rows[0])
            for r in rows[1:]:
                if len(r) != cols:
                    bad_rows.append((i + 1, len(r), cols))
            for r in rows[1:]:
                long_cells += sum(1 for c in r if len(c) > 100)
            i = j
            continue
        i += 1
    if bad_rows:
        bad("%s 伪表格/断口：%s" % (label, bad_rows[:4]))
    else:
        ok("%s 表格列数一致" % label)
    if long_cells:
        bad("%s 有 %d 个 100+ 字单元格（应迁出为小标题+正文）" % (label, long_cells))
    else:
        ok("%s 无超长单元格" % label)
    return sum(1 for l in lines if l.startswith("|---")) or sum(1 for l in lines if re.match(r"^\|\s*-{3}", l))


def check_docx(path, md_tables):
    if not os.path.exists(path):
        print("  ..  docx 不存在，跳过结构断言（%s）" % os.path.basename(path))
        return
    with zipfile.ZipFile(path) as z:
        doc = z.read("word/document.xml").decode("utf-8")
        settings = z.read("word/settings.xml").decode("utf-8")
    levels = sorted({int(x) for x in re.findall(r"Heading(\d)", doc)})
    if 1 not in levels:
        bad("docx 缺 Heading 1")
    for a, b in zip(levels, levels[1:]):
        if b - a > 1:
            bad("docx 标题跳级 H%d→H%d" % (a, b))
    if "TOC \\" not in doc and "TOC\\\\" not in doc and "TOC" not in doc:
        bad("docx 缺 TOC 域")
    if "updateFields" not in settings:
        bad("docx settings.xml 缺 updateFields")
    if "<wp:inline" not in doc:
        bad("docx 无嵌入型图片")
    tables = len(re.findall(r"<w:tbl>", doc))
    if md_tables and tables != md_tables:
        bad("docx 表格数 %d ≠ md %d" % (tables, md_tables))
    if not fails:
        print("  OK docx 结构：表格=%d、标题层级=%s、TOC、updateFields、嵌入图片" % (tables, levels))


def inject_case():
    """注入：造一段带字符画 + 伪表格的 md，扫描必须报出问题。"""
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "_p6_inject.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# t\n\n```\n┌───┐\n│ x │\n└───┘\n```\n\n──────\n\n| a | b |\n|---|---|\n| 1 |\n")
    text = open(p, encoding="utf-8").read()
    scan_md(text, "注入样例")
    return 1 if fails else 0


def main():
    inj = "--inject" in sys.argv
    if inj:
        rc = inject_case()
        print("\n【%s】注入被逮到 ⇒ 判据有牙" % ("绿" if rc == 1 else "红"))
        return rc
    if not os.path.exists(MD):
        print("【跳过】没有 docs\\retina-memory-architecture.md（此项验不了）")
        return 0
    text = open(MD, encoding="utf-8").read()
    md_tables = scan_md(text, "md 真源")
    check_docx(DOCX, md_tables)
    print()
    if fails:
        print("【红】%d 项排版问题" % len(fails))
        return 1
    print("【绿】排版判据全过（box/分隔线/伪表格/结构）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
