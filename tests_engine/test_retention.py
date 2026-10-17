"""Corrective pass: reliable retention cleanup (temporary SQLite databases, synthetic data only)."""

import sqlite3
from datetime import timedelta

import httpx
from conftest import Site, article, run

from engine.observe import metrics
from engine.store import repo
from engine.store.db import connect

SECRET = "UNIQUE-EXPIRED-SENTENCE-0123456789 "
TITLE = "UNIQUE-EXPIRED-TITLE-9876543210"


def _doc(conn, text=SECRET * 3, title=TITLE):
    rid = repo.create_run(conn, "retrieval", {}, {}, run_days=30)
    uid = repo.add_url(conn, rid, url_key=f"u{rid}", canonical_url="https://x.example/a", domain="x.example",
                       registrable_domain="x.example", platform=None, decision="accepted", reject_code=None,
                       reject_detail=None)
    repo.add_document(conn, rid, uid, None, {"evidence_kind": "fetched_page", "source_url": "https://x.example/a",
                                             "extractor": "t", "text": text, "title": title, "usable": True},
                      text_days=7)
    return rid


def test_blocked_checkpoint_is_retried_even_when_nothing_new_expires(tmp_path):
    db = tmp_path / "c.db"
    conn = connect(db)
    _doc(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    reader = sqlite3.connect(str(db))
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM documents").fetchone()
    first = repo.purge(conn, repo.now() + timedelta(days=8))
    assert first["texts_cleared"] == 1 and first["checkpoint_complete"] is False
    assert SECRET.encode() in db.read_bytes()  # logically deleted, still physically in the main file
    reader.rollback()
    reader.close()
    retry = repo.purge(conn, repo.now() + timedelta(days=8))
    assert retry["texts_cleared"] == 0 and retry["checkpoint_complete"] is True  # nothing new expired; retried
    for f in tmp_path.iterdir():  # verified before the connection closes (closing would also checkpoint)
        assert SECRET.encode() not in f.read_bytes() and TITLE.encode() not in f.read_bytes(), f.name
    conn.close()


def test_titles_are_cleared_with_text_at_the_7_day_expiry(tmp_path):
    conn = connect(tmp_path / "t.db")
    _doc(conn)
    _doc(conn, text=None, title=TITLE + "-ONLY")  # a title without usable text also expires
    assert repo.purge(conn, repo.now() + timedelta(days=6))["texts_cleared"] == 0
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE title IS NOT NULL").fetchone()[0] == 2
    assert repo.purge(conn, repo.now() + timedelta(days=8))["texts_cleared"] == 2
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE title IS NOT NULL OR text IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE text_sha256 IS NULL").fetchone()[0] == 2  # (none set)
    assert repo.purge(conn, repo.now() + timedelta(days=31))["runs_deleted"] == 2  # 30-day run retention unchanged
    for f in tmp_path.iterdir():
        assert TITLE.encode() not in f.read_bytes()
    conn.close()


def test_purge_outcome_is_recorded_on_the_next_run(tmp_path):
    db = tmp_path / "m.db"
    conn = connect(db)
    old = _doc(conn)
    conn.execute("UPDATE documents SET text_expires_at=? WHERE run_id=?", (repo.iso(repo.now() - timedelta(days=1)),
                                                                          old))
    site = Site({"/p": httpx.Response(200, html=article("In-scope text about GPU capacity planning."))})
    _, rid = run(site, ["https://ok.example/p"], conn=conn)
    m = metrics(conn, rid)["maintenance"]
    assert m["available"] and m["status"] == "ok" and m["purge"]["texts_cleared"] == 1
    assert m["purge"]["checkpoint_complete"] is True
    conn.close()


def test_blocked_checkpoint_is_visible_in_run_metrics(tmp_path):
    db = tmp_path / "b.db"
    conn = connect(db)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _doc(conn)  # leaves pages in the WAL
    reader = sqlite3.connect(str(db))
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM documents").fetchone()
    site = Site({"/p": httpx.Response(200, html=article("In-scope text about GPU capacity planning."))})
    _, rid = run(site, ["https://ok.example/p"], conn=conn)
    reader.rollback()
    reader.close()
    m = metrics(conn, rid)["maintenance"]
    assert m["status"] == "checkpoint_incomplete" and m["purge"]["checkpoint_complete"] is False
    conn.close()
