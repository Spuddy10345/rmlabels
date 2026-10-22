# Sheet maths / combinations findings

Scripts: tests/synthetic/agent-sheets/ (matrix.py, bookkeeping.py, dups.py, refeed.py, edge.py, textsheets.py, misc.py, gen.py).
State/outputs: /private/tmp/claude-501/agent-sheets-state/.

## Ranked issues

1. `--start` with a non-integer crashes with a traceback (cli.py `int(a.start)`):
   `--start abc`, `2.5`, `''`, `AUTO`, `'auto '`, `1e0` -> `ValueError: invalid literal for int()`.
   Repro: `uv run python tests/synthetic/dryrun.py --start abc tests/fixtures/synthetic/01-single/evri-standard-4x6.pdf`
   Out-of-range numbers are silently clamped, not rejected: `--start 0` -> 1, `-1` -> 1, `5` -> 4, `99` -> 4 (label lands in the wrong cell and, with --print, the wrong "next position" is saved).
2. One bad input aborts the whole run with a traceback, even when other files are fine (no file is skipped, nothing printed):
   zero-bytes.pdf (EmptyFileError), truncated.pdf (FileDataError), encrypted-needs-password.pdf (`ValueError: document closed or encrypted`), garbage.png / actually-a-pdf.png (PIL.UnidentifiedImageError), a directory named `x.pdf` (FileDataError).
   Repro: `... 06-nonlabels/blank-page.pdf 08-hostile/garbage.png 01-single/evri-standard-4x6.pdf`
   (Unsupported/missing paths and plain directories are handled cleanly.)
3. Own-sheet re-feed drops labels that have no text and no raster image (pure vector art): `own_sheet_cells` only looks at get_image_info and text spans. Repro: a one-page PDF with only drawn rectangles (made in misc.py) -> sheet has 1 label, re-feeding the `rm-labels-*.pdf` gives "No label found". Rare in real labels (human-readable text usually exists). Blank text labels (`--text ''`) likewise vanish on re-feed (they are empty cells).
4. Output filename has 1 s resolution (`rm-labels-%Y%m%d-%H%M%S.pdf`): several jobs in the same second overwrite one file, and history rows all point at the same sheet (3 --print jobs in one second -> 1 file, 3 jobs referencing it, first sheets lost). Only matters for scripted/rapid use.
5. Text labels with a long unbreakable token are shrunk instead of wrapped: 20 chars ~14pt, 30 chars ~7pt, 40 ~5pt, 80/300/500 chars become an illegible hairline. Spaces in text wrap fine.
6. `--print` of a file that yields no labels (blank page etc.) still records it in history as printed (so it later gets a "printed before" warning) and counts toward "N file(s)".
7. No duplicate warning within one run: the same file twice, or two files with identical bytes, silently places two copies (warning only compares against history). Text labels are never duplicate-checked. A re-saved copy (different bytes) is not detected (expected for a hash).
8. Minor: blank/whitespace/`\n\n` `--text` consumes an empty cell silently; `--grid` silently ignores files/--text/--start; `--text -dash` (separate arg) is an argparse error (use `--text=-dash`); text beginning with `@` can't be literal; presets containing a literal backslash-n (TOML single-quoted) are shown literally because presets skip the `\n` replacement; `lp` failure is a raw CalledProcessError traceback (position correctly NOT advanced).

## Verified correct

- Matrix: start {1,2,3,4,auto(seeded 2)} x counts {1,2,3,4,5,7,8,9,13} x text {0,2} x fill {off,on} = 180 runs, 0 failures. Checked JSON placements AND per-cell ink on rendered output: sheet count = ceil((start0+total)/4), first sheet honours start, later sheets restart at 1, nothing dropped or overlapping, text labels after all files, multi-file order kept.
- --fill: no effect on large labels; enlarges small PDF labels (A6-ish 100% -> 126-136%, 57x32 mm -> 239%, rows in 6-up fixture 100 -> 110-114%); images always at 100% fill.
- Bookkeeping (shared --state): 1 label -> next 2; auto continues 2 -> 3 -> 4 -> full prints "none (sheet full)" and stores 0; auto after a full sheet starts at 1 with no "Continuing" message; preview runs (auto or explicit start) never move the position; --print with explicit --start overwrites saved position; multi-sheet end position correct (3 labels from pos 3 -> next 2); --grid --print and failed/empty runs leave it unchanged; text-only prints move it.
- "Printed before" warning: same file, renamed copy of identical bytes, also on preview runs. Warning is on preview too.
- Re-feed: partly filled sheet (start 3) -> 2 labels; text sheet -> 4 labels with text preserved visually; 2-sheet output -> 6 labels; renamed copy still recognised (metadata keyword); images+text; --fill on re-feed stays 100%; re-feed with --start 1/2/4 relocates correctly; two generations stable; mixing with real files works.
- Text rendering: emoji, CJK, Hebrew (RTL), HTML/script text escaped, trailing backslash, 30 lines (tiny but fits), 100 words fine. `@missing`/`@`/`@RETURN` give a clean message listing presets; mixed `@return` + plain + files fine. No args / only unsupported -> "No label files given." (exit 1).
- Many files / same file x12 / 30 distinct files: correct sheet counts.

## Noted in passing (other agents' areas)

- hermes-de-a6.pdf, royalmail-specialdelivery-a6.pdf, anpost/bpost a6 PDFs each yield 2 labels (split in two) though manifest expects 1; page 2 of the 04-multipage four/five/thirteen files yields 2 labels (so "expect 4" gives 5); `variant-evri-standard-4x6-compound-path-barcodes.pdf` yields no label. a4-8up-4rows yields only 4 labels.

## Doubts

- Whether silent clamping of --start 5 is intended; whether `auto` after a full sheet should say so.
- Sheet PNGs eyeballed: s3-n5-t2 (3 sheets), s4-n13-t2-fill, refeed-b, refeed-b-start3, 4 text-edge sheets, long-token strip, preset sheet.
