"""Prototype (monkeypatch, NOT editing rmlabels/): count sub-path segments via get_drawings() instead of bboxlog entries."""
import sys; sys.path.insert(0, "/Users/finjo/code/rmlabels")
import pymupdf
from rmlabels import core
for p in sys.argv[1:]:
    pg = pymupdf.open(p)[0]
    dr = pg.get_drawings()
    segs = sum(len(d["items"]) for d in dr)
    big = [(len(d["items"]), tuple(round(v) for v in d["rect"])) for d in dr if len(d["items"]) > 3]
    print(p.split("/")[-1], "bboxlog paths", sum('path' in k for k, _ in core.page_items(pg)), "drawings", len(dr), "segments", segs, "complex:", big[:4])
