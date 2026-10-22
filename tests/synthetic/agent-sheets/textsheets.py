import sys, shutil, json
from pathlib import Path
sys.path.insert(0, "/Users/finjo/code/rmlabels/tests/synthetic")
import dryrun
ST = Path("/private/tmp/claude-501/agent-sheets-state/textsheets"); shutil.rmtree(ST, ignore_errors=True)
groups = {
 "g1": ["", "   ", "\\n\\n", "<b>x</b>&amp;"],
 "g2": ["📦 FRAGILE 🚚\\n❤️👨‍👩‍👧", "日本語のラベル\\n東京都千代田区1-1", "שלום עולם\\nרחוב הרצל 1", "W"*80],
 "g3": ["x"*500, "\\n".join(f"line {i}" for i in range(30)), " ".join(["word"]*100), "W"*300],
 "g4": ["hello\\", "a\\\\nb", "<script>alert(1)</script><img src=x>", "@return"],
}
for g, ts in groups.items():
    argv = []
    for t in ts: argv += ["--text", t]
    r = dryrun.run(argv, state=ST/g, png=True)
    print(g, r["exit"], r["traceback"], r["stderr"].strip()[:200], r["pngs"], [p["what"] for p in r["placements"]])
