import io

import pymupdf
import pytest
from PIL import Image, ImageDraw

from rmlabels import core
from rmlabels.inputs import open_source


def fake_label_png(size=(600, 400), margin=150, fmt="PNG"):
    """A landscape 'label' (black box with bars) sitting in a white margin."""
    img = Image.new("RGB", (size[0] + 2 * margin, size[1] + 2 * margin), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([margin, margin, margin + size[0], margin + size[1]], outline="black", width=4)
    for x in range(margin + 20, margin + size[0] - 20, 12):
        d.rectangle([x, margin + 40, x + 5, margin + 140], fill="black")
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt,ext", [("PNG", ".png"), ("JPEG", ".jpg"), ("HEIF", ".heic")])
def test_image_becomes_one_label_filling_the_cell(fmt, ext):
    if fmt == "HEIF":
        from pillow_heif import register_heif_opener
        register_heif_opener()
    name, doc = open_source("shot" + ext, data=fake_label_png(fmt=fmt))
    labels = list(core.extract_labels([(name, doc)]))
    assert len(labels) == 1
    out = pymupdf.open()
    lines = []
    core.place(out, labels, 0, fill=False, log=lines.append)
    assert "rotated" in lines[0]          # landscape image turned upright
    assert "(100%" in lines[0]            # fills the cell without --fill
    # white margin was trimmed: the page is about the box's aspect ratio (604x404 + pad)
    r = doc[0].rect
    assert abs(r.width / r.height - 612 / 412) < 0.03


def test_text_label_is_drawn_and_fits():
    out = pymupdf.open()
    core.place(out, [core.TextLabel("FRAGILE"), core.TextLabel("word " * 300)], 0, False, log=lambda *_: None)
    page = out[0]
    words = page.get_text("words")
    assert any(w[4] == "FRAGILE" for w in words)
    for pos, text_bbox in ((0, core.cell_rect(0)), (1, core.cell_rect(1))):
        inside = [w for w in words if pymupdf.Rect(w[:4]).intersects(text_bbox)]
        assert inside
        # nothing spills out of its cell (small tolerance for glyph ascenders)
        assert all(pymupdf.Rect(w[:4]) in text_bbox + (-2, -12, 2, 2) for w in inside)
    # the big word gets a big font
    size = max(s["size"] for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]
               if "FRAGILE" in s["text"])
    assert size >= 40
