"""Phase 5: identity resolution against labelled synthetic cases (criteria I1-I11, PHASE5_PLAN.md)."""

import hashlib
import json
import shutil

import pytest
from conftest import ROOT
from identity_cases import CASES, RESOLVED, SUBJECT, TODAY, doc

from engine.identity.evidence import Subject, extract
from engine.identity.resolver import resolve
from engine.identity.store import active_overrides, latest, override_decision, resolve_run
from engine.observe import metrics
from engine.store import repo
from engine.store.db import SchemaOutdated, connect
from engine.store.migrations import migrate


def run_case(case, overrides=None):
    subject = Subject(**case["subject"])
    evidence = [extract(subject=subject, **d) for d in case["docs"]]
    return resolve(subject, evidence, leads=case.get("leads", []), overrides=overrides, today=TODAY)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_labelled_case(case):
    r = run_case(case)
    got_docs = {d.doc_id: d.status for d in r.documents}
    assert got_docs == case["expect_docs"], [(d.doc_id, d.status, d.reasons) for d in r.documents]
    assert r.status == case["expect"], r.explanation
    assert r.resolved == (case["expect"] in RESOLVED)
    for key, status in case.get("expect_profiles", {}).items():
        got = {f"{p.platform}/{p.handle}": p.status for p in r.profiles}
        assert got.get(key) == status, (key, got)
    if "expect_namesakes" in case:
        assert sum(k.startswith("namesake:") for k in r.candidates) == case["expect_namesakes"]
    assert r.explanation  # every outcome is explained


def test_forced_match_rate_is_zero():
    unresolved_cases = [c for c in CASES if c["expect"] not in RESOLVED]
    forced = [c["id"] for c in unresolved_cases if run_case(c).resolved]
    assert len(unresolved_cases) >= 10 and forced == []


def test_namesake_documents_are_never_attributed():
    for case in CASES:
        r = run_case(case)
        for d in r.documents:
            if d.candidate.startswith("namesake:"):
                assert d.status == "rejected", (case["id"], d)


def test_name_and_organisation_alone_never_resolves():
    subject = Subject(**dict(SUBJECT, organization_domain=None))
    many = [extract(subject=subject, **doc(i, f"https://site{i}.example/x",
                                          "Priya Raman of Nimbus Compute talks about GPU planning."))
            for i in range(5)]
    r = resolve(subject, many, today=TODAY)
    assert {d.status for d in r.documents} == {"possible"} and r.status == "possible" and not r.resolved


def test_namesake_never_merged_even_if_it_mentions_target_city():
    subject = Subject(**dict(SUBJECT, organization_domain=None))
    ev = extract(subject=subject, **doc(1, "https://x.example/a",
                                        "Priya Raman, the pastry chef at Saffron Bakehouse in Bengaluru, baked bread."))
    r = resolve(subject, [ev], today=TODAY)
    assert r.documents[0].status == "rejected" and r.status == "insufficient_evidence"


def test_two_profiles_on_one_platform_are_never_both_confirmed():
    for case in CASES:
        r = run_case(case)
        by_platform = {}
        for p in r.profiles:
            if p.status == "confirmed":
                by_platform.setdefault(p.platform, []).append(p.handle)
        assert all(len(v) == 1 for v in by_platform.values()), (case["id"], by_platform)


# ---------------------------------------------------------------- storage, overrides and reversal (I9)


def _seed_run(conn, case):
    rid = repo.create_run(conn, "research", {"case": case["id"]}, {}, run_days=30)
    for d in case["docs"]:
        uid = repo.add_url(conn, rid, url_key=d["doc_id"], canonical_url=d["url"], domain=d["domain"],
                           registrable_domain=d["registrable"], platform=None, decision="accepted",
                           reject_code=None, reject_detail=None)
        conn.execute("INSERT INTO documents (id, run_id, url_id, evidence_kind, source_url, text_chars, usable,"
                     " extractor, text, metadata_json, published_at, duplicate_of, created_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (d["doc_id"], rid, uid, "fetched_page", d["url"], len(d["text"]), 1, "t", d["text"],
                      json.dumps(d["metadata"]), d["published_at"], None, repo.iso()))
    return rid


def test_override_is_recorded_reversible_and_applied():
    case = next(c for c in CASES if c["id"] == "name_plus_org_only")
    conn = connect(":memory:")
    rid = _seed_run(conn, case)
    subject = Subject(**case["subject"])
    resolve_run(conn, rid, subject, today=TODAY)
    assert latest(conn, rid)["status"] == "possible"

    first = override_decision(conn, rid, "d1", "confirmed", "reviewer verified with the person directly")
    resolve_run(conn, rid, subject, today=TODAY)
    now = latest(conn, rid)
    assert now["status"] == "confirmed" and active_overrides(conn, rid) == {"d1": "confirmed"}

    second = override_decision(conn, rid, "d1", "rejected", "reviewer: wrong person after all")
    resolve_run(conn, rid, subject, today=TODAY)
    assert latest(conn, rid)["status"] == "insufficient_evidence"
    chain = conn.execute("SELECT id, status, superseded_by FROM identity_decisions WHERE run_id=? AND"
                         " decided_by='user' ORDER BY id", (rid,)).fetchall()
    assert [tuple(r) for r in chain] == [(first, "confirmed", second), (second, "rejected", None)]
    resolutions = conn.execute("SELECT status, superseded_by FROM identity_resolutions WHERE run_id=? ORDER BY id",
                               (rid,)).fetchall()
    assert [r["status"] for r in resolutions] == ["possible", "confirmed", "insufficient_evidence"]
    assert [r["superseded_by"] is None for r in resolutions] == [False, False, True]
    with pytest.raises(ValueError):
        override_decision(conn, rid, "d1", "confirmed", " ")  # a reason is mandatory


def test_resolution_is_visible_in_run_metrics_as_advisory():
    case = next(c for c in CASES if c["id"] == "namesakes_with_target")
    conn = connect(":memory:")
    rid = _seed_run(conn, case)
    resolve_run(conn, rid, Subject(**case["subject"]), today=TODAY)
    ident = metrics(conn, rid)["identity"]
    assert ident["available"] and ident["advisory"] and ident["status"] == "strongly_supported"
    assert ident["documents_by_status"] == {"strong": 2, "rejected": 2}


def test_research_run_records_identity_resolution():
    from world import World, run_world

    w = World(routes=[(lambda q, inc: True, ["https://nimbuscompute.example/team/priya-raman",
                                            "https://chef.example/priya-raman"])])
    conn, rid = run_world(w, organization_domain="nimbuscompute.example")
    ident = metrics(conn, rid)["identity"]
    assert ident["available"] and ident["status"] == "confirmed"
    assert ident["documents_by_status"].get("rejected") == 1  # the pastry chef stays a namesake


# ---------------------------------------------------------------- migration 0005 on a copy (I10)


LOCAL_DB = ROOT / "data" / "engine.db"


@pytest.mark.skipif(not LOCAL_DB.exists(), reason="local Phase 1/2 database not present")
def test_migration_0005_on_a_copy(tmp_path):
    original = hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest()
    copy = tmp_path / "c.db"
    shutil.copy2(LOCAL_DB, copy)
    with pytest.raises(SchemaOutdated):
        connect(copy)
    conn = connect(copy, upgrade=False)
    counts = lambda: {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
                      for t in ("runs", "documents", "fetch_attempts", "search_queries")}
    before = counts()
    assert "up 5 identity_resolution" in migrate(conn)
    assert counts() == before
    for run_id in [r[0] for r in conn.execute("SELECT id FROM runs")]:
        assert metrics(conn, run_id)["identity"]["available"] is False  # old runs: no resolution, nothing invented
    assert migrate(conn, 2)[0] == "down 5 identity_resolution" and counts() == before
    conn.close()
    assert hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest() == original
