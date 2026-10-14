"""Show what core.page_items sees on each page (agent-fp). Read-only; does not call the CLI.
  RMLABELS_DATA=/private/tmp/claude-501/agent-fp-state/data RMLABELS_CONFIG=/private/tmp/claude-501/agent-fp-state/config.toml \
  uv run python tests/fixtures/synthetic/agent-fp/inspect_items.py file.pdf ..."""
import sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import pymupdf
from rmlabels import core
MM = core.MM
for f in sys.argv[1:]:
    d = pymupdf.open(f)
    for pg in d:
        if pg.rotation: pg.remove_rotation()
        items = core.page_items(pg)
        c = collections.Counter(("image" if "image" in k else "path" if "path" in k else "text") for k, _ in items)
        imgs = [(round(r.width/MM), round(r.height/MM)) for k, r in items if "image" in k]
        u = core.union(items)
        regs = core.split_regions(pg, items)
        print(f"{Path(f).name} p{pg.number+1} page={pg.rect.width/MM:.0f}x{pg.rect.height/MM:.0f}mm {dict(c)} imgs_mm={imgs} union={u.width/MM:.0f}x{u.height/MM:.0f}mm "
              f"looks={core.looks_like_label(items)} regions={len(regs)} frame={'Y' if core.find_frame(pg, items) else 'N'}")

print("--- clips from extract_labels (x0,y0,x1,y1 mm  w x h mm)")
for f in sys.argv[1:]:
    for doc, pno, clip, tag in core.extract_labels([Path(f)]):
        print(f"{Path(f).name} {tag}: [{clip.x0/MM:.0f},{clip.y0/MM:.0f},{clip.x1/MM:.0f},{clip.y1/MM:.0f}] {clip.width/MM:.0f}x{clip.height/MM:.0f}mm")
