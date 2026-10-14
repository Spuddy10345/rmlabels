import pymupdf
from pathlib import Path
B=Path("/private/tmp/claude-501/agent-sheets-state/misc")
imgs=[("tok20",),("tok30",),("tok40",),("tok50",),("tok60",)]
pix=[pymupdf.Pixmap(str(next((B/n[0]/"out").glob("*.png")))) for n in imgs]
cw=pix[0].width//2; ch=pix[0].height//2
page=pymupdf.open().new_page(width=cw*5,height=ch)
for i,p in enumerate(pix):
    p.set_origin(0,0)
    top=pymupdf.Pixmap(p, p.width, p.height) if False else p
    page.insert_image(pymupdf.Rect(i*cw,0,(i+1)*cw,ch),pixmap=p,keep_proportion=False,clip=pymupdf.Rect(0,0,cw,ch)) if False else None
# simpler: crop each to top-left cell
for i,p in enumerate(pix):
    c=pymupdf.Pixmap(p.colorspace,pymupdf.IRect(0,0,cw,ch),p.alpha) if False else None
page2=pymupdf.open().new_page(width=cw*5,height=ch)
for i,p in enumerate(pix):
    page2.show_pdf_page(pymupdf.Rect(i*cw,0,(i+1)*cw,ch), pymupdf.open(next((B/f"tok{[20,30,40,50,60][i]}"/"out").glob("*.pdf"))), 0, clip=pymupdf.Rect(0,0,300,ch*72/72*0+420))
page2.get_pixmap(dpi=80).save(B/"tokens.png")
shutil_p=B/"d3"/"out"
