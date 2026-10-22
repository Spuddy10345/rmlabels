"""Contact sheet from arbitrary PNGs: contact_png.py out.png a.png b.png ...  (pillow only)"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw
out, files = sys.argv[1], sys.argv[2:]
W, cols = 300, 4
tiles = []
for f in files:
    im = Image.open(f).convert("RGB"); im = im.resize((W, int(im.height * W / im.width)))
    d = ImageDraw.Draw(im); d.rectangle((0, 0, W, 12), fill=(255, 255, 200)); d.text((2, 0), Path(f).stem[:52], fill=(0, 0, 0))
    d.rectangle((0, 0, im.width - 1, im.height - 1), outline=(150, 150, 150)); tiles.append(im)
H = max(t.height for t in tiles); rows = (len(tiles) + cols - 1) // cols
sheet = Image.new("RGB", (cols * W, rows * H), (255, 255, 255))
for i, t in enumerate(tiles): sheet.paste(t, ((i % cols) * W, (i // cols) * H))
sheet.save(out); print(out, sheet.size)
