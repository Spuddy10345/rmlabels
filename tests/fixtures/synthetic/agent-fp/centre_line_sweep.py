"""How the unframed 'label + despatch note below' outcome depends on where the label's last ink sits
relative to the A4 centre line (y=148.5mm). Builds temp PDFs (not kept), calls core.extract_labels.
  RMLABELS_DATA=... RMLABELS_CONFIG=... uv run python tests/fixtures/synthetic/agent-fp/centre_line_sweep.py"""
import sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[3]))
import pymupdf
import make_agent_fp as M
from rmlabels import core
MM = core.MM

def build(y_end, ruled, framed):
    lab = M.label_div(border=False, h=150).replace("height:150mm", "height:%dmm" % max(y_end, 100))
    # push footer: label_div has fixed layout; instead put a footer line at y_end
    foot = f'<div style="position:absolute;left:4mm;top:{y_end-6}mm;font:6pt Helvetica;color:#777">TEST LABEL - NOT VALID FOR POSTAGE - INVENTED DATA</div>'
    frame = f'<div style="position:absolute;left:0;top:0;width:100mm;height:{y_end+1}mm;border:1px solid #000;box-sizing:border-box"></div>' if framed else ""
    html = f'<div style="position:relative;height:297mm">{lab}{foot}{frame}<div style="position:absolute;top:{y_end+8}mm;left:0;width:210mm">{M.note(5, ruled)}</div></div>'
    out = Path(tempfile.mkdtemp(prefix="cl-", dir="/private/tmp/claude-501/agent-fp-state")) / "x.pdf"
    M.chrome(M.page(html), out); return out

print("y_end = bottom of label ink (mm) | unframed+plain note | unframed+ruled note | framed+ruled note   [clips in mm WxH]")
for y in (100, 120, 135, 144, 147, 148, 150, 152, 156, 165):
    row = []
    for ruled, framed in ((False, False), (True, False), (True, True)):
        pdf = build(y, ruled, framed)
        clips = [f"{c.width/MM:.0f}x{c.height/MM:.0f}" for _, _, c, _ in core.extract_labels([pdf])]
        row.append(", ".join(clips) or "none")
    print(f"{y:4d} | {row[0]:16s} | {row[1]:24s} | {row[2]}")
