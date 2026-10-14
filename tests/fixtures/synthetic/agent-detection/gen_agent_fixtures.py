"""Generator for agent-detection fixtures. Writes ONLY into this directory (pdf/ subdirs + manifest.json).
Run: uv run python -I tests/fixtures/synthetic/agent-detection/gen_agent_fixtures.py
All data is invented. Never touches the parent fixtures dir."""
from __future__ import annotations
import io, json, random, sys
from pathlib import Path
import pymupdf
from PIL import Image

HERE = Path(__file__).resolve().parent
MM = 72 / 25.4
BLACK = (0, 0, 0)
MANIFEST = []
PAGES = {"a3": (297 * MM, 420 * MM), "4x6": (288, 432), "a6": (105 * MM, 148 * MM), "a5": (148 * MM, 210 * MM), "a4": (210 * MM, 297 * MM)}


def save(doc, rel, expect, desc, labels=None, **params):
    p = HERE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    doc.save(p, garbage=3, deflate=True)
    MANIFEST.append(dict(file=rel, expect=expect, desc=desc, labels=labels or [], params=params))


# ---- primitives -----------------------------------------------------------------------------
def bars(seed, n, width=200, minw=1.0, maxw=3.5):
    """n bars (x0,x1) laid out across `width` pt, deterministic."""
    rnd = random.Random(seed)
    ws = [rnd.uniform(minw, maxw) for _ in range(n)]
    gaps = [rnd.uniform(minw, maxw) for _ in range(n)]
    scale = width / (sum(ws) + sum(gaps))
    x, out = 0, []
    for w, g in zip(ws, gaps):
        out.append((x, x + w * scale)); x += (w + g) * scale
    return out


def linear(page, x, y, w, h, n, mode="rects", seed=1):
    """Linear barcode of n bars. mode: rects (one path per bar) | compound (one path total) | image | stroke(lines)."""
    bs = bars(seed, n, w)
    if mode == "rects":
        for a, b in bs:
            page.draw_rect(pymupdf.Rect(x + a, y, x + b, y + h), color=None, fill=BLACK)
    elif mode == "compound":
        sh = page.new_shape()
        for a, b in bs:
            sh.draw_rect(pymupdf.Rect(x + a, y, x + b, y + h))
        sh.finish(color=None, fill=BLACK); sh.commit()
    elif mode == "image":
        pw, ph = int(w * 4), int(h * 4)
        im = Image.new("L", (pw, ph), 255)
        for a, b in bs:
            for xx in range(int(a * 4), max(int(b * 4), int(a * 4) + 1)):
                if xx < pw:
                    for yy in range(ph):
                        im.putpixel((xx, yy), 0)
        buf = io.BytesIO(); im.save(buf, "PNG")
        page.insert_image(pymupdf.Rect(x, y, x + w, y + h), stream=buf.getvalue())
    elif mode == "stroke":  # each bar as a stroked line
        for a, b in bs:
            page.draw_line((x + (a + b) / 2, y), (x + (a + b) / 2, y + h), color=BLACK, width=max(b - a, .5))


def matrix2d(page, x, y, side, n=21, mode="rects", seed=2):
    rnd = random.Random(seed)
    m = side / n
    cells = [(i, j) for i in range(n) for j in range(n)
             if (i < 7 and j < 7 and (i in (0, 6) or j in (0, 6) or (2 <= i <= 4 and 2 <= j <= 4))) or rnd.random() < .5]
    if mode in ("rects", "stroke"):  # 'stroke' only makes sense for linear codes
        for i, j in cells:
            page.draw_rect(pymupdf.Rect(x + i * m, y + j * m, x + (i + 1) * m, y + (j + 1) * m), color=None, fill=BLACK)
    elif mode == "compound":
        sh = page.new_shape()
        for i, j in cells:
            sh.draw_rect(pymupdf.Rect(x + i * m, y + j * m, x + (i + 1) * m, y + (j + 1) * m))
        sh.finish(color=None, fill=BLACK); sh.commit()
    elif mode == "image":
        px = 4
        im = Image.new("L", (n * px, n * px), 255)
        for i, j in cells:
            for xx in range(i * px, (i + 1) * px):
                for yy in range(j * px, (j + 1) * px):
                    im.putpixel((xx, yy), 0)
        buf = io.BytesIO(); im.save(buf, "PNG")
        page.insert_image(pymupdf.Rect(x, y, x + side, y + side), stream=buf.getvalue())


def txt(page, x, y, s, size=9, bold=False):
    page.insert_text((x, y), s, fontsize=size, fontname="hebo" if bold else "helv", color=BLACK)


LINES = ["Ms Placeholder Person", "Flat 3, 14 Nowhere Lane", "Fictionville", "FV1 2XX", "UNITED KINGDOM",
         "Ref: ORD-0000123", "Weight 0.7kg", "Service: Standard", "Contents: Gift", "Ship date 01/01/2026",
         "Return: Acme Ltd, 1 Test Rd", "Sender ref 999-000", "Parcel 1 of 1", "Handle with care",
         "Delivery notes: none", "Customs: not required", "Account 00-00-00"]


def draw_label(page, x, y, w, h, *, frame=False, top="2d", bottom="linear", nbars=40, nmod=21, mode="rects",
               gap=None, gapw=10, pitch=12, top_hdr=True):
    """Generic carrier-style label in rect (x,y,w,h) pt.
    top/bottom: '2d' | 'linear' | 'none' = code placed in upper / lower part.
    gap=(fraction of h) -> text lines overlapping [gap*h-gapw/2, gap*h+gapw/2] (relative to label top) are skipped => whitespace band.
    Text lines fill from the top down to the bottom code; 2D code (if top) at top-right, linear at bottom."""
    if frame:
        page.draw_rect(pymupdf.Rect(x, y, x + w, y + h), color=BLACK, width=1.2)
    pad = 8
    if top_hdr:
        txt(page, x + pad, y + pad + 10, "CARRIER  PARCEL", 11, True)
    codeH = 40 * h / 432
    bot_y = y + h - pad - codeH - 12
    side = min(56 * w / 288, 56)
    if top == "2d":
        matrix2d(page, x + w - pad - side, y + pad + 14, side, mode=mode)
    elif top == "linear":
        linear(page, x + pad, y + pad + 14, w - 2 * pad, 28 * h / 432, nbars, mode)
    if bottom == "linear":
        linear(page, x + pad, bot_y, w - 2 * pad, codeH, nbars, mode, seed=5)
        txt(page, x + w / 2 - 40, y + h - pad, "7030238535403 2", 8)
    elif bottom == "2d":
        matrix2d(page, x + w / 2 - side / 2, y + h - pad - side, side, mode=mode)
    ty = y + pad + 20 + 14 * h / 432 + 22
    i = 0
    rel_lo = rel_hi = None
    if gap is not None:
        rel_lo, rel_hi = gap * h - gapw / 2, gap * h + gapw / 2
    while ty < bot_y - 8:
        # text bbox ~ [baseline-0.93*size... baseline+0.22*size]; skip if it touches the gap
        rel = ty - y
        if rel_lo is not None and rel + 3 > rel_lo and rel - 9 < rel_hi:
            ty += pitch; continue
        txt(page, x + pad, ty, LINES[i % len(LINES)], 9, bold=(i == 0))
        ty += pitch; i += 1


def one(size="4x6"):
    d = pymupdf.open(); w, h = PAGES[size]
    return d, d.new_page(width=w, height=h), w, h


# ---- 1. single-label: whitespace-gap sweep --------------------------------------------------------
def gen_gap_sweeps():
    for frame in (False, True):
        for combo, top, bottom in (("2d+lin", "2d", "linear"), ("lin+lin", "linear", "linear"), ("2d+2d", "2d", "2d"),
                                    ("none+lin", "none", "linear"), ("2d+none", "2d", "none")):
            for g in [x / 100 for x in range(34, 67, 2)]:
                d, pg, w, h = one()
                draw_label(pg, 0, 0, w, h, frame=frame, top=top, bottom=bottom, gap=g, gapw=6)
                save(d, f"gap/{'frame' if frame else 'noframe'}_{combo}_g{int(g*100):02d}.pdf", 1,
                     f"4x6 label, whitespace band centred at {g:.0%} of height, codes {combo}, frame={frame}",
                     [[0, 0, w, h]], sweep="gap", frame=frame, combo=combo, gap=g)


# ---- 2. bar-count threshold -----------------------------------------------------------------------
def gen_barcount():
    for n in (1, 5, 10, 14, 15, 16, 30):
        for frame in (False, True):
            d, pg, w, h = one()
            draw_label(pg, 0, 0, w, h, frame=frame, top="none", bottom="linear", nbars=n, gap=None)
            save(d, f"barcount/lin_only_{n:02d}bars_{'frame' if frame else 'noframe'}.pdf", 1,
                 f"4x6, ONE linear barcode of {n} separate rect bars; frame={frame}", [[0, 0, w, h]], bars=n, frame=frame)
    # thresholds counting *everything*: text lines are not paths; a frame stroke counts as 1 path, a rule as 1
    for n in (13, 14, 15):
        d, pg, w, h = one()
        draw_label(pg, 0, 0, w, h, frame=True, top="none", bottom="linear", nbars=n)
        for k in range(3):
            pg.draw_line((8, 120 + k * 6), (w - 8, 120 + k * 6), color=BLACK, width=.5)
        save(d, f"barcount/lin_{n}bars_plus_frame_plus_3rules.pdf", 1,
             f"4x6 with {n} bars + frame + 3 ruler lines (paths = {n}+1+3)", [[0, 0, w, h]], bars=n)


# ---- 3. barcode representation --------------------------------------------------------------------
def gen_repr():
    for mode in ("rects", "compound", "image", "stroke"):
        for combo, top, bottom in (("2d+lin", "2d", "linear"), ("lin-only", "none", "linear"), ("2d-only", "2d", "none")):
            for frame in (False, True):
                d, pg, w, h = one()
                draw_label(pg, 0, 0, w, h, frame=frame, top=top, bottom=bottom, mode=mode, nbars=50)
                save(d, f"repr/{mode}_{combo}_{'frame' if frame else 'noframe'}.pdf", 1,
                     f"4x6, barcodes drawn as {mode}; {combo}; frame={frame}", [[0, 0, w, h]], mode=mode, combo=combo, frame=frame)
    # compound + incidental paths making up the count
    for extra in (0, 5, 10, 14, 15):
        d, pg, w, h = one()
        draw_label(pg, 0, 0, w, h, frame=False, top="2d", bottom="linear", mode="compound")
        for k in range(extra):
            pg.draw_rect(pymupdf.Rect(8, 100 + k * 5, w - 8, 102 + k * 5), color=None, fill=(.8, .8, .8))
        save(d, f"repr/compound_plus_{extra:02d}_grey_rules.pdf", 1,
             f"compound barcodes + {extra} extra thin grey path rects (total paths = 2+{extra})", [[0, 0, w, h]], extra=extra)


# ---- 4. multi-up sheets ---------------------------------------------------------------------------
def grid_sheet(name, sheet, rows, cols, gutter_mm, margin_mm, *, frame=False, landscape=False, dx_mm=0, dy_mm=0,
               nbars=40, empty=(), lw_mm=None, lh_mm=None, aspect=100 / 150, top="2d", mode="rects", folder="multiup"):
    """rows x cols of labels on a sheet. Cells share the sheet minus margin/gutters; each label is the largest
    `aspect` (w/h) rectangle that fits its cell (or lw_mm x lh_mm), centred in the cell. The whole grid is centred on the
    sheet then moved by (dx_mm, dy_mm)."""
    d = pymupdf.open()
    W, H = PAGES[sheet]
    if landscape:
        W, H = H, W
    pg = d.new_page(width=W, height=H)
    g, m = gutter_mm * MM, margin_mm * MM
    cellw, cellh = (W - 2 * m - g * (cols - 1)) / cols, (H - 2 * m - g * (rows - 1)) / rows
    if lw_mm:
        cw, ch = lw_mm * MM, lh_mm * MM
        cellw, cellh = cw, ch
    else:
        cw, ch = (cellh * aspect, cellh) if cellw / cellh > aspect else (cellw, cellw / aspect)
    gw, gh = cols * cellw + (cols - 1) * g, rows * cellh + (rows - 1) * g
    x0, y0 = (W - gw) / 2 + dx_mm * MM, (H - gh) / 2 + dy_mm * MM
    labels = []
    for r in range(rows):
        for c in range(cols):
            if (r, c) in empty:
                continue
            x = x0 + c * (cellw + g) + (cellw - cw) / 2
            y = y0 + r * (cellh + g) + (cellh - ch) / 2
            draw_label(pg, x, y, cw, ch, frame=frame, top=top, bottom="linear", nbars=nbars, mode=mode, pitch=max(7, 12 * ch / 432))
            labels.append([x, y, x + cw, y + ch])
    save(d, f"{folder}/{name}.pdf", len(labels),
         f"{sheet}{' landscape' if landscape else ''} {rows}x{cols} (rows x cols) gutter={gutter_mm}mm margin={margin_mm}mm label={cw/MM:.0f}x{ch/MM:.0f}mm frame={frame} shift=({dx_mm},{dy_mm})mm",
         labels, sheet=sheet, rows=rows, cols=cols, gutter=gutter_mm, margin=margin_mm, frame=frame, dx=dx_mm, dy=dy_mm, landscape=landscape)


def gen_multiup():
    for frame in (False, True):
        f = "fr" if frame else "nf"
        for gut in (0, 1, 3, 5, 10):
            grid_sheet(f"a4_2x2_gut{gut}_{f}", "a4", 2, 2, gut, 5, frame=frame)
            grid_sheet(f"a4_2x1_stacked_gut{gut}_{f}", "a4", 2, 1, gut, 5, frame=frame)
            grid_sheet(f"a4land_1x2_gut{gut}_{f}", "a4", 1, 2, gut, 5, frame=frame, landscape=True)
        for mar in (0, 5, 15, 30):
            grid_sheet(f"a4_2x2_margin{mar}_{f}", "a4", 2, 2, 4, mar, frame=frame)
        for sheet in ("a5", "a6"):
            grid_sheet(f"{sheet}_1x1_{f}", sheet, 1, 1, 0, 5, frame=frame)
            grid_sheet(f"{sheet}_2x1_stacked_{f}", sheet, 2, 1, 3, 4, frame=frame)
            grid_sheet(f"{sheet}_2x2_{f}", sheet, 2, 2, 3, 4, frame=frame)
            grid_sheet(f"{sheet}land_1x2_{f}", sheet, 1, 2, 3, 4, frame=frame, landscape=True)
        grid_sheet(f"a4_3x1_stack_{f}", "a4", 3, 1, 4, 5, frame=frame)
        grid_sheet(f"a4_1x3_row_{f}", "a4", 1, 3, 4, 5, frame=frame)
        grid_sheet(f"a4land_1x3_row_{f}", "a4", 1, 3, 4, 5, frame=frame, landscape=True)
        grid_sheet(f"a4_2x3_grid_{f}", "a4", 3, 2, 4, 5, frame=frame)       # 2 cols x 3 rows
        grid_sheet(f"a4_3x2_grid_{f}", "a4", 2, 3, 4, 5, frame=frame)       # 3 cols x 2 rows
        grid_sheet(f"a4land_3x2_grid_{f}", "a4", 2, 3, 4, 5, frame=frame, landscape=True)
        grid_sheet(f"a4land_2x3_grid_{f}", "a4", 3, 2, 4, 5, frame=frame, landscape=True)
        grid_sheet(f"a4_3up_2x2_empty_{f}", "a4", 2, 2, 4, 5, frame=frame, empty=[(1, 1)])
        grid_sheet(f"a4_2x2_empty_topleft_{f}", "a4", 2, 2, 4, 5, frame=frame, empty=[(0, 0)])
    # grid shifted so centre line crosses a label (user hand-placed labels / printer offset)
    for dx in (-30, -15, -5, 0, 5, 15, 30):
        grid_sheet(f"a4_2x2_shiftx{dx:+d}_nf", "a4", 2, 2, 4, 20, frame=False, dx_mm=dx, lw_mm=80, lh_mm=120)
    for dy in (-30, -10, 0, 10, 30):
        grid_sheet(f"a4_2x2_shifty{dy:+d}_nf", "a4", 2, 2, 4, 20, frame=False, dy_mm=dy, lw_mm=80, lh_mm=100)


# ---- 5. minimal dead-zone experiments --------------------------------------------------------------
def gen_minimal():
    """No text between the two code groups. Top group = 2D code whose lowest edge is at mid+dt;
    bottom group = linear code whose highest edge is at mid+db. A full-width header line crosses x=W/2 so only y can split."""
    for dt in range(-10, 11, 1):      # top group edge, bottom fixed at mid+db=+0
        d, pg, w, h = one(); mid = h / 2
        txt(pg, 8, 18, "CARRIER PARCEL SERVICE  ------------------------------------------  Ref 0001", 9, True)
        matrix2d(pg, w - 70, mid + dt - 56, 56)
        linear(pg, 20, mid + 0, w - 40, 40, 30)
        save(d, f"minimal/y_top_edge{dt:+03d}_bot_edge+0.pdf", 1, f"2D bottom edge at mid{dt:+d}pt; barcode top edge at mid+0", [[0, 0, w, h]], axis="y", dt=dt, db=0)
    for db in range(-10, 11, 1):
        d, pg, w, h = one(); mid = h / 2
        txt(pg, 8, 18, "CARRIER PARCEL SERVICE  ------------------------------------------  Ref 0001", 9, True)
        matrix2d(pg, w - 70, mid + 0 - 56, 56)
        linear(pg, 20, mid + db, w - 40, 40, 30)
        save(d, f"minimal/y_top_edge+00_bot_edge{db:+03d}.pdf", 1, f"2D bottom edge at mid+0; barcode top edge at mid{db:+d}pt", [[0, 0, w, h]], axis="y", dt=0, db=db)
    # x-axis analogue: address text block left, 2D code right, nothing crossing W/2 (typical DPD/Evri look).
    for dr in range(-10, 11, 2):     # right group's left edge relative to W/2
        d, pg, w, h = one(); mid = w / 2
        for i in range(8):
            txt(pg, 10, 40 + i * 13, LINES[i], 10, i == 0)
        matrix2d(pg, mid + dr, 40, 60)
        linear(pg, mid + dr, 130, 60, 30, 20)   # a little barcode below the 2D, right col
        save(d, f"minimal/x_text_left_code_right_edge{dr:+03d}.pdf", 1, f"address text left (x<=~mid), codes right starting at W/2{dr:+d}pt", [[0, 0, w, h]], axis="x", dr=dr)
    # text left + ONLY a 2D code right (2D-only label)
    d, pg, w, h = one()
    for i in range(10):
        txt(pg, 10, 40 + i * 13, LINES[i], 10, i == 0)
    matrix2d(pg, w - 90, 40, 70)
    save(d, "minimal/x_2d_only_address_left.pdf", 1, "address left, single 2D code right, no linear code; only a 2D vector code", [[0, 0, w, h]], axis="x")
    d, pg, w, h = one()
    for i in range(10):
        txt(pg, 10, 40 + i * 13, LINES[i], 10, i == 0)
    matrix2d(pg, w - 90, 40, 70, mode="image")
    save(d, "minimal/x_2d_image_address_left.pdf", 1, "address left, single 2D code as an image right", [[0, 0, w, h]], axis="x")
    # address top, linear code bottom (e.g. typical US layout) with and without frame, long address crossing mid
    for frame in (False, True):
        d, pg, w, h = one()
        if frame: pg.draw_rect(pymupdf.Rect(2, 2, w - 2, h - 2), color=BLACK, width=1)
        for i in range(10):
            txt(pg, 12, 40 + i * 13, LINES[i], 10, i == 0)
        linear(pg, 20, 330, w - 40, 70, 40)
        save(d, f"minimal/y_address_top_barcode_bottom_{'frame' if frame else 'noframe'}.pdf", 1, "address text in the top 40%, big linear barcode in bottom 25%, nothing in between", [[0, 0, w, h]], axis="y", frame=frame)


def gen_phase():
    """Realistic dense label: header + 2D top-right + linear bottom + text lines at pitch p; vary p and phase so the centre line
    hits different places in the text rhythm. Measures how often a gap-less label is split."""
    for p in (11, 12, 13, 14):
        for phase in range(0, p, 1):
            d, pg, w, h = one()
            txt(pg, 8, 18, "CARRIER PARCEL", 11, True)
            pg.draw_line((8, 24), (w - 8, 24), color=BLACK, width=1)   # header rule: crosses x=W/2 so only the y split is in play
            matrix2d(pg, w - 70, 30, 56)
            linear(pg, 20, 372, w - 40, 40, 40, seed=5)
            ty, i = 80 + phase, 0
            while ty < 355:
                txt(pg, 8, ty, LINES[i % len(LINES)], 9)
                ty += p; i += 1
            save(d, f"phase/p{p}_ph{phase:02d}.pdf", 1, f"dense text pitch {p}pt phase {phase}; 2D top right, linear bottom, no frame", [[0, 0, w, h]], pitch=p, phase=phase)


def gen_extra():
    """Grids beyond halves, symmetric about the sheet centre (so the only reason to fail is 'only one cut per axis')."""
    for frame in (False, True):
        f = "fr" if frame else "nf"
        grid_sheet(f"a3land_1x4_{f}", "a3", 1, 4, 4, 5, frame=frame, landscape=True)
        grid_sheet(f"a4land_1x4_small_{f}", "a4", 1, 4, 3, 5, frame=frame, landscape=True)
        grid_sheet(f"a4_8up_2cols_4rows_{f}", "a4", 4, 2, 2, 5, frame=frame, aspect=99 / 68)
        grid_sheet(f"a4_4x1_stack_{f}", "a4", 4, 1, 2, 5, frame=frame, aspect=150 / 70)
        for dx in (-30, -15, 15, 30):
            grid_sheet(f"a4_2x2_shiftx{dx:+d}_{f}", "a4", 2, 2, 4, 20, frame=frame, dx_mm=dx, lw_mm=80, lh_mm=120)
        for dy in (-30, -10, 10, 30):
            grid_sheet(f"a4_2x2_shifty{dy:+d}_{f}", "a4", 2, 2, 4, 20, frame=frame, dy_mm=dy, lw_mm=80, lh_mm=100)


def main():
    only = sys.argv[1:]
    for name, fn in (("minimal", gen_minimal), ("phase", gen_phase), ("gap", gen_gap_sweeps), ("barcount", gen_barcount),
                     ("repr", gen_repr), ("multiup", gen_multiup), ("extra", gen_extra)):
        if not only or name in only:
            fn()
    mf = HERE / "manifest.json"
    old = json.loads(mf.read_text()) if mf.exists() and only else []
    keep = [e for e in old if e["file"].split("/")[0] not in only]
    merged = {e["file"]: e for e in keep + MANIFEST}   # later entries win (extra re-emits some multiup files)
    (HERE / "manifest.json").write_text(json.dumps(list(merged.values()), indent=1))
    print(len(MANIFEST), "fixtures")


if __name__ == "__main__":
    main()
