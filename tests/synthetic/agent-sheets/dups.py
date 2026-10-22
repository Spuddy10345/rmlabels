import sys, shutil
from pathlib import Path
sys.path.insert(0, "/Users/finjo/code/rmlabels/tests/synthetic")
import dryrun
F = Path("/Users/finjo/code/rmlabels/tests/fixtures/synthetic/01-single")
evri, gls = str(F/"evri-standard-4x6.pdf"), str(F/"gls-de-4x6.pdf")
ST = Path("/private/tmp/claude-501/agent-sheets-state/dups"); shutil.rmtree(ST, ignore_errors=True)
tmp = Path("/private/tmp/claude-501/agent-sheets-state/dups-files"); tmp.mkdir(parents=True, exist_ok=True)
shutil.copy(evri, tmp/"renamed-copy.pdf"); shutil.copy(evri, tmp/"evri-copy2.pdf")
# re-saved copy (same content, different bytes)
import pymupdf
d = pymupdf.open(evri); d.save(tmp/"resaved.pdf", garbage=4)
def step(label, argv):
    r = dryrun.run(argv, state=ST)
    print("---", label, "| exit", r["exit"], "| n placements", len(r["placements"]), "| next", r["next_position_file"])
    for l in (r["stdout"]+r["stderr"]).splitlines():
        if not l.startswith("  label") and not l.startswith("→"): print("   >", l)
    if r["traceback"]: print(r["traceback"])
step("A identical content twice in one run (fresh history), preview", [evri, str(tmp/"renamed-copy.pdf")])
step("B same file twice in one run, print", ["--print", evri, evri])
step("C renamed copy after print", [str(tmp/"renamed-copy.pdf")])
step("D re-saved (different bytes) after print", [str(tmp/"resaved.pdf")])
step("E gls (never printed) + evri copy2", [gls, str(tmp/"evri-copy2.pdf")])
step("F print gls only", ["--print", gls])
step("G preview gls", [gls])
step("H text label dup (text printed twice)", ["--print", "--text", "hello"])
step("H2 text again", ["--print", "--text", "hello"])
