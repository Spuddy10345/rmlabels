import email.message
import sqlite3

import pymupdf
import pytest
from fastapi.testclient import TestClient

from conftest import private
from rmlabels import core, mail, store, web


@pytest.fixture
def client(monkeypatch):
    store.clear(store.connect())
    core.write_next_position(0)
    printed = []
    monkeypatch.setattr(core, "print_sheet", lambda dest, **kw: printed.append((dest, kw)))
    c = TestClient(web.app)
    c.printed = printed
    return c


def upload(client, *paths, url="/upload", field="files"):
    return client.post(url, files=[(field, (p.name, p.read_bytes(), "application/octet-stream")) for p in paths])


def test_queue_preview_print_and_history(client):
    label, note = private("23x.pdf"), private("rm-note.pdf")
    r = upload(client, label, note)
    assert "Added 2 file(s)" in r.text
    assert "no label found" in r.text                     # the text-only PDF is flagged, not printed
    assert "Added" not in upload(client, label).text      # same file isn't queued twice
    client.post("/text", data={"text": "FRAGILE"})
    r = client.post("/text", data={"preset": "return"})
    assert "3 labels</b> on <b>1 sheet" in r.text

    r = client.get("/panel", params={"start": "3", "mono": "1"})
    assert checked_slots(r.text) == [1, 2] and r.text.count("already used") == 0  # start at 3 = labels 1 and 2 used; shading is in the PNG
    pdf = pymupdf.open(stream=client.get("/sheet.pdf", params={"start": "3"}).content)
    words = pdf[0].get_text()
    assert "FRAGILE" in words and len(pdf) == 2           # from position 3: 23x at 3, FRAGILE at 4, preset on sheet 2

    r = client.post("/print", data={"start": "3", "mono": "1"})
    assert "Sent 3 label(s) on 2 sheet(s)" in r.text and "next free label is 2" in r.text
    (dest, kw), = client.printed
    assert kw == {"mono": True, "draft": False} and dest.exists()
    assert core.read_next_position() == 1
    assert "no label found" in r.text                     # rm-note stays queued; the rest are gone
    assert "Queue" in r.text and "23x.pdf" not in r.text

    h = client.get("/history").text
    assert "23x.pdf" in h and "FRAGILE" in h and "from 3" in h
    job, items = store.history(store.connect())[0]
    client.post("/history/requeue", data={"item": [str(items[0]["id"])]}, follow_redirects=False)
    r = client.get("/panel")
    assert "reprint" in r.text and "printed " in r.text   # duplicate badge on the reprint


def checked_slots(html):
    """Which slot-map boxes are ticked (the labels marked used) in a panel."""
    import re
    return [int(n) for n in re.findall(r'id="slot-\d" name="used" value="(\d)" checked', html)]


def test_api_upload_for_shortcut(client, tmp_path):
    junk = tmp_path / "x.txt"
    junk.write_text("hi")
    r = upload(client, private("23x.pdf"), junk, url="/api/upload", field="file")
    assert r.json()["added"] == ["23x.pdf"] and "x.txt (not a PDF or image)" in r.json()["skipped"]


def test_cross_origin_post_refused(client):
    r = client.post("/print", headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and not client.printed
    assert client.post("/clear", headers={"origin": "http://testserver"}).status_code == 200


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200 and 'id="panel"' in r.text and "+ return" in r.text


# ── failures are visible, bad input doesn't 500 ───────────────────────────────
HX = {"hx-request": "true"}


@pytest.fixture
def quiet_client(client):
    return TestClient(web.app, raise_server_exceptions=False)


def test_server_error_is_shown_to_htmx_and_browsers(quiet_client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(web, "build", boom)
    r = quiet_client.get("/panel", headers=HX)
    assert r.status_code == 500 and r.headers["hx-retarget"] == "#toast" and 'role="alert"' in r.text
    r = quiet_client.get("/panel")
    assert r.status_code == 500 and "Something went wrong" in r.text and "<html" in r.text
    r = quiet_client.post("/api/upload", files=[("file", ("a.txt", b"x"))])  # still JSON for the Shortcut
    assert r.headers["content-type"].startswith("application/json")


def test_unknown_page_and_empty_uploads(client):
    assert "Not found" in client.get("/nope").text and client.get("/nope").status_code == 404
    r = client.post("/api/upload")
    assert r.status_code == 400 and r.json()["error"] == "No file received."
    r = client.post("/upload")
    assert "Choose a file first." in r.text and 'role="alert"' in r.text


def test_junk_form_values_are_tolerated(client):
    assert client.get("/panel", params={"start": "abc"}).status_code == 200
    assert checked_slots(client.get("/panel", params={"start": "9"}).text) == [1, 2, 3]   # clamped to label 4
    assert client.post("/history/requeue", data={"item": ["abc", "-1"]}, follow_redirects=False).status_code == 303


def test_print_reports_an_unwritable_output_folder(client, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    monkeypatch.setitem(web.CONFIG, "out_dir", blocker / "out")
    client.post("/text", data={"text": "FRAGILE"})
    jobs = len(store.history(store.connect()))
    r = client.post("/print", data={"start": "1"})
    assert r.status_code == 502 and "so nothing was printed" in r.text
    assert not client.printed and core.read_next_position() == 0 and len(store.history(store.connect())) == jobs
    assert "FRAGILE" in client.get("/panel").text         # still queued


def test_print_failure_keeps_the_queue(client, monkeypatch):
    def broken(dest, **kw):
        raise RuntimeError("printer on fire")
    monkeypatch.setattr(core, "print_sheet", broken)
    client.post("/text", data={"text": "FRAGILE"})
    jobs = len(store.history(store.connect()))
    r = client.post("/print", data={"start": "1"})
    assert r.status_code == 502 and "Printing failed: printer on fire" in r.text
    assert core.read_next_position() == 0 and len(store.history(store.connect())) == jobs
    assert "FRAGILE" in client.get("/panel").text


def test_mail_check_runs_in_a_worker_thread(client, monkeypatch):
    def fetch(db, cfg=None):                              # the SQLite connection must belong to this thread
        db.execute("SELECT 1")
        return ["label.pdf"]
    monkeypatch.setattr(mail, "fetch_into_queue", fetch)
    r = client.post("/mail/check")
    assert r.status_code == 200 and "Queued from mail: label.pdf" in r.text


# ── mail ──────────────────────────────────────────────────────────────────────
class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages

    def __call__(self, host, port):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def login(self, user, pw):
        assert pw == "secret"

    def select(self, box, readonly):
        return ("OK", [b"2"])

    def search(self, *a):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, num, what):
        return "OK", [(b"", self.messages[int(num) - 1].as_bytes())]


def message(mid, sender, attachments):
    m = email.message.EmailMessage()
    m["Message-ID"], m["From"], m["Subject"] = mid, sender, "Your label"
    m.set_content("see attached")
    for name, data in attachments:
        m.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return m


def test_mail_imports_each_message_once(client, monkeypatch):
    label = private("23x.pdf").read_bytes()
    msgs = [message("<a@x>", "Vinted <no-reply@vinted.co.uk>", [("label.pdf", label), ("terms.txt", b"t")]),
            message("<b@x>", "Spam <x@spam.example>", [("other.pdf", label + b"\n")])]
    monkeypatch.setattr(mail.imaplib, "IMAP4", FakeIMAP(msgs))
    monkeypatch.setattr(mail, "password", lambda cfg: "secret")
    cfg = {"user": "me", "host": "h", "port": 1, "mailbox": "Labels", "days": 7, "senders": ["vinted.co.uk"],
           "keychain_service": "k"}
    db = store.connect()
    assert mail.fetch_into_queue(db, cfg) == ["label.pdf"]
    assert mail.fetch_into_queue(db, cfg) == []           # both messages remembered
    assert [i["source"] for i in store.queue(db)] == ["mail"]


def test_sender_filter():
    assert mail.sender_allowed("A <a@mail.vinted.co.uk>", ["vinted.co.uk"])
    assert mail.sender_allowed("x@evri.com", ["x@evri.com"])
    assert not mail.sender_allowed("x@notvinted.co.uk", ["vinted.co.uk"])
    assert mail.sender_allowed("anyone@x.com", [])


# ── two-step buttons, stale queues, concurrent prints ─────────────────────────
def test_print_and_clear_use_two_step_buttons_not_confirm(client):
    client.post("/text", data={"text": "FRAGILE"})
    r = client.get("/panel", params={"start": "2", "mono": "1"})
    assert "hx-confirm" not in r.text
    assert 'data-arm="Tap again to print 1 label on 1 sheet, starting at label 2, black only"' in r.text
    assert 'hx-trigger="armed"' in r.text and 'data-arm="Remove all 1?"' in r.text


def test_stale_tab_cannot_print_a_different_queue(client):
    client.post("/text", data={"text": "FRAGILE"})
    v = web.token(store.connect())
    client.post("/text", data={"text": "ANOTHER"})        # e.g. the phone added something meanwhile
    r = client.post("/print", data={"start": "1", "v": v})
    assert r.status_code == 409 and "queue changed" in r.text and 'role="alert"' in r.text
    assert not client.printed and "ANOTHER" in r.text      # nothing printed; the refreshed panel is returned
    fresh = web.token(store.connect())
    assert client.post("/print", data={"start": "1", "v": fresh}).status_code == 200 and len(client.printed) == 1
    assert client.post("/print", data={"start": "1", "v": fresh}).status_code == 409 and len(client.printed) == 1


def test_stale_sheet_map_cannot_print_after_another_tab_used_a_slot(client):
    client.post("/text", data={"text": "FRAGILE"})
    base = core.read_used_mask()                            # tab B loaded the sheet with every slot free
    core.write_used_mask(0b1)                               # tab A printed into slot 1
    client.post("/text", data={"text": "ANOTHER"})          # B's poll then refreshes its token
    v = web.token(store.connect())
    r = client.post("/print", data={"used_set": "1", "base": str(base), "v": v})
    assert r.status_code == 409 and not client.printed
    r = client.get("/panel", params={"used_set": "1", "base": str(base), "v": v})
    assert 'name="base" value="1"' in r.text                # the poll reconciles to the saved sheet


def test_double_submit_prints_once(client):
    client.post("/text", data={"text": "FRAGILE"})
    v = web.token(store.connect())
    assert client.post("/print", data={"start": "1", "v": v}).status_code == 200
    r = client.post("/print", data={"start": "1", "v": v})  # the same tap arriving twice
    assert r.status_code == 409 and len(client.printed) == 1


def test_concurrent_prints_are_serialised(client, monkeypatch):
    import threading
    import time
    sent = []

    def slow(dest, **kw):
        time.sleep(0.2)
        sent.append(dest)
    monkeypatch.setattr(core, "print_sheet", slow)
    client.post("/text", data={"text": "FRAGILE"})
    v = web.token(store.connect())
    codes = []

    def fire():
        codes.append(TestClient(web.app).post("/print", data={"start": "1", "v": v}).status_code)
    threads = [threading.Thread(target=fire) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200, 409] and len(sent) == 1


def test_panel_changed_poll(client):
    client.post("/text", data={"text": "FRAGILE"})
    v = web.token(store.connect())
    assert client.get("/panel/changed", params={"v": v}).status_code == 204
    client.post("/text", data={"text": "ANOTHER"})
    r = client.get("/panel/changed", params={"v": v})
    assert r.status_code == 200 and "ANOTHER" in r.text
    assert client.get("/panel/changed").status_code == 200  # no token: always send the panel


# ── what happened after Print: CUPS job id, status chip, undo, position ───────
@pytest.fixture
def cups(client, monkeypatch):
    """A fake CUPS: print_sheet hands out job numbers; job_status answers from cups.answer."""
    class Cups:
        answer = ("printing", "")
        asked = []
        next_job = 42
    c = Cups()

    def print_sheet(dest, **kw):
        c.next_job += 1
        return c.next_job
    monkeypatch.setattr(core, "print_sheet", print_sheet)
    monkeypatch.setattr(core, "job_status", lambda job: (c.asked.append(job), c.answer)[1])
    c.client = client
    return c


def print_one(client, text="FRAGILE", start="1"):
    client.post("/text", data={"text": text})
    r = client.post("/print", data={"start": start, "mono": "1"})
    return r, store.latest_job(store.connect())


def test_print_records_the_cups_job_and_shows_a_status_chip(cups):
    r, job = print_one(cups.client)
    assert job["cups_job"] == 43 and job["state"] == "printing"
    assert 'id="job"' in r.text and "Printing…" in r.text and 'hx-trigger="load delay:2s"' in r.text
    assert f'hx-get="/jobs/{job["id"]}/status?n=1"' in r.text
    assert "Printing…" in cups.client.get("/").text          # still visible after a reload


def test_chip_polls_until_cups_is_done_then_stops_asking(cups):
    _, job = print_one(cups.client)
    r = cups.client.get(f"/jobs/{job['id']}/status", params={"n": 3})
    assert "hx-get" in r.text and "Printing…" in r.text
    cups.answer = ("done", "")
    r = cups.client.get(f"/jobs/{job['id']}/status", params={"n": 4})
    assert "Finished spooling" in r.text and "check the tray" in r.text and "hx-get" not in r.text
    assert store.get_job(store.connect(), job["id"])["state"] == "done"
    asked = len(cups.asked)
    cups.client.get(f"/jobs/{job['id']}/status")             # finished jobs aren't re-asked about
    assert len(cups.asked) == asked


def test_chip_gives_up_politely_and_ignores_status_hiccups(cups):
    _, job = print_one(cups.client)
    cups.answer = ("unknown", "")
    r = cups.client.get(f"/jobs/{job['id']}/status", params={"n": 5})
    assert "hx-get" in r.text and store.get_job(store.connect(), job["id"])["state"] == "printing"  # unchanged
    r = cups.client.get(f"/jobs/{job['id']}/status", params={"n": web.CHIP_POLLS})
    assert "hx-get" not in r.text and "No confirmation from the printer" in r.text


def test_printer_problem_is_spelled_out(cups):
    _, job = print_one(cups.client)
    cups.answer = ("stopped", "Out of paper")
    r = cups.client.get(f"/jobs/{job['id']}/status")
    assert "The printer needs attention" in r.text and "Out of paper." in r.text


def test_unknown_job_is_a_404_not_a_crash(cups):
    assert cups.client.get("/jobs/9999/status").status_code == 404


def test_failed_print_can_be_undone_and_restores_the_position(cups):
    _, job = print_one(cups.client, start="2")               # label 2, so the saved position moves to 3
    assert core.read_next_position() == 2
    cups.answer = ("failed", "")
    r = cups.client.get(f"/jobs/{job['id']}/status")
    assert "The print failed" in r.text and f'hx-post="/jobs/{job["id"]}/undo"' in r.text and "put 1 item back" in r.text
    r = cups.client.post(f"/jobs/{job['id']}/undo")
    assert r.status_code == 200 and "Put 1 item(s) back in the queue." in r.text and "back to 2" in r.text
    assert core.read_next_position() == 1 and "FRAGILE" in cups.client.get("/panel").text
    assert store.get_job(store.connect(), job["id"])["state"] == "undone"
    assert cups.client.post(f"/jobs/{job['id']}/undo").status_code == 409   # can't undo twice


def test_undo_leaves_the_position_alone_if_something_printed_since(cups):
    _, first = print_one(cups.client, "ONE")
    cups.answer = ("failed", "")
    cups.client.get(f"/jobs/{first['id']}/status")
    cups.answer = ("printing", "")
    print_one(cups.client, "TWO", start="2")
    saved = core.read_next_position()
    r = cups.client.post(f"/jobs/{first['id']}/undo")
    assert "Put 1 item(s) back" in r.text and "back to" not in r.text and core.read_next_position() == saved


def test_only_failed_or_cancelled_prints_can_be_undone(cups):
    _, job = print_one(cups.client)
    r = cups.client.post(f"/jobs/{job['id']}/undo")
    assert r.status_code == 409 and "Only a failed or cancelled print" in r.text


def test_sent_but_not_recorded_warns_loudly(cups, monkeypatch):
    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(store, "record_job", broken)
    cups.client.post("/text", data={"text": "FRAGILE"})
    r = cups.client.post("/print", data={"start": "1"})
    assert r.status_code == 500 and "was sent to the printer" in r.text and "print twice" in r.text
    assert "FRAGILE" in cups.client.get("/panel").text       # still queued, so the warning is true


def test_set_position_by_hand(client):
    r = client.post("/position", data={"start": "3"})
    assert core.read_next_position() == 2 and "label 3 is the next free" in r.text
    r = client.post("/position", data={"start": "nope"})
    assert "Pick a label first." in r.text and core.read_next_position() == 2
    client.post("/position", data={"start": "1"})
    assert core.read_next_position() == 0


# ── caching: fast, but never stale ────────────────────────────────────────────
def label_png(text="PARCEL"):
    import io

    from PIL import Image, ImageDraw
    img = Image.new("RGB", (600, 900), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((20, 20, 580, 880), outline="black", width=6)
    d.text((60, 60), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def extractions(monkeypatch):
    real, calls = core.extract_labels, []
    monkeypatch.setattr(core, "extract_labels", lambda sources: (calls.append(1), real(sources))[1])
    return calls


def add_png(client, name="a.png", text="PARCEL"):
    r = client.post("/upload", files=[("files", (name, label_png(text), "image/png"))])
    assert "Added 1 file(s)" in r.text
    return r


def test_labels_are_extracted_once_not_on_every_panel(client, extractions):
    add_png(client)
    client.post("/text", data={"text": "FRAGILE"})
    n = len(extractions)
    for params in ({"start": "1"}, {"start": "1", "mono": "1"}, {"start": "1", "draft": "1"}, {"start": "3"}, {"start": "1", "fill": "1"}):
        assert client.get("/panel", params=params).status_code == 200
        client.get("/sheet.pdf", params=params)
    client.post("/print", data={"start": "1"})
    assert len(extractions) == n                         # option toggles, previews and the print never re-read the file


def test_a_changed_queue_is_never_served_from_a_stale_sheet(client):
    add_png(client)
    one = client.get("/sheet.pdf", params={"start": "1"}).content
    client.post("/text", data={"text": "ADDED LATER"})
    two = pymupdf.open(stream=client.get("/sheet.pdf", params={"start": "1"}).content)
    assert "ADDED LATER" in two[0].get_text() and one != two.tobytes()
    assert "ADDED LATER" not in pymupdf.open(stream=one)[0].get_text()
    pdf3 = pymupdf.open(stream=client.get("/sheet.pdf", params={"start": "2"}).content)  # start is part of the key
    assert pdf3[0].get_pixmap(dpi=40).samples != two[0].get_pixmap(dpi=40).samples


def test_removed_items_leave_the_cache(client):
    add_png(client)
    item = store.queue(store.connect())[0]
    client.get("/panel")
    assert item["id"] in web._items
    client.post(f"/items/{item['id']}/remove")
    assert item["id"] not in web._items


def test_sheet_cache_stays_small_and_closes_old_sheets(client):
    client.post("/text", data={"text": "FRAGILE"})
    docs = []
    for start in ("1", "2", "3", "4"):
        client.get("/panel", params={"start": start})
        docs.append(list(web._sheets.values())[-1])
    assert len(web._sheets) == web.KEEP_SHEETS
    assert docs[0].is_closed and not docs[-1].is_closed
    assert client.get("/panel", params={"start": "1"}).status_code == 200   # an evicted sheet is simply rebuilt


def test_preview_images_are_pngs_with_a_cache_header(client):
    client.post("/text", data={"text": "FRAGILE"})
    panel = client.get("/panel", params={"start": "2"}).text
    assert 'src="/preview/0.png?used_set=1&amp;used=1&amp;v=' in panel and 'loading="lazy"' in panel and "base64" not in panel.split('class="previews"')[1]
    r = client.get("/preview/0.png", params={"start": "2"})
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n" and "max-age" in r.headers["cache-control"]
    assert client.get("/preview/0.png", params={"start": "2", "dpi": "999"}).status_code == 200   # clamped, not an error
    assert client.get("/preview/7.png").status_code == 404
    first = client.get("/preview/0.png", params={"start": "1"}).content
    assert first != r.content                            # start moves the 'used' shading, so the picture differs


def test_stale_preview_version_is_rejected_without_caching(client):
    import re
    client.post("/text", data={"text": "FRAGILE"})
    old_version = re.search(r'name="v" value="([^"]+)"', client.get("/panel").text).group(1)
    client.post("/text", data={"text": "HANDLE WITH CARE"})

    stale = client.get("/preview/0.png", params={"used_set": "1", "v": old_version})
    assert stale.status_code == 409 and stale.headers["cache-control"] == "no-store"
    new_version = re.search(r'name="v" value="([^"]+)"', client.get("/panel").text).group(1)
    current = client.get("/preview/0.png", params={"used_set": "1", "v": new_version})
    assert current.status_code == 200 and "max-age" in current.headers["cache-control"]


def test_saving_a_cached_sheet_does_not_disturb_it(client):
    client.post("/text", data={"text": "FRAGILE"})
    with web.ENGINE, store.session() as db:
        out = web.build(db, 0, False)[0]
        before = out[0].get_pixmap(dpi=50).samples
        core.save_sheet(out, client.printed and client.printed[0][0] or web.CONFIG["out_dir"] / "cache-check.pdf")
        out.tobytes(garbage=3, deflate=True)
        assert out[0].get_pixmap(dpi=50).samples == before


def test_text_fitting_is_remembered_and_unchanged():
    core.fit_text.cache_clear()
    cell = tuple(core.cell_rect(0))
    first = core.fit_text("FRAGILE", cell)
    assert core.fit_text("FRAGILE", cell) == first and core.fit_text.cache_info().hits == 1
    sizes = [core.fit_text(t, cell)[0] for t in ("FRAGILE", "x " * 100, "x " * 300, "x " * 3000)]
    assert sizes == sorted(sizes, reverse=True) and set(sizes) <= set(core.TEXT_SIZES)   # more text never gets a bigger font
    assert sizes[-1] == core.TEXT_SIZES[-1]                                                # and absurd amounts shrink to the smallest


# ── hardening: hosts, cross-site posts, uploads, the phone token, logs ────────
def test_only_known_hosts_are_answered(client):
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400   # DNS rebinding
    for host in ("127.0.0.1:8765", "localhost:8765", "testserver", "mac.tail9dda7a.ts.net"):
        assert client.get("/", headers={"host": host}).status_code == 200, host
    assert client.get("/", headers={"host": "nottailscale.net"}).status_code == 400


def test_extra_hosts_come_from_the_config(client):
    (mw,) = [m for m in web.app.user_middleware if m.cls is web.TrustedHostMiddleware]
    assert mw.kwargs["allowed_hosts"] == web.HOSTS + ["testserver"]   # defaults, plus allowed_hosts from the test config
    assert client.get("/", headers={"host": "testserver"}).status_code == 200


def test_cross_site_posts_are_refused_even_without_an_origin(client):
    r = client.post("/print", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and not client.printed
    assert client.post("/clear", headers={"sec-fetch-site": "same-origin"}).status_code == 200
    assert client.post("/clear").status_code == 200       # curl / the Shortcut send neither header


def test_upload_size_limits(client, monkeypatch):
    big = label_png()
    monkeypatch.setattr(web, "MAX_UPLOAD", len(big) - 1)
    r = client.post("/upload", files=[("files", ("big.png", big, "image/png"))])
    assert "over the 0 MB limit" in r.text and "Added" not in r.text
    monkeypatch.setattr(web, "MAX_UPLOAD", 10 * 2**20)
    monkeypatch.setattr(web, "MAX_BODY", 100)
    r = client.post("/upload", files=[("files", ("a.png", big, "image/png"))])
    assert r.status_code == 413 and "too big" in r.text
    assert client.post("/api/upload", files=[("file", ("a.png", big, "image/png"))]).status_code == 413
    assert not any(i["name"] == "big.png" for i in store.queue(store.connect()))


def test_files_that_can_never_be_labels_are_refused_with_a_reason(client):
    junk = ("scan.pdf", b"this is not a pdf at all", "application/pdf")
    r = client.post("/upload", files=[("files", junk), ("files", ("empty.png", b"", "image/png"))])
    assert "scan.pdf (it isn&#39;t a readable PDF or image)" in r.text   # Jinja escapes the apostrophe
    assert "empty.png (the file is empty)" in r.text and 'class="flash error"' in r.text
    assert not store.queue(store.connect())               # nothing junk was queued
    r = client.post("/api/upload", files=[("file", junk)])
    assert r.json()["added"] == [] and "readable" in r.json()["skipped"][0]


def test_api_token_is_optional_and_checked_when_set(client, monkeypatch):
    png = ("a.png", label_png("TOKEN"), "image/png")
    assert client.post("/api/upload", files=[("file", png)]).status_code == 200            # off by default
    monkeypatch.setitem(web.CONFIG, "api_token", "s3cret")
    r = client.post("/api/upload", files=[("file", ("b.png", label_png("TOKEN2"), "image/png"))])
    assert r.status_code == 401 and r.json()["error"].startswith("Missing or wrong API token")
    assert client.post("/api/upload", files=[("file", png)], headers={"authorization": "Bearer nope"}).status_code == 401
    ok = client.post("/api/upload", files=[("file", ("c.png", label_png("TOKEN3"), "image/png"))],
                     headers={"authorization": "Bearer s3cret"})
    assert ok.status_code == 200 and ok.json()["added"] == ["c.png"]
    assert client.get("/panel").status_code == 200        # the token only guards the Shortcut endpoint


def test_logging_rotates_and_is_not_duplicated(tmp_path):
    import logging
    from rmlabels import cli
    path = tmp_path / "logs" / "rmlabels.log"
    cli.setup_logging(path)
    first_file = next(h for h in logging.getLogger("rmlabels").handlers
                      if isinstance(h, logging.handlers.RotatingFileHandler))
    cli.setup_logging(path)                               # a second call must not double every line
    assert first_file.stream is None
    logging.getLogger("rmlabels.web").info("print: 1 label(s) cups job 7")
    logging.getLogger("uvicorn.error").info("server started")
    logging.getLogger("rmlabels.web").debug("noise")
    for h in set(logging.getLogger("rmlabels").handlers + logging.getLogger("uvicorn.error").handlers):
        h.flush()
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and "cups job 7" in lines[0] and "INFO rmlabels.web" in lines[0]
    assert "INFO uvicorn.error" in lines[1]
    rotating = [h for h in logging.getLogger("rmlabels").handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    uvicorn_file = [h for h in logging.getLogger("uvicorn.error").handlers
                    if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(rotating) == len(uvicorn_file) == 1 and rotating[0] is uvicorn_file[0]
    assert rotating[0].maxBytes == 1_000_000 and rotating[0].backupCount == 3
    for name in ("rmlabels", "uvicorn.error"):            # leave global logging as we found it
        for h in [h for h in logging.getLogger(name).handlers if getattr(h, "_rmlabels", False)]:
            logging.getLogger(name).removeHandler(h)
            h.close()


# ── used-label mask: peeling labels from anywhere on the sheet ────────────────
HOLES = {"used_set": "1", "used": ["1", "3"]}          # labels 1 and 3 already peeled


def slot_states(html):
    """{label number: 'used' | 'prints' | 'free'} from the slot map."""
    import re
    return {n: state for state, n in re.findall(r'class="slot (used|prints|free)"[^>]*>\s*<input[^>]*value="(\d)"', html)}


def test_slot_map_shows_used_free_and_will_print(client):
    client.post("/text", data={"text": "FRAGILE"})
    states = slot_states(client.get("/panel", params=HOLES).text)
    assert states == {"1": "used", "2": "prints", "3": "used", "4": "free"}
    client.post("/text", data={"text": "ANOTHER"})
    assert slot_states(client.get("/panel", params=HOLES).text) == {"1": "used", "2": "prints", "3": "used", "4": "prints"}


def test_nothing_ticked_means_a_fresh_sheet_not_the_saved_one(client):
    core.write_used_mask(0b0011)
    client.post("/text", data={"text": "FRAGILE"})
    assert checked_slots(client.get("/panel").text) == [1, 2]                      # no form state: show the saved sheet
    assert checked_slots(client.get("/panel", params={"used_set": "1"}).text) == []  # form said "none used"


def test_printing_into_the_gaps_then_onto_a_fresh_sheet(client):
    for t in ("AAA", "BBB", "CCC"):
        client.post("/text", data={"text": t})
    pdf = pymupdf.open(stream=client.get("/sheet.pdf", params=HOLES).content)
    assert len(pdf) == 2 and "AAA" in pdf[0].get_text() and "BBB" in pdf[0].get_text() and "CCC" in pdf[1].get_text()
    r = client.post("/print", data=HOLES)
    assert "Sent 3 label(s) on 2 sheet(s)" in r.text and "next free label is 2" in r.text
    assert core.read_used_mask() == 0b0001                 # sheet 2 has its first label used
    job = store.latest_job(store.connect())
    assert job["used_before"] == 0b0101 and job["start"] == 2 and job["labels"] == 3   # "from 2": the first free label


def test_printing_that_fills_the_sheet_leaves_a_fresh_one(client):
    client.post("/text", data={"text": "AAA"})
    client.post("/text", data={"text": "BBB"})
    r = client.post("/print", data={"used_set": "1", "used": ["1", "3"]})
    assert "that sheet is full" in r.text and core.read_used_mask() == 0


def test_undo_restores_a_gappy_sheet_exactly(cups):
    cups.client.post("/text", data={"text": "AAA"})
    cups.client.post("/text", data={"text": "BBB"})
    cups.client.post("/print", data=HOLES)
    job = store.latest_job(store.connect())
    cups.answer = ("failed", "")
    cups.client.get(f"/jobs/{job['id']}/status")
    r = cups.client.post(f"/jobs/{job['id']}/undo")
    assert core.read_used_mask() == 0b0101 and "back to 2" in r.text
    assert checked_slots(cups.client.get("/panel").text) == [1, 3]


def test_undo_of_a_job_from_before_masks_existed(cups):
    _, job = print_one(cups.client, start="2")             # label 2 → saved sheet: labels 1 and 2 used
    with store.session() as db:                            # as if recorded before the used_before column existed
        db.execute("UPDATE jobs SET used_before = NULL WHERE id = ?", (job["id"],))
        db.commit()
    cups.answer = ("cancelled", "")
    cups.client.get(f"/jobs/{job['id']}/status")
    cups.client.post(f"/jobs/{job['id']}/undo")
    assert core.read_used_mask() == 0b0001


def test_saving_the_sheet_by_hand_with_gaps(client):
    r = client.post("/position", data=HOLES)
    assert core.read_used_mask() == 0b0101 and "labels 1 and 3 are used, and label 2 is the next free one" in r.text
    r = client.post("/position", data={"used_set": "1"})
    assert core.read_used_mask() == 0 and "fresh" in r.text
    r = client.post("/position", data={"used_set": "1", "used": ["2"]})
    assert "label 2 is used, and label 1 is the next free one" in r.text
    assert core.read_used_mask() == 0b0010
    client.post("/position", data={"used_set": "1", "used": ["1", "2", "3", "4"]})
    assert core.read_used_mask() == 0                      # four used = a spent sheet = the next one is fresh
    assert client.post("/position", data={}).status_code == 200 and "Pick a label first" in client.post("/position", data={}).text


def test_save_button_only_when_the_map_differs_from_the_saved_sheet(client):
    core.write_used_mask(0b0011)
    assert "Save this as the sheet in the printer" not in client.get("/panel").text
    assert "Save this as the sheet in the printer" in client.get("/panel", params=HOLES).text


def test_the_queue_version_changes_when_the_saved_sheet_changes(client):
    client.post("/text", data={"text": "FRAGILE"})
    before = web.token(store.connect())
    core.write_used_mask(0b0101)
    assert web.token(store.connect()) != before


# ── usability and accessibility basics ────────────────────────────────────────
def test_print_options_are_remembered_for_the_next_visit(client):
    import re
    def checked(html):
        return sorted(re.findall(r'id="opt-(\w+)" name="\w+" value="1" checked', html))
    assert checked(client.get("/").text) == ["mono"]        # defaults: black ink only
    client.post("/text", data={"text": "FRAGILE"})
    client.post("/print", data={"fill": "1", "draft": "1"})  # colour (mono unticked), enlarged, draft
    assert checked(client.get("/").text) == ["draft", "fill"]
    client.post("/text", data={"text": "AGAIN"})
    client.post("/print", data={"mono": "1"})
    assert checked(client.get("/").text) == ["mono"]
    client.post("/text", data={"text": "FAILS"})
    client.post("/print", data={"fill": "1"}, headers={"sec-fetch-site": "cross-site"})  # a refused print remembers nothing
    assert checked(client.get("/").text) == ["mono"]


def test_failed_prints_do_not_change_the_remembered_options(client, monkeypatch):
    import re
    monkeypatch.setattr(core, "print_sheet", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    client.post("/text", data={"text": "FRAGILE"})
    client.post("/print", data={"fill": "1", "draft": "1"})
    assert re.findall(r'id="opt-(\w+)" name="\w+" value="1" checked', client.get("/").text) == ["mono"]


def test_only_typed_text_asks_the_page_to_clear_the_box(client):
    assert client.post("/text", data={"text": "FRAGILE"}).headers["hx-trigger"] == "text-added"
    assert "hx-trigger" not in client.post("/text", data={"preset": "return"}).headers      # preset keeps what you typed
    assert "hx-trigger" not in client.post("/text", data={"text": "   "}).headers            # nothing was added


def test_page_structure_for_screen_readers_and_keyboards(client):
    r = client.get("/").text
    assert '<h1 class="sr-only">Print labels</h1>' in r and 'class="skip" href="#main"' in r and 'id="main"' in r
    assert 'aria-current="page">Queue' in r and 'aria-current="page">History' not in r
    h = client.get("/history").text
    assert 'aria-current="page">History' in h and '<h1 class="sr-only">Print history</h1>' in h


def test_toggles_explain_themselves_and_previews_can_be_enlarged(client):
    client.post("/text", data={"text": "FRAGILE"})
    r = client.get("/").text
    assert "Uses just the black cartridge" in r and "Scales a label up" in r and "Faster and lighter" in r
    assert '<dialog id="zoom"' in r and 'class="zoom" data-full="/preview/0.png?used_set=1&amp;v=' in r and "dpi=150" in r
    assert client.get("/preview/0.png", params={"dpi": "150"}).status_code == 200


def test_manifest_is_installable_and_scoped(client):
    m = client.get("/manifest.webmanifest").json()
    assert m["id"] == "/" and m["scope"] == "/" and m["start_url"] == "/" and m["display"] == "standalone"
    assert m["description"] and {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}


# ── extras: undo, history, printer line, test grid ────────────────────────────
def test_removing_an_item_offers_undo_and_undo_brings_it_back(client):
    client.post("/text", data={"text": "FRAGILE"})
    item = store.queue(store.connect())[0]
    r = client.post(f"/items/{item['id']}/remove")
    assert "Removed FRAGILE." in r.text and f'hx-vals=\'{{"ids": "{item["id"]}"}}\'' in r.text and ">Undo<" in r.text
    assert "Nothing queued" in r.text and not store.queue(store.connect())
    r = client.post("/items/restore", data={"ids": str(item["id"])})
    assert "Put 1 item back in the queue." in r.text and [i["id"] for i in store.queue(store.connect())] == [item["id"]]


def test_clear_can_be_undone_as_a_whole(client):
    for t in ("ONE", "TWO", "THREE"):
        client.post("/text", data={"text": t})
    ids = [i["id"] for i in store.queue(store.connect())]
    r = client.post("/clear")
    assert "Queue cleared." in r.text and f'"ids": "{",".join(map(str, ids))}"' in r.text
    r = client.post("/items/restore", data={"ids": ",".join(map(str, ids))})
    assert "Put 3 items back in the queue." in r.text and len(store.queue(store.connect())) == 3


def test_clearing_nothing_offers_no_undo(client):
    assert ">Undo<" not in client.post("/clear").text
    assert ">Undo<" not in client.post("/items/99999/remove").text


def test_undo_never_duplicates_or_resurrects_printed_items(client):
    add_png(client, "a.png")
    first = store.queue(store.connect())[0]
    client.post(f"/items/{first['id']}/remove")
    add_png(client, "a-again.png")                         # the same picture, queued again meanwhile
    r = client.post("/items/restore", data={"ids": str(first["id"])})
    assert "Nothing to bring back" in r.text and 'role="alert"' in r.text and len(store.queue(store.connect())) == 1
    printed = store.queue(store.connect())[0]["id"]
    client.post("/print", data={"used_set": "1"})          # that copy is printed
    r = client.post("/items/restore", data={"ids": str(printed)})
    assert "Nothing to bring back" in r.text and not store.queue(store.connect())   # a printed item stays printed
    assert store.get_item(store.connect(), printed)["status"] == "printed"
    assert client.post("/items/restore", data={"ids": "abc,,-3"}).status_code == 200


def test_undo_toast_stays_longer_than_a_plain_one(client):
    js = (web.HERE / "static" / "app.js").read_text()
    assert "15000" in js and "[hx-post]" in js


def test_htmx_server_errors_preserve_app_fragments_but_reject_other_bodies(client):
    js = (web.HERE / "static" / "app.js").read_text()
    assert "htmx:beforeSwap" in js and "HX-Retarget" in js and "hx-swap-oob=\"true\"" in js
    assert "e.detail.shouldSwap = false" in js


def test_mobile_print_action_stays_visible_below_previews(client):
    css = (web.HERE / "static" / "app.css").read_text()
    assert "position: fixed; bottom: 0" in css and ".previews { padding-bottom: 180px; }" in css


def jobs_in_history(html):
    return html.count('action="/history/requeue"')


def test_history_pages_and_says_how_each_print_went(client):
    with store.session() as db:
        before = len(store.history(db, 10_000))
        for _ in range(25):
            store.record_job(db, [], web.CONFIG["out_dir"] / "x.pdf", 1, 1, 1, True)
        last = store.latest_job(db)
        store.set_job_state(db, last["id"], "failed")
    page1 = client.get("/history").text
    assert jobs_in_history(page1) == web.HISTORY_PAGE and "Older prints" in page1 and ">Failed<" in page1
    import re
    older = re.search(r'href="/history\?before=(\d+)"', page1).group(1)
    page2 = client.get("/history", params={"before": older}).text
    assert jobs_in_history(page2) == min(web.HISTORY_PAGE, before + 25 - web.HISTORY_PAGE)
    assert "Print again" in page1 and "Queue ticked again" not in page1


def test_history_query_count_does_not_grow_with_the_number_of_prints(client):
    with store.session() as db:
        for _ in range(5):
            store.record_job(db, [], web.CONFIG["out_dir"] / "x.pdf", 1, 1, 1, True)
        seen = []
        db.set_trace_callback(seen.append)
        store.history(db, 10)
        db.set_trace_callback(None)
    assert len(seen) == 2                                  # jobs, then every job's items together


def test_forgetting_a_print_keeps_its_items_and_the_sheet(client, tmp_path):
    sheet = tmp_path / "sheet.pdf"
    sheet.write_bytes(b"%PDF-1.4")
    client.post("/text", data={"text": "FRAGILE"})
    with store.session() as db:
        ids = [i["id"] for i in store.queue(db)]
        job = store.record_job(db, ids, sheet, 1, 1, 1, True)
    r = client.post(f"/history/{job}/delete")
    assert r.status_code == 200 and r.text == ""           # the card is swapped out for nothing
    with store.session() as db:
        assert store.get_job(db, job) is None
        assert store.get_item(db, ids[0])["status"] == "printed" and store.get_item(db, ids[0])["job_id"] is None
    assert sheet.exists()


def test_missing_sheet_file_gets_a_friendly_page(client, tmp_path):
    with store.session() as db:
        job = store.record_job(db, [], tmp_path / "gone.pdf", 1, 1, 1, True)
    r = client.get(f"/history/{job}/sheet.pdf")
    assert r.status_code == 404 and "That sheet file is no longer there." in r.text and "<html" in r.text


def test_printer_line_reports_problems_and_is_cached(client, monkeypatch):
    asked = []
    answer = {"v": ("ready", "")}
    monkeypatch.setattr(core, "printer_status", lambda *a: (asked.append(1), answer["v"])[1])
    monkeypatch.setattr(web, "_printer", (0.0, ("unknown", "")))
    r = client.get("/printer")
    assert "Printer ready" in r.text and 'class="printer ready"' in r.text
    answer["v"] = ("stopped", "Out of paper")
    assert "Printer ready" in client.get("/printer").text and len(asked) == 1       # cached for a few seconds
    monkeypatch.setattr(web, "_printer", (0.0, ("unknown", "")))
    r = client.get("/printer")
    assert "Out of paper" in r.text and 'class="printer problem"' in r.text
    answer["v"] = ("unknown", "")
    monkeypatch.setattr(web, "_printer", (0.0, ("unknown", "")))
    assert "isn't available" in client.get("/printer").text
    assert "Checking the printer" in client.get("/").text and 'hx-get="/printer"' in client.get("/").text


def test_test_grid_prints_without_touching_the_queue_history_or_sheet(client, monkeypatch):
    sent = []
    monkeypatch.setattr(core, "print_sheet", lambda dest, **kw: (sent.append((dest, kw)), 88)[1])
    client.post("/text", data={"text": "FRAGILE"})
    core.write_used_mask(0b0011)
    jobs = len(store.history(store.connect(), 10_000))
    r = client.post("/grid", data={"mono": "1"})
    assert r.status_code == 200 and "test grid to the printer (CUPS job 88)" in r.text and "plain paper" in r.text
    (dest, kw), = sent
    assert dest.name == "rm-labels-grid.pdf" and kw == {"mono": True, "draft": False}
    assert "Label 1" in pymupdf.open(dest)[0].get_text()
    assert core.read_used_mask() == 0b0011 and len(store.history(store.connect(), 10_000)) == jobs
    assert "FRAGILE" in client.get("/panel").text          # the queue is untouched


def test_test_grid_failure_is_reported(client, monkeypatch):
    monkeypatch.setattr(core, "print_sheet", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("printer on fire")))
    r = client.post("/grid")
    assert r.status_code == 502 and "didn&#39;t print: printer on fire" in r.text


# ── regressions from the code review ──────────────────────────────────────────
def test_every_pymupdf_use_holds_the_engine_lock(client, monkeypatch):
    seen = []
    real_open, real_grid = web.open_source, core.test_grid
    monkeypatch.setattr(web, "open_source", lambda *a, **k: (seen.append(("upload", web.ENGINE._is_owned())), real_open(*a, **k))[1])
    monkeypatch.setattr(core, "test_grid", lambda out: (seen.append(("grid", web.ENGINE._is_owned())), real_grid(out))[1])
    add_png(client)
    client.post("/api/upload", files=[("file", ("b.png", label_png("B"), "image/png"))])
    client.post("/grid")
    assert {what for what, _ in seen} == {"upload", "grid"} and all(held for _, held in seen)  # reading queued files counts too


def test_ticking_every_label_means_a_fresh_sheet_and_the_preview_still_shows_labels(client):
    client.post("/text", data={"text": "FRAGILE"})
    four = {"used_set": "1", "used": ["1", "2", "3", "4"]}
    r = client.get("/panel", params=four)
    assert checked_slots(r.text) == [] and slot_states(r.text)["1"] == "prints" and "starting at label 1" in r.text
    assert "&amp;used=" not in r.text                        # the preview URL is for a fresh sheet, not four shaded labels
    page = pymupdf.open(stream=client.get("/sheet.pdf", params=four).content)[0]
    assert "FRAGILE" in page.get_text() and slot_of_label(page) == 0


def slot_of_label(page):
    centre = (page.search_for("FRAGILE")[0].tl + page.search_for("FRAGILE")[0].br) / 2
    return [i for i in range(4) if core.cell_rect(i, pad=0).contains(centre)][0]


def test_undone_items_do_not_claim_to_have_been_printed(cups):
    add_png(cups.client, "a.png", text="UNDO-UNIQUE")        # an image no earlier test has printed
    cups.client.post("/print", data={"used_set": "1"})
    job = store.latest_job(store.connect())
    cups.answer = ("failed", "")
    cups.client.get(f"/jobs/{job['id']}/status")
    r = cups.client.post(f"/jobs/{job['id']}/undo")
    assert "Put 1 item(s) back" in r.text and "printed " not in r.text.split('class="queue"')[1]   # no 'printed <date>' warning
    with store.session() as db:
        assert {store.get_item(db, i)["status"] for i in store.job_items(db, job["id"])} == {"undone"}
    assert "a.png" in cups.client.get("/history").text       # still listed against the failed print


def test_undo_says_when_something_was_already_queued_again(cups):
    add_png(cups.client)
    cups.client.post("/print", data={"used_set": "1"})
    job = store.latest_job(store.connect())
    add_png(cups.client, "again.png")                          # the same picture is queued again meanwhile
    cups.answer = ("failed", "")
    cups.client.get(f"/jobs/{job['id']}/status")
    r = cups.client.post(f"/jobs/{job['id']}/undo")
    assert "Put 0 item(s) back" in r.text and "1 already in the queue again" in r.text


def test_serve_starts_uvicorn_without_its_own_logging_config(monkeypatch):
    import logging

    import uvicorn

    from rmlabels import cli
    called = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: called.update(k))
    try:
        cli.serve(["--port", "1"])
        assert called["log_config"] is None and called["access_log"] is False   # uvicorn's default config would drop our handlers
    finally:
        for name in ("rmlabels", "uvicorn.error"):
            for h in [h for h in logging.getLogger(name).handlers if getattr(h, "_rmlabels", False)]:
                logging.getLogger(name).removeHandler(h)
                h.close()


def test_odd_authorization_headers_are_401_not_500(client, monkeypatch):
    monkeypatch.setitem(web.CONFIG, "api_token", "s3cret")
    quiet = TestClient(web.app, raise_server_exceptions=False)
    for header in ("Bearer é".encode(), "Bearer ÿþ".encode("latin-1"), b"", b"Bearer"):
        r = quiet.post("/api/upload", files=[("file", ("a.png", label_png(), "image/png"))], headers={"authorization": header})
        assert r.status_code == 401, header


def asgi_post(headers, body_chunks=()):
    """Call the real app with a hand-built request, for headers an HTTP client won't let us send."""
    import asyncio
    sent, chunks = [], list(body_chunks)

    async def receive():
        return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)} if chunks else {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)
    scope = {"type": "http", "method": "POST", "path": "/api/upload", "raw_path": b"/api/upload", "query_string": b"", "scheme": "http",
             "server": ("testserver", 80), "client": ("1.2.3.4", 1), "root_path": "", "http_version": "1.1",
             "headers": [(b"host", b"testserver"), *headers]}
    asyncio.run(web.app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def test_body_limit_handles_bad_and_missing_content_length(monkeypatch):
    status, body = asgi_post([(b"content-length", b"abc"), (b"content-type", b"multipart/form-data; boundary=x")])
    assert status == 400 and b"malformed" in body
    monkeypatch.setattr(web, "MAX_BODY", 1000)
    status, body = asgi_post([(b"content-length", b"999999"), (b"content-type", b"multipart/form-data; boundary=x")])
    assert status == 413 and b"too big" in body
    # no Content-Length at all (chunked), and a body that keeps coming: stopped as it streams in
    head = b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n\r\n'
    status, body = asgi_post([(b"content-type", b"multipart/form-data; boundary=b")], [head + b"x" * 600, b"x" * 600, b"x" * 600])
    assert status == 413 and b"too big" in body


def test_chunked_upload_over_the_limit_is_refused(client, monkeypatch):
    monkeypatch.setattr(web, "MAX_BODY", 5000)
    body = (b"--b\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.png\"\r\n\r\n" + b"x" * 20000 + b"\r\n--b--\r\n")
    r = client.post("/upload", content=iter([body[:7000], body[7000:]]), headers={"content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413 and "too big" in r.text
    assert not any(i["name"] == "a.png" for i in store.queue(store.connect()))


def test_a_print_that_succeeded_never_reports_failure_because_of_follow_up_work(client, monkeypatch):
    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(store, "set_setting", broken)
    monkeypatch.setattr(web, "job_context", broken)
    client.post("/text", data={"text": "FRAGILE"})
    r = client.post("/print", data={"used_set": "1"})
    assert r.status_code == 200 and "Sent 1 label(s) on 1 sheet(s)" in r.text and 'class="flash error"' not in r.text
    assert len(client.printed) == 1 and not store.queue(store.connect())        # and it really is recorded


def test_slow_cups_does_not_freeze_the_rest_of_the_page(client, monkeypatch):
    import threading
    import time
    started, release = threading.Event(), threading.Event()

    def slow(dest, **kw):
        started.set()
        release.wait(5)
    monkeypatch.setattr(core, "print_sheet", slow)
    client.post("/text", data={"text": "FRAGILE"})
    v = web.token(store.connect())
    t = threading.Thread(target=lambda: TestClient(web.app).post("/print", data={"used_set": "1", "v": v}))
    t.start()
    assert started.wait(5)                                  # lp is "running"; this is the slow part
    began = time.monotonic()
    other = TestClient(web.app)
    assert other.get("/panel").status_code == 200 and other.get("/preview/0.png").status_code == 200
    assert other.get("/panel/changed", params={"v": v}).status_code in (200, 204)
    assert time.monotonic() - began < 2                     # none of that waited for the printer
    release.set()
    t.join(10)


def test_a_second_print_waits_for_the_first_and_is_then_refused(client, monkeypatch):
    import threading
    import time
    release, entered = threading.Event(), threading.Event()
    sent = []

    def slow(dest, **kw):
        sent.append(dest)
        entered.set()
        release.wait(5)
    monkeypatch.setattr(core, "print_sheet", slow)
    client.post("/text", data={"text": "FRAGILE"})
    v = web.token(store.connect())
    codes = []
    first = threading.Thread(target=lambda: codes.append(TestClient(web.app).post("/print", data={"used_set": "1", "v": v}).status_code))
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=lambda: codes.append(TestClient(web.app).post("/print", data={"used_set": "1", "v": v}).status_code))
    second.start()
    time.sleep(0.3)
    assert len(sent) == 1 and not codes                     # the second is waiting its turn, not printing
    release.set()
    first.join(10)
    second.join(10)
    assert sorted(codes) == [200, 409] and len(sent) == 1


def test_cached_previews_and_other_requests_do_not_rebuild_the_sheet(client, monkeypatch):
    client.post("/text", data={"text": "FRAGILE"})
    client.get("/preview/0.png", params={"used_set": "1"})
    calls = []
    real = web.place_queue
    monkeypatch.setattr(web, "place_queue", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    for _ in range(3):
        assert client.get("/preview/0.png", params={"used_set": "1"}).status_code == 200
    assert not calls                                        # served from the PNG cache without touching the sheet


def test_one_query_finds_when_every_queued_file_was_last_printed(client):
    for n in range(6):
        add_png(client, f"p{n}.png", text=f"LABEL{n}")
    with web.ENGINE, store.session() as db:
        statements = []
        db.set_trace_callback(statements.append)
        web.build(db, 0, False)
        db.set_trace_callback(None)
    assert sum("JOIN jobs" in q for q in statements) == 1  # not one per queued item


def test_last_printed_many_matches_last_printed(client):
    with store.session() as db:
        for n in range(3):
            store.add_file(db, f"m{n}.png", label_png(f"M{n}"), "upload")
        ids = [i["id"] for i in store.queue(db)]
        store.record_job(db, ids[:2], web.CONFIG["out_dir"] / "x.pdf", 1, 2, 1, True)
        shas = [store.get_item(db, i)["sha256"] for i in ids]
        many = store.last_printed_many(db, shas)
        assert {s: many.get(s) for s in shas} == {s: store.last_printed(db, s) for s in shas}
        assert store.last_printed_many(db, []) == {} and shas[2] not in many
