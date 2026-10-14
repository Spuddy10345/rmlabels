# False positives / false negatives on non-label documents

Scope: `tests/fixtures/synthetic/{06-nonlabels,02-platforms,04-multipage,05-pagegeom}` plus my own realistic
variants in `tests/fixtures/synthetic/agent-fp/` (generators and probe scripts live there; nothing under
`rmlabels/` or other fixtures was touched). All runs through `tests/synthetic/dryrun.py`; sheet PNGs were
viewed. Core read: `page_items`, `find_frame`, `decorative_images`, `looks_like_label`, `split_regions`,
`extract_labels`.

Cost model worth keeping in mind: a **false positive burns a real sticky label** (there is no page/label
picker, and a skipped page is silent unless the whole file yields nothing). A **false negative** costs nothing
but a reprint. So FPs matter more than FNs here.

## 0. The sweep verdict hides the key failure

`sweep-results.json` marks `02-platforms/clickdrop-label-plus-despatch-note-a4.pdf` and
`clickdrop-label-plus-cn22-a4.pdf` as `ok` because the *count* is 1. The crop is the **whole A4 content at
53% / 47%** (`uv run python tests/fixtures/synthetic/agent-fp/probe.py --src <file>` and view the PNG): label,
despatch note and CN22 all on one label slot, label shrunk, text tiny. Count-only verdicts need a crop-size check
(e.g. expect clip near 100x150 mm).

## 1. Mechanisms and thresholds

### 1a. `looks_like_label()` (core.py ~181): `images > 0 or paths >= 15`
- **Image: no size floor at all.** A 1-pixel image shown at 0.3 mm counts (`thresholds.py` D). So do a 6 mm
  "heart" icon, a white 20 mm image, and a logo of any size. The only exemptions are `decorative_images()`:
  >= 60% of page area AND < 0.5% dark pixels, non-JPEG. A full-page mid-grey PNG or any full-page JPEG counts.
- **Paths: 15 separate vector ops** (`get_bboxlog` entries with "path": strokes and fills each count once;
  clip-only ops did not appear in the log). Measured: 14 hairline rules -> skipped, 15 -> label; 3x4 = 12 cell
  rects -> skipped, 4x4 = 16 -> label; 8 zebra fills alone -> skipped, 8 fills + 8 rules -> label. A whole table
  stroked as one op counts as 1 path (skipped). Chrome/Skia emits every border segment separately, so almost any
  bordered HTML table (header + 2 rows x 4 cols) passes 15; so does a receipt with 16 hairlines.
- Consequence by layout (this is what decides what gets printed):
  - Logo top-right on an otherwise text invoice: `split_regions` cuts at the vertical centre (nothing crosses
    x = W/2), so the "label" is *just the logo box* (2x2 mm for the 1 px case).
  - Text crossing the centre: the whole page (or its top half if nothing crosses y = H/2; the invoice-with-logo
    fixture comes out as the top 103 mm of the page, footer silently dropped).
  - Everything is then scaled to fit one label slot and rotated if wider than tall (invoices print sideways at ~70%).

### 1b. `split_regions()` (core.py ~195): splits at x = W/2 and/or y = H/2 whenever no item straddles the line +-2pt
This is the biggest systematic contributor to wrong counts in my slice, and it is **not about invoices**:
an *unframed* label with a >= 4 pt blank band exactly on the page centre line, where both halves have >= 15
paths or an image, is cut in two. Examples: `01-single/dpd-nextday-4x6.pdf` (also `variant-dpd-*-noframe`,
hermes-de-a6, ... 16 files in the baseline). Because multipage fixtures reuse those labels, the baseline
"EXTRA" in `label-textslip-label`, `label-terms-label`, `blank-pages-between-labels`, `mixed-page-sizes`,
`label-invoice-cn22-label` and `05-pagegeom/cropbox-smaller-than-mediabox` is mostly this (the text-only slip and
T&Cs pages ARE correctly skipped; the extra is the DPD/Royal Mail page being halved: barcode alone on a second slot).
Reported for the other agent's area; it matters here because it makes mixed-document counts untrustworthy.

### 1c. Why "despatch note below a label" works or fails (`find_frame`)
Frame (a stroked/filled box >= 50 mm a side) is the only thing that separates a label from a note below it.
- Framed label + note with text/ruled table, **label has lots of vector paths (QR/bars as individual rects)**:
  frame found, crop exactly 100x150 (e.g. `agent-fp/cd-framed-note-ruled.pdf`, `cd-label-plus-realistic-cn22.pdf`).
  Works through the 2nd-chance rule.
- 2nd-chance rule needs >= 80% of ALL vector paths inside the box, so the paths outside may be at most 25% of
  those inside. Sweep (`frame_ratio_sweep.py`): label = 60 bar rects and no QR (62 paths): **one** ruled table
  row below it already breaks the frame -> whole page (201x195 mm, ~47%). With a QR drawn as ~240 rects the
  frame survives up to a 5-row table and fails at 8 rows.
- **Raster barcodes make it worse** (`raster_label_sweep.py`; images inside the box are fine but the label has
  almost no paths): framed label + despatch note whose header cell has a grey fill, or any ruled table -> whole
  page. Pure-text note (no fills at all) -> OK. So it hinges on whether the note contains even one vector fill.
- **Any image outside the frame kills it** (1st pass: every image must be inside; 2nd pass: `any(r not in loose
  for images)`): `cd-framed-note-ruled-logo.pdf` (logo in the despatch note) -> 201x258 mm, 46%;
  `ret-rm-framed-label-instructions-with-icons.pdf` (four 9 mm step icons) -> 101x202 mm, 66%;
  `p2g-label-instructions-cert-ruled.pdf` (certificate of posting with logo + 2-row table) -> 192x211 mm, 49%.
  The code comment says a repeated logo outside is fine, but only if a copy is *inside* the frame, which a
  marketplace/Parcel2Go logo in the note is not.
- **No frame at all** (the repo's click&drop fixtures, and `agent-fp/cd-unframed-*`): the outcome is decided by
  whether any single item straddles y = 148.5 mm. In the repo fixtures the barcode bars (127-152 mm) straddle it,
  so no split, the note merges in (53%). When nothing straddles it, the page splits and a *ruled* note becomes a
  **second label** (`cd-unframed-note-ruled.pdf` -> 2 found; note emitted at 69%), a plain-text note is dropped.
  `centre_line_sweep.py` shows all three outcomes depending on label height (100-165 mm), i.e. luck.
- Note beside the label (`cd-framed-note-right.pdf`): the label is found, but the note (logo + table) becomes a
  second "label". No frame/page-region logic helps because the split happens before `find_frame`.

### 1d. CN22 / CN23
- `06-nonlabels/cn22-form-alone.pdf` and `cn23-form-a4.pdf` are skipped only because they have **10 vector
  paths**. A more realistic CN22 (checkbox row, 2-row ruled table, signature box: 43 paths,
  `agent-fp/cn22-realistic-alone-a6.pdf`) is **found as a label** and printed (rotated, 100%). So skip/print
  is an accident of how many boxes the form has, not a decision.
- Same page as a label: with a frame, the CN22 is silently dropped (`cd-label-plus-realistic-cn22.pdf`);
  unframed, it becomes a second label (`cd-unframed-label-plus-realistic-cn22.pdf`).
- A skipped page inside a multi-page PDF produces **no message** (cli.py only warns when the whole file gives
  zero labels), and the sheet log doesn't list skipped pages.

### 1e. False negatives
- `06-nonlabels/barcode-font-text-only-label.pdf`: text only (2 spans), nothing "barcode-ish", so skipped with the
  generic "text-only pages are skipped" message. Real but rare (barcode fonts such as Code 39 appear in some
  in-house/WMS labels). A text-only label is indistinguishable from a text invoice without font knowledge.
- Compound-path barcode variants (3 files in 01-single) are other agents' area.
- `05-pagegeom/mediabox-negative-origin.pdf` -> 0 found is **a fixture artefact**. See 3 below.

## 2. Realistic variants (agent-fp/, regenerate with `make_agent_fp.py`, run with `sweep_fp.py`)

Scale column is what the tool printed; a correct 100x150 label is ~88-89%.

| File | Wanted | Got | Verdict |
|---|---|---|---|
| cd-framed-note-plain / -ruled | 1 | 1 @ 88%, exact crop | good |
| cd-framed-note-ruled-logo | 1 | 1 @ 46% whole page | FAIL (image outside frame) |
| cd-framed-note-right (logo+table beside) | 1 | 2 (note as 2nd label) | FAIL |
| cd-unframed-note-plain | 1 | 1 (note dropped), rotated 100% because ink crop is near-square 94x89 | ok, odd rotation |
| cd-unframed-note-ruled | 1 | 2 (note printed) | FAIL |
| ps-amazon-logo-ruled-orderbarcode | 0 | 1 @ 71% sideways | FAIL, but contains a genuine vector barcode: inherently ambiguous |
| ps-ebay-logo-plain | 0 | 1 @ 71% | FAIL (logo only) |
| ps-etsy-plain-icon (6 mm icon) | 0 | 1 @ 71% | FAIL |
| ps-spaced-rows-rules-only (16 hairlines) | 0 | 1 @ 65% | FAIL |
| ret-evri-framed-label-text-instructions | 1 | 1 @ 88% | good |
| ret-rm-framed-label-instructions-text-only | 1 | 1 @ 88% | good |
| ret-rm-framed-label-instructions-with-icons | 1 | 1 @ 66%, label + instructions | FAIL |
| p2g-label-instructions-cert-plain | 1 | 1 @ 88% | good |
| p2g-label-instructions-cert-ruled (logo+table) | 1 | 1 @ 49% whole page | FAIL |
| cd-label-plus-realistic-cn22 (framed) | 1 | 1 @ 88%, CN22 dropped silently | good for the label, see CN22 note |
| cd-unframed-label-plus-realistic-cn22 | 1 | 2 (CN22 printed) | FAIL |
| mp-label-plain-invoice-label-a4 (text-only invoice) | 2 | 2, in page order | good |
| mp-label-invoice-cn22-label-a4 (ruled invoice+logo, CN22) | 2 | 4: label, invoice (rotated, 78%), CN22 (rotated, 85%), label | FAIL |

Realistic-exposure judgement (my fixtures are Chrome-printed, invented layouts; I could not check real Click & Drop
or Parcel2Go PDFs, so treat the "unframed" cases as a worst case):
- A user feeding **only the carrier's label PDF** is fine (frames or tight pages). The risk is the **combined A4
  "label + paperwork" PDFs** and **marketplace packing slips**.
- Most likely hits: (1) packing slips with a logo or a ruled table printed as labels (burns a sticker);
  (2) integrated label + despatch note shrunk to ~50% when the note has any image/ruled table and the label
  has few vector paths or no frame; (3) Parcel2Go certificate with a logo.
- Plain-text despatch notes under a framed label are handled well.

## 3. Page geometry (viewed)

| File | Result |
|---|---|
| page-rotate-90 | upright, 89% (rotated into the portrait cell) |
| page-rotate-180 | **upside down** (faithful to what a viewer shows; poppler render of the source is also upside down, so not the tool's fault) |
| page-rotate-270 | **upside down**; a viewer shows it turned sideways. Cause: `place()` always rotates landscape labels one way (rotate=90). |
| landscape-a4-one-rotated-label | upside down, same cause |
| userunit-2 | correct: pymupdf applies UserUnit, page is 200x300 mm, label fills the cell at 44% of that (visually identical to a normal 4x6) |
| a0-page-small-label | correct 88%, label found in the middle |
| cropbox-smaller-than-mediabox | CropBox is respected (page.rect = CropBox, items outside are ignored) but the label is **split in two** by the 1b centre-line bug, each half rotated |
| content-bleeds-off-page | clipped to the page (left edge of the text cut, bar cut) exactly like a viewer; no warning |
| mediabox-negative-origin | 0 found, **fixture artefact**: the generator calls `set_mediabox(-200,-300,...)` without translating the content (still drawn at 0..283), so only the bottom-right corner (13 paths, 29x44 mm) of the label is on the page. pdftoppm renders the same fragment. Not a tool bug. |

My own correct geometry fixtures (`make_geom_fp.py`): MediaBox [-200 -300 83 125] with translated content,
[1000 2000 ...] offset origin, negative origin + /Rotate 90, CropBox inset, A4 with CropBox = label area: all
found and placed correctly (`probe.py agent-fp/geom-*.pdf`).

**Rotation direction** (`rotation_check.py`): landscape source whose text reads *downward* -> output upright;
text reading *upward* -> output **upside down**. The tool picks one rotation direction regardless of text
direction, so /Rotate 270 (or labels turned CCW) come out upside down. Any 180-degree-wrong label is still
scannable, but it is something a user would notice on the peeled sticker. The text line `dir` from
`page.get_text("dict")` could choose the direction. (Also affects the multiup area.)

## 4. Mixed multi-page docs
- Order: always page order, then region order (row-major) within a page. Good.
- Scale is per label, not uniform (88-100% in `04-multipage/mixed-page-sizes`; two landscape-half labels at
  66-67%). 4x6 labels and A6 labels end up side by side at different scales; cosmetic.
- The invoice page and a realistic CN22 are inserted as labels (see table); the skipped pages (text-only slip,
  T&Cs, blank, 10-path CN22) are skipped *silently* in a multi-page PDF.
- A small pre-print safeguard would be the cheapest remedy for the FP cost: the sheet log already says which
  page became which label, so the web UI could show a per-label thumbnail/untick, and the CLI could print
  "skipped p2 (no barcode)". I did not implement anything (read-only task).

## 5. CN22 / CN23: risk for someone posting abroad (your call)
Currently a user posting outside the UK who feeds a combined label + customs PDF may get: label only (CN22
dropped silently when framed), CN22 printed as a "label" in the 4-up grid (realistic multi-box forms; 100% scale,
possibly cut because forms are larger than a label cell and get shrunk), or CN22 dropped with no warning (simple
forms). In all cases there is no consistent behaviour or warning. Whether the customs declaration should go on
the A4 sticker sheet at all is a decision for you (CN22/CN23 are often supposed to be attached/visible on the
parcel, sometimes on the carrier's own pouch or sheet). At minimum a "skipped page N" message would make it
visible, so the user isn't surprised at the post office.

## Honest doubts
- All "realistic" variants are my own Chrome-printed approximations; real Click & Drop / Parcel2Go / Amazon PDFs
  may have frames, different path counts (barcodes as a single path or an image), or different structure, which
  changes outcomes in either direction (frame rule is sensitive to path counts).
- The 1b split bug dominates the baseline count noise; I did not fix or fully characterise it (other agent's area).
- The near-square rotation (94x89 mm ink crop rotated sideways at no scale gain) is cosmetic, from my fixture's
  half-empty label; real labels rarely crop that square.
