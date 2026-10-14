import sys, shutil, json
from pathlib import Path
sys.path.insert(0, "/Users/finjo/code/rmlabels/tests/synthetic")
import dryrun
F = Path("/Users/finjo/code/rmlabels/tests/fixtures/synthetic")
S1 = F/"01-single"
G = Path("/Users/finjo/code/rmlabels/tests/synthetic/agent-sheets/gen")
ROOT = Path("/private/tmp/claude-501/agent-sheets-state/refeed"); shutil.rmtree(ROOT, ignore_errors=True)
def gen(tag, argv, png=True):
    r = dryrun.run(argv, state=ROOT/tag, png=png)
    print(f"=== GEN {tag}: {len(r['placements'])} labels, sheets {r.get('sheets')}, exit {r['exit']}", [ (p['pos'],p['sheet'],p['what'][:28]) for p in r['placements']])
    return r
def feed(tag, sheet, extra=(), png=True, name=None):
    p = sheet
    if name:
        q = Path(ROOT/"renamed"); q.mkdir(exist_ok=True); p = str(q/name); shutil.copy(sheet, p)
    r = dryrun.run(list(extra)+[p], state=ROOT/("feed-"+tag), png=png)
    print(f"   FEED {tag} {list(extra)}: {len(r['placements'])} labels exit {r['exit']} sheets {r.get('sheets')}", [(x['pos'],x['sheet'],x['what'][-14:],x['scale']) for x in r['placements']], r['stderr'].strip()[:150], r['traceback'] or "")
    return r
a = gen("a-start3", ["--start","3",str(S1/"evri-standard-4x6.pdf"),str(S1/"gls-de-4x6.pdf")])
feed("a", a["sheet"]); feed("a-start1", a["sheet"], ["--start","1"]); feed("a-start4", a["sheet"], ["--start","4"]); feed("a-start2", a["sheet"], ["--start","2"])
b = gen("b-text", [str(S1/"evri-standard-4x6.pdf"),"--text","@return","--text","Hello\\nthere","--text","FRAGILE"])
feed("b", b["sheet"]); feed("b-start3", b["sheet"], ["--start","3"])
feed("b-renamed", b["sheet"], name="my-sheet.pdf")
c = gen("c-2sheets", [str(G/"n5.pdf"),"--text","@fragile"])
feed("c", c["sheet"])
d = gen("d-vector", [str(S1/"variant-linear-only-vector.pdf"), str(S1/"variant-evri-standard-4x6-compound-path-barcodes.pdf"), str(S1/"qr-only-big-code.pdf") if False else str(F/"02-platforms/qr-only-big-code.pdf")])
feed("d", d["sheet"])
e = gen("e-images", [str(F/"07-images/label.png"), str(F/"07-images/label.jpg"), "--text","note"])
feed("e", e["sheet"])
f = gen("f-fill", ["--fill", str(S1/"royalmail-specialdelivery-a6.pdf"), str(F/"05-pagegeom/tiny-57x32-product-label.pdf")])
feed("f", f["sheet"]); feed("f-fill", f["sheet"], ["--fill"])
# generational: feed twice
g = feed("a2", a["sheet"]); 
feed("a3", g["sheet"])
# mixed with real files
feed("mixed", a["sheet"], [str(S1/"colissimo-fr-4x6.pdf")]) 
