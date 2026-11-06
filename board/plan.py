"""Which (format, library, version) runs are missing? Prints a GitHub Actions matrix as JSON.

A run is missing when results/<fmt>/<lib>/<version>.jsonl.gz doesn't exist for the latest PyPI release.
--all: every library again (e.g. after changing the corpus pin; old results move to an archive by hand)."""
import json, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "board/libs.json").read_text())


def latest(pkg):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=30) as r:
        return json.load(r)["info"]["version"]


def main():
    force = "--all" in sys.argv
    versions, todo = {}, []
    for lib in CFG["libs"]:
        if lib["pkg"] not in versions:
            versions[lib["pkg"]] = latest(lib["pkg"])
        v = versions[lib["pkg"]]
        f = CFG["formats"][lib["fmt"]]
        out = ROOT / f"results/{lib['fmt']}/{lib['id']}/{v}.jsonl.gz"
        if force or not out.exists():
            todo.append({
                "fmt": lib["fmt"], "lib": lib["id"], "pkg": lib["pkg"], "version": v, "fn": lib["fn"],
                "install": " ".join(f"{e}=={v}" for e in (lib["extra"] or [lib["pkg"]])),
                "corpus": f["corpus"], "commit": CFG["corpora"][f["corpus"]]["commit"], "ext": f["ext"],
            })
    print(json.dumps({"include": todo}))


if __name__ == "__main__":
    main()
