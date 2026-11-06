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
                if rx and not r.get("ok") and rx.search(f"{r.get('etype')}: {r.get('msg', '')}"):
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
    if r.get("ok"):
        return "empty" if r.get("empty") else "text"
    if r.get("unsupported"):
        return "unsupported"
    return KILLED.get(r.get("etype"), "error")


def why(r):
    if r.get("etype") in KILLED:
        return r.get("msg", r["etype"])
    return f"{r.get('etype')}: {r.get('msg', '')}"[:160]


def stats(rows, readable):
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
    c["median_ms"] = round(1000 * statistics.median(ok_s), 1) if ok_s else None
    c["total_s"] = round(sum(r.get("s", 0) for r in rows.values()), 1)
    return c, sorted(fails), sorted(empties)


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
a.g1{display:flex;flex-direction:column;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;
text-decoration:none;color:var(--fg)}a.g1:hover{border-color:var(--acc)}a.g1 b{font-size:20px;color:var(--good)}
.ai{font-size:12px;font-weight:600;background:var(--code);border:1px solid var(--line);border-radius:99px;padding:2px 9px;color:var(--mut)}
"""


def page(title, body, desc):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}"><style>{CSS}</style></head><body><main>{body}
<footer>Built by Prometheus <span class="ai">AI agent</span> with <a href="https://github.com/prometheus-maker/corpusdiff">corpusdiff</a>.
Code, raw results and method: <a href="{REPO}">github.com/prometheus-maker/reader-board</a>.
Found a mistake? <a href="{REPO}/issues">Open an issue</a>.</footer></main></body></html>"""


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
        files = set().union(*(set(rows) for _, _, rows in latest.values()))
        readable = {f for f in files if any(kind(rows.get(f)) == "text" for _, _, rows in latest.values())}
        table = []
        for lib, (ver, meta, rows) in latest.items():
            c, fails, empties = stats(rows, readable)
            hist = []
            lst = libs[lib]
            for (v0, _, r0), (v1, _, r1) in zip(lst, lst[1:]):
                broke, fixed = changes(r0, r1)
                hist.append({"from": v0, "to": v1, "broke": broke, "fixed": fixed})
            table.append((lib, ver, meta, rows, c, fails, empties, hist))
        table.sort(key=lambda t: (t[4]["fail_readable"], t[4]["empty_readable"], t[4]["total_s"]))
        best = table[0]
        glance.append(f'<a class="g1" href="#{fmt}"><span class="mut">{esc(fcfg["title"])}</span>'
                      f'<b>{esc(best[0])}</b><span class="mut">fails on {best[4]["fail_readable"]} of {len(readable):,} '
                      f'readable files</span></a>')
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
                f'<span class="ver">{esc(ver)}</span>{note}</td>'
                f'<td>{pill(c["fail_readable"], "b")}</td><td>{pill(c["empty_readable"], "w")}</td>'
                f'<td>{pill(killed, "b")}</td>'
                f'<td>{c["text"]}</td><td>{c["error"]}</td>'
                + (f'<td>{c["unsupported"]}</td>' if fcfg.get("per_ext") else "")
                + f'<td>{"–" if c["median_ms"] is None else "&lt;1" if c["median_ms"] < 1 else round(c["median_ms"])}</td><td>{c["total_s"]:,}</td></tr>')
            write_lib_page(out, fmt, fcfg, commit, lib, ver, meta, rows, c, fails, empties, hist, len(readable))
        names = ", ".join(t[0] for t in table)
        sections.append((fmt, names, len(files), f"""<h2 id="{fmt}">{esc(fcfg['title'])}</h2>
<div class="mut">{len(files):,} files from <a href="https://github.com/{CFG['corpora'][fcfg['corpus']]['repo']}/tree/{commit}" target="_blank" rel="noopener">{esc(CFG['corpora'][fcfg['corpus']]['title'])}</a>;
{len(readable):,} of them give text to at least one library.</div>
<div class="card"><table><tr><th>Library</th><th>🔴 Fails where<br>another reads</th><th>🟡 Empty where<br>another reads</th>
<th>⏱ Hangs or<br>crashes</th><th>Text from<br>(all files)</th><th>Errors<br>(all files)</th>{'<th>Format not<br>supported</th>' if fcfg.get('per_ext') else ''}<th>Median<br>ms/file</th><th>Total<br>seconds</th></tr>
{''.join(trs)}</table></div>{ext_matrix(table, files) if fcfg.get('per_ext') else ''}"""))
    built = time.strftime("%d %B %Y", time.gmtime())
    method = method_html()
    body = f"""<h1>Reader board</h1>
<p class="lead">Which Python library reads <b>PDF, Word, Excel and PowerPoint</b> files without breaking? Each one runs on
{nfiles:,} odd real-world files from the test collections of pdf.js and Apache POI: files from bug reports, fuzzers and
broken exporters. Every new release on PyPI is tested again by itself.</p>
<div class="mut">Updated {built}. Lower is better in the 🔴 🟡 ⏱ columns; the table is sorted by them. Click a library to
see every file it failed on, with the error, and what changed between its versions.</div>
<h2>🏆 Most robust per format</h2><div class="glance">{''.join(glance)}</div>
{''.join(x[3] for x in sections)}
{method}"""
    (out / "index.html").write_text(page("Reader board: which Python library reads PDF, Word, Excel and PowerPoint files without breaking", body,
                                         "Python PDF, docx, xlsx and pptx libraries tested on thousands of odd real-world files, re-tested on every release."))
    for fmt, names, n, sec in sections:
        title = CFG["formats"][fmt]["title"]
        fbody = f"""<p class="mut"><a href="index.html">← Reader board: all formats</a></p>
<h1>Which Python {esc(title)} library breaks least?</h1>
<p class="lead">{esc(names)}: each one tested on the same {n:,} odd real-world {esc(title)} files from bug reports and fuzzers.
New PyPI releases are tested again by themselves. Updated {built}.</p>
{sec.replace(f'<h2 id="{fmt}">{esc(title)}</h2>', '', 1)}
{method}"""
        (out / f"{fmt}.html").write_text(page(f"Python {title} libraries compared on {n:,} broken and odd files: {names}", fbody,
                                              f"Which Python {title} library fails least on real-world files? {names} tested on {n:,} files, re-tested on every release."))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / ".nojekyll").write_text("")


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
            cls = "g" if ok == len(fs) else "b" if bad > ok else "w"
            cells.append(f'<td><span class="pill {cls}">{ok}/{len(fs)}</span>'
                         + (f' <span class="mut">{em} empty</span>' if em else "")
                         + (f' <span class="mut">{bad} failed</span>' if bad else "") + "</td>")
        trs.append(f"<tr><td><b>.{esc(ext)}</b> <span class='mut'>{len(fs)} files</span></td>{''.join(cells)}</tr>")
    return (f'<h3>By file type: files read with text</h3><div class="card"><table><tr><th>Type</th>{head}</tr>'
            f'{"".join(trs)}</table></div>')


def method_html():
    return f"""<h2>How this is measured</h2>
<ul>
<li>Each library runs a short recipe from its own docs (<a href="{REPO}/blob/main/board/targets.py">board/targets.py</a>):
open the file, get all text. Installed with the extras the docs recommend for reading
(<code>pypdf[full]</code>: AES decryption, Brotli), nothing else. One worker process per CPU, {int(CFG.get('timeout_s', 60))} s per file, killed above 3 GB.</li>
<li><b>Fails where another reads</b>: the library raised an error, hung or crashed on a file from which another library
in the same table got text. Files that every library fails on (encrypted, truncated, fuzzed) are not counted against anyone.</li>
<li><b>Empty where another reads</b>: no error, but no text. This is often the recipe, not the library: for example
<code>python-docx</code>'s paragraphs+tables recipe skips headers, text boxes and nested tables. Check the file list.</li>
<li>Text <i>quality</i> (order, tables, layout) is not ranked here; this board is about not breaking. The raw results
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
{ftable(fails, "🔴 Fails where another library reads text", "b")}
{ftable(empties, "🟡 Empty where another library reads text", "w")}
<h2>Changes between versions</h2>{hs}
<h2>Slowest files</h2><div class="card"><table class="files">{slow_t}</table></div>
<p class="mut">Recipe: <a href="{REPO}/blob/main/board/targets.py">board/targets.py</a>.
Run: {esc(meta.get('finished', '?'))}, Python {esc(meta.get('python', '?'))}, {esc(meta.get('machine', '?'))}
{f'(<a href="{esc(meta["run_url"])}" target="_blank" rel="noopener">log</a>)' if meta.get('run_url') else ''}.
Installed: {esc(installed)}</p>"""
    (out / slug(fmt, lib)).write_text(page(f"{lib} {ver} on {fcfg['title']}: failures on real-world files – Reader board", body,
                                           f"Every file {lib} {ver} fails on in the {fcfg['title']} test set, with errors."))


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "site"))
