"""Extra nasty-but-plausible inputs for the images/hostile review. All data is invented.
Reads (never writes) ../07-images and ../01-single; writes only into this directory.

    uv run python tests/fixtures/synthetic/agent-images/make_agent_images.py
"""
import io, os, struct, zlib
from pathlib import Path
import pymupdf
from PIL import Image, ImageCms, ImageOps, ImageDraw

HERE = Path(__file__).resolve().parent
SYN = HERE.parent
LABEL_PNG = SYN / "07-images/label.png"
LABEL_PDF = SYN / "01-single/evri-standard-4x6.pdf"
Image.MAX_IMAGE_PIXELS = None  # generator only


def label() -> Image.Image:
    return Image.open(LABEL_PNG).convert("RGB")


def out(name):
    return HERE / name


def main():
    L = label()  # 1180x1770-ish
    # --- sizes ---------------------------------------------------------------
    for px, name in [((1, 1), "1x1-black.png"), ((1, 1), "1x1-white.png")]:
        Image.new("RGB", px, "black" if "black" in name else "white").save(out(name))
    Image.new("RGB", (2, 3), "black").save(out("2x3-black.png"))
    # 12000x18000 (216 MP): label centred on white, white margins -> big memory, trims down
    for w, h, name in [(9000, 13000, "big-9000x13000-117mp.png"), (12000, 18000, "big-12000x18000.png"), (20000, 20000, "bomb-20000x20000.png")]:
        big = Image.new("L", (w, h), 255)
        lab = L.convert("L").resize((1180, 1770))
        big.paste(lab, ((w - 1180) // 2, (h - 1770) // 2))
        big.save(out(name), optimize=False, compress_level=1)
    # 12 MP phone-style photo of a label on a desk with sensor noise (PNG-embedding size/time check)
    ph = Image.open(SYN / "07-images/label-photo-on-desk.jpg").convert("RGB").resize((3024, 4032), Image.LANCZOS)
    noise = Image.effect_noise(ph.size, 12).convert("RGB")
    Image.blend(ph, noise, 0.08).save(out("photo-12mp-noisy.jpg"), quality=92)
    # --- JPEG variants ----------------------------------------------------------
    L.save(out("progressive.jpg"), progressive=True, quality=90)
    L.save(out("optimized-baseline.jpg"), optimize=True, quality=90)
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    L.save(out("icc-small.jpg"), icc_profile=srgb, quality=90)
    # ~4 MB padded ICC in a PNG (iCCP chunk); header size field left as-is
    L.save(out("icc-huge-4mb.png"), icc_profile=srgb + os.urandom(4 * 1024 * 1024))
    # 1-bit, grey, 16-bit, palette+transparency, LA, 16bit RGB
    L.convert("1").save(out("mode-1bit.png"))
    L.convert("L").save(out("mode-gray.png"))
    L.convert("L").convert("I;16").save(out("mode-gray16.png"))
    p = L.convert("P", palette=Image.ADAPTIVE, colors=16); p.info["transparency"] = 0; p.save(out("mode-palette-transparent0.png"), transparency=0)
    L.convert("LA").save(out("mode-LA.png"))
    # transparent label: white text-less label where the background pixels have RGB black but alpha 0
    rgba = L.convert("RGBA"); px = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = px[x, y]
            if r > 245 and g > 245 and b > 245:
                px[x, y] = (0, 0, 0, 0)
    rgba.save(out("transparent-black-rgb-under-alpha0.png"))
    # white-on-transparent ("dark mode" export): invisible once flattened onto white
    inv = ImageOps.invert(L).convert("RGBA"); 
    wt = Image.new("RGBA", L.size, (255, 255, 255, 0)); a = ImageOps.invert(L.convert("L")); wt.putalpha(a)
    wt.save(out("white-ink-on-transparent.png"))
    # CMYK TIFF, multi-page PDF-in-TIFF, 16-bit TIFF
    L.convert("CMYK").save(out("cmyk.tiff"))
    # faded/low-contrast scan: darkest ink is only grey 200-215 (washed-out thermal print / weak scanner)
    for k, nm in [(0.15, "faint-ink-15pct.png"), (0.30, "faint-ink-30pct.png"), (0.50, "faint-ink-50pct.png")]:
        L.convert("L").point(lambda v, k=k: round(255 - (255 - v) * k)).convert("RGB").save(out(nm))
    # --- EXIF orientations 1..8, built so a correct decoder shows the label upright ----
    # orientation -> transform that, applied to the upright image, gives the stored pixels
    inverse = {1: None, 2: Image.FLIP_LEFT_RIGHT, 3: Image.ROTATE_180, 4: Image.FLIP_TOP_BOTTOM,
               5: Image.TRANSPOSE, 6: Image.ROTATE_90, 7: Image.TRANSVERSE, 8: Image.ROTATE_270}
    for o in range(1, 9):
        stored = L if inverse[o] is None else L.transpose(inverse[o])
        ex = Image.Exif(); ex[0x0112] = o
        stored.save(out(f"exif-orientation-{o}.jpg"), exif=ex.tobytes(), quality=92)
    # --- damaged images (partial downloads etc.) ----------------------------------
    jb = (SYN / "07-images/label.jpg").read_bytes(); pb = LABEL_PNG.read_bytes()
    out("truncated-60pct.jpg").write_bytes(jb[: int(len(jb) * .6)])
    out("truncated-60pct.png").write_bytes(pb[: int(len(pb) * .6)])
    out("jpeg-with-junk-prefix.jpg").write_bytes(b"\r\n" + jb)
    out("jpeg-with-trailing-junk.jpg").write_bytes(jb + b"\x00" * 5000 + b"junk")
    # bad CRC in IDAT
    bad = bytearray(pb); i = pb.find(b"IDAT") + 20; bad[i] ^= 0xFF; out("png-bad-idat-crc.png").write_bytes(bytes(bad))
    # --- mislabelled extensions (poison-pill candidates for the web queue) --------------
    out("actually-a-jpeg.pdf").write_bytes(jb)                    # distinct bytes from other fixtures
    out("actually-a-png-distinct.pdf").write_bytes(pb + b"\x00")  # PNG + trailing byte (distinct sha)
    out("actually-a-tiff.pdf").write_bytes((SYN / "07-images/label.tiff").read_bytes())
    out("actually-html.pdf").write_bytes(b"<html><body>Your label is attached</body></html>")
    out("actually-a-jpeg.png").write_bytes(jb)                   # JPEG named .png: should just work
    out("actually-a-png.jpg").write_bytes(pb)
    out("actually-a-pdf.jpg").write_bytes(LABEL_PDF.read_bytes())
    # --- names ---------------------------------------------------------------------
    for src, dst in [(LABEL_PDF, ".LABEL.PDF"), (LABEL_PDF, "label.pdf.pdf"), (LABEL_PDF, "label.PdF"),
                     (LABEL_PDF, "trailing-dot.pdf."), (LABEL_PDF, "  spaces around  .pdf"),
                     (LABEL_PDF, "-dash-first.pdf"), (LABEL_PDF, "ansi-\x1b[31mred\x1b[0m.pdf"),
                     (LABEL_PDF, "new\nline.pdf"), (LABEL_PDF, "<img src=x onerror=alert(1)>.pdf"),
                     (LABEL_PDF, "emoji-📦-label.pdf"), (LABEL_PDF, "nfd-étiquette.pdf")]:
        out(dst).write_bytes(src.read_bytes())
    # symlinks (relative), dangling, to a directory, and a loop
    for name, target in [("symlink-to-label.pdf", "../01-single/evri-standard-4x6.pdf"),
                         ("symlink-to-label-image.png", "../07-images/label.png"),
                         ("symlink-dangling.pdf", "../01-single/does-not-exist.pdf"),
                         ("symlink-to-dir.pdf", "../01-single")]:
        p = out(name)
        if p.is_symlink() or p.exists(): p.unlink()
        os.symlink(target, p)
    d = out("a-directory.pdf"); d.mkdir(exist_ok=True)
    # --- PDFs ---------------------------------------------------------------------------
    src = pymupdf.open(LABEL_PDF)
    big = pymupdf.open()
    for _ in range(500):
        big.insert_pdf(src)
    big.save(out("500-pages-one-label-each.pdf"), garbage=3, deflate=True)
    # page 3 of 5 has a corrupt content stream (valid page tree)
    five = pymupdf.open()
    for _ in range(5):
        five.insert_pdf(src)
    five.save(out("_tmp5.pdf")); five = pymupdf.open(out("_tmp5.pdf"))
    cx = five[2].get_contents()[0]
    five.update_stream(cx, b"q 1 0 0 1 0 0 cm BT /F1 12 Tf (broken ET garbage \x00\xff\xfe  <<<< >>> [[[ Q Q Q Q")
    five.save(out("page3-content-stream-garbage.pdf"), garbage=0)
    # page 3 whose /Contents points at a nonexistent object, and a page with a bogus MediaBox
    five = pymupdf.open(out("_tmp5.pdf"))
    five.xref_set_key(five[2].xref, "Contents", "9999 0 R")
    five.save(out("page3-contents-missing-object.pdf"), garbage=0)
    five = pymupdf.open(out("_tmp5.pdf"))
    five.xref_set_key(five[2].xref, "MediaBox", "[0 0 0 0]")
    five.save(out("page3-zero-mediabox.pdf"), garbage=0)
    five = pymupdf.open(out("_tmp5.pdf"))
    five.xref_set_key(five[2].xref, "MediaBox", "[0 0 14400 14400]")
    five.save(out("page3-huge-mediabox.pdf"), garbage=0)
    out("_tmp5.pdf").unlink()
    # damaged tail: PDF with the last 400 bytes (xref/trailer) chopped, recoverable by repair
    pb2 = LABEL_PDF.read_bytes(); out("pdf-xref-chopped-repairable.pdf").write_bytes(pb2[:-300])
    # PDF with leading junk before %PDF (common from email gateways)
    out("pdf-with-leading-junk.pdf").write_bytes(b"\r\n\r\nHTTP/1.1 200 OK\r\n\r\n" + pb2)
    # Hidden layers. The visible label is the 07-images label PNG as an image (so detection sees a label);
    # content inside an OFF layer must NOT appear on the sheet (a viewer hides it).
    def page_with_label(oc=None):
        d = pymupdf.open(); pg = d.new_page(width=283, height=425)
        pg.insert_image(pymupdf.Rect(10, 10, 273, 415), filename=str(LABEL_PNG), oc=oc)
        return d, pg
    # (a) OFF layer carrying a red block + text over the middle of the label
    d, pg = page_with_label()
    off = d.add_ocg("Hidden: DO NOT PRINT", on=False)
    pg.draw_rect(pymupdf.Rect(30, 200, 250, 330), color=(1, 0, 0), fill=(1, 0, 0), oc=off)
    pg.insert_text((40, 270), "HIDDEN LAYER TEXT", fontsize=20, color=(1, 1, 1), oc=off)
    d.save(out("ocg-hidden-red-block-over-label.pdf"))
    # (b) layer ON for viewing but OFF for printing (/Usage /Print /PrintState /OFF)
    d, pg = page_with_label()
    scr = d.add_ocg("Screen only", on=True)
    pg.draw_rect(pymupdf.Rect(30, 200, 250, 330), color=(0, 0, 1), fill=(0, 0, 1), oc=scr)
    pg.insert_text((40, 270), "SCREEN-ONLY NOTE", fontsize=20, color=(1, 1, 1), oc=scr)
    d.xref_set_key(scr, "Usage", "<</Print <</PrintState /OFF>>/View <</ViewState /ON>>>>")
    d.save(out("ocg-print-state-off.pdf"))
    # (c) layer OFF for viewing but ON for printing (a 'print only' crop-mark / return-address layer)
    d, pg = page_with_label()
    po = d.add_ocg("Print only", on=False)
    pg.insert_text((20, 30), "PRINT-ONLY MARK", fontsize=12, color=(0, 0.5, 0), oc=po)
    d.xref_set_key(po, "Usage", "<</Print <</PrintState /ON>>/View <</ViewState /OFF>>>>")
    d.save(out("ocg-print-only-layer.pdf"))
    # (d) the whole label image lives in an OFF layer: a viewer shows a blank page
    d = pymupdf.open(); pg = d.new_page(width=283, height=425)
    o = d.add_ocg("Everything", on=False)
    pg.insert_image(pymupdf.Rect(10, 10, 273, 415), filename=str(LABEL_PNG), oc=o)
    d.save(out("ocg-whole-label-in-off-layer.pdf"))
    # (e) a 3-page PDF: label, text-only page (terms and conditions), label
    d = pymupdf.open(); d.insert_pdf(src); 
    t = d.new_page(width=283, height=425); t.insert_text((30, 60), "Terms and conditions of carriage ...", fontsize=10)
    d.insert_pdf(src)
    d.save(out("3pages-label-textonly-label.pdf"))
    print("done")


if __name__ == "__main__":
    main()
