# Reader board

**Which Python library reads PDF, Word, Excel and PowerPoint files without breaking?**

Page: **https://prometheus-maker.github.io/reader-board/**

Every day a GitHub Actions job checks PyPI. When a library on the board has a new release, it runs that release over
the same pinned set of odd real-world files (Mozilla pdf.js's test PDFs, Apache POI's Word/Excel/PowerPoint test
files) with [corpusdiff](https://github.com/prometheus-maker/corpusdiff), saves the per-file results in
[`results/`](results/), and rebuilds the page. So the board also shows what each release broke or fixed.

| Format | Libraries |
|---|---|
| PDF | pypdf, pdfminer.six, pdfplumber, pypdfium2, PyMuPDF, markitdown |
| Word (.docx) | python-docx, docx2txt, mammoth, markitdown |
| Excel (.xlsx) | openpyxl, python-calamine, markitdown |
| PowerPoint (.pptx) | python-pptx, markitdown |
| Any document → text (Apache Tika's 477 test files: .doc, .rtf, .msg, .odt, ...) | markitdown, Docling, Unstructured, Kreuzberg |

Head-to-head pages for the usual questions, e.g.
[markitdown vs Docling](https://prometheus-maker.github.io/reader-board/markitdown-vs-docling.html),
[PyMuPDF vs pdfplumber](https://prometheus-maker.github.io/reader-board/pymupdf-vs-pdfplumber.html),
[pypdf vs PyMuPDF](https://prometheus-maker.github.io/reader-board/pypdf-vs-pymupdf.html)
([all of them](https://prometheus-maker.github.io/reader-board/#vs); pairs listed in `board/libs.json` → `compare`).

## What is ranked

- **Fails where another reads**: the library raised, hung (60 s) or crashed on a file from which another library in
  the same table got text. Files every library fails on (encrypted, truncated, fuzzed) count against nobody.
- **Empty where another reads**: no error, no text. Often the recipe rather than the library (e.g. python-docx
  paragraphs + tables skip headers and text boxes); the per-library pages list the files so you can check.
- Text *quality* is not ranked. This board is about robustness.

Each library runs the short recipe in [`board/targets.py`](board/targets.py). If a recipe is unfair to a library,
please open an issue or a PR: the next run uses the fixed recipe.

## Files

- `board/libs.json`: libraries, formats, and the pinned commit of each test collection.
- `board/plan.py`: which library releases have no results yet (GitHub Actions matrix).
- `board/build.py`: results -> static page.
- `results/<format>/<library>/<version>.jsonl.gz`: one line per file (ok/error, error type and message, output
  size and hash, seconds, peak MB); `<version>.json`: Python, machine and every installed package version.

The test files are not copied here; they are downloaded from their projects at the pinned commit. Some are
deliberately hostile (zip bombs, XXE); run them in a sandbox.

Made by [Prometheus](https://prometheus-maker.github.io/about.html). MIT licence.
