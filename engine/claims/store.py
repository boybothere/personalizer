"""Identity-gated claim extraction and storage (PHASE6_PLAN.md §2.4, §2.4.1, §2.6).

Claims are made only when the subject is resolved, and only from documents whose identity status is confirmed,
strong or historical. Namesake and rejected pages are never read. When identity changes, dependent claims are
superseded (never silently deleted) and recomputed from retained text; if the text has expired, the run says so.
Trace rows hold counts and reason codes only, never sentences.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date

from .. import origins, privacy
from ..identity.evidence import Subject
from ..store import repo
from .extract import RULES, DocInput, extract_doc
from .status import build

GATED_DOC_STATUSES = ("confirmed", "strong", "historical")


def _latest_resolution(conn: sqlite3.Connection, run_id: str):
    return conn.execute("SELECT * FROM identity_resolutions WHERE run_id=? AND superseded_by IS NULL ORDER BY id DESC"
                        " LIMIT 1", (run_id,)).fetchone()


def _doc_statuses(conn: sqlite3.Connection, run_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in conn.execute("SELECT target_ref, status FROM identity_decisions WHERE run_id=? AND target_type='document'"
                          " AND superseded_by IS NULL ORDER BY id", (run_id,)):
        out[r["target_ref"]] = r["status"]  # later rows win (a user decision is newer than the rule row it replaced)
    return out


def _supersede(conn, run_id: str, resolution_id: int, reason: str, ids: list[int] | None = None) -> int:
    if ids is None:
        return conn.execute("UPDATE claims SET superseded_by=?, supersede_reason=? WHERE run_id=? AND"
                            " superseded_by IS NULL", (resolution_id, reason, run_id)).rowcount
    n = 0
    for cid in ids:
        n += conn.execute("UPDATE claims SET superseded_by=?, supersede_reason=? WHERE id=? AND superseded_by IS NULL",
                          (resolution_id, reason, cid)).rowcount
    return n


def update_claims(conn: sqlite3.Connection, run_id: str, subject: Subject, today: date | None = None,
                  settings=None) -> dict:
    today = today or date.today()
    stale = settings.stale_after_days if settings else 730
    res = _latest_resolution(conn, run_id)
    if res is None:
        repo.trace(conn, run_id, "claims", "withheld", reason="no_identity_resolution")
        return {"claims": 0, "withheld": "no_identity_resolution"}
    groups = origins.ensure(conn, run_id, settings)
    statuses = _doc_statuses(conn, run_id)
    rows = conn.execute("""SELECT d.id, d.source_url, d.text, d.author, d.published_at, d.published_source,
                                  d.date_conflict, d.evidence_kind, d.text_expires_at, u.registrable_domain
                           FROM documents d JOIN urls u ON u.id = d.url_id
                           WHERE d.run_id=? AND d.usable=1 ORDER BY d.created_at, d.id""", (run_id,)).fetchall()
    prev = conn.execute("SELECT * FROM claims WHERE run_id=? AND superseded_by IS NULL ORDER BY id",
                        (run_id,)).fetchall()
    prev_gate, had_extraction = _previous_gate(conn, run_id)
    if not res["resolved"]:
        n = _supersede(conn, run_id, res["id"], "identity_changed")
        repo.trace(conn, run_id, "claims", "withheld", reason="identity_unresolved",
                   data={"subject_status": res["status"], "documents": len(rows), "claims_superseded": n, "gate": {}})
        return {"claims": 0, "withheld": "identity_unresolved", "subject_status": res["status"],
                "claims_superseded": n}

    gated = [r for r in rows if statuses.get(r["id"]) in GATED_DOC_STATUSES]
    gated_set = {r["id"] for r in gated}
    gate = {r["id"]: statuses[r["id"]] for r in gated}
    withheld: dict[str, int] = {}
    for r in rows:
        if r["id"] not in gated_set:
            k = f"document_identity_{statuses.get(r['id'], 'undecided')}"
            withheld[k] = withheld.get(k, 0) + 1
    signature = hashlib.sha256((res["status"] + "|" + ",".join(
        sorted(f"{d}:{s}" for d, s in gate.items()))).encode()).hexdigest()[:32]
    # documents whose identity gate changed since the last extraction (entered, left, or changed status)
    changed_docs = {d for d in set(gate) | set(prev_gate) if gate.get(d) != prev_gate.get(d)} if had_extraction \
        else set()
    prev_fp = {c["id"]: _row_fingerprint(conn, c) for c in prev}

    def affected(claim_id: int) -> bool:
        fp = prev_fp[claim_id]
        return fp[8] != res["status"] or bool({e[0] for e in fp[9]} & changed_docs)

    expired = [r["id"] for r in gated if r["text"] is None]
    if expired:
        # cannot re-extract: supersede only the claims the identity change affects (dependent-claims policy,
        # §2.4.1); claims that do not depend on a changed document stay as they are
        bad = [cid for cid in prev_fp if affected(cid)]
        n = _supersede(conn, run_id, res["id"], "identity_changed", bad)
        repo.trace(conn, run_id, "claims", "incomplete", reason="text_expired",
                   data={"documents_without_text": len(expired), "claims_superseded": n, "gate": gate})
        return {"claims": None, "incomplete": "text_expired", "claims_superseded": n}

    docs: dict[str, DocInput] = {}
    for r in gated:
        url = r["source_url"].rstrip("/").lower()
        docs[r["id"]] = DocInput(
            r["id"], r["source_url"], r["registrable_domain"] or "", r["text"], r["author"], r["published_at"],
            r["published_source"], bool(r["date_conflict"]), r["evidence_kind"], statuses[r["id"]],
            groups.get(r["id"], r["id"]),
            authored=bool(r["author"] and subject.name_regex().search(r["author"]))
            or url in {x.rstrip("/").lower() for k in subject.known_urls for x in {k, privacy.safe_url(k) or k}},
            text_expires_at=r["text_expires_at"])
    cands, dropped, no_claim = [], {}, 0
    for d in docs.values():
        c, dr, nc = extract_doc(d, subject, today)
        cands += c
        no_claim += nc
        for k, v in dr.items():
            dropped[k] = dropped.get(k, 0) + v
    claims = build(cands, docs, today, stale,
                   settings.pattern_min_events if settings else 3, settings.pattern_min_years if settings else 2)

    # keep unchanged claims (same row, same id); supersede only claims whose result changed; insert the rest
    unmatched_prev = dict(prev_fp)
    to_insert = []
    for cl in claims:
        fp = _new_fingerprint(cl, res["status"])
        match = next((cid for cid, pfp in unmatched_prev.items() if pfp == fp), None)
        if match is None:
            to_insert.append(cl)
        else:
            del unmatched_prev[match]
    new_docs_by_key = {}
    for cl in to_insert:
        new_docs_by_key.setdefault(_key(cl.claim_type, cl.predicate, privacy.safe_label(cl.object_key), cl.polarity),
                                   set()).update(e.doc.id for e in cl.evidence)
    superseded = {"identity_changed": 0, "recomputed": 0}
    for cid, fp in unmatched_prev.items():
        newer = new_docs_by_key.get(_key(fp[0], fp[1], fp[2], fp[3]), set())
        why = "identity_changed" if affected(cid) or newer & changed_docs else "recomputed"
        superseded[why] += _supersede(conn, run_id, res["id"], why, [cid])
    kept = len(prev_fp) - len(unmatched_prev)
    now = repo.iso()
    for cl in to_insert:
        cur = conn.execute(
            "INSERT INTO claims (run_id, resolution_id, claim_type, predicate, object_text, object_key, polarity,"
            " attribution_mode, attributed_to, status, confidence, flags_json, reasons_json, origin_groups,"
            " newest_date, identity_status, gate_signature, rule_version, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, res["id"], cl.claim_type, cl.predicate, privacy.safe_label(cl.object_text),
             privacy.safe_label(cl.object_key), cl.polarity, cl.mode,
             cl.attributed_to, cl.status, cl.confidence, json.dumps(cl.flags), json.dumps(cl.reasons),
             cl.origin_groups, cl.newest_date, res["status"], signature, RULES, now))
        for e in cl.evidence:
            a, b = e.cand.span
            sentence = e.doc.text[a:b]
            conn.execute(
                "INSERT INTO claim_evidence (claim_id, run_id, document_id, role, source_url, evidence_kind,"
                " published_at, published_source, date_conflict, event_date, origin_group, document_identity_status,"
                " subject_link, evidence_text, evidence_sha256, span_start, span_end, extraction_rule,"
                " text_expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cur.lastrowid, run_id, e.doc.id, e.role, e.doc.url, e.doc.evidence_kind, e.doc.published_at,
                 e.doc.published_source, int(e.doc.date_conflict), e.cand.event_date, e.doc.origin_group,
                 e.doc.identity_status, e.cand.subject_link, sentence, hashlib.sha256(sentence.encode()).hexdigest(),
                 a, b, f"{e.cand.rule}:{e.cand.claim_type}:{e.cand.predicate}", e.doc.text_expires_at, now))
    by_status: dict[str, int] = {}
    for cl in claims:
        by_status[cl.status] = by_status.get(cl.status, 0) + 1
    if not had_extraction and not prev:
        reason = "initial"
    elif changed_docs or any(fp[8] != res["status"] for fp in prev_fp.values()):
        reason = "identity_changed"
    else:
        reason = "recomputed"
    n_superseded = sum(superseded.values())
    repo.trace(conn, run_id, "claims", "extracted", reason=reason,
               data={"claims": len(claims), "by_status": by_status, "documents_gated": len(gated),
                     "withheld_documents": withheld, "out_of_scope_dropped": dropped,
                     "mentions_without_claim": no_claim, "claims_superseded": n_superseded,
                     "superseded_by_reason": superseded, "claims_kept": kept, "claims_inserted": len(to_insert),
                     "documents_with_changed_identity": len(changed_docs), "gate": gate})
    return {"claims": len(claims), "by_status": by_status, "withheld_documents": withheld,
            "out_of_scope_dropped": dropped, "claims_superseded": n_superseded, "superseded_by_reason": superseded,
            "claims_kept": kept, "claims_inserted": len(to_insert), "reason": reason}


def _key(claim_type, predicate, object_key, polarity) -> tuple:
    return claim_type, predicate, object_key, polarity


def _previous_gate(conn: sqlite3.Connection, run_id: str) -> tuple[dict[str, str], bool]:
    """Document id -> identity status at the last extraction (ids and status codes only, no text)."""
    row = conn.execute("SELECT data_json FROM trace_events WHERE run_id=? AND stage='claims' AND action IN"
                       " ('extracted','withheld','incomplete') ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
    if not row:
        return {}, False
    return (json.loads(row[0] or "{}").get("gate") or {}), True


def _row_fingerprint(conn: sqlite3.Connection, c) -> tuple:
    ev = conn.execute("SELECT document_id, span_start, role, document_identity_status FROM claim_evidence WHERE"
                      " claim_id=? ORDER BY document_id, span_start, role", (c["id"],)).fetchall()
    return (c["claim_type"], c["predicate"], c["object_key"], c["polarity"], c["status"], c["confidence"],
            c["flags_json"], c["attribution_mode"], c["identity_status"], tuple(tuple(e) for e in ev))


def _new_fingerprint(cl, identity_status: str) -> tuple:
    ev = sorted((e.doc.id, e.cand.span[0], e.role, e.doc.identity_status) for e in cl.evidence)
    return (cl.claim_type, cl.predicate, privacy.safe_label(cl.object_key), cl.polarity, cl.status, cl.confidence,
            json.dumps(cl.flags), cl.mode, identity_status, tuple(ev))


def active_claims(conn: sqlite3.Connection, run_id: str, with_evidence: bool = True) -> list[dict]:
    out = []
    for c in conn.execute("SELECT * FROM claims WHERE run_id=? AND superseded_by IS NULL ORDER BY id", (run_id,)):
        item = {k: c[k] for k in ("id", "claim_type", "predicate", "object_text", "polarity", "attribution_mode",
                                  "attributed_to", "status", "confidence", "origin_groups", "newest_date",
                                  "identity_status", "rule_version")}
        item["flags"] = json.loads(c["flags_json"])
        item["reasons"] = json.loads(c["reasons_json"])
        if with_evidence:
            item["evidence"] = [dict(e) for e in conn.execute(
                "SELECT role, source_url, published_at, published_source, event_date, origin_group,"
                " document_identity_status, subject_link, evidence_text, span_start, span_end, extraction_rule"
                " FROM claim_evidence WHERE claim_id=? ORDER BY id", (c["id"],))]
        out.append(item)
    return out


def claims_metrics(conn: sqlite3.Connection, run_id: str) -> dict:
    rows = conn.execute("SELECT claim_type, status, confidence, flags_json FROM claims WHERE run_id=? AND"
                        " superseded_by IS NULL", (run_id,)).fetchall()
    by: dict[str, dict[str, int]] = {"type": {}, "status": {}, "confidence": {}, "flag": {}}
    for r in rows:
        for k, v in (("type", r["claim_type"]), ("status", r["status"]), ("confidence", r["confidence"])):
            by[k][v] = by[k].get(v, 0) + 1
        for f in json.loads(r["flags_json"]):
            by["flag"][f] = by["flag"].get(f, 0) + 1
    last = conn.execute("SELECT action, reason, data_json FROM trace_events WHERE run_id=? AND stage='claims'"
                        " ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
    docs = conn.execute("SELECT COUNT(*) FROM origin_groups WHERE run_id=?", (run_id,)).fetchone()[0]
    groups = conn.execute("SELECT COUNT(DISTINCT origin_group) FROM origin_groups WHERE run_id=?", (run_id,)).fetchone()[0]
    return {"claims": len(rows), "by_type": by["type"], "by_status": by["status"], "by_confidence": by["confidence"],
            "by_flag": by["flag"], "last_action": dict(last) if last else None,
            "documents_in_origin_groups": docs, "origin_groups": groups,
            "note": "claim confidence is separate from identity confidence (see the identity section)"}
