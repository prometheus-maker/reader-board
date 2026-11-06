"""Which (format, library, version) runs are missing? Prints a GitHub Actions matrix as JSON.

A run is missing when results/<fmt>/<lib>/<version>.jsonl.gz doesn't exist for the latest PyPI release.
--all: every library again (e.g. after changing the corpus pin; old results move to an archive by hand).
--history N: also the N-1 releases before the latest (to see what each release broke or fixed)."""
import json, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "board/libs.json").read_text())


def releases(pkg, n):
    """The latest release and up to n-1 releases before it (no pre-releases, no yanked ones)."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=30) as r:
        d = json.load(r)
    latest = d["info"]["version"]
    good = [v for v, files in d["releases"].items()
            if files and not all(f.get("yanked") for f in files) and v.replace(".", "").isdigit()]
    good.sort(key=lambda v: [int(p) for p in v.split(".")])
    older = [v for v in good if [int(p) for p in v.split(".")] < [int(p) for p in latest.split(".")]] if latest in good else []
    return [latest] + older[::-1][: n - 1]


def main():
    force = "--all" in sys.argv
    n = int(sys.argv[sys.argv.index("--history") + 1]) if "--history" in sys.argv else 1
    versions, todo = {}, []
    for lib in CFG["libs"]:
        if lib["pkg"] not in versions:
            versions[lib["pkg"]] = releases(lib["pkg"], n)
        f = CFG["formats"][lib["fmt"]]
        for v in versions[lib["pkg"]]:
            if not force and (ROOT / f"results/{lib['fmt']}/{lib['id']}/{v}.jsonl.gz").exists():
                continue
            todo.append({
                "fmt": lib["fmt"], "lib": lib["id"], "pkg": lib["pkg"], "version": v, "fn": lib["fn"],
                "install": " ".join(f"{e}=={v}" for e in (lib["extra"] or [lib["pkg"]])),
                "corpus": f["corpus"], "commit": CFG["corpora"][f["corpus"]]["commit"], "ext": f["ext"],
                "apt": lib.get("apt", ""), "jobs": lib.get("jobs", 4), "timeout": f.get("timeout", 60),
                "mem_mb": f.get("mem_mb", 3000),
            })
    print(json.dumps({"include": todo}))


if __name__ == "__main__":
    main()
