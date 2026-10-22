import sys; sys.path.insert(0, "/Users/finjo/code/rmlabels")
import pymupdf
from rmlabels import core
MM=core.MM
def diag(path, verbose=True):
    doc=pymupdf.open(path); out=[]
    for page in doc:
        items=core.page_items(page)
        W,H=page.rect.width,page.rect.height
        un=core.union(items)
        cy=[(k,tuple(round(v) for v in b)) for k,b in items if b.y0<H/2-2 and b.y1>H/2+2]
        cx=[(k,tuple(round(v) for v in b)) for k,b in items if b.x0<W/2-2 and b.x1>W/2+2]
        regs=core.split_regions(page,items)
        kinds={}
        for k,_ in items: kinds[k]=kinds.get(k,0)+1
        out.append(dict(page=(round(W),round(H)),n=len(items),kinds=kinds,union=tuple(round(v) for v in un),
            cross_y=len(cy),cross_x=len(cx),regions=len(regs),
            region_stats=[(sum('image' in k for k,_ in r),sum('path' in k for k,_ in r),len(r)) for r in regs],
            whole_looks=core.looks_like_label(items)))
    return out
if __name__=="__main__":
    for p in sys.argv[1:]:
        for o in diag(p): print(p.split('synthetic/')[-1],o)
