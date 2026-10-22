"""Run the engine (extract_labels) on every fixture in manifest.json and score it. Writes results.json.
uv run python -I tests/fixtures/synthetic/agent-detection/analyze.py   (sys.path hack below)"""
import sys, json, contextlib, io
sys.path.insert(0, "/Users/finjo/code/rmlabels")
from pathlib import Path
import pymupdf
from rmlabels import core
HERE = Path(__file__).resolve().parent
man = json.load(open(HERE / "manifest.json"))
res = []
ctr = lambda r: pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
for e in man:
    p = HERE / e["file"]
    doc = pymupdf.open(p); page = doc[0]
    items = core.page_items(page)
    H = page.rect.height
    # free band around mid-height
    cl = [(k, b) for k, b in items if b.y0 < H / 2 - 2 and b.y1 > H / 2 + 2]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            clips = [c for _, _, c, _ in core.extract_labels([p])]
    except Exception as ex:
        res.append(dict(file=e["file"], error=repr(ex))); continue
    truth = [pymupdf.Rect(*t) for t in e["labels"]]
    # per truth label: how many clips contain its centre; per clip: how many truth centres
    cover = [sum(ctr(t) in c for c in clips) for t in truth]
    multi = [sum(ctr(t) in c for t in truth) for c in clips]
    lost = [(k, b) for k, b in items if not any(ctr(b) in c + (-1, -1, 1, 1) for c in clips)]
    lost_ink = [(k, b) for k, b in lost if k != "text"]  # not counting text
    # clip too small for its label? compare each truth rect to the clip containing its centre
    alien = cut = 0
    for t in truth:
        cs = [c for c in clips if ctr(t) in c]
        if len(cs) == 1:
            c = cs[0]
            alien += sum(1 for k, b in items if ctr(b) in c and ctr(b) not in t + (-3, -3, 3, 3))
            cut += sum(1 for k, b in items if ctr(b) in t and ctr(b) not in c)
    res.append(dict(file=e["file"], expect=e["expect"], found=len(clips), params=e["params"],
                    clean=sum(1 for t, cv in zip(truth, cover) if cv == 1) if truth else None,
                    merged=sum(1 for m in multi if m > 1), orphan=sum(1 for m in multi if m == 0),
                    uncovered_truth=sum(1 for c in cover if c == 0),
                    lost_items=len(lost), lost_ink=len(lost_ink), lost_text=len(lost) - len(lost_ink),
                    alien_items=alien, cut_items=cut,
                    mid_crossers=len(cl),
                    ok=(len(clips) == e["expect"] and not lost and all(m == 1 for m in multi) and not alien and not cut)))
json.dump(res, open(HERE / "results.json", "w"), indent=1)
bad = [r for r in res if not r.get("ok")]
print(len(res), "fixtures;", len(bad), "not ok")
