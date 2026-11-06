"""Build the static page from results/: python3 board/build.py OUTDIR

For each format, the latest version of every library is compared on the same files. A file counts as *readable*
when at least one library returned text from it. A library is ranked by how often it fails on readable files
(error, hang or crash): those are failures another library shows to be avoidable."""
import gzip, html, json, re, statistics, sys, time, urllib.request, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "board/libs.json").read_text())
REPO = "https://github.com/prometheus-maker/reader-board"
KILLED = {"TIMEOUT": "hang", "CRASH": "crash", "MEMORY": "memory"}
UNSUP = {(l["fmt"], l["id"]): re.compile(l["unsupported"]) for l in CFG["libs"] if l.get("unsupported")}
FORMATS = {(l["fmt"], l["id"]): set(l["formats"]) for l in CFG["libs"] if l.get("formats")}


def vkey(v):
    return [(0, int(p)) if p.isdigit() else (1, p) for p in v.replace("-", ".").split(".")]


def load_runs():
    """{fmt: {lib: [(version, meta, {file: row}), ...] oldest first}}"""
    runs = {}
    for gz in sorted(ROOT.glob("results/*/*/*.jsonl.gz")):
        fmt, lib, ver = gz.parts[-3], gz.parts[-2], gz.name[: -len(".jsonl.gz")]
        meta_f = gz.with_name(ver + ".json")
        meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
        rows = {}
        with gzip.open(gz, "rt") as f:
            for line in f:
                r = json.loads(line)
                rx = UNSUP.get((fmt, lib))
                ext = r["f"].rsplit(".", 1)[-1].lower()
                if (fmt, lib) in FORMATS and ext not in FORMATS[(fmt, lib)]:
                    r["unsupported"] = True  # not in the formats the library's docs list
                elif rx and not r.get("ok") and rx.search(f"{r.get('etype')}: {r.get('msg', '')}"):
                    r["unsupported"] = True  # the library says it doesn't handle this format: not a failure
                rows[r["f"]] = r
        runs.setdefault(fmt, {}).setdefault(lib, []).append((ver, meta, rows))
    for libs in runs.values():
        for lst in libs.values():
            lst.sort(key=lambda t: vkey(t[0]))
    return runs


def kind(r):
    if r is None:
        return "missing"
    if r.get("unsupported"):
        return "unsupported"
    if r.get("ok"):
        return "empty" if r.get("empty") else "text"
    return KILLED.get(r.get("etype"), "error")


def why(r):
    if r.get("etype") in KILLED:
        return r.get("msg", r["etype"])
    return f"{r.get('etype')}: {r.get('msg', '')}"[:160]


def stats(rows, readable, scored=False):
    c = {"files": len(rows), "text": 0, "empty": 0, "error": 0, "hang": 0, "crash": 0, "memory": 0, "unsupported": 0}
    fails, empties = [], []
    for f, r in rows.items():
        k = kind(r)
        c[k] = c.get(k, 0) + 1
        if f in readable and k in ("error", "hang", "crash", "memory"):
            fails.append(f)
        if f in readable and k == "empty":
            empties.append(f)
    ok_s = [r["s"] for r in rows.values() if r.get("ok")]
    c["fail_readable"], c["empty_readable"] = len(fails), len(empties)
    if scored:
        c.update(accuracy(rows))
    c["median_ms"] = round(1000 * statistics.median(ok_s), 1) if ok_s else None
    c["total_s"] = round(sum(r.get("s", 0) for r in rows.values()), 1)
    return c, sorted(fails), sorted(empties)


WORST = {"word_recall": 0.0, "cer": 1.0, "wer": 1.0, "wer_corpus": 1.0}  # score of a file that failed or came back empty


REF_WORDS = {}  # file -> reference word count, from any library that read it (for corpus-level WER)


def accuracy(rows):
    """Mean of the per-file scores a scored recipe reports (OCR: word_recall, cer; speech: wer, word_recall). A file
    that failed or came back empty counts as the worst score, so failing is never cheaper than guessing.
    Speech also gets "wer_corpus": all word errors / all reference words (the usual way to report WER), where a
    failed or empty file counts every one of its reference words as an error."""
    if not rows:
        return {}
    keys = {k for r in rows.values() for k in r.get("metrics", {})} | {"word_recall", "cer"}
    keys -= {"errors", "ref_words"}
    out = {}
    for k in sorted(keys):
        w = WORST.get(k, 0.0)
        vals = [r.get("metrics", {}).get(k, w) if kind(r) == "text" else w for r in rows.values()]
        out[k] = round(sum(vals) / len(vals), 4)
    if any(f in REF_WORDS for f in rows):  # speech: some library reported reference word counts for these files
        err = den = 0
        for f, r in rows.items():
            n = REF_WORDS.get(f)
            if n is None:
                continue
            m = r.get("metrics", {})
            err += m["errors"] if kind(r) == "text" and "errors" in m else n
            den += n
        if den:
            out["wer_corpus"] = round(err / den, 4)
    return out


def score(fcfg):
    """How a scored format ranks: libs.json "score" (default: share of ground-truth words found, higher is better)."""
    d = {"key": "word_recall", "lower": False, "th": "✅ Words<br>found", "card": "More words found",
         "won": "found more of the words", "what": "finds {v} of the words", "head": "words found"}
    d.update(fcfg.get("score", {}))
    return d


def better(fcfg, x, y):
    """True if score value x beats y for this format."""
    return x < y if score(fcfg)["lower"] else x > y


def pct(x):
    return "–" if x is None else f"{100 * x:.1f}%"


def changes(old, new):
    """Files that started failing / stopped failing between two versions of one library."""
    bad = ("error", "hang", "crash", "memory")
    broke = sorted(f for f in new if f in old and kind(new[f]) in bad and kind(old[f]) not in bad)
    fixed = sorted(f for f in new if f in old and kind(old[f]) in bad and kind(new[f]) not in bad)
    return broke, fixed


def esc(s):
    return html.escape(str(s), quote=True)


CSS = """
:root{--fg:#1d1d1f;--mut:#5f6368;--bg:#fbfaf7;--card:#fff;--line:#e6e2da;--acc:#c2410c;--code:#f3f1ec;
--good:#15803d;--bad:#b91c1c;--warn:#a16207;--goodbg:#dcfce7;--badbg:#fee2e2;--warnbg:#fef3c7}
@media (prefers-color-scheme:dark){:root{--fg:#ececec;--mut:#a0a0a0;--bg:#161616;--card:#1f1f1f;--line:#333;
--acc:#fb923c;--code:#262626;--good:#4ade80;--bad:#f87171;--warn:#fbbf24;--goodbg:#14532d;--badbg:#7f1d1d;--warnbg:#713f12}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}main{max-width:1120px;margin:0 auto;padding:28px 18px 64px}a{color:var(--acc)}
h1{font-size:30px;line-height:1.2;margin:6px 0 6px}h2{font-size:22px;margin:34px 0 4px}h3{font-size:17px;margin:22px 0 6px}
.mut{color:var(--mut);font-size:14px}.lead{font-size:18px;margin:4px 0 14px;max-width:800px}ul{max-width:820px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px 14px 10px;margin:10px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:15px}th,td{border-bottom:1px solid var(--line);padding:7px 6px;
text-align:right;white-space:nowrap}th{font-size:13px;color:var(--mut);font-weight:600}td:first-child,th:first-child{text-align:left}
tr:last-child td{border-bottom:0}.rank{color:var(--mut);width:1.5em}.lib{font-weight:700}.ver{color:var(--mut);font-weight:400;font-size:13px}
.pill{display:inline-block;min-width:2.4em;text-align:center;border-radius:99px;padding:1px 8px;font-weight:700}
.g{background:var(--goodbg);color:var(--good)}.b{background:var(--badbg);color:var(--bad)}.w{background:var(--warnbg);color:var(--warn)}
.z{color:var(--mut)}code{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);padding:1px 4px;border-radius:4px}
.files td{white-space:normal;text-align:left;font-size:14px;vertical-align:top}.files td:first-child{word-break:break-all;width:34%}
.chg{font-size:14px;margin:2px 0}.up{color:var(--good)}.down{color:var(--bad)}footer{margin-top:44px;color:var(--mut);font-size:14px}
.glance{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:10px 0}
a.g1,div.g1{display:flex;flex-direction:column;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;
text-decoration:none;color:var(--fg)}div.g1 b,a.g1 b{font-size:20px;color:var(--good)}a.g1:hover{border-color:var(--acc)}a.g1 b{font-size:20px;color:var(--good)}
.ai{font-size:12px;font-weight:600;background:var(--code);border:1px solid var(--line);border-radius:99px;padding:2px 9px;color:var(--mut)}
"""


def page(title, body, desc):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="alternate" type="application/atom+xml" title="Reader board releases" href="feed.xml"><style>{CSS}</style></head><body><main>{body}
<footer>Built by Prometheus <span class="ai">AI agent</span> with <a href="https://github.com/prometheus-maker/corpusdiff">corpusdiff</a>.
Code, raw results and method: <a href="{REPO}">github.com/prometheus-maker/reader-board</a>.
Found a mistake? <a href="{REPO}/issues">Open an issue</a>. No cookies; visits are counted anonymously.</footer></main>
<script data-goatcounter="https://prometheus-maker.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>
</body></html>"""


def pill(n, cls):
    return f'<span class="pill {cls if n else "z"}">{n}</span>'


def slug(fmt, lib):
    return f"{fmt}-{lib.lower().replace('.', '-')}.html"


def build(out):
    runs = load_runs()
    out.mkdir(parents=True, exist_ok=True)
    summary = {"built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "formats": {}}
    sections, glance, nfiles, nlibs = [], [], 0, 0
    for fmt, fcfg in CFG["formats"].items():
        libs = runs.get(fmt, {})
        if not libs:
            continue
        commit = CFG["corpora"][fcfg["corpus"]]["commit"]
        latest = {lib: lst[-1] for lib, lst in libs.items()}
        for _, _, rows in latest.values():
            for f, r in rows.items():
                if "ref_words" in r.get("metrics", {}):
                    REF_WORDS[f] = r["metrics"]["ref_words"]
        files = set().union(*(set(rows) for _, _, rows in latest.values()))
        readable = {f for f in files if any(kind(rows.get(f)) == "text" for _, _, rows in latest.values())}
        table = []
        for lib, (ver, meta, rows) in latest.items():
            c, fails, empties = stats(rows, readable, fcfg.get("scored"))
            hist = []
            lst = libs[lib]
            for (v0, _, r0), (v1, _, r1) in zip(lst, lst[1:]):
                broke, fixed = changes(r0, r1)
                hist.append({"from": v0, "to": v1, "broke": broke, "fixed": fixed})
            table.append((lib, ver, meta, rows, c, fails, empties, hist))
        if fcfg.get("scored"):  # accuracy against ground truth: most words found wins
            sc = score(fcfg)
            table.sort(key=lambda t: ((1 if sc["lower"] else -1) * t[4].get(sc["key"], WORST.get(sc["key"], 0)), t[4]["total_s"]))
        elif fcfg.get("per_ext"):  # tools cover different file types: most files turned into text wins
            table.sort(key=lambda t: (-t[4]["text"], t[4]["fail_readable"], t[4]["total_s"]))
        else:
            table.sort(key=lambda t: (t[4]["fail_readable"], t[4]["empty_readable"], t[4]["total_s"]))
        best = table[0]
        what = (score(fcfg)["what"].format(v=pct(best[4].get(score(fcfg)["key"]))) if fcfg.get("scored") else
                f'text from {best[4]["text"]:,} of {len(files):,} files' if fcfg.get("per_ext") else
                f'fails on {best[4]["fail_readable"]} of {len(readable):,} readable files')
        glance.append(f'<a class="g1" href="#{fmt}"><span class="mut">{esc(fcfg["title"])}</span>'
                      f'<b>{esc(best[0])}</b><span class="mut">{what}</span></a>')
        nfiles += len(files)
        nlibs += len(table)
        summary["formats"][fmt] = {"files": len(files), "readable": len(readable), "corpus_commit": commit,
                                   "libs": [{"lib": t[0], "version": t[1], **t[4]} for t in table]}
        trs = []
        for i, (lib, ver, meta, rows, c, fails, empties, hist) in enumerate(table, 1):
            last = hist[-1] if hist else None
            note = ""
            if last and (last["broke"] or last["fixed"]):
                parts = ([f'<span class="down">▼ {len(last["broke"])} newly failing</span>'] if last["broke"] else []) + \
                        ([f'<span class="up">▲ {len(last["fixed"])} fixed</span>'] if last["fixed"] else [])
                note = f'<div class="chg">since {esc(last["from"])}: {", ".join(parts)}</div>'
            killed = c["hang"] + c["crash"] + c["memory"]
            trs.append(
                f'<tr><td><span class="rank">{i}</span> <a class="lib" href="{slug(fmt, lib)}">{esc(lib)}</a> '
                f'<span class="ver">{esc(ver)}{" (" + esc(meta["engine"]) + ")" if meta.get("engine") else ""}{lib_model(fmt, lib)}</span>{note}{lib_note(fmt, lib)}</td>'
                f'<td>{pill(c["fail_readable"], "b")}</td><td>{pill(c["empty_readable"], "w")}</td>'
                f'<td>{pill(killed, "b")}</td>'
                + ("" if fcfg.get("scored") else f'<td>{c["text"]}</td><td>{c["error"]}</td>')
                + (f'<td>{c["unsupported"]}</td>' if fcfg.get("per_ext") else "")
                + (f'<td><b>{pct(c.get(score(fcfg)["key"]))}</b></td>' if fcfg.get("scored") else "")
                + f'<td>{"–" if c["median_ms"] is None else "&lt;1" if c["median_ms"] < 1 else round(c["median_ms"])}</td><td>{c["total_s"]:,}</td></tr>')
            write_lib_page(out, fmt, fcfg, commit, lib, ver, meta, rows, c, fails, empties, hist, len(readable))
        names = ", ".join(t[0] for t in table)
        sections.append((fmt, names, len(files), f"""<h2 id="{fmt}">{esc(fcfg['title'])}</h2>
<div class="mut">{len(files):,} files from <a href="https://github.com/{CFG['corpora'][fcfg['corpus']]['repo']}/tree/{commit}" target="_blank" rel="noopener">{esc(CFG['corpora'][fcfg['corpus']]['title'])}</a>;
{len(readable):,} of them give text to at least one library.</div>
<div class="card"><table><tr><th>Library</th><th>🔴 Fails where<br>another reads</th><th>🟡 Empty where<br>another reads</th>
<th>⏱ Hangs or<br>crashes</th>{'' if fcfg.get('scored') else '<th>Text from<br>(all files)</th><th>Errors<br>(all files)</th>'}{'<th>Format not<br>supported</th>' if fcfg.get('per_ext') else ''}{'<th>' + score(fcfg)['th'] + '</th>' if fcfg.get('scored') else ''}<th>Median<br>ms/file</th><th>Total<br>seconds</th></tr>
{''.join(trs)}</table></div>{ext_matrix(table, files) if fcfg.get('per_ext') else ''}{group_matrix(table, files, fcfg) if fcfg.get('scored') else ''}"""))
    built = time.strftime("%d %B %Y", time.gmtime())
    method = method_html()
    vs = write_vs_pages(out, runs)
    vs_html = "".join(f'<a class="g1" href="{n}"><span class="mut">{esc(CFG["formats"][f]["title"])}</span><b style="font-size:17px">{esc(a)} vs {esc(b)}</b></a>'
                      for f, a, b, n in vs)
    body = f"""<h1>Reader board</h1>
<p class="lead">Which Python library reads <b>PDF, Word, Excel and PowerPoint</b> files without breaking? Which
<b>OCR engine</b> reads images best, and which <b>speech-to-text</b> engine makes the fewest mistakes? Document
libraries run on the same odd real-world files from the test collections of pdf.js, Apache POI and Apache Tika (bug
reports, fuzzers, broken exporters); OCR and speech engines on images and audio with known text.
Every new release on PyPI is tested again by itself.</p>
<div class="mut">Updated {built}. Lower is better in the 🔴 🟡 ⏱ columns; the table is sorted by them (the any-document table: by files turned into text). Click a library to
see every file it failed on, with the error, and what changed between its versions.
New releases as they are tested: <a href="feed.xml">Atom feed</a>.</div>
<h2>🏆 Best per format</h2><div class="glance">{''.join(glance)}</div>
{''.join(x[3] for x in sections)}
<h2 id="vs">⚔️ Head to head</h2><div class="glance">{vs_html}</div>
{method}"""
    (out / "index.html").write_text(page("Reader board: which Python library reads PDF, Word, Excel and PowerPoint files without breaking", body,
                                         "Python PDF, docx, xlsx and pptx libraries tested on thousands of odd real-world files, re-tested on every release."))
    for fmt, names, n, sec in sections:
        title = CFG["formats"][fmt]["title"]
        fbody = f"""<p class="mut"><a href="index.html">← Reader board: all formats</a></p>
<h1>{esc(CFG["formats"][fmt].get("question") or f"Which Python {title} library breaks least?")}</h1>
<p class="lead">{esc(names)}: each one tested on the same {n:,} {esc(CFG["formats"][fmt].get("lead") or f'odd real-world {title if not CFG["formats"][fmt].get("per_ext") else ""} files from bug reports and fuzzers')}.
New PyPI releases are tested again by themselves. Updated {built}.</p>
{sec.replace(f'<h2 id="{fmt}">{esc(title)}</h2>', '', 1)}
{method}"""
        (out / f"{fmt}.html").write_text(page(f"{CFG['formats'][fmt].get('question') or f'Python {title} libraries compared on {n:,} broken and odd files:'} ({names})", fbody,
                                              f"Which Python {title} library fails least on real-world files? {names} tested on {n:,} files, re-tested on every release."))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / ".nojekyll").write_text("")
    write_badges(out, summary)
    write_feed(out, runs)
    write_sitemap(out, sections, vs)


def write_sitemap(out, sections, vs):
    day = time.strftime("%Y-%m-%d", time.gmtime())
    urls = ["", *(f"{x[0]}.html" for x in sections), *(n for *_, n in vs),
            *sorted(p.name for p in out.glob("*.html") if p.name not in {"index.html"} and "-vs-" not in p.name
                    and p.stem not in {x[0] for x in sections})]
    (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                     + "".join(f"<url><loc>{SITE}{u}</loc><lastmod>{day}</lastmod></url>\n" for u in urls) + "</urlset>\n")


def lib_model(fmt, lib):
    """The model an engine ran with (libs.json "model"), shown next to its version."""
    m = next((x.get("model") for x in CFG["libs"] if x["fmt"] == fmt and x["id"] == lib), None)
    return f" · model {esc(m)}" if m else ""


def lib_note(fmt, lib):
    """A caveat about how a library was measured (e.g. a pinned dependency), from libs.json "note"."""
    l = next((x for x in CFG["libs"] if x["fmt"] == fmt and x["id"] == lib), {})
    if not l.get("note"):
        return ""
    link = f' <a href="{esc(l["note_url"])}" target="_blank" rel="noopener">why</a>' if l.get("note_url") else ""
    return f'<div class="chg mut" style="white-space:normal;max-width:360px">Note: {esc(l["note"])}{link}</div>'


def vs_links(fmt, lib):
    pairs = [(a, b) for f, a, b in CFG.get("compare", []) if f == fmt and lib in (a, b)]
    return ("<p>⚔️ Compare: " + " · ".join(f'<a href="{vs_slug(a, b)}">{esc(a)} vs {esc(b)}</a>' for a, b in pairs) + "</p>") if pairs else ""


def vs_slug(a, b):
    return f"{a.lower().replace('.', '-')}-vs-{b.lower().replace('.', '-')}.html"


def write_vs_pages(out, runs):
    """One head-to-head page per pair in libs.json "compare" (the comparisons people search for), built from the
    same results as the tables. Returns [(fmt, a, b, slug)] for the index."""
    made = []
    built = time.strftime("%d %B %Y", time.gmtime())
    for fmt, a, b in CFG.get("compare", []):
        libs = runs.get(fmt, {})
        if a not in libs or b not in libs:
            continue
        fcfg = CFG["formats"][fmt]
        commit = CFG["corpora"][fcfg["corpus"]]["commit"]
        (va, ma, ra), (vb, mb, rb) = libs[a][-1], libs[b][-1]
        files = sorted(set(ra) & set(rb))
        bad = ("error", "hang", "crash", "memory")
        ka = {f: kind(ra[f]) for f in files}
        kb = {f: kind(rb[f]) for f in files}
        both = [f for f in files if ka[f] == "text" and kb[f] == "text"]
        only_a = [f for f in files if ka[f] == "text" and kb[f] != "text"]
        only_b = [f for f in files if kb[f] == "text" and ka[f] != "text"]
        ca, _, _ = stats({f: ra[f] for f in files}, set(), fcfg.get("scored"))
        cb, _, _ = stats({f: rb[f] for f in files}, set(), fcfg.get("scored"))
        fa = sum(ka[f] in bad for f in only_b)  # a breaks where b reads
        fb = sum(kb[f] in bad for f in only_a)
        ta, tb = ca["text"], cb["text"]
        if fcfg.get("scored"):  # accuracy, not "any text", decides
            k = score(fcfg)["key"]
            ta, tb = ca.get(k, WORST.get(k, 0)), cb.get(k, WORST.get(k, 0))
        more = (a if better(fcfg, ta, tb) else b if better(fcfg, tb, ta) else None) if fcfg.get("scored") else \
            (a if ta > tb else b if tb > ta else None)
        hi, lo = ((ta, tb) if more in (a, None) else (tb, ta))  # winner's value first
        sa, sb = ca["total_s"], cb["total_s"]
        fast, slow_, fs, ss = (a, b, sa, sb) if sa <= sb else (b, a, sb, sa)
        ratio = f"{ss / fs:,.0f}× faster" if fs > 0 and ss / fs >= 1.5 else "about as fast"
        fv = pct if fcfg.get("scored") else (lambda x: f"{x:,}")
        won = score(fcfg)["won"] if fcfg.get("scored") else "got text from more files"
        verdict = (f"<b>{esc(more)}</b> {won}: {fv(hi)} vs {fv(lo)}{'' if fcfg.get('scored') else f' of {len(files):,}'}."
                   if more else f"Tie: {fv(ta)} for both.")
        repo = CFG["corpora"][fcfg["corpus"]]["repo"]

        def link(f):
            if "{treepath}" in fcfg["blob"]:
                tp = tree_paths(repo, commit).get(f)
                url = fcfg["blob"].format(commit=commit, treepath=tp) if tp else f"https://github.com/{repo}/tree/{commit}"
            else:
                url = fcfg["blob"].format(commit=commit, path=f)
            return f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(f)}</a>'

        def what(r):
            k = kind(r)
            return {"empty": "no text", "unsupported": "format not supported", "missing": "not run"}.get(k) or why(r)

        def flist(fs, rows_other, name_other, cap=40):
            if not fs:
                return "<p class='mut'>None.</p>"
            trs = "".join(f"<tr><td>{link(f)}</td><td>{esc(name_other)}: {esc(what(rows_other[f]))}</td></tr>" for f in fs[:cap])
            more_ = f"<p class='mut'>…and {len(fs) - cap} more: see each library's page.</p>" if len(fs) > cap else ""
            return f'<div class="card"><table class="files">{trs}</table></div>{more_}'

        def bucket(fs, k):
            return sum(1 for f in fs if k(f))

        rows_t = "".join(
            f"<tr><td><a class='lib' href='{slug(fmt, n)}'>{esc(n)}</a> <span class='ver'>{esc(v)}</span></td>"
            f"<td>{c['text']:,}</td><td>{pill(bucket(o, lambda f, kk=kk: kk[f] in bad), 'b')}</td>"
            f"<td>{pill(bucket(o, lambda f, kk=kk: kk[f] == 'empty'), 'w')}</td>"
            f"<td>{c['hang'] + c['crash'] + c['memory']}</td>"
            + (f"<td>{c['unsupported']}</td>" if fcfg.get("per_ext") else "")
            + (f"<td><b>{pct(c.get(score(fcfg)['key']))}</b></td>" if fcfg.get("scored") else "")
            + f"<td>{'–' if c['median_ms'] is None else '&lt;1' if c['median_ms'] < 1 else round(c['median_ms'])}</td><td>{c['total_s']:,}</td></tr>"
            for n, v, c, o, kk in ((a, va, ca, only_b, ka), (b, vb, cb, only_a, kb)))
        table = [(a, va, ma, {f: ra[f] for f in files}), (b, vb, mb, {f: rb[f] for f in files})]
        title_f = fcfg["title"]
        body = f"""<p class="mut"><a href="{fmt}.html">← {esc(title_f)}: all libraries</a> · <a href="index.html">Reader board</a></p>
<h1>{esc(a)} vs {esc(b)}: {esc(fcfg.get('vs_question') or 'which one reads more real-world ' + ('documents' if fcfg.get('per_ext') else title_f + ' files') + '?')}</h1>
<p class="lead">{esc(a)} {esc(va)} and {esc(b)} {esc(vb)} on the same {len(files):,} {esc(fcfg.get('lead') or 'odd real-world files')} from
<a href="https://github.com/{repo}/tree/{commit}" target="_blank" rel="noopener">{esc(CFG['corpora'][fcfg['corpus']]['title'])}</a>{'' if fcfg.get('lead') else ' (bug reports, fuzzers, broken exporters)'}.
Measured, not opinion; re-tested by itself on every new PyPI release. Updated {built}.</p>
<div class="glance"><div class="g1"><span class="mut">{score(fcfg)['card'] if fcfg.get('scored') else 'More files read'}</span><b>{esc(more or 'tie')}</b><span class="mut">{fv(hi)} vs {fv(lo)}</span></div>
<div class="g1"><span class="mut">Faster on this set</span><b>{esc(fast)}</b><span class="mut">{fs:,} s vs {ss:,} s total ({ratio})</span></div>
<div class="g1"><span class="mut">Both read</span><b>{len(both):,}</b><span class="mut">only {esc(a)}: {len(only_a)} · only {esc(b)}: {len(only_b)}</span></div></div>
<p>{verdict} {esc(a)} broke (error, hang or crash) on {fa} files that {esc(b)} read; {esc(b)} broke on {fb} files that {esc(a)} read.</p>
<div class="card"><table><tr><th>Library</th><th>Text from</th><th>🔴 Fails where<br>the other reads</th><th>🟡 Empty where<br>the other reads</th><th>⏱ Hangs or<br>crashes</th>{'<th>Format not<br>supported</th>' if fcfg.get('per_ext') else ''}{'<th>' + score(fcfg)['th'] + '</th>' if fcfg.get('scored') else ''}<th>Median<br>ms/file</th><th>Total<br>seconds</th></tr>{rows_t}</table></div>
{ext_matrix(table, files) if fcfg.get('per_ext') else ''}{group_matrix(table, files, fcfg) if fcfg.get('scored') else ''}
<h2>Files only {esc(a)} reads ({len(only_a)})</h2>{flist(only_a, rb, b)}
<h2>Files only {esc(b)} reads ({len(only_b)})</h2>{flist(only_b, ra, a)}
<h2>What this does not tell you</h2>
<p>This compares <b>not breaking</b>: whether each library gets any text out of each file. It does not rank text quality
(reading order, tables, headings), and speed is one GitHub-hosted runner, default settings, no OCR switched on.
If you need structure for RAG, also look at the output on your own files.</p>
{method_html()}"""
        name = vs_slug(a, b)
        noun = fcfg.get("noun", "files")
        desc = (f"{a} vs {b}, measured on {len(files):,} real-world {noun} with known text: words found {pct(ta)} vs {pct(tb)}; "
                f"{fast} {ratio}. Re-tested on every release." if fcfg.get("scored") else
                f"{a} vs {b}, measured: text from {ta:,} vs {tb:,} of {len(files):,} odd real-world "
                f"{'documents' if fcfg.get('per_ext') else title_f + ' files'}; {fast} {ratio}. Re-tested on every release.")
        (out / name).write_text(page(f"{a} vs {b}: tested on {len(files):,} real-world {noun} ({built[-4:]})", body, desc))
        made.append((fmt, a, b, name))
    return made


SITE = "https://prometheus-maker.github.io/reader-board/"


def write_badges(out, summary):
    """shields.io endpoint JSON per library and format: https://img.shields.io/endpoint?url=<SITE>badges/<fmt>-<lib>.json"""
    (out / "badges").mkdir(exist_ok=True)
    for fmt, f in summary["formats"].items():
        for rank, l in enumerate(f["libs"], 1):
            n = l["fail_readable"]
            color = "brightgreen" if rank == 1 else "green" if n <= 2 else "yellow" if n <= 10 else "orange"
            (out / "badges" / slug(fmt, l["lib"]).replace(".html", ".json")).write_text(json.dumps({
                "schemaVersion": 1, "label": f"reader-board {fmt}",
                "message": f"#{rank} of {len(f['libs'])} · {n} fails on {f['readable']:,} files", "color": color}))


def write_feed(out, runs):
    """Atom feed: one entry per new release tested, with what it broke or fixed."""
    entries = []
    for fmt, libs in runs.items():
        title_f = CFG["formats"].get(fmt, {}).get("title", fmt)
        for lib, lst in libs.items():
            for (v0, _, r0), (v1, m1, r1) in zip(lst, lst[1:]):
                broke, fixed = changes(r0, r1)
                when = m1.get("finished", "2026-11-06T00:00:00Z")
                summary = f"{len(broke)} files newly failing, {len(fixed)} fixed, compared with {v0}."
                entries.append((when, f"{lib} {v1} on {title_f}: {len(broke)} newly failing, {len(fixed)} fixed",
                                SITE + slug(fmt, lib), summary + (" Newly failing: " + ", ".join(broke[:10]) if broke else "")))
    entries.sort(reverse=True)
    items = "".join(f"""<entry><title>{esc(t)}</title><link href="{esc(u)}"/><id>{esc(u)}#{esc(t)}</id>
<updated>{w}</updated><summary>{esc(sm)}</summary></entry>""" for w, t, u, sm in entries[:100])
    upd = entries[0][0] if entries else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (out / "feed.xml").write_text(f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Reader board: what each library release broke or fixed</title>
<link href="{SITE}"/><link rel="self" href="{SITE}feed.xml"/><id>{SITE}</id><updated>{upd}</updated>
<author><name>Prometheus (AI agent)</name></author>{items}</feed>
""")


def group_matrix(table, files, fcfg):
    """Scored formats: words found per folder of the test set (documents, receipts, screenshots, photos...)."""
    groups = {}
    for f in files:
        groups.setdefault(f.split("/", 1)[0] if "/" in f else "other", []).append(f)
    head = "".join(f"<th>{esc(t[0])}</th>" for t in table)
    trs = []
    for g, fs in sorted(groups.items()):
        cells = []
        for t in table:
            a = accuracy({f: t[3][f] for f in fs if f in t[3]})
            sc = score(fcfg)
            v = a.get(sc["key"])
            good, ok = (0.1, 0.25) if sc["lower"] else (0.9, 0.7)
            cls = "b" if v is None else ("g" if better(fcfg, v, good) or v == good else "w" if better(fcfg, v, ok) or v == ok else "b")
            cells.append(f'<td><span class="pill {cls}">{pct(v)}</span></td>')
        trs.append(f"<tr><td><b>{esc(g)}</b> <span class='mut'>{len(fs)} {esc(fcfg.get('noun', 'files'))}</span></td>{''.join(cells)}</tr>")
    return (f'<h3>By kind of {esc(fcfg.get("noun", "files").rstrip("s"))}: {esc(score(fcfg)["head"])}</h3><div class="card"><table><tr><th>{esc(fcfg.get("noun", "files").capitalize())}</th>{head}</tr>'
            f'{"".join(trs)}</table></div>')


def ext_matrix(table, files):
    """One row per file type: how many files each library read (green), returned empty (yellow), failed on (red)."""
    exts = {}
    for f in files:
        exts.setdefault(f.rsplit(".", 1)[-1].lower(), []).append(f)
    head = "".join(f"<th>{esc(t[0])}</th>" for t in table)
    trs = []
    for ext, fs in sorted(exts.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        cells = []
        for t in table:
            ks = [kind(t[3].get(f)) for f in fs]
            if all(k == "unsupported" for k in ks):
                cells.append('<td class="z">not supported</td>')
                continue
            ok, em = ks.count("text"), ks.count("empty")
            bad = sum(k in ("error", "hang", "crash", "memory") for k in ks)
            un = ks.count("unsupported")
            cls = "g" if ok == len(fs) else "b" if bad > ok else "w"
            cells.append(f'<td><span class="pill {cls}">{ok}/{len(fs)}</span>'
                         + (f' <span class="mut">{em} empty</span>' if em else "")
                         + (f' <span class="mut">{bad} failed</span>' if bad else "")
                         + (f' <span class="mut">{un} not supported</span>' if un else "") + "</td>")
        trs.append(f"<tr><td><b>.{esc(ext)}</b> <span class='mut'>{len(fs)} files</span></td>{''.join(cells)}</tr>")
    return (f'<h3>By file type: files read with text</h3><div class="card"><table><tr><th>Type</th>{head}</tr>'
            f'{"".join(trs)}</table></div>')


def method_html():
    return f"""<h2>How this is measured</h2>
<ul>
<li>Each library runs a short recipe from its own docs (<a href="{REPO}/blob/main/board/targets.py">board/targets.py</a>):
open the file, get all text. Installed with the extras the docs recommend for reading
(<code>pypdf[full]</code>: AES decryption, Brotli), nothing else. One worker process per CPU; per file {' / '.join(f'{t} s' for t in sorted({f.get("timeout", 60) for f in CFG['formats'].values()}))} at most (the any-document table allows longer: some tools load models), killed above the memory limit.</li>
<li><b>Fails where another reads</b>: the library raised an error, hung or crashed on a file from which another library
in the same table got text. Files that every library fails on (encrypted, truncated, fuzzed) are not counted against anyone.</li>
<li><b>Empty where another reads</b>: no error, but no text. This is often the recipe, not the library: for example
<code>python-docx</code>'s paragraphs+tables recipe skips headers, text boxes and nested tables. Check the file list.</li>
<li><b>Any document → text</b>: a file type the tool's docs don't list, or that the tool itself rejects as unsupported,
counts as "not supported", not as a failure (lists in <a href="{REPO}/blob/main/board/libs.json">board/libs.json</a>).
Returning a file's raw RTF source counts as empty, for every tool. Each tool runs with default settings: no OCR
switched on by hand.</li>
<li><b>OCR</b>: each image comes with the human-written ground-truth text published with its dataset. <b>Words found</b> is the share of its words
that appear anywhere in the engine's output (reading order doesn't matter); an image that fails or comes back empty counts as 0. The ground truth of
receipts and document pages covers only their annotated text, so a character error rate would punish engines for reading
more than was annotated; it is in the raw results but not ranked. English models, CPU, default settings.</li>
<li><b>Speech</b>: each clip comes with its dataset's reference transcript. Both texts go through Whisper's English
text normalizer (case, punctuation, "twenty" = "20"); <b>word error rate</b> is all substituted, missing and extra
words over all reference words, the usual corpus-level WER. A clip that fails or comes back empty counts all its words
as errors. The three Whisper implementations use the same base.en model; each other engine its documented default
model. English, CPU, default decoding settings.</li>
<li><b>Language detection</b>: each text's language is known from Tatoeba. An answer counts as correct if the
library's code matches after mapping variants to ISO 639-1 (zh-cn → zh, nb → no, iw → he). Close languages count as
different: Croatian detected as Bosnian or Serbian, or Norwegian as Danish, is wrong. One call per text with default
settings and all languages enabled.</li>
<li>Text <i>quality</i> (order, tables, layout) is not ranked in the other tables; they are about not breaking. The raw results
have every output's size and hash.</li>
<li>Same files for every version: the test collections are pinned to one commit. GitHub-hosted runner, so times are
comparable within one table, roughly.</li>
</ul>"""


_TREES = {}


def tree_paths(repo, commit, marker="/src/test/resources/test-documents/"):
    """Local corpus path (module/rest, as corpusdiff stores Tika files) -> path in the repo."""
    if (repo, commit) not in _TREES:
        h = {"User-Agent": "reader-board"}
        if os.environ.get("GITHUB_TOKEN"):
            h["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        try:
            url = f"https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1"
            tree = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60))["tree"]
        except Exception:  # noqa: BLE001 - links fall back to the repo root
            tree = []
        m = {}
        for t in tree:
            head, sep, rest = t["path"].partition(marker)
            if sep and rest:
                m[f"{head.rsplit('/', 1)[-1]}/{rest}"] = t["path"]
        _TREES[(repo, commit)] = m
    return _TREES[(repo, commit)]


def write_lib_page(out, fmt, fcfg, commit, lib, ver, meta, rows, c, fails, empties, hist, nread):
    repo = CFG["corpora"][fcfg["corpus"]]["repo"]

    def link(f):
        if "{treepath}" in fcfg["blob"]:
            tp = tree_paths(repo, commit).get(f)
            url = fcfg["blob"].format(commit=commit, treepath=tp) if tp else f"https://github.com/{repo}/tree/{commit}"
        else:
            url = fcfg["blob"].format(commit=commit, path=f)
        return f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(f)}</a>'

    def ftable(fs, title, cls):
        if not fs:
            return f"<h3>{title}: none</h3>"
        trs = "".join(f"<tr><td>{link(f)}</td><td>{esc(why(rows[f]) if not rows[f].get('ok') else 'no text')}"
                      f" <span class='mut'>({rows[f].get('s', 0)} s)</span></td></tr>" for f in fs)
        return f'<h3>{title}: <span class="pill {cls}">{len(fs)}</span></h3><div class="card"><table class="files">{trs}</table></div>'

    hs = "".join(
        f"<h3>{esc(h['from'])} → {esc(h['to'])}: <span class='down'>{len(h['broke'])} newly failing</span>, "
        f"<span class='up'>{len(h['fixed'])} fixed</span></h3>"
        + (f"<div class='card'><table class='files'>{''.join(f'<tr><td>{link(f)}</td><td>newly failing</td></tr>' for f in h['broke'])}"
           f"{''.join(f'<tr><td>{link(f)}</td><td>fixed</td></tr>' for f in h['fixed'])}</table></div>" if h["broke"] or h["fixed"] else "")
        for h in reversed(hist)) or "<p class='mut'>Only one version tested so far. The next PyPI release is tested automatically.</p>"
    slow = sorted((r for r in rows.values() if r.get("ok")), key=lambda r: -r["s"])[:10]
    slow_t = "".join(f"<tr><td>{link(r['f'])}</td><td>{r['s']} s, {r.get('peak_mb', '?')} MB</td></tr>" for r in slow)
    installed = ", ".join(x for x in meta.get("installed", []) if not x.lower().startswith(("pip==", "setuptools")))
    body = f"""<p class="mut"><a href="{fmt}.html">← {esc(fcfg["title"])} libraries</a> · <a href="index.html">Reader board</a></p>
<h1>{esc(lib)} <span class="ver">{esc(ver)}</span> on {esc(fcfg['title'])}</h1>
<p class="lead">{c['files']:,} files: text from {c['text']:,}, empty {c['empty']:,}, errors {c['error']:,},
hangs {c['hang']}, crashes {c['crash'] + c['memory']}. {nread:,} files give text to at least one library.</p>
{lib_note(fmt, lib)}
{ftable(fails, "🔴 Fails where another library reads text", "b")}
{ftable(empties, "🟡 Empty where another library reads text", "w")}
{vs_links(fmt, lib)}
<h2>Changes between versions</h2>{hs}
<h2>Badge</h2><p>For a README: <img alt="badge" src="https://img.shields.io/endpoint?url={SITE}badges/{slug(fmt, lib).replace('.html', '.json')}"></p>
<pre><code>[![Reader board](https://img.shields.io/endpoint?url={SITE}badges/{slug(fmt, lib).replace('.html', '.json')})]({SITE}{slug(fmt, lib)})</code></pre>
<h2>Slowest files</h2><div class="card"><table class="files">{slow_t}</table></div>
<p class="mut">Recipe: <a href="{REPO}/blob/main/board/targets.py">board/targets.py</a>.
Run: {esc(meta.get('finished', '?'))}, Python {esc(meta.get('python', '?'))}, {esc(meta.get('machine', '?'))}
{f'(<a href="{esc(meta["run_url"])}" target="_blank" rel="noopener">log</a>)' if meta.get('run_url') else ''}.
Installed: {esc(installed)}</p>"""
    (out / slug(fmt, lib)).write_text(page(f"{lib} {ver} on {fcfg['title']}: failures on real-world files – Reader board", body,
                                           f"Every file {lib} {ver} fails on in the {fcfg['title']} test set, with errors."))


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "site"))
