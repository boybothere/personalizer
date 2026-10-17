"""Canonicalisation, URL policy, platform registry, migrations, retention and deletion."""

import sqlite3
from datetime import timedelta

import pytest

from engine.config import EngineConfig, Platforms
from engine.failures import EXPLANATIONS, Code
from engine.platforms import REGISTRY
from engine.store import repo
from engine.store.db import connect
from engine.store.migrations import MIGRATIONS, Migration, current_version, migrate
from engine.urls import canonicalize, check


@pytest.mark.parametrize("a,b", [
    ("https://www.example.com/a/b/?utm_source=x&b=2&a=1#frag", "http://example.com/a/b?a=1&b=2"),
    ("https://example.com:443/x", "https://example.com/x"),
    ("http://twitter.com/jack/status/20", "https://x.com/jack/status/20"),
    ("https://youtu.be/dQw4w9WgXcQ?si=tracking", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://EXAMPLE.com/Caf%C3%A9", "https://example.com/Café"),
])
def test_equivalent_urls_share_a_key(a, b):
    assert canonicalize(a).key == canonicalize(b).key


@pytest.mark.parametrize("a,b", [
    ("https://example.com/a", "https://example.com/b"),
    ("https://example.com/a?id=1", "https://example.com/a?id=2"),
    ("https://example.com:8080/a", "https://example.com/a"),
])
def test_different_urls_have_different_keys(a, b):
    assert canonicalize(a).key != canonicalize(b).key


def test_registrable_domain_uses_public_suffix_list():
    assert canonicalize("https://news.bbc.co.uk/x").registrable == "bbc.co.uk"
    assert canonicalize("https://blog.example.com/x").registrable == "example.com"


@pytest.mark.parametrize("url,code", [
    ("ftp://example.org/", Code.URL_UNSUPPORTED_SCHEME),
    ("https://user:pw@example.org/", Code.URL_CREDENTIALS),
    ("http://127.0.0.1/", Code.URL_PRIVATE_ADDRESS),
    ("http://169.254.169.254/latest/meta-data", Code.URL_PRIVATE_ADDRESS),
    ("http://localhost:8000/", Code.URL_PRIVATE_ADDRESS),
    ("https://www.spokeo.com/Jane-Doe", Code.URL_EXCLUDED_DOMAIN),
    ("https://www.instagram.com/p/abc/", Code.URL_PLATFORM_DISCOVERY_ONLY),
    ("https://www.linkedin.com/in/someone", Code.URL_PLATFORM_DISCOVERY_ONLY),
    ("https://x.com/jack/status/20", Code.URL_PLATFORM_CONNECTOR_ONLY),
    ("https://www.reddit.com/r/x/comments/1/", Code.URL_PLATFORM_CONNECTOR_ONLY),
    ("https://www.tiktok.com/@a/video/1", Code.URL_PLATFORM_UNAVAILABLE),
    ("not a url", Code.URL_INVALID),
])
def test_static_policy_codes(url, code):
    _, decision = check(url, Platforms())
    assert not decision.allowed and decision.code == code


def test_connector_platform_reports_switch_state():
    _, off = check("https://x.com/a/status/1", Platforms())
    _, on = check("https://x.com/a/status/1", Platforms(x_oembed_enabled=True))
    assert "connector disabled" in off.detail and "connector enabled" in on.detail
    assert not on.allowed  # pages are never fetched; content only through the connector


def test_every_platform_mode_has_evidence_and_probe_date():
    for p in REGISTRY:
        assert p.evidence and p.probed and p.modes


def test_every_failure_code_is_explained():
    assert set(EXPLANATIONS) == set(Code)


# ---------------------------------------------------------------- migrations


HEAD = MIGRATIONS[-1].version
NEXT = HEAD + 1


def test_migrations_are_reversible():
    conn = connect(":memory:")
    assert current_version(conn) == HEAD
    assert migrate(conn, 0) == [f"down {m.version} {m.name}" for m in reversed(MIGRATIONS)]
    assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} <= {
        "schema_migrations", "sqlite_sequence"}
    assert migrate(conn) == [f"up {m.version} {m.name}" for m in MIGRATIONS]
    repo.create_subject(conn, "x")  # schema usable after re-upgrade


def test_each_migration_steps_down_and_up_individually():
    conn = connect(":memory:")
    for m in reversed(MIGRATIONS):
        assert migrate(conn, m.version - 1) == [f"down {m.version} {m.name}"]
    for m in MIGRATIONS:
        assert migrate(conn, m.version) == [f"up {m.version} {m.name}"]


def test_downgrading_a_later_migration_preserves_earlier_data():
    extra = Migration(NEXT, "test_extra", "CREATE TABLE extra (id INTEGER PRIMARY KEY);"
                                          "ALTER TABLE subjects ADD COLUMN note TEXT",
                      "ALTER TABLE subjects DROP COLUMN note; DROP TABLE extra")
    conn = connect(":memory:")
    sid = repo.create_subject(conn, "Keep me")
    migrate(conn, NEXT, MIGRATIONS + [extra])
    assert current_version(conn) == NEXT
    migrate(conn, HEAD, MIGRATIONS + [extra])
    assert conn.execute("SELECT display_name FROM subjects WHERE id=?", (sid,)).fetchone()[0] == "Keep me"
    assert "note" not in [r[1] for r in conn.execute("PRAGMA table_info(subjects)")]


def test_failed_migration_rolls_back():
    broken = Migration(NEXT, "broken", "CREATE TABLE ok_table (id INTEGER); THIS IS NOT SQL", "DROP TABLE ok_table")
    conn = connect(":memory:")
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, NEXT, MIGRATIONS + [broken])
    assert current_version(conn) == HEAD
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ok_table'").fetchone()


def test_engine_db_is_separate_from_prototype_db():
    assert EngineConfig().db_path.name == "engine.db"


# ---------------------------------------------------------------- retention and deletion


def _seed(conn):
    sid = repo.create_subject(conn, "Subject")
    rid = repo.create_run(conn, "retrieval", {}, {}, run_days=30, subject_id=sid)
    uid = repo.add_url(conn, rid, url_key="k", canonical_url="https://e.org/", domain="e.org",
                       registrable_domain="e.org", platform=None, decision="accepted", reject_code=None,
                       reject_detail=None)
    aid = repo.add_attempt(conn, rid, uid, {"attempt_no": 1, "request_url": "https://e.org/", "outcome": "success",
                                           "http_status": 200})
    did = repo.add_document(conn, rid, uid, aid, {"evidence_kind": "fetched_page", "source_url": "https://e.org/",
                                                  "usable": True, "text_chars": 4, "extractor": "t", "text": "text"},
                            text_days=7)
    return sid, rid, did


def test_delete_subject_cascades_everything():
    conn = connect(":memory:")
    sid, rid, _ = _seed(conn)
    assert repo.delete_subject(conn, sid)
    for table in ("runs", "urls", "fetch_attempts", "documents"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_purge_clears_text_then_deletes_expired_runs():
    conn = connect(":memory:")
    _, rid, did = _seed(conn)
    out = repo.purge(conn, repo.now() + timedelta(days=8))
    assert out == {"runs_deleted": 0, "texts_cleared": 1, "snippets_cleared": 0}
    row = conn.execute("SELECT text, text_chars FROM documents WHERE id=?", (did,)).fetchone()
    assert row["text"] is None and row["text_chars"] == 4  # hashes/metadata kept
    assert repo.purge(conn, repo.now() + timedelta(days=31))["runs_deleted"] == 1


def test_referral_ref_param_is_tracking_but_git_refs_are_kept():
    # live run: github.com/adbar/trafilatura?ref=blog.republicofdata.io was counted as a separate URL
    assert canonicalize("https://github.com/adbar/trafilatura?ref=blog.republicofdata.io").key == \
        canonicalize("https://github.com/adbar/trafilatura").key
    assert canonicalize("https://github.com/a/b/blob/x?ref=main").key != canonicalize("https://github.com/a/b/blob/x").key
