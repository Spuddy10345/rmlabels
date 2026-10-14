import json, sys, shutil
from pathlib import Path
sys.path.insert(0, "/Users/finjo/code/rmlabels/tests/synthetic")
import dryrun
F = Path("/Users/finjo/code/rmlabels/tests/fixtures/synthetic/01-single")
G = Path("/Users/finjo/code/rmlabels/tests/synthetic/agent-sheets/gen")
evri, gls, col, rm48 = [str(F/n) for n in ("evri-standard-4x6.pdf","gls-de-4x6.pdf","colissimo-fr-4x6.pdf","royalmail-tracked48-a4.pdf")]
ST = Path("/private/tmp/claude-501/agent-sheets-state/book"); shutil.rmtree(ST, ignore_errors=True)
def step(label, argv, st=ST):
    r = dryrun.run(argv, state=st)
    pl = [(p["pos"], p["sheet"]) for p in r.get("placements", [])]
    print(f"--- {label}: {' '.join(a if not a.startswith('/') else Path(a).name for a in argv)}")
    print("   exit", r["exit"], "| sheets", r.get("sheets"), "| placements", pl, "| next_position file:", repr(r["next_position_file"]))
    for l in (r["stdout"]+r["stderr"]).splitlines():
        if not l.startswith("  label") and not l.startswith("→"): print("   >", l)
    if r["traceback"]: print(r["traceback"])
    return r
step("1 print 1 label from fresh", ["--print", evri])
step("2 auto, 1 label (gls)", ["--start","auto","--print", gls])
step("3 preview auto (should not move)", ["--start","auto", col])
step("3b preview with start 1 (should not move)", [col])
step("4 auto print 1 (col)", ["--start","auto","--print", col])
step("5 auto print 1 (rm48) -> fills sheet", ["--start","auto","--print", rm48])
step("6 auto after full sheet, preview", ["--start","auto", evri])
step("7 auto print 2 labels after full sheet", ["--start","auto","--print", G/"n2.pdf" if False else str(G/"n2.pdf")])
step("8 auto print 3 labels (pos3 -> spill)", ["--start","auto","--print", str(G/"n3.pdf")])
step("9 print text only", ["--print","--text","hi"])
step("10 auto print 5", ["--start","auto","--print", str(G/"n5.pdf")])
step("11 explicit start 2 print 1", ["--start","2","--print", evri])
step("12 --start auto, preview 4 (should still continue)", ["--start","auto", str(G/"n4.pdf")])
step("13 --grid --print (should not move)", ["--grid","--print"])
step("14 only unsupported file --print (no state change)", ["--print","/Users/finjo/code/rmlabels/tests/fixtures/synthetic/08-hostile/notes.txt"])
step("15 --print w/ file lacking labels (blank page) + text", ["--print","--start","auto","/Users/finjo/code/rmlabels/tests/fixtures/synthetic/06-nonlabels/blank-page.pdf","--text","x"])
