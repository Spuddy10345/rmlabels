"""Prototype (monkeypatch only, rmlabels/ untouched): refuse a centre split if it would make cells smaller than a real label.
Compares label counts on parent manifest fixtures (01-single, 03-multiup) with/without the guard."""
import sys, json, contextlib, io
sys.path.insert(0, "/Users/finjo/code/rmlabels")
from pathlib import Path
import pymupdf
from rmlabels import core
SYN = Path("/Users/finjo/code/rmlabels/tests/fixtures/synthetic")
MM = core.MM
orig = core.split_regions
MIN_SHORT, MIN_LONG = float(sys.argv[1]) if len(sys.argv) > 1 else 85, float(sys.argv[2]) if len(sys.argv) > 2 else 120

def guarded(page, items):
    W, H = page.rect.width, page.rect.height
    tol = 2
    cx = any(b.x0 < W / 2 - tol and b.x1 > W / 2 + tol for _, b in items)
    cy = any(b.y0 < H / 2 - tol and b.y1 > H / 2 + tol for _, b in items)
    ok = lambda w, h: min(w, h) >= MIN_SHORT * MM and max(w, h) >= MIN_LONG * MM
    xs = [0, W] if cx or not ok(W / 2, H if cy else H / 2) and True else [0, W / 2, W]
    ys = [0, H] if cy else [0, H / 2, H]
    # cells after the proposed cut
    xs = [0, W / 2, W] if not cx else [0, W]
    cw = (xs[1] - xs[0]); chh = (ys[1] - ys[0])
    if len(xs) > 2 and not ok(cw, chh): xs = [0, W]; cw = W; chh = ys[1] - ys[0]
    if len(ys) > 2 and not ok(xs[1] - xs[0], chh): ys = [0, H]
    regions = []
    for yi in range(len(ys) - 1):
        for xi in range(len(xs) - 1):
            cell = pymupdf.Rect(xs[xi], ys[yi], xs[xi + 1], ys[yi + 1])
            sub = [(k, b) for k, b in items if b.intersects(cell) and (b & cell).get_area() > 0.5 * b.get_area()]
            if sub and core.looks_like_label(sub):
                regions.append(sub)
    return regions

def count(p):
    with contextlib.redirect_stdout(io.StringIO()):
        try: return len([1 for _ in core.extract_labels([Path(p)])])
        except Exception as e: return -1

man = json.load(open(SYN / "manifest.json"))
rows = []
for e in man:
    if not e["file"].startswith(("01-single", "03-multiup")): continue
    p = SYN / e["file"]
    core.split_regions = orig; a = count(p)
    core.split_regions = guarded; b = count(p)
    rows.append((e["file"], e["expect"], a, b))
core.split_regions = orig
print("thresholds short>=%smm long>=%smm" % (MIN_SHORT, MIN_LONG))
print("baseline ok:", sum(x[1] == x[2] for x in rows), "guarded ok:", sum(x[1] == x[3] for x in rows), "of", len(rows))
for f, ex, a, b in rows:
    if a != b or (b != ex): print(f"  {f}: expect {ex} base {a} guarded {b}")
