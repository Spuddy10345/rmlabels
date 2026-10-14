# Detection and splitting on PDFs (01-single, 03-multiup)

Scope: `page_items`, `find_frame`, `split_regions`, `extract_labels`, `looks_like_label` in `rmlabels/core.py`.
Nothing under `rmlabels/` was edited. Own fixtures, generator and scripts are in
`tests/fixtures/synthetic/agent-detection/` (423 PDFs, `manifest.json`, `results.json`).
Key scripts: `diag.py`, `gap.py` (inspect one file), `analyze.py` (score every fixture), `crop_sensitivity.py`,
`proto_minsize.py` and `proto_fix.py` (monkeypatch prototypes of possible fixes, they do not touch core.py).
Regenerate with `gen_agent_fixtures.py [minimal|phase|gap|barcount|repr|multiup|extra]`.

## How the splitter really behaves

```python
crosses_y(y): any item with b.y0 < y-2 and b.y1 > y+2      # item must be taller than 4 pt and span the line
ys = [0,H] if crosses_y(H/2) else [0,H/2,H]                # same for x
cell kept if  items whose majority area is in the cell  pass looks_like_label   # image, or >= 15 path items
regions = kept cells    # cells that do NOT pass are silently discarded
```

Four consequences, each confirmed by experiment:

1. **A "gap" is not needed.** Splitting needs only that no single item spans a 4 pt dead zone around the page centre.
   Abutting text lines, or a barcode that ends just above its human-readable digits, are enough.
2. **Composite codes are invisible to the crossing test.** A 2D code made of 200 tiny rects, or a barcode made of thin
   bars, never "crosses" anything, even when it sits across the centre line. So the centre line can cut straight through
   a barcode.
3. **Any non-code half is dropped without a warning.** If exactly one cell passes `looks_like_label`, the output is that
   cell only (address text lost). `extract_labels` never checks that the regions cover the page content.
4. **One cut per axis.** At most a 2x2 result. 3 rows/columns, 4 across, 6-up and 8-up cannot be produced.

## Task 1: single label found as 2

Root cause: `split_regions` line 203/207, `crosses_y(H/2)` is False so `ys=[0,H/2,H]`. Both halves then pass
`looks_like_label` (barcode-ish thing in each: 109 paths + 31 paths in `01-single/dpd-nextday-4x6.pdf`).
`regions` has 2 entries, so the `if not regions` fallback is never used.

Measured on the 17 baseline cases (free band at mid-height between the last item above and first item below):

| class | count | band | files |
|---|---|---|---|
| knife-edge | 10 | -0.5 to +0.5 mm | dpd-nextday, hermes-de-a6, anpost, chronopost, bpost, brt, seur, vinted-go, 2 variant-dpd noframe |
| real blank band | 7 | 39 to 62 mm | inpost-locker, amazon-shipping, sendle, israelpost, royalmail-intl/specialdelivery, dpd-local-pickup |

Knife-edge example: DPD route barcode bottom at y=211.8, its digits start at y=213.2, page centre 212.6, window
[210.6, 214.6]. Hermes: two text lines whose boxes overlap by 0.6 pt. Smallest change that flips the result:
`crop_sensitivity.py` changes only the page height. `dpd-nextday-4x6.pdf` goes 1 -> 2 -> 1 as the page height moves by
about 10 pt (3.5 mm); `hermes-de-a6.pdf` has a ~6 pt window. `inpost-locker-4x6.pdf` never flips (57 mm blank band).

Minimal layout (`minimal/`): 2D code on top, 30-bar barcode below, nothing between.
- Barcode top edge at mid-2 pt or lower (e.g. +0): split. At mid-3 pt: no split (the 40 pt tall bars now cross).
  Sharp threshold exactly at the tolerance `tol=2`.
- The 2D code's lower edge can be anywhere (even 10 pt past the centre): it never blocks, because its modules are tiny.

**Realistic odds.** Frameless label with dense text, 2D code up and linear code down, text lines at pitch p:
`phase/` (50 fixtures, pitch 11-14 pt, phase swept) splits **27-43 %** of the time (a dead zone of 4 pt plus the line
slack, per line pitch). Mid-height splits happen whenever the centre falls between two text lines. With a drawn frame
(any stroked rect around the label): **0 of 85** frame sweeps split (the frame crosses both centre lines).
Hence the risk is concentrated in frameless labels where both halves contain a code: logo image on top, barcode image
below counts too (`variant-dpd-barcodes-png-noframe.pdf`). I would call it **likely** for real frameless vector labels
with a code in each half (maybe a quarter to a third), and near-zero for framed ones. Caveat: the parent fixtures are invented layouts (make_synthetic_labels.py), not real carrier files. Whether real carriers draw 2D codes as 100+ separate rects (yes for many reportlab/FPDF
outputs, no for images or compound paths) decides how often the "code in both halves" condition holds.

Worse variant (3 above): if only one half has a code the other half is **dropped silently**.
- `minimal/y_address_top_barcode_bottom_noframe.pdf`: address in the top 40 %, barcode in the bottom. The barcode is cut
  into a left and a right half, each rotated into its own label; the address is gone (PNG checked).
- `minimal/x_2d_only_address_left.pdf` and `x_2d_image_address_left.pdf`: address left, one 2D code right, short
  lines. x-split: output is the 2D code alone (PNG checked). `gap/noframe_2d+none_*` loses text at all 17 positions.
- `gap/noframe_none+lin_g50.pdf`: gap at mid-height, text-only top half dropped.
These all need no item crossing the vertical centre either, which is easy for left-aligned short text plus thin bars.

## Task 2: compound-path barcodes

`page_items` takes `get_bboxlog()`: a barcode drawn as ONE path (what zxing SVG, bwip-js and Skia produce) is one
`fill-path`. `looks_like_label` needs 1 image or >= 15 paths; the three compound variants have 6-7 paths in total, so
`looks_like_label(items)` is False, `split_regions` and the fallback both return nothing, 0 labels
("No label found in ...", exit 0, so it is at least not silent). Sweeps (`barcount/`, `repr/`):
- Separate rect bars: 14 bars fail (0 labels), 15 pass. A frame stroke counts as one path, so "14 bars + frame" passes.
- Compound: fails with 0, 5, 10 extra incidental paths, passes at 14 (2 compound + 14 = 16, threshold effectively 15).
  Compound + frame still fails. Compound 2D-only, linear-only, 2D+linear all 0.
- Images (PNG) and stroked-line bars are fine. A label of 1 image + text works.
- `proto_fix.py`: `get_drawings()` shows the compound paths hold 31-71 segments each (`len(d["items"])`),
  so counting path *segments* rather than bboxlog entries would recognise them. It would also make
  `06-nonlabels/invoice-ruled-table.pdf` (73 ruled lines) look like a label, which already happens (not my area).

Likelihood: high for labels printed from a browser/SVG pipeline (this is how many marketplace and web-tool PDFs are
made), low for classic generators. The three variant fixtures are plausible, not contrived.

## Task 2b: multi-up that finds too few

Verified: `a4-6up-3rows`, `a4-8up-4rows`: only 4 cells (halves), so 2-3 labels are merged into one placed label
(content is kept, scaled to 93 %/smaller). `a3-landscape-4-in-a-row` and `a4-2up-diagonal-offset`: the page centre
line passes through a label (label 3 starts 2.3 pt left of centre; second label spans the centre), an item crosses, no
split, whole page placed as one label at 34 % / 47 %. In `a3-landscape-4-in-a-row` even a perfectly symmetric 1x4 fails
(`multiup/a3land_1x4_*`), because only one cut per axis exists. These are design limits, not bugs. They are silent
(merged, not warned).

Own 2x2 / 2x1 / 1x2 grid results (`multiup/`): **all pass** for gutters 0, 1, 3, 5, 10 mm, outer margins 0-30 mm,
A4, A5 and A6 sheets (portrait and landscape), with and without frames, one slot empty (3-up in 2x2). Edge-to-edge
(gutter 0) works.

Fail: 3x1, 1x3, 4x1, 1x4, 2x3, 3x2, 2x4 (always merged, found = 1-4 of 3-8).
Asymmetric placement (`a4_2x2_shiftx*_nf`, `shifty*_nf`, grid moved off the page centre so the centre line passes
through a label):
- with frames: merged labels (safe, all content kept).
- without frames: **silent corruption** for some shifts. `shiftx+15_nf` and `+30_nf` give 4 "labels" that are cut at
  the page centre and re-paired (342/490 foreign items inside clips; PNG checked, half 2D codes glued to the
  neighbouring labels). `shifty-10_nf` mildly so (30 foreign items). Other shifts merge. The centre line cutting through
  a label is not detected when the label is made of small pieces.

## Prototype fix check (not applied)

`proto_minsize.py` refuses a centre cut if a resulting cell would be smaller than a plausible label (short side
< 85 mm or long side < 120 mm). Over the 69 parent fixtures in 01-single and 03-multiup: 44 -> 61 correct;
all 17 single-label over-splits fixed, nothing that passed before regressed. It does not help the compound-path,
>4-label, or off-centre cases. On my own fixtures it also fixes all `gap/`, `minimal/`, `phase/` over-splits but
turns my artificial A6/A5 sheets with 2-4 tiny labels (50x75 mm) into 1 label, which I think is acceptable.

## Likely fixture artefacts / caveats

- `a4-landscape-2up-a5-cells` (A5-sized labels, found 4): same over-split mechanism, but A5 cells pass the size guard.
- `stroke` 2D variants in `repr/` use rect modules (no stroked 2D mode), only the linear code is stroked.
- My A6 / A5 multi-label sheets (50x75 mm labels) are artificial; real carriers do not do this, as far as I know.
- Gaps in my sweeps use quantised text lines (gap width only approximately as labelled); `gap/` results are best read
  as "a band around the centre", not exact widths.
- Dead-zone and phase numbers come from synthetic Helvetica 9 pt text; real fonts change the line-box overlap.
