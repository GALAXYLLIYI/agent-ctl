# -*- coding: utf-8 -*-
"""刀B · 渲染代理检查：COM 无头导出 PDF → 文本等价 ≥95% → 逐页 PNG。

导不出就如实返回 unobservable（不假装成功）。
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(REPO, "work", "_排版管线_20261026")
DOCX = os.path.join(WORK, "dist", "retina-memory-architecture.docx")
PDF = os.path.join(WORK, "dist", "retina-memory-architecture.pdf")
PNG_DIR = os.path.join(WORK, "渲染")
PROGIDS = ("Word.Application", "KWPS.Application", "wps.Application")


def export_pdf():
    try:
        import pythoncom
        import win32com.client
    except Exception as e:                      # noqa: BLE001
        return None, "pywin32 不可用: %r" % (e,)
    pythoncom.CoInitialize()
    for pid in PROGIDS:
        try:
            app = win32com.client.Dispatch(pid)
        except Exception:
            continue
        try:
            try:
                app.Visible = False
                app.DisplayAlerts = 0
            except Exception:
                pass
            doc = app.Documents.Open(DOCX, ReadOnly=True)
            doc.ExportAsFixedFormat(PDF, 17)     # 17 = wdExportFormatPDF
            doc.Close(False)
            try:
                app.Quit()
            except Exception:
                pass
            return pid, None
        except Exception as e:                   # noqa: BLE001
            try:
                app.Quit()
            except Exception:
                pass
            last = "%s: %r" % (pid, e)
    return None, "所有 COM 候选都失败（最后：%s）" % locals().get("last", "无")


def docx_text(path):
    import docx
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                parts.append(c.text)
    return re.sub(r"\s+", "", "".join(parts))


def pdf_text(path):
    import pymupdf
    doc = pymupdf.open(path)
    txt = "".join(page.get_text() for page in doc)
    os.makedirs(PNG_DIR, exist_ok=True)
    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(dpi=120)
        pix.save(os.path.join(PNG_DIR, "page_%02d.png" % i))
    return re.sub(r"\s+", "", txt), len(doc)


def main():
    if not os.path.exists(DOCX):
        print("UNOBSERVABLE：没有 dist docx，先跑 build_docx.py")
        return 2
    pid, err = export_pdf()
    if not os.path.exists(PDF):
        print("UNOBSERVABLE：COM 导不出 PDF（%s）" % (err or "未知"))
        return 2
    dt = docx_text(DOCX)
    pt, pages = pdf_text(PDF)
    if not dt:
        print("UNOBSERVABLE：docx 提取不到文本")
        return 2
    ratio = 100.0 * len(pt) / max(1, len(dt))
    print("导出程序=%s 页数=%d docx字符=%d pdf字符=%d 等价=%.1f%%" %
          (pid, pages, len(dt), len(pt), ratio))
    print("证据：%s（%d 页 PNG 在 %s）" % (PDF, pages, PNG_DIR))
    return 0 if ratio >= 95.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
