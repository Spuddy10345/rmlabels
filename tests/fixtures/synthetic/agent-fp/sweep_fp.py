"""Run every agent-fp fixture through the dryrun harness and print placements + the clip size
(what is actually cropped), so 'count is right but the crop is wrong' shows up.
  uv run python tests/fixtures/synthetic/agent-fp/sweep_fp.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "tests/synthetic"))
import dryrun
man = {m["file"]: m for m in json.loads((HERE / "manifest.json").read_text())}
res = []
for f, m in man.items():
    st = Path(tempfile.mkdtemp(prefix="sw-", dir="/private/tmp/claude-501/agent-fp-state"))
    r = dryrun.run([str(HERE / f)], state=st, png=True)
    pl = [(p["what"].split(".pdf ")[-1], p["scale"], p["rotated"]) for p in r["placements"]]
    print(f"{f:55s} expect={m['expect']!s:4} found={len(pl)} {pl}")
    res.append(dict(file=f, expect=m["expect"], found=len(pl), placements=pl, png=r.get("pngs")))
(HERE / "sweep-results-fp.json").write_text(json.dumps(res, indent=1))
