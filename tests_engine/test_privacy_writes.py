"""Corrective pass: out-of-scope content is filtered at the write boundary, for every entry point.

All data is synthetic; all web and provider traffic is the offline fake web (MockTransport)."""

import json

import httpx
import pytest
from conftest import Site, article, run
from test_search import FakeAPIs, run_search
from world import World, page, run_world

from engine import privacy
from engine.claims import scope
from engine.failures import Code
from engine.search.spec import SearchSpec
from engine.store import repo
from engine.store.db import connect
from engine.urls import check

SENSITIVE_SENTENCES = [
    "Priya Raman lives in Indiranagar with her husband.",
    "She was treated at a hospital last year.",
    "Priya Raman, a volunteer at Narcotics Anonymous, shared her story.",
]
SENSITIVE_URL = "https://profiles.example/priya-raman-hospital-stay"
IN_SCOPE = "Priya Raman, VP of Infrastructure at Nimbus Compute, spoke about chips."


def scan(directory, needles):
    hits = []
    for f in sorted(directory.iterdir()):
        raw = f.read_bytes()
        hits += [(f.name, n) for n in needles if n.encode() in raw]
    return hits


NEEDLES = SENSITIVE_SENTENCES + ["hospital-stay", "Narcotics Anonymous", "narcotics anonymous"]


# ---------------------------------------------------------------- classification and URLs


def test_support_group_regression_is_out_of_scope_at_storage_level():
    assert privacy.category("Priya Raman, a volunteer at Narcotics Anonymous, shared her story.") == "health"
    assert privacy.category("She attends AA meetings on Tuesdays.") == "health"
    assert privacy.category(IN_SCOPE) is None


def test_storage_level_keeps_identity_relevant_places_but_claim_level_drops_them():
    s = "Priya Raman is a pastry chef in Chennai who opened a bakery."
    assert privacy.category(s) is None  # needed to recognise a namesake; removed by end-of-run redaction
    assert scope.classify(s, privacy._today()) == "place_undated"


def test_sensitive_url_paths_are_never_requested_and_stored_only_as_a_hash():
    _, decision = check(SENSITIVE_URL, None)
    assert not decision.allowed and decision.code == Code.URL_OUT_OF_SCOPE
    assert check("https://profiles.example/priya-raman", None)[1].allowed
    assert privacy.safe_url(SENSITIVE_URL).startswith("withheld:out_of_scope:")
    assert privacy.safe_url("https://news.example/2026/09/gpu-capacity") == "https://news.example/2026/09/gpu-capacity"
    assert privacy.scrub_urls(f"redirect to {SENSITIVE_URL} refused").startswith("redirect to withheld:")


# ---------------------------------------------------------------- each repo write function


def test_every_repo_write_function_filters_before_storing():
    conn = connect(":memory:")
    rid = repo.create_run(conn, "retrieval", {}, {}, run_days=30)
    lead = repo.add_lead(conn, rid, SENSITIVE_URL, "search_result", snippet=" ".join(SENSITIVE_SENTENCES),
                         origin_ref=f"see {SENSITIVE_URL}")
    uid = repo.add_url(conn, rid, url_key="k", canonical_url=SENSITIVE_URL, domain="profiles.example",
                       registrable_domain="profiles.example", platform=None, decision="rejected",
                       reject_code="URL_OUT_OF_SCOPE", reject_detail=f"was {SENSITIVE_URL}")
    repo.add_attempt(conn, rid, uid, {"attempt_no": 1, "request_url": SENSITIVE_URL, "outcome": "failure",
                                      "detail": f"redirected to {SENSITIVE_URL}"})
    repo.add_robots_check(conn, rid, "https://profiles.example", 200, "parsed", None, f"via {SENSITIVE_URL}", None)
    text = " ".join([IN_SCOPE] + SENSITIVE_SENTENCES)
    did = repo.add_document(conn, rid, uid, None, {
        "evidence_kind": "fetched_page", "source_url": SENSITIVE_URL, "canonical_link": SENSITIVE_URL,
        "title": "Priya Raman hospital stay", "extractor": "t", "text": text, "usable": True,
        "metadata": {"same_as": [SENSITIVE_URL, "https://github.com/priyaraman"]}}, text_days=7)
    repo.trace(conn, rid, "retrieval", "fetched", reason=f"final {SENSITIVE_URL}", data={"final_url": SENSITIVE_URL})
    dump = json.dumps([[dict(r) for r in conn.execute(f"SELECT * FROM {t}")]
                       for t in ("leads", "urls", "fetch_attempts", "robots_checks", "documents", "trace_events")],
                      default=str)
    assert not [n for n in NEEDLES + [SENSITIVE_URL, "hospital stay"] if n in dump]
    stored = conn.execute("SELECT text, title FROM documents WHERE id=?", (did,)).fetchone()
    assert stored["text"].startswith(IN_SCOPE) and len(stored["text"]) == len(text)  # in-scope kept, offsets kept
    assert "github.com/priyaraman" in dump  # ordinary provenance is preserved
    assert lead


# ---------------------------------------------------------------- entry points: search, retrieve, research


def test_search_entry_point_filters_titles_snippets_and_urls(tmp_path):
    def tavily(request):
        return httpx.Response(200, json={"results": [
            {"url": SENSITIVE_URL, "title": "Priya Raman hospital stay", "content": SENSITIVE_SENTENCES[1]},
            {"url": "https://ok.example/a", "title": "In-scope title", "content": SENSITIVE_SENTENCES[0]}],
            "usage": {"credits": 1}})

    apis = FakeAPIs(overrides={("api.tavily.com", "/search"): tavily})
    spec = SearchSpec(purpose="test", phrases=["Priya Raman"])
    conn, rid = run_search(apis, [spec], Site({"/a": httpx.Response(200, html=article(IN_SCOPE))}),
                           conn=connect(tmp_path / "s.db"), enabled=("tavily",))
    rows = conn.execute("SELECT url, title, snippet FROM search_results WHERE run_id=?", (rid,)).fetchall()
    assert any(r["url"].startswith("withheld:out_of_scope:") for r in rows)
    assert {r["title"] for r in rows} >= {"In-scope title"}
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert scan(tmp_path, NEEDLES + ["Priya Raman hospital stay"]) == []
    conn.close()


def test_retrieve_entry_point_filters_pages_and_refuses_sensitive_redirects(tmp_path):
    site = Site({"/page": httpx.Response(200, html=article(IN_SCOPE + " " + " ".join(SENSITIVE_SENTENCES), 4)),
                 "/moved": httpx.Response(302, headers={"location": "/priya-raman-hospital-stay"}),
                 "/priya-raman-hospital-stay": httpx.Response(200, html=article("Should never be fetched.", 20))})
    conn, rid = run(site, ["https://ok.example/page", "https://ok.example/moved", SENSITIVE_URL],
                    conn=connect(tmp_path / "r.db"))
    assert "/priya-raman-hospital-stay" not in [r.url.path for r in site.requests]
    codes = {r[0] for r in conn.execute("SELECT reject_code FROM urls WHERE run_id=?", (rid,))}
    failures = {r[0] for r in conn.execute("SELECT failure_code FROM fetch_attempts WHERE run_id=?", (rid,))}
    assert Code.URL_OUT_OF_SCOPE.value in codes | failures
    text = conn.execute("SELECT text FROM documents WHERE run_id=? AND usable=1", (rid,)).fetchone()[0]
    assert IN_SCOPE in text
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert scan(tmp_path, NEEDLES + ["Should never be fetched"]) == []
    conn.close()


def _world():
    return World(routes=[(lambda q, inc: True, ["https://nimbuscompute.example/team/priya-raman", SENSITIVE_URL,
                                                "https://profiles.example/priya"])],
                 pages={"https://profiles.example/priya": page(" ".join(
                     [IN_SCOPE] + SENSITIVE_SENTENCES + ["Priya Raman is a member of the Open Compute Project."] * 4)),
                     SENSITIVE_URL: page("Should never be fetched. " * 20)},
                 tavily_override=lambda req, n: httpx.Response(200, json={"results": [
                     {"url": SENSITIVE_URL, "title": "T", "content": SENSITIVE_SENTENCES[1]},
                     {"url": "https://profiles.example/priya", "title": "T", "content": "x"},
                     {"url": "https://nimbuscompute.example/team/priya-raman", "title": "T", "content": "x"}],
                     "usage": {"credits": 1}}) if b"/search" in req.url.raw_path else None)


@pytest.mark.parametrize("stage_fn", ["scrub_transient", "origins.compute", "resolve_run", "update_claims",
                                      "redact_run"])
def test_interrupted_run_leaves_no_storage_level_content_on_disk(tmp_path, monkeypatch, stage_fn):
    import engine.research_run as rr

    def crash(*a, **k):
        raise RuntimeError(f"simulated crash in {stage_fn}")

    if stage_fn == "origins.compute":
        monkeypatch.setattr(rr.origins, "compute", crash)
    else:
        monkeypatch.setattr(rr, stage_fn, crash)
    conn = connect(tmp_path / "c.db")
    with pytest.raises(RuntimeError):
        run_world(_world(), conn=conn, organization_domain="nimbuscompute.example")
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "running"  # left behind by the crash
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE text LIKE ?", (f"%{IN_SCOPE}%",)).fetchone()[0] == 1
    conn.close()  # the last connection closing checkpoints the WAL into the main file
    assert scan(tmp_path, NEEDLES) == []


def test_identity_records_withhold_out_of_scope_organisation_labels():
    from claim_cases import C, D, anchor
    from claims_harness import run_case

    case = C("leak", "X", [anchor(), D("https://support.example/x", [
        ("Priya Raman, a volunteer at Narcotics Anonymous, shared her story.", "out_of_scope")], "2026-05-01")])
    conn, rid, _, _ = run_case(case)
    dump = json.dumps([[dict(r) for r in conn.execute(f"SELECT * FROM {t} WHERE run_id=?", (rid,))]
                       for t in ("identity_resolutions", "identity_decisions", "trace_events", "claims")])
    assert "narcotics" not in dump.lower()
    keys = {r[0] for r in conn.execute("SELECT candidate_key FROM identity_decisions WHERE run_id=?", (rid,))}
    assert any(k.startswith("namesake:out_of_scope:") for k in keys)  # still a separate namesake, label withheld
    assert conn.execute("SELECT status FROM identity_resolutions WHERE run_id=? AND superseded_by IS NULL",
                        (rid,)).fetchone()[0] == "confirmed"


def test_research_entry_point_end_to_end(tmp_path):
    conn, rid = run_world(_world(), conn=connect(tmp_path / "w.db"), organization_domain="nimbuscompute.example")
    reqs = [r for r in conn.execute("SELECT request_url FROM fetch_attempts WHERE run_id=?", (rid,))]
    assert not any("hospital" in (r[0] or "") for r in reqs)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert scan(tmp_path, NEEDLES + ["Should never be fetched"]) == []
    conn.close()
