"""Write the facts of one run next to its results: versions of everything installed, Python, machine, settings."""
import json, os, platform, shutil, subprocess, sys, time
from importlib import metadata

out, pkg, version, corpus, commit, timeout = sys.argv[1:7]
json.dump({
    "pkg": pkg, "version": version, "corpus": corpus, "corpus_commit": commit, "timeout_s": float(timeout),
    "python": platform.python_version(), "machine": f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs",
    "installed": sorted(f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions()),
    "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "run_url": os.environ.get("RUN_URL", ""),
    # wrappers around a system program (pytesseract -> tesseract): the program's version is what gets measured
    "engine": (subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout.split("\n")[0]
               if pkg == "pytesseract" and shutil.which("tesseract") else ""),
}, open(out, "w"), indent=1)
