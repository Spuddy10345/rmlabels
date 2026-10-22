"""Does the output come out upright for landscape sources whose text runs up vs down? Uses the harness.
  uv run python tests/fixtures/synthetic/agent-fp/rotation_check.py"""
import sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "tests/synthetic"))
import dryrun, pymupdf
MM = 72 / 25.4
src = pymupdf.open(ROOT / "tests/fixtures/synthetic/01-single/evri-standard-4x6.pdf")
tmp = Path(tempfile.mkdtemp(prefix="rot-", dir="/private/tmp/claude-501/agent-fp-state"))
def textdir(sheet):
    d = pymupdf.open(sheet); dirs = {}
    for b in d[0].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            k = tuple(round(x) for x in l["dir"]); dirs[k] = dirs.get(k, 0) + len(l["spans"])
    return dirs
UP = {(1, 0): "upright", (0, -1): "reads upward (rotated CCW)", (0, 1): "reads downward (rotated CW)", (-1, 0): "UPSIDE DOWN"}
for name, mk in (
    ("landscape page, content drawn rotated CCW (text reads upward)", lambda: (150, 100, 90)),
    ("landscape page, content drawn rotated CW (text reads downward)", lambda: (150, 100, -90)),
):
    w, h, rot = mk()
    d = pymupdf.open(); pg = d.new_page(width=w * MM * 1.4, height=h * MM * 1.4)
    pg.show_pdf_page(pymupdf.Rect(5 * MM, 5 * MM, 5 * MM + h * MM * 1.3, 5 * MM + w * MM * 1.3).__class__(5 * MM, 5 * MM, 5 * MM + w * MM * 1.3, 5 * MM + h * MM * 1.3), src, 0, rotate=rot)
    f = tmp / f"{'ccw' if rot == 90 else 'cw'}.pdf"; d.save(f)
    seen = textdir(f)
    r = dryrun.run([str(f)], state=Path(tempfile.mkdtemp(dir=tmp)), png=True)
    out = textdir(r["sheet"]) if r.get("sheet") else None
    print(name); print("   source text direction:", {UP.get(k, k): v for k, v in seen.items()}); print("   output text direction:", {UP.get(k, k): v for k, v in (out or {}).items()}, r["placements"][:1])
