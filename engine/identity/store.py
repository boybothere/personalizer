"""Persist identity resolutions and decisions; reversible user overrides with a supersede chain."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from .. import origins, privacy
from ..store import repo
from .evidence import Subject, extract
from .resolver import Resolution, profile_key, resolve

PLATFORM_REJECTS = ("URL_PLATFORM_DISCOVERY_ONLY", "URL_PLATFORM_CONNECTOR_ONLY", "URL_PLATFORM_UNAVAILABLE")


def active_overrides(conn: sqlite3.Connection, run_id: str) -> dict[str, str]:
    rows = conn.execute("SELECT target_ref, status FROM identity_decisions WHERE run_id=? AND decided_by='user'"
                        " AND target_type='document' AND superseded_by IS NULL", (run_id,)).fetchall()
    return {r["target_ref"]: r["status"] for r in rows}


def _profile_leads(conn: sqlite3.Connection, run_id: str, subject: Subject) -> list[str]:
    """Platform leads whose URL or stored snippet carries the full name (snippet-only: at most 'possible')."""
    first, last = subject.first.lower(), subject.last.lower()
    out = []
    for r in conn.execute("SELECT l.url, l.snippet FROM leads l JOIN urls u ON u.id = l.url_id WHERE l.run_id=?"
                          f" AND u.reject_code IN ({','.join('?' * len(PLATFORM_REJECTS))})",
                          (run_id, *PLATFORM_REJECTS)):
        hay = f"{r['url']} {r['snippet'] or ''}".lower().replace("-", " ").replace("_", " ")
        if not r["url"].startswith("withheld:") and first in hay and last in hay and profile_key(r["url"]):
            out.append(r["url"])
    return sorted(set(out))


def resolve_run(conn: sqlite3.Connection, run_id: str, subject: Subject, today: date | None = None) -> int:
    """Resolve identity for a run's documents (while text is retained) and store the result."""
    groups = origins.ensure(conn, run_id)  # independence is counted by origin group (identity-rules-2)
    evidence = []
    for d in conn.execute("""SELECT d.id, d.source_url, d.title, d.text, d.metadata_json, d.published_at,
                                    d.duplicate_of, d.flags_json, u.domain, u.registrable_domain
                             FROM documents d JOIN urls u ON u.id = d.url_id
                             WHERE d.run_id=? AND d.usable=1 ORDER BY d.created_at, d.id""", (run_id,)):
        evidence.append(extract(doc_id=d["id"], url=d["source_url"], domain=d["domain"],
                                registrable=d["registrable_domain"], published_at=d["published_at"],
                                duplicate_of=d["duplicate_of"], title=d["title"], text=d["text"],
                                metadata=json.loads(d["metadata_json"]) if d["metadata_json"] else {},
                                subject=subject, origin_group=groups.get(d["id"])))
        _, named, after = privacy.removal_signal(json.loads(d["flags_json"]) if d["flags_json"] else [])
        evidence[-1].removed_named, evidence[-1].redacted_after_identity = named, after
    # carried forward: pages whose text expired, or was redacted after identity already judged the full text
    expired = {e.doc_id for e in evidence if not e.text_available or e.redacted_after_identity}
    carried = {r["target_ref"]: (r["status"], r["candidate_key"]) for r in conn.execute(
        "SELECT target_ref, status, candidate_key FROM identity_decisions WHERE run_id=? AND target_type='document'"
        " AND decided_by='rules' AND superseded_by IS NULL", (run_id,)) if r["target_ref"] in expired}
    result = resolve(subject, evidence, leads=_profile_leads(conn, run_id, subject),
                     overrides=active_overrides(conn, run_id), today=today, carried=carried)
    return save(conn, run_id, result)


def _candidate(key: str) -> str:
    kind, _, label = key.partition(":")
    return f"{kind}:{privacy.safe_label(label)}" if label else key


def _signals(sig: dict) -> dict:
    out = dict(sig)
    for k in ("other_orgs", "partial_orgs"):
        if out.get(k):
            out[k] = [privacy.safe_label(o) for o in out[k]]
    if out.get("occupation"):
        out["occupation"] = privacy.safe_label(out["occupation"])
    return out


def save(conn: sqlite3.Connection, run_id: str, r: Resolution) -> int:
    """Write boundary: organisation and occupation labels taken from page text are withheld when out of scope
    (for example a support group named as a namesake's affiliation); URLs in reasons are scrubbed."""
    now = repo.iso()
    explanation = [privacy.safe_statement(x) for x in r.explanation]
    details = {"candidates": {_candidate(k): v for k, v in r.candidates.items()},
               "conflicts": [privacy.safe_statement(x) for x in r.conflicts]}
    cur = conn.execute(
        "INSERT INTO identity_resolutions (run_id, status, resolved, explanation_json, details_json, rule_version,"
        " created_at) VALUES (?,?,?,?,?,?,?)",
        (run_id, r.status, int(r.resolved), json.dumps(explanation), json.dumps(details), r.rule_version, now))
    rid = cur.lastrowid
    conn.execute("UPDATE identity_resolutions SET superseded_by=? WHERE run_id=? AND id<>? AND superseded_by IS NULL",
                 (rid, run_id, rid))
    conn.execute("UPDATE identity_decisions SET superseded_by=? WHERE run_id=? AND decided_by='rules'"
                 " AND superseded_by IS NULL AND resolution_id<>?", (rid, run_id, rid))
    for d in r.documents:
        if d.decided_by == "user":
            continue  # the active user decision row already exists and stays authoritative
        conn.execute("INSERT INTO identity_decisions (run_id, resolution_id, target_type, target_ref, candidate_key,"
                     " status, signals_json, reasons_json, decided_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (run_id, rid, "document", d.doc_id, _candidate(d.candidate), d.status,
                      json.dumps(_signals(d.signals), default=str),
                      json.dumps([privacy.safe_statement(x) for x in d.reasons]), "rules", now))
    for p in r.profiles:
        conn.execute("INSERT INTO identity_decisions (run_id, resolution_id, target_type, target_ref, candidate_key,"
                     " status, signals_json, reasons_json, decided_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (run_id, rid, "profile", privacy.safe_url(p.url), f"{p.platform}/{p.handle}", p.status,
                      json.dumps({"sources": [privacy.safe_url(s) for s in p.sources]}),
                      json.dumps([privacy.safe_statement(x) for x in p.reasons]), "rules", now))
    repo.trace(conn, run_id, "identity", "resolved", ref_type="identity_resolution", ref_id=rid, reason=r.status,
               data={"resolved": r.resolved, "explanation": explanation[:6]})
    return rid


def override_decision(conn: sqlite3.Connection, run_id: str, doc_id: str, status: str, note: str) -> int:
    """Record a reviewer's decision for one document. Supersedes earlier user decisions for it; rule rows
    stay as history. Call resolve_run() afterwards to recompute the subject with the override applied."""
    if status not in {"confirmed", "strong", "possible", "historical", "ambiguous", "rejected", "insufficient"}:
        raise ValueError(f"unknown status {status}")
    if not note.strip():
        raise ValueError("an override needs a reason")
    cur = conn.execute("INSERT INTO identity_decisions (run_id, resolution_id, target_type, target_ref, candidate_key,"
                       " status, signals_json, reasons_json, decided_by, note, created_at)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, None, "document", doc_id, "target" if status != "rejected" else "unassigned", status,
                        "{}", json.dumps([f"user override: {note}"]), "user", note, repo.iso()))
    new_id = cur.lastrowid
    conn.execute("UPDATE identity_decisions SET superseded_by=? WHERE run_id=? AND target_type='document'"
                 " AND target_ref=? AND decided_by='user' AND id<>? AND superseded_by IS NULL",
                 (new_id, run_id, doc_id, new_id))
    repo.trace(conn, run_id, "identity", "override", ref_type="document", ref_id=doc_id, reason=note,
               data={"status": status, "decision_id": new_id})
    return new_id


def latest(conn: sqlite3.Connection, run_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM identity_resolutions WHERE run_id=? AND superseded_by IS NULL ORDER BY id DESC"
                     " LIMIT 1", (run_id,)).fetchone()
    if not r:
        return None
    docs = conn.execute("SELECT status, COUNT(*) FROM identity_decisions WHERE run_id=? AND target_type='document'"
                        " AND superseded_by IS NULL GROUP BY 1", (run_id,)).fetchall()
    profiles = conn.execute("SELECT candidate_key, status FROM identity_decisions WHERE run_id=? AND"
                            " target_type='profile' AND resolution_id=?", (run_id, r["id"])).fetchall()
    return {"status": r["status"], "resolved": bool(r["resolved"]), "rule_version": r["rule_version"],
            "explanation": json.loads(r["explanation_json"]), "details": json.loads(r["details_json"]),
            "documents_by_status": {k: v for k, v in docs}, "profiles": {k: v for k, v in profiles}}
