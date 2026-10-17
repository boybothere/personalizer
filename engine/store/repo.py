"""Write and read helpers. All persistence goes through here so every action leaves a record."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------- subjects and runs


def create_subject(conn: sqlite3.Connection, display_name: str) -> str:
    sid = new_id("subj")
    conn.execute("INSERT INTO subjects VALUES (?, ?, ?)", (sid, display_name, iso()))
    return sid


def create_run(conn: sqlite3.Connection, kind: str, input_data: dict, config: dict, run_days: int,
               subject_id: str | None = None) -> str:
    rid = new_id("run")
    created = now()
    conn.execute(
        "INSERT INTO runs (id, subject_id, kind, status, input_json, config_json, created_at, started_at, expires_at)"
        " VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?)",
        (rid, subject_id, kind, _json(input_data), _json(config), iso(created), iso(created),
         iso(created + timedelta(days=run_days))),
    )
    return rid


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, stop_reason: str, gates: list[dict]) -> None:
    conn.execute("UPDATE runs SET status = ?, stop_reason = ?, gates_json = ?, finished_at = ? WHERE id = ?",
                 (status, stop_reason, _json(gates), iso(), run_id))


def request_cancel(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("UPDATE runs SET cancel_requested = 1 WHERE id = ?", (run_id,))


def cancel_requested(conn: sqlite3.Connection, run_id: str) -> bool:
    row = conn.execute("SELECT cancel_requested FROM runs WHERE id = ?", (run_id,)).fetchone()
    return bool(row and row[0])


def get_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    for k in ("input_json", "config_json", "gates_json"):
        out[k.removesuffix("_json")] = json.loads(out.pop(k)) if out.get(k) else None
    return out


# ---------------------------------------------------------------- observability


def stage_start(conn: sqlite3.Connection, run_id: str, stage: str) -> int:
    cur = conn.execute("INSERT INTO stage_events (run_id, stage, started_at) VALUES (?, ?, ?)", (run_id, stage, iso()))
    return cur.lastrowid


def stage_end(conn: sqlite3.Connection, event_id: int, seconds: float, outcome: str) -> None:
    conn.execute("UPDATE stage_events SET ended_at = ?, seconds = ?, outcome = ? WHERE id = ?",
                 (iso(), round(seconds, 4), outcome, event_id))


def trace(conn: sqlite3.Connection, run_id: str, stage: str, action: str, *, ref_type: str | None = None,
          ref_id: Any = None, reason: str | None = None, data: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO trace_events (run_id, at, stage, action, ref_type, ref_id, reason, data_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, iso(), stage, action, ref_type, None if ref_id is None else str(ref_id), reason, _json(data)),
    )


def add_provider_usage(conn: sqlite3.Connection, run_id: str, provider: str, operation: str,
                       units: float, cost_usd: float) -> None:
    conn.execute("INSERT INTO provider_usage (run_id, provider, operation, units, cost_usd, at) VALUES (?,?,?,?,?,?)",
                 (run_id, provider, operation, units, cost_usd, iso()))


# ---------------------------------------------------------------- leads and URLs


def add_lead(conn: sqlite3.Connection, run_id: str, url: str, lead_kind: str, **fields) -> int:
    cols = ["run_id", "url", "lead_kind", "observed_at"] + list(fields)
    vals = [run_id, url, lead_kind, iso()] + [v if not isinstance(v, (list, dict)) else _json(v) for v in fields.values()]
    cur = conn.execute(f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    return cur.lastrowid


def find_url(conn: sqlite3.Connection, run_id: str, url_key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM urls WHERE run_id = ? AND url_key = ?", (run_id, url_key)).fetchone()


def add_url(conn: sqlite3.Connection, run_id: str, *, url_key: str, canonical_url: str, domain: str,
            registrable_domain: str, platform: str | None, decision: str, reject_code: str | None,
            reject_detail: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO urls (run_id, url_key, canonical_url, domain, registrable_domain, platform, decision,"
        " reject_code, reject_detail, first_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, url_key, canonical_url, domain, registrable_domain, platform, decision, reject_code,
         reject_detail, iso()),
    )
    return cur.lastrowid


def link_lead(conn: sqlite3.Connection, lead_id: int, url_id: int, duplicate: bool) -> None:
    conn.execute("UPDATE leads SET url_id = ?, is_duplicate = ? WHERE id = ?", (url_id, int(duplicate), lead_id))


# ---------------------------------------------------------------- retrieval


def add_robots_check(conn: sqlite3.Connection, run_id: str, origin: str, http_status: int | None, result: str,
                     failure_code: str | None, detail: str | None, crawl_delay: float | None) -> None:
    conn.execute(
        "INSERT INTO robots_checks (run_id, origin, http_status, result, failure_code, detail, crawl_delay, checked_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (run_id, origin, http_status, result, failure_code, detail, crawl_delay, iso()),
    )


def add_attempt(conn: sqlite3.Connection, run_id: str, url_id: int, a: dict) -> int:
    cur = conn.execute(
        "INSERT INTO fetch_attempts (run_id, url_id, attempt_no, request_url, outcome, http_status, content_type,"
        " bytes, elapsed_ms, failure_code, detail, started_at, connected_ip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, url_id, a["attempt_no"], a["request_url"], a["outcome"], a.get("http_status"),
         a.get("content_type"), a.get("bytes", 0), a.get("elapsed_ms"), a.get("failure_code"), a.get("detail"),
         a.get("started_at") or iso(), a.get("connected_ip")),
    )
    return cur.lastrowid


def add_document(conn: sqlite3.Connection, run_id: str, url_id: int, fetch_attempt_id: int | None, d: dict,
                 text_days: int) -> str:
    did = new_id("doc")
    created = now()
    conn.execute(
        "INSERT INTO documents (id, run_id, url_id, fetch_attempt_id, evidence_kind, source_url, canonical_link,"
        " content_type, title, author, site_name, language, published_at, published_source, date_candidates_json,"
        " date_conflict, modified_at, text_chars, text_sha256, simhash, usable, failure_code, extractor, flags_json,"
        " metadata_json, text, text_expires_at, created_at) VALUES"
        " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (did, run_id, url_id, fetch_attempt_id, d["evidence_kind"], d["source_url"], d.get("canonical_link"),
         d.get("content_type"), d.get("title"), d.get("author"), d.get("site_name"), d.get("language"),
         d.get("published_at"), d.get("published_source"), _json(d.get("date_candidates")),
         int(bool(d.get("date_conflict"))), d.get("modified_at"), d.get("text_chars", 0), d.get("text_sha256"),
         d.get("simhash"), int(bool(d.get("usable"))), d.get("failure_code"), d["extractor"],
         _json(d.get("flags")), _json(d.get("metadata")), d.get("text"),
         iso(created + timedelta(days=text_days)) if d.get("text") else None, iso(created)),
    )
    return did


def mark_duplicate(conn: sqlite3.Connection, doc_id: str, duplicate_of: str) -> None:
    conn.execute("UPDATE documents SET duplicate_of = ? WHERE id = ?", (duplicate_of, doc_id))


# ---------------------------------------------------------------- retention and deletion


def _checkpoint(conn: sqlite3.Connection) -> bool:
    """Copy the WAL into the main file and truncate it. With WAL, old page images (with deleted or expired text)
    stay in the -wal file until a checkpoint; secure_delete covers the main file. Returns False when a reader
    blocked it (busy): the old content must then be treated as still on disk until a later checkpoint succeeds."""
    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    return row is None or row[0] == 0


def delete_run(conn: sqlite3.Connection, run_id: str) -> bool:
    deleted = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,)).rowcount > 0
    _checkpoint(conn)
    return deleted


def delete_subject(conn: sqlite3.Connection, subject_id: str) -> bool:
    deleted = conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,)).rowcount > 0
    _checkpoint(conn)
    return deleted


def purge(conn: sqlite3.Connection, at: datetime | None = None) -> dict:
    """Delete expired runs; null out document text past its expiry (hashes and metadata stay)."""
    t = iso(at)
    texts = conn.execute("UPDATE documents SET text = NULL WHERE text IS NOT NULL AND text_expires_at < ?",
                         (t,)).rowcount
    snippets = conn.execute("UPDATE leads SET snippet = NULL WHERE snippet IS NOT NULL AND snippet_expires_at < ?",
                            (t,)).rowcount
    snippets += conn.execute("UPDATE search_results SET snippet = NULL, title = NULL WHERE (snippet IS NOT NULL"
                             " OR title IS NOT NULL) AND text_expires_at < ?", (t,)).rowcount
    evidence = 0
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claim_evidence'").fetchone():
        evidence = conn.execute("UPDATE claim_evidence SET evidence_text = NULL WHERE evidence_text IS NOT NULL"
                                " AND text_expires_at < ?", (t,)).rowcount
    runs = conn.execute("DELETE FROM runs WHERE expires_at < ?", (t,)).rowcount
    out = {"runs_deleted": runs, "texts_cleared": texts, "snippets_cleared": snippets, "evidence_cleared": evidence}
    if texts or snippets or runs or evidence:
        out["checkpoint_complete"] = _checkpoint(conn)
    return out
