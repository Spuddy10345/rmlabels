import io

import pymupdf
import pytest
from PIL import Image, ImageDraw

from rmlabels import core


def png(image):
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


def test_non_decorative_image_sharing_overlay_bounds_is_retained():
    doc = pymupdf.open()
    page = doc.new_page()
    scan = Image.new("RGB", (600, 850), "white")
    draw = ImageDraw.Draw(scan)
    for x in range(50, 550, 12):
        draw.rectangle((x, 150, x + 5, 300), fill="black")
    page.insert_image(page.rect, stream=png(scan))

    overlay = Image.new("RGBA", scan.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).line((0, 0, 20, 20), fill=(0, 0, 0, 255), width=2)
    page.insert_image(page.rect, stream=png(overlay))

    assert core.decorative_images(page)
    images = [(kind, rect) for kind, rect in core.page_items(page) if "image" in kind]
    assert len(images) == 2
    assert len(list(core.extract_labels([("shared.pdf", doc)]))) == 1


def test_sparse_unmasked_raster_label_is_not_decorative():
    doc = pymupdf.open()
    page = doc.new_page()
    sparse = Image.new("RGB", (600, 850), "white")
    ImageDraw.Draw(sparse).rectangle((290, 420, 309, 439), fill="black")
    page.insert_image(page.rect, stream=png(sparse))

    assert page.get_images(full=True)[0][1] == 0
    assert core.decorative_images(page) == []
    assert len(list(core.extract_labels([("sparse.pdf", doc)]))) == 1


def test_ink_fraction_counts_only_dark_opaque_pixels():
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(100):
        for x in range(100):
            if y * 100 + x < 190:
                pixels[x, y] = (255, 255, 255, 255)
            elif y * 100 + x < 200:
                pixels[x, y] = (0, 0, 0, 255)

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=png(image))
    xref, smask = page.get_images(full=True)[0][:2]

    assert core.ink_fraction(doc, xref, smask) == 0.001


def fedex_style_page():
    """A 4x6in thermal label as FedEx prints it: one 200 dpi 1-bit picture filling the whole page,
    with a barcode that runs right to the bottom edge."""
    img = Image.new("1", (800, 1200), 1)
    draw = ImageDraw.Draw(img)
    draw.rectangle((30, 20, 400, 110), fill=0)                  # origin block (stands in for text)
    draw.line((0, 150, 800, 150), fill=0, width=3)
    draw.rectangle((40, 440, 600, 620), fill=0)                 # the dense 2D barcode
    draw.line((0, 700, 800, 700), fill=0, width=3)
    for x in range(70, 730, 14):
        draw.rectangle((x, 990, x + 6, 1199), fill=0)           # tracking barcode, cut by the page edge
    doc = pymupdf.open()
    doc.new_page(width=288, height=432).insert_image(pymupdf.Rect(0, 0, 288, 432), stream=png(img))
    return doc


def test_fedex_style_raster_labels_are_placed_whole_one_per_page():
    doc = fedex_style_page()
    doc.insert_pdf(fedex_style_page())
    labels = list(core.extract_labels([("fedex.pdf", doc)]))
    assert [tag for *_, tag in labels] == ["fedex.pdf p1", "fedex.pdf p2"]  # one per page, no splitting
    assert all(clip == doc[0].rect for _, _, clip, _ in labels)             # nothing trimmed off the picture

    out = pymupdf.open()
    core.place(out, labels, 0, False, log=lambda *_: None)
    assert len(out) == 1
    for pos in (0, 1):
        cell = core.cell_rect(pos)
        scale = min(cell.width / 288, cell.height / 432)  # a 4x6in label needs shrinking to fit a 99x139mm cell
        shown = out[0].get_pixmap(dpi=144, clip=cell)
        ink = pymupdf.Pixmap(pymupdf.csGRAY, shown).samples
        rows = [y for y in range(shown.h) if 0 in ink[y * shown.w:(y + 1) * shown.w]]
        # ink runs from the origin block at the top to the barcode at the bottom, all of it shrunk together
        assert (rows[-1] - rows[0] + 1) / 2 == pytest.approx(432 * scale * (1199 - 20) / 1200, abs=2)
    for pos in (2, 3):
        assert 0 not in pymupdf.Pixmap(pymupdf.csGRAY, out[0].get_pixmap(dpi=72, clip=core.cell_rect(pos))).samples
