import pymupdf
import pytest

from rmlabels import core


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "STATE", tmp_path / "next_position")
    monkeypatch.setattr(core, "USED", tmp_path / "used_mask")
    return tmp_path


def slot_of(page, word):
    """0-based slot (reading order) whose cell contains `word`."""
    hit = page.search_for(word)
    assert hit, f"{word!r} not on the page"
    centre = (hit[0].tl + hit[0].br) / 2
    (slot,) = [i for i in range(4) if core.cell_rect(i, pad=0).contains(centre)]
    return slot


def sheets_for(words, start=0, skip=()):
    out = pymupdf.open()
    end = core.place(out, [core.TextLabel(w) for w in words], start, False, log=lambda *_: None, skip=skip)
    return out, end


def test_mask_from_start():
    assert [core.mask_from_start(n) for n in range(5)] == [0, 1, 3, 7, 15]
    assert core.free_slots(0b0101) == [1, 3] and core.free_slots(15) == []


@pytest.mark.parametrize("mask, n, expected", [
    (0, 0, 0), (0, 1, 0b0001), (0, 3, 0b0111),
    (0, 4, 0),                      # a sheet exactly filled counts as used up: next print starts a fresh one
    (0, 5, 0b0001),                 # 4 on the first sheet, 1 on a second
    (0b0011, 1, 0b0111), (0b0011, 2, 0), (0b0011, 3, 0b0001),
    (0b0101, 1, 0b0111),            # peeled 1 and 3: the next label goes in 2
    (0b0101, 2, 0), (0b0101, 3, 0b0001),
    (0b1111, 2, 0b0011),            # nothing free: starts on a fresh sheet
])
def test_used_after(mask, n, expected):
    assert core.used_after(mask, n) == expected


def test_labels_go_around_peeled_slots_then_onto_a_fresh_sheet():
    out, end = sheets_for(["AAA", "BBB", "CCC"], skip={0, 2})   # labels 1 and 3 already peeled
    assert len(out) == 2
    assert slot_of(out[0], "AAA") == 1 and slot_of(out[0], "BBB") == 3
    assert slot_of(out[1], "CCC") == 0                           # the second sheet is fresh: no skipping there
    assert end == 1


def test_plain_start_still_works_and_skip_is_additive():
    out, _ = sheets_for(["AAA", "BBB"], start=2)
    assert slot_of(out[0], "AAA") == 2 and slot_of(out[0], "BBB") == 3
    out, _ = sheets_for(["AAA", "BBB"], start=1, skip={2})
    assert slot_of(out[0], "AAA") == 1 and slot_of(out[0], "BBB") == 3


def test_a_completely_used_sheet_means_start_on_a_new_one():
    out, end = sheets_for(["AAA"], skip={0, 1, 2, 3})
    assert len(out) == 1 and slot_of(out[0], "AAA") == 0 and end == 1


def test_empty_label_list_makes_no_sheets():
    out, end = sheets_for([], start=2)
    assert len(out) == 0 and end == 2


def test_mask_round_trips_and_keeps_the_older_file_in_step(files):
    core.write_used_mask(0b0101)
    assert core.read_used_mask() == 0b0101
    assert (files / "next_position").read_text() == "1"          # lowest free label, for anything still reading the old file
    assert core.read_next_position() == 1
    core.write_used_mask(0b1111)                                  # a full sheet is stored as a fresh one
    assert core.read_used_mask() == 0 and core.read_next_position() == 0
    assert not list(files.glob("*.tmp"))                          # written atomically


def test_the_older_position_file_is_still_understood(files):
    assert core.read_used_mask() == 0                             # nothing saved: fresh sheet
    (files / "next_position").write_text("3")
    assert core.read_used_mask() == 0b0111 and core.read_next_position() == 3
    core.write_next_position(2)                                   # the old API now writes a mask too
    assert core.read_used_mask() == 0b0011 and (files / "used_mask").read_text() == "3"
    core.write_next_position(4)                                   # 4 wraps to a fresh sheet, as before
    assert core.read_used_mask() == 0


def test_garbage_in_the_mask_file_falls_back(files):
    (files / "used_mask").write_text("banana")
    (files / "next_position").write_text("2")
    assert core.read_used_mask() == 0b0011
    (files / "used_mask").write_text("99")
    assert core.read_used_mask() == 99 & core.FULL
