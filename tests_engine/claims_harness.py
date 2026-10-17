"""Run labelled claim cases through the real pipeline stages (origin groups -> identity -> claims -> redaction)."""

from __future__ import annotations

import json
from datetime import timedelta

from claim_cases import TODAY

from engine import origins
from engine.claims import scope
from engine.claims.store import update_claims
from engine.claims.text import sentence_spans
from engine.config import ClaimSettings
from engine.identity.evidence import Subject
from engine.identity.store import resolve_run
from engine.store import repo
from engine.store.db import connect

SETTINGS = ClaimSettings()


def subject_of(case) -> Subject:
    s = dict(case["subject"])
    s["known_urls"] = list(s.get("known_urls", [])) + [d["url"] for d in case["docs"] if d["known"]]
    return Subject(**s)


def seed(conn, case, prefix="") -> tuple[str, list[dict]]:
    rid = repo.create_run(conn, "research", {"case": case["id"]}, {}, run_days=30)
    created = repo.now()
    placed = []
    for i, d in enumerate(case["docs"]):
        host = d["url"].split("/")[2]
        registrable = ".".join(host.split(".")[-2:])
        text = " ".join(s for s, _ in d["sents"])
        uid = repo.add_url(conn, rid, url_key=f"{prefix}u{i}", canonical_url=d["url"], domain=host,
                           registrable_domain=registrable, platform=None, decision="accepted", reject_code=None,
                           reject_detail=None)
        doc_id = f"{prefix}d{i:02d}"
        conn.execute("INSERT INTO documents (id, run_id, url_id, evidence_kind, source_url, canonical_link, title,"
                     " author, published_at, date_conflict, text_chars, usable, extractor, text, metadata_json,"
                     " text_expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (doc_id, rid, uid, d["kind"], d["url"], d["canonical"], d["title"], d["author"], d["published"],
                      int(d["date_conflict"]), len(text), 1, "test", text, json.dumps(d["meta"]),
                      repo.iso(created + timedelta(days=7)), repo.iso(created + timedelta(seconds=i))))
        offsets, pos = [], 0
        for s, label in d["sents"]:
            offsets.append((pos, pos + len(s), s, label))
            pos += len(s) + 1
        placed.append(dict(doc_id=doc_id, text=text, sentences=offsets))
    return rid, placed


def run_case(case, conn=None, redact=False, prefix=""):
    conn = conn or connect(":memory:")
    rid, placed = seed(conn, case, prefix)
    subject = subject_of(case)
    origins.compute(conn, rid, SETTINGS, ignore_words=frozenset({subject.first, subject.last}))
    resolve_run(conn, rid, subject, today=TODAY)
    result = update_claims(conn, rid, subject, today=TODAY, settings=SETTINGS)
    if redact:
        scope.redact_run(conn, rid, TODAY, subject.location)
    return conn, rid, placed, result


def predicted_labels(conn, rid, placed, case) -> list[tuple[str, str, str, str]]:
    """(doc_id, sentence, expected label, predicted label) for every labelled sentence."""
    by_span = {}
    for r in conn.execute("""SELECT e.document_id, e.span_start, c.claim_type, c.polarity, c.status
                             FROM claim_evidence e JOIN claims c ON c.id = e.claim_id
                             WHERE c.run_id=? AND c.superseded_by IS NULL AND e.role='supports'
                               AND c.claim_type <> 'activity_pattern'""", (rid,)):
        if r["status"] == "rejected":
            label = "rejected"
        elif r["status"] == "insufficient_evidence":
            label = "insufficient"
        elif r["claim_type"] == "stated_preference":
            label = "pref+" if r["polarity"] == 1 else "pref-"
        else:
            label = r["claim_type"]
        by_span[(r["document_id"], r["span_start"])] = label
    out = []
    location = case["subject"].get("location")
    for p in placed:
        for start, _, s, expected in p["sentences"]:
            if scope.classify(s, TODAY, location):
                got = "out_of_scope"
            else:
                got = by_span.get((p["doc_id"], start), "no_claim")
            out.append((p["doc_id"], s, expected, got))
    return out


def spans_match(placed) -> bool:
    return all([(a, b) for a, b, _, _ in p["sentences"]] == sentence_spans(p["text"]) for p in placed)
