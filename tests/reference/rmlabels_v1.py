#!/Users/finjo/.local/share/rmlabels/venv/bin/python
"""
rmlabels — combine shipping labels from any source onto 4-up A4 label sheets
(Double Dragon / Avery J8169 / L7169: 99.1 x 139 mm, 2 x 2).

Each input PDF page is treated as one label; whitespace is auto-cropped, and
pages that are obviously 2-up or 4-up sheets (e.g. Click & Drop A4 output) are
split into their individual labels. Everything stays vector, so barcodes stay
sharp.

Usage:
  rmlabels.py [--start N|auto] [--print|--preview|--grid] [--fill] FILE.pdf ...
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pymupdf

# ── Sheet geometry (mm) — tweak these if the test grid is off ─────────────────
LABEL_W, LABEL_H = 99.1, 139.0
TOP, LEFT = 9.5, 4.65        # sheet margin to first label edge
PITCH_X, PITCH_Y = 101.6, 139.0  # label-to-label distance (2.5 mm column gap)
PAD = 3.0                    # keep print this far inside each label edge
NUDGE_X, NUDGE_Y = 0.0, 0.0  # printer calibration offset (+ = right / down)

PRINTER = os.environ.get("RMLABELS_PRINTER", "EPSON_XP_2200_Series")
STATE = Path.home() / ".local/share/rmlabels/next_position"
OUT_DIR = Path.home() / "Downloads"

MM = 72 / 25.4
A4 = pymupdf.paper_rect("a4")


def cell_rect(pos: int, pad: float = PAD) -> pymupdf.Rect:
    """pos 0..3 → (TL, TR, BL, BR) label rectangle in points."""
    col, row = pos % 2, pos // 2
    x0 = LEFT + col * PITCH_X + NUDGE_X
    y0 = TOP + row * PITCH_Y + NUDGE_Y
    return pymupdf.Rect(x0 + pad, y0 + pad, x0 + LABEL_W - pad, y0 + LABEL_H - pad) * MM


# ── Label detection ───────────────────────────────────────────────────────────
def page_items(page: pymupdf.Page):
    """(kind, rect) for every visibly drawn thing, minus full-page backgrounds.
    Text is taken per span (bboxlog text runs can span half the page)."""
    area = page.rect.get_area()
    out = []
    for kind, r in page.get_bboxlog():
        r = pymupdf.Rect(r) & page.rect
        if r.is_empty or "text" in kind:
            continue
        if kind.startswith("fill-path") and r.get_area() > 0.9 * area:
            continue  # white page background
        out.append((kind, r))
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                r = pymupdf.Rect(s["bbox"]) & page.rect
                if s["text"].strip() and not r.is_empty:
                    out.append(("text", r))
    return out


def find_frame(page: pymupdf.Page, items):
    """The label's own printed border, if it has one.

    Many labels (Parcel2Go, eBay, Click & Drop) draw a box round the label and
    put cut guides, arrows or instructions outside it. Cropping to that box
    ignores the junk and makes the same label come out the same size whatever
    page it was supplied on. Only used if every distinct image/barcode and
    nearly all the text sit inside it, so nothing real is ever cut off.

    An image that is merely *repeated* outside the frame is fine as long as one
    copy is inside (Parcel2Go reprints the Royal Mail logo on its Certificate of
    Posting below the label). The frame needn't be half the size of everything
    on the page either: on a full A4 Parcel2Go sheet the label is a 100x150 mm
    box beside instruction panels and a certificate, so anything >= 50 mm a
    side counts.
    """
    content = union(items)
    if content.is_empty:
        return None
    min_w = min(0.5 * content.width, 50 * MM)
    min_h = min(0.5 * content.height, 50 * MM)
    boxes = [d["rect"] for d in page.get_drawings()
             if d.get("color") is not None                      # stroked outline
             and d["rect"].width > min_w
             and d["rect"].height > min_h
             and d["rect"].intersects(content)
             and d["rect"].get_area() < 1.6 * content.get_area()]  # not a whole-sheet border
    centre = lambda r: pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
    region = content + (-2, -2, 2, 2)
    images = []  # (identity, rect) for images belonging to this region
    for n, info in enumerate(page.get_image_info(xrefs=True)):
        r = pymupdf.Rect(info["bbox"])
        if centre(r) in region:
            images.append((info.get("xref") or ("inline", n), r))  # inline images: each unique
    texts = [r for k, r in items if k == "text"]
    for frame in sorted(boxes, key=lambda r: -r.get_area()):
        loose = frame + (-4, -4, 4, 4)
        inside = {key for key, r in images if centre(r) in loose}
        if any(key not in inside for key, _ in images):
            continue
        if texts and sum(centre(r) in loose for r in texts) < 0.85 * len(texts):
            continue
        return frame + (-1.5, -1.5, 1.5, 1.5)  # keep the border line itself
    return None


def looks_like_label(items) -> bool:
    """Needs a barcode-ish thing: an image or lots of vector paths."""
    images = sum(1 for k, _ in items if "image" in k)
    paths = sum(1 for k, _ in items if "path" in k)
    return images > 0 or paths >= 15


def union(items) -> pymupdf.Rect:
    r = pymupdf.Rect()
    for _, b in items:
        r |= b
    return r


def split_regions(page: pymupdf.Page, items):
    """Split a page into sub-labels along its centre lines when nothing crosses them."""
    W, H = page.rect.width, page.rect.height
    tol = 2  # pt

    def crosses_x(x):
        return any(b.x0 < x - tol and b.x1 > x + tol for _, b in items)

    def crosses_y(y):
        return any(b.y0 < y - tol and b.y1 > y + tol for _, b in items)

    xs = [0, W] if crosses_x(W / 2) else [0, W / 2, W]
    ys = [0, H] if crosses_y(H / 2) else [0, H / 2, H]
    regions = []
    for yi in range(len(ys) - 1):
        for xi in range(len(xs) - 1):
            cell = pymupdf.Rect(xs[xi], ys[yi], xs[xi + 1], ys[yi + 1])
            sub = [(k, b) for k, b in items if b.intersects(cell) and (b & cell).get_area() > 0.5 * b.get_area()]
            if sub and looks_like_label(sub):
                regions.append(sub)
    return regions


SHEET_MARK = "rmlabels-sheet"
STRAY_GLYPHS = set("↑↓←→⬆⬇⇧")  # Parcel2Go appends a big lone "↑" after its CO2e footer


def strip_stray_glyphs(page: pymupdf.Page) -> int:
    """Remove lone arrow characters (in memory only; the source file is untouched)."""
    hits = [pymupdf.Rect(s["bbox"]) for b in page.get_text("dict")["blocks"]
            for line in b.get("lines", []) for s in line["spans"]
            if s["text"].strip() and set(s["text"].strip()) <= STRAY_GLYPHS]
    for r in hits:
        # The glyph's box is much taller than its ink and overlaps the footer line,
        # so redact only a tiny square at its centre to avoid eating neighbouring text.
        c = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        page.add_redact_annot(pymupdf.Rect(c.x - 1, c.y - 1, c.x + 1, c.y + 1), fill=False)
    if hits:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    return len(hits)


def is_own_sheet(doc: pymupdf.Document, p: Path, page: pymupdf.Page) -> bool:
    """A sheet this tool already made (re-fed to reprint or move labels)."""
    a4 = abs(page.rect.width - A4.width) < 3 and abs(page.rect.height - A4.height) < 3
    marked = SHEET_MARK in (doc.metadata or {}).get("keywords", "")
    return a4 and (marked or p.name.startswith("rm-labels-"))


def own_sheet_cells(page: pymupdf.Page):
    """Split one of our own 4-up sheets back into its label cells.

    Generic detection can't be trusted here: the labels are nested, clipped
    PDFs, and the clipped-away parts (page backgrounds, footers) still report
    their full size, so the sheet looks like one giant label. We know exactly
    where we put each label, so just cut on the grid."""
    marks = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    marks += [pymupdf.Rect(s["bbox"]) for b in page.get_text("dict")["blocks"]
              for line in b.get("lines", []) for s in line["spans"] if s["text"].strip()]
    cells = []
    for pos in range(4):
        cell = cell_rect(pos, pad=0)
        if any(pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2) in cell for r in marks):
            cells.append((pos, cell_rect(pos)))  # padded cell = exactly what we printed
    return cells


def extract_labels(paths: list[Path]):
    """Yield (doc, page_no, clip_rect, description)."""
    for p in paths:
        doc = pymupdf.open(p)
        for page in doc:
            if page.rotation:
                page.remove_rotation()
            if not is_own_sheet(doc, p, page):
                strip_stray_glyphs(page)
            if is_own_sheet(doc, p, page):
                for pos, clip in own_sheet_cells(page):
                    yield doc, page.number, clip, f"{p.name} p{page.number + 1} label {pos + 1}"
                continue
            items = page_items(page)
            if not items:
                continue
            regions = split_regions(page, items)
            if not regions and looks_like_label(items):
                regions = [items]
            for i, sub in enumerate(regions):
                frame = find_frame(page, sub)
                clip = frame if frame else union(sub) + (-2, -2, 2, 2)
                clip &= page.rect
                tag = f"{p.name} p{page.number + 1}" + (f" #{i + 1}" if len(regions) > 1 else "")
                yield doc, page.number, clip, tag


# ── Imposition ────────────────────────────────────────────────────────────────
def place(out: pymupdf.Document, labels, start: int, fill: bool) -> int:
    pos, page = start, None
    for doc, pno, clip, tag in labels:
        if page is None or pos == 4:
            page = out.new_page(width=A4.width, height=A4.height)
            pos %= 4  # first sheet keeps `start`; later sheets begin at label 1
        cell = cell_rect(pos)
        # rotate landscape labels so they use the portrait cell properly
        rotate = 90 if (clip.width > clip.height) != (cell.width > cell.height) else 0
        cw, ch = (clip.height, clip.width) if rotate else (clip.width, clip.height)
        scale = min(cell.width / cw, cell.height / ch)
        if not fill:
            scale = min(scale, 1.0)  # never enlarge unless asked
        w, h = cw * scale, ch * scale
        target = pymupdf.Rect(cell.x0 + (cell.width - w) / 2, cell.y0 + (cell.height - h) / 2,
                              cell.x0 + (cell.width + w) / 2, cell.y0 + (cell.height + h) / 2)
        page.show_pdf_page(target, doc, pno, clip=clip, rotate=rotate, keep_proportion=True)
        print(f"  label {pos + 1} of sheet {len(out)} ← {tag}  ({scale:.0%}{', rotated' if rotate else ''})")
        pos += 1
    return pos


def test_grid(out: pymupdf.Document):
    page = out.new_page(width=A4.width, height=A4.height)
    for pos in range(4):
        edge = cell_rect(pos, pad=0)
        page.draw_rect(edge, color=(0, 0, 0), width=0.5)
        page.draw_rect(cell_rect(pos), color=(1, 0, 0), width=0.3, dashes="[2] 0")
        c = edge.tl + (edge.br - edge.tl) / 2
        page.draw_line(c - (8, 0), c + (8, 0)); page.draw_line(c - (0, 8), c + (0, 8))
        page.insert_text(edge.tl + (10, 24), f"Label {pos + 1}", fontsize=14)
    page.insert_text((40, A4.height - 14), "Black = label edge, red = safe print area. Print at 100% on plain paper, "
                     "hold against a label sheet to the light.", fontsize=7)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--start", default="1", help="first free label on the sheet: 1-4 (TL,TR,BL,BR) or 'auto'")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--print", action="store_true", help="send straight to the printer at 100%%")
    mode.add_argument("--preview", action="store_true", help="open in Preview (default)")
    ap.add_argument("--grid", action="store_true", help="make a calibration/test grid instead (combine with --print)")
    ap.add_argument("--fill", action="store_true", help="allow enlarging small labels to fill the label")
    ap.add_argument("--mono", action="store_true", help="print with the black cartridge only (greyscale driver mode)")
    ap.add_argument("--draft", action="store_true", help="use the driver's normal 360 dpi quality instead of Fine 720 dpi")
    a = ap.parse_args()

    out = pymupdf.open()
    if a.grid:
        test_grid(out)
        start = end = 0
    else:
        files = [f.expanduser() for f in a.files if f.suffix.lower() == ".pdf" and f.expanduser().exists()]
        if not files:
            sys.exit("No PDF files given.")
        if a.start == "auto":
            start = int(STATE.read_text()) if STATE.exists() else 0
        else:
            start = max(1, min(4, int(a.start))) - 1
        labels = list(extract_labels(files))
        if not labels:
            sys.exit("Couldn't find any labels in those PDFs.")
        if a.start == "auto" and start:
            used = "label 1 is" if start == 1 else f"labels 1-{start} are"
            print(f"Continuing a part-used sheet: {used} treated as already used.")
            print("Load that sheet, or choose 'New sheet' if you put in a fresh one.")
        print(f"{len(labels)} label(s) from {len(files)} file(s), starting at position {start + 1}:")
        end = place(out, labels, start, a.fill)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = OUT_DIR / (f"rm-labels-grid.pdf" if a.grid else f"rm-labels-{stamp}.pdf")
    out.set_metadata({"keywords": SHEET_MARK, "creator": "rmlabels"})
    out.save(dest, garbage=3, deflate=True)
    print(f"→ {dest}")

    if a.print:
        opts = ["PageSize=A4", "print-scaling=none", "EPIJ_Bdls=0", "EPIJ_Medi=0"]
        if not a.draft:
            # Plain paper + Fine: 720 dpi, one-way head passes -> crisp barcodes and solid text
            opts += ["EPIJ_Qual=304", "Resolution=720x720dpi", "EPIJ_Mode=3", "EPIJ_OPT_Bi_D=0"]
        if a.mono:
            opts += ["ColorModel=Mono", "EPIJ_Ink_=0"]
        cmd = ["lp", "-d", PRINTER]
        for o in opts:
            cmd += ["-o", o]
        subprocess.run(cmd + [str(dest)], check=True)
        if not a.grid:
            STATE.write_text(str(end % 4))
            print(f"Printed. Next free label on the last sheet: {end % 4 + 1 if end % 4 else 'none (sheet full)'}")
    else:
        # Preview never uses up labels: only a real print moves the saved position on
        subprocess.run(["open", "-a", "Preview", str(dest)])


if __name__ == "__main__":
    main()
