"""
rmlabels — combine shipping labels from any source onto 4-up A4 label sheets
(Double Dragon / Avery J8169 / L7169: 99.1 x 139 mm, 2 x 2).

Accepts PDFs and images (PNG, JPG, HEIC…). Text-only labels (--text) are
placed after the files. Presets from ~/.config/rmlabels/config.toml are used
as --text @name.

Usage:
  rmlabels [--start N|auto] [--print|--preview] [--mono] [--draft] [--fill]
           [--text TEXT ...] FILE ...
  rmlabels --grid [--print] [--mono]
  rmlabels serve [--port 8765]     web UI
  rmlabels fetch-mail              pull label attachments into the web UI queue
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import logging.handlers
import subprocess
import sys
from pathlib import Path

import pymupdf

from . import core, store
from .config import CONFIG, DATA_DIR
from .inputs import is_supported, open_source


def resolve_text(value: str) -> str:
    if value.startswith("@"):
        presets = CONFIG["presets"]
        if value[1:] not in presets:
            names = ", ".join("@" + k for k in presets) or "none — add a [presets] table to the config"
            sys.exit(f"Unknown text preset {value}. Available: {names}")
        return presets[value[1:]]
    return value.replace("\\n", "\n")


def start_arg(value: str) -> str | int:
    """--start: 1-4, or 'auto'. Anything else is refused: silently clamping 5 to 4 would waste a label."""
    if value.lower() == "auto":
        return "auto"
    try:
        n = int(value)
    except ValueError:
        n = 0
    if not 1 <= n <= 4:
        raise argparse.ArgumentTypeError(f"{value!r} isn't a label position: use 1, 2, 3, 4 or auto")
    return n


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["serve"]:
        return serve(argv[1:])
    if argv[:1] == ["fetch-mail"]:
        return fetch_mail()

    ap = argparse.ArgumentParser(prog="rmlabels", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--start", default=1, type=start_arg, help="first free label on the sheet: 1-4 (TL,TR,BL,BR) or 'auto'")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--print", action="store_true", help="send straight to the printer at 100%%")
    mode.add_argument("--preview", action="store_true", help="open in Preview (default)")
    ap.add_argument("--grid", action="store_true", help="make a calibration/test grid instead (combine with --print)")
    ap.add_argument("--fill", action="store_true", help="allow enlarging small labels to fill the label")
    ap.add_argument("--mono", action="store_true", help="print with the black cartridge only (greyscale driver mode)")
    ap.add_argument("--draft", action="store_true", help="use the driver's normal 360 dpi quality instead of Fine 720 dpi")
    ap.add_argument("--text", action="append", default=[], metavar="TEXT",
                    help="add a text-only label (repeatable; \\n for a new line, @name for a preset)")
    a = ap.parse_args(argv)

    out = pymupdf.open()
    files, texts = [], [resolve_text(t) for t in a.text]
    if a.grid:
        core.test_grid(out)
        start = end = 0
    else:
        for f in a.files:
            f = f.expanduser()
            if not f.exists():
                print(f"Skipping {f}: file not found.", file=sys.stderr)
            elif not is_supported(f.name):
                print(f"Skipping {f.name}: not a PDF or image.", file=sys.stderr)
            else:
                files.append(f)
        if not files and not texts:
            sys.exit("No label files given.")
        mask = core.read_used_mask() if a.start == "auto" else core.mask_from_start(a.start - 1)

        db = store.connect()
        labels, readable = [], []
        for f in files:
            try:
                # open_source goes by what the file contains, so a PNG saved as .pdf still works
                found = list(core.extract_labels([open_source(f.name, path=f)]))
                when = store.last_printed(db, hashlib.sha256(f.read_bytes()).hexdigest())
            except Exception as e:  # one bad file must not cost the user the good ones
                print(f"Skipping {f.name}: {e}", file=sys.stderr)
                continue
            readable.append(f)
            if not found:
                print(f"No label found in {f.name} (text-only pages are skipped; use --text for notes).", file=sys.stderr)
            labels += found
            if when:
                print(f"⚠ {f.name} was already printed on {dt.datetime.fromisoformat(when):%a %d %b %H:%M}.")
        files = readable
        labels += [core.TextLabel(t) for t in texts]
        if not labels:
            sys.exit("Couldn't find any labels in those files.")
        free = core.free_slots(mask)
        first = (free or [0])[0]
        if a.start == "auto" and mask:
            print(f"Continuing a part-used sheet: {describe_used(mask)} treated as already used.")
            print("Load that sheet, or choose 'New sheet' if you put in a fresh one.")
        print(f"{len(labels)} label(s) from {len(files)} file(s), starting at position {first + 1}:")
        core.place(out, labels, 0, a.fill, skip={i for i in range(4) if mask >> i & 1})

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = CONFIG["out_dir"] / (f"rm-labels-grid.pdf" if a.grid else f"rm-labels-{stamp}.pdf")
    core.save_sheet(out, dest)
    print(f"→ {dest}")

    if a.print:
        try:
            job = core.print_sheet(dest, mono=a.mono, draft=a.draft)
        except RuntimeError as e:
            sys.exit(f"Printing failed: {e}")
        if not a.grid:
            after = core.used_after(mask, len(labels))
            core.write_used_mask(after)
            ids = [store.add_file(db, f.name, f.read_bytes(), "cli")[0] for f in files]
            ids += [store.add_text(db, t, "cli") for t in texts]
            store.record_job(db, ids, dest, first + 1, len(labels), len(out), a.mono, cups_job=job, used_before=mask)
            print(f"Printed. Next free label on the last sheet: {core.free_slots(after)[0] + 1 if after else 'none (sheet full)'}")
    else:
        # Preview never uses up labels: only a real print moves the saved position on
        subprocess.run(["open", "-a", "Preview", str(dest)])


def describe_used(mask: int) -> str:
    """'label 1 is', 'labels 1-3 are', 'labels 1 and 3 are'."""
    used = [i + 1 for i in range(4) if mask >> i & 1]
    if len(used) == 1:
        return f"label {used[0]} is"
    if used == list(range(1, len(used) + 1)):
        return f"labels 1-{len(used)} are"
    return "labels " + ", ".join(map(str, used[:-1])) + f" and {used[-1]} are"


def setup_logging(path: Path):
    """Keep our own log (rotated, so it can't grow forever) and still copy it to stderr for launchd's log.
    uvicorn must be started with log_config=None: its default configuration strips handlers off its own loggers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("rmlabels", "uvicorn.error"):
        logger = logging.getLogger(name)
        for h in [h for h in logger.handlers if getattr(h, "_rmlabels", False)]:
            logger.removeHandler(h)  # called twice (tests, reloads) must not duplicate lines
        file = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file.setFormatter(fmt)
        stderr = logging.StreamHandler()
        stderr.setFormatter(fmt)
        for h in (file, stderr):
            h._rmlabels = True
            logger.addHandler(h)
        logger.setLevel(logging.INFO)


def serve(argv):
    ap = argparse.ArgumentParser(prog="rmlabels serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    import uvicorn
    setup_logging(DATA_DIR / "rmlabels.log")
    # access_log off: the page polls, so it would log thousands of lines a day. log_config=None: keep our handlers.
    uvicorn.run("rmlabels.web:app", host=a.host, port=a.port, access_log=False, log_config=None)


def fetch_mail():
    from . import mail
    added = mail.fetch_into_queue(store.connect())
    print(f"Queued {len(added)} label file(s) from mail." + ("" if not added else "\n  " + "\n  ".join(added)))


if __name__ == "__main__":
    main()
