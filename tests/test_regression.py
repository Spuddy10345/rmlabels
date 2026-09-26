"""v2 must lay out real labels exactly as v1 did (tests/reference/rmlabels_v1.py)."""
import importlib.util
from pathlib import Path

import pymupdf
import pytest

from conftest import private
from make_fixtures import n_up
from rmlabels import core

REF = Path(__file__).parent / "reference" / "rmlabels_v1.py"
spec = importlib.util.spec_from_file_location("rmlabels_v1", REF)
v1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v1)


def render(doc):
    return [p.get_pixmap(dpi=100).samples for p in doc]


def run(engine, paths, start=0, fill=False):
    out = pymupdf.open()
    labels = list(engine.extract_labels(paths))
    engine.place(out, labels, start, fill, **({"log": lambda *_: None} if engine is core else {}))
    return out, labels


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    d = tmp_path_factory.mktemp("in")
    label = private("23x.pdf")
    for n in (2, 4):
        n_up(label, n).save(d / f"clickdrop-{n}up.pdf")
    paths = [label, private("Order-94912151-Docs-020909.pdf"), d / "clickdrop-2up.pdf", d / "clickdrop-4up.pdf"]
    # one of our own finished sheets, fed back in to reprint
    sheet, _ = run(v1, paths[:2], start=1)
    sheet.set_metadata({"keywords": v1.SHEET_MARK})
    sheet.save(d / "rm-labels-20260101-000000.pdf")
    return paths + [d / "rm-labels-20260101-000000.pdf"]


@pytest.mark.parametrize("start", [0, 2])
@pytest.mark.parametrize("fill", [False, True])
def test_pixel_identical_to_v1(inputs, start, fill, capsys):
    old, old_labels = run(v1, inputs, start, fill)
    new, new_labels = run(core, inputs, start, fill)
    capsys.readouterr()
    assert [(t, tuple(c)) for _, _, c, t in new_labels] == [(t, tuple(c)) for _, _, c, t in old_labels]
    assert len(new) == len(old)
    assert render(new) == render(old)


def test_label_counts(inputs):
    counts = {p.name: len(list(core.extract_labels([p]))) for p in inputs}
    assert counts == {"23x.pdf": 1, "Order-94912151-Docs-020909.pdf": 1,
                      "clickdrop-2up.pdf": 2, "clickdrop-4up.pdf": 4,
                      "rm-labels-20260101-000000.pdf": 2}


def test_text_only_pdf_is_not_a_label():
    assert list(core.extract_labels([private("rm-note.pdf")])) == []
