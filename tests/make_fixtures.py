"""Synthetic multi-up pages built from a real single label (kept in private/)."""
import pymupdf


def n_up(label_pdf, n):
    """An A4 page carrying the label 2-up (top/bottom) or 4-up (2x2), like Click & Drop."""
    src = pymupdf.open(label_pdf)
    out = pymupdf.open()
    a4 = pymupdf.paper_rect("a4")
    page = out.new_page(width=a4.width, height=a4.height)
    W, H = a4.width, a4.height
    cells = ([pymupdf.Rect(0, 0, W, H / 2), pymupdf.Rect(0, H / 2, W, H)] if n == 2 else
             [pymupdf.Rect(x, y, x + W / 2, y + H / 2) for y in (0, H / 2) for x in (0, W / 2)])
    for c in cells:
        page.show_pdf_page(c + (8, 8, -8, -8), src, 0)
    return out
