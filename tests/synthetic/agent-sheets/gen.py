"""Build exact N-page label PDFs by cycling 4 clean single-label fixtures. Output: gen/nN.pdf"""
import pymupdf
from pathlib import Path
F = Path("/Users/finjo/code/rmlabels/tests/fixtures/synthetic/01-single")
srcs = [F/n for n in ("evri-standard-4x6.pdf","gls-de-4x6.pdf","colissimo-fr-4x6.pdf","royalmail-tracked48-a4.pdf")]
out = Path(__file__).parent/"gen"; out.mkdir(exist_ok=True)
for n in (1,2,3,4,5,9,13):
    d = pymupdf.open()
    for i in range(n):
        s = pymupdf.open(srcs[i%4]); d.insert_pdf(s)
    d.save(out/f"n{n}.pdf")
