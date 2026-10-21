import sqlite3
import threading

import pytest

from rmlabels import store

ADD_CUPS = (("ALTER TABLE jobs ADD COLUMN cups_job INTEGER",), ("ALTER TABLE jobs ADD COLUMN state TEXT",))


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(store, "DB_PATH", path)
    return path


def columns(db, table):
    return [r[1] for r in db.execute(f"PRAGMA table_info({table})")]


def test_fresh_database_is_current_and_uses_wal(db_path, monkeypatch):
    monkeypatch.setattr(store, "MIGRATIONS", list(ADD_CUPS))
    with store.session() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert {"cups_job", "state"} <= set(columns(db, "jobs"))


def test_old_database_migrates_without_losing_rows(db_path, monkeypatch):
    old = sqlite3.connect(db_path)                         # exactly what the app created before migrations existed
    old.executescript(store.SCHEMA)
    old.execute("INSERT INTO jobs (printed_at, sheet, start, labels, sheets, mono) VALUES ('2026-10-20T17:00:00', '/x.pdf', 1, 2, 1, 1)")
    old.execute("INSERT INTO items (kind, name, source, added_at, status, job_id) VALUES ('text', 'FRAGILE', 'upload', 'now', 'printed', 1)")
    old.commit()
    old.close()
    monkeypatch.setattr(store, "MIGRATIONS", list(ADD_CUPS))
    with store.session() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        job = db.execute("SELECT * FROM jobs").fetchone()
        assert (job["labels"], job["sheet"], job["cups_job"], job["state"]) == (2, "/x.pdf", None, None)
        assert db.execute("SELECT name, status FROM items").fetchone()[:] == ("FRAGILE", "printed")


def test_migrations_run_once_and_tolerate_a_second_runner(db_path, monkeypatch):
    monkeypatch.setattr(store, "MIGRATIONS", list(ADD_CUPS))
    a, b = store.connect(), store.connect()               # e.g. the web server and the CLI starting together
    store.migrate(a)
    store.migrate(b)                                      # must not fail with "duplicate column"
    assert columns(a, "jobs").count("cups_job") == 1
    a.close()
    b.close()


def test_failed_migration_rolls_back(db_path, monkeypatch):
    monkeypatch.setattr(store, "MIGRATIONS", [("ALTER TABLE jobs ADD COLUMN ok INTEGER", "ALTER TABLE nope ADD COLUMN x")])
    with pytest.raises(sqlite3.OperationalError):
        store.connect()
    store._migrated.discard(db_path)
    db = sqlite3.connect(db_path)
    assert "ok" not in columns(db, "jobs") and db.execute("PRAGMA user_version").fetchone()[0] == 0


def test_connection_can_be_used_from_another_thread(db_path):
    db = store.connect()
    seen = []
    t = threading.Thread(target=lambda: seen.append(db.execute("SELECT COUNT(*) FROM items").fetchone()[0]))
    t.start()
    t.join()
    db.close()
    assert seen == [0]
