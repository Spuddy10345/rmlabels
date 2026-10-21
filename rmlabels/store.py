"""Queue and print history (SQLite), plus copies of every label file so any
past print can be repeated. Lives in ~/.local/share/rmlabels/."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import sqlite3
import threading
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
    status   TEXT NOT NULL DEFAULT 'queued',  -- queued | printed | removed | undone
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


# Schema changes after the baseline above. Entry n is applied once, in order, and leaves
# PRAGMA user_version = n + 1. Each entry is a tuple of single statements.
MIGRATIONS: list[tuple[str, ...]] = [
    ("ALTER TABLE jobs ADD COLUMN cups_job INTEGER",),  # CUPS job number, to ask the printer how it got on
    ("ALTER TABLE jobs ADD COLUMN state TEXT",),        # last known: sent | queued | printing | stopped | done | failed | cancelled | undone | unknown
    ("ALTER TABLE jobs ADD COLUMN used_before INTEGER",),  # which labels on the sheet were already used before this print (bit n = label n+1)
    ("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",),  # small things worth remembering across devices
]

_migrated: set[Path] = set()
_migrate_lock = threading.Lock()


def migrate(db: sqlite3.Connection):
    """Bring the database to the latest schema. Safe to run from several processes at once (web, CLI)."""
    db.executescript(SCHEMA)
    if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
        db.execute("PRAGMA journal_mode = WAL")  # readers no longer block the writer; persists in the file
    if db.execute("PRAGMA user_version").fetchone()[0] >= len(MIGRATIONS):
        return
    db.execute("BEGIN IMMEDIATE")  # whoever gets here second re-reads the version and finds nothing to do
    try:
        for n in range(db.execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS)):
            for stmt in MIGRATIONS[n]:
                db.execute(stmt)
            db.execute(f"PRAGMA user_version = {n + 1}")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def connect() -> sqlite3.Connection:
    """A new connection. Close it when done (or use session()); it may be handed between threads."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    if DB_PATH not in _migrated:
        with _migrate_lock:
            if DB_PATH not in _migrated:
                migrate(db)
                _migrated.add(DB_PATH)
    return db


@contextlib.contextmanager
def session():
    db = connect()
    try:
        yield db
    finally:
        db.close()


def last_printed(db, sha: str) -> str | None:
    row = db.execute("SELECT j.printed_at FROM items i JOIN jobs j ON j.id = i.job_id "
                     "WHERE i.sha256 = ? AND i.status = 'printed' ORDER BY j.printed_at DESC LIMIT 1",
                     (sha,)).fetchone()
    return row[0] if row else None


def last_printed_many(db, shas) -> dict[str, str]:
    """last_printed() for many files in one query: {sha256: when}."""
    shas = sorted(set(shas))
    if not shas:
        return {}
    rows = db.execute("SELECT i.sha256, MAX(j.printed_at) FROM items i JOIN jobs j ON j.id = i.job_id "
                      f"WHERE i.status = 'printed' AND i.sha256 IN ({','.join('?' * len(shas))}) GROUP BY i.sha256", shas)
    return {r[0]: r[1] for r in rows}


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


def get_item(db, item_id: int):
    return db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def remove(db, item_id: int) -> list[int]:
    """Take an item out of the queue. Returns the ids actually removed, so the page can offer to undo it."""
    return _take_out(db, "id = ?", (item_id,))


def clear(db) -> list[int]:
    return _take_out(db, "1 = 1", ())


def _take_out(db, where: str, args: tuple) -> list[int]:
    ids = [r[0] for r in db.execute(f"SELECT id FROM items WHERE status = 'queued' AND {where}", args)]
    db.executemany("UPDATE items SET status = 'removed' WHERE id = ?", [(i,) for i in ids])
    db.commit()
    return ids


def restore(db, item_ids: list[int]) -> int:
    """Undo remove()/clear(): put removed items back in the queue. Skips anything that was printed since, and
    any file that was queued again in the meantime (it would be there twice)."""
    n = 0
    for i in item_ids:
        it = db.execute("SELECT * FROM items WHERE id = ? AND status = 'removed'", (i,)).fetchone()
        if not it:
            continue
        if it["sha256"] and db.execute("SELECT 1 FROM items WHERE sha256 = ? AND status = 'queued'", (it["sha256"],)).fetchone():
            continue
        db.execute("UPDATE items SET status = 'queued' WHERE id = ?", (i,))
        n += 1
    db.commit()
    return n


def record_job(db, item_ids: list[int], sheet: Path, start: int, labels: int, sheets: int, mono: bool,
               cups_job: int | None = None, used_before: int | None = None) -> int:
    cur = db.execute("INSERT INTO jobs (printed_at, sheet, start, labels, sheets, mono, cups_job, state, used_before) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, 'sent', ?)",
                     (now(), str(sheet), start, labels, sheets, int(mono), cups_job, used_before))
    job = cur.lastrowid
    db.executemany("UPDATE items SET status = 'printed', job_id = ? WHERE id = ?", [(job, i) for i in item_ids])
    db.commit()
    return job


def get_setting(db, key: str, default=None):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(db, key: str, value):
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, json.dumps(value)))
    db.commit()


def get_job(db, job_id: int):
    return db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def latest_job(db):
    return db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()


def set_job_state(db, job_id: int, state: str):
    db.execute("UPDATE jobs SET state = ? WHERE id = ?", (state, job_id))
    db.commit()


def mark_undone(db, job_id: int):
    """The job's items stay attached to it for History, but stop counting as printed."""
    db.execute("UPDATE items SET status = 'undone' WHERE job_id = ? AND status = 'printed'", (job_id,))
    db.commit()


def job_items(db, job_id: int) -> list[int]:
    return [r[0] for r in db.execute("SELECT id FROM items WHERE job_id = ? ORDER BY id", (job_id,))]


def history(db, limit: int = 50, before: int | None = None):
    """[(job, its items)], newest first. `before` is a job id: only older jobs, for paging."""
    jobs = db.execute("SELECT * FROM jobs WHERE id < ? ORDER BY id DESC LIMIT ?", (before or 2**62, limit)).fetchall()
    by_job: dict[int, list] = {j["id"]: [] for j in jobs}
    marks = ",".join("?" * len(by_job))
    for it in db.execute(f"SELECT * FROM items WHERE job_id IN ({marks}) ORDER BY id", list(by_job)):
        by_job[it["job_id"]].append(it)  # one query for every job's items, not one per job
    return [(j, by_job[j["id"]]) for j in jobs]


def delete_job(db, job_id: int):
    """Forget a print. Its items stay (they were printed) but no longer say when."""
    db.execute("UPDATE items SET job_id = NULL WHERE job_id = ?", (job_id,))
    db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    db.commit()


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
