"""
The label engine: find labels in PDF pages and impose them 4-up on A4
(Double Dragon / Avery J8169 / L7169: 99.1 x 139 mm, 2 x 2).

Each input PDF page is treated as one label; whitespace is auto-cropped, and
pages that are obviously 2-up or 4-up sheets (e.g. Click & Drop A4 output) are
split into their individual labels. Everything stays vector, so barcodes stay
sharp.
"""
from __future__ import annotations

import functools
import html
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .config import CONFIG, DATA_DIR

# ── Sheet geometry (mm) — tweak these if the test grid is off ─────────────────
LABEL_W, LABEL_H = 99.1, 139.0
TOP, LEFT = 9.5, 4.65        # sheet margin to first label edge
PITCH_X, PITCH_Y = 101.6, 139.0  # label-to-label distance (2.5 mm column gap)
PAD = 3.0                    # keep print this far inside each label edge
NUDGE_X, NUDGE_Y = float(CONFIG["nudge_x"]), float(CONFIG["nudge_y"])  # printer calibration (+ = right / down)

STATE = DATA_DIR / "next_position"  # first free label (older form, still written)
USED = DATA_DIR / "used_mask"          # which of the 4 labels are used, e.g. 5 = labels 1 and 3

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
    overlays = decorative_images(page)
    other_images = [
        pymupdf.Rect(info["bbox"])
        for info in page.get_image_info(xrefs=True)
        if not any(
            info.get("xref") == xref and abs(pymupdf.Rect(info["bbox"]) - overlay_rect) < 2
            for xref, overlay_rect in overlays
        )
    ]
    out = []
    for kind, r in page.get_bboxlog():
        r = pymupdf.Rect(r) & page.rect
        if r.is_empty or "text" in kind:
            continue
        if kind.startswith("fill-path") and r.get_area() > 0.9 * area:
            continue  # white page background
        if ("image" in kind
                and any(abs(r - overlay_rect) < 2 for _, overlay_rect in overlays)
                and not any(abs(r - image_rect) < 2 for image_rect in other_images)):
            continue  # cut-guide overlay drawn as a page-sized picture
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
    overlays = {xref for xref, _ in decorative_images(page)}
    images = []  # (identity, rect) for images belonging to this region
    for n, info in enumerate(page.get_image_info(xrefs=True)):
        r = pymupdf.Rect(info["bbox"])
        if centre(r) in region and not (info.get("xref") and info["xref"] in overlays):
            images.append((info.get("xref") or ("inline", n), r))  # inline images: each unique
    texts = [r for k, r in items if k == "text"]
    paths = [r for k, r in items if "path" in k]
    for frame in sorted(boxes, key=lambda r: -r.get_area()):
        loose = frame + (-4, -4, 4, 4)
        inside = {key for key, r in images if centre(r) in loose}
        if any(key not in inside for key, _ in images):
            continue
        if texts and sum(centre(r) in loose for r in texts) < 0.85 * len(texts):
            continue
        return frame + (-1.5, -1.5, 1.5, 1.5)  # keep the border line itself

    # Second chance, for labels printed beside/below their own instructions
    # (Yodel/InPost returns: label box top-left, help text right and below).
    # Text entirely outside the box is fine then, but only for a label-sized
    # box that holds every image and nearly all the vector art (the barcode),
    # with no text straddling its edge — so nothing belonging to it is cut.
    # (A decorative page-wide box on a document cover is not label-shaped.)
    for frame in sorted(boxes, key=lambda r: -r.get_area()):
        short, long = sorted((frame.width, frame.height))
        if not (60 * MM <= short <= 160 * MM and long <= 220 * MM):  # label-shaped: up to A5-ish
            continue
        loose = frame + (-4, -4, 4, 4)
        if any(r not in loose for _, r in images):  # every image wholly inside, not just its centre
            continue
        if not looks_like_label([(k, r) for k, r in items if r in loose]):  # holds a barcode-ish thing
            continue
        if not paths or sum(r in loose for r in paths) < 0.8 * len(paths):
            continue
        if any(r.intersects(loose) and not r in loose for r in texts):
            continue
        return frame + (-1.5, -1.5, 1.5, 1.5)
    return None


def decorative_images(page: pymupdf.Page):
    """(xref, rect) of near-empty pictures covering most of the page.

    Some labels (Yodel) draw their dashed cut guide and scissors as a
    transparent page-sized image. It isn't content: treating it as such makes
    the whole A4 page look like one label."""
    doc = page.parent
    cache = doc.__dict__.setdefault("_rm_overlays", {})
    if page.number in cache:
        return cache[page.number]
    found = []
    area = page.rect.get_area()
    for xref, smask, *_, filt, _ in page.get_images(full=True):
        if not smask:
            continue  # sparse opaque labels can also have very little ink
        if filt in ("DCTDecode", "JPXDecode"):
            continue  # a photo or scan, never a flat overlay (and slow to decode)
        for r in page.get_image_rects(xref):
            r &= page.rect
            if r.get_area() < 0.6 * area:
                continue
            try:
                if ink_fraction(doc, xref, smask) < 0.005:
                    found.append((xref, r))
            except Exception:
                pass  # unreadable image: leave it counted as content
    cache[page.number] = found
    return found


DARK = bytes(1 if v < 200 else 0 for v in range(256))  # grey level -> "would show as ink"
OPAQUE = bytes(1 if v > 20 else 0 for v in range(256))


def ink_fraction(doc: pymupdf.Document, xref: int, smask: int = 0) -> float:
    """Share of an image's pixels that would visibly print (darker than light grey)."""
    pix = pymupdf.Pixmap(doc, xref)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    gray = pymupdf.Pixmap(pymupdf.csGRAY, pix) if pix.n != 1 else pix
    dark = gray.samples.translate(DARK).count(1) / (gray.width * gray.height)
    if smask:  # a soft mask decides what shows: ink needs dark *and* opaque
        alpha = pymupdf.Pixmap(doc, smask)
        if alpha.n != 1 or alpha.width != gray.width or alpha.height != gray.height:
            raise ValueError("soft mask dimensions do not match image")
        dark_pixels = gray.samples.translate(DARK)
        opaque_pixels = alpha.samples.translate(OPAQUE)
        visible_ink = (
            int.from_bytes(dark_pixels, "little")
            & int.from_bytes(opaque_pixels, "little")
        ).bit_count()
        return visible_ink / (gray.width * gray.height)
    return dark


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


MIN_LABEL_SHORT, MIN_LABEL_LONG = 85 * MM, 120 * MM  # a cut that leaves cells smaller than this isn't splitting labels apart


def plausible_label_size(w: float, h: float) -> bool:
    return min(w, h) >= MIN_LABEL_SHORT and max(w, h) >= MIN_LABEL_LONG


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
    # A cut is only believable if it leaves label-sized cells. Without this, a single label with a
    # blank band across its middle (2D code above, barcode below) is cut into two "labels".
    for cut_xs, cut_ys in ((xs, ys), (xs, [0, H]), ([0, W], ys), ([0, W], [0, H])):
        if all(plausible_label_size(cut_xs[i + 1] - cut_xs[i], cut_ys[j + 1] - cut_ys[j])
               for i in range(len(cut_xs) - 1) for j in range(len(cut_ys) - 1)):
            xs, ys = cut_xs, cut_ys
            break
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


def is_own_sheet(doc: pymupdf.Document, name: str, page: pymupdf.Page) -> bool:
    """A sheet this tool already made (re-fed to reprint or move labels)."""
    a4 = abs(page.rect.width - A4.width) < 3 and abs(page.rect.height - A4.height) < 3
    marked = SHEET_MARK in (doc.metadata or {}).get("keywords", "")
    return a4 and (marked or name.startswith("rm-labels-"))


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


def extract_labels(sources):
    """Yield (doc, page_no, clip_rect, description).

    Each source is a PDF path, or a (name, document) pair for files that were
    converted in memory (images) or come from the web UI's store."""
    for src in sources:
        name, doc = (src.name, pymupdf.open(src)) if isinstance(src, Path) else src
        for page in doc:
            if page.rotation:
                page.remove_rotation()
            if not is_own_sheet(doc, name, page):
                strip_stray_glyphs(page)
            if is_own_sheet(doc, name, page):
                for pos, clip in own_sheet_cells(page):
                    yield doc, page.number, clip, f"{name} p{page.number + 1} label {pos + 1}"
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
                tag = f"{name} p{page.number + 1}" + (f" #{i + 1}" if len(regions) > 1 else "")
                yield doc, page.number, clip, tag


# ── Text-only labels ──────────────────────────────────────────────────────────
@dataclass
class TextLabel:
    text: str

    @property
    def tag(self) -> str:
        first = self.text.strip().splitlines()[0] if self.text.strip() else ""
        return f"text “{first[:30]}{'…' if len(first) > 30 else ''}”"


TEXT_SIZES = (72, 60, 48, 40, 34, 28, 24, 20, 17, 14, 12)


def text_layout(text: str) -> tuple[list[str], str, str]:
    """(lines, alignment, html body) for a text label."""
    lines = text.strip().splitlines() or [""]
    align = "center" if len(lines) <= 4 and max(map(len, lines)) <= 28 else "left"
    return lines, align, "<br>".join(html.escape(line) or "&nbsp;" for line in lines)


def text_css(size: int, align: str) -> str:
    return f"* {{font-family: sans-serif; font-size: {size}pt; line-height: 1.2; text-align: {align}}}"


@functools.lru_cache(maxsize=256)
def fit_text(text: str, cell: tuple[float, float, float, float]) -> tuple[int, float]:
    """(font size, spare height) for the largest size at which `text` fits the cell. It takes a dozen scratch
    renders to find, and the answer depends only on the text and the cell, so it is remembered."""
    lines, align, body = text_layout(text)
    rect = pymupdf.Rect(cell)

    def trial(size, scale_low=1):
        """(spare height, rendered line count) for this size on a scratch page."""
        scratch = pymupdf.open().new_page(width=A4.width, height=A4.height)
        spare, _ = scratch.insert_htmlbox(rect, body, css=text_css(size, align), scale_low=scale_low)
        rows = {round(l["bbox"][1]) for b in scratch.get_text("dict")["blocks"] for l in b.get("lines", [])}
        return spare, len(rows)

    fits = [(size, *trial(size)) for size in TEXT_SIZES]
    fits = [(size, spare, rows) for size, spare, rows in fits if spare >= 0]
    # keep the author's lines intact if that still leaves readable text;
    # otherwise (a paragraph) let it wrap at the biggest size that fits
    unwrapped = [f for f in fits if f[2] <= len(lines) and f[0] >= 17]
    if unwrapped or fits:
        size, spare, _ = (unwrapped or fits)[0]
    else:  # too long even at the smallest size: shrink to fit
        size = TEXT_SIZES[-1]
        spare, _ = trial(size, scale_low=0)
    return size, spare


def draw_text_label(page: pymupdf.Page, cell: pymupdf.Rect, text: str):
    """Write `text` into the cell at the largest font size that fits, centred
    vertically. Short notes are centred; longer messages are left-aligned."""
    _, align, body = text_layout(text)
    size, spare = fit_text(text, tuple(cell))
    box = pymupdf.Rect(cell.x0, cell.y0 + max(spare, 0) / 2, cell.x1, cell.y1)
    page.insert_htmlbox(box, body, css=text_css(size, align), scale_low=0)


# ── Imposition ────────────────────────────────────────────────────────────────
def place(out: pymupdf.Document, labels, start: int, fill: bool, log=print, skip=()) -> int:
    """Lay labels out from slot `start` of the first sheet, leaving the slots in `skip` (already peeled) empty
    on that sheet only. Returns the slot after the last label."""
    skipped, pos, page = set(skip), start, None
    for label in labels:
        while pos in skipped:
            pos += 1
        if pos >= 4:  # this sheet is full (or had no free slot): carry on with a fresh one
            page, pos, skipped = None, 0, set()
        if page is None:
            page = out.new_page(width=A4.width, height=A4.height)
        cell = cell_rect(pos)
        if isinstance(label, TextLabel):
            draw_text_label(page, cell, label.text)
            log(f"  label {pos + 1} of sheet {len(out)} ← {label.tag}")
            pos += 1
            continue
        doc, pno, clip, tag = label
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
        log(f"  label {pos + 1} of sheet {len(out)} ← {tag}  ({scale:.0%}{', rotated' if rotate else ''})")
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


# ── Output ────────────────────────────────────────────────────────────────────
def save_sheet(out: pymupdf.Document, dest: Path) -> Path:
    out.set_metadata({"keywords": SHEET_MARK, "creator": "rmlabels"})
    out.save(dest, garbage=3, deflate=True)
    return dest


PRINT_TIMEOUT = 20  # seconds for `lp` to hand the job to CUPS (not for the print itself)


def print_sheet(dest: Path, mono: bool = False, draft: bool = False, printer: str | None = None) -> int | None:
    """Hand the sheet to CUPS. Returns the CUPS job number (None if `lp` didn't say), or raises RuntimeError
    with the reason. Success only means CUPS accepted the job; job_status() says what happened next."""
    opts = ["PageSize=A4", "print-scaling=none", "EPIJ_Bdls=0", "EPIJ_Medi=0"]
    if not draft:
        # Plain paper + Fine: 720 dpi, one-way head passes -> crisp barcodes and solid text
        opts += ["EPIJ_Qual=304", "Resolution=720x720dpi", "EPIJ_Mode=3", "EPIJ_OPT_Bi_D=0"]
    if mono:
        opts += ["ColorModel=Mono", "EPIJ_Ink_=0"]
    cmd = ["lp", "-d", printer or CONFIG["printer"]]
    for o in opts:
        cmd += ["-o", o]
    try:
        r = subprocess.run(cmd + [str(dest)], capture_output=True, text=True, timeout=PRINT_TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError("`lp` wasn't found, so there is no way to print from here.") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"CUPS didn't answer within {PRINT_TIMEOUT} seconds. The job may still have been queued, "
                           "so check the printer queue before printing again.") from None
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"lp exited with status {r.returncode}.")
    m = re.search(r"request id is \S+-(\d+)", r.stdout)
    return int(m.group(1)) if m else None


# CUPS job-state (IPP) → what the UI shows. Only "completed" means the spooler is finished, which for
# a network printer is not quite the same as paper in the tray, so the UI words it carefully.
JOB_STATES = {"pending": "queued", "pending-held": "held", "processing": "printing", "processing-stopped": "stopped",
              "canceled": "cancelled", "aborted": "failed", "completed": "done"}
FINAL_STATES = {"done", "failed", "cancelled"}
PRINTER_PROBLEMS = {"media-empty": "Out of paper", "media-needed": "Out of paper", "media-jam": "Paper jam",
                    "marker-supply-empty": "A cartridge is empty", "marker-supply-low": "A cartridge is low",
                    "offline": "The printer is offline", "paused": "The printer is paused",
                    "door-open": "The printer cover is open", "cover-open": "The printer cover is open",
                    "input-tray-missing": "The paper tray is missing", "shutdown": "The printer is shutting down"}


def _problems(reasons: str) -> list[str]:
    """Plain-English problems from a comma-separated IPP *-state-reasons value."""
    found = []
    for reason in reasons.split(","):
        said = PRINTER_PROBLEMS.get(re.sub(r"-(error|warning|report)$", "", reason.strip()))
        if said and said not in found:
            found.append(said)
    return found


def parse_printer_status(text: str) -> tuple[str, str]:
    """(state, problem) from `ipptool -tv … get-printer-attributes.test` output. state: ready | busy | stopped | unknown."""
    m = re.search(r"^\s*printer-state \(enum\) = (\S+)", text, re.M)
    state = {"idle": "ready", "processing": "busy", "stopped": "stopped"}.get(m.group(1), "unknown") if m else "unknown"
    if state == "unknown":
        return state, ""
    m = re.search(r"^\s*printer-state-reasons \([^)]*\) = (.+)$", text, re.M)
    problems = _problems(m.group(1)) if m else []
    if re.search(r"^\s*printer-is-accepting-jobs \(boolean\) = false", text, re.M):
        problems.append("The printer isn't accepting jobs")
    if state == "stopped" and not problems:
        problems.append("The print queue is stopped")
    return state, "; ".join(problems)


def printer_status(printer: str | None = None) -> tuple[str, str]:
    """Ask CUPS about the printer itself, so problems show up before a print rather than after. Never raises."""
    out = _ipptool(f"ipp://localhost/printers/{printer or CONFIG['printer']}", "get-printer-attributes.test")
    return ("unknown", "") if out is None else parse_printer_status(out)


def parse_job_status(text: str) -> tuple[str, str]:
    """(state, problem) from `ipptool -tv … get-job-attributes.test` output; state is "unknown" if unreadable."""
    m = re.search(r"^\s*job-state \(enum\) = (\S+)", text, re.M)
    state = JOB_STATES.get(m.group(1), "unknown") if m else "unknown"
    m = re.search(r"^\s*job-printer-state-reasons \([^)]*\) = (.+)$", text, re.M)
    return state, "; ".join(_problems(m.group(1)) if m else [])


def _ipptool(uri: str, test: str) -> str | None:
    """Output of an ipptool query, or None if CUPS couldn't be asked. macOS starts cupsd on demand and it can
    refuse the very first connection while it wakes up ("Operation now in progress"), so connecting gets one retry."""
    for attempt in range(2):
        try:
            r = subprocess.run(["ipptool", "-tv", uri, test], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if "Unable to connect" not in r.stderr:
            return r.stdout
        if attempt == 0:
            time.sleep(0.7)
    return None


def job_status(job_id: int) -> tuple[str, str]:
    """Ask CUPS how a job is getting on. Never raises: if CUPS or ipptool can't be reached the answer is
    "unknown", which callers must not treat as a failure (the connection can hiccup while the job is fine)."""
    out = _ipptool(f"ipp://localhost/jobs/{job_id}", "get-job-attributes.test")
    return _lpstat_status(job_id) if out is None else parse_job_status(out)


def _lpstat_status(job_id: int) -> tuple[str, str]:
    """Fallback when ipptool is missing: only tells waiting/printing apart from finished."""
    try:
        for flag, state in (("not-completed", "printing"), ("completed", "done")):
            out = subprocess.run(["lpstat", "-W", flag, "-o"], capture_output=True, text=True, timeout=5).stdout
            if re.search(rf"-{job_id}\s", out):
                return state, ""
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown", ""


FULL = 0b1111  # all four slots used


def mask_from_start(start: int) -> int:
    """Slots before `start` are used: starting at label 3 means labels 1 and 2 are gone."""
    return (1 << max(0, min(4, start))) - 1


def free_slots(mask: int) -> list[int]:
    return [i for i in range(4) if not mask >> i & 1]


def used_after(mask: int, n: int) -> int:
    """The sheet left in the printer after n labels go into its free slots (and onto fresh sheets after that).
    A sheet with nothing left on it counts as a fresh one, so the answer is 0 rather than FULL."""
    free = free_slots(mask)
    if n <= 0:
        return mask
    if n <= len(free):
        mask |= sum(1 << i for i in free[:n])
    else:
        mask = mask_from_start((n - len(free)) % 4)
    return 0 if mask == FULL else mask


def _write_atomic(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)  # a crash can't leave a half-written file that reads as "fresh sheet"


def read_used_mask() -> int:
    """Which labels on the sheet in the printer are used (bit n = label n+1). Falls back to the older
    single 'next free label' number if there is no mask yet."""
    try:
        return int(USED.read_text()) & FULL
    except (OSError, ValueError):
        pass
    try:
        return mask_from_start(int(STATE.read_text()) % 4)
    except (OSError, ValueError):
        return 0


def write_used_mask(mask: int):
    mask = 0 if mask & FULL == FULL else mask & FULL
    _write_atomic(USED, str(mask))
    _write_atomic(STATE, str((free_slots(mask) or [0])[0]))  # the older single-number form, for anything still reading it


def read_next_position() -> int:
    """0-based first free label on the sheet in the printer (0 = fresh sheet)."""
    return (free_slots(read_used_mask()) or [0])[0]


def write_next_position(end: int):
    write_used_mask(mask_from_start(end % 4))
