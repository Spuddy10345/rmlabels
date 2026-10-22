"""Correct page-geometry fixtures (the 05-pagegeom/mediabox-negative-origin.pdf one draws a label at 0..283 and
then moves the MediaBox elsewhere, so only a corner of the label is on the page: a fixture artefact).
Here the content is translated so the whole label really sits inside the odd MediaBox.
  uv run python tests/fixtures/synthetic/agent-fp/make_geom_fp.py"""
import json
from pathlib import Path
import pymupdf
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SRC = ROOT / "tests/fixtures/synthetic/01-single/evri-standard-4x6.pdf"
MM = 72 / 25.4
MAN = []

def shifted(dx, dy, rotate=0, name="x.pdf", desc="", cropbox=None):
    """PDF user-space translate of the page content by (dx, dy) and move the MediaBox the same way."""
    d = pymupdf.open(SRC); pg = d[0]
    w, h = pg.rect.width, pg.rect.height
    pg.clean_contents()
    xref = pg.get_contents()[0]
    d.update_stream(xref, f"q 1 0 0 1 {dx} {dy} cm\n".encode() + d.xref_stream(xref) + b"\nQ")
    # MediaBox in PDF space (origin bottom-left)
    d.xref_set_key(pg.xref, "MediaBox", f"[{dx} {dy} {dx + w} {dy + h}]")
    if cropbox:
        x0, y0, x1, y1 = cropbox
        d.xref_set_key(pg.xref, "CropBox", f"[{dx + x0} {dy + y0} {dx + x1} {dy + y1}]")
    if rotate:
        d.xref_set_key(pg.xref, "Rotate", str(rotate))
    f = HERE / name; d.save(f); MAN.append(dict(file=name, expect=1, desc=desc, risk="geometry"))

shifted(-200, -300, name="geom-negative-origin-correct.pdf", desc="4x6 label whose MediaBox is [-200 -300 83 125] and whose content is translated to match (a real negative-origin page)")
shifted(1000, 2000, name="geom-positive-offset-origin.pdf", desc="MediaBox [1000 2000 1283 2425], content translated")
shifted(-200, -300, rotate=90, name="geom-negative-origin-rot90.pdf", desc="negative origin + /Rotate 90")
shifted(0, 0, name="geom-cropbox-nonzero-origin.pdf", desc="4x6 label, CropBox inset by 20pt each side (CropBox cuts the label's own margin only)", cropbox=(20, 20, 263, 405))
# A4 with label in the top-left; CropBox = label rect only (common: printers/exporters crop to the label)
d = pymupdf.open(); pg = d.new_page(width=595.28, height=841.89)
pg.show_pdf_page(pymupdf.Rect(0, 0, 283.46, 425.2), pymupdf.open(SRC), 0)
pg.set_cropbox(pymupdf.Rect(0, 0, 283.46, 425.2)); d.save(HERE / "geom-a4-cropbox-to-label.pdf")
MAN.append(dict(file="geom-a4-cropbox-to-label.pdf", expect=1, desc="A4 MediaBox, label top-left, CropBox = exactly the 4x6 label (frame box == page)", risk="geometry"))
m = HERE / "manifest-geom.json"; m.write_text(json.dumps(MAN, indent=1)); print(len(MAN), "geometry fixtures")
