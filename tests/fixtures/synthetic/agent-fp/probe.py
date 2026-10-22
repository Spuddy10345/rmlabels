"""Probe helper (agent-fp). Runs files through the dryrun harness, one private state per run, and
renders the source pages + output sheet PNGs into /private/tmp/claude-501/agent-fp-state/view/.

  uv run python tests/fixtures/synthetic/agent-fp/probe.py [--src] file.pdf ...
"""
import json, shutil, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests/synthetic"))
import dryrun, pymupdf  # noqa

VIEW = Path("/private/tmp/claude-501/agent-fp-state/view"); VIEW.mkdir(parents=True, exist_ok=True)

def probe(path, extra=(), src=False, dpi=45):
    path = Path(path)
    st = Path(tempfile.mkdtemp(prefix="afp-", dir="/private/tmp/claude-501/agent-fp-state"))
    r = dryrun.run([*extra, str(path)], state=st, png=True)
    n = path.stem
    outs = []
    for i, p in enumerate(r.get("pngs", [])):
        dst = VIEW / f"{n}-sheet{i+1}.png"; shutil.copy(p, dst); outs.append(str(dst))
    if src:
        d = pymupdf.open(path)
        for i, pg in enumerate(d):
            dst = VIEW / f"{n}-src-p{i+1}.png"; pg.get_pixmap(dpi=dpi).save(dst); outs.append(str(dst))
    pl = [(p["pos"], p["sheet"], p["what"].replace(path.name, ""), p["scale"], p["rotated"]) for p in r["placements"]]
    print(f"{path.name}: exit={r['exit']} found={len(pl)} {pl}")
    if r.get("traceback"): print(r["traceback"])
    for o in outs: print("   ", o)
    return r

if __name__ == "__main__":
    a = sys.argv[1:]; src = "--src" in a
    a = [x for x in a if x != "--src"]
    for f in a: probe(f, src=src)
