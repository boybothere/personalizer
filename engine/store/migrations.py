"""Versioned, reversible migrations. Each migration has `up` and `down` SQL and runs in one transaction.

Never edit an applied migration; add a new one. Downgrades are destructive for the tables they
drop, so the CLI requires an explicit target version.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: str
    down: str


M0001_UP = """
CREATE TABLE subjects (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    subject_id TEXT REFERENCES subjects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    stop_reason TEXT,
    input_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    gates_json TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    expires_at TEXT NOT NULL
);
CREATE TABLE stage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    seconds REAL,
    outcome TEXT
);
CREATE TABLE trace_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    stage TEXT NOT NULL,
    action TEXT NOT NULL,
    ref_type TEXT,
    ref_id TEXT,
    reason TEXT,
    data_json TEXT
);
CREATE TABLE urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url_key TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    domain TEXT NOT NULL,
    registrable_domain TEXT NOT NULL,
    platform TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
    reject_code TEXT,
    reject_detail TEXT,
    first_seen_at TEXT NOT NULL,
    UNIQUE (run_id, url_key)
);
CREATE TABLE leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    url_id INTEGER REFERENCES urls(id) ON DELETE SET NULL,
    lead_kind TEXT NOT NULL CHECK (lead_kind IN ('seed_url', 'search_result', 'connector_link', 'discovered_link')),
    origin_ref TEXT,
    provider TEXT,
    query_text TEXT,
    rank INTEGER,
    snippet TEXT,
    snippet_limitations TEXT,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    observed_at TEXT NOT NULL
);
CREATE TABLE robots_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    origin TEXT NOT NULL,
    http_status INTEGER,
    result TEXT NOT NULL CHECK (result IN ('parsed', 'allow_all', 'disallow_all')),
    failure_code TEXT,
    detail TEXT,
    crawl_delay REAL,
    checked_at TEXT NOT NULL
);
CREATE TABLE fetch_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url_id INTEGER NOT NULL REFERENCES urls(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    request_url TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'redirect', 'retry', 'failure', 'skipped')),
    http_status INTEGER,
    content_type TEXT,
    bytes INTEGER NOT NULL DEFAULT 0,
    elapsed_ms INTEGER,
    failure_code TEXT,
    detail TEXT,
    started_at TEXT NOT NULL
);
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url_id INTEGER NOT NULL REFERENCES urls(id) ON DELETE CASCADE,
    fetch_attempt_id INTEGER REFERENCES fetch_attempts(id) ON DELETE SET NULL,
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN
        ('fetched_page', 'feed_entry', 'api_record', 'oembed_record', 'provider_extract')),
    source_url TEXT NOT NULL,
    canonical_link TEXT,
    content_type TEXT,
    title TEXT,
    author TEXT,
    site_name TEXT,
    language TEXT,
    published_at TEXT,
    published_source TEXT,
    date_candidates_json TEXT,
    date_conflict INTEGER NOT NULL DEFAULT 0,
    modified_at TEXT,
    text_chars INTEGER NOT NULL,
    text_sha256 TEXT,
    simhash TEXT,
    usable INTEGER NOT NULL,
    failure_code TEXT,
    duplicate_of TEXT REFERENCES documents(id) ON DELETE SET NULL,
    extractor TEXT NOT NULL,
    flags_json TEXT,
    metadata_json TEXT,
    text TEXT,
    text_expires_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE provider_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    units REAL NOT NULL,
    cost_usd REAL NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX idx_urls_run ON urls(run_id);
CREATE INDEX idx_leads_run ON leads(run_id);
CREATE INDEX idx_attempts_run ON fetch_attempts(run_id, url_id);
CREATE INDEX idx_documents_run ON documents(run_id);
CREATE INDEX idx_trace_run ON trace_events(run_id);
"""

M0001_DOWN = """
DROP TABLE provider_usage;
DROP TABLE documents;
DROP TABLE fetch_attempts;
DROP TABLE robots_checks;
DROP TABLE leads;
DROP TABLE urls;
DROP TABLE trace_events;
DROP TABLE stage_events;
DROP TABLE runs;
DROP TABLE subjects;
"""

M0002_UP = """
CREATE TABLE search_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    purpose TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    rendered_json TEXT,
    rendering_notes TEXT,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed', 'skipped')),
    failure_code TEXT,
    http_status INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    results_returned INTEGER NOT NULL DEFAULT 0,
    results_off_target INTEGER NOT NULL DEFAULT 0,
    new_urls INTEGER NOT NULL DEFAULT 0,
    request_id TEXT,
    storage_policy TEXT NOT NULL CHECK (storage_policy IN ('persist', 'transient')),
    started_at TEXT NOT NULL
);
CREATE TABLE search_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    query_id INTEGER NOT NULL REFERENCES search_queries(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    rank INTEGER NOT NULL,
    url TEXT,
    url_key TEXT NOT NULL,
    domain TEXT,
    title TEXT,
    snippet TEXT,
    provider_date TEXT,
    off_target INTEGER NOT NULL DEFAULT 0,
    lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    withheld INTEGER NOT NULL DEFAULT 0,
    text_expires_at TEXT
);
CREATE TABLE provider_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    unit TEXT NOT NULL,
    units REAL NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    reported_cost_usd REAL,
    run_id TEXT,
    at TEXT NOT NULL
);
ALTER TABLE provider_usage ADD COLUMN query_id INTEGER;
ALTER TABLE provider_usage ADD COLUMN unit TEXT;
ALTER TABLE provider_usage ADD COLUMN reported_cost_usd REAL;
ALTER TABLE provider_usage ADD COLUMN detail_json TEXT;
ALTER TABLE leads ADD COLUMN snippet_expires_at TEXT;
ALTER TABLE leads ADD COLUMN storage_policy TEXT;
CREATE INDEX idx_sq_run ON search_queries(run_id);
CREATE INDEX idx_sr_run ON search_results(run_id, url_key);
CREATE INDEX idx_ledger ON provider_ledger(provider, at)
"""

M0002_DOWN = """
DROP INDEX idx_ledger;
DROP INDEX idx_sr_run;
DROP INDEX idx_sq_run;
ALTER TABLE leads DROP COLUMN storage_policy;
ALTER TABLE leads DROP COLUMN snippet_expires_at;
ALTER TABLE provider_usage DROP COLUMN detail_json;
ALTER TABLE provider_usage DROP COLUMN reported_cost_usd;
ALTER TABLE provider_usage DROP COLUMN unit;
ALTER TABLE provider_usage DROP COLUMN query_id;
DROP TABLE provider_ledger;
DROP TABLE search_results;
DROP TABLE search_queries
"""

M0003_UP = """
CREATE TABLE research_objectives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    category TEXT NOT NULL,
    priority REAL NOT NULL,
    target_docs INTEGER NOT NULL,
    target_domains INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'covered', 'saturated', 'exhausted', 'blocked')),
    status_reason TEXT,
    queries_run INTEGER NOT NULL DEFAULT 0,
    relevant_docs INTEGER NOT NULL DEFAULT 0,
    domains INTEGER NOT NULL DEFAULT 0,
    profile_leads INTEGER NOT NULL DEFAULT 0,
    low_yield_streak INTEGER NOT NULL DEFAULT 0,
    variants_json TEXT NOT NULL,
    created_reason TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, key)
);
CREATE TABLE planner_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step INTEGER NOT NULL,
    objective_key TEXT,
    query_ids_json TEXT,
    spec_json TEXT,
    selection_reason TEXT,
    scores_json TEXT,
    contribution_json TEXT,
    followups_json TEXT,
    identity_signal TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('continue', 'stop')),
    decision_reason TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX idx_pd_run ON planner_decisions(run_id, step)
"""

M0003_DOWN = """
DROP INDEX idx_pd_run;
DROP TABLE planner_decisions;
DROP TABLE research_objectives
"""

M0004_UP = """
ALTER TABLE fetch_attempts ADD COLUMN connected_ip TEXT
"""

M0004_DOWN = """
ALTER TABLE fetch_attempts DROP COLUMN connected_ip
"""

M0005_UP = """
CREATE TABLE identity_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'strongly_supported', 'possible', 'conflicting',
                                           'insufficient_evidence')),
    resolved INTEGER NOT NULL,
    explanation_json TEXT NOT NULL,
    details_json TEXT,
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded_by INTEGER
);
CREATE TABLE identity_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    resolution_id INTEGER REFERENCES identity_resolutions(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK (target_type IN ('document', 'profile')),
    target_ref TEXT NOT NULL,
    candidate_key TEXT,
    status TEXT NOT NULL,
    signals_json TEXT,
    reasons_json TEXT,
    decided_by TEXT NOT NULL CHECK (decided_by IN ('rules', 'user')),
    note TEXT,
    created_at TEXT NOT NULL,
    superseded_by INTEGER
);
CREATE INDEX idx_idd_run ON identity_decisions(run_id, target_type, target_ref)
"""

M0005_DOWN = """
DROP INDEX idx_idd_run;
DROP TABLE identity_decisions;
DROP TABLE identity_resolutions
"""

MIGRATIONS: list[Migration] = [
    Migration(1, "observability_and_retrieval", M0001_UP, M0001_DOWN),
    Migration(2, "search_layer_and_provider_ledger", M0002_UP, M0002_DOWN),
    Migration(3, "adaptive_planner", M0003_UP, M0003_DOWN),
    Migration(4, "fetch_connected_ip", M0004_UP, M0004_DOWN),
    Migration(5, "identity_resolution", M0005_UP, M0005_DOWN),
]

_BOOKKEEPING = """CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"""


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(_BOOKKEEPING)
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0


def _run_script(conn: sqlite3.Connection, script: str, bookkeeping: str, params: tuple) -> None:
    # sqlite3's executescript commits first, so the transaction is managed explicitly.
    conn.execute("PRAGMA foreign_keys = OFF")  # drops in down() must not cascade mid-script
    try:
        conn.execute("BEGIN")
        for statement in (s.strip() for s in script.split(";")):
            if statement:
                conn.execute(statement)
        conn.execute(bookkeeping, params)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def migrate(conn: sqlite3.Connection, target: int | None = None,
            migrations: list[Migration] | None = None) -> list[str]:
    """Move the schema to `target` (default: latest). Returns the steps performed."""
    migrations = sorted(migrations or MIGRATIONS, key=lambda m: m.version)
    target = migrations[-1].version if target is None else target
    known = {m.version for m in migrations} | {0}
    if target not in known:
        raise ValueError(f"unknown target version {target}")
    current = current_version(conn)
    steps = []
    now = datetime.now(timezone.utc).isoformat()
    if target > current:
        for m in migrations:
            if current < m.version <= target:
                _run_script(conn, m.up, "INSERT INTO schema_migrations VALUES (?, ?, ?)", (m.version, m.name, now))
                steps.append(f"up {m.version} {m.name}")
    elif target < current:
        for m in reversed(migrations):
            if target < m.version <= current:
                _run_script(conn, m.down, "DELETE FROM schema_migrations WHERE version = ?", (m.version,))
                steps.append(f"down {m.version} {m.name}")
    return steps
