import sys; sys.path.insert(0, "/Users/finjo/code/rmlabels")
import pymupdf
from rmlabels import core
for p in sys.argv[1:]:
    pg=pymupdf.open(p)[0]; items=core.page_items(pg); H=pg.rect.height; W=pg.rect.width
    above=max(b.y1 for k,b in items if b.y1<=H/2+2)
    below=min(b.y0 for k,b in items if b.y0>=H/2-2)
    # items touching the mid band
    print(p.split('/')[-1], 'H/2=%.1f'%(H/2), 'gap: last-above %.1f first-below %.1f -> free band %.1f pt (%.1f mm)'%(above,below,below-above,(below-above)/core.MM))
    near=[(k,tuple(round(v,1) for v in b)) for k,b in items if abs(b.y1-H/2)<30 or abs(b.y0-H/2)<30]
    for n in near[:12]: print('   ',n)
