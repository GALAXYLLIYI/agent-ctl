# -*- coding: utf-8 -*-
"""刀B · 一条命令：干净 md → docx（含 TOC / reference.docx / mermaid 渲染），并跑结构断言。

用法：
  python tools/build_docx.py                 # 生成到 work\\_排版管线_20261026\\dist\\
  python tools/build_docx.py --publish       # 生成后复制为 docs\\retina-memory-architecture.docx
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANDOC = r"F:\ZhuanZ\Documents\tools\pandoc-3.12.1\pandoc.exe"
MD = os.path.join(REPO, "docs", "retina-memory-architecture.md")
WORK = os.path.join(REPO, "work", "_排版管线_20261026")
DIST = os.path.join(WORK, "dist")
TMP = os.path.join(WORK, "tmp")
SVG = os.path.join(WORK, "svg")
BOX = re.compile(r"[┌│├└─┐┘┬┴┼]")


def run_pandoc(out):
    os.makedirs(DIST, exist_ok=True)
    for d in (TMP, SVG):
        os.makedirs(d, exist_ok=True)
    # mmdc 走 os.execute（系统代码页）⇒ 中间路径必须纯 ASCII；且 pandoc 写 C: 会被拒 ⇒ 放 F: 的 ASCII 目录
    ascii_tmp = os.path.join(REPO, "work", "mml_tmp")
    os.makedirs(ascii_tmp, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "TMP": TMP, "TEMP": TMP,
        # mmdc 走 os.execute（系统代码页）⇒ 中间路径必须纯 ASCII，否则中文路径被吞
        "AGENTCTL_MMD_TMP": ascii_tmp, "AGENTCTL_MMD_SVG_DIR": ascii_tmp,
        "AGENTCTL_MMDC": shutil.which("mmdc") or "mmdc",
        "AGENTCTL_PUPPETEER": os.path.join(REPO, "tools", "puppeteer-edge.json"),
        "AGENTCTL_MMD_WIDTH": "1200",
    })
    cmd = [PANDOC, MD, "-o", out,
           "--toc", "--toc-depth=3",
           "--reference-doc", os.path.join(REPO, "docx", "reference.docx"),
           "--lua-filter", os.path.join(REPO, "tools", "mermaid.lua"),
           "--resource-path", REPO]
    r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True)
    print("pandoc rc=", r.returncode)
    if r.stderr:
        print(r.stderr.decode("utf-8", "replace")[:800])
    return r.returncode == 0


def patch_settings(docx_path):
    """pandoc 不写 updateFields；补一个，Word 打开会提示更新 TOC 域。"""
    tmp = docx_path + ".tmp"
    with zipfile.ZipFile(docx_path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/settings.xml":
                s = data.decode("utf-8")
                if "updateFields" not in s:
                    s = s.replace("</w:settings>",
                                  '<w:updateFields w:val="true"/></w:settings>')
                data = s.encode("utf-8")
            zout.writestr(item, data)
    os.replace(tmp, docx_path)


def copy_svgs():
    ascii_tmp = os.path.join(REPO, "work", "mml_tmp")
    if not os.path.isdir(ascii_tmp):
        return
    for f in os.listdir(ascii_tmp):
        if f.endswith(".svg"):
            shutil.copyfile(os.path.join(ascii_tmp, f), os.path.join(SVG, f))


def read_zip_xml(docx_path, name):
    with zipfile.ZipFile(docx_path) as z:
        return z.read(name).decode("utf-8")


def assert_structure(docx_path):
    md = open(MD, encoding="utf-8").read()
    fails = []
    if BOX.search(md):
        fails.append("md 仍有 box 字符")
    if any(re.match(r"^---+$", ln.strip()) for ln in md.splitlines()):
        fails.append("md 仍有独立 --- 行")

    doc = read_zip_xml(docx_path, "word/document.xml")
    settings = read_zip_xml(docx_path, "word/settings.xml")

    heads = re.findall(r'w:val="Heading(\d)"', doc) + re.findall(r'Heading(\d)', doc)
    levels = sorted({int(x) for x in heads if x.isdigit()})
    if 1 not in levels:
        fails.append("缺 Heading 1")
    for a, b in zip(levels, levels[1:]):
        if b - a > 1:
            fails.append("标题跳级: H%d -> H%d" % (a, b))
    if not re.search(r"TOC \\\\?o", doc):
        fails.append("缺 TOC 域")
    if "updateFields" not in settings:
        fails.append("settings.xml 缺 updateFields")
    if 'w:txbxContent' in doc:
        fails.append("含文本框（应为嵌入型图片）")
    if not re.search(r"<wp:inline", doc):
        fails.append("没有嵌入型图片（wp:inline）")

    tables = len(re.findall(r"<w:tbl>", doc))
    md_tables = len(re.findall(r"^\|---", md, re.M))
    if tables != md_tables:
        fails.append("表格数 docx=%d md=%d" % (tables, md_tables))
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    out = os.path.join(DIST, "retina-memory-architecture.docx")
    if not run_pandoc(out):
        return 1
    patch_settings(out)
    copy_svgs()
    fails = assert_structure(out)
    if fails:
        print("【红】结构断言 %d 项：" % len(fails))
        for f in fails:
            print("  -", f)
        return 1
    print("【绿】结构断言通过：表格=%d、TOC 域、updateFields、嵌入型图片、标题无跳级" %
          len(re.findall(r"<w:tbl>", read_zip_xml(out, "word/document.xml"))))
    if a.publish:
        dst = os.path.join(REPO, "docs", "retina-memory-architecture.docx")
        shutil.copyfile(out, dst)
        print("published:", dst, os.path.getsize(dst), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
