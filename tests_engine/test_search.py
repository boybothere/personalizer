"""Search layer: rendering, parsing, accounting, budgets, free-tier guard, provider failures, storage
policies from provider terms, provider-extract fallback rules, provider metrics and deletion."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from conftest import TODAY, RecordingSleep, Site, article, make_cfg

from engine.failures import Code
from engine.fetch.client import StaticResolver
from engine.observe import metrics, provider_metrics
from engine.search import ledger
from engine.search.providers import BraveProvider, ExaProvider, TavilyProvider
from engine.search.spec import SearchSpec, matches_site, off_target
from engine.search_run import search_run
from engine.store import repo
from engine.store.db import connect

KEYS = {"brave": "BRAVE-KEY-SECRET-123", "tavily": "tvly-SECRET-456", "exa": "EXA-SECRET-789"}


def spec(**kw):
    base = {"purpose": "test", "phrases": ["robots exclusion protocol"]}
    base.update(kw)
    return SearchSpec(**base)


# ---------------------------------------------------------------- specs and rendering


def test_spec_requires_purpose_and_content():
    with pytest.raises(ValueError):
        SearchSpec(purpose=" ", phrases=["x"])
    with pytest.raises(ValueError):
        SearchSpec(purpose="why")


@pytest.mark.parametrize("url,site,ok", [
    ("https://github.com/adbar/trafilatura", "github.com/adbar", True),
    ("https://github.com/adbarxyz/repo", "github.com/adbar", False),
    ("https://docs.github.com/x", "github.com", True),
    ("https://notgithub.com/x", "github.com", False),
    ("https://www.medium.com/@a/post", "medium.com", True),
])
def test_site_matching(url, site, ok):
    assert matches_site(url, site) == ok


def test_off_target_respects_sites_and_exclusions():
    s = spec(sites=["rfc-editor.org"], exclude_sites=["rfc-editor.org/errata"])
    assert not off_target("https://www.rfc-editor.org/rfc/rfc9309.html", s)
    assert off_target("https://example.com/rfc9309", s)
    assert off_target("https://www.rfc-editor.org/errata/eid1", s)


def _client():
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))


def test_brave_rendering():
    p = BraveProvider("k", client=_client())
    r = p.render(spec(terms=["crawler"], any_of=["robots.txt", "web crawler"], sites=["a.com", "b.org/x"],
                      exclude_sites=["c.net"], freshness_days=7))
    assert r.params["q"] == ('"robots exclusion protocol" crawler (robots.txt OR "web crawler") '
                             "(site:a.com OR site:b.org/x) -site:c.net")
    assert r.params["freshness"] == "pw" and "path" in r.notes[0]
    assert "X-Subscription-Token" in r.headers and "headers" not in r.public()


def test_tavily_rendering_reports_what_it_cannot_express():
    p = TavilyProvider("k", client=_client())
    r = p.render(spec(any_of=["a", "b"], sites=["github.com/adbar"], exclude_sites=["x.com"], freshness_days=30))
    assert r.json["include_domains"] == ["github.com"] and r.json["exclude_domains"] == ["x.com"]
    assert r.json["include_raw_content"] is False and r.json["start_date"]
    assert any("OR groups" in n for n in r.notes) and any("approximated" in n for n in r.notes)


def test_exa_rendering_uses_path_filters_and_requests_no_contents():
    p = ExaProvider("k", client=_client())
    r = p.render(spec(sites=["github.com/adbar"], freshness_days=10))
    assert r.json["includeDomains"] == ["github.com/adbar"] and "contents" not in r.json
    assert r.json["startPublishedDate"].endswith("T00:00:00.000Z") and "not guaranteed" in r.notes[0]


# ---------------------------------------------------------------- fake provider APIs


class FakeAPIs:
    """Scripted Brave/Tavily/Exa endpoints; records every request."""

    def __init__(self, results: dict[str, list[str]] | None = None, overrides: dict | None = None):
        self.results = results or {}
        self.overrides = overrides or {}  # (host, path) -> callable or iterator of responses
        self.calls: list[httpx.Request] = []

    def count(self, host_part: str, path: str | None = None) -> int:
        return sum(host_part in r.url.host and (path is None or r.url.path == path) for r in self.calls)

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        key = (request.url.host, request.url.path)
        if key in self.overrides:
            o = self.overrides[key]
            return next(o) if hasattr(o, "__next__") else o(request)
        urls = self.results.get(request.url.host.split(".")[-2], [])
        if "brave" in request.url.host:
            return httpx.Response(200, json={"web": {"results": [
                {"url": u, "title": f"BRAVE-TITLE {i}", "description": f"BRAVE-SNIPPET-{i}"} for i, u in enumerate(urls)]}},
                headers={"X-RateLimit-Remaining": "1, 999"})
        if "tavily" in request.url.host and request.url.path == "/search":
            return httpx.Response(200, json={"results": [
                {"url": u, "title": f"T{i}", "content": f"TAVILY-SNIPPET-{i}", "published_date": "2026-09-01"}
                for i, u in enumerate(urls)], "usage": {"credits": 1}, "request_id": "tv-1"})
        if "tavily" in request.url.host and request.url.path == "/extract":
            body = json.loads(request.content)
            return httpx.Response(200, json={"results": [{"url": body["urls"][0],
                                                          "raw_content": "Provider copy of the page. " * 20}],
                                             "failed_results": [], "usage": {"credits": 1}})
        if "exa" in request.url.host:
            return httpx.Response(200, json={"requestId": "ex-1", "costDollars": {"total": 0.007},
                                             "results": [{"url": u, "title": f"EXA-TITLE {i}",
                                                          "publishedDate": "2026-08-01T00:00:00.000Z"}
                                                         for i, u in enumerate(urls)]})
        return httpx.Response(404)


def run_search(apis: FakeAPIs, specs, site: Site | None = None, cfg=None, conn=None, retrieve=True,
               enabled=("brave", "tavily", "exa"), keys=KEYS):
    cfg = cfg or make_cfg()
    cfg.providers.enabled = list(enabled)
    for name in ("brave", "tavily", "exa"):
        setattr(cfg.providers, f"{name}_api_key", keys.get(name))
    conn = conn or connect(":memory:")
    client = httpx.AsyncClient(transport=httpx.MockTransport(apis))
    from engine.search.runner import build_providers

    sleep = RecordingSleep()
    provs = build_providers(cfg, client, sleep)
    rid = asyncio.run(search_run(conn, cfg, specs, providers=provs,
                                 transport=httpx.MockTransport(site or Site({})), resolver=StaticResolver(),
                                 sleep=sleep, today=TODAY, retrieve=retrieve))
    return conn, rid


def status(conn, rid):
    r = repo.get_run(conn, rid)
    return r["status"], r["stop_reason"], {g["gate"]: g for g in r["gates"]}


# ---------------------------------------------------------------- accounting and quality metrics


def test_search_accounting_matches_requests_sent_and_reported_costs():
    apis = FakeAPIs({"brave": ["https://a.example/1", "https://shared.example/x"],
                     "tavily": ["https://shared.example/x", "https://b.example/2"],
                     "exa": ["https://c.example/3"]})
    page = lambda topic: httpx.Response(200, html=article(f"This page discusses {topic} at length for testing."))  # noqa: E731
    site = Site({"/1": page("alpha"), "/x": page("a shared subject"), "/2": page("bravo"), "/3": page("charlie")})
    conn, rid = run_search(apis, [spec()], site)
    m = metrics(conn, rid)
    s = m["search"]
    assert s["queries_planned"] == 3 and s["queries_completed"] == 3
    assert s["provider_requests_including_retries"] == len(apis.calls) == 3
    assert s["results_returned"] == 5 and s["unique_result_urls"] == 4
    p = m["providers"]
    assert p["brave"]["results"]["exclusive_urls"] == 1 and p["tavily"]["results"]["exclusive_urls"] == 1
    assert p["brave"]["results"]["unique_on_target_urls"] == 2
    assert p["exa"]["cost"]["provider_reported_usd"] == 0.007 and p["exa"]["cost"]["unit"] == "usd"
    assert p["tavily"]["cost"]["units"] == 1 and p["tavily"]["cost"]["estimated_list_usd"] == 0.008
    assert p["brave"]["cost"]["estimated_list_usd"] == 0.005
    assert p["brave"]["downstream"]["usable_unique_documents"] == 2
    assert p["brave"]["cost"]["estimated_usd_per_usable_document"] == 0.0025
    assert status(conn, rid)[0] == "completed"
    led = ledger.summary(conn, make_cfg().providers)
    assert led["brave"]["units_used"] == 1 and led["exa"]["provider_reported_usd"] == 0.007


def test_diminishing_returns_are_measured_per_query():
    apis = FakeAPIs({"tavily": ["https://a.example/1", "https://a.example/2"]})
    conn, rid = run_search(apis, [spec(), spec(purpose="repeat")], retrieve=False, enabled=["tavily"])
    assert metrics(conn, rid)["search"]["new_urls_by_query"] == [2, 0]


# ---------------------------------------------------------------- budgets, guard, provider failures


def test_search_budget_and_cost_ceiling_are_enforced_before_sending():
    apis = FakeAPIs({"exa": ["https://a.example/1"]})
    conn, rid = run_search(apis, [spec(purpose=f"q{i}") for i in range(3)], cfg=make_cfg(max_search_requests=2),
                           retrieve=False, enabled=["exa"])
    assert apis.count("exa") == 2
    st, stop, gates = status(conn, rid)
    assert (st, stop) == ("partial", "BUDGET_SEARCHES") and not gates["all_queries_processed"]["passed"]

    apis = FakeAPIs({"exa": ["https://a.example/1"]})
    conn, rid = run_search(apis, [spec(purpose=f"q{i}") for i in range(3)], cfg=make_cfg(max_cost_usd=0.01),
                           retrieve=False, enabled=["exa"])
    assert apis.count("exa") == 1 and status(conn, rid)[1] == "BUDGET_COST"


def test_free_tier_guard_blocks_requests_that_would_exceed_monthly_cap():
    conn = connect(":memory:")
    ledger.record(conn, "brave", "search", "request", 900, 4.5, None, None)  # cap reached by earlier usage
    apis = FakeAPIs({"brave": ["https://a.example/1"], "tavily": ["https://a.example/1"]})
    conn, rid = run_search(apis, [spec(), spec(purpose="again")], conn=conn, retrieve=False,
                           enabled=["brave", "tavily"])
    assert apis.count("brave") == 0 and apis.count("tavily") == 2
    m = metrics(conn, rid)
    assert m["providers"]["brave"]["requests"]["skipped_by_code"] == {"SEARCH_FREE_TIER_GUARD": 2}
    assert status(conn, rid)[0] == "partial"


def test_quota_exhaustion_disables_provider_for_the_run():
    apis = FakeAPIs({"exa": ["https://a.example/1"]},
                    overrides={("api.tavily.com", "/search"): lambda r: httpx.Response(432, text="plan limit")})
    conn, rid = run_search(apis, [spec(), spec(purpose="b"), spec(purpose="c")], retrieve=False,
                           enabled=["tavily", "exa"])
    assert apis.count("tavily") == 1
    p = metrics(conn, rid)["providers"]["tavily"]["requests"]
    assert p["failed_by_code"] == {"SEARCH_QUOTA_EXHAUSTED": 1} and p["skipped_by_code"] == {"SEARCH_PROVIDER_DISABLED": 2}
    assert ledger.summary(conn, make_cfg().providers)["tavily"]["units_used"] == 0  # failed calls not billed


@pytest.mark.parametrize("responses,expected", [
    ([httpx.Response(429), httpx.Response(200, json={"web": {"results": []}})], ("completed", None, 2)),
    ([httpx.Response(401)] * 3, ("failed", "SEARCH_AUTH_ERROR", 1)),
    ([httpx.Response(500)] * 3, ("failed", "SEARCH_PROVIDER_ERROR", 3)),
    ([httpx.Response(200, text="not json")], ("failed", "SEARCH_BAD_RESPONSE", 1)),
    ([httpx.Response(422, json={"error": "bad"})], ("failed", "SEARCH_BAD_REQUEST", 1)),
])
def test_provider_error_classification(responses, expected):
    apis = FakeAPIs(overrides={("api.search.brave.com", "/res/v1/web/search"): iter(responses)})
    conn, rid = run_search(apis, [spec()], retrieve=False, enabled=["brave"])
    row = conn.execute("SELECT status, failure_code, attempts FROM search_queries WHERE run_id=?", (rid,)).fetchone()
    assert tuple(row) == expected


def test_unconfigured_providers_fail_the_run_without_requests():
    apis = FakeAPIs()
    conn, rid = run_search(apis, [spec()], keys={})
    assert apis.calls == []
    st, _, gates = status(conn, rid)
    assert st == "failed" and gates["search_succeeded"]["code"] == "SEARCH_NOT_CONFIGURED"


def test_off_target_results_are_counted_not_used():
    apis = FakeAPIs({"tavily": ["https://www.rfc-editor.org/rfc/rfc9309.html", "https://spam.example/rfc"]})
    conn, rid = run_search(apis, [spec(sites=["rfc-editor.org"])], retrieve=False, enabled=["tavily"])
    m = metrics(conn, rid)
    assert m["search"]["results_off_target"] == 1 and m["leads"]["total"] == 1


# ---------------------------------------------------------------- storage policy from provider terms


def test_transient_providers_leave_no_result_text_and_unrequested_urls_are_withheld(tmp_path):
    db = tmp_path / "e.db"
    apis = FakeAPIs({"brave": ["https://fetched.example/page", "https://www.instagram.com/someprofile/"],
                     "exa": ["https://www.instagram.com/someprofile/"],
                     "tavily": ["https://www.instagram.com/tavilyprofile/"]})
    site = Site({"/page": httpx.Response(200, html=article("Fetched page text."))})
    conn, rid = run_search(apis, [spec()], site, conn=connect(db))
    rows = conn.execute("SELECT provider, url, title, snippet, withheld FROM search_results WHERE run_id=?"
                        " ORDER BY id", (rid,)).fetchall()
    by = [(r["provider"], r["url"], r["title"], r["snippet"], r["withheld"]) for r in rows]
    assert ("brave", "https://fetched.example/page", None, None, 0) in by  # we requested it: our own access record
    assert ("brave", None, None, None, 1) in by and ("exa", None, None, None, 1) in by  # never requested
    assert ("tavily", "https://www.instagram.com/tavilyprofile/", "T0", "TAVILY-SNIPPET-0", 0) in by
    leads = {r["provider"]: r for r in conn.execute("SELECT * FROM leads WHERE run_id=?", (rid,))}
    assert leads["tavily"]["snippet"] == "TAVILY-SNIPPET-0" and leads["tavily"]["snippet_expires_at"]
    assert all(r["url"].startswith("withheld:") or "fetched.example" in r["url"]
               for r in conn.execute("SELECT url FROM leads WHERE run_id=? AND storage_policy='transient'", (rid,)))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw = db.read_bytes()
    assert b"BRAVE-SNIPPET" not in raw and b"BRAVE-TITLE" not in raw and b"EXA-TITLE" not in raw
    assert b"someprofile" not in raw  # transient-only, unrequested URL fully withheld


def test_api_keys_are_never_written_to_the_database(tmp_path):
    db = tmp_path / "k.db"
    conn, rid = run_search(FakeAPIs({"tavily": ["https://a.example/1"]}), [spec()], conn=connect(db), retrieve=False)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw = db.read_bytes()
    for key in KEYS.values():
        assert key.encode() not in raw
    assert repo.get_run(conn, rid)["config"]["providers"]["tavily_api_key"] is True


# ---------------------------------------------------------------- provider extract fallback (decision D3.3)


def _fallback_run(route_response, robots="User-agent: *\nAllow: /\n", enabled=True):
    apis = FakeAPIs({"tavily": ["https://slow.example/page"]})
    site = Site({"/page": route_response}, robots=robots)
    cfg = make_cfg()
    cfg.providers.fallback_enabled = enabled  # these tests exercise the fallback's own rules
    return apis, *run_search(apis, [spec()], site, cfg=cfg, enabled=["tavily"])


def test_fallback_is_disabled_by_default_for_real_research():
    assert make_cfg().providers.fallback_enabled is False

    def timeout(request):
        raise httpx.ReadTimeout("t", request=request)

    apis, conn, rid = _fallback_run(timeout, enabled=False)
    assert apis.count("tavily", "/extract") == 0
    assert not conn.execute("SELECT 1 FROM documents WHERE evidence_kind='provider_extract'").fetchone()


def test_fallback_used_after_technical_failure():
    def timeout(request):
        raise httpx.ReadTimeout("t", request=request)

    apis, conn, rid = _fallback_run(timeout)
    assert apis.count("tavily", "/extract") == 1
    doc = conn.execute("SELECT evidence_kind, usable, flags_json, metadata_json FROM documents WHERE run_id=?",
                       (rid,)).fetchone()
    assert doc["evidence_kind"] == "provider_extract" and doc["usable"] == 1
    assert json.loads(doc["flags_json"]) == ["provider_extract_after:NETWORK_TIMEOUT"]
    assert metrics(conn, rid)["providers"]["tavily"]["downstream"]["provider_extract_documents"] == 1


def test_fallback_used_for_javascript_only_pages():
    apis, conn, rid = _fallback_run(httpx.Response(200, html="<html><body><div id='root'></div></body></html>"))
    assert apis.count("tavily", "/extract") == 1


@pytest.mark.parametrize("response,robots", [
    (httpx.Response(200, html=article()), "User-agent: *\nDisallow: /\n"),  # robots denial
    (httpx.Response(403), "User-agent: *\nAllow: /\n"),  # access control
    (httpx.Response(404), "User-agent: *\nAllow: /\n"),  # gone: provider copy may be stale
    (httpx.Response(429, headers={"retry-after": "9999"}), "User-agent: *\nAllow: /\n"),  # rate limit
    (httpx.Response(200, html=article()), httpx.Response(503)),  # robots unreachable = disallow
])
def test_fallback_never_used_to_bypass_denials(response, robots):
    apis, conn, rid = _fallback_run(response, robots)
    assert apis.count("tavily", "/extract") == 0
    assert not conn.execute("SELECT 1 FROM documents WHERE run_id=? AND evidence_kind='provider_extract'",
                            (rid,)).fetchone()


# ---------------------------------------------------------------- retention and deletion enforcement


def test_subject_deletion_removes_search_data_but_keeps_spend_ledger(tmp_path):
    db = tmp_path / "d.db"
    conn = connect(db)
    sid = repo.create_subject(conn, "Subject")
    apis = FakeAPIs({"tavily": ["https://a.example/unique-path-zq"]})
    cfg = make_cfg()
    cfg.providers.enabled = ["tavily"]
    cfg.providers.tavily_api_key = KEYS["tavily"]
    from engine.search.runner import build_providers

    client = httpx.AsyncClient(transport=httpx.MockTransport(apis))
    rid = asyncio.run(search_run(conn, cfg, [spec()], providers=build_providers(cfg, client), retrieve=False,
                                 transport=httpx.MockTransport(Site({})), resolver=StaticResolver(), today=TODAY,
                                 subject_id=sid))
    assert conn.execute("SELECT COUNT(*) FROM search_results").fetchone()[0] == 1
    assert repo.delete_subject(conn, sid)
    for table in ("runs", "search_queries", "search_results", "leads", "urls", "provider_usage", "trace_events"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert conn.execute("SELECT COUNT(*) FROM provider_ledger").fetchone()[0] == 1  # spend record survives
    raw = db.read_bytes() + (Path(str(db) + "-wal").read_bytes() if Path(str(db) + "-wal").exists() else b"")
    assert b"TAVILY-SNIPPET" not in raw and b"unique-path-zq" not in raw  # secure_delete + checkpoint


def test_retention_runs_automatically_at_the_start_of_each_run():
    conn = connect(":memory:")
    apis = FakeAPIs({"tavily": ["https://a.example/1"]})
    conn, rid = run_search(apis, [spec()], conn=conn, retrieve=False, enabled=["tavily"])
    conn.execute("UPDATE leads SET snippet_expires_at = '2000-01-01'")
    conn.execute("UPDATE search_results SET text_expires_at = '2000-01-01'")
    conn.execute("UPDATE runs SET expires_at = '2000-01-01' WHERE id = ?", (rid,))
    run_search(FakeAPIs(), [spec()], conn=conn, retrieve=False, enabled=["tavily"])
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE id=?", (rid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM search_results WHERE snippet IS NOT NULL").fetchone()[0] == 0


def test_provider_metrics_aggregate_across_runs():
    conn = connect(":memory:")
    for _ in range(2):
        run_search(FakeAPIs({"exa": ["https://a.example/1"]}), [spec()], conn=conn, retrieve=False, enabled=["exa"])
    agg = provider_metrics(conn)
    assert agg["exa"]["requests"]["completed"] == 2 and agg["exa"]["cost"]["provider_reported_usd"] == 0.014


# ---------------------------------------------------------------- regressions from the phase-2 live run


def test_tavily_requests_published_date_estimates():
    assert TavilyProvider("k", client=_client()).render(spec()).json["include_published_date"] is True


def test_extract_credits_accrue_even_when_provider_reports_zero():
    apis = FakeAPIs({"tavily": ["https://slow.example/page"]},
                    overrides={("api.tavily.com", "/extract"): lambda r: httpx.Response(200, json={
                        "results": [{"url": "https://slow.example/page", "raw_content": "Copy. " * 60}],
                        "usage": {"credits": 0}})})

    def timeout(request):
        raise httpx.ReadTimeout("t", request=request)

    cfg = make_cfg()
    cfg.providers.fallback_enabled = True
    conn, rid = run_search(apis, [spec()], Site({"/page": timeout}), cfg=cfg, enabled=["tavily"])
    units = conn.execute("SELECT units FROM provider_ledger WHERE operation='extract'").fetchone()[0]
    assert units == pytest.approx(0.2)  # 1 credit per 5 successes, accrued so the free-tier guard is not fooled
