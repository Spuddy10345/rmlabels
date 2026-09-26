import pymupdf
import pytest

from conftest import private
from rmlabels import cli, core, store


@pytest.fixture
def no_side_effects(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    return calls


def newest(tmp_out):
    return pymupdf.open(sorted(tmp_out.glob("rm-labels-2*.pdf"))[-1])


def test_mixed_files_and_text(no_side_effects, tmp_out, tmp_path, capsys):
    missing, junk = tmp_path / "nope.pdf", tmp_path / "notes.txt"
    junk.write_text("x")
    cli.main([str(private("23x.pdf")), str(missing), str(junk), str(private("rm-note.pdf")),
              "--text", "FRAGILE", "--text", "@return", "--start", "2"])
    out = capsys.readouterr()
    assert "nope.pdf: file not found" in out.err
    assert "notes.txt: not a PDF or image" in out.err
    assert "No label found in rm-note.pdf" in out.err
    assert "3 label(s) from 2 file(s), starting at position 2" in out.out
    sheet = newest(tmp_out)
    assert len(sheet) == 1
    text = sheet[0].get_text()
    assert "FRAGILE" in text and "Test Person" in text
    assert no_side_effects[-1][:3] == ["open", "-a", "Preview"]
    assert core.read_next_position() == 0  # preview doesn't move the saved position


def test_print_records_history_and_warns_on_repeat(no_side_effects, capsys):
    label = str(private("23x.pdf"))
    cli.main(["--print", "--mono", "--start", "3", label])
    lp = no_side_effects[-1]
    assert lp[:3] == ["lp", "-d", "EPSON_XP_2200_Series"] and "ColorModel=Mono" in lp
    assert core.read_next_position() == 3
    db = store.connect()
    (job, items), *_ = store.history(db)
    assert job["start"] == 3 and job["labels"] == 1 and items[0]["name"] == "23x.pdf"
    capsys.readouterr()
    cli.main(["--start", "auto", label])
    out = capsys.readouterr().out
    assert "already printed" in out
    assert "labels 1-3 are" in out


def test_unknown_preset(no_side_effects):
    with pytest.raises(SystemExit, match="@return"):
        cli.main(["--text", "@nope"])
