"""Threshold experiments on core.looks_like_label / extract_labels (agent-fp). Read-only on rmlabels.
  RMLABELS_DATA=... RMLABELS_CONFIG=... uv run python tests/fixtures/synthetic/agent-fp/thresholds.py"""
import io, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import pymupdf
from PIL import Image
from rmlabels import core
MM = core.MM

def n_found(doc, name="x.pdf"):
    return [ (round(c.width/MM), round(c.height/MM)) for _, _, c, _ in core.extract_labels([(name, doc)])]

def text_page(doc=None, rows=12):
    doc = doc or pymupdf.open()
    pg = doc.new_page(width=595.28, height=841.89)
    pg.insert_text((50, 60), "Invoice 1001", fontsize=18)
    for i in range(rows):
        pg.insert_text((50, 100 + 18*i), f"{i+1} x Widget model {i+1}      GBP {i+4}.99", fontsize=10)
    return doc, pg

print("== A: horizontal rules only (a ruled list), text invoice with N rules")
for n in (5, 10, 14, 15, 16, 20):
    doc, pg = text_page()
    for i in range(n): pg.draw_line((45, 95 + 15*i), (550, 95 + 15*i), width=0.5)
    print(f"  {n:2d} rules ->", n_found(doc))

print("== B: R rows x C cols grid drawn as one rect per cell (typical 'table with borders')")
for r, c in ((3, 4), (4, 4), (3, 5), (5, 3), (4, 3)):
    doc, pg = text_page()
    for i in range(r):
        for j in range(c): pg.draw_rect(pymupdf.Rect(45 + j*125, 95 + i*18, 170 + j*125, 113 + i*18), width=0.5)
    print(f"  {r}x{c}={r*c:2d} cell rects ->", n_found(doc))

print("== C: R rows x C cols drawn as ONE stroked path per table (single drawing op)")
doc, pg = text_page()
s = pg.new_shape()
for i in range(10):
    for j in range(5): s.draw_rect(pymupdf.Rect(45 + j*100, 95 + i*18, 145 + j*100, 113 + i*18))
s.finish(width=0.5); s.commit()
print("  50 cells in one stroke op ->", n_found(doc), "(paths counted:", sum('path' in k for k,_ in core.page_items(pg)), ")")

print("== D: a single image of various sizes on an otherwise text-only invoice")
def png(px, col=(200, 30, 30)):
    b = io.BytesIO(); Image.new("RGB", (px, px), col).save(b, "PNG"); return b.getvalue()
for px, mm in ((1, 0.3), (1, 1), (4, 3), (50, 10), (200, 40), (200, 100)):
    doc, pg = text_page()
    pg.insert_image(pymupdf.Rect(400, 30, 400 + mm*MM, 30 + mm*MM), stream=png(px))
    print(f"  {px}px image shown at {mm}mm ->", n_found(doc))
print("  WHITE 20mm image ->", end=" "); doc, pg = text_page(); pg.insert_image(pymupdf.Rect(400,30,457,87), stream=png(20,(255,255,255))); print(n_found(doc))

print("== E: page-sized watermark/letterhead image (decorative_images rule: >=60% page AND <0.5% ink)")
for name, col in (("pale-grey 230", (230,230,230)), ("mid-grey 150 (dark)", (150,150,150)), ("transparent-ish white", (255,255,255))):
    doc, pg = text_page()
    pg.insert_image(pg.rect, stream=png(40, col), overlay=False)
    print(f"  full-page {name} ->", n_found(doc))
doc, pg = text_page()
b = io.BytesIO(); im = Image.new("RGB", (400, 560), (255,255,255)); im.putpixel((5,5), (0,0,0)); 
from PIL import ImageDraw
ImageDraw.Draw(im).text((100,250), "DRAFT", fill=(0,0,0)); im.save(b, "JPEG"); pg.insert_image(pg.rect, stream=b.getvalue(), overlay=False)
print("  full-page JPEG letterhead (JPEG is never treated as overlay) ->", n_found(doc))

print("== F: other path-ish things in ordinary documents")
doc, pg = text_page()
for i in range(8): pg.draw_rect(pymupdf.Rect(45, 95+i*36, 550, 95+i*36+18), color=None, fill=(0.93,0.93,0.93))  # zebra stripes
print("  8 zebra-striped rows (fill only) ->", n_found(doc))
doc, pg = text_page()
for i in range(8): pg.draw_rect(pymupdf.Rect(45, 95+i*36, 550, 95+i*36+18), color=None, fill=(0.93,0.93,0.93)); pg.draw_line((45, 95+i*36+18),(550,95+i*36+18))
print("  8 zebra stripes + 8 rules (=16) ->", n_found(doc))
