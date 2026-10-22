"""How many labels are found when only the page height changes by k pt (extra white at the bottom / crop)? Uses engine directly."""
import sys, io, contextlib
sys.path.insert(0, "/Users/finjo/code/rmlabels")
import pymupdf
from rmlabels import core
F = "/Users/finjo/code/rmlabels/tests/fixtures/synthetic/01-single/"
for name in sys.argv[1:] or ["dpd-nextday-4x6.pdf", "hermes-de-a6.pdf", "inpost-locker-4x6.pdf"]:
    out = []
    for k in range(-12, 13, 2):
        doc = pymupdf.open(F + name); pg = doc[0]
        mb = pg.mediabox  # k>0: extend page downward by k pt (white margin); k<0: crop k pt off the bottom
        pg.set_mediabox(pymupdf.Rect(mb.x0, mb.y0, mb.x1, mb.y1 - k * -1 if False else mb.y1 + k)); pg.set_cropbox(pg.mediabox)
        with contextlib.redirect_stdout(io.StringIO()):
            n = len(list(core.extract_labels([(name, doc)])))
        out.append(f"{k:+d}:{n}")
    print(name, " ".join(out))
