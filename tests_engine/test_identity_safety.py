"""Corrective pass 2: removed text keeps sentence boundaries, and identity safeguards survive privacy filtering.

Synthetic data only. Cases A, A2, B, C, D are the ones reported by the final Phase 6 review."""

import json

from claim_cases import SUBJECT, TODAY, C, D, anchor
from claims_harness import SETTINGS, run_case, subject_of
from conftest import make_cfg

from engine import origins, privacy
from engine.claims import scope
from engine.claims.store import update_claims
from engine.claims.text import is_placeholder, placeholder, sentence_spans
from engine.identity.evidence import Subject, _sentences
from engine.identity.store import override_decision, resolve_run
from engine.store import repo
from engine.store.db import connect

SUBJ_SENT = "Priya Raman, VP of Infrastructure at Nimbus Compute, spoke on the panel."
CHEF_SENSITIVE = "Priya Raman, a pastry chef, sings in her church choir."
JAZZ = "She loves jazz."


def run_production(docs, subject=SUBJECT, conn=None):
    """Seed through the real write path (repo.add_url / repo.add_document), then the run's identity and claims
    stages, as research_run does."""
    conn = conn or connect(":memory:")
    subj = Subject(**{k: v for k, v in subject.items()})
    rid = repo.create_run(conn, "research", {"input": {"name": subj.name, "location": subj.location}}, {},
                          run_days=30)
    for i, (url, text, published, meta) in enumerate(docs):
        host = url.split("/")[2]
        uid = repo.add_url(conn, rid, url_key=f"u{i}", canonical_url=url, domain=host,
                           registrable_domain=".".join(host.split(".")[-2:]), platform=None, decision="accepted",
                           reject_code=None, reject_detail=None)
        repo.add_document(conn, rid, uid, None, {"evidence_kind": "fetched_page", "source_url": url,
                                                 "extractor": "t", "text": text, "usable": True,
                                                 "published_at": published, "metadata": meta or {}},
                          text_days=7, **_subject_kw(subj))
    origins.compute(conn, rid, SETTINGS, ignore_words=frozenset({subj.first, subj.last}))
    resolve_run(conn, rid, subj, today=TODAY)
    update_claims(conn, rid, subj, today=TODAY, settings=SETTINGS)
    return conn, rid


def _subject_kw(subj):
    import inspect

    return {"subject_name": subj.name} if "subject_name" in inspect.signature(repo.add_document).parameters else {}


def _status(conn, rid):
    return conn.execute("SELECT status FROM identity_resolutions WHERE run_id=? AND superseded_by IS NULL",
                        (rid,)).fetchone()[0]


def _docs(conn, rid):
    return {r[0]: r[1] for r in conn.execute("SELECT target_ref, status FROM identity_decisions WHERE run_id=? AND"
                                             " target_type='document' AND superseded_by IS NULL", (rid,))}


def _by_url(conn, rid):
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT d.source_url, i.status, d.id FROM identity_decisions i JOIN documents d ON d.id = i.target_ref"
        " WHERE i.run_id=? AND i.target_type='document' AND i.superseded_by IS NULL", (rid,))}


def _claims(conn, rid):
    return [tuple(r) for r in conn.execute("SELECT claim_type, object_key, status FROM claims WHERE run_id=? AND"
                                           " superseded_by IS NULL", (rid,))]


ANCHOR = ("https://nimbuscompute.example/team/priya",
          "Priya Raman is VP of Infrastructure at Nimbus Compute, where she leads the GPU cloud team.",
          "2026-01-10", None)


# ---------------------------------------------------------------- 1. sentence boundaries


def test_placeholder_is_a_sentence_of_its_own_and_keeps_offsets():
    text = f"{SUBJ_SENT} {CHEF_SENSITIVE} {JAZZ}"
    filtered = privacy.filter_text(text)
    assert len(filtered) == len(text)
    spans = [filtered[a:b] for a, b in sentence_spans(filtered)]
    assert spans[0] == SUBJ_SENT and is_placeholder(spans[1]) and spans[2] == JAZZ
    assert _sentences(filtered)[2] == JAZZ  # the identity splitter agrees
    for n in range(1, 40):
        p = placeholder(n)
        assert len(p) == n and (n < 4 or is_placeholder(p))


def test_end_of_run_redaction_keeps_boundaries_and_legacy_placeholders_are_hard_boundaries():
    text = "Priya Raman, a pastry chef in Chennai, opened a bakery. She loves jazz."
    red, counts = scope.redact_text(text, TODAY)
    assert counts == {"place_undated": 1}
    assert [red[a:b] for a, b in sentence_spans(red)][1] == "She loves jazz."
    legacy = "Priya Raman spoke on the panel. [removed: out of scope]       She loves jazz."
    assert [legacy[a:b] for a, b in sentence_spans(legacy)][-1] == "She loves jazz."


def test_case_a_pronoun_after_removed_namesake_sentence_is_not_attributed():
    conn, rid = run_production([ANCHOR, ("https://panel.example/x", f"{SUBJ_SENT} {CHEF_SENSITIVE} {JAZZ}",
                                         "2026-05-01", None)])
    assert ("stated_preference", "jazz", "tentative") not in _claims(conn, rid)
    assert not any(c[1] == "jazz" for c in _claims(conn, rid))


def test_case_a2_end_of_run_redaction_then_re_resolution_attributes_nothing_new():
    case = C("A2", "X", [anchor(), D("https://panel.example/x", [
        (SUBJ_SENT, ""), ("Priya Raman, a pastry chef in Chennai, opened a bakery.", ""), (JAZZ, "")], "2026-05-01"),
        D("https://other.example/y", [("Unrelated page about chips.", "")], "2026-05-01")])
    conn, rid, _, _ = run_case(case, redact=True)
    before_docs, before_claims = _docs(conn, rid), _claims(conn, rid)
    assert before_docs["d01"] == "ambiguous" and before_claims == []
    override_decision(conn, rid, "d02", "rejected", "reviewer: unrelated page")  # any later identity change
    resolve_run(conn, rid, subject_of(case), today=TODAY)
    update_claims(conn, rid, subject_of(case), today=TODAY, settings=SETTINGS)
    assert not any(c[1] == "jazz" for c in _claims(conn, rid))  # no inheritance across the removed sentence


# ---------------------------------------------------------------- 2. identity-safety signal (cases A, B, C, D)

GH = {"same_as": ["https://github.com/priyaraman"]}
OFFICIAL_2019 = ("https://nimbuscompute.example/team/priya",
                 "Priya Raman is VP of Infrastructure at Nimbus Compute, where she leads the GPU cloud team.",
                 "2019-03-01", GH)
NO_DOMAIN = dict(SUBJECT, organization_domain=None)
OLD_SUPPORT = [("https://techpress.example/a", "Priya Raman, VP of Infrastructure at Nimbus Compute, said GPU"
                " capacity will double.", "2018-02-01", None),
               ("https://summit.example/b", "Priya Raman of Nimbus Compute, a VP of Infrastructure, spoke about"
                " inference costs at the summit.", "2018-06-01", None)]


def _flags(conn, rid):
    return {r[0]: json.loads(r[1] or "[]") for r in conn.execute("SELECT id, flags_json FROM documents WHERE"
                                                                   " run_id=? ORDER BY created_at", (rid,))}


def test_signal_is_counts_only_and_scoped_to_run_and_document():
    conn, rid = run_production([ANCHOR, ("https://panel.example/x", f"{SUBJ_SENT} {CHEF_SENSITIVE} {JAZZ}",
                                         "2026-05-01", None)])
    _, other = run_production([ANCHOR], conn=conn)  # a second run in the same database
    by_url = {r[0]: json.loads(r[1] or "[]") for r in conn.execute("SELECT source_url, flags_json FROM documents"
                                                                    " WHERE run_id=?", (rid,))}
    assert by_url[ANCHOR[0]] == [] and by_url["https://panel.example/x"] == ["privacy_removed:1",
                                                                             "privacy_removed_named:1"]
    dump = json.dumps([[dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in
                       ("documents", "identity_decisions", "identity_resolutions", "trace_events", "claims")])
    assert "church" not in dump.lower() and "choir" not in dump.lower()
    assert all("privacy_removed" not in json.dumps(f) for f in _flags(conn, other).values())


def test_case_a_page_with_removed_namesake_sentence_is_ambiguous_and_ungated():
    conn, rid = run_production([ANCHOR, ("https://panel.example/x", f"{SUBJ_SENT} {CHEF_SENSITIVE} {JAZZ}",
                                         "2026-05-01", None)])
    docs = _by_url(conn, rid)
    assert docs[ANCHOR[0]][0] == "confirmed" and docs["https://panel.example/x"][0] == "ambiguous"
    assert _status(conn, rid) == "confirmed"
    assert _claims(conn, rid) == []  # same outcome as with the raw text: no claims from the mixed page


def test_case_a2_redacted_page_keeps_its_decision_on_re_resolution():
    case = C("A2", "X", [anchor(), D("https://panel.example/x", [
        (SUBJ_SENT, ""), ("Priya Raman, a pastry chef in Chennai, opened a bakery.", ""), (JAZZ, "")], "2026-05-01"),
        D("https://other.example/y", [("Unrelated page about chips.", "")], "2026-05-01")])
    conn, rid, _, _ = run_case(case, redact=True, subject_name=SUBJECT["name"])
    assert "privacy_redacted_after_identity" in json.loads(conn.execute(
        "SELECT flags_json FROM documents WHERE id='d01'").fetchone()[0])
    override_decision(conn, rid, "d02", "rejected", "reviewer: unrelated page")
    resolve_run(conn, rid, subject_of(case), today=TODAY)
    update_claims(conn, rid, subject_of(case), today=TODAY, settings=SETTINGS)
    assert _docs(conn, rid)["d01"] == "ambiguous" and _claims(conn, rid) == []


def test_case_b_shared_profile_conflict_still_fires():
    neutral = [OFFICIAL_2019, ("https://news.example/2026/orbit",
                               "Priya Raman joined Orbit Labs as CTO this month.", "2026-09-01", GH)]
    sensitive = [OFFICIAL_2019, ("https://news.example/2026/clinic",
                                 "Priya Raman joined Hope Recovery Clinic as a counsellor this month.", "2026-09-01",
                                 GH)]
    c1, r1 = run_production(neutral)
    c2, r2 = run_production(sensitive)
    assert _status(c1, r1) == "conflicting" and _status(c2, r2) == "conflicting"
    expl = json.loads(c2.execute("SELECT explanation_json FROM identity_resolutions WHERE run_id=? AND superseded_by"
                                 " IS NULL", (r2,)).fetchone()[0])
    assert any("removed as out of scope" in e for e in expl) and "clinic" not in json.dumps(expl).lower()
    assert _claims(c2, r2) == []


def test_case_c_staleness_cap_still_applies():
    c1, r1 = run_production(OLD_SUPPORT + [("https://news.example/c", "Priya Raman, CTO at Orbit Labs, unveiled a"
                                            " chip.", "2026-08-01", None)], NO_DOMAIN)
    c2, r2 = run_production(OLD_SUPPORT + [("https://news.example/c", "Priya Raman, chaplain at St Mary's Church, led"
                                            " a prayer service.", "2026-08-01", None)], NO_DOMAIN)
    assert _status(c1, r1) == "possible" and _status(c2, r2) == "possible"
    assert _claims(c2, r2) == []


def test_case_d_subject_mentioned_only_in_removed_text_stays_unresolved():
    conn, rid = run_production([("https://nimbuscompute.example/team/priya", "Priya Raman, VP of Infrastructure at"
                                 " Nimbus Compute, chairs our employee health council.", "2026-01-10", None)])
    assert _status(conn, rid) == "insufficient_evidence" and _claims(conn, rid) == []  # intended: coverage loss
    expl = json.loads(conn.execute("SELECT explanation_json FROM identity_resolutions WHERE run_id=? AND"
                                   " superseded_by IS NULL", (rid,)).fetchone()[0])
    assert any("removed as out of scope" in e for e in expl)


def test_removed_signal_interacts_safely_with_overrides_and_claim_supersession():
    page = ("https://panel.example/x", f"{SUBJ_SENT} {CHEF_SENSITIVE} Priya Raman is a member of the Rotary Club.",
            "2026-05-01", None)
    conn, rid = run_production([ANCHOR, page])
    assert _claims(conn, rid) == []  # ambiguous page: nothing gated
    doc = _by_url(conn, rid)["https://panel.example/x"][1]
    subj = Subject(**SUBJECT)
    override_decision(conn, rid, doc, "strong", "reviewer checked the page: the Rotary sentence is the subject")
    resolve_run(conn, rid, subj, today=TODAY)
    update_claims(conn, rid, subj, today=TODAY, settings=SETTINGS)
    gated = _claims(conn, rid)
    assert ("association", "rotary club", "tentative") in gated  # a reviewer decision is respected
    override_decision(conn, rid, doc, "ambiguous", "reviewer: on reflection, unclear")
    resolve_run(conn, rid, subj, today=TODAY)
    out = update_claims(conn, rid, subj, today=TODAY, settings=SETTINGS)
    assert _claims(conn, rid) == [] and out["superseded_by_reason"]["identity_changed"] == len(gated)


# ---------------------------------------------------------------- P1: no stale strong after redaction (release gate)


def test_p1_redacted_evidence_cannot_keep_a_stale_strong_status():
    case = C("P1", "X", [
        D("https://techpress.example/a", [("Priya Raman of Nimbus Compute spoke at the summit in Bengaluru.", ""),
                                          ("Priya Raman is a member of the Rotary Club.", "")], "2026-05-01"),
        D("https://summit.example/b", [("Priya Raman, VP of Infrastructure at Nimbus Compute, said capacity is"
                                        " growing.", ""), ("She is a mentor at Women Who Code.", "")], "2026-06-01"),
        D("https://unrelated.example/c", [("Unrelated page about chips.", "")], "2026-05-01")], subject=NO_DOMAIN)
    conn, rid, _, _ = run_case(case, redact=True, subject_name=SUBJECT["name"])
    # initial run judged the full text: d00 strong (organisation + the subject's location), two independent sources
    assert _docs(conn, rid)["d00"] == "strong" and _status(conn, rid) == "strongly_supported"
    keys = {c[1] for c in _claims(conn, rid)}
    assert {"rotary club", "women who code"} <= keys
    # end-of-run redaction removed the only sentence tying d00's name to the organisation
    assert "Bengaluru" not in conn.execute("SELECT text FROM documents WHERE id='d00'").fetchone()[0]

    subj = subject_of(case)

    def again():
        resolve_run(conn, rid, subj, today=TODAY)
        return update_claims(conn, rid, subj, today=TODAY, settings=SETTINGS)

    override_decision(conn, rid, "d02", "rejected", "reviewer: unrelated page")  # an unrelated identity change
    out = again()
    assert _docs(conn, rid)["d00"] == "insufficient" and _status(conn, rid) == "possible"
    assert _claims(conn, rid) == []  # the claims the stronger status had gated are withdrawn
    assert out["withheld"] == "identity_unresolved" and out["claims_superseded"] == 2
    assert {r[0] for r in conn.execute("SELECT supersede_reason FROM claims WHERE run_id=? AND superseded_by IS"
                                       " NOT NULL", (rid,))} == {"identity_changed"}
    again()  # repeated resolution is stable
    assert _docs(conn, rid)["d00"] == "insufficient" and _status(conn, rid) == "possible" and _claims(conn, rid) == []

    override_decision(conn, rid, "d00", "strong", "reviewer checked the original page")
    again()
    assert _docs(conn, rid)["d00"] == "strong" and _status(conn, rid) == "strongly_supported"
    assert {"rotary club", "women who code"} <= {c[1] for c in _claims(conn, rid)}
    override_decision(conn, rid, "d00", "insufficient", "reviewer: reversed after a second look")
    again()
    assert _docs(conn, rid)["d00"] == "insufficient" and _status(conn, rid) == "possible" and _claims(conn, rid) == []
    user = conn.execute("SELECT status, superseded_by FROM identity_decisions WHERE run_id=? AND decided_by='user'"
                        " AND target_ref='d00' ORDER BY id", (rid,)).fetchall()
    assert [u["status"] for u in user] == ["strong", "insufficient"] and user[0]["superseded_by"] is not None
