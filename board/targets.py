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


# --- any document (RAG ingestion tools), default settings; one converter per worker process ---
_CACHE = {}


def _no_raw(out):
    """Same rule for every tool: returning the file's own RTF source is not text extraction (counts as empty)."""
    t = out["text"] or ""
    if t.lstrip().startswith("{\\rtf"):
        return {"text": "", "returned_raw_rtf": len(t)}
    return out


def markitdown_any(path):
    return _no_raw(markitdown_text(path))


def docling_any(path):
    if "docling" not in _CACHE:
        from docling.document_converter import DocumentConverter
        _CACHE["docling"] = DocumentConverter()
    return _no_raw({"text": _CACHE["docling"].convert(path).document.export_to_markdown()})


def unstructured_any(path):
    from unstructured.partition.auto import partition
    return _no_raw({"text": "\n\n".join(str(e) for e in partition(filename=path))})


def kreuzberg_any(path):
    import kreuzberg
    return _no_raw({"text": kreuzberg.extract_file_sync(path).content})


# --- OCR (images with a ground-truth file next to each: NAME.gt.txt) ---
# Each engine's default recipe from its docs, English models, CPU. The score compares the text with the ground truth:
#   cer         character error rate after whitespace is collapsed (0 = perfect; capped at 1)
#   word_recall share of ground-truth words found anywhere in the output (ignores reading order)
def _truth(path):
    import os
    base = os.path.splitext(path)[0]
    with open(base + ".gt.txt", encoding="utf-8") as f:
        return f.read()


def _score(text, path):
    import re
    import unicodedata
    from collections import Counter
    from rapidfuzz.distance import Levenshtein

    def norm(s):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()

    def words(s):
        return Counter(w for w in re.findall(r"\w+", norm(s).lower()))

    t, g = norm(text), norm(_truth(path))
    cer = min(1.0, Levenshtein.distance(t, g) / max(1, len(g)))
    gw, tw = words(g), words(t)
    recall = sum((gw & tw).values()) / max(1, sum(gw.values()))
    return {"text": text, "metrics": {"cer": round(cer, 4), "word_recall": round(recall, 4)}}


def tesseract_ocr(path):
    import pytesseract
    from PIL import Image
    return _score(pytesseract.image_to_string(Image.open(path)), path)


def easyocr_ocr(path):
    if "easyocr" not in _CACHE:
        import easyocr
        _CACHE["easyocr"] = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _score("\n".join(_CACHE["easyocr"].readtext(path, detail=0, paragraph=True)), path)


def rapidocr_ocr(path):
    if "rapidocr" not in _CACHE:
        from rapidocr import RapidOCR
        _CACHE["rapidocr"] = RapidOCR()
    r = _CACHE["rapidocr"](path)
    return _score("\n".join(r.txts or ()), path)


def paddleocr_ocr(path):
    if "paddleocr" not in _CACHE:
        from paddleocr import PaddleOCR
        _CACHE["paddleocr"] = PaddleOCR(lang="en", use_doc_orientation_classify=False, use_doc_unwarping=False,
                                        use_textline_orientation=False)
    res = _CACHE["paddleocr"].predict(path)
    return _score("\n".join(t for r in res for t in r["rec_texts"]), path)


def doctr_ocr(path):
    if "doctr" not in _CACHE:
        from doctr.models import ocr_predictor
        _CACHE["doctr"] = ocr_predictor(pretrained=True)
    from doctr.io import DocumentFile
    return _score(_CACHE["doctr"](DocumentFile.from_images(path)).render(), path)


# --- Speech to text (16 kHz mono WAV clips with the reference transcript next to each: NAME.gt.txt) ---
# Each engine's documented recipe, English model, CPU. Both texts go through Whisper's English text normalizer
# (lower case, no punctuation, "twenty" == "20") before scoring:
#   wer         word error rate: word-level edit distance / reference words (0 = perfect; capped at 1)
#   word_recall share of reference words found anywhere in the output
def _asr_score(text, path):
    from collections import Counter
    from rapidfuzz.distance import Levenshtein
    from whisper_normalizer.english import EnglishTextNormalizer

    if "norm" not in _CACHE:
        _CACHE["norm"] = EnglishTextNormalizer()
    norm = _CACHE["norm"]
    hyp, ref = norm(text).split(), norm(_truth(path)).split()
    wer = min(1.0, Levenshtein.distance(hyp, ref) / max(1, len(ref)))
    recall = sum((Counter(ref) & Counter(hyp)).values()) / max(1, len(ref))
    errors = Levenshtein.distance(hyp, ref)
    return {"text": text, "metrics": {"wer": round(wer, 4), "word_recall": round(recall, 4),
                                      "errors": errors, "ref_words": len(ref)}}


def whisper_asr(path):
    if "whisper" not in _CACHE:
        import whisper
        _CACHE["whisper"] = whisper.load_model("base.en", device="cpu")
    return _asr_score(_CACHE["whisper"].transcribe(path, fp16=False, language="en")["text"], path)


def faster_whisper_asr(path):
    if "fw" not in _CACHE:
        from faster_whisper import WhisperModel
        _CACHE["fw"] = WhisperModel("base.en", device="cpu", compute_type="int8")
    segments, _ = _CACHE["fw"].transcribe(path, language="en")
    return _asr_score(" ".join(s.text for s in segments), path)


def whispercpp_asr(path):
    if "wcpp" not in _CACHE:
        from pywhispercpp.model import Model
        _CACHE["wcpp"] = Model("base.en", print_progress=False, print_realtime=False)
    return _asr_score(" ".join(s.text for s in _CACHE["wcpp"].transcribe(path)), path)


def vosk_asr(path):
    import json
    import wave
    import vosk
    if "vosk" not in _CACHE:
        vosk.SetLogLevel(-1)
        _CACHE["vosk"] = vosk.Model(lang="en-us")
    with wave.open(path, "rb") as w:
        rec = vosk.KaldiRecognizer(_CACHE["vosk"], w.getframerate())
        parts = []
        while data := w.readframes(4000):
            if rec.AcceptWaveform(data):
                parts.append(json.loads(rec.Result())["text"])
        parts.append(json.loads(rec.FinalResult())["text"])
    return _asr_score(" ".join(p for p in parts if p), path)


def moonshine_asr(path):
    import moonshine_onnx
    return _asr_score(" ".join(moonshine_onnx.transcribe(path, "moonshine/base")), path)


def parakeet_asr(path):
    if "parakeet" not in _CACHE:
        import onnx_asr
        _CACHE["parakeet"] = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2")
    return _asr_score(_CACHE["parakeet"].recognize(path), path)


# --- Language identification (NAME.txt with NAME.gt.txt holding its ISO 639-1 code) ---
# Each library's one-call recipe from its docs, all languages enabled. Scored correct/incorrect after mapping the
# library's code to ISO 639-1 (zh-cn -> zh, nb -> no, iw -> he).
_LANG_ALIASES = {"zh-cn": "zh", "zh-tw": "zh", "zh-hans": "zh", "zh-hant": "zh", "nb": "no", "nn": "no",
                 "iw": "he", "in": "id", "pes": "fa", "zho": "zh", "cmn": "zh"}


def _lang_score(code, path):
    truth = _truth(path).strip()
    c = (code or "").strip().lower().replace("_", "-")
    c = _LANG_ALIASES.get(c, c.split("-")[0])
    return {"text": c, "metrics": {"correct": 1.0 if c == truth else 0.0}}


def _lang_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def langdetect_lid(path):
    from langdetect import DetectorFactory, detect
    DetectorFactory.seed = 0  # langdetect is random unless seeded
    return _lang_score(detect(_lang_text(path)), path)


def langid_lid(path):
    import langid
    return _lang_score(langid.classify(_lang_text(path))[0], path)


def py3langid_lid(path):
    import py3langid
    return _lang_score(py3langid.classify(_lang_text(path))[0], path)


def lingua_lid(path):
    if "lingua" not in _CACHE:
        from lingua import LanguageDetectorBuilder
        _CACHE["lingua"] = LanguageDetectorBuilder.from_all_languages().build()
    lang = _CACHE["lingua"].detect_language_of(_lang_text(path))
    return _lang_score(lang.iso_code_639_1.name if lang else None, path)


def fastlangdetect_lid(path):
    from fast_langdetect import detect
    r = detect(_lang_text(path).replace("\n", " "))  # it reads one line at a time
    if isinstance(r, list):
        r = r[0] if r else {}
    return _lang_score(r.get("lang"), path)


# --- PDF to Markdown quality (olmOCR-bench pages; checks in the corpus's *.jsonl, scored by board/olmo) ---
# Each tool's documented one-call recipe with default settings. A page passes a check when the Markdown contains the
# expected text, leaves out headers/footers, keeps reading order or table cell neighbours (olmOCR-bench's own checks).
# olmOCR-bench's check files; arxiv_math and old_scans_math are left out: math checks need KaTeX in a browser
_OLMO_CATS = ("headers_footers", "long_tiny_text", "multi_column", "old_scans", "table_tests")


def _olmo_score(md, path):
    import os
    if "olmo" not in _CACHE:
        from olmo.tests import load_tests  # board/olmo (corpusdiff puts this file's folder on the path)
        root = os.path.dirname(os.path.abspath(path))
        while not os.path.exists(os.path.join(root, "multi_column.jsonl")) and root != "/":
            root = os.path.dirname(root)
        index = {}
        for cat in _OLMO_CATS:
            for t in load_tests(os.path.join(root, f"{cat}.jsonl")):
                index.setdefault(t.pdf, []).append(t)
        _CACHE["olmo"] = (os.path.join(root, "pdfs"), index)
    pdfs, index = _CACHE["olmo"]
    tests = index.get(os.path.relpath(os.path.abspath(path), pdfs), [])
    passed = sum(1 for t in tests if t.run(md or "")[0])
    return {"text": md, "metrics": {"passed": passed, "checks": len(tests),
                                    "pass_rate": round(passed / len(tests), 4) if tests else 0.0}}


def pymupdf4llm_md(path):
    import pymupdf4llm
    return _olmo_score(pymupdf4llm.to_markdown(path), path)


def markitdown_md(path):
    if "md" not in _CACHE:
        from markitdown import MarkItDown
        _CACHE["md"] = MarkItDown()
    return _olmo_score(_CACHE["md"].convert(path).markdown, path)


def docling_md(path):
    if "docling" not in _CACHE:
        from docling.document_converter import DocumentConverter
        _CACHE["docling"] = DocumentConverter()
    return _olmo_score(_CACHE["docling"].convert(path).document.export_to_markdown(), path)


def kreuzberg_md(path):
    import kreuzberg
    cfg = kreuzberg.ExtractionConfig(output_format="markdown")
    return _olmo_score(kreuzberg.extract_file_sync(path, config=cfg).content, path)


def marker_md(path):
    if "marker" not in _CACHE:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        _CACHE["marker"] = PdfConverter(artifact_dict=create_model_dict())
    from marker.output import text_from_rendered
    text, _, _ = text_from_rendered(_CACHE["marker"](path))
    return _olmo_score(text, path)
