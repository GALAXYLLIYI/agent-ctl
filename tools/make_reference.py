# -*- coding: utf-8 -*-
"""生成 docx/reference.docx（只存样式）：正文宋体12pt/1.3行距/孤行控制、Source Code 双字体、
分隔线段落下边框、表格表头加粗。"""
import os
import shutil
import subprocess

import docx
from docx.enum.text import WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(REPO, "docx", "reference.docx")
PANDOC = os.environ.get("AGENTCTL_PANDOC") or shutil.which("pandoc") or "pandoc"


def set_east_asia(style_or_run, font_cn: str, font_ascii: str | None = None):
    rpr = style_or_run.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.append(rf)
    rf.set(qn("w:eastAsia"), font_cn)
    if font_ascii:
        rf.set(qn("w:ascii"), font_ascii)
        rf.set(qn("w:hAnsi"), font_ascii)


def paragraph_border(style, sz=6):
    ppr = style.element.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(sz))
    bottom.set(qn("w:space"), "6")
    bottom.set(qn("w:color"), "808080")
    bdr.append(bottom)
    ppr.append(bdr)


def main():
    if not os.path.exists(REF):
        data = subprocess.run([PANDOC, "--print-default-data-file", "reference.docx"],
                              capture_output=True).stdout
        open(REF, "wb").write(data)
    d = docx.Document(REF)

    # 正文
    st = d.styles["Normal"]
    st.font.name = "宋体"
    st.font.size = docx.shared.Pt(12)
    st.paragraph_format.line_spacing = 1.3
    st.paragraph_format.widow_control = True
    set_east_asia(st, "宋体", "Times New Roman")

    # 标题：中文黑体
    for name in ("Title", "Heading 1", "Heading 2", "Heading 3"):
        try:
            s = d.styles[name]
            set_east_asia(s, "黑体", "Arial")
        except KeyError:
            pass

    # Source Code
    names = [s.name for s in d.styles]
    if "Source Code" in names:
        sc = d.styles["Source Code"]
    else:
        from docx.enum.style import WD_STYLE_TYPE
        sc = d.styles.add_style("Source Code", WD_STYLE_TYPE.PARAGRAPH)
    sc.font.name = "Consolas"
    sc.font.size = docx.shared.Pt(9)
    sc.paragraph_format.line_spacing = 1.1
    set_east_asia(sc, "宋体", "Consolas")

    # 分隔线段落：下边框，无大纲级别
    if "Separator" in [s.name for s in d.styles]:
        sep = d.styles["Separator"]
    else:
        from docx.enum.style import WD_STYLE_TYPE
        sep = d.styles.add_style("Separator", WD_STYLE_TYPE.PARAGRAPH)
    paragraph_border(sep, 6)
    sep.paragraph_format.space_before = docx.shared.Pt(6)
    sep.paragraph_format.space_after = docx.shared.Pt(6)

    d.save(REF)
    print("reference.docx ok:", os.path.getsize(REF), "bytes")


if __name__ == "__main__":
    main()
