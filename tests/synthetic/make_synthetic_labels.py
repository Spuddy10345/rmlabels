"""Synthetic shipping labels for stress-testing rmlabels' detection.

    uv run --with zxing-cpp --with fonttools --with numpy python tests/synthetic/make_synthetic_labels.py

Writes tests/fixtures/synthetic/<category>/* and a manifest.json. Everything is invented:
the names, addresses and tracking numbers are fiction, the carrier branding is plain text
(no logos), and every label says TEST LABEL in its footer. They imitate the *shape* of real
carrier output (barcode types, layouts, page formats, the odd quirk), not the real thing.

manifest.json records, per file, what a human would want from the tool:
  expect  number of labels that should be found (null = genuinely ambiguous)
  risk    why the file might trip detection
"""
from __future__ import annotations

import io
import json
import random
import shutil
import sys
from pathlib import Path

import numpy as np
import pymupdf
import zxingcpp
from PIL import Image, ImageDraw, ImageFilter, ImageOps

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
MM = 72 / 25.4
UNI = "/Library/Fonts/Arial Unicode.ttf"
FOOT = "TEST LABEL - NOT VALID FOR POSTAGE - INVENTED DATA"
rng = random.Random(20261014)
MANIFEST: list[dict] = []
pymupdf.TOOLS.set_aa_level(0)  # crisp barcode rasters
_uni_font = pymupdf.Font(fontfile=UNI) if Path(UNI).exists() else None


def c(r, g, b):
    return (r / 255, g / 255, b / 255)


BLACK, WHITE, GREY = (0, 0, 0), (1, 1, 1), c(110, 110, 110)

# ── people ────────────────────────────────────────────────────────────────────
# (name, address lines, big postcode/routing text, country)
UK = [
    ("Prof. Bartholomew Quibblesnort", ["Flat 3, The Old Tannery", "14 Cobblers Lane", "Nether Wallop", "HAMPSHIRE"], "ZZ20 8XX", "UNITED KINGDOM"),
    ("Dr Ximena Vanterpool-Oyelaran", ["Unit 9, Bumblebee Business Park", "Marmalade Road", "WOLVERHAMPTON"], "ZZ1 4QQ", "UNITED KINGDOM"),
    ("Captain Marmaduke Fizzwick", ["The Crooked Lighthouse", "Gull Point", "Little Snoring", "NORFOLK"], "ZZ21 0AB", "UNITED KINGDOM"),
    ("Siobhán Ní Chonchúir", ["2 Hobnob Mews", "Cathays", "CARDIFF"], "ZZ10 3PF", "UNITED KINGDOM"),
    ("Left Hand Tea Co. (Goods In)", ["Warehouse C, Teapot Wharf", "Gas Street", "BIRMINGHAM"], "ZZ1 2HX", "UNITED KINGDOM"),
    ("Mx Pickle O'Hare-Smythe", ["Rose Cottage", "The Green", "Much Binding-in-the-Marsh", "GLOUCESTERSHIRE"], "ZZ55 6RT", "UNITED KINGDOM"),
]
US = [
    ("Prunella Featherstonehaugh", ["88 Wombat Hollow Rd Apt 5B", "PORTLAND OR"], "97201-1234", "UNITED STATES"),
    ("Absolutely Nothing LLC", ["Attn: Receiving Dock 4", "1200 Industrial Pkwy Ste 300", "COLUMBUS OH"], "43215-0001", "UNITED STATES"),
    ("Dave's Left Sock Warehouse", ["PO Box 4477", "ALBUQUERQUE NM"], "87101-4477", "UNITED STATES"),
]
EU = [
    ("Frau Gudrun Schnitzelbaum", ["Zum Goldenen Pfau 7a", "München"], "80331", "DEUTSCHLAND"),
    ("Monsieur Aristide Bouillabaisse", ["12 Rue des Escargots Bleus", "Bât. C, 3e étage", "Lyon"], "69002", "FRANCE"),
    ("Mevrouw Wilhelmina van der Stroopwafel", ["Kaasplein 4 bis", "Gouda"], "2801 AB", "NEDERLAND"),
    ("Don Quijote de la Mancha Pérez", ["Calle del Molino de Viento, 5 - 2ºB", "Valencia"], "46001", "ESPAÑA"),
    ("Łukasz Wiśniewski-Kowalczyk", ["ul. Żółwia 12/4", "Kraków"], "30-001", "POLSKA"),
    ("Björn Þórðarson", ["Hverfisgata 2b", "Reykjavík"], "101", "ÍSLAND"),
]
WORLD = [
    ("田中 太郎 様", ["東京都千代田区ぽんぽこ町1-2-3", "メゾン桜 405号室"], "100-0001", "日本 JAPAN"),
    ("Иван Петров Сидоров", ["ул. Витоша 15, ет. 3, ап. 9", "София"], "1000", "БЪЛГАРИЯ"),
    ("דנה כהן-לוי", ["רחוב הצבר 7 דירה 2", "תל אביב"], "6100000", "ישראל ISRAEL"),
    ("Matilda Waltzing-Bluegum", ["Lot 7 Dingo Creek Rd", "BLACKHEATH NSW"], "2785", "AUSTRALIA"),
    ("Ms. Maple Syrupson", ["4455 Rue Moose, Unité 6", "MONTRÉAL QC"], "H2X 1Y4", "CANADA"),
    ("Nguyễn Thị Phượng Hoàng", ["Số 9, Ngõ 12 Phố Hàng Bột", "Hà Nội"], "100000", "VIỆT NAM"),
]
EVIL = [  # shapes that stress layout rather than detection
    ("A Very Long Business Name Trading As Another Very Long Name (Procurement Dept) Ltd", ["Building 12, Level 3, Suite 301B, Triple-Barrelled Industrial Estate and Logistics Park Extension",
        "Care of The Receptionist On Duty Between 9am and 5pm Mon-Fri", "Line three has a lot of words in it", "Line four", "Line five", "Line six", "Line seven", "MIDDLE-OF-NOWHERE"], "ZZ99 9ZZ", "UNITED KINGDOM"),
    ("X", ["1 A St"], "ZZ1 1AA", "UK"),
]
SENDERS = ["Wobbly Wheel Bicycle Emporium, 3 Penny Farthing Way, ZZ3 7WW", "Mrs Eglantine Pumpernickel, 9 Brioche Gardens, ZZ12 5BB",
           "Left Hand Tea Co, Teapot Wharf, ZZ1 2HX", "Acme Anvil Returns Dept, 1 Coyote Crescent, ZZ4 4AC"]


def digits(n):
    return "".join(rng.choice("0123456789") for _ in range(n))


# ── drawing helpers ───────────────────────────────────────────────────────────
def font_for(s, bold):
    return "uni" if any(ord(ch) > 0xFF for ch in s) else ("hebo" if bold else "helv")


def tw(s, size, bold=False):
    f = font_for(s, bold)
    return _uni_font.text_length(s, size) if f == "uni" else pymupdf.get_text_length(s, fontname=f, fontsize=size)


def T(page, x, y, s, size=10, bold=False, color=BLACK, align="l", maxw=None):
    """Baseline text, shrunk to maxw if given. Hebrew is reversed into visual order."""
    if any("֐" <= ch <= "׿" for ch in s):
        s = s[::-1]
    w = tw(s, size, bold)
    if maxw and w > maxw:
        size *= maxw / w
        w = maxw
    x -= w / 2 if align == "c" else (w if align == "r" else 0)
    if font_for(s, bold) == "uni" and not getattr(page, "_uni", False):
        page.insert_font(fontname="uni", fontfile=UNI)
        page._uni = True
    page.insert_text((x, y), s, fontsize=size, fontname=font_for(s, bold), color=color)
    return w


LINEAR = {"Code128", "Code39", "ITF", "EAN13"}


def barcode_doc(fmt, data, compound=False):
    """One-page PDF of the barcode. Default: one little rectangle per bar/run, which is how most
    carrier PDFs draw them. compound=True: zxing's SVG, i.e. the whole code as a single path."""
    barcode_ = zxingcpp.create_barcode(data, getattr(zxingcpp.BarcodeFormat, fmt))
    if compound:
        d = pymupdf.open(stream=barcode_.to_svg().encode(), filetype="svg")
        return pymupdf.open("pdf", d.convert_to_pdf())
    grid = zxingcpp.write_barcode_to_image(barcode_, scale=1, add_quiet_zones=False, add_hrt=False)
    rows = [bytes(r) for r in np.array(grid)]
    h = len(rows) if fmt not in LINEAR else max(8, len(rows[0]) // 4)
    doc = pymupdf.open()
    pg = doc.new_page(width=len(rows[0]), height=h)
    for y, row in enumerate(rows if fmt not in LINEAR else rows[:1]):
        x = 0
        while x < len(row):
            if row[x] == 0:
                x1 = x
                while x1 < len(row) and row[x1] == 0:
                    x1 += 1
                top, bot = (y, y + 1) if fmt not in LINEAR else (0, h)
                pg.draw_rect(pymupdf.Rect(x, top, x1, bot), color=None, fill=BLACK)
                x = x1
            else:
                x += 1
    return doc


def barcode(page, rect, fmt, data, mode="vector", hrt=None, hrt_size=8, quiet=True):
    """Draw a barcode into rect. mode: vector (a rect per bar) | compound (one path) | png | jpeg (~300 dpi)."""
    r = pymupdf.Rect(rect)
    linear = fmt in LINEAR
    if linear and hrt:
        T(page, (r.x0 + r.x1) / 2, r.y1 - 1, hrt, hrt_size, align="c", maxw=r.width)
        r.y1 -= hrt_size + 3
    if not linear:
        side = min(r.width, r.height)
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        r = pymupdf.Rect(cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
    d = barcode_doc(fmt, data, compound=mode == "compound")
    if mode in ("vector", "compound"):
        page.show_pdf_page(r, d, 0, keep_proportion=not linear)
    else:
        sx, sy = r.width * 300 / 72 / d[0].rect.width, r.height * 300 / 72 / d[0].rect.height
        pix = d[0].get_pixmap(matrix=pymupdf.Matrix(sx, sy), alpha=False)
        page.insert_image(r, stream=pix.tobytes("jpeg" if mode == "jpeg" else "png"), keep_proportion=False)


def hr(page, x0, x1, y, w=1.0, color=BLACK):
    page.draw_line((x0, y), (x1, y), color=color, width=w)


def person(group, i):
    return group[i % len(group)]


# ── label styles ──────────────────────────────────────────────────────────────
# p: brand, service, color, to=(name, lines, pc, country), track, bars=[(fmt,data,role)], mode,
#    banner, letter, frame, weight, ref, sender
def common_footer(pg, b, s):
    T(pg, (b.x0 + b.x1) / 2, b.y1 - 4 * s, FOOT, 5 * s, color=GREY, align="c", maxw=b.width - 10)


def role(p, r):
    return [x for x in p["bars"] if x[2] == r]


def draw_uk(pg, b, p):
    s = b.width / 283.5
    x0, x1 = b.x0 + 10 * s, b.x1 - 10 * s
    pg.draw_rect(pymupdf.Rect(b.x0, b.y0, b.x1, b.y0 + 26 * s), color=None, fill=p["color"])
    T(pg, x0, b.y0 + 18 * s, p["brand"], 17 * s, True, WHITE)
    T(pg, x1, b.y0 + 16 * s, p["service"], 9 * s, True, WHITE, "r")
    y = b.y0 + 42 * s
    name, lines, pc, country = p["to"]
    twod = role(p, "2d")
    right = x1 - (80 * s if twod else 0)
    T(pg, x0, y, "DELIVER TO", 6.5 * s, color=GREY)
    y += 14 * s
    T(pg, x0, y, name, 14 * s, True, maxw=right - x0)
    for ln in lines:
        y += 13 * s
        T(pg, x0, y, ln, 10.5 * s, maxw=right - x0)
    y += 30 * s
    T(pg, x0, y, pc, 26 * s, True, maxw=right - x0)
    y += 12 * s
    T(pg, x0, y, country, 8 * s, True)
    if twod:
        fmt, data, _ = twod[0]
        barcode(pg, (x1 - 74 * s, b.y0 + 34 * s, x1, b.y0 + 108 * s), fmt, data, p["mode"])
    y = max(y, b.y0 + 120 * s) + 14 * s
    if p.get("banner"):
        pg.draw_rect(pymupdf.Rect(x0, y - 9 * s, x0 + 130 * s, y + 8 * s), color=None, fill=BLACK)
        T(pg, x0 + 6 * s, y + 3 * s, p["banner"], 9 * s, True, WHITE)
        y += 18 * s
    T(pg, x0, y, f"From: {p['sender']}", 6.5 * s, maxw=x1 - x0)
    y += 10 * s
    T(pg, x0, y, f"Weight: {p['weight']}   Ref: {p['ref']}   Date: 14/10/2026", 6.5 * s, color=GREY)
    for fmt, data, _ in role(p, "route"):
        barcode(pg, (x0, y + 8 * s, x1, y + 50 * s), fmt, data, p["mode"], hrt=data)
        y += 56 * s
    for fmt, data, _ in role(p, "track")[:1]:
        top = max(y + 10 * s, b.y1 - 100 * s)
        barcode(pg, (x0, top, x1, b.y1 - 14 * s), fmt, data, p["mode"], hrt=p["track"], hrt_size=10 * s)
    common_footer(pg, b, s)


def draw_us(pg, b, p):
    s = b.width / 283.5
    x0, x1 = b.x0 + 8 * s, b.x1 - 8 * s
    y = b.y0 + 12 * s
    T(pg, x0, y + 6 * s, p["brand"], 12 * s, True, p["color"])
    for i, ln in enumerate([p["sender"].split(",")[0], "Ship date: 14OCT26", f"Wt: {p['weight']}", f"Ref: {p['ref']}"]):
        T(pg, x0, y + (22 + i * 10) * s, ln, 7.5 * s)
    box = pymupdf.Rect(x1 - 62 * s, y - 2 * s, x1, y + 60 * s)
    pg.draw_rect(box, color=None, fill=BLACK)
    T(pg, (box.x0 + box.x1) / 2, box.y0 + 46 * s, p.get("letter", "P"), 46 * s, True, WHITE, "c")
    T(pg, x1, y + 72 * s, p["service"], 8 * s, True, align="r")
    y += 80 * s
    hr(pg, b.x0, b.x1, y, 3 * s)
    y += 10 * s
    left2d = role(p, "2d")
    xa = x0 + (84 * s if left2d else 0)
    if left2d:
        fmt, data, _ = left2d[0]
        barcode(pg, (x0, y + 4 * s, x0 + 78 * s, y + 82 * s), fmt, data, p["mode"])
    name, lines, pc, country = p["to"]
    T(pg, xa, y + 10 * s, "SHIP TO:", 8 * s, True)
    T(pg, xa + 8 * s, y + 26 * s, name, 12 * s, True, maxw=x1 - xa - 8 * s)
    yy = y + 26 * s
    for ln in lines:
        yy += 13 * s
        T(pg, xa + 8 * s, yy, ln, 11 * s, True, maxw=x1 - xa - 8 * s)
    yy += 14 * s
    T(pg, xa + 8 * s, yy, f"{pc}  {country}", 11 * s, True, maxw=x1 - xa - 8 * s)
    y = max(yy + 10 * s, y + 90 * s)
    hr(pg, b.x0, b.x1, y, 2 * s)
    for fmt, data, _ in role(p, "route"):
        barcode(pg, (x0 + 10 * s, y + 8 * s, x1 - 10 * s, y + 70 * s), fmt, data, p["mode"], hrt=data if fmt in LINEAR else None)
        y += 76 * s
        hr(pg, b.x0, b.x1, y, 3 * s)
    if p.get("banner"):
        T(pg, (b.x0 + b.x1) / 2, y + 16 * s, p["banner"], 14 * s, True, align="c")
        y += 24 * s
    for fmt, data, _ in role(p, "track")[:1]:
        T(pg, (b.x0 + b.x1) / 2, y + 14 * s, f"{p['brand']} TRACKING #", 8 * s, True, align="c")
        barcode(pg, (x0, y + 20 * s, x1, min(b.y1 - 16 * s, y + 110 * s)), fmt, data, p["mode"], hrt=p["track"], hrt_size=11 * s)
    common_footer(pg, b, s)


def draw_eu(pg, b, p):
    s = b.width / 283.5
    x0, x1 = b.x0 + 10 * s, b.x1 - 10 * s
    y = b.y0 + 12 * s
    T(pg, x0, y + 16 * s, p["brand"], 20 * s, True, p["color"])
    name, lines, pc, country = p["to"]
    box = pymupdf.Rect(x1 - 90 * s, y - 2 * s, x1, y + 28 * s)
    pg.draw_rect(box, color=None, fill=BLACK)
    T(pg, (box.x0 + box.x1) / 2, box.y0 + 22 * s, pc, 18 * s, True, WHITE, "c", maxw=box.width - 6)
    y += 40 * s
    twod = role(p, "2d")
    if twod:
        fmt, data, _ = twod[0]
        barcode(pg, (x0, y, x0 + 76 * s, y + 76 * s), fmt, data, p["mode"])
    for i, ln in enumerate([p["service"], f"Gewicht/Poids/Weight: {p['weight']}", f"Ref: {p['ref']}", f"Sendung: {p['track']}"]):
        T(pg, x0 + 86 * s, y + (14 + i * 14) * s, ln, 8.5 * s, i == 0, maxw=x1 - x0 - 86 * s)
    y += 96 * s
    hr(pg, x0, x1, y, 1.5 * s)
    y += 20 * s
    T(pg, x0, y, name, 15 * s, True, maxw=x1 - x0)
    for ln in lines:
        y += 15 * s
        T(pg, x0, y, ln, 12 * s, maxw=x1 - x0)
    y += 18 * s
    T(pg, x0, y, f"{pc}  {country}", 14 * s, True, maxw=x1 - x0)
    y += 12 * s
    if p.get("banner"):
        T(pg, x0, y + 8 * s, p["banner"], 9 * s, True, p["color"])
    for fmt, data, _ in role(p, "track")[:1]:
        barcode(pg, (x0, max(y + 20 * s, b.y1 - 120 * s), x1, b.y1 - 14 * s), fmt, data, p["mode"], hrt=p["track"], hrt_size=11 * s)
    common_footer(pg, b, s)


def draw_waybill(pg, b, p):
    s = b.width / 283.5
    x0, x1 = b.x0 + 8 * s, b.x1 - 8 * s
    pg.draw_rect(pymupdf.Rect(b.x0, b.y0, b.x1, b.y0 + 34 * s), color=None, fill=p["color"])
    T(pg, x0, b.y0 + 24 * s, p["brand"], 22 * s, True, c(212, 5, 17))
    T(pg, x1, b.y0 + 22 * s, p["service"], 9 * s, True, c(212, 5, 17), "r")
    y = b.y0 + 44 * s
    pg.draw_rect(pymupdf.Rect(x0, y, x1, y + 56 * s), color=BLACK, width=1)
    T(pg, x0 + 4 * s, y + 10 * s, "From (Shipper):", 6.5 * s, color=GREY)
    for i, ln in enumerate(p["sender"].split(", ")[:4]):
        T(pg, x0 + 4 * s, y + (22 + 11 * i) * s, ln, 8.5 * s)
    y += 56 * s
    name, lines, pc, country = p["to"]
    pg.draw_rect(pymupdf.Rect(x0, y, x1, y + 96 * s), color=BLACK, width=1)
    T(pg, x0 + 4 * s, y + 10 * s, "To (Receiver):", 6.5 * s, color=GREY)
    T(pg, x0 + 4 * s, y + 26 * s, name, 12 * s, True, maxw=x1 - x0 - 8)
    for i, ln in enumerate(lines[:4]):
        T(pg, x0 + 4 * s, y + (40 + 12 * i) * s, ln, 10 * s, maxw=x1 - x0 - 8)
    T(pg, x0 + 4 * s, y + 92 * s, f"{pc} {country}", 9 * s, True, maxw=x1 - x0 - 8)
    y += 96 * s
    for i, (k, v) in enumerate([("Pieces", "1/1"), ("Weight", p["weight"]), ("Contents", "Goods"), ("Ref", p["ref"])]):  # grid of cells
        cx = x0 + i * (x1 - x0) / 4
        pg.draw_rect(pymupdf.Rect(cx, y, cx + (x1 - x0) / 4, y + 26 * s), color=BLACK, width=0.8)
        T(pg, cx + 3, y + 8 * s, k, 6 * s, color=GREY)
        T(pg, cx + 3, y + 20 * s, v, 9 * s, True, maxw=(x1 - x0) / 4 - 6)
    y += 36 * s
    for i, dest in enumerate(["ORIG", pc[:3].upper()]):
        pg.draw_rect(pymupdf.Rect(x0 + i * 120 * s, y, x0 + i * 120 * s + 110 * s, y + 40 * s), color=None, fill=BLACK)
        T(pg, x0 + i * 120 * s + 55 * s, y + 31 * s, dest, 28 * s, True, WHITE, "c")
    twod = role(p, "2d")
    if twod:
        barcode(pg, (x1 - 54 * s, y - 4 * s, x1, y + 50 * s), twod[0][0], twod[0][1], p["mode"])
    y += 56 * s
    bars = role(p, "route") + role(p, "track")
    gap = (b.y1 - 18 * s - y) / max(1, len(bars))
    for fmt, data, _ in bars:
        barcode(pg, (x0, y, x1, y + gap - 4 * s), fmt, data, p["mode"], hrt=data, hrt_size=9 * s)
        y += gap
    common_footer(pg, b, s)


def draw_qr(pg, b, p):
    """Paperless drop-off: a big QR code and instructions (Evri / InPost / Vinted shape)."""
    s = b.width / 283.5
    x0, x1 = b.x0 + 10 * s, b.x1 - 10 * s
    pg.draw_rect(pymupdf.Rect(b.x0, b.y0, b.x1, b.y0 + 40 * s), color=None, fill=p["color"])
    T(pg, (b.x0 + b.x1) / 2, b.y0 + 28 * s, p["brand"], 22 * s, True, WHITE, "c")
    T(pg, (b.x0 + b.x1) / 2, b.y0 + 62 * s, "Show this code at the drop-off point", 11 * s, True, align="c")
    fmt, data, _ = p["bars"][0]
    barcode(pg, (x0 + 20 * s, b.y0 + 74 * s, x1 - 20 * s, b.y0 + 74 * s + (x1 - x0 - 40 * s)), fmt, data, p["mode"])
    y = b.y0 + 74 * s + (x1 - x0 - 40 * s) + 22 * s
    T(pg, (b.x0 + b.x1) / 2, y, p["track"], 16 * s, True, align="c")
    y += 20 * s
    for ln in ["No printer? No problem. Take your parcel to a drop-off point", f"Service: {p['service']}   Ref: {p['ref']}",
               f"Deliver to: {p['to'][0]}, {p['to'][2]}"]:
        T(pg, (b.x0 + b.x1) / 2, y, ln, 8 * s, align="c", maxw=b.width - 20)
        y += 11 * s
    common_footer(pg, b, s)


STYLES = {"uk": draw_uk, "us": draw_us, "eu": draw_eu, "waybill": draw_waybill, "qr": draw_qr}


# ── carrier catalogue ─────────────────────────────────────────────────────────
def spec(brand, service, style, color, fmt, bars, to, **kw):
    return dict(brand=brand, service=service, style=style, color=color, fmt=fmt, bars=bars, to=to, **kw)


def mk(i):
    return dict(weight=f"{rng.choice([0.2, 0.45, 1.2, 2.8, 7.5, 12.0])}kg", ref=f"ORD-{digits(6)}", sender=SENDERS[i % len(SENDERS)])


def carriers():
    C = []

    def add(key, brand, service, style, color, fmt, bars, to, **kw):
        C.append((key, spec(brand, service, style, color, fmt, bars, to, **kw)))

    def trk(prefix, n, suffix=""):
        return f"{prefix}{digits(n)}{suffix}"

    t = trk("ZZ", 9, "GB")
    add("royalmail-tracked48-a4", "Royal Mail", "Tracked 48", "uk", c(218, 41, 28), "a4tl", [("DataMatrix", "RM" + t, "2d"), ("Code128", t, "track")], person(UK, 0), track=t)
    t = trk("ZZ", 9, "GB")
    add("royalmail-tracked24-4x6", "Royal Mail", "Tracked 24", "uk", c(218, 41, 28), "4x6", [("DataMatrix", "RM" + t, "2d"), ("Code128", t, "track")], person(UK, 1), track=t, frame=True)
    t = trk("ZZ", 9, "GB")
    add("royalmail-specialdelivery-a6", "Royal Mail", "Special Delivery 9am", "uk", c(218, 41, 28), "a6", [("DataMatrix", "RM" + t, "2d"), ("Code128", t, "track")], person(UK, 2), track=t, banner="SIGNED FOR")
    t = trk("ZZ", 9, "GB")
    add("royalmail-intl-tracked-signed", "Royal Mail", "Intl Tracked & Signed", "uk", c(218, 41, 28), "4x6", [("DataMatrix", "RM" + t, "2d"), ("Code128", t, "track")], person(EU, 0), track=t, banner="CUSTOMS: CN22 ATTACHED")
    t = trk("PF", 10)
    add("parcelforce-express48-a4", "Parcelforce", "Express 48", "uk", c(0, 56, 147), "a4tl", [("Code128", t, "track"), ("PDF417", "PF|" + t, "2d")], person(UK, 3), track=t)
    t = "H" + digits(15)
    add("evri-standard-4x6", "Evri", "Standard Home Delivery", "uk", c(0, 160, 220), "4x6", [("Code128", t, "track"), ("QRCode", "EVRI|" + t, "2d")], person(UK, 4), track=t, frame=True)
    t = "EVR" + digits(11)
    add("evri-dropoff-qr-a4", "Evri", "Drop-off", "qr", c(0, 160, 220), "a4tl", [("QRCode", "https://example.invalid/evri/" + t, "2d")], person(UK, 5), track=t)
    t = digits(14)
    add("dpd-nextday-4x6", "DPD", "Next Day", "uk", c(220, 0, 50), "4x6", [("DataMatrix", "%007" + t, "2d"), ("Code128", t, "track"), ("Code128", "ROUTE" + digits(6), "route")], person(UK, 0), track=t)
    t = digits(14)
    add("dpd-local-pickup-a6", "DPD Local", "Pickup", "uk", c(220, 0, 50), "a6", [("DataMatrix", "%007" + t, "2d"), ("Code128", t, "track")], person(UK, 1), track=t)
    t = "JJD" + digits(15)
    add("yodel-standard-a4-cutguide", "Yodel", "Standard", "uk", c(0, 154, 73), "a4tl-guide", [("Code128", t, "track"), ("DataMatrix", t, "2d")], person(UK, 2), track=t)
    t = "IP" + digits(12)
    add("inpost-locker-4x6", "InPost", "Locker 48", "uk", c(255, 205, 0), "4x6", [("QRCode", "INPOST|" + t, "2d"), ("Code128", t, "track")], person(UK, 3), track=t)
    t = "TBA" + digits(12)
    add("amazon-shipping-4x6", "amazon shipping", "Standard", "uk", c(35, 47, 62), "4x6", [("Code128", t, "track"), ("QRCode", t, "2d")], person(UK, 4), track=t)
    t = digits(10)
    add("dhl-express-waybill-a4", "DHL EXPRESS", "WORLDWIDE", "waybill", c(255, 204, 0), "a4tl", [("Code128", "JD01" + digits(14), "route"), ("Code128", "(2L)" + t, "track"), ("DataMatrix", t, "2d")], person(WORLD, 3), track=t)
    t = "GM" + digits(18)
    add("dhl-ecommerce-4x6", "DHL eCommerce", "Parcel Connect", "eu", c(212, 5, 17), "4x6", [("DataMatrix", t, "2d"), ("Code128", t, "track")], person(US, 1), track=t)
    t = "1Z" + "".join(rng.choice("0123456789ABCDEFGHJKLMNPRSTUVWXY") for _ in range(16))
    add("ups-ground-maxicode", "UPS", "UPS GROUND", "us", c(53, 28, 21), "4x6", [("MaxiCode", "[)>\x1e01\x1d96" + digits(9) + "\x1d840\x1d001\x1d" + t, "2d"), ("PDF417", "UPS|" + t, "route"), ("Code128", t, "track")], person(US, 0), track=t, letter="G", frame=True)
    t = digits(12)
    add("fedex-express-pdf417", "FedEx", "Priority Overnight", "us", c(77, 20, 140), "4x6", [("PDF417", "FDX|" + t, "route"), ("Code128", "96" + digits(20), "route"), ("Code128", t, "track")], person(US, 1), track=t, letter="A")
    t = digits(12)
    add("fedex-ground-4x6", "FedEx Ground", "Ground", "us", c(255, 102, 0), "4x6", [("Code128", digits(14), "route"), ("Code128", t, "track")], person(US, 2), track=t, letter="G")
    t = "9400" + digits(18)
    add("usps-priority-4x6", "USPS", "PRIORITY MAIL", "us", c(51, 51, 102), "4x6", [("Code128", "420" + digits(5), "route"), ("Code128", t, "track")], person(US, 0), track=t, letter="P", banner="USPS TRACKING #")
    t = "9205" + digits(18)
    add("usps-ground-advantage-4x6", "USPS", "GROUND ADVANTAGE", "us", c(51, 51, 102), "4x6", [("Code128", "420" + digits(5), "route"), ("Code128", t, "track")], person(US, 2), track=t, letter="G")
    t = digits(16)
    add("canadapost-expedited-4x6", "Canada Post", "Expedited", "us", c(218, 41, 28), "4x6", [("DataMatrix", t, "2d"), ("Code128", t, "route"), ("Code128", "2025" + digits(12), "track")], person(WORLD, 4), track=t, letter="E")
    t = digits(12)
    add("australiapost-parcel-post-a4", "AUSTRALIA POST", "Parcel Post", "eu", c(218, 41, 28), "a4tl", [("DataMatrix", "AP" + t, "2d"), ("Code128", "9" + t, "track")], person(WORLD, 3), track=t)
    t = "S" + digits(10)
    add("sendle-standard-4x6", "Sendle", "Standard", "uk", c(255, 80, 120), "4x6", [("QRCode", t, "2d"), ("Code128", t, "track")], person(WORLD, 3), track=t)
    t = digits(11)
    add("aramex-ppx-4x6", "aramex", "PDX Priority", "waybill", c(238, 238, 238), "4x6", [("Code128", t, "track"), ("Code128", "ARX" + digits(8), "route")], person(WORLD, 2), track=t)
    t = digits(11)
    add("gls-de-4x6", "GLS", "BusinessParcel", "eu", c(0, 32, 91), "4x6", [("DataMatrix", "GLS|" + t, "2d"), ("Code128", t, "track")], person(EU, 0), track=t)
    t = digits(12)
    add("dhl-paket-de-4x6", "DHL Paket", "Standard", "eu", c(212, 5, 17), "4x6", [("DataMatrix", "DE" + t, "2d"), ("Code128", "00" + digits(18), "track")], person(EU, 0), track="00" + digits(18))
    t = digits(14)
    add("hermes-de-a6", "Hermes", "Päckchen", "eu", c(0, 120, 190), "a6", [("Aztec", t, "2d"), ("Code128", t, "track")], person(EU, 0), track=t)
    t = "6A" + digits(11)
    add("colissimo-fr-4x6", "Colissimo", "Domicile sans signature", "eu", c(255, 190, 0), "4x6", [("DataMatrix", t, "2d"), ("Code128", t, "track")], person(EU, 1), track=t)
    t = "XJ" + digits(9) + "FR"
    add("chronopost-13h-a6", "Chronopost", "Chrono 13", "eu", c(0, 80, 160), "a6", [("DataMatrix", t, "2d"), ("Code128", t, "track")], person(EU, 1), track=t)
    t = "3S" + "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(4)) + digits(9)
    add("postnl-pakje-4x6", "PostNL", "Pakket", "eu", c(237, 125, 0), "4x6", [("Code128", t, "track"), ("DataMatrix", t, "2d")], person(EU, 2), track=t)
    t = "3232" + digits(14)
    add("bpost-bpack-a6", "bpost", "bpack 24h Pro", "eu", c(218, 41, 28), "a6", [("Code128", t, "track"), ("DataMatrix", t, "2d")], person(EU, 2), track=t)
    t = "PQ" + digits(18)
    add("correos-paq-4x6", "Correos", "Paq Premium", "eu", c(255, 205, 0), "4x6", [("Code128", t, "track"), ("DataMatrix", t, "2d")], person(EU, 3), track=t)
    t = digits(14)
    add("seur-24-a6", "SEUR", "Seur 24", "eu", c(218, 41, 28), "a6", [("Code128", t, "track"), ("Aztec", t, "2d")], person(EU, 3), track=t)
    t = "00" + digits(18)
    add("postnord-mypack-4x6", "PostNord", "MyPack Home", "eu", c(0, 160, 220), "4x6", [("Code128", t, "track"), ("DataMatrix", t, "2d")], person(EU, 5), track=t)
    t = digits(12)
    add("brt-italy-a6", "BRT Corriere", "Express", "eu", c(218, 41, 28), "a6", [("Code128", t, "track"), ("DataMatrix", t, "2d")], person(EU, 4), track=t)
    t = "RR" + digits(9) + "AT"
    add("austrianpost-a4", "Österreichische Post", "Paket Premium", "eu", c(255, 205, 0), "a4tl", [("DataMatrix", t, "2d"), ("Code128", t, "track")], person(EU, 0), track=t)
    t = "99." + digits(2) + "." + digits(6) + "." + digits(8)
    add("swisspost-priority-4x6", "Die Post", "PostPac Priority", "eu", c(255, 205, 0), "4x6", [("DataMatrix", t, "2d"), ("Code128", t.replace(".", ""), "track")], person(EU, 1), track=t)
    t = digits(12)
    add("japanpost-yupack-jp-4x6", "ゆうパック", "Yu-Pack 発送", "eu", c(218, 41, 28), "4x6", [("QRCode", t, "2d"), ("Code128", t, "track")], person(WORLD, 0), track=t)
    t = digits(12)
    add("bulgaria-posts-4x6", "Български пощи", "Пакет Експрес", "eu", c(0, 128, 90), "4x6", [("Code128", t, "track"), ("DataMatrix", t, "2d")], person(WORLD, 1), track=t)
    t = digits(11)
    add("israelpost-ems-hebrew-4x6", "EMS Israel Post", "EMS", "uk", c(0, 80, 160), "4x6", [("Code128", t, "track"), ("Aztec", t, "2d")], person(WORLD, 2), track=t)
    t = "EN" + digits(9) + "BR"
    add("correios-sedex-br-4x6", "Correios", "SEDEX Mundi", "eu", c(255, 205, 0), "4x6", [("Code128", t, "track"), ("DataMatrix", t, "2d")], person(UK, 0), track=t)
    t = "AN" + digits(9) + "IE"
    add("anpost-parcel-ie-a6", "An Post", "Parcel Post", "eu", c(0, 120, 60), "a6", [("Code128", t, "track"), ("QRCode", t, "2d")], person(UK, 3), track=t)
    t = digits(13)
    add("vinted-go-qr-4x6", "VINTED GO", "Locker drop-off", "qr", c(0, 150, 150), "4x6", [("QRCode", "https://example.invalid/vg/" + t, "2d")], person(EU, 5), track=t)
    t = "SSCC " + digits(18)
    add("amazon-fba-sscc-pallet-4x6", "FBA", "Pallet label", "waybill", c(255, 153, 0), "4x6", [("Code128", "(00)" + t[5:], "track"), ("Code128", "(420)" + digits(5), "route")], person(US, 1), track=t)
    t = digits(14)
    add("evil-long-address-4x6", "Evri", "Standard Home Delivery", "uk", c(0, 160, 220), "4x6", [("Code128", t, "track"), ("QRCode", t, "2d")], EVIL[0], track=t)
    t = digits(14)
    add("evil-tiny-address-a6", "DPD", "Classic", "uk", c(220, 0, 50), "a6", [("Code128", t, "track")], EVIL[1], track=t)
    for i, (key, p) in enumerate(C):
        p.update(mk(i))
        p.setdefault("mode", "vector")
        p.setdefault("frame", False)
    return C


# ── page / document builders ──────────────────────────────────────────────────
FORMATS = {"4x6": (100, 150), "a6": (105, 148), "a5": (148, 210), "a4": (210, 297), "a4tl": (210, 297), "a4tl-guide": (210, 297)}


def label_doc(p, fmt=None):
    """A single-page document exactly the size of the label (for composing onto sheets)."""
    fmt = fmt or p["fmt"]
    w, h = (FORMATS["4x6"] if fmt in ("a4tl", "a4tl-guide") else FORMATS[fmt])
    doc = pymupdf.open()
    pg = doc.new_page(width=w * MM, height=h * MM)
    box = pymupdf.Rect(0, 0, w * MM, h * MM)
    STYLES[p["style"]](pg, box + (2, 2, -2, -2), p)
    if p.get("frame"):
        pg.draw_rect(box + (1, 1, -1, -1), color=BLACK, width=1)
    return doc


def label_pdf(p, fmt=None):
    """The label as a carrier would deliver it: native 4x6/A6, or at the top-left of an A4."""
    fmt = fmt or p["fmt"]
    src = label_doc(p, fmt if not fmt.startswith("a4") else "4x6")
    if not fmt.startswith("a4"):
        return src
    doc = pymupdf.open()
    pg = doc.new_page(width=210 * MM, height=297 * MM)
    r = pymupdf.Rect(12 * MM, 12 * MM, (12 + 100) * MM, (12 + 150) * MM)
    pg.show_pdf_page(r, src, 0)
    if fmt == "a4tl-guide":  # dashed cut guide + instructions beside/below, page-sized
        pg.draw_rect(r + (-4, -4, 4, 4), color=GREY, width=0.8, dashes="[4 3] 0")
        T(pg, 12 * MM, 175 * MM, "Fold here / Cut along the dashed line / Attach with clear tape", 8, color=GREY)
        for i in range(8):
            T(pg, 12 * MM, (186 + i * 6) * MM, f"Step {i + 1}: keep your proof of postage until delivery is confirmed.", 8)
    return doc


def save(doc, rel, expect, desc, risk=(), **extra):
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(doc, pymupdf.Document):
        try:
            doc.subset_fonts()
        except Exception:
            pass
        doc.save(path, garbage=3, deflate=True)
    else:
        path.write_bytes(doc)
    MANIFEST.append(dict(file=rel, expect=expect, desc=desc, risk=list(risk), **extra))
    return path


def compose(sheet_mm, cells, rot=0):
    """cells: [(x_mm, y_mm, w_mm, h_mm, label_doc, rotate)] → one-page PDF."""
    doc = pymupdf.open()
    pg = doc.new_page(width=sheet_mm[0] * MM, height=sheet_mm[1] * MM)
    for x, y, w, h, src, rotate in cells:
        pg.show_pdf_page(pymupdf.Rect(x * MM, y * MM, (x + w) * MM, (y + h) * MM), src, 0, rotate=rotate)
    if rot:
        pg.set_rotation(rot)
    return doc


def concat(docs):
    out = pymupdf.open()
    for d in docs:
        out.insert_pdf(d)
    return out


def render(doc, dpi=200):
    pix = doc[0].get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# ── non-label documents ───────────────────────────────────────────────────────
def invoice_doc(rows=8, logo=False, w=210, h=297, title="INVOICE", grid=True):
    doc = pymupdf.open()
    pg = doc.new_page(width=w * MM, height=h * MM)
    x0, x1 = 15 * MM, (w - 15) * MM
    T(pg, x0, 25 * MM, "Wobbly Wheel Bicycle Emporium", 16, True)
    T(pg, x1, 25 * MM, title, 22, True, align="r")
    if logo:
        im = Image.new("RGB", (240, 120), "white")
        d = ImageDraw.Draw(im)
        d.ellipse([10, 10, 110, 110], fill="black")
        d.ellipse([130, 10, 230, 110], fill="black")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        pg.insert_image(pymupdf.Rect(x0, 30 * MM, x0 + 40 * MM, 50 * MM), stream=buf.getvalue())
    T(pg, x0, 60 * MM, f"Order {digits(7)} - {person(UK, 0)[0]}", 10)
    y = 75 * MM
    for col in range(4):
        cx = x0 + col * (x1 - x0) / 4
        T(pg, cx + 3, y + 12, ["Item", "Qty", "Unit", "Total"][col], 9, True)
    for r in range(rows):
        y += 18
        if grid:
            hr(pg, x0, x1, y, 0.5)
            for col in range(5):  # a ruled table: lots of little vector paths and no barcode
                cx = x0 + col * (x1 - x0) / 4
                pg.draw_line((cx, y), (cx, y + 18), width=0.5)
        for col, v in enumerate([f"Sprocket model {r + 1}", str(1 + r % 3), f"£{4 + r}.99", f"£{(4 + r) * (1 + r % 3)}.99"]):
            T(pg, x0 + col * (x1 - x0) / 4 + 3, y + 12, v, 9)
    if grid:
        hr(pg, x0, x1, y + 18, 0.5)
    T(pg, x0, (h - 20) * MM, "Thank you for your order. " + FOOT, 8, color=GREY)
    return doc


def text_page(lines, w=210, h=297):
    doc = pymupdf.open()
    pg = doc.new_page(width=w * MM, height=h * MM)
    for i, ln in enumerate(lines):
        T(pg, 20 * MM, (25 + i * 6) * MM, ln, 10, i == 0)
    return doc


def cn22_doc(w=100, h=150):
    """Customs declaration: a form of boxes and tick-boxes, no barcode."""
    doc = pymupdf.open()
    pg = doc.new_page(width=w * MM, height=h * MM)
    x0, x1 = 6 * MM, (w - 6) * MM
    pg.draw_rect(pymupdf.Rect(x0, 6 * MM, x1, (h - 6) * MM), color=BLACK, width=1.2)
    T(pg, x0 + 4, 14 * MM, "CUSTOMS DECLARATION  CN 22", 11, True)
    y = 20 * MM
    for label in ["Gift", "Commercial sample", "Documents", "Other"]:
        pg.draw_rect(pymupdf.Rect(x0 + 6, y, x0 + 16, y + 10), color=BLACK, width=0.8)
        T(pg, x0 + 22, y + 8, label, 9)
        y += 16
    for hdr in ["Quantity and detailed description of contents", "Weight (kg)", "Value", "HS tariff no.", "Country of origin"]:
        y += 6
        T(pg, x0 + 4, y, hdr, 7, color=GREY)
        pg.draw_rect(pymupdf.Rect(x0 + 4, y + 3, x1 - 4, y + 22), color=BLACK, width=0.6)
        T(pg, x0 + 8, y + 16, "1 x sprocket (invented)", 9)
        y += 26
    T(pg, x0 + 4, (h - 10) * MM, FOOT, 5, color=GREY)
    return doc


def ocg_label(p):
    """A label whose address block is on a hidden optional-content layer."""
    doc = label_doc(p)
    ocg = doc.add_ocg("Hidden address", on=False)
    pg = doc[0]
    pg.draw_rect(pymupdf.Rect(10, 290, 273, 400), color=None, fill=(1, 0, 0), oc=ocg)
    pg.insert_text((30, 350), "HIDDEN LAYER - should not be visible", fontsize=12, color=WHITE, oc=ocg)
    return doc


# ── build everything ──────────────────────────────────────────────────────────
def build():
    if OUT.exists():
        for child in OUT.iterdir():  # leave agent-*/ (hand-made extras) and findings alone
            if not child.name.startswith("agent-"):
                shutil.rmtree(child) if child.is_dir() else child.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    C = carriers()
    by = dict(C)

    # 01 single labels in their native format
    for key, p in C:
        save(label_pdf(p), f"01-single/{key}.pdf", 1, f"{p['brand']} {p['service']}, {p['fmt']}, {p['mode']} barcodes",
             [r for r in ["cut guide + page-sized help text" if p["fmt"] == "a4tl-guide" else "", "very long address" if "evil-long" in key else "",
                          "tiny address" if "evil-tiny" in key else "", "non-Latin script" if p["to"][3] in ("日本 JAPAN", "БЪЛГАРИЯ", "ישראל ISRAEL") or p["brand"] in ("ゆうパック", "Български пощи") else ""] if r],
             carrier=p["brand"], format=p["fmt"])

    # 01b same label, different barcode embedding (vector / png / jpeg) and frame / no frame
    base = by["dpd-nextday-4x6"]
    for mode in ("png", "jpeg"):
        for fr in (False, True):
            p = dict(base, mode=mode, frame=fr)
            save(label_pdf(p), f"01-single/variant-dpd-barcodes-{mode}-{'frame' if fr else 'noframe'}.pdf", 1,
                 f"DPD label with {mode} raster barcodes, {'with' if fr else 'without'} a border", ["raster barcodes"])
    for key in ("dpd-nextday-4x6", "royalmail-tracked24-4x6", "evri-standard-4x6"):
        save(label_pdf(dict(by[key], mode="compound")), f"01-single/variant-{key}-compound-path-barcodes.pdf", 1,
             f"{by[key]['brand']} label whose barcodes are each ONE compound path (few vector ops)", ["barcode = 1 path op: fails 'paths >= 15'"])
    p = dict(by["fedex-ground-4x6"], bars=[("Code128", digits(14), "route"), ("Code128", digits(12), "track")])
    save(label_pdf(p), "01-single/variant-linear-only-vector.pdf", 1, "Vector Code128 only, no 2D, no border - few vector paths", ["few vector paths: barcode may not look 'barcode-ish'"])

    # 02 marketplaces / aggregators / quirks
    p = dict(by["evri-standard-4x6"], brand="Evri via Parcel2Go", fmt="a4tl-guide")
    d = label_pdf(p)
    pg = d[0]
    T(pg, 180 * MM, 120 * MM, "↑", 90, True)  # Parcel2Go's stray arrow after the footer
    save(d, "02-platforms/parcel2go-style-stray-arrow.pdf", 1, "Label with box, cut guide and a big stray ↑ glyph outside it", ["stray glyph"])
    d = label_pdf(dict(by["usps-priority-4x6"], brand="eBay Labels"), "4x6")
    pg = d[0]
    pg.insert_page(-1) if False else None
    save(d, "02-platforms/ebay-labels-4x6.pdf", 1, "eBay-shaped USPS label")
    sp = label_pdf(by["royalmail-tracked48-a4"], "a4tl")
    pg = sp[0]
    T(pg, 15 * MM, 190 * MM, "Despatch note - Order " + digits(7), 12, True)
    for i in range(10):
        T(pg, 15 * MM, (200 + i * 7) * MM, f"{1 + i % 3} x Sprocket model {i + 1}  ....  £{4 + i}.99", 10)
    save(sp, "02-platforms/clickdrop-label-plus-despatch-note-a4.pdf", 1, "Click & Drop integrated A4: label top-left, despatch note text below (no table lines)", ["text below the label"])
    sp = label_pdf(by["royalmail-intl-tracked-signed"], "4x6")
    combo = pymupdf.open()
    pg = combo.new_page(width=210 * MM, height=297 * MM)
    pg.show_pdf_page(pymupdf.Rect(10 * MM, 10 * MM, 110 * MM, 160 * MM), sp, 0)
    pg.show_pdf_page(pymupdf.Rect(10 * MM, 165 * MM, 110 * MM, 315 * MM) & pg.rect, cn22_doc(), 0)
    save(combo, "02-platforms/clickdrop-label-plus-cn22-a4.pdf", 1, "Label on top, CN22 customs form below on the same A4 (OBA combined shape)", ["CN22 form below the label", "form has many ruled boxes"])
    save(label_pdf(dict(by["evri-dropoff-qr-a4"], brand="Vinted Go via Evri")), "02-platforms/qr-only-big-code.pdf", 1, "Paperless QR only, large code")
    d = label_doc(by["usps-priority-4x6"])
    save(concat([d, invoice_doc(5, grid=False, w=100, h=150, title="PACKING SLIP")]), "02-platforms/shipstation-label-then-packing-slip.pdf", 1, "2 pages: label then a text-only packing slip", ["page 2 is text only"])

    # 03 multi-up
    L = lambda k, fmt="4x6": label_doc(by[k], fmt)
    two = [L("evri-standard-4x6"), L("dpd-nextday-4x6")]
    save(compose((210, 297), [(5, 3, 200, 145, two[0], 0), (5, 150, 200, 145, two[1], 0)]), "03-multiup/a4-2up-stacked.pdf", 2, "Two labels stacked top/bottom on A4 (scaled to fit halves)")
    save(compose((210, 297), [(5, 5, 100, 140, L("gls-de-4x6"), 0), (105, 5, 100, 140, L("colissimo-fr-4x6"), 0),
                              (5, 150, 100, 140, L("royalmail-tracked24-4x6"), 0), (105, 150, 100, 140, L("ups-ground-maxicode"), 0)]), "03-multiup/a4-4up-2x2.pdf", 4, "Four labels 2x2 on A4 (Click & Drop style)")
    save(compose((210, 297), [(5, 5, 100, 140, L("gls-de-4x6"), 0), (105, 5, 100, 140, L("colissimo-fr-4x6"), 0),
                              (5, 150, 100, 140, L("royalmail-tracked24-4x6"), 0)]), "03-multiup/a4-3up-one-empty-slot.pdf", 3, "Three labels in a 2x2 grid with the bottom-right slot empty")
    save(compose((210, 297), [(2, 2, 100, 148, L("evri-standard-4x6"), 0), (108, 2, 100, 148, L("dpd-nextday-4x6"), 0)]), "03-multiup/a4-2up-top-only.pdf", 2, "Two labels side by side in the top half; bottom half blank", ["blank half"])
    save(compose((297, 210), [(5, 5, 140, 200, L("evri-standard-4x6"), 0), (150, 5, 140, 200, L("dpd-nextday-4x6"), 0)]), "03-multiup/a4-landscape-2up-side-by-side.pdf", 2, "Landscape A4, two portrait labels side by side")
    save(compose((297, 210), [(5, 5, 143, 200, label_doc(by["hermes-de-a6"], "a6"), 0), (150, 5, 143, 200, label_doc(by["bpost-bpack-a6"], "a6"), 0)]), "03-multiup/a4-landscape-2up-a5-cells.pdf", 2, "Landscape A4 with two A6-shaped labels enlarged to A5 cells")
    save(compose((210, 297), [(5, 5, 200, 140, L("evri-standard-4x6"), 90), (5, 150, 200, 140, L("dpd-nextday-4x6"), 90)]), "03-multiup/a4-2up-labels-rotated-90.pdf", 2, "Portrait A4, each label rotated 90° (landscape on page)", ["rotated content"])
    save(compose((210, 297), [(10, 5, 90, 90, L("royalmail-tracked24-4x6"), 0), (110, 5, 90, 90, L("evri-standard-4x6"), 0), (10, 100, 90, 90, L("dpd-nextday-4x6"), 0), (110, 100, 90, 90, L("gls-de-4x6"), 0),
                              (10, 195, 90, 90, L("postnl-pakje-4x6"), 0), (110, 195, 90, 90, L("correos-paq-4x6"), 0)]), "03-multiup/a4-6up-3rows.pdf", 6, "Six small labels, 2 columns x 3 rows. Splitter only knows halves", ["3 rows: no centre-line split"])
    cells = [(5 + (i % 2) * 100, 3 + (i // 2) * 72, 98, 70, label_doc(by[k], "4x6"), 0) for i, k in enumerate(["evri-standard-4x6", "dpd-nextday-4x6", "gls-de-4x6", "colissimo-fr-4x6", "postnl-pakje-4x6", "correos-paq-4x6", "sendle-standard-4x6", "inpost-locker-4x6"])]
    save(compose((210, 297), cells), "03-multiup/a4-8up-4rows.pdf", 8, "Eight tiny labels, 2 x 4", ["4 rows", "tiny scale"])
    save(compose((210, 297), [(55, 8, 100, 150, L("evri-standard-4x6"), 0)]), "03-multiup/a4-1up-centred-across-centre-line.pdf", 1, "One label centred so it crosses the page's vertical centre line", ["crosses split line"])
    save(compose((210, 297), [(5, 5, 100, 150, L("evri-standard-4x6"), 0), (98, 150, 100, 140, L("dpd-nextday-4x6"), 0)]), "03-multiup/a4-2up-diagonal-offset.pdf", 2, "Two labels diagonally offset, the second overlapping the centre line", ["crosses split line"])
    save(compose((210, 297), [(5, 5, 100, 150, L("evri-standard-4x6"), 0), (115, 5, 90, 126, label_doc(by["hermes-de-a6"], "a6"), 0)]), "03-multiup/a4-2up-different-sizes.pdf", 2, "A 4x6 and an A6 side by side, top of page")
    save(compose((420, 297), [(5 + i * 100, 5, 98, 147, label_doc(by[k], "4x6"), 0) for i, k in enumerate(["evri-standard-4x6", "dpd-nextday-4x6", "gls-de-4x6", "colissimo-fr-4x6"])]), "03-multiup/a3-landscape-4-in-a-row.pdf", 4, "A3 landscape with four labels in a single row", ["non-A4 page", "4 columns"])
    save(compose((210, 297), [(0, 0, 105, 148.5, L("evri-standard-4x6"), 0), (105, 0, 105, 148.5, L("dpd-nextday-4x6"), 0), (0, 148.5, 105, 148.5, L("gls-de-4x6"), 0), (105, 148.5, 105, 148.5, L("colissimo-fr-4x6"), 0)]),
         "03-multiup/a4-4up-edge-to-edge.pdf", 4, "Four labels butted exactly against each other, no gutters", ["labels touch the centre lines"])
    save(compose((210, 297), [(5, 5, 200, 140, L("evri-standard-4x6"), 0)]), "03-multiup/a4-1up-label-stretched-wide.pdf", 1, "One label distorted to 200x140 mm (non-uniform scale)", ["aspect ratio", "wide label crosses centre"])
    same = L("evri-standard-4x6")
    save(compose((210, 297), [(5, 5, 100, 140, same, 0), (105, 5, 100, 140, same, 0), (5, 150, 100, 140, same, 0), (105, 150, 100, 140, same, 0)]), "03-multiup/a4-4up-same-label-x4.pdf", 4, "The very same label four times (duplicate content)")

    # 04 multi-page documents
    pages = [L(k) for k in ["evri-standard-4x6", "dpd-nextday-4x6", "gls-de-4x6", "colissimo-fr-4x6", "postnl-pakje-4x6"]]
    save(concat(pages), "04-multipage/five-labels-one-per-page.pdf", 5, "Five 4x6 pages, one label each")
    save(concat(pages[:4]), "04-multipage/four-labels-one-per-page.pdf", 4, "Four 4x6 pages: exactly fills one sheet")
    save(concat(pages + [L("correos-paq-4x6")] * 8), "04-multipage/thirteen-labels-bulk.pdf", 13, "13 pages: spills over into a 4th sheet", ["sheet overflow"])
    save(concat([L(list(by)[i % len(by)]) for i in range(50)]), "04-multipage/fifty-labels-bulk.pdf", 50, "50 carrier labels in one PDF (a bulk upload)", ["many pages; slow?"])
    save(concat([L("usps-priority-4x6"), invoice_doc(6, w=100, h=150, grid=True), cn22_doc(), L("dpd-nextday-4x6")]), "04-multipage/label-invoice-cn22-label.pdf", 3, "label, ruled invoice, CN22 form, label - what counts as a label?", ["ruled invoice is 'barcode-ish' vector art", "CN22 is a form"])
    save(concat([L("evri-standard-4x6"), invoice_doc(6, w=100, h=150, grid=False, title="PACKING SLIP"), L("dpd-nextday-4x6")]), "04-multipage/label-textslip-label.pdf", 2, "label, text-only slip (no lines), label", ["text-only page in the middle"])
    blank = pymupdf.open()
    blank.new_page(width=100 * MM, height=150 * MM)
    save(concat([blank, L("evri-standard-4x6"), blank, L("dpd-nextday-4x6"), blank]), "04-multipage/blank-pages-between-labels.pdf", 2, "Blank pages around and between two labels", ["empty pages"])
    save(concat([label_pdf(by["royalmail-tracked48-a4"], "a4tl"), L("gls-de-4x6"), label_doc(by["hermes-de-a6"], "a6"), compose((297, 210), [(5, 5, 140, 200, L("dpd-nextday-4x6"), 0), (150, 5, 140, 200, L("evri-standard-4x6"), 0)])]),
         "04-multipage/mixed-page-sizes.pdf", 5, "A4+tl label, 4x6, A6, then a landscape A4 with 2 labels", ["mixed page sizes/orientations"])
    save(concat([compose((210, 297), [(5, 5, 100, 140, L("gls-de-4x6"), 0), (105, 5, 100, 140, L("colissimo-fr-4x6"), 0), (5, 150, 100, 140, L("royalmail-tracked24-4x6"), 0), (105, 150, 100, 140, L("ups-ground-maxicode"), 0)]),
                 compose((210, 297), [(5, 5, 100, 140, L("evri-standard-4x6"), 0), (105, 5, 100, 140, L("dpd-nextday-4x6"), 0)]), L("postnl-pakje-4x6")]),
         "04-multipage/mixed-4up-2up-1up.pdf", 7, "Page 1 has 4 labels, page 2 has 2, page 3 has 1")
    save(concat([L("evri-standard-4x6"), text_page(["Terms and conditions of carriage", *[f"Clause {i}: the carrier shall not be liable for anything at all." for i in range(30)]]), L("dpd-nextday-4x6")]),
         "04-multipage/label-terms-label.pdf", 2, "label, text-only T&Cs page, label", ["text-only page"])

    # 05 page geometry
    for rot in (90, 180, 270):
        d = label_doc(by["evri-standard-4x6"])
        d[0].set_rotation(rot)
        save(d, f"05-pagegeom/page-rotate-{rot}.pdf", 1, f"A 4x6 label whose page has /Rotate {rot}", ["/Rotate"])
    d = label_pdf(by["royalmail-tracked48-a4"], "a4tl")
    d[0].set_cropbox(pymupdf.Rect(0, 0, 130 * MM, 180 * MM))
    save(d, "05-pagegeom/cropbox-smaller-than-mediabox.pdf", 1, "A4 whose CropBox shows only the label's corner of the page", ["CropBox"])
    src = label_doc(by["evri-standard-4x6"])
    w_, h_ = src[0].rect.width, src[0].rect.height
    d = pymupdf.open()
    pg = d.new_page(width=w_, height=h_)
    pg.set_mediabox(pymupdf.Rect(-200, -300, w_ - 200, h_ - 300))
    pg.show_pdf_page(pg.rect, src, 0)
    save(d, "05-pagegeom/mediabox-negative-origin.pdf", 1, "MediaBox with a negative origin", ["odd MediaBox"])
    d = label_doc(by["evri-standard-4x6"])
    d.xref_set_key(d[0].xref, "UserUnit", "2")
    save(d, "05-pagegeom/userunit-2.pdf", 1, "Page declares /UserUnit 2 (should print at 2x physical size)", ["UserUnit"])
    big = pymupdf.open()
    pg = big.new_page(width=841 * MM, height=1189 * MM)
    pg.show_pdf_page(pymupdf.Rect(300 * MM, 500 * MM, 400 * MM, 650 * MM), label_doc(by["evri-standard-4x6"]), 0)
    save(big, "05-pagegeom/a0-page-small-label-in-the-middle.pdf", 1, "A0 sheet with one small label floating in the middle", ["huge page", "off-centre"])
    tiny = pymupdf.open()
    pg = tiny.new_page(width=57 * MM, height=32 * MM)
    barcode(pg, (4, 4, 57 * MM - 4, 32 * MM - 16), "Code128", digits(12), hrt=digits(12), hrt_size=7)
    T(pg, 4, 32 * MM - 5, "SKU " + digits(6), 6)
    save(tiny, "05-pagegeom/tiny-57x32-product-label.pdf", 1, "A 57x32 mm product label, not a shipping label", ["tiny; enlarged only with --fill"])
    d = label_doc(by["evri-standard-4x6"])
    pg = d[0]
    big_p = pymupdf.open()
    bp = big_p.new_page(width=100 * MM, height=150 * MM)
    bp.show_pdf_page(pymupdf.Rect(-30, -30, 100 * MM + 30, 150 * MM + 30), d, 0)  # content bleeds beyond the page
    save(big_p, "05-pagegeom/content-bleeds-off-page.pdf", 1, "Label scaled up so its edges fall outside the page", ["content outside page"])
    land = pymupdf.open()
    pg = land.new_page(width=297 * MM, height=210 * MM)
    pg.show_pdf_page(pymupdf.Rect(10 * MM, 10 * MM, 160 * MM, 110 * MM), label_doc(by["evri-standard-4x6"]), 0, rotate=90)
    save(land, "05-pagegeom/landscape-a4-one-rotated-label.pdf", 1, "Landscape A4 with a single label rotated 90°", ["rotated content"])

    # 06 things that are not labels (or are borderline)
    save(invoice_doc(6, grid=False), "06-nonlabels/invoice-text-only.pdf", 0, "Plain text invoice, no lines, no images", [])
    save(invoice_doc(12, grid=True), "06-nonlabels/invoice-ruled-table.pdf", 0, "Invoice with a fully ruled table (60+ vector line paths)", ["many paths => looks_like_label()"])
    save(invoice_doc(6, grid=False, logo=True), "06-nonlabels/invoice-with-logo-image.pdf", 0, "Text invoice with a logo picture", ["an image counts as 'barcode-ish'"])
    save(invoice_doc(4, logo=True, grid=True), "06-nonlabels/invoice-logo-and-ruled.pdf", 0, "Invoice with logo and ruled table", ["image + paths"])
    save(text_page(["How to return your parcel", *[f"{i}. Pack the item securely and keep the receipt." for i in range(1, 25)]]), "06-nonlabels/instructions-text-only.pdf", 0, "A text-only instruction sheet")
    save(cn22_doc(), "06-nonlabels/cn22-form-alone.pdf", None, "A CN22 customs form alone (printable? ambiguous)", ["form not label"])
    save(cn22_doc(210, 297), "06-nonlabels/cn23-form-a4.pdf", None, "CN23-style A4 customs form", ["form not label"])
    barcode_font = pymupdf.open()
    pg = barcode_font.new_page(width=100 * MM, height=150 * MM)
    T(pg, 10, 40, "TO: " + person(UK, 0)[0], 12, True)
    T(pg, 10, 70, "|| ||| | |||| || | ||| ||| | || ||| |", 24)  # a 'barcode font' is just text
    save(barcode_font, "06-nonlabels/barcode-font-text-only-label.pdf", 1, "A label whose 'barcode' is a font (pure text)", ["text only => skipped though it is a real label"])
    one_img = pymupdf.open()
    pg = one_img.new_page(width=210 * MM, height=297 * MM)
    im = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(im).rectangle([20, 20, 380, 180], outline="black", width=6)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    pg.insert_image(pymupdf.Rect(20 * MM, 20 * MM, 80 * MM, 50 * MM), stream=buf.getvalue())
    T(pg, 20 * MM, 70 * MM, "Welcome to our online shop! Thanks for choosing us.", 14)
    save(one_img, "06-nonlabels/thank-you-card-logo.pdf", 0, "A thank-you card: one logo image and text", ["image => treated as label?"])
    ink = pymupdf.open()
    pg = ink.new_page(width=210 * MM, height=297 * MM)
    pg.draw_rect(pg.rect, color=None, fill=c(240, 240, 240))
    save(ink, "06-nonlabels/blank-grey-page.pdf", 0, "A page with only a light-grey full-page background")
    save(pymupdf.open() if False else (lambda d: (d.new_page(width=210 * MM, height=297 * MM), d)[1])(pymupdf.open()), "06-nonlabels/blank-page.pdf", 0, "One genuinely blank A4 page")

    # 07 images
    lab = render(label_doc(by["evri-standard-4x6"]), 300)
    save_img = lambda im, name, expect, desc, risk=(), **kw: (OUT / "07-images").mkdir(parents=True, exist_ok=True) or im.save(OUT / "07-images" / name, **kw) or MANIFEST.append(dict(file=f"07-images/{name}", expect=expect, desc=desc, risk=list(risk)))
    save_img(lab, "label.png", 1, "Plain PNG render of a label at 300 dpi")
    save_img(lab, "label.jpg", 1, "JPEG of the same", quality=85)
    save_img(lab, "label.webp", 1, "WebP of the same")
    save_img(lab, "label.bmp", 1, "BMP of the same")
    save_img(lab, "label.gif", 1, "GIF (256 colours) of the same")
    save_img(lab, "label.tiff", 1, "TIFF of the same")
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        save_img(lab, "label.heic", 1, "HEIC (iPhone) of the same", format="HEIF")
    except Exception as e:
        print("skipping HEIC:", e, file=sys.stderr)
    save_img(lab, "label-UPPERCASE.JPG", 1, "Upper-case extension")
    pad = Image.new("RGB", (lab.width + 600, lab.height + 600), "white")
    pad.paste(lab, (300, 300))
    save_img(pad, "label-huge-white-margins.png", 1, "Label with 300px white margin all round", ["margin trimming"])
    off = Image.new("RGB", (lab.width + 200, lab.height + 200), (238, 236, 230))
    off.paste(lab, (100, 100))
    save_img(off, "label-offwhite-scan-margins.png", 1, "Label on off-white paper, scanner style", ["margins not trimmed if < 235?"])
    desk = Image.new("RGB", (lab.width + 900, lab.height + 700), (150, 105, 70))
    ImageDraw.Draw(desk).rectangle([0, 0, desk.width, 120], fill=(130, 90, 60))
    desk.paste(lab.rotate(4, expand=True, fillcolor=(150, 105, 70)), (400, 300))
    desk = desk.filter(ImageFilter.GaussianBlur(1.2))
    save_img(desk, "label-photo-on-desk.jpg", 1, "Skewed phone photo of a printed label on a wooden desk", ["background not white", "skew"], quality=70)
    rgba = lab.convert("RGBA")
    rgba.putalpha(Image.new("L", rgba.size, 255))
    clear = Image.new("RGBA", (lab.width + 100, lab.height + 100), (0, 0, 0, 0))
    clear.paste(rgba, (50, 50))
    save_img(clear, "label-transparent-background.png", 1, "PNG with a transparent border", ["alpha flatten"])
    save_img(ImageOps.invert(lab), "label-dark-mode-inverted.png", 1, "Inverted (dark-mode screenshot)", ["black background"])
    save_img(lab.convert("CMYK"), "label-cmyk.jpg", 1, "CMYK JPEG", ["colour space"], quality=85)
    exif = lab.rotate(90, expand=True)
    ex = Image.Exif()
    ex[0x0112] = 6
    exif.save(OUT / "07-images" / "label-exif-rotated.jpg", exif=ex, quality=85)
    MANIFEST.append(dict(file="07-images/label-exif-rotated.jpg", expect=1, desc="Pixels stored sideways with EXIF Orientation=6 (phone camera)", risk=["EXIF orientation"]))
    chrome = Image.new("RGB", (lab.width // 2 + 40, lab.height // 2 + 330), (242, 242, 247))
    chrome.paste(lab.resize((lab.width // 2, lab.height // 2)), (20, 200))
    dr = ImageDraw.Draw(chrome)
    dr.rectangle([0, 0, chrome.width, 150], fill=(30, 30, 40))
    dr.text((20, 60), "9:41   Evri app  <  Your parcel   ...", fill="white")
    dr.rectangle([0, chrome.height - 130, chrome.width, chrome.height], fill=(0, 160, 220))
    save_img(chrome, "screenshot-with-app-chrome.png", 1, "Phone screenshot: status bar + app chrome around the label", ["only white is trimmed"])
    twoimg = Image.new("RGB", (lab.width * 2 + 100, lab.height + 100), "white")
    twoimg.paste(lab, (40, 50))
    twoimg.paste(render(label_doc(by["dpd-nextday-4x6"]), 300), (lab.width + 60, 50))
    save_img(twoimg, "screenshot-two-labels-side-by-side.png", 2, "One image containing two labels side by side", ["images are one label each"])
    qr = render(label_doc(dict(by["vinted-go-qr-4x6"], mode="png")), 300).crop((0, 700, 1180, 1480))
    save_img(qr, "qr-code-cropped-screenshot.png", 1, "Just the QR code and text, tightly cropped")
    save_img(Image.new("RGB", (800, 1200), "white"), "blank-white.png", 0, "A pure white image", ["blank image is still an 'image'"])
    save_img(Image.new("RGB", (800, 1200), "black"), "solid-black.png", None, "A pure black image", ["no white to trim"])
    save_img(lab.resize((60, 90)), "tiny-60x90.png", 1, "A 60x90 pixel thumbnail of a label", ["unreadable but valid"])
    save_img(lab.resize((lab.width * 3, lab.height * 3), Image.BICUBIC), "huge-3600x5400.png", 1, "A 3600x5400 px image (large)", ["memory/time"])
    save_img(lab.crop((0, 0, lab.width, lab.height // 3)), "label-top-third-only.png", 1, "Only the top third of a label (cropped screenshot)", ["partial label"])
    frames = [lab, render(label_doc(by["dpd-nextday-4x6"]), 300)]
    frames[0].save(OUT / "07-images" / "two-page.tiff", save_all=True, append_images=frames[1:])
    MANIFEST.append(dict(file="07-images/two-page.tiff", expect=None, desc="Multi-page TIFF with two labels", risk=["only first frame read?"]))
    frames[0].convert("P").save(OUT / "07-images" / "animated.gif", save_all=True, append_images=[f.convert("P") for f in frames[1:]], duration=500, loop=0)
    MANIFEST.append(dict(file="07-images/animated.gif", expect=None, desc="Animated GIF whose two frames are different labels", risk=["only first frame read?"]))

    # 08 hostile / structural
    good = label_doc(by["evri-standard-4x6"])
    save(good.tobytes(), "08-hostile/valid-no-extension", None, "A valid label PDF with no file extension", ["skipped as unsupported"])
    save(good.tobytes(), "08-hostile/LABEL-UPPERCASE.PDF", 1, "Upper-case .PDF extension")
    save(good.tobytes(), "08-hostile/label with spaces & (brackets) 100% [v2].pdf", 1, "Spaces, ampersand, brackets and a percent sign in the name")
    save(good.tobytes(), "08-hostile/étiquette-colis-日本語-ラベル.pdf", 1, "Non-ASCII filename")
    save(good.tobytes(), "08-hostile/" + "very-long-file-name-" * 9 + ".pdf", 1, "~190 character filename")
    save(good.tobytes(), "08-hostile/.hidden-label.pdf", 1, "Dotfile name")
    save(lab.tobytes() if False else (lambda b: (b.seek(0), b.getvalue())[1])((lambda b: (lab.save(b, "PNG"), b)[1])(io.BytesIO())), "08-hostile/actually-a-png.pdf", None, "A PNG file named .pdf", ["wrong extension"])
    pb = io.BytesIO()
    good.save(pb)
    save(pb.getvalue(), "08-hostile/actually-a-pdf.png", None, "A PDF file named .png", ["wrong extension"])
    save(good.tobytes()[:900], "08-hostile/truncated.pdf", 0, "A PDF cut off after 900 bytes", ["corrupt"])
    save(b"", "08-hostile/zero-bytes.pdf", 0, "An empty file", ["corrupt"])
    save(b"%PDF-1.7\n" + bytes(rng.randrange(256) for _ in range(2000)), "08-hostile/garbage-after-header.pdf", 0, "PDF header then random bytes", ["corrupt"])
    save(b"not an image at all", "08-hostile/garbage.png", 0, "Text bytes pretending to be a PNG", ["corrupt"])
    save(b"just a note\n", "08-hostile/notes.txt", 0, "Unsupported extension", ["skipped as unsupported"])
    enc = OUT / "08-hostile" / "encrypted-owner-password-only.pdf"
    enc.parent.mkdir(parents=True, exist_ok=True)
    good.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="", permissions=pymupdf.PDF_PERM_PRINT)
    MANIFEST.append(dict(file="08-hostile/encrypted-owner-password-only.pdf", expect=1, desc="AES-256, opens without a password but restricts copying/editing", risk=["encryption"]))
    enc = OUT / "08-hostile" / "encrypted-needs-password.pdf"
    good.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    MANIFEST.append(dict(file="08-hostile/encrypted-needs-password.pdf", expect=0, desc="Needs a user password to open (password: secret)", risk=["encryption", "should fail cleanly"]))
    save(ocg_label(by["evri-standard-4x6"]), "08-hostile/hidden-optional-content-layer.pdf", 1, "A hidden OCG layer carries extra drawn content", ["hidden layer still counted?"])
    w = label_doc(by["evri-standard-4x6"])
    wid = pymupdf.Widget()
    wid.field_type, wid.field_name, wid.field_value = pymupdf.PDF_WIDGET_TYPE_TEXT, "note", "Leave in the shed"
    wid.rect = pymupdf.Rect(20, 300, 260, 330)
    w[0].add_widget(wid)
    save(w, "08-hostile/form-field-note-on-label.pdf", 1, "A fillable text field with a delivery note sitting on the label", ["form widget"])
    ann = label_doc(by["evri-standard-4x6"])
    ann[0].add_freetext_annot(pymupdf.Rect(20, 330, 260, 380), "FRAGILE - handle with care", fontsize=14, text_color=(1, 0, 0))
    ann[0].add_stamp_annot(pymupdf.Rect(150, 200, 270, 260), stamp=0)
    save(ann, "08-hostile/annotations-stamp-and-freetext.pdf", 1, "FreeText and Stamp annotations laid over the label", ["annotations"])
    ol = pymupdf.open()
    pg = ol.new_page(width=100 * MM, height=150 * MM)
    pg.show_pdf_page(pg.rect, label_doc(by["usps-priority-4x6"]), 0)
    # outline every glyph as a path (Canva-style 'convert text to curves'): approximate with a text-to-path via get_texttrace
    save(ol, "08-hostile/nested-form-xobject.pdf", 1, "Label nested as a Form XObject inside another page")
    big_text = pymupdf.open()
    pg = big_text.new_page(width=100 * MM, height=150 * MM)
    barcode(pg, (20, 280, 260, 380), "Code128", digits(14), hrt=digits(14))
    for i in range(400):
        T(pg, 10 + (i % 8) * 34, 20 + (i // 8) * 5.2, f"{i:03d}", 4)
    save(big_text, "08-hostile/dense-micro-text-and-barcode.pdf", 1, "400 tiny text runs and one barcode", ["many text spans"])

    # 09 unicode / layout stress, one per script
    for i, (key, grp, idx) in enumerate([("jp", WORLD, 0), ("bg", WORLD, 1), ("he", WORLD, 2), ("vn", WORLD, 5), ("pl", EU, 4), ("is", EU, 5)]):
        p = dict(by["gls-de-4x6"], to=grp[idx], brand="Unicode Express", track=digits(12))
        p["bars"] = [("DataMatrix", p["track"], "2d"), ("Code128", p["track"], "track")]
        save(label_pdf(p, "4x6"), f"09-unicode/address-{key}.pdf", 1, f"Recipient in {grp[idx][3]} script", ["fonts"])

    # 10 a few that rmlabels itself made, to re-feed
    # (generated at test time by the harness: run any 4-up job and feed rm-labels-*.pdf back in)

    (OUT / "manifest.json").write_text(json.dumps(MANIFEST, indent=1, ensure_ascii=False))
    md = ["# Synthetic label fixtures", "", "Generated by `tests/synthetic/make_synthetic_labels.py`. All data is invented.", "",
          "| file | expect | description | risk |", "|---|---|---|---|"]
    md += [f"| `{m['file']}` | {m['expect'] if m['expect'] is not None else '?'} | {m['desc']} | {', '.join(m['risk'])} |" for m in MANIFEST]
    (OUT / "README.md").write_text("\n".join(md) + "\n")
    print(f"{len(MANIFEST)} fixtures → {OUT}")


if __name__ == "__main__":
    build()
