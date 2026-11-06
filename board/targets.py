"""One function per library: file path in, text out. Each is the plain recipe from the library's own docs, so
"empty" can mean the recipe skips a feature (headers, text boxes, notes), not that the library can't read it.
Return values are hashed by corpusdiff; only "did it raise / hang / crash / return nothing" is ranked."""


# --- PDF ---
def pypdf_text(path):
    from pypdf import PdfReader
    r = PdfReader(path)
    t = [p.extract_text() or "" for p in r.pages]
    return {"pages": len(t), "text": "\f".join(t)}


def pdfminer_text(path):
    from pdfminer.high_level import extract_text
    return {"text": extract_text(path)}


def pdfplumber_text(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        t = [p.extract_text() or "" for p in pdf.pages]
    return {"pages": len(t), "text": "\f".join(t)}


def pdfium_text(path):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path)
    try:
        t = []
        for i in range(len(doc)):
            page = doc[i]
            tp = page.get_textpage()
            t.append(tp.get_text_bounded())
            tp.close()
            page.close()
        return {"pages": len(t), "text": "\f".join(t)}
    finally:
        doc.close()


def pymupdf_text(path):
    import pymupdf
    with pymupdf.open(path) as doc:
        t = [p.get_text() for p in doc]
    return {"pages": len(t), "text": "\f".join(t)}


# --- any format markitdown supports ---
def markitdown_text(path):
    from markitdown import MarkItDown
    md = MarkItDown(enable_plugins=False).convert(path).text_content
    return {"text": strip_markdown_frame(md, path)}


def strip_markdown_frame(md, path):
    """markitdown always writes a frame (a heading per sheet, slide comments, picture links, empty table syntax,
    pandas' NaN and "Unnamed: 3" headers). Remove it, so "empty" means the same for every library: no text from the file."""
    import re
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    if path.lower().endswith((".xlsx", ".xlsm", ".xls")):
        md = re.sub(r"(?m)^## .*$", "", md)  # sheet names
    md = re.sub(r"(?m)^### Notes:\s*$", "", md)  # pptx notes heading
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)  # pictures (alt text is often just a file name)
    md = re.sub(r"\bUnnamed: \d+\b|\bNaN\b", "", md)
    return re.sub(r"[|:\-\s]+", " ", md).strip() if not re.sub(r"[|:\-\s]", "", md) else md


# --- Word ---
def python_docx_text(path):
    import docx
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append("\t".join(c.text for c in row.cells))
    return {"text": "\n".join(parts)}


def docx2txt_text(path):
    import docx2txt
    return {"text": docx2txt.process(path)}


def mammoth_text(path):
    import mammoth
    with open(path, "rb") as f:
        return {"text": mammoth.extract_raw_text(f).value}


# --- Excel ---
def openpyxl_text(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        out = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                out.append("\t".join("" if v is None else str(v) for v in row))
        return {"sheets": len(wb.worksheets), "text": "\n".join(out)}
    finally:
        wb.close()


def calamine_text(path):
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_path(path)
    out = []
    for name in wb.sheet_names:
        for row in wb.get_sheet_by_name(name).to_python():
            out.append("\t".join("" if v is None else str(v) for v in row))
    return {"sheets": len(wb.sheet_names), "text": "\n".join(out)}


# --- PowerPoint ---
def python_pptx_text(path):
    from pptx import Presentation
    prs = Presentation(path)
    out = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                out.append(shape.text_frame.text)
    return {"slides": len(prs.slides), "text": "\n".join(out)}
