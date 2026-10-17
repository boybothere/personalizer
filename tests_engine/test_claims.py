"""Phase 6: evidence-gated claims, origin groups, organisation matching, privacy and retention (C1-C17)."""

import hashlib
import json
import shutil
import sqlite3
from datetime import timedelta

import pytest
from claim_cases import CASES, TODAY, C, anchor, news
from claims_harness import SETTINGS, predicted_labels, run_case, seed, spans_match, subject_of
from conftest import ROOT

from engine import origins
from engine.claims.store import active_claims, update_claims
from engine.claims.status import CAPS
from engine.identity.orgmatch import OrgMatcher
from engine.identity.store import override_decision, resolve_run
from engine.observe import metrics
from engine.store import repo
from engine.store.db import SchemaOutdated, connect
from engine.store.migrations import migrate

RESOLVED = ("confirmed", "strongly_supported")
GATED = ("confirmed", "strong", "historical")


def _claims(conn, rid):
    return conn.execute("SELECT * FROM claims WHERE run_id=? AND superseded_by IS NULL", (rid,)).fetchall()


# ---------------------------------------------------------------- labelled cases


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_labelled_case(case):
    conn, rid, placed, result = run_case(case)
    assert spans_match(placed), "sentence splitter must reproduce the labelled sentences"
    ident = conn.execute("SELECT status FROM identity_resolutions WHERE run_id=? AND superseded_by IS NULL",
                         (rid,)).fetchone()[0]
    if case["identity"]:
        assert ident == case["identity"]
    claims = _claims(conn, rid)
    if case["no_claims"]:
        assert claims == [], [tuple(c) for c in claims]
    if case["withheld"]:
        assert result.get("withheld") == case["withheld"]
    for x in case["expect"]:
        hit = [c for c in claims if c["claim_type"] == x["type"] and c["object_key"] == x["key"]
               and (x["pol"] is None or c["polarity"] == x["pol"])]
        assert hit, (x, [(c["claim_type"], c["object_key"], c["status"]) for c in claims])
        c = hit[0]
        flags = json.loads(c["flags_json"])
        assert c["status"] == x["status"], (x["key"], c["status"], flags)
        assert not x["conf"] or c["confidence"] == x["conf"]
        assert set(x["flags"]) <= set(flags) and not set(x["no_flags"]) & set(flags), (x["key"], flags)
        assert not x["mode"] or c["attribution_mode"] == x["mode"]
        assert not x["groups"] or c["origin_groups"] == x["groups"]
    if case["origins"]:
        links = {r["link"] for row in conn.execute("SELECT reasons_json FROM origin_groups WHERE run_id=?", (rid,))
                 for r in json.loads(row[0])}
        assert set(case["origins"]["links"]) <= links, links
    labels = predicted_labels(conn, rid, placed, case)
    assert [(s, e) for _, s, e, g in labels if e != g] == []


def test_heldout_set_misses_but_never_invents():
    """Held-out cases were frozen before their first run and not used for tuning. First run (2026-10-18):
    83/87 sentences agreed; the 4 disagreements were all misses (no claim produced). Any regression, and any
    disagreement that produces a claim where none was expected, fails."""
    from claim_cases_heldout import HELDOUT

    rows = []
    for case in HELDOUT:
        conn, rid, placed, _ = run_case(case)
        assert spans_match(placed)
        rows += predicted_labels(conn, rid, placed, case)
    disagreements = [(s, e, g) for _, s, e, g in rows if e != g]
    assert all(g == "no_claim" for _, _, g in disagreements), disagreements
    assert len(rows) == 87 and len(rows) - len(disagreements) >= 83


def test_corpus_size():
    sentences = sum(len(d["sents"]) for c in CASES for d in c["docs"])
    assert len(CASES) >= 60 and sentences >= 200


# ---------------------------------------------------------------- invariants over every case (C1, C3, C5, C7)


def _all_runs():
    for case in CASES:
        yield case, *run_case(case)


def test_no_subject_claims_without_resolved_identity_or_gated_document():
    forced = []
    for case, conn, rid, _, _ in _all_runs():
        res = conn.execute("SELECT status FROM identity_resolutions WHERE run_id=? AND superseded_by IS NULL",
                           (rid,)).fetchone()[0]
        for c in _claims(conn, rid):
            if res not in RESOLVED or c["identity_status"] not in RESOLVED:
                forced.append((case["id"], c["object_key"]))
        for e in conn.execute("SELECT e.document_identity_status, e.document_id FROM claim_evidence e JOIN claims c ON"
                              " c.id=e.claim_id WHERE c.run_id=? AND c.superseded_by IS NULL", (rid,)):
            if e[0] not in GATED:
                forced.append((case["id"], e[1]))
    assert forced == []


def test_namesake_and_rejected_documents_are_never_read_for_claims():
    for case, conn, rid, _, _ in _all_runs():
        rejected = {r[0] for r in conn.execute("SELECT target_ref FROM identity_decisions WHERE run_id=? AND"
                                               " target_type='document' AND superseded_by IS NULL AND status NOT IN"
                                               " ('confirmed','strong','historical')", (rid,))}
        used = {r[0] for r in conn.execute("SELECT document_id FROM claim_evidence WHERE run_id=?", (rid,))}
        assert not rejected & used, case["id"]


def test_activities_associations_and_mentions_are_never_promoted_to_preferences():
    promoted = []
    for case, conn, rid, placed, _ in _all_runs():
        labelled = {(p["doc_id"], a): lab for p in placed for a, _, _, lab in p["sentences"]}
        for r in conn.execute("SELECT e.document_id, e.span_start FROM claim_evidence e JOIN claims c ON c.id=e.claim_id"
                              " WHERE c.run_id=? AND c.claim_type='stated_preference' AND c.status NOT IN"
                              " ('rejected','insufficient_evidence') AND e.role='supports'", (rid,)):
            if labelled.get((r[0], r[1])) not in ("pref+", "pref-"):
                promoted.append((case["id"], labelled.get((r[0], r[1]))))
    assert promoted == []


def test_every_claim_has_complete_provenance_and_recomputable_confidence():
    for case, conn, rid, placed, _ in _all_runs():
        texts = {p["doc_id"]: p["text"] for p in placed}
        for c in _claims(conn, rid):
            flags = set(json.loads(c["flags_json"]))
            ev = conn.execute("SELECT * FROM claim_evidence WHERE claim_id=?", (c["id"],)).fetchall()
            assert ev, case["id"]
            for e in ev:
                assert e["source_url"] and e["document_id"] and e["origin_group"] and e["extraction_rule"]
                assert e["evidence_text"] == texts[e["document_id"]][e["span_start"]:e["span_end"]]
                assert e["evidence_sha256"] == hashlib.sha256(e["evidence_text"].encode()).hexdigest()
                assert e["text_expires_at"]
            if c["status"] not in ("rejected", "insufficient_evidence"):
                supports = [e for e in ev if e["role"] == "supports"]
                assert any(e["published_at"] or e["event_date"] for e in supports) or "date_missing" in flags
            expected = {"verified": "high", "tentative": "low", "conflicting": "none", "rejected": "none",
                        "insufficient_evidence": "none"}.get(c["status"])
            if c["status"] == "strongly_supported":
                expected = "high" if not flags - {"single_origin"} else "medium"
            assert c["confidence"] == expected, (case["id"], c["status"], flags)
            if flags & CAPS:
                assert c["status"] in ("tentative", "conflicting"), (case["id"], c["object_key"], flags)


def test_reported_preferences_are_always_tentative_and_attributed():
    seen = 0
    for case, conn, rid, _, _ in _all_runs():
        for c in _claims(conn, rid):
            if c["claim_type"] == "stated_preference" and c["attribution_mode"] == "reported" and \
                    c["status"] not in ("rejected", "insufficient_evidence", "conflicting"):
                seen += 1
                assert c["status"] == "tentative" and c["confidence"] == "low"
                assert c["attributed_to"].startswith("reported by ") and "not the subject's own" in c["attributed_to"]
    assert seen >= 5


def test_copies_collapse_into_one_claim_with_one_origin():
    case = next(c for c in CASES if c["id"] == "g2_agency_story_three_sites")
    conn, rid, _, _ = run_case(case)
    claims = [c for c in _claims(conn, rid) if c["claim_type"] == "association"]
    assert len(claims) == 1 and claims[0]["origin_groups"] == 1 and claims[0]["status"] == "tentative"
    sites = {e[0] for e in conn.execute("SELECT source_url FROM claim_evidence WHERE claim_id=?", (claims[0]["id"],))}
    assert len(sites) == 3  # three sites, one origin: no inflated corroboration


def test_activity_patterns_are_labelled_and_need_distinct_dated_events():
    found = {}
    for case, conn, rid, _, _ in _all_runs():
        for c in _claims(conn, rid):
            if c["claim_type"] == "activity_pattern":
                assert c["object_text"].endswith("not a stated preference") and c["origin_groups"] >= 3
                found[case["id"]] = c["status"]
    assert found == {"g4_pattern_positive": "strongly_supported"}


# ---------------------------------------------------------------- organisation matching (C4)


@pytest.mark.parametrize("candidate,expected", [
    ("Nimbus Compute", "exact"), ("Nimbus Compute Inc.", "exact"), ("NIMBUS COMPUTE", "exact"),
    ("Nimbus Compute's", "exact"), ("At Nimbus Compute", "exact"), ("Nimbus Compute CTO Priya Raman", "exact"),
    ("NimbusCompute", "alias"), ("Stratus GPU", "alias"),
    ("Nimbus", "partial"), ("Nimbus Compute Labs Ltd", "partial"), ("Nimbus Compute India", "partial"),
    ("Nimbus Compute Bank", "different"), ("Nimbus Computers India", "different"), ("Compute Nimbus", "different"),
    ("Orbit Labs", "different"),
])
def test_organisation_matching_is_by_tokens(candidate, expected):
    m = OrgMatcher("Nimbus Compute", ["Stratus GPU"], "nimbuscompute.example", {"Priya", "Raman"})
    assert m.match(candidate) == expected


# ---------------------------------------------------------------- origin groups (C3)


def test_independent_pages_stay_separate_and_merges_record_their_reason():
    docs = [origins.Doc("a", "https://one.example/x", "one.example", "Priya Raman spoke about chips at length today."),
            origins.Doc("b", "https://two.example/y", "two.example", "A different article about a different topic."),
            origins.Doc("c", "https://three.example/z", "three.example", "Copy supplied (PTI). Something else."),
            origins.Doc("d", "https://four.example/w", "four.example", "Another (PTI) story on markets.")]
    g = origins.group(docs)
    assert g["a"][0] != g["b"][0] and g["c"][0] == g["d"][0] == "c"
    assert {"link": "news_agency", "with": "d"} in g["c"][1]


def test_short_shared_job_title_is_not_a_copy_but_a_shared_passage_is():
    t1 = "Priya Raman, VP of Infrastructure at Nimbus Compute, discussed chips with analysts on Monday morning."
    t2 = "Priya Raman, VP of Infrastructure at Nimbus Compute, will keynote the summit about power budgets."
    shared = "the company expects to double its GPU capacity across every region before the end of next year"
    g = origins.group([origins.Doc("a", "https://a.example/1", "a.example", t1),
                       origins.Doc("b", "https://b.example/2", "b.example", t2),
                       origins.Doc("c", "https://c.example/3", "c.example", "Analysts said " + shared + "."),
                       origins.Doc("d", "https://d.example/4", "d.example", "Executives noted " + shared + " too.")],
                      ignore_words=frozenset({"priya", "raman"}))
    assert g["a"][0] != g["b"][0]
    assert g["c"][0] == g["d"][0]


# ---------------------------------------------------------------- dependent claims when identity changes (C2, C15)


def _two_source_case():
    return C("dep", "G1", [
        anchor(),
        news("podcastone", "ep", 0, [('"I love trail running," Raman said.', "pref+")]),
        news("magazinetwo", "ep", 1, [('In the interview she said: "I really love trail running."', "pref+")],
             "2026-07-01")])


def test_identity_and_claim_confidence_are_stored_separately():
    conn, rid, _, _ = run_case(_two_source_case())
    c = _claims(conn, rid)[0]
    assert (c["status"], c["confidence"], c["identity_status"]) == ("strongly_supported", "high", "confirmed")
    statuses = {e["document_identity_status"] for e in conn.execute("SELECT * FROM claim_evidence WHERE claim_id=?",
                                                                   (c["id"],))}
    assert statuses == {"strong"}
    m = metrics(conn, rid)
    assert m["identity"]["status"] == "confirmed" and m["claims"]["by_confidence"] == {"high": 1}


def test_document_override_supersedes_dependent_claims_and_recomputes():
    case = _two_source_case()
    conn, rid, _, _ = run_case(case)
    subject = subject_of(case)
    first = _claims(conn, rid)[0]
    override_decision(conn, rid, "d02", "rejected", "reviewer: this interview is about someone else")
    resolve_run(conn, rid, subject, today=TODAY)
    out = update_claims(conn, rid, subject, today=TODAY, settings=SETTINGS)
    old = conn.execute("SELECT superseded_by, supersede_reason FROM claims WHERE id=?", (first["id"],)).fetchone()
    assert old["superseded_by"] is not None and old["supersede_reason"] == "identity_changed"
    now = _claims(conn, rid)
    assert len(now) == 1 and now[0]["status"] == "tentative" and now[0]["origin_groups"] == 1  # one source left
    assert out["claims_superseded"] == 1


def test_subject_becoming_unresolved_supersedes_all_claims():
    case = _two_source_case()
    conn, rid, _, _ = run_case(case)
    subject = subject_of(case)
    for d in ("d00", "d01", "d02"):
        override_decision(conn, rid, d, "ambiguous", "reviewer: cannot tell these people apart")
    resolve_run(conn, rid, subject, today=TODAY)
    out = update_claims(conn, rid, subject, today=TODAY, settings=SETTINGS)
    assert out["withheld"] == "identity_unresolved" and _claims(conn, rid) == []
    reasons = {r[0] for r in conn.execute("SELECT supersede_reason FROM claims WHERE run_id=?", (rid,))}
    assert reasons == {"identity_changed"}


def test_rerun_without_identity_change_is_recomputed_not_identity_changed():
    case = _two_source_case()
    conn, rid, _, _ = run_case(case)
    subject = subject_of(case)
    resolve_run(conn, rid, subject, today=TODAY)
    update_claims(conn, rid, subject, today=TODAY, settings=SETTINGS)
    reasons = [r[0] for r in conn.execute("SELECT supersede_reason FROM claims WHERE run_id=? AND superseded_by IS NOT"
                                          " NULL", (rid,))]
    assert reasons == ["recomputed"] and len(_claims(conn, rid)) == 1


def test_expired_text_means_incomplete_never_invented():
    case = _two_source_case()
    conn, rid, _, _ = run_case(case)
    subject = subject_of(case)
    repo.purge(conn, repo.now() + timedelta(days=8))
    override_decision(conn, rid, "d02", "rejected", "reviewer: wrong person")
    resolve_run(conn, rid, subject, today=TODAY)
    out = update_claims(conn, rid, subject, today=TODAY, settings=SETTINGS)
    assert out["incomplete"] == "text_expired" and out["claims_superseded"] == 1
    assert _claims(conn, rid) == []  # the only claim depended on d02; nothing new can be extracted


# ---------------------------------------------------------------- privacy: every persistence path (C9, C13, C14)


def _out_of_scope_sentences():
    return [s for c in CASES for d in c["docs"] for s, lab in d["sents"] if lab == "out_of_scope"]


def _scan(directory, needles):
    found = []
    for f in sorted(directory.iterdir()):
        raw = f.read_bytes()
        found += [(f.name, n) for n in needles if n.encode() in raw]
    return found


def test_out_of_scope_sentences_are_not_left_in_any_database_file(tmp_path):
    db = tmp_path / "p.db"
    conn = connect(db)
    assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY: no temp files with row content
    runs = []
    for n, case in enumerate([c for c in CASES if c["group"] == "G7"]):
        _, rid, placed, _ = run_case(case, conn=conn, redact=True, prefix=f"r{n}")
        runs.append(rid)
    assert repo._checkpoint(conn) is True
    needles = _out_of_scope_sentences()
    assert len(needles) >= 20
    assert _scan(tmp_path, needles) == []
    assert sorted(f.name for f in tmp_path.iterdir()) <= ["p.db", "p.db-shm", "p.db-wal"]
    for rid in runs:
        exported = json.dumps([metrics(conn, rid), active_claims(conn, rid)], ensure_ascii=False)
        trace = json.dumps([dict(r) for r in conn.execute("SELECT * FROM trace_events WHERE run_id=?", (rid,))],
                           ensure_ascii=False)
        assert not [n for n in needles if n in exported or n in trace]
    # offsets stay valid after redaction: evidence still equals the stored text
    for rid in runs:
        for e in conn.execute("SELECT e.evidence_text, e.span_start, e.span_end, d.text FROM claim_evidence e JOIN"
                              " documents d ON d.id = e.document_id WHERE e.run_id=?", (rid,)):
            assert e["text"][e["span_start"]:e["span_end"]] == e["evidence_text"]
    conn.close()


def test_full_research_run_redacts_pages_and_snippets(tmp_path):
    import httpx
    from world import World, page, run_world

    sensitive = ["Priya Raman lives in Indiranagar with her husband.", "She is flying to Goa tomorrow for a wedding."]
    snippet = "Priya Raman was treated at a hospital last year."
    w = World(routes=[(lambda q, inc: True, ["https://nimbuscompute.example/team/priya-raman",
                                            "https://profiles.example/priya"])],
              pages={"https://profiles.example/priya": page(" ".join(
                  ["Priya Raman, VP of Infrastructure at Nimbus Compute, spoke about chips."] + sensitive
                  + ["Priya Raman is a member of the Open Compute Project."] * 4))},
              tavily_override=lambda req, n: httpx.Response(200, json={"results": [
                  {"url": "https://profiles.example/priya", "title": "T", "content": snippet},
                  {"url": "https://nimbuscompute.example/team/priya-raman", "title": "T", "content": "x"}],
                  "usage": {"credits": 1}}) if b"/search" in req.url.raw_path else None)
    db = tmp_path / "r.db"
    conn, rid = run_world(w, conn=connect(db), organization_domain="nimbuscompute.example")
    m = metrics(conn, rid)
    assert m["claims"]["available"] and m["claims"]["advisory"]
    assert {"origin_groups", "claims", "privacy_redaction"} <= set(m["time_by_stage_s"])
    red = json.loads(conn.execute("SELECT data_json FROM trace_events WHERE run_id=? AND stage='privacy_redaction'",
                                  (rid,)).fetchone()[0])
    assert red["documents_redacted"] >= 1 and red["checkpoint_complete"] is True
    # page sentences mention a husband and a wedding (family is checked first); the snippet mentions a hospital
    assert set(red["sentences_by_category"]) == {"family", "health"} and \
        sum(red["sentences_by_category"].values()) >= 3
    assert m["claims"]["by_type"].get("association") == 1  # the in-scope sentence still yields its claim
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert _scan(tmp_path, sensitive + [snippet]) == []
    assert sorted(f.name for f in tmp_path.iterdir()) <= ["r.db", "r.db-shm", "r.db-wal"]
    conn.close()


def test_engine_writes_no_log_files():
    for f in (ROOT / "engine").rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert "FileHandler" not in src and "basicConfig" not in src and "logging.getLogger" not in src, f
        assert "open(" not in src or f.name in ("cli.py", "config.py"), f  # cli reads a specs file; config reads .env


# ---------------------------------------------------------------- retention (P6-Q2, C13)


def test_evidence_text_expires_after_7_days_and_claims_after_30(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    case = next(c for c in CASES if c["id"] == "g4_explicit_positive")
    _, rid, placed, _ = run_case(case, conn=conn)
    sentence = "I love trail running."
    assert conn.execute("SELECT COUNT(*) FROM claim_evidence WHERE evidence_text=?", (sentence,)).fetchone()[0] == 1

    out = repo.purge(conn, repo.now() + timedelta(days=6))
    assert out["evidence_cleared"] == 0  # not yet expired
    out = repo.purge(conn, repo.now() + timedelta(days=8))
    assert out["evidence_cleared"] >= 1 and out["texts_cleared"] >= 1 and out["checkpoint_complete"] is True
    assert conn.execute("SELECT COUNT(*) FROM claim_evidence WHERE evidence_text IS NOT NULL").fetchone()[0] == 0
    kept = conn.execute("SELECT evidence_sha256, span_start FROM claim_evidence").fetchall()
    assert kept and all(k["evidence_sha256"] for k in kept)  # hashes and offsets kept
    assert all(e["evidence_text"] is None for c in active_claims(conn, rid) for e in c["evidence"])  # exports too
    assert _scan(tmp_path, [sentence, "sourdough baking."]) == []
    assert _claims(conn, rid)  # claim structure lives as long as the run

    out = repo.purge(conn, repo.now() + timedelta(days=31))
    assert out["runs_deleted"] == 1 and conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    assert _scan(tmp_path, ["trail running", "sourdough baking"]) == []
    conn.close()


def test_blocked_checkpoint_is_reported_not_hidden(tmp_path):
    db = tmp_path / "b.db"
    conn = connect(db)
    case = next(c for c in CASES if c["id"] == "g4_explicit_positive")
    run_case(case, conn=conn)
    reader = sqlite3.connect(str(db))
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM claims").fetchone()  # holds a read snapshot
    out = repo.purge(conn, repo.now() + timedelta(days=8))
    assert out["checkpoint_complete"] is False
    reader.rollback()
    reader.close()
    assert repo._checkpoint(conn) is True
    conn.close()


# ---------------------------------------------------------------- migration 0006 on a copy (C10)


LOCAL_DB = ROOT / "data" / "engine.db"


@pytest.mark.skipif(not LOCAL_DB.exists(), reason="local Phase 1/2 database not present")
def test_migration_0006_on_a_copy(tmp_path):
    original = hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest()
    copy = tmp_path / "c.db"
    shutil.copy2(LOCAL_DB, copy)
    with pytest.raises(SchemaOutdated):
        connect(copy)
    conn = connect(copy, upgrade=False)
    tables = ("runs", "documents", "fetch_attempts", "search_queries")
    counts = lambda: {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: E731
    before = counts()
    steps = migrate(conn)
    assert "up 6 claims" in steps and counts() == before
    for run_id in [r[0] for r in conn.execute("SELECT id FROM runs")]:
        assert metrics(conn, run_id)["claims"]["available"] is False  # old runs: nothing invented
    down = migrate(conn, 5)
    assert down == ["down 6 claims"] and counts() == before
    assert "up 6 claims" in migrate(conn)
    conn.close()
    assert hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest() == original


def test_seed_helper_matches_labels():
    conn = connect(":memory:")
    rid, placed = seed(conn, CASES[0])
    assert spans_match(placed) and rid
