import subprocess

import pytest

from rmlabels import core

# Captured from `ipptool -tv ipp://localhost/jobs/111 get-job-attributes.test` on the Mac (job finished normally).
COMPLETED = """
    Get job info with get-job-attributes                                 [PASS]
        job-id (integer) = 111
        job-state (enum) = completed
        job-state-reasons (keyword) = processing-to-stop-point
        job-printer-state-message (textWithoutLanguage) = 
        job-printer-state-reasons (keyword) = none
"""
NOT_FOUND = "        status-code = client-error-not-found (Job #99999 does not exist.)\n"


def fake_run(monkeypatch, result=None, raises=None):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if raises:
            raise raises
        return result
    monkeypatch.setattr(core.subprocess, "run", run)
    return calls


def done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


def test_print_sheet_returns_the_cups_job_number(monkeypatch, tmp_path):
    calls = fake_run(monkeypatch, done("request id is EPSON_XP_2200_Series-112 (1 file(s))\n"))
    assert core.print_sheet(tmp_path / "a.pdf", mono=True) == 112
    cmd, kw = calls[0]
    assert cmd[:3] == ["lp", "-d", core.CONFIG["printer"]] and "ColorModel=Mono" in cmd and cmd[-1].endswith("a.pdf")
    assert kw["timeout"] == core.PRINT_TIMEOUT and kw["capture_output"]


def test_print_sheet_without_a_job_number_still_succeeds(monkeypatch, tmp_path):
    fake_run(monkeypatch, done("queued\n"))
    assert core.print_sheet(tmp_path / "a.pdf") is None


def test_print_sheet_failures_say_why(monkeypatch, tmp_path):
    fake_run(monkeypatch, done(stderr="lp: Error - The printer or class does not exist.\n", code=1))
    with pytest.raises(RuntimeError, match="does not exist"):
        core.print_sheet(tmp_path / "a.pdf")
    fake_run(monkeypatch, raises=subprocess.TimeoutExpired("lp", 20))
    with pytest.raises(RuntimeError, match="check the printer queue before printing again"):
        core.print_sheet(tmp_path / "a.pdf")
    fake_run(monkeypatch, raises=FileNotFoundError())
    with pytest.raises(RuntimeError, match="`lp` wasn't found"):
        core.print_sheet(tmp_path / "a.pdf")


def test_parse_job_status():
    assert core.parse_job_status(COMPLETED) == ("done", "")
    assert core.parse_job_status(COMPLETED.replace("completed", "processing")) == ("printing", "")
    assert core.parse_job_status(COMPLETED.replace("completed", "pending")) == ("queued", "")
    assert core.parse_job_status(COMPLETED.replace("completed", "aborted"))[0] == "failed"
    assert core.parse_job_status(COMPLETED.replace("completed", "canceled"))[0] == "cancelled"
    # job-state-reasons lies on this driver ("processing-to-stop-point" for a good job), so only job-state counts
    assert core.parse_job_status(COMPLETED.replace("none", "media-empty-error"))[0] == "done"


def test_parse_job_status_explains_printer_problems():
    stopped = COMPLETED.replace("completed", "processing-stopped").replace(
        "(keyword) = none", "(1setOf keyword) = media-empty-error,marker-supply-low-warning,media-empty-error")
    assert core.parse_job_status(stopped) == ("stopped", "Out of paper; A cartridge is low")
    assert core.parse_job_status(COMPLETED.replace("= none", "= media-jam-error"))[1] == "Paper jam"


def test_unreadable_answers_are_unknown_not_failures():
    assert core.parse_job_status(NOT_FOUND) == ("unknown", "")
    assert core.parse_job_status('ipptool: Unable to connect to "localhost" on port 631 - Operation now in progress') == ("unknown", "")
    assert core.parse_job_status("") == ("unknown", "")


def test_job_status_asks_ipptool_and_survives_errors(monkeypatch):
    calls = fake_run(monkeypatch, done(COMPLETED))
    assert core.job_status(111) == ("done", "")
    assert calls[0][0][:3] == ["ipptool", "-tv", "ipp://localhost/jobs/111"] and calls[0][1]["timeout"] == 5
    fake_run(monkeypatch, raises=subprocess.TimeoutExpired("ipptool", 5))
    assert core.job_status(111) == ("unknown", "")


def test_job_status_falls_back_to_lpstat_without_ipptool(monkeypatch):
    def run(cmd, **kw):
        if cmd[0] == "ipptool":
            raise FileNotFoundError()
        listing = {"not-completed": "EPSON-12            finjo   1024   Wed 21 Oct 14:00:00 2026\n",
                   "completed": "EPSON-11            finjo   1024   Wed 21 Oct 13:00:00 2026\n"}
        return done(listing[cmd[2]])
    monkeypatch.setattr(core.subprocess, "run", run)
    assert core.job_status(12) == ("printing", "")
    assert core.job_status(11) == ("done", "")
    assert core.job_status(99) == ("unknown", "")


# Captured from `ipptool -tv ipp://localhost/printers/EPSON_XP_2200_Series get-printer-attributes.test` (idle, no problems).
PRINTER_IDLE = """
        printer-is-accepting-jobs (boolean) = true
        printer-state (enum) = idle
        printer-state-message (textWithoutLanguage) = 
        printer-state-reasons (keyword) = none
"""
REFUSED = subprocess.CompletedProcess([], 1, stdout="", stderr='ipptool: Unable to connect to "localhost" on port 631 - Operation now in progress\n')


def test_parse_printer_status():
    assert core.parse_printer_status(PRINTER_IDLE) == ("ready", "")
    assert core.parse_printer_status(PRINTER_IDLE.replace("idle", "processing")) == ("busy", "")
    out_of_paper = PRINTER_IDLE.replace("idle", "stopped").replace("(keyword) = none", "(1setOf keyword) = media-empty-error,marker-supply-low-report")
    assert core.parse_printer_status(out_of_paper) == ("stopped", "Out of paper; A cartridge is low")
    assert core.parse_printer_status(PRINTER_IDLE.replace("idle", "stopped")) == ("stopped", "The print queue is stopped")
    assert core.parse_printer_status(PRINTER_IDLE.replace("= true", "= false")) == ("ready", "The printer isn't accepting jobs")
    assert core.parse_printer_status("") == ("unknown", "") and core.parse_printer_status("garbage") == ("unknown", "")


def test_ipptool_retries_once_while_cupsd_wakes_up(monkeypatch):
    answers = [REFUSED, done(COMPLETED)]
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **kw: answers.pop(0))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    assert core.job_status(111) == ("done", "") and not answers


def test_ipptool_gives_up_after_one_retry_without_claiming_failure(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[0])
        return REFUSED if cmd[0] == "ipptool" else done("")
    monkeypatch.setattr(core.subprocess, "run", run)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    assert core.job_status(111) == ("unknown", "") and core.printer_status() == ("unknown", "")
    assert calls.count("ipptool") == 4                    # two queries, two attempts each; no endless loop


def test_a_missing_job_is_not_mistaken_for_a_connection_problem(monkeypatch):
    calls = fake_run(monkeypatch, done(NOT_FOUND, code=1))
    assert core.job_status(99999) == ("unknown", "") and len(calls) == 1   # answered, so no retry
