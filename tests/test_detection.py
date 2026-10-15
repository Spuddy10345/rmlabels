import io

import pymupdf
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
