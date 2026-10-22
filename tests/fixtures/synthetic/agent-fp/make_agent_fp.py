"""Generator for the agent-fp fixtures: more realistic 'label + other stuff on one page' documents.
All data invented. Pages are printed by headless Chrome (Skia PDFs, like many real carrier/marketplace
tools: every table border / barcode bar is its own vector op) then, for a few, post-processed with pymupdf.

    uv run python tests/fixtures/synthetic/agent-fp/make_agent_fp.py

Writes only into this directory (tests/fixtures/synthetic/agent-fp/). Never touches other fixtures.
"""
from __future__ import annotations
import base64, io, json, random, subprocess, sys, tempfile
from pathlib import Path
import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
rng = random.Random(7)
MANIFEST = []

def b64png(w, h, col, text=None):
    im = Image.new("RGB", (w, h), (255, 255, 255)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((2, 2, w - 3, h - 3), 8, fill=col)
    if text: d.text((10, h // 2 - 5), text, fill=(255, 255, 255))
    b = io.BytesIO(); im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()

LOGO = b64png(240, 80, (255, 153, 0), "ACME MARKETPLACE")
SMALL_ICON = b64png(40, 40, (0, 120, 200), "1")

def bars(w_mm, h_mm, n=60):
    x, out = 0, []
    for _ in range(n):
        w = rng.choice((1, 1, 2, 3)); gap = rng.choice((1, 2))
        out.append(f'<rect x="{x}" y="0" width="{w}" height="100"/>'); x += w + gap
    return f'<svg viewBox="0 0 {x} 100" preserveAspectRatio="none" style="width:{w_mm}mm;height:{h_mm}mm;display:block" xmlns="http://www.w3.org/2000/svg">{"".join(out)}</svg>'

def qr(mm, n=21):
    cells = "".join(f'<rect x="{i}" y="{j}" width="1" height="1"/>' for i in range(n) for j in range(n) if rng.random() < .5)
    return f'<svg viewBox="0 0 {n} {n}" style="width:{mm}mm;height:{mm}mm;display:block" xmlns="http://www.w3.org/2000/svg">{cells}</svg>'

def label_div(border=True, w=100, h=150, carrier="Royal Mail", svc="Tracked 48", to="Dr Test Recipient", extra=""):
    b = "1px solid #000" if border else "none"
    return f'''<div style="width:{w}mm;height:{h}mm;box-sizing:border-box;border:{b};padding:4mm;font:11pt Helvetica,Arial,sans-serif;position:relative">
<div style="background:#d22;color:#fff;font-weight:bold;padding:2mm 3mm;display:flex;justify-content:space-between"><span>{carrier}</span><span>{svc}</span></div>
<div style="display:flex;justify-content:space-between;margin-top:3mm"><div><small>DELIVER TO</small><br><b>{to}</b><br>14 Cobblers Lane<br>Nether Wallop<br>HAMPSHIRE<br><span style="font-size:22pt;font-weight:bold">ZZ20 8XX</span></div>{qr(26)}</div>
<div style="margin-top:12mm">{bars(80, 26)}<div style="text-align:center;font-size:8pt">ZZ822798555GB</div></div>
<div style="font-size:6pt;color:#777;margin-top:3mm">TEST LABEL - NOT VALID FOR POSTAGE - INVENTED DATA</div>{extra}</div>'''

def table(rows, cols=("Item", "SKU", "Qty", "Price"), ruled=True, shade_header=True):
    bd = "border:1px solid #444;" if ruled else ""
    th = "".join(f'<th style="{bd}padding:1.5mm;text-align:left;{"background:#eee" if shade_header else ""}">{c}</th>' for c in cols)
    trs = "".join("<tr>" + "".join(f'<td style="{bd}padding:1.5mm">{v}</td>' for v in (f"Sprocket model {i+1}", f"SP-{100+i}", 1 + i % 3, f"£{4+i}.99")) + "</tr>" for i in range(rows))
    return f'<table style="border-collapse:collapse;width:100%;font:10pt Helvetica,Arial,sans-serif"><tr>{th}</tr>{trs}</table>'

def page(body, w="210mm", h="297mm", margin="0"):
    return f'<!doctype html><html><head><meta charset="utf-8"><style>@page{{size:{w} {h};margin:{margin}}}html,body{{margin:0;padding:0}}</style></head><body>{body}</body></html>'

def chrome(html, out: Path):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "p.html"; p.write_text(html)
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={out}", f"file://{p}"],
                       check=True, capture_output=True)

def add(name, html, expect, desc, risk="", w="210mm", h="297mm", margin="0", post=None):
    out = HERE / name
    chrome(page(html, w, h, margin), out)
    if post:
        d = pymupdf.open(out); post(d); d.save(out.with_suffix(".tmp")); d.close(); out.with_suffix(".tmp").replace(out)
    MANIFEST.append(dict(file=name, expect=expect, desc=desc, risk=risk))

NOTE_H = '<div style="padding:6mm 10mm;font:11pt Helvetica,Arial,sans-serif"><h3 style="margin:0 0 3mm">Despatch note</h3>Order 9243565 &middot; Ship date 14/10/2026<br><br>'
def note(rows=5, ruled=True, logo=False):
    return NOTE_H + (f'<img src="{LOGO}" style="height:12mm;float:right">' if logo else "") + table(rows, ruled=ruled) + "<p style='font-size:9pt'>Thank you for your order. Returns within 30 days.</p></div>"

def main():
    # ---- Click & Drop style: A4 = label top-left, despatch note below --------------------------------
    for fr in (False, True):
        for ruled in (False, True):
            add(f"cd-{'framed' if fr else 'unframed'}-note-{'ruled' if ruled else 'plain'}.pdf",
                label_div(border=fr) + note(5, ruled), 1,
                f"C&D integrated A4: 100x150 label top-left ({'with' if fr else 'no'} border), despatch note table ({'ruled' if ruled else 'unruled'}) below",
                "despatch note below label")
    add("cd-framed-note-ruled-logo.pdf", label_div(True) + note(8, True, logo=True), 1,
        "C&D style: framed label, then despatch note with logo image + ruled 8-row table", "logo + table")
    add("cd-framed-note-right.pdf", f'<div style="display:flex">{label_div(True)}<div style="width:110mm">{note(6, True, True)}</div></div>', 1,
        "framed label left, despatch note (logo + ruled table) to the right", "note beside label")

    # ---- Marketplace packing slips (NOT labels) ---------------------------------------------------
    add("ps-amazon-logo-ruled-orderbarcode.pdf",
        f'<div style="padding:12mm;font:10pt Helvetica,Arial,sans-serif"><img src="{LOGO}" style="height:14mm"><h2>Packing slip</h2>Order #123-4567890-1234567<br>{bars(60,12,40)}<br>{table(9)}<p>Returning an item? Visit your orders.</p></div>',
        0, "Amazon-like packing slip: logo, ruled item table, order barcode (a genuine vector barcode, but not a label)", "slip with a barcode")
    add("ps-ebay-logo-plain.pdf",
        f'<div style="padding:12mm;font:10pt Helvetica,Arial,sans-serif"><img src="{LOGO}" style="height:14mm"><h2>Order summary</h2>{table(4, ruled=False)}</div>', 0,
        "eBay-like packing slip: logo + unruled table", "logo")
    add("ps-etsy-plain-icon.pdf",
        f'<div style="padding:12mm;font:10pt Helvetica,Arial,sans-serif"><h2>Thanks for your order <img src="{SMALL_ICON}" style="height:6mm"></h2>{table(3, ruled=False)}</div>', 0,
        "Etsy-like packing slip: a tiny 6 mm heart/icon image, unruled table", "tiny image")
    add("ps-spaced-rows-rules-only.pdf",
        '<div style="padding:12mm;font:10pt Helvetica,Arial,sans-serif"><h2>Receipt</h2>' + "".join(f'<div style="border-bottom:1px solid #ccc;padding:2mm 0">Item {i} .... £{i}.00</div>' for i in range(16)) + '</div>', 0,
        "receipt with a hairline under each of 16 lines (no table)", "16 rules")

    # ---- Returns labels with instructions ---------------------------------------------------------
    STEPS = '<ol style="font:10pt Helvetica,Arial,sans-serif;width:150mm">' + "".join(f"<li style='margin:3mm 0'>Step {i}: pack the item securely and attach this label.</li>" for i in range(1, 6)) + "</ol>"
    add("ret-evri-framed-label-text-instructions.pdf",
        f'<div style="display:flex;gap:6mm;padding:8mm">{label_div(True, carrier="Evri", svc="Returns")}<div style="font:10pt Helvetica,Arial,sans-serif;width:80mm"><h3>How to return</h3>{STEPS.replace("150mm","75mm")}</div></div>', 1,
        "Evri-like returns: bordered label left, text-only instructions right", "instruction text")
    add("ret-rm-framed-label-instructions-with-icons.pdf",
        f'<div style="padding:8mm">{label_div(True, svc="Returns")}<div style="font:10pt Helvetica,Arial,sans-serif;margin-top:6mm">' +
        "".join(f'<div style="display:flex;align-items:center;gap:4mm;margin:3mm 0"><img src="{SMALL_ICON}" style="height:9mm">Step {i}: do the thing and keep your receipt.</div>' for i in range(1, 5)) + '</div></div>', 1,
        "Royal Mail-like returns: bordered label with 4 small numbered icon images in the instructions below", "icons outside the frame")
    add("ret-rm-framed-label-instructions-text-only.pdf",
        f'<div style="padding:8mm">{label_div(True, svc="Returns")}{STEPS}</div>', 1,
        "bordered label with text-only instructions below", "instruction text below")

    # ---- Parcel2Go-like: label box + side panels + certificate of posting with a ruled table + logo -------
    P2G_CERT = f'''<div style="margin-top:8mm;border:1px solid #000;padding:5mm;font:10pt Helvetica,Arial,sans-serif;width:180mm"><img src="{LOGO}" style="height:10mm;float:right">
    <h3 style="margin:0">Certificate of Posting</h3>{table(2, cols=("Service","Tracking","Weight","Date"))}<p style="font-size:8pt">Keep this certificate as proof of posting.</p></div>'''
    add("p2g-label-instructions-cert-ruled.pdf",
        f'<div style="padding:10mm"><div style="display:flex;gap:6mm">{label_div(True, carrier="Parcel2Go", svc="Royal Mail 48")}<div style="font:9pt Helvetica,Arial,sans-serif;width:70mm"><b>Instructions</b><br>Stick the label on the largest side.<br>Do not fold.</div></div>{P2G_CERT}</div>', 1,
        "Parcel2Go-like A4: bordered label + instruction panel + certificate of posting (logo repeated, 2-row ruled table)", "ruled certificate")
    add("p2g-label-instructions-cert-plain.pdf",
        f'<div style="padding:10mm"><div style="display:flex;gap:6mm">{label_div(True, carrier="Parcel2Go", svc="Royal Mail 48")}<div style="font:9pt Helvetica,Arial,sans-serif;width:70mm"><b>Instructions</b><br>Stick the label on the largest side.</div></div><div style="margin-top:8mm;font:10pt Helvetica,Arial,sans-serif"><b>Certificate of Posting</b><br>Service: Royal Mail 48<br>Date: 14/10/2026</div></div>', 1,
        "Parcel2Go-like A4 with an unruled, image-free certificate of posting", "text below frame")

    # ---- Customs forms (realistic: many ruled boxes) ---------------------------------------------------
    def cn22(w="95mm"):
        boxes = "".join(f'<div style="display:inline-block;width:4mm;height:4mm;border:1px solid #000;margin-right:2mm"></div>{t} &nbsp;' for t in ("Gift", "Documents", "Sale of goods", "Commercial sample", "Returned goods", "Other"))
        tbl = table(2, cols=("Description of contents", "Qty", "Weight (kg)", "Value (£)"))
        return f'<div style="width:{w};border:1.5px solid #000;padding:3mm;font:8pt Helvetica,Arial,sans-serif"><b style="font-size:12pt">CUSTOMS DECLARATION CN22</b><br><br>{boxes}<br><br>{tbl}<br><div style="border:1px solid #000;height:10mm"></div><small>Signature / date</small></div>'
    add("cn22-realistic-alone-a6.pdf", cn22("100mm"), None, "A CN22 alone, realistic ruled boxes (checkboxes + 2-row table + signature box)", "form with >15 vector ops", w="105mm", h="148mm", margin="2mm")
    add("cd-label-plus-realistic-cn22.pdf", label_div(True, svc="Intl Tracked") + '<div style="padding:4mm 8mm">' + cn22() + '</div>', 1,
        "A4: bordered label top-left, realistic CN22 below", "customs form below label")
    add("cd-unframed-label-plus-realistic-cn22.pdf", label_div(False, svc="Intl Tracked") + '<div style="padding:4mm 8mm">' + cn22() + '</div>', 1,
        "A4: unbordered label top-left, realistic CN22 below", "customs form below label")

    # ---- Mixed multi-page docs -------------------------------------------------------------------------
    def multipage(name, pages, expect, desc, risk):
        """pages = list of HTML bodies, each printed on its own 4x6 or A4 page via CSS page breaks."""
        html = "".join(f'<div style="page-break-after:always;height:100vh;overflow:hidden">{p}</div>' for p in pages)
        add(name, html, expect, desc, risk, w="210mm", h="297mm")

    multipage("mp-label-invoice-cn22-label-a4.pdf",
              [label_div(True, to="Alice First"), f'<div style="padding:10mm">{note(8, True, True)}</div>', '<div style="padding:10mm">' + cn22("150mm") + '</div>', label_div(True, to="Bob Second")], 2,
              "4 A4 pages: label, ruled invoice+logo, CN22, label", "invoice and CN22 pages, labels at A4")
    multipage("mp-label-plain-invoice-label-a4.pdf",
              [label_div(True, to="Alice First"), f'<div style="padding:10mm">{note(8, False, False)}</div>', label_div(True, to="Bob Second")], 2,
              "3 A4 pages: label, text-only invoice, label", "control")

    (HERE / "manifest.json").write_text(json.dumps(MANIFEST, indent=1))
    print(len(MANIFEST), "fixtures written to", HERE)


if __name__ == '__main__':
    main()
