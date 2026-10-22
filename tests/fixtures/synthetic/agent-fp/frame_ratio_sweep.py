"""find_frame's 2nd-chance rule needs >=80% of all vector paths inside the label box.
A label with few vector ops + a ruled despatch table below breaks it. Sweep table rows.
  RMLABELS_DATA=... RMLABELS_CONFIG=... uv run python tests/fixtures/synthetic/agent-fp/frame_ratio_sweep.py"""
import sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[3]))
import make_agent_fp as M
from rmlabels import core
MM = core.MM

def lab(nbars, qrcells):
    q = M.qr(26) if qrcells else ""
    return f'''<div style="width:100mm;height:150mm;box-sizing:border-box;border:1px solid #000;padding:4mm;font:11pt Helvetica,Arial,sans-serif">
<div style="background:#d22;color:#fff;font-weight:bold;padding:2mm 3mm">Royal Mail Tracked 48</div>
<div style="display:flex;justify-content:space-between;margin-top:3mm"><div><b>Dr Test Recipient</b><br>14 Cobblers Lane<br>ZZ20 8XX</div>{q}</div>
<div style="margin-top:12mm">{M.bars(80, 26, nbars)}</div></div>'''

print("label_paths(approx) | note table rows -> clip(s) mm ; 'FRAME' when clip is the 100x150 box")
for nbars, qr_ in ((60, False), (60, True), (100, True)):
    for rows in (0, 1, 2, 3, 5, 8):
        note = M.note(rows, True) if rows else ""
        html = lab(nbars, qr_) + note
        out = Path(tempfile.mkdtemp(prefix="fr-", dir="/private/tmp/claude-501/agent-fp-state")) / "x.pdf"
        M.chrome(M.page(html), out)
        import pymupdf
        d = pymupdf.open(out); it = core.page_items(d[0]); paths = sum('path' in k for k, _ in it)
        clips = [(c.width/MM, c.height/MM) for _, _, c, _ in core.extract_labels([out])]
        tag = "FRAME" if clips and abs(clips[0][0]-100) < 3 and abs(clips[0][1]-150) < 3 else "NO-FRAME"
        print(f"bars={nbars:3d} qr={int(qr_)} total_paths={paths:3d} note_rows={rows} -> {tag:8s} {[f'{w:.0f}x{h:.0f}' for w, h in clips]}")
