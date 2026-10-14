import sys, shutil, json, subprocess, os
from pathlib import Path
sys.path.insert(0, "/Users/finjo/code/rmlabels/tests/synthetic")
import dryrun, pymupdf
B = Path("/private/tmp/claude-501/agent-sheets-state/misc"); shutil.rmtree(B, ignore_errors=True)
F = Path("/Users/finjo/code/rmlabels/tests/fixtures/synthetic")
# 1. same-second collisions: in-process loop of cli.main with identical state
child = r'''
import sys, json
sys.path.insert(0, "/Users/finjo/code/rmlabels")
from rmlabels import cli, core, store
import subprocess
cli.subprocess.run = lambda *a, **k: None
core.subprocess.run = lambda *a, **k: None
f = "/Users/finjo/code/rmlabels/tests/fixtures/synthetic/01-single/evri-standard-4x6.pdf"
for i in range(3):
    cli.main(["--print", "--text", f"job {i}", f])
'''
env = dict(os.environ, RMLABELS_DATA=str(B/"d1"/"data"), RMLABELS_CONFIG=str(B/"d1"/"c.toml"))
(B/"d1").mkdir(parents=True); (B/"d1"/"c.toml").write_text(f'out_dir="{B/"d1"/"out"}"\n'); (B/"d1"/"out").mkdir()
r = subprocess.run([sys.executable, "-I", "-c", child], env=env, capture_output=True, text=True, cwd="/Users/finjo/code/rmlabels")
print("collision test:", r.returncode, r.stderr[-300:], "files in out:", [p.name for p in (B/"d1"/"out").iterdir()])
import sqlite3
db = sqlite3.connect(B/"d1"/"data"/"rmlabels.db")
print("jobs:", db.execute("select id,sheet,labels from jobs").fetchall())
# 2. lp failing
child2 = r'''
import sys
sys.path.insert(0, "/Users/finjo/code/rmlabels")
from rmlabels import cli, core
import subprocess
def boom(cmd, **k): raise subprocess.CalledProcessError(1, cmd)
core.subprocess.run = boom
cli.main(["--print", "--text", "x"])
'''
env2 = dict(os.environ, RMLABELS_DATA=str(B/"d2"/"data"), RMLABELS_CONFIG=str(B/"d2"/"c.toml"))
(B/"d2").mkdir(parents=True); (B/"d2"/"c.toml").write_text(f'out_dir="{B/"d2"/"out"}"\n[presets]\nlit = \'Return\\nTo\\nLiteral\'\nreal = "Return\\nTo"\n'); (B/"d2"/"out").mkdir()
r = subprocess.run([sys.executable, "-I", "-c", child2], env=env2, capture_output=True, text=True, cwd="/Users/finjo/code/rmlabels")
print("lp fail:", r.returncode, r.stderr.strip().splitlines()[-1], "| next_position exists:", (B/"d2"/"data"/"next_position").exists())
# 3. preset with literal backslash-n
r = dryrun.run(["--text","@lit","--text","@real"], state=B/"d3", png=True)
print("presets", r["exit"])
shutil.copy(B/"d2"/"c.toml", B/"d3"/"config.toml")
r = dryrun.run(["--text","@lit","--text","@real"], state=B/"d3", png=True); print(r["exit"], r["stderr"], r["pngs"])
# 4. pure-vector label refeed
d = pymupdf.open(); p = d.new_page(width=288, height=432)
import random; random.seed(1)
x = 20
for i in range(60):
    w = random.choice([2,3,5]); p.draw_rect(pymupdf.Rect(x,300,x+w,400), fill=(0,0,0), color=None); x += w+2
    if x>260: break
p.draw_rect(pymupdf.Rect(10,10,278,422), color=(0,0,0))
d.save(B/"vec-only.pdf")
r = dryrun.run([str(B/"vec-only.pdf")], state=B/"d4"); print("vec-only gen", len(r["placements"]), r["stderr"].strip())
if r.get("sheet"):
    r2 = dryrun.run([r["sheet"]], state=B/"d5"); print("vec-only refeed", len(r2["placements"]), r2["stderr"].strip())
# 5. long tokens
for n in (20, 30, 40, 50, 60):
    r = dryrun.run(["--text","A"*n], state=B/f"tok{n}", png=True); print("token", n, r["pngs"][0])
