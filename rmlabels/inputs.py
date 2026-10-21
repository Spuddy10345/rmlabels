"""Turn any supported file into something the engine can place.

PDFs go through unchanged. Images (PNG/JPG/HEIC/…) have their white margins
trimmed and become a one-page PDF in memory, sized so the picture fills a
label: an image has no real-world size, so there is nothing to preserve.
"""
from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from .core import cell_rect

PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
SUPPORTED = PDF_EXT | IMAGE_EXT
ACCEPT = ",".join(sorted(SUPPORTED)) + ",application/pdf,image/*"  # for <input accept=…>

_heif_registered = False


def is_supported(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED


def image_to_pdf(data: bytes) -> pymupdf.Document:
    global _heif_registered
    if not _heif_registered:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        _heif_registered = True

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, "white")
        flat.paste(img, mask=img.getchannel("A"))
        img = flat
    img = img.convert("RGB")

    # trim near-white margins (scans are off-white, so not just pure 255)
    bbox = img.convert("L").point(lambda v: 255 if v < 235 else 0).getbbox()
    if bbox:
        pad = round(0.01 * max(img.size))
        x0, y0, x1, y1 = bbox
        img = img.crop((max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)))

    buf = io.BytesIO()
    img.save(buf, "PNG")
    w, h = img.size
    cell = cell_rect(0)
    # the engine turns landscape labels upright, so fit the portrait orientation
    k = min(cell.width / min(w, h), cell.height / max(w, h))
    doc = pymupdf.open()
    page = doc.new_page(width=w * k, height=h * k)
    page.insert_image(page.rect, stream=buf.getvalue())
    return doc


def open_source(name: str, path: Path | None = None, data: bytes | None = None):
    """(name, document) for a PDF or image, from a path or raw bytes.

    What the file *contains* decides how it is read, not its extension: a PNG saved as .pdf
    (or a PDF saved as .png) still works. Anything unreadable raises ValueError with a
    message fit to show the user."""
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"unsupported file type: {name}")
    if data is None:
        data = Path(path).read_bytes()
    if not data:
        raise ValueError("the file is empty")
    try:
        if b"%PDF-" in data[:1024]:
            doc = pymupdf.open(stream=data, filetype="pdf")
            if doc.needs_pass:
                raise ValueError("the PDF is password-protected")
            if not doc.page_count:
                raise ValueError("the PDF has no pages")
        else:
            doc = image_to_pdf(data)
    except ValueError:
        raise
    except Image.DecompressionBombError as e:
        raise ValueError("the image is far too large to be a label") from e
    except Exception as e:
        raise ValueError("it isn't a readable PDF or image") from e
    return name, doc
