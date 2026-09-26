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
import subprocess
import sys
from pathlib import Path

import pymupdf

from . import core, store
from .config import CONFIG
from .inputs import IMAGE_EXT, is_supported, open_source


def resolve_text(value: str) -> str:
    if value.startswith("@"):
        presets = CONFIG["presets"]
        if value[1:] not in presets:
            names = ", ".join("@" + k for k in presets) or "none — add a [presets] table to the config"
            sys.exit(f"Unknown text preset {value}. Available: {names}")
        return presets[value[1:]]
    return value.replace("\\n", "\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["serve"]:
        return serve(argv[1:])
    if argv[:1] == ["fetch-mail"]:
        return fetch_mail()

    ap = argparse.ArgumentParser(prog="rmlabels", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--start", default="1", help="first free label on the sheet: 1-4 (TL,TR,BL,BR) or 'auto'")
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
        if a.start == "auto":
            start = core.read_next_position()
        else:
            start = max(1, min(4, int(a.start))) - 1

        db = store.connect()
        labels = []
        for f in files:
            # PDFs go in as paths, exactly as before; images are converted in memory
            src = open_source(f.name, path=f) if f.suffix.lower() in IMAGE_EXT else f
            found = list(core.extract_labels([src]))
            if not found:
                print(f"No label found in {f.name} (text-only pages are skipped; use --text for notes).", file=sys.stderr)
            labels += found
            when = store.last_printed(db, hashlib.sha256(f.read_bytes()).hexdigest())
            if when:
                print(f"⚠ {f.name} was already printed on {dt.datetime.fromisoformat(when):%a %d %b %H:%M}.")
        labels += [core.TextLabel(t) for t in texts]
        if not labels:
            sys.exit("Couldn't find any labels in those files.")
        if a.start == "auto" and start:
            used = "label 1 is" if start == 1 else f"labels 1-{start} are"
            print(f"Continuing a part-used sheet: {used} treated as already used.")
            print("Load that sheet, or choose 'New sheet' if you put in a fresh one.")
        print(f"{len(labels)} label(s) from {len(files)} file(s), starting at position {start + 1}:")
        end = core.place(out, labels, start, a.fill)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = CONFIG["out_dir"] / (f"rm-labels-grid.pdf" if a.grid else f"rm-labels-{stamp}.pdf")
    core.save_sheet(out, dest)
    print(f"→ {dest}")

    if a.print:
        core.print_sheet(dest, mono=a.mono, draft=a.draft)
        if not a.grid:
            core.write_next_position(end)
            ids = [store.add_file(db, f.name, f.read_bytes(), "cli")[0] for f in files]
            ids += [store.add_text(db, t, "cli") for t in texts]
            store.record_job(db, ids, dest, start + 1, len(labels), len(out), a.mono)
            print(f"Printed. Next free label on the last sheet: {end % 4 + 1 if end % 4 else 'none (sheet full)'}")
    else:
        # Preview never uses up labels: only a real print moves the saved position on
        subprocess.run(["open", "-a", "Preview", str(dest)])


def serve(argv):
    ap = argparse.ArgumentParser(prog="rmlabels serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    import uvicorn
    uvicorn.run("rmlabels.web:app", host=a.host, port=a.port)


def fetch_mail():
    from . import mail
    added = mail.fetch_into_queue(store.connect())
    print(f"Queued {len(added)} label file(s) from mail." + ("" if not added else "\n  " + "\n  ".join(added)))


if __name__ == "__main__":
    main()
