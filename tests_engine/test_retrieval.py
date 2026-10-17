"""End-to-end retrieval runs: metric accuracy, quality gates, budgets, cancellation, audit trail,
and regressions for the prototype failures documented in the audit (C1, C2, C3, C8, C10)."""

import asyncio
import json

import httpx
from conftest import TODAY, RecordingSleep, Site, article, make_cfg, run

from engine.fetch.client import StaticResolver
from engine.observe import metrics
from engine.retrieval import SNIPPET_LIMITATIONS, Lead, Retriever
from engine.store import repo
from engine.store.db import connect

JS_SHELL = "<html><body><div id='root'></div><script src='a.js'></script></body></html>"


def status(conn, run_id):
    r = repo.get_run(conn, run_id)
    return r["status"], r["stop_reason"], {g["gate"]: g for g in r["gates"]}


def test_metrics_equal_what_the_server_actually_received():
    calls = iter([httpx.Response(503), httpx.Response(200, html=article())])
    site = Site({"/ok": httpx.Response(200, html=article()), "/flaky": lambda r: next(calls),
                 "/gone": httpx.Response(404), "/moved": httpx.Response(301, headers={"location": "/ok2"}),
                 "/ok2": httpx.Response(200, html=article("Different text about chess openings."))},
                robots="User-agent: *\nDisallow: /secret\n")
    urls = [f"https://a.example/{p}" for p in ("ok", "flaky", "gone", "moved", "secret", "ok")] + [
        "https://www.instagram.com/p/x/"]
    conn, rid = run(site, urls)
    m = metrics(conn, rid)
    assert m["fetch"]["attempts"] == len(site.content_requests()) == 6  # ok, flaky x2, gone, moved, ok2
    assert m["fetch"]["robots_requests"] == sum(r.url.path == "/robots.txt" for r in site.requests) == 1
    assert m["fetch"]["retries"] == 1 and m["fetch"]["redirect_hops"] == 1
    assert m["leads"]["total"] == 7 and m["leads"]["duplicate_leads"] == 1
    assert m["urls"]["unique_candidates"] == 6 and m["urls"]["rejected_by_code"] == {"URL_PLATFORM_DISCOVERY_ONLY": 1}
    assert m["fetch"]["urls_retrieved"] == 3
    assert m["fetch"]["urls_failed_by_code"] == {"HTTP_NOT_FOUND": 1, "ROBOTS_DISALLOWED": 1}
    assert m["blocked_or_inaccessible"] == {"URL_PLATFORM_DISCOVERY_ONLY": 1, "ROBOTS_DISALLOWED": 1}
    assert m["extraction"]["usable"] == 3
    assert status(conn, rid)[0] == "completed"


def test_http_success_is_not_extraction_success_and_zero_usable_fails_the_run():
    # regression C1/C10: the prototype reported a 'resolved' brief with zero pages fetched by itself
    site = Site({"/a": httpx.Response(200, html=JS_SHELL), "/b": httpx.Response(200, html="<html><body>Hi</body></html>")})
    conn, rid = run(site, ["https://a.example/a", "https://a.example/b"])
    m = metrics(conn, rid)
    assert m["fetch"]["http_2xx"] == 2 and m["fetch"]["urls_retrieved"] == 2
    assert m["extraction"]["usable"] == 0
    assert m["extraction"]["failed_by_code"] == {"JS_REQUIRED": 1, "TEXT_TOO_SHORT": 1}
    st, _, gates = status(conn, rid)
    assert st == "failed" and gates["usable_documents"]["code"] == "NO_USABLE_DOCUMENTS"


def test_search_snippets_stay_leads_with_limitations():
    leads = [Lead("https://www.instagram.com/p/abc/", kind="search_result", provider="brave",
                  query_text='site:instagram.com "Jane Roe"', rank=1, snippet="Jane Roe: loving the beaches of Goa"),
             Lead("https://a.example/gone", kind="search_result", provider="brave", snippet="old cached text")]
    conn, rid = run(Site({}), leads)
    m = metrics(conn, rid)
    assert m["extraction"]["documents"] == 0 and m["leads"]["snippet_only"] == 2
    assert m["fetch"]["attempts"] == 1  # only the fetchable URL was requested; snippets are not fetches
    row = conn.execute("SELECT snippet, snippet_limitations, provider, query_text FROM leads WHERE rank=1").fetchone()
    assert row["snippet"].startswith("Jane Roe") and json.loads(row["snippet_limitations"]) == SNIPPET_LIMITATIONS
    assert status(conn, rid)[0] == "failed"


def test_fetch_budget_stops_run_as_partial_and_records_unprocessed_urls():
    site = Site({f"/p{i}": httpx.Response(200, html=article(f"Topic number {i} is discussed here.")) for i in range(5)})
    cfg = make_cfg(max_fetch_attempts=2)
    cfg.fetch.global_concurrency = 1
    conn, rid = run(site, [f"https://a.example/p{i}" for i in range(5)], cfg=cfg)
    m = metrics(conn, rid)
    assert m["fetch"]["attempts"] == len(site.content_requests()) == 2
    st, stop, gates = status(conn, rid)
    assert st == "partial" and stop == "BUDGET_FETCHES"
    assert not gates["all_work_processed"]["passed"] and "3 accepted URLs not processed" in gates["all_work_processed"]["detail"]
    skipped = conn.execute("SELECT COUNT(*) FROM fetch_attempts WHERE run_id=? AND outcome='skipped'"
                           " AND failure_code='BUDGET_FETCHES'", (rid,)).fetchone()[0]
    assert skipped == 3


def test_retry_then_budget_counts_as_unprocessed():
    site = Site({"/p": httpx.Response(503)})
    cfg = make_cfg(max_fetch_attempts=1)
    conn, rid = run(site, ["https://a.example/p"], cfg=cfg)
    st, stop, gates = status(conn, rid)
    assert stop == "BUDGET_FETCHES" and not gates["all_work_processed"]["passed"]
    assert [r[0] for r in conn.execute("SELECT outcome FROM fetch_attempts WHERE run_id=? ORDER BY id", (rid,))] == [
        "retry", "skipped"]


def test_lead_and_byte_budgets():
    site = Site({f"/p{i}": httpx.Response(200, html=article()) for i in range(4)})
    conn, rid = run(site, [f"https://a.example/p{i}" for i in range(4)], cfg=make_cfg(max_leads=2))
    assert status(conn, rid)[1] == "BUDGET_LEADS" and metrics(conn, rid)["leads"]["total"] == 2
    dropped = conn.execute("SELECT data_json FROM trace_events WHERE run_id=? AND action='leads_dropped'", (rid,)).fetchone()
    assert json.loads(dropped[0]) == {"dropped": 2}

    cfg = make_cfg(max_total_bytes=600)
    cfg.fetch.global_concurrency = 1
    conn, rid = run(site, [f"https://a.example/p{i}" for i in range(4)], cfg=cfg)
    m = metrics(conn, rid)
    assert status(conn, rid)[1] == "BUDGET_BYTES" and m["fetch"]["bytes"] <= 600 + 2048


def _timed_site(fast_paths: set[str]):
    async def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path not in fast_paths:
            await asyncio.sleep(2)
        return httpx.Response(200, html=article(f"Page {request.url.path} talks about sailing."))
    return httpx.MockTransport(handler)


def test_time_budget_with_nothing_retrieved_fails_and_records_in_flight_requests():
    cfg = make_cfg(max_wall_seconds=0.3)
    conn, rid = run(_timed_site(set()), [f"https://a.example/p{i}" for i in range(3)], cfg=cfg)
    st, stop, gates = status(conn, rid)
    assert (st, stop) == ("failed", "BUDGET_TIME")  # zero usable documents is never 'partial success'
    rows = conn.execute("SELECT failure_code, detail FROM fetch_attempts WHERE run_id=?", (rid,)).fetchall()
    assert len(rows) == 3 and all(r["failure_code"] == "BUDGET_TIME" for r in rows)
    assert all("in flight" in r["detail"] for r in rows)
    assert metrics(conn, rid)["fetch"]["attempts"] == 3  # in-flight requests are still counted


def test_time_budget_after_some_success_is_partial():
    cfg = make_cfg(max_wall_seconds=0.5)
    conn, rid = run(_timed_site({"/p0"}), [f"https://a.example/p{i}" for i in range(3)], cfg=cfg)
    st, stop, gates = status(conn, rid)
    assert (st, stop) == ("partial", "BUDGET_TIME")
    assert gates["usable_documents"]["passed"] and not gates["all_work_processed"]["passed"]


def test_cancellation():
    async def slow(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        await asyncio.sleep(1)
        return httpx.Response(200, html=article())

    conn = connect(":memory:")
    r = Retriever(conn, make_cfg(), transport=httpx.MockTransport(slow), resolver=StaticResolver(),
                  sleep=RecordingSleep(), today=TODAY)

    async def go():
        task = asyncio.ensure_future(r.run([Lead(f"https://a.example/p{i}") for i in range(3)]))
        await asyncio.sleep(0.1)
        repo.request_cancel(conn, conn.execute("SELECT id FROM runs").fetchone()[0])
        return await task

    rid = asyncio.run(go())
    assert status(conn, rid)[:2] == ("cancelled", "CANCELLED")


def test_run_is_auditable_from_input_to_document():
    site = Site({"/a": httpx.Response(302, headers={"location": "/b"}), "/b": httpx.Response(200, html=article())})
    lead = Lead("https://a.example/a?utm_source=news", kind="search_result", provider="tavily",
                query_text='"Jane Roe" interview', rank=3, snippet="snippet text")
    conn, rid = run(site, [lead])
    chain = conn.execute("""
        SELECT l.query_text, l.provider, u.canonical_url, fa.outcome, fa.http_status, d.source_url, d.usable
        FROM documents d JOIN fetch_attempts fa ON fa.id = d.fetch_attempt_id JOIN urls u ON u.id = d.url_id
        JOIN leads l ON l.url_id = u.id WHERE d.run_id = ?""", (rid,)).fetchone()
    assert dict(chain) == {"query_text": '"Jane Roe" interview', "provider": "tavily",
                           "canonical_url": "https://a.example/a", "outcome": "success", "http_status": 200,
                           "source_url": "https://a.example/b", "usable": 1}
    run_row = repo.get_run(conn, rid)
    assert run_row["input"]["leads"][0]["snippet"] == "snippet text" and run_row["config"]["budget"]
    actions = [r[0] for r in conn.execute("SELECT action FROM trace_events WHERE run_id=? ORDER BY id", (rid,))]
    assert actions[0] == "started" and actions[-1] == "finished"
    assert {"url_accepted", "fetched", "usable"} <= set(actions)


def test_org_pages_do_not_crowd_out_other_sources():
    # regression C3: the prototype sorted by tier and truncated to 12, keeping 9 company pages
    routes = {f"/news/{i}": httpx.Response(200, html=article(f"Corporate announcement {i} about dividends.")) for i in range(15)}
    routes["/interview"] = httpx.Response(200, html=article("In an interview she said she loves birdwatching."))
    site = Site(routes)
    urls = [f"https://corp.example/news/{i}" for i in range(15)] + ["https://indie.example/interview"]
    conn, rid = run(site, urls)
    domains = {r[0] for r in conn.execute("SELECT u.domain FROM documents d JOIN urls u ON u.id=d.url_id "
                                          "WHERE d.run_id=? AND d.usable=1", (rid,))}
    assert "indie.example" in domains and metrics(conn, rid)["fetch"]["urls_retrieved"] == 16


def test_failed_requests_are_not_counted_as_completed():
    # regression C8: the prototype counted every search call in `finally`, including failures
    site = Site({"/a": httpx.Response(500), "/b": httpx.Response(200, html=article())})
    conn, rid = run(site, ["https://a.example/a", "https://a.example/b"])
    m = metrics(conn, rid)
    assert m["fetch"]["attempts"] == 4 and m["fetch"]["urls_retrieved"] == 1
    assert m["fetch"]["urls_failed_by_code"] == {"HTTP_SERVER_ERROR": 1}


def test_syndicated_copies_are_marked_near_duplicates():
    text = ("Jane Roe described her favourite hiking routes in the Nilgiris and explained why she avoids weekend "
            "crowds, preferring early weekday starts with a flask of filter coffee and a paper map.")
    site = Site({"/orig": httpx.Response(200, html=article(text, repeat=3)),
                 "/copy": httpx.Response(200, html=article(text, repeat=3))})
    conn, rid = run(site, ["https://orig.example/orig", "https://mirror.example/copy"])
    m = metrics(conn, rid)
    assert m["extraction"]["usable"] == 2 and m["extraction"]["near_duplicates"] == 1
    assert m["validation"]["documents_passed"] == 1


def test_feed_retrieval_produces_entry_records():
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel><title>B</title><item><title>P</title>'
           '<link>https://blog.example/p</link><pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate>'
           '<description>My favourite weekend ritual is a long swim followed by idli at the corner cafe.</description>'
           '</item></channel></rss>')
    site = Site({"/feed": httpx.Response(200, content=rss.encode(), headers={"content-type": "application/rss+xml"})})
    conn, rid = run(site, ["https://blog.example/feed"])
    doc = conn.execute("SELECT evidence_kind, source_url, published_at, published_source, usable FROM documents"
                       " WHERE run_id=?", (rid,)).fetchone()
    assert dict(doc) == {"evidence_kind": "feed_entry", "source_url": "https://blog.example/p",
                         "published_at": "2026-10-05", "published_source": "feed:published", "usable": 1}
    parsed = conn.execute("SELECT reason FROM trace_events WHERE run_id=? AND action='feed_parsed'", (rid,)).fetchone()
    assert parsed[0] == "1 of 1 entries kept"


def test_feed_entry_cap_is_recorded():
    items = "".join(f"<item><title>P{i}</title><link>https://blog.example/{i}</link><description>Entry number {i} "
                    f"describes a weekend hike.</description></item>" for i in range(5))
    rss = f'<?xml version="1.0"?><rss version="2.0"><channel><title>B</title>{items}</channel></rss>'
    site = Site({"/feed": httpx.Response(200, content=rss.encode(), headers={"content-type": "application/rss+xml"})})
    cfg = make_cfg()
    cfg.extract.max_feed_entries = 2
    conn, rid = run(site, ["https://blog.example/feed"], cfg=cfg)
    data = conn.execute("SELECT data_json FROM trace_events WHERE run_id=? AND action='feed_parsed'", (rid,)).fetchone()
    assert json.loads(data[0]) == {"kept": 2, "total": 5, "truncated": 3}


def test_no_leads_fails():
    conn, rid = run(Site({}), [])
    assert status(conn, rid)[0] == "failed" and status(conn, rid)[2]["has_leads"]["code"] == "NO_LEADS"
