# Images and hostile files: findings

Scope: `07-images`, `08-hostile`, `09-unicode`, plus my own inputs in `tests/fixtures/synthetic/agent-images/`
(generator: `make_agent_images.py`, ~40 MB of output). Everything was run through `tests/synthetic/dryrun.py`
(CLI) or `TestClient(web.app)` with `RMLABELS_DATA/CONFIG` in a temp dir. Nothing under `rmlabels/` was edited.
Tested against the working tree (which includes the uncommitted `decorative_images` change in `core.py`).

## Ranked real issues

### 1. (HIGH, web) A non-PDF saved with a `.pdf` extension bricks the whole web UI
`open_source` opens `*.pdf` with `pymupdf.open(path)`, which sniffs the content, so a PNG/JPEG/TIFF named `.pdf` opens
as an *image document* instead of raising. `item_labels` therefore returns a label, `build()` makes a thumbnail fine,
and then `core.place()` -> `page.show_pdf_page()` raises `ValueError: is no PDF`. That is outside the `try` in
`item_labels`, so every request that calls `build()` returns HTTP 500: `GET /`, `GET /panel`, `/sheet.pdf`, `/print`,
and the upload that added it. The item stays queued, so the UI stays dead; nothing in the UI can remove it. The only way
out is `curl -X POST :8765/clear` or editing the sqlite db. `/api/upload` (iOS Shortcut) happily replies
`Queued x.pdf` and bricks the UI from the phone.

Repro (web):
```
files: tests/fixtures/synthetic/08-hostile/actually-a-png.pdf  (use a PNG whose bytes differ from any other queued file)
agent-images/actually-a-jpeg.pdf, actually-a-png-distinct.pdf, actually-a-tiff.pdf  -> POST /upload -> 500; GET / -> 500
```
Recovery check: `POST /clear` -> `GET /` is 200 again.
CLI: same root cause, `ValueError: is no PDF` traceback AFTER the "N label(s) from M file(s)" line is printed (so the
header is misleading).
Fix sketch: sniff/validate in `open_source` (e.g. `doc.is_pdf` or check `%PDF`, otherwise treat as image), and/or wrap
the whole per-item pipeline including the first `show_pdf_page` in the same guard.

### 2. (HIGH, CLI) One unreadable file aborts the whole run; no sheet at all
Every one of these dies with a raw Python traceback and exit 1, wherever it sits in the list (first, middle, last, two
bad ones; verified for each of first/middle/last): `zero-bytes.pdf`, `truncated.pdf`, `garbage-after-header.pdf`,
`garbage.png`, `actually-a-pdf.png` (PDF named .png), `actually-a-png.pdf`, `encrypted-needs-password.pdf`
(`ValueError: document closed or encrypted`), and new ones: `truncated-60pct.jpg/png` (partial download,
`OSError: image file is truncated`), `png-bad-idat-crc.png`, `jpeg-with-junk-prefix.jpg`, `icc-huge-4mb.png`
(Pillow `MAX_TEXT_CHUNK`), `big-12000x18000.png` / `bomb-20000x20000.png` (`DecompressionBombError`, > 179 MP),
`actually-a-pdf.jpg`, a directory or symlink-to-directory named `x.pdf`.
Good labels in the same batch are lost (no partial sheet). The good news: it always fails before any side effect: no
sheet written, no `lp` call, `next_position` and history untouched (checked with `--print --mono`). Skipped-by-extension
files (`notes.txt`, `valid-no-extension`, `trailing-dot.pdf.`) and not-found files are handled properly (message on
stderr, the rest of the batch runs).
Fix sketch: wrap the per-file `open_source`/`extract_labels` in try/except in `cli.py` (like `web.item_labels` does),
print `Skipping X: couldn't read it (reason)`, continue.

### 3. (HIGH, correctness) Hidden/print-state PDF layers (OCGs) are not honoured on the sheet
Source rendered by pymupdf (and by macOS QuickLook) vs the output sheet, using `ocg-hidden-red-block-over-label.pdf`:
the viewer hides the layer; the sheet PRINTS the red block and "HIDDEN LAYER TEXT" over the barcode. Likely cause:
`show_pdf_page` grafts the `/OC` marked content but not `/OCProperties /D /OFF`, so the hidden OCG is visible in the
output. Detection is fine (`ocg-whole-label-in-off-layer.pdf` correctly gives "No label found"), only the placement
ignores it. Same for `08-hostile/hidden-optional-content-layer.pdf`: the viewer shows the address, the sheet shows an
extra white box that wipes out part of it. `ocg-print-state-off.pdf` (a layer with `/Print /PrintState /OFF`, visible on
screen): a printed sheet should omit it; it is printed. (Note the 08 fixture is itself broken: only the white rectangle
is inside the OCG; the "HIDDEN LAYER" text is outside it and is therefore visible in every viewer.)
Fix sketch: copy the OCProperties /D config into the output doc, or strip OFF-layer content from the source page before
placing, or render-and-compare.

### 4. (MEDIUM) Pages silently dropped from multi-page PDFs, no warning
`page3-content-stream-garbage.pdf` and `page3-contents-missing-object.pdf` (5 pages, page 3 unreadable): 4 labels,
no message anywhere (CLI says `4 label(s) from 1 file(s)`; the web queue says "4 labels", no badge). Likewise
`3pages-label-textonly-label.pdf` (label, terms page, label) gives 2 labels and no warning, although the README says
text-only pages are "skipped with a warning" (the warning only fires when the *whole file* yields nothing). Someone with a
5-parcel PDF would find a label missing at the post office.
Also: `dense-micro-text-and-barcode.pdf` (400 tiny text runs, one barcode) is trimmed to the lower half of the page: the
vertical midline is not crossed, so `split_regions` halves the page and drops the barcode-less top half. Silent content loss,
and the output is rotated 90 deg although the source page is upright portrait. Baseline counted it "ok" because the count is 1.

### 5. (MEDIUM, web) Bad uploads look fine until you read the badge
Upload of a corrupt file replies "Added 3 file(s)." (success text), `/api/upload` replies `Queued zero-bytes.pdf`, and
the queue row shows a red "no label found" badge with a `?` thumbnail: the same text as for a text-only PDF, so you can't
tell corrupt/encrypted/too-large from "not a label". The reason is only in the server log, and it is logged once per
panel refresh (6x per interaction). Previews of the other items are NOT affected (exception-safe), `/print` prints the
good ones and leaves the bad one queued. Fine behaviour; messaging could be better. `encrypted-needs-password.pdf` is
reported only as "no label found" (CLI: `ValueError: document closed or encrypted`).

### 6. (MEDIUM, semantics) Images that silently print badly
(all as seen on the PNG sheets)
- Dark-mode / inverted screenshot (`label-dark-mode-inverted.png`): printed as a big black block with white text and the
  blue header turned orange. Nothing detects/inverts it. Also `1x1-black.png`, `2x3-black.png`, `solid-black.png` print a
  full black cell (ink dump; `solid-black.png` and 1x1 black count as a label).
- `label-photo-on-desk.jpg`: whole brown desk printed, label skewed and ~60% size. `screenshot-with-app-chrome.png`: status
  bar, blue bottom bar and grey margin printed, label shrunk. (README documents the chrome case.)
- `tiny-60x90.png`: upscaled ~7x into a blurry unreadable block with no warning.
- `label-offwhite-scan-margins.png`: margin pixels are L~236 so trimming works, but the 1% `pad` is the paper colour, so a
  tinted ~1.3 mm border prints (light dither on a mono print). Minor.
- `label-top-third-only.png` (cropped, landscape): rotated 90 deg and enlarged to fill the cell, text runs sideways.
  `qr-code-cropped-screenshot.png` (landscape): also rotated sideways. `screenshot-two-labels-side-by-side.png`: ONE label
  cell, rotated, both labels at ~50% size.
- `two-page.tiff`, `animated.gif`: only frame 1, silently.
- `photo-12mp-noisy.jpg`: whole photo re-encoded as PNG at full resolution: ~5 s per photo and ~6 MB of sheet each
  (2 photos 10 s / 11.7 MB). Low.
- Faint ink: `faint-ink-15pct.png` (darkest ink grey 217) -> "No label found". New `decorative_images` rule
  (`ink_fraction < 0.5%` of pixels darker than 200) also drops it. Threshold edge, unlikely in practice.
- `white-ink-on-transparent.png` -> "No label found" (message wording is about text-only PDFs).
Baseline discrepancy: `blank-white.png` is now correctly "No label found" (was a label in the baseline), thanks to the
uncommitted `decorative_images` change.

### 7. (LOW) Names, output files
- Output file name has second resolution: two runs in the same second overwrite each other (`rm-labels-<stamp>.pdf`).
  Seen when two dryrun calls landed in the same second.
- Terminal control characters and newlines in filenames are echoed raw in the log lines (`ansi-\x1b[31mred...pdf`,
  `new\nline.pdf` splits the "label N <-" line). Cosmetic. 190-char name prints in full. HTML in a file name is escaped in the
  web UI (checked `<img src=x onerror=...>.pdf`: no raw HTML in the response).
- `valid-no-extension` (a real PDF) is skipped as "not a PDF or image": only magic-byte sniffing would rescue it
  (e.g. iOS share-sheet files); `.hidden`/`.LABEL.PDF`/`label.PdF`/`label.pdf.pdf`/leading-space/emoji/NFD names, symlinks
  to labels all work. Dangling symlink: "file not found" (clean).
- 500-page PDF: CLI 4.4 s, 125 sheets, 1.4 MB; web 7 s and a 4.7 MB panel response per refresh. OK.
- 117 MP PNG (below Pillow's limit): 0.3 s, 820 MB peak RSS. Above 179 MP: traceback (see 2).

## Handled well
- EXIF orientation 1-8 (my `exif-orientation-N.jpg`, built so a correct decoder shows it upright): all identical output.
  The 07 `label-exif-rotated.jpg` comes out upside down but QuickLook and PIL also show it upside down: **fixture bug**
  (wrong rotation direction in the generator), not a tool bug.
- Transparent PNG (incl. RGB under alpha 0 = black), palette+transparency, LA, 1-bit, grey, 16-bit grey, CMYK JPEG,
  CMYK TIFF, progressive/optimised JPEG, ICC small, JPEG with trailing junk, HEIC/WebP/GIF/BMP/TIFF, upper-case ext,
  3600x5400 (0.3 s), huge white margins trimmed.
- Owner-password-only PDF works; chopped-xref and leading-junk PDFs are repaired and print; page 3 with zero/huge
  MediaBox still gives a label; nested form XObject OK.
- Web: a bad item never breaks previewing or printing the others except issue 1; duplicate sha detection; unsupported ext
  skipped with a message; XSS-safe names.
- Unicode 09 labels: output matches the source. Fixture artefacts, not tool bugs: Polish "Lukasz Wisniewski" shows `.`
  for L-stroke/s-acute and Hebrew/"ISRAEL" order is garbled *in the source PDFs* (Helvetica base font).
- Failing runs have no side effects (no sheet, print, history or position change).

## Annotations / form fields vs the sheet
Output is built with `show_pdf_page`, which does not carry widgets or annotations. Viewer vs sheet:
- `annotations-stamp-and-freetext.pdf`: viewer shows a red "APPROVED" stamp and red "FRAGILE - handle with care"; sheet shows neither.
- `form-field-note-on-label.pdf`: viewer shows the big "Leave in the shed" over the barcode; sheet drops it.
Probably the right call for stamps (and the field overlaps the barcode), but it is a silent viewer/print mismatch
(FreeText notes someone typed on a label are lost). Decide deliberately; maybe warn.

## Doubts
- Landscape image -> forced 90 deg rotation is by design for landscape labels but is odd for QR/partial screenshots.
- Hidden-layer fix direction (print vs view state) is a product decision for `/Print /PrintState`.
- I did not test the actual print path (lp stubbed).
