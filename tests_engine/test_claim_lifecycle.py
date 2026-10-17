"""Corrective pass: claim lifecycle when identity decisions change (plan §2.4.1). Synthetic data only."""

import json
from datetime import timedelta

from claim_cases import TODAY, C, anchor, blog, news
from claims_harness import SETTINGS, run_case, subject_of

from engine.claims.store import update_claims
from engine.identity.store import override_decision, resolve_run
from engine.store import repo


def _case():
    return C("life", "L", [
        anchor(),
        blog("me", [("I love trail running.", "pref+"), ("I play chess on weekends.", "activity")]),
        news("papera", "x", 0, [("She is a member of the Rotary Club.", "association"),
                                ('"I love pottery classes," Raman said.', "pref+")]),
        news("paperb", "y", 1, [('In the interview she said: "I really love pottery classes."', "pref+"),
                                ("Raman wrote about GPU scheduling in 2025.", "activity")], "2026-07-01")])


def _active(conn, rid):
    return {(c["claim_type"], c["object_key"]): (c["id"], c["status"])
            for c in conn.execute("SELECT * FROM claims WHERE run_id=? AND superseded_by IS NULL", (rid,))}


def _change(conn, rid, case, doc, status, note):
    override_decision(conn, rid, doc, status, note)
    resolve_run(conn, rid, subject_of(case), today=TODAY)
    return update_claims(conn, rid, subject_of(case), today=TODAY, settings=SETTINGS)


def test_initial_extraction_is_labelled_initial():
    conn, rid, _, result = run_case(_case())
    assert result["reason"] == "initial" and result["claims_superseded"] == 0
    reasons = [r[0] for r in conn.execute("SELECT reason FROM trace_events WHERE run_id=? AND stage='claims'", (rid,))]
    assert reasons == ["initial"]
    active = _active(conn, rid)
    assert active[("stated_preference", "pottery classes")][1] == "strongly_supported"
    assert active[("stated_preference", "trail running")][1] == "verified"


def test_only_claims_tied_to_the_changed_page_are_superseded():
    case = _case()
    conn, rid, _, _ = run_case(case)
    before = _active(conn, rid)
    out = _change(conn, rid, case, "d03", "rejected", "reviewer: page B is about someone else")
    after = _active(conn, rid)
    # unaffected claims keep the same row
    for key in [("stated_preference", "trail running"), ("activity", "chess"), ("association", "rotary club")]:
        assert after[key] == before[key]
    # the claim cited only by page B is gone; the corroborated claim is recomputed from page A alone
    assert ("activity", "gpu scheduling") not in after
    assert after[("stated_preference", "pottery classes")][1] == "tentative"
    assert after[("stated_preference", "pottery classes")][0] != before[("stated_preference", "pottery classes")][0]
    sup = dict(conn.execute("SELECT object_key, supersede_reason FROM claims WHERE run_id=? AND superseded_by IS NOT"
                            " NULL", (rid,)).fetchall())
    assert sup == {"pottery classes": "identity_changed", "gpu scheduling": "identity_changed"}
    assert out["reason"] == "identity_changed" and out["claims_kept"] == 3


def test_repeated_identity_changes_restore_claims_with_a_full_history():
    case = _case()
    conn, rid, _, _ = run_case(case)
    first = {k: v[1] for k, v in _active(conn, rid).items()}
    _change(conn, rid, case, "d03", "rejected", "reviewer: wrong person")
    _change(conn, rid, case, "d03", "strong", "reviewer: it was the right person after all")
    assert {k: v[1] for k, v in _active(conn, rid).items()} == first  # same claims and statuses again
    history = conn.execute("SELECT object_key, supersede_reason FROM claims WHERE run_id=? AND superseded_by IS NOT"
                           " NULL ORDER BY id", (rid,)).fetchall()
    assert [tuple(h) for h in history] == [("pottery classes", "identity_changed"),
                                           ("gpu scheduling", "identity_changed"),
                                           ("pottery classes", "identity_changed")]
    user = conn.execute("SELECT status, superseded_by FROM identity_decisions WHERE run_id=? AND decided_by='user'"
                        " ORDER BY id", (rid,)).fetchall()
    assert [u["status"] for u in user] == ["rejected", "strong"] and user[0]["superseded_by"] is not None


def test_identity_downgrade_cannot_leave_a_claim_verified():
    case = _case()
    conn, rid, _, _ = run_case(case)
    assert _active(conn, rid)[("stated_preference", "trail running")][1] == "verified"
    _change(conn, rid, case, "d01", "historical", "reviewer: the personal site is old")
    after = conn.execute("SELECT status, flags_json, identity_status FROM claims WHERE run_id=? AND superseded_by IS"
                         " NULL AND object_key='trail running'", (rid,)).fetchone()
    assert after["status"] != "verified" and "historical_identity" in json.loads(after["flags_json"])
    assert after["identity_status"] == "confirmed"  # identity confidence is reported separately from claim status


def test_subject_unresolved_supersedes_every_claim():
    case = _case()
    conn, rid, _, _ = run_case(case)
    for d in ("d00", "d01", "d02", "d03"):
        override_decision(conn, rid, d, "ambiguous", "reviewer: cannot tell these people apart")
    resolve_run(conn, rid, subject_of(case), today=TODAY)
    out = update_claims(conn, rid, subject_of(case), today=TODAY, settings=SETTINGS)
    assert out["withheld"] == "identity_unresolved" and _active(conn, rid) == {}


def test_after_text_expiry_only_affected_claims_are_superseded():
    case = _case()
    conn, rid, _, _ = run_case(case)
    before = _active(conn, rid)
    repo.purge(conn, repo.now() + timedelta(days=8))
    out = _change(conn, rid, case, "d03", "rejected", "reviewer: wrong person")
    assert out["incomplete"] == "text_expired" and out["claims_superseded"] == 2
    after = _active(conn, rid)
    assert set(after) == {("stated_preference", "trail running"), ("activity", "chess"), ("association", "rotary club")}
    assert all(after[k] == before[k] for k in after)  # unaffected claims untouched, nothing invented
