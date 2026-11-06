"""Write the facts of one run next to its results: versions of everything installed, Python, machine, settings."""
import json, os, platform, sys, time
from importlib import metadata

out, pkg, version, corpus, commit, timeout = sys.argv[1:7]
json.dump({
    "pkg": pkg, "version": version, "corpus": corpus, "corpus_commit": commit, "timeout_s": float(timeout),
    "python": platform.python_version(), "machine": f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs",
    "installed": sorted(f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions()),
    "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "run_url": os.environ.get("RUN_URL", ""),
}, open(out, "w"), indent=1)
