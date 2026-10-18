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
