"""Bad input must not take the whole run down; a single label must not be cut in two.

Regressions found by the synthetic-label stress tests (tests/synthetic/)."""
import io

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from rmlabels import cli, core, store, web

MM = core.MM


def barcode_label(w_mm=100, h_mm=150, band=(0.45, 0.55), frame=False) -> pymupdf.Document:
    """A 4x6-ish label with a 2D code in the upper part, a linear barcode in the lower part and an
    empty band across the middle of the page (the shape of many real carrier labels)."""
    doc = pymupdf.open()
    page = doc.new_page(width=w_mm * MM, height=h_mm * MM)
    W, H = page.rect.width, page.rect.height
    page.insert_text((20, 30), "DELIVER TO: A. PERSON", fontsize=12)
    for i in range(6):                      # a 2D code made of 36 separate modules
        for j in range(6):
            if (i + j) % 2 == 0:
                page.draw_rect(pymupdf.Rect(W - 90 + i * 10, 50 + j * 10, W - 80 + i * 10, 60 + j * 10), color=None, fill=(0, 0, 0))
    for k in range(30):                     # a linear barcode, one rect per bar
        x = 20 + k * 7
        page.draw_rect(pymupdf.Rect(x, H * band[1] + 20, x + (2 if k % 2 else 4), H * band[1] + 90), color=None, fill=(0, 0, 0))
    if frame:
        page.draw_rect(pymupdf.Rect(2, 2, W - 2, H - 2), color=(0, 0, 0), width=1)
    return doc


def four_up() -> pymupdf.Document:
    a4 = pymupdf.paper_rect("a4")
    out = pymupdf.open()
    page = out.new_page(width=a4.width, height=a4.height)
    src = barcode_label()
    for x in (0, a4.width / 2):
        for y in (0, a4.height / 2):
            page.show_pdf_page(pymupdf.Rect(x, y, x + a4.width / 2, y + a4.height / 2) + (8, 8, -8, -8), src, 0)
    return out


def found(doc, name="t.pdf"):
    return list(core.extract_labels([(name, doc)]))


def test_label_with_blank_band_through_the_middle_is_one_label():
    labels = found(barcode_label())
    assert len(labels) == 1
    clip = labels[0][2]
    assert clip.y0 < 30 and clip.y1 > 300             # from the address at the top to the barcode at the bottom


def test_small_pages_are_never_cut_into_pieces_smaller_than_a_label():
    a6 = barcode_label(105, 148)
    assert len(found(a6)) == 1


def test_real_multi_up_pages_still_split():
    assert len(found(four_up())) == 4


@pytest.fixture
def no_side_effects(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    return calls


@pytest.fixture
def files(tmp_path):
    good = tmp_path / "good.pdf"
    barcode_label(frame=True).save(good)
    png = io.BytesIO()
    Image.new("RGB", (40, 60), "black").save(png, "PNG")
    bad = {
        "empty.pdf": b"",
        "truncated.pdf": good.read_bytes()[:300],
        "garbage.png": b"not an image",
        "png-named-pdf.pdf": png.getvalue(),
        "pdf-named-png.png": good.read_bytes(),
    }
    for name, data in bad.items():
        (tmp_path / name).write_bytes(data)
    locked = tmp_path / "locked.pdf"
    barcode_label().save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    return tmp_path


@pytest.mark.parametrize("bad", ["empty.pdf", "truncated.pdf", "garbage.png", "locked.pdf"])
def test_one_unreadable_file_does_not_stop_the_good_ones(no_side_effects, files, capsys, bad):
    cli.main([str(files / bad), str(files / "good.pdf")])
    out = capsys.readouterr()
    assert f"Skipping {bad}" in out.err
    assert "1 label(s) from 1 file(s)" in out.out


def test_a_mislabelled_extension_still_works(no_side_effects, files, capsys):
    cli.main([str(files / "pdf-named-png.png"), str(files / "good.pdf")])
    out = capsys.readouterr()
    assert "Skipping" not in out.err
    assert "2 label(s)" in out.out


def test_only_unreadable_files_exit_with_a_message(no_side_effects, files, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([str(files / "empty.pdf"), str(files / "garbage.png")])
    assert "Couldn't find any labels" in str(e.value)
    assert "Skipping empty.pdf" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["abc", "2.5", "", "0", "5", "-1", "99"])
def test_bad_start_is_rejected_with_a_message(no_side_effects, files, capsys, value):
    with pytest.raises(SystemExit) as e:
        cli.main(["--start", value, str(files / "good.pdf")])
    assert e.value.code == 2
    assert "--start" in capsys.readouterr().err


@pytest.mark.parametrize("value,pos", [("1", 1), ("4", 4), ("auto", 1), ("AUTO", 1)])
def test_good_start_values(no_side_effects, files, capsys, value, pos):
    core.write_next_position(0)                       # 'auto' reads the saved position
    cli.main(["--start", value, str(files / "good.pdf")])
    assert f"starting at position {pos}" in capsys.readouterr().out


def test_web_survives_an_image_uploaded_as_pdf(files):
    store.clear(store.connect())
    client = TestClient(web.app)
    png = (files / "png-named-pdf.pdf").read_bytes()
    good = (files / "good.pdf").read_bytes()
    client.post("/upload", files=[("files", ("x.pdf", png, "application/pdf")), ("files", ("good.pdf", good, "application/pdf"))])
    for url in ("/", "/panel", "/sheet.pdf"):
        assert client.get(url).status_code == 200, url
    store.clear(store.connect())
