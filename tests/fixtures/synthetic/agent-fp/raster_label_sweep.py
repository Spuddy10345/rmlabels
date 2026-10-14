"""Framed label whose barcodes are raster images (few vector paths) + a despatch note below.
  RMLABELS_DATA=... RMLABELS_CONFIG=... uv run python tests/fixtures/synthetic/agent-fp/raster_label_sweep.py"""
import sys, tempfile, io, base64
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[3]))
import make_agent_fp as M
from PIL import Image
from rmlabels import core
MM = core.MM
im = Image.new("L", (400, 100), 255)
x = 0
while x < 396:
    w = M.rng.choice((2, 4, 6)); 
    for xx in range(x, min(x + w, 400)):
        for yy in range(100): im.putpixel((xx, yy), 0)
    x += w + M.rng.choice((2, 4))
b = io.BytesIO(); im.save(b, "PNG"); BC = "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
def lab():
    return f'''<div style="width:100mm;height:150mm;box-sizing:border-box;border:1px solid #000;padding:4mm;font:11pt Helvetica,Arial,sans-serif">
<div style="background:#d22;color:#fff;font-weight:bold;padding:2mm 3mm">Royal Mail Tracked 48</div><b>Dr Test Recipient</b><br>14 Cobblers Lane<br>ZZ20 8XX
<img src="{BC}" style="display:block;margin-top:14mm;width:80mm;height:26mm"></div>'''
import pymupdf
print("raster-barcode framed label + note below")
for desc, note in (("no note", ""), ("plain text note", M.note(5, False)),
                   ("plain note, no shaded header", M.NOTE_H + "<br>".join(f"{i+1} x Sprocket model {i+1} ... £{4+i}.99" for i in range(5)) + "</div>"), ("ruled note 1 row", M.note(1, True)), ("ruled note 5 rows", M.note(5, True)),
                   ("note w/ logo, unruled", M.note(3, False, True))):
    out = Path(tempfile.mkdtemp(prefix="rl-", dir="/private/tmp/claude-501/agent-fp-state")) / "x.pdf"
    M.chrome(M.page(lab() + note), out)
    clips = [f"{c.width/MM:.0f}x{c.height/MM:.0f}" for _, _, c, _ in core.extract_labels([out])]
    print(f"  {desc:24s} -> {clips}")
