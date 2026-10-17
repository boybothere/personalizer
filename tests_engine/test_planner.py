"""Phase 3: adaptive planner and stopping logic (acceptance criteria A1-A14 in docs/engine/PHASE3_PLAN.md).

All offline and deterministic: a scripted Tavily answers by query text; pages are fictional (.example)."""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import httpx
import pytest
from conftest import ROOT, make_cfg
from world import PAGES, SUBJECT, World, decisions, has, run_world, site

from engine.config import PlannerSettings
from engine.observe import metrics
from engine.plan import ResearchInput, build_plan
from engine.policy import PolicyViolation, screen
from engine.research_run import preview
from engine.search import ledger
from engine.store import repo
from engine.store.db import SchemaOutdated, connect
from engine.store.migrations import migrate

BIO = "https://nimbuscompute.example/team/priya-raman"
PRESS = "https://techpress.example/2026/09/01/gpu-capacity"
SUMMIT = "https://summit.example/speakers/priya-raman"
CHEF = "https://chef.example/priya-raman"
NOISE = "https://unrelated.example/gpu-news"
BLOG = "https://blog.example/priya-writes"
NEWS = "https://news.example/2026/08/02/nimbus-launch"
IDENTITY_Q = lambda q, inc: '"Priya Raman" "Nimbus Compute"' in q and not any(  # noqa: E731
    w in q for w in ("announces", "conference"))


def status(conn, rid):
    r = repo.get_run(conn, rid)
    return r["status"], r["stop_reason"], {g["gate"]: g for g in r["gates"]}


def objectives(conn, rid):
    return metrics(conn, rid)["planner"]["objectives"]


def rich_world(**kw):
    return World(routes=[
        (IDENTITY_Q, [BIO, PRESS, CHEF]),
        (has("interview"), [PRESS, SUMMIT]),
        (has("wrote"), [BLOG]),
        (has("announces"), [NEWS]),
        (has("infrastructure"), [NOISE, SUMMIT]),
    ], **kw)


# ---------------------------------------------------------------- plan, policy (A11)

PREFERENCE_WORDS = ("love", "favourite", "favorite", "enjoy", "prefer", "like", "fan of", "passion", "hobby")


def test_plan_is_deterministic_and_has_no_preference_wording():
    inp = ResearchInput(**SUBJECT, focus_topics=["cricket"])
    a, b = build_plan(inp, PlannerSettings()), build_plan(inp, PlannerSettings())
    assert [[v.signature for v in o.variants] for o in a] == [[v.signature for v in o.variants] for o in b]
    texts = [v.spec.text().lower() for o in a for v in o.variants]
    assert not any(w in t for t in texts for w in PREFERENCE_WORDS)
    focus = next(o for o in a if o.key == "focus:cricket")
    assert focus.variants[0].spec.terms == ["cricket"] and focus.priority < 0.5


@pytest.mark.parametrize("purpose,focus", [
    ("Find out her religion before the meeting", []),
    ("Discuss partnership", ["health"]),
    ("Discuss partnership", ["where she lives"]),
    ("Discuss partnership", ["family members"]),
])
def test_policy_refuses_sensitive_purpose_or_focus(purpose, focus):
    with pytest.raises(PolicyViolation):
        screen(purpose, focus)


def test_refused_request_records_nothing():
    conn = connect(":memory:")
    with pytest.raises(PolicyViolation):
        run_world(World(), conn=conn, meeting_purpose="find her home address")
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_dry_run_preview_needs_no_network():
    plan = preview(ResearchInput(**SUBJECT), make_cfg())
    assert plan[0]["objective"] == "identity" and all(p["variants"] or p["status"] == "exhausted" for p in plan)


# ---------------------------------------------------------------- A1 signals drive selection, A10 trace


def test_query_sequence_depends_on_measured_signals():
    strong = run_world(rich_world())
    weak = run_world(World(routes=[(IDENTITY_Q, [CHEF]), (has("pastry"), [CHEF])]))
    seq = lambda r: [d["objective_key"] for d in decisions(*r) if d["decision"] == "continue"]  # noqa: E731
    assert seq(strong)[:2] == ["identity", "official_profile"]  # identity covered at step 1 -> move on
    assert set(seq(weak)) == {"identity"}  # identity never supported -> only identity work, then stop
    d1 = decisions(*strong)[0]
    scores = json.loads(d1["scores_json"])
    assert scores["identity"]["score"] > 0 and scores["appearances"]["skipped"].startswith("waiting for identity")


def test_every_step_is_explained_in_the_trace():
    conn, rid = run_world(rich_world())
    rows = decisions(conn, rid)
    steps = [d for d in rows if d["decision"] == "continue"]
    assert steps and rows[-1]["decision"] == "stop" and rows[-1]["decision_reason"].startswith(rows[-1]["decision_reason"].split(":")[0])
    for d in steps:
        assert d["selection_reason"] and "highest score" in d["selection_reason"]
        c = json.loads(d["contribution_json"])
        assert {"new_urls", "relevant_docs", "duplicates", "off_target", "irrelevant_docs"} <= set(c)
        assert "Identity signal" in d["decision_reason"] and json.loads(d["query_ids_json"])
    planner_traces = conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id=? AND stage='planner'", (rid,)).fetchone()[0]
    assert planner_traces == len(rows)


# ---------------------------------------------------------------- A2 follow-ups from discoveries


def _followups(conn, rid):
    return [f for d in decisions(conn, rid) for f in json.loads(d["followups_json"])]


def test_official_domain_and_handle_followups():
    conn, rid = run_world(rich_world(), organization_domain=None)
    f = _followups(conn, rid)
    official = next(x for x in f if x["origin"] == "followup:official_domain")
    assert official["spec"]["sites"] == ["nimbuscompute.example"] and "organisation name in domain" in official["reason"]
    handle = next(x for x in f if x["origin"] == "followup:handle")
    assert handle["spec"]["sites"] == ["medium.com/@priyaraman"]
    # discoveries are acted on promptly: the handle query is issued on the very next step
    rows = [d for d in decisions(conn, rid) if d["decision"] == "continue"]
    found = next(i for i, d in enumerate(rows) if "followup:handle" in d["followups_json"])
    assert json.loads(rows[found + 1]["spec_json"])["sites"] == ["medium.com/@priyaraman"]
    assert "discovery 1.6" in rows[found + 1]["selection_reason"]


def test_weak_identity_triggers_disambiguation():
    conn, rid = run_world(World(routes=[(IDENTITY_Q, [CHEF])]))
    dis = [x for x in _followups(conn, rid) if x["origin"] == "followup:weak_identity"]
    assert len(dis) == 2 and "name the person without the organisation" in dis[0]["reason"]
    issued = [json.loads(d["spec_json"]) for d in decisions(conn, rid) if d["spec_json"]]
    assert any(s["phrases"] == ["Priya Raman", "VP of Infrastructure"] for s in issued)


def test_diversity_followup_excludes_seen_domains():
    from world import page, para
    same_domain = {f"https://techpress.example/a{i}": page(para(
        f"Interview {i}: Priya Raman of Nimbus Compute discussed topic number {i} with our reporter in depth."))
        for i in range(3)}
    w = World(routes=[(IDENTITY_Q, [BIO, PRESS, SUMMIT]),
                      (has("interview"), ["https://techpress.example/a0", "https://techpress.example/a1"])],
              pages=same_domain)
    cfg = make_cfg()
    cfg.planner.global_window = 10  # by design diversity ranks below objectives with no coverage at all
    conn, rid = run_world(w, cfg=cfg)
    div = [x for x in _followups(conn, rid) if x["origin"] == "followup:diversity"]
    assert div and "techpress.example" in div[0]["spec"]["exclude_sites"]
    assert any(q.get("exclude_domains") == ["techpress.example"] for q in w.queries)  # actually sent


# ---------------------------------------------------------------- A3 duplicates, A4 irrelevant results


def test_duplicate_results_are_not_progress_and_not_refetched():
    w = World(routes=[(lambda q, inc: True, [BIO, PRESS, SUMMIT])])
    conn, rid = run_world(w)
    assert len(w.page_requests) == len(set(w.page_requests)) == 3  # each page fetched once
    st, stop, _ = status(conn, rid)
    assert stop == "DIMINISHING_RETURNS" and st == "completed"
    later = [json.loads(d["contribution_json"]) for d in decisions(conn, rid)[1:] if d["contribution_json"]]
    assert later and all(c["new_urls"] == 0 and c["relevant_docs"] == 0 and c["duplicates"] + c["off_target"] == 3
                         for c in later)


def test_irrelevant_and_name_only_pages_never_count_as_coverage():
    w = World(routes=[(lambda q, inc: True, [NOISE, CHEF])])
    conn, rid = run_world(w)
    m = metrics(conn, rid)
    assert all(o["relevant_docs"] == 0 for o in m["planner"]["objectives"].values())
    c = json.loads(decisions(conn, rid)[0]["contribution_json"])
    assert c["irrelevant_docs"] == 1 and c["weak_docs"] == 1


# ---------------------------------------------------------------- A5 identity


def test_unresolved_identity_fails_the_run_and_blocks_other_research():
    w = World(routes=[(lambda q, inc: True, [CHEF])])
    conn, rid = run_world(w)
    st, stop, gates = status(conn, rid)
    assert (st, stop) == ("failed", "IDENTITY_UNRESOLVED") and not gates["identity_resolved"]["passed"]
    assert {d["objective_key"] for d in decisions(conn, rid) if d["decision"] == "continue"} == {"identity"}


# ---------------------------------------------------------------- A6 provider errors and credits


def _fail_after(n_ok: int, response: httpx.Response):
    return lambda request, n: None if n <= n_ok else response


def test_provider_error_streak_stops_partial():
    w = rich_world(tavily_override=_fail_after(1, httpx.Response(500)))
    conn, rid = run_world(w)
    st, stop, _ = status(conn, rid)
    assert (st, stop) == ("partial", "PROVIDER_ERRORS") and len(w.queries) == 4  # 1 ok + 3 failures


def test_provider_errors_with_nothing_found_fail():
    conn, rid = run_world(rich_world(tavily_override=_fail_after(0, httpx.Response(503))))
    assert status(conn, rid)[:2] == ("failed", "PROVIDER_ERRORS")


def test_exhausted_credits_stop_partial():
    w = rich_world(tavily_override=_fail_after(1, httpx.Response(432, text="plan limit")))
    conn, rid = run_world(w)
    st, stop, _ = status(conn, rid)
    assert (st, stop) == ("partial", "NO_PROVIDER_AVAILABLE") and len(w.queries) == 2
    assert "SEARCH_QUOTA_EXHAUSTED" in decisions(conn, rid)[-1]["decision_reason"]


def test_monthly_free_tier_guard_stops_before_any_request():
    conn = connect(":memory:")
    ledger.record(conn, "tavily", "search", "credit", 900, 7.2, None, None)
    w = rich_world()
    conn, rid = run_world(w, conn=conn)
    assert w.queries == [] and status(conn, rid)[1] == "NO_PROVIDER_AVAILABLE"
    assert "SEARCH_FREE_TIER_GUARD" in decisions(conn, rid)[-1]["decision_reason"]
    assert status(conn, rid)[0] != "completed"


# ---------------------------------------------------------------- A7 every budget limit


@pytest.mark.parametrize("limit,value,code", [
    ("max_search_requests", 2, "BUDGET_SEARCHES"),
    ("max_cost_usd", 0.017, "BUDGET_COST"),  # 2 searches at $0.008 list price fit, the third does not
    ("max_fetch_attempts", 1, "BUDGET_FETCHES"),
    ("max_total_bytes", 900, "BUDGET_BYTES"),
    ("max_robots_requests", 1, "BUDGET_ROBOTS"),
    ("max_leads", 2, "BUDGET_LEADS"),
])
def test_each_budget_limit_stops_with_its_code_and_never_completes(limit, value, code):
    cfg = make_cfg(**{limit: value})
    cfg.fetch.global_concurrency = 1
    conn, rid = run_world(rich_world(), cfg=cfg)
    st, stop, gates = status(conn, rid)
    assert stop == code and st in ("partial", "failed") and st != "completed"
    assert gates.get("planner_finished", {"passed": False})["passed"] is False


def test_time_budget_stops_partial_or_failed():
    w = World(routes=[(IDENTITY_Q, [BIO, PRESS])])

    async def slow(request):
        await asyncio.sleep(1.0)
        return PAGES[BIO]
    w.pages[PRESS] = slow
    conn, rid = run_world(w, cfg=make_cfg(max_wall_seconds=0.5))
    st, stop, _ = status(conn, rid)
    assert stop == "BUDGET_TIME" and st != "completed"


def test_lead_limit_still_fetches_already_accepted_leads():
    # regression found while measuring budget behaviour: hitting max_leads abandoned the accepted leads
    conn, rid = run_world(rich_world(), cfg=make_cfg(max_leads=2))
    st, stop, _ = status(conn, rid)
    m = metrics(conn, rid)
    assert (st, stop) == ("partial", "BUDGET_LEADS") and m["leads"]["total"] == 2 and m["fetch"]["urls_retrieved"] == 2


def test_search_budget_is_checked_before_sending():
    w = rich_world()
    run_world(w, cfg=make_cfg(max_search_requests=3))
    assert len(w.queries) == 3


# ---------------------------------------------------------------- A8 diminishing returns and early stopping


def test_diminishing_returns_stop_well_inside_budget():
    w = World(routes=[(IDENTITY_Q, [BIO, PRESS])])  # identity found, nothing else anywhere
    cfg = make_cfg()
    conn, rid = run_world(w, cfg=cfg)
    st, stop, _ = status(conn, rid)
    assert (st, stop) == ("completed", "DIMINISHING_RETURNS")
    assert len(w.queries) == 1 + cfg.planner.global_window < cfg.budget.max_search_requests
    assert metrics(conn, rid)["planner"]["coverage_gaps"]  # gaps are reported, never hidden


def test_plan_resolves_early_when_coverage_is_met():
    w = World(routes=[
        (IDENTITY_Q, [BIO, PRESS]), (site("nimbuscompute.example"), [BIO]),
        (has("infrastructure"), [PRESS, SUMMIT]), (has("announces"), [NEWS, PRESS]),
        (has("interview"), [PRESS, SUMMIT]), (has("wrote"), [BLOG, PRESS]),
        (site("github.com"), ["https://github.com/priyaraman"]), (site("bsky.app"), ["https://bsky.app/profile/priyaraman"]),
    ], pages={"https://github.com/priyaraman": PAGES[BLOG], "https://bsky.app/profile/priyaraman": PAGES[BLOG]})
    w.pages.pop("https://github.com/priyaraman")  # profile pages: leads only matter for discovery objective
    conn, rid = run_world(w)
    st, stop, _ = status(conn, rid)
    obj = objectives(conn, rid)
    assert (st, stop) == ("completed", "PLAN_RESOLVED"), obj
    assert len(w.queries) < 15 and obj["identity"]["status"] == "covered"


# ---------------------------------------------------------------- A9 determinism


def test_same_world_same_decisions():
    def trace():
        conn, rid = run_world(rich_world())
        return [(d["objective_key"], d["spec_json"], d["decision"], d["decision_reason"]) for d in decisions(conn, rid)]
    assert trace() == trace()


# ---------------------------------------------------------------- A11 no preferences manufactured


def test_planner_produces_no_preference_records():
    conn, rid = run_world(rich_world(), focus_topics=["cricket"])
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # phase 6 adds `claims`/`claim_evidence`, written only by the identity-gated claims stage, never by the planner
    assert not {t for t in tables if "prefer" in t or "claim" in t} - {"claims", "claim_evidence"}
    assert not any("claim" in json.dumps(dict(d)).lower() for d in decisions(conn, rid))
    issued = [json.loads(d["spec_json"]) for d in decisions(conn, rid) if d["spec_json"]]
    assert not any(w in json.dumps(s).lower() for s in issued for w in PREFERENCE_WORDS)


# ---------------------------------------------------------------- A12 existing records untouched


LOCAL_DB = ROOT / "data" / "engine.db"


@pytest.mark.skipif(not LOCAL_DB.exists(), reason="local Phase 1/2 database not present")
def test_existing_phase1_2_records_survive_migration_on_a_copy(tmp_path):
    original_hash = hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest()
    copy = tmp_path / "engine_copy.db"
    shutil.copy2(LOCAL_DB, copy)
    with pytest.raises(SchemaOutdated):
        connect(copy)  # never migrated implicitly
    conn = connect(copy, upgrade=False)
    runs = [r[0] for r in conn.execute("SELECT id FROM runs ORDER BY created_at")]
    counts = lambda: {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
                      for t in ("runs", "leads", "urls", "fetch_attempts", "documents", "search_queries",
                                "search_results", "provider_ledger")}
    strip = lambda m: {k: v for k, v in m.items() if k != "planner"}  # noqa: E731
    before = {r: strip(metrics(conn, r)) for r in runs}
    rows_before = counts()
    assert migrate(conn, 3) == ["up 3 adaptive_planner"]
    assert {r: strip(metrics(conn, r)) for r in runs} == before and counts() == rows_before
    assert all(metrics(conn, r)["planner"]["available"] is False for r in runs)
    assert migrate(conn, 2) == ["down 3 adaptive_planner"] and counts() == rows_before
    conn.close()
    assert hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest() == original_hash


# ---------------------------------------------------------------- A14 ledger caveat


def test_ledger_summary_is_flagged_as_unverified():
    s = ledger.summary(connect(":memory:"), make_cfg().providers)
    assert "NOT a verified account balance" in s["_caveat"] and "unreconciled" in s["_caveat"]
