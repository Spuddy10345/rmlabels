"""Queue and print history (SQLite), plus copies of every label file so any
past print can be repeated. Lives in ~/.local/share/rmlabels/."""
from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from pathlib import Path

from .config import DATA_DIR

DB_PATH = DATA_DIR / "rmlabels.db"
FILES = DATA_DIR / "files"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY,
    printed_at TEXT NOT NULL,
    sheet      TEXT NOT NULL,     -- the PDF that was sent to the printer
    start      INTEGER NOT NULL,  -- 1-4
    labels     INTEGER NOT NULL,
    sheets     INTEGER NOT NULL,
    mono       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id       INTEGER PRIMARY KEY,
    kind     TEXT NOT NULL,       -- 'file' | 'text'
    name     TEXT NOT NULL,       -- original filename (files) or first line (text)
    path     TEXT,                -- stored copy (files)
    sha256   TEXT,                -- files
    text     TEXT,                -- text labels
    source   TEXT NOT NULL,       -- 'upload' | 'mail' | 'cli' | 'reprint'
    added_at TEXT NOT NULL,
    status   TEXT NOT NULL DEFAULT 'queued',  -- queued | printed | removed
    job_id   INTEGER REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS items_sha ON items (sha256);
CREATE TABLE IF NOT EXISTS mail_seen (
    message_id TEXT PRIMARY KEY,
    subject    TEXT,
    seen_at    TEXT NOT NULL
);
"""


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def last_printed(db, sha: str) -> str | None:
    row = db.execute("SELECT j.printed_at FROM items i JOIN jobs j ON j.id = i.job_id "
                     "WHERE i.sha256 = ? AND i.status = 'printed' ORDER BY j.printed_at DESC LIMIT 1",
                     (sha,)).fetchone()
    return row[0] if row else None


def add_file(db, name: str, data: bytes, source: str) -> tuple[int, bool]:
    """Queue a file. Returns (item id, newly added). A file already waiting in
    the queue isn't added twice."""
    sha = hashlib.sha256(data).hexdigest()
    row = db.execute("SELECT id FROM items WHERE sha256 = ? AND status = 'queued'", (sha,)).fetchone()
    if row:
        return row["id"], False
    FILES.mkdir(parents=True, exist_ok=True)
    path = FILES / f"{sha}{Path(name).suffix.lower()}"
    if not path.exists():
        path.write_bytes(data)
    cur = db.execute("INSERT INTO items (kind, name, path, sha256, source, added_at) VALUES ('file', ?, ?, ?, ?, ?)",
                     (name, str(path), sha, source, now()))
    db.commit()
    return cur.lastrowid, True


def add_text(db, text: str, source: str) -> int:
    first = text.strip().splitlines()[0][:80] if text.strip() else "(blank)"
    cur = db.execute("INSERT INTO items (kind, name, text, source, added_at) VALUES ('text', ?, ?, ?, ?)",
                     (first, text, source, now()))
    db.commit()
    return cur.lastrowid


def queue(db) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM items WHERE status = 'queued' ORDER BY id").fetchall()


def remove(db, item_id: int):
    db.execute("UPDATE items SET status = 'removed' WHERE id = ? AND status = 'queued'", (item_id,))
    db.commit()


def clear(db):
    db.execute("UPDATE items SET status = 'removed' WHERE status = 'queued'")
    db.commit()


def record_job(db, item_ids: list[int], sheet: Path, start: int, labels: int, sheets: int, mono: bool) -> int:
    cur = db.execute("INSERT INTO jobs (printed_at, sheet, start, labels, sheets, mono) VALUES (?, ?, ?, ?, ?, ?)",
                     (now(), str(sheet), start, labels, sheets, int(mono)))
    job = cur.lastrowid
    db.executemany("UPDATE items SET status = 'printed', job_id = ? WHERE id = ?", [(job, i) for i in item_ids])
    db.commit()
    return job


def history(db, limit: int = 50):
    jobs = db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [(j, db.execute("SELECT * FROM items WHERE job_id = ? ORDER BY id", (j["id"],)).fetchall()) for j in jobs]


def requeue(db, item_ids: list[int]) -> int:
    """Put copies of past items back in the queue (for a reprint)."""
    n = 0
    for i in item_ids:
        it = db.execute("SELECT * FROM items WHERE id = ?", (i,)).fetchone()
        if not it:
            continue
        if it["kind"] == "file":
            if db.execute("SELECT 1 FROM items WHERE sha256 = ? AND status = 'queued'", (it["sha256"],)).fetchone():
                continue
            db.execute("INSERT INTO items (kind, name, path, sha256, source, added_at) VALUES ('file', ?, ?, ?, 'reprint', ?)",
                       (it["name"], it["path"], it["sha256"], now()))
        else:
            db.execute("INSERT INTO items (kind, name, text, source, added_at) VALUES ('text', ?, ?, 'reprint', ?)",
                       (it["name"], it["text"], now()))
        n += 1
    db.commit()
    return n


def mail_seen(db, message_id: str) -> bool:
    return db.execute("SELECT 1 FROM mail_seen WHERE message_id = ?", (message_id,)).fetchone() is not None


def mark_mail_seen(db, message_id: str, subject: str):
    db.execute("INSERT OR IGNORE INTO mail_seen (message_id, subject, seen_at) VALUES (?, ?, ?)",
               (message_id, subject, now()))
    db.commit()
