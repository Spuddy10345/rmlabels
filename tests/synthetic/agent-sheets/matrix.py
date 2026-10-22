"""Matrix: start x label counts x text x fill. Verifies JSON placements and per-cell ink on the output sheet."""
import itertools, json, math, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, "/Users/finjo/code/rmlabels/tests/synthetic")
import dryrun, pymupdf
sys.path.insert(0, "/Users/finjo/code/rmlabels")
F = Path("/Users/finjo/code/rmlabels/tests/fixtures/synthetic")
evri, gls, col, rm48, her = [str(F/"01-single"/n) for n in ("evri-standard-4x6.pdf","gls-de-4x6.pdf","colissimo-fr-4x6.pdf","royalmail-tracked48-a4.pdf","hermes-de-a6.pdf")]
up4, up2 = str(F/"03-multiup/a4-4up-2x2.pdf"), str(F/"03-multiup/a4-2up-stacked.pdf")
four, five, thirteen = [str(F/"04-multipage"/n) for n in ("four-labels-one-per-page.pdf","five-labels-one-per-page.pdf","thirteen-labels-bulk.pdf")]
G=lambda n:str(Path("/Users/finjo/code/rmlabels/tests/synthetic/agent-sheets/gen")/f"n{n}.pdf")
SETS = {1:[evri],2:[up2],3:[evri,up2],4:[up4],5:[G(5)],7:[G(5),up2],8:[up4,G(4)],9:[G(5),G(4)],13:[G(13)]}
TEXTS = {0:[], 2:["--text","@return","--text","Hello\\nworld"]}
ROOT = Path("/private/tmp/claude-501/agent-sheets-state/matrix")
def cellrects():
    from rmlabels import core
    return [core.cell_rect(p, pad=0) for p in range(4)]
def occupied(sheet):
    cr = cellrects(); doc = pymupdf.open(sheet); out=[]
    for pg in doc:
        pix = pg.get_pixmap(dpi=40)
        occ=[]
        for r in cr:
            sc=40/72
            x0,y0,x1,y1=[int(v*sc) for v in (r.x0,r.y0,r.x1,r.y1)]
            n=0
            for y in range(y0,y1):
                for x in range(x0,x1):
                    if sum(pix.pixel(x,y)[:3])<600: n+=1
            occ.append(n>20)
        # ink outside any cell
        out.append(occ)
    return out
def job(args):
    start, n, tk, fill = args
    tag=f"s{start}-n{n}-t{tk}-f{int(fill)}"
    argv=[]
    if start!="auto": argv+=["--start",str(start)]
    else: argv+=["--start","auto"]
    if fill: argv.append("--fill")
    argv+=TEXTS[tk]+SETS[n]
    st=ROOT/tag
    if start=="auto":
        # pre-seed next_position=2 (labels 1,2 used) -> expect start 3
        (st/"data").mkdir(parents=True,exist_ok=True); (st/"data"/"next_position").write_text("2")
    r=dryrun.run(argv,state=st)
    return args,r
def main():
    jobs=list(itertools.product([1,2,3,4,"auto"],[1,2,3,4,5,7,8,9,13],[0,2],[False,True]))
    problems=[]; results={}
    with ThreadPoolExecutor(6) as ex:
        for args,r in ex.map(job,jobs):
            start,n,tk,fill=args
            s0 = 2 if start=="auto" else start-1
            T = 2 if tk else 0
            total=n+T
            pl=r.get("placements",[])
            issues=[]
            if r.get("exit")!=0 or r.get("traceback"): issues.append(f"exit={r.get('exit')} {r.get('traceback')}")
            if len(pl)!=total: issues.append(f"placements {len(pl)} != {total}")
            exp_sheets=math.ceil((s0+total)/4)
            if r.get("sheets")!=exp_sheets: issues.append(f"sheets {r.get('sheets')} != {exp_sheets}")
            # sequence
            exp=[((s0+i)%4+1,(s0+i)//4+1) for i in range(total)]
            got=[(p["pos"],p["sheet"]) for p in pl]
            if exp!=got: issues.append(f"seq {got} != {exp}")
            kinds=["text" if p["what"].startswith("text") else "file" for p in pl]
            if kinds!=["file"]*n+["text"]*T: issues.append(f"order {kinds}")
            if r.get("sheet"):
                occ=occupied(r["sheet"])
                for si,o in enumerate(occ):
                    e=[False]*4
                    for i in range(total):
                        pos,sh=exp[i]
                        if sh==si+1: e[pos-1]=True
                    if o!=e: issues.append(f"sheet{si+1} occupancy {o} != {e}")
            results[str(args)]=dict(issues=issues,scales=[p['scale'] for p in pl],next=r.get("next_position_file"))
            if issues: problems.append((args,issues))
    json.dump(results,open(ROOT/"matrix-results.json","w"),indent=1)
    print(len(jobs),"runs;",len(problems),"with issues")
    for a,i in problems: print(a,i)
main()
