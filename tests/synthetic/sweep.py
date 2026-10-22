"""Run every fixture in manifest.json on its own and compare labels found with `expect`.

    uv run python tests/synthetic/sweep.py [substring-filter ...]   # writes sweep-results.json beside manifest

Verdicts: ok | SUSPECT (right count, but a lone label scaled <75%: probably cropped too wide) | MISSED (found fewer) | EXTRA (found more) | CRASH (traceback) | ~ (expect unknown)
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dryrun import ROOT, run  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "synthetic"


def one(m):
    r = run([str(FIX / m["file"])])
    n = len(r.get("placements", []))
    exp = m["expect"]
    if r.get("exit") == "EXCEPTION" or r.get("exit") == "child-crash":
        v = "CRASH"
    elif exp is None:
        v = "~"
    elif n == exp:
        scales = [x["scale"] for x in r["placements"] if x["scale"]]
        # a lone label should come out near 100%; ~50% means the whole page was cropped as one 'label'
        v = "SUSPECT" if exp == 1 and scales and min(scales) < 75 else "ok"
    else:
        v = "MISSED" if n < exp else "EXTRA"
    return dict(file=m["file"], expect=exp, found=n, verdict=v, exit=r.get("exit"), risk=m["risk"], desc=m["desc"],
                stderr=r.get("stderr", "").strip()[-300:], tb=(r.get("traceback") or "").strip().splitlines()[-1:] or None,
                placements=[p["what"] + (f" ({p['scale']}%)" if p["scale"] else "") for p in r.get("placements", [])][:6])


if __name__ == "__main__":
    manifest = json.loads((FIX / "manifest.json").read_text())
    filt = sys.argv[1:]
    manifest = [m for m in manifest if not filt or any(f in m["file"] for f in filt)]
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(one, manifest))
    (FIX / "sweep-results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    for r in results:
        if r["verdict"] != "ok":
            print(f"{r['verdict']:7} exp={r['expect']!s:4} got={r['found']:<3} {r['file']}  {r['tb'] or ''}")
    from collections import Counter
    print(Counter(r["verdict"] for r in results))
