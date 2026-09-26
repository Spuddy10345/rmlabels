import email.message

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
    assert 'value="3" checked' in r.text and r.text.count("already used") == 0  # shading is in the PNG
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
