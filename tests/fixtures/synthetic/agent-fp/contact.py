"""Contact sheet of output PNGs from sweep-results-fp.json -> /private/tmp/claude-501/agent-fp-state/view/contact-<n>.png
  uv run python tests/fixtures/synthetic/agent-fp/contact.py [name-substring ...]"""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw
HERE = Path(__file__).resolve().parent
res = json.loads((HERE / "sweep-results-fp.json").read_text())
subs = sys.argv[1:]
sel = [r for r in res if not subs or any(s in r["file"] for s in subs)]
W = 300; cols = 4
tiles = []
for r in sel:
    for p in (r["png"] or [])[:1]:
        im = Image.open(p).convert("RGB"); im = im.resize((W, int(im.height * W / im.width)))
        d = ImageDraw.Draw(im); d.rectangle((0, 0, W, 12), fill=(255, 255, 200)); d.text((2, 0), f"{r['file'][:48]} f={r['found']}", fill=(0, 0, 0))
        d.rectangle((0, 0, im.width - 1, im.height - 1), outline=(150, 150, 150)); tiles.append(im)
H = max(t.height for t in tiles); rows = (len(tiles) + cols - 1) // cols
sheet = Image.new("RGB", (cols * W, rows * H), (255, 255, 255))
for i, t in enumerate(tiles): sheet.paste(t, ((i % cols) * W, (i // cols) * H))
out = Path("/private/tmp/claude-501/agent-fp-state/view"); out.mkdir(exist_ok=True, parents=True)
n = "-".join(subs) or "all"; sheet.save(out / f"contact-{n}.png"); print(out / f"contact-{n}.png", sheet.size)
