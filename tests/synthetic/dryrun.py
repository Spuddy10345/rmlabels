"""Run the real rmlabels CLI without side effects (no Preview, no printer, isolated state).

    uv run python tests/synthetic/dryrun.py [--state DIR] [--png] [rmlabels args...]
    uv run python tests/synthetic/dryrun.py --png --start 3 tests/fixtures/synthetic/03-multiup/a4-4up-2x2.pdf

Prints one JSON object: exit status, stdout/stderr, any traceback, the per-label placement lines,
and the output sheet. `--print` is allowed: `lp` is stubbed, but history and the saved next-free
position ARE recorded, in --state (so repeat runs with the same --state to test --start auto).
Default --state is a fresh temp dir. Your real ~/.local/share/rmlabels is never touched.

Also importable: `from dryrun import run`.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(argv: list[str], state: Path | None = None, png: bool = False, dpi: int = 70) -> dict:
    """Run the CLI in a subprocess-free way. state keeps queue/history/next_position between calls."""
    state = Path(state or tempfile.mkdtemp(prefix="rmlabels-dry-"))
    (state / "out").mkdir(parents=True, exist_ok=True)
    cfg = state / "config.toml"
    if not cfg.exists():
        cfg.write_text(f'out_dir = "{state / "out"}"\n[presets]\nreturn = "Return to:\\nTest Person\\n1 Test Road"\nfragile = "FRAGILE\\nHandle with care"\n')
    # Must be set before rmlabels is imported; a child process keeps each run's module state clean.
    return _in_child(argv, state, png, dpi)


def _in_child(argv, state, png, dpi):
    import subprocess
    env = dict(os.environ, RMLABELS_DATA=str(state / "data"), RMLABELS_CONFIG=str(state / "config.toml"), PYTHONWARNINGS="ignore")
    r = subprocess.run([sys.executable, "-I", str(Path(__file__)), "--child", json.dumps(dict(argv=argv, png=png, dpi=dpi, state=str(state)))],
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=900)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return dict(argv=argv, exit="child-crash", stderr=r.stderr[-2000:], stdout=r.stdout[-2000:])


def child(spec: dict):
    sys.path.insert(0, str(ROOT))
    import pymupdf
    from rmlabels import cli, core
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="request id is EPSON_TEST-7 (1 file(s))\n", stderr="")
    cli.subprocess.run = fake_run
    core.subprocess.run = fake_run
    out, err = io.StringIO(), io.StringIO()
    status, tb = 0, None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(spec["argv"])
        except SystemExit as e:
            status = e.code if e.code is not None else 0
        except BaseException:
            status, tb = "EXCEPTION", traceback.format_exc()
    stdout = out.getvalue()
    sheet = None
    m = re.search(r"→ (.+\.pdf)", stdout)
    placements = re.findall(r"^\s+label (\d) of sheet (\d+) ← (.+?)\s+\((\d+)%(, rotated)?\)$|^\s+label (\d) of sheet (\d+) ← (text .+)$", stdout, re.M)
    res = dict(argv=spec["argv"], exit=status, stdout=stdout, stderr=err.getvalue(), traceback=tb, calls=[c[:3] for c in calls],
               placements=[dict(pos=int(a or f), sheet=int(b or g), what=(c or h), scale=int(d) if d else None, rotated=bool(e)) for a, b, c, d, e, f, g, h in placements])
    if m and Path(m.group(1)).exists():
        sheet = Path(m.group(1))
        doc = pymupdf.open(sheet)
        res.update(sheet=str(sheet), sheets=len(doc))
        if spec["png"]:
            pngs = []
            for i, pg in enumerate(doc):
                p = sheet.with_name(f"{sheet.stem}-p{i + 1}.png")
                pg.get_pixmap(dpi=spec["dpi"]).save(p)
                pngs.append(str(p))
            res["pngs"] = pngs
    ns = Path(spec["state"]) / "data" / "next_position"
    res["next_position_file"] = ns.read_text() if ns.exists() else None
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        child(json.loads(sys.argv[2]))
    else:
        args, st, png = sys.argv[1:], None, False
        if "--png" in args:
            args.remove("--png")
            png = True
        if "--state" in args:
            i = args.index("--state")
            st = Path(args[i + 1])
            del args[i:i + 2]
        print(json.dumps(run(args, st, png), indent=1, ensure_ascii=False))
