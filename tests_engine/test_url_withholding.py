"""Corrective pass 2: undated place/travel URL paths are fetched but never stored in plain text.

Synthetic data, offline fake web, temporary SQLite files (main, WAL and SHM inspected)."""

import httpx
from conftest import Site, article, run
from test_search import FakeAPIs, run_search
from world import World, page, run_world

from engine import privacy
from engine.search.spec import SearchSpec
from engine.store.db import connect
from engine.urls import check

TRAVEL = "https://profiles.example/priya-raman-goa-trip"
PLACE = "https://events.example/bengaluru-summit-recap"
SLUG_WORDS = ["goa-trip", "bengaluru-summit"]
IN_SCOPE = "Priya Raman, VP of Infrastructure at Nimbus Compute, spoke about chips."


def scan(directory, needles):
    hits = []
    for f in sorted(directory.iterdir()):
        raw = f.read_bytes()
        hits += [(f.name, n) for n in needles if n.encode() in raw]
    return hits


def test_place_and_travel_paths_keep_the_host_only():
    for url in (TRAVEL, PLACE):
        stored = privacy.safe_url(url)
        assert stored.startswith("https://" + url.split("/")[2] + "/withheld-path:")
        assert not any(w in stored for w in SLUG_WORDS)
        assert check(url, None)[1].allowed  # still fetchable
    assert privacy.safe_url("https://news.example/2023/03/goa-trip-report") == \
        "https://news.example/2023/03/goa-trip-report"  # a dated past trip is allowed (D4.2)
    assert privacy.safe_url("https://profiles.example/priya-raman") == "https://profiles.example/priya-raman"
    assert privacy.safe_url(privacy.safe_url(TRAVEL)) == privacy.safe_url(TRAVEL)  # idempotent
    assert privacy.safe_url("https://x.example/priya-raman-hospital-stay").startswith("withheld:out_of_scope:")


def test_research_run_fetches_but_never_stores_place_paths(tmp_path):
    w = World(routes=[(lambda q, inc: True, ["https://nimbuscompute.example/team/priya-raman", TRAVEL,
                                            "https://redirects.example/r"])],
              pages={TRAVEL: page(" ".join([IN_SCOPE] + ["Priya Raman is a member of the Open Compute Project."] * 4)),
                     "https://redirects.example/r": httpx.Response(302, headers={"location": PLACE}),
                     PLACE: page(" ".join([IN_SCOPE] + ["Priya Raman is a mentor at Women Who Code."] * 4))},
              tavily_override=lambda req, n: httpx.Response(200, json={"results": [
                  {"url": TRAVEL, "title": "T", "content": "x"},
                  {"url": "https://redirects.example/r", "title": "T", "content": "x"},
                  {"url": "https://nimbuscompute.example/team/priya-raman", "title": "T", "content": "x"}],
                  "usage": {"credits": 1}}) if b"/search" in req.url.raw_path else None)
    conn = connect(tmp_path / "w.db")
    _, rid = run_world(w, conn=conn, organization_domain="nimbuscompute.example", known_urls=[TRAVEL])
    assert TRAVEL in w.page_requests and PLACE in w.page_requests  # fetched, including through the redirect
    hosts = {r[0] for r in conn.execute("SELECT source_url FROM documents WHERE run_id=? AND usable=1", (rid,))}
    assert any(h.startswith("https://profiles.example/withheld-path:") for h in hosts)
    assert any(h.startswith("https://events.example/withheld-path:") for h in hosts)
    anchors = {r[0] for r in conn.execute("SELECT i.status FROM identity_decisions i JOIN documents d ON"
                                          " d.id=i.target_ref WHERE i.run_id=? AND i.superseded_by IS NULL AND"
                                          " d.source_url LIKE 'https://profiles.example/withheld-path:%'", (rid,))}
    assert anchors == {"confirmed"}  # the user-supplied known URL still anchors identity in its stored form
    ev = {r[0] for r in conn.execute("SELECT source_url FROM claim_evidence WHERE run_id=?", (rid,))}
    assert ev and not any(w in u for u in ev for w in SLUG_WORDS)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert scan(tmp_path, SLUG_WORDS) == []  # leads, search results, urls, attempts, documents, traces, run input
    conn.close()


def test_retrieve_and_search_entry_points_store_no_place_paths(tmp_path):
    site = Site({"/priya-raman-goa-trip": httpx.Response(200, html=article(IN_SCOPE))})
    conn, rid = run(site, [TRAVEL], conn=connect(tmp_path / "r.db"))
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE run_id=? AND usable=1", (rid,)).fetchone()[0] == 1
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    def tavily(request):
        return httpx.Response(200, json={"results": [{"url": PLACE, "title": "Summit recap", "content": "x"}],
                                         "usage": {"credits": 1}})

    conn, _ = run_search(FakeAPIs(overrides={("api.tavily.com", "/search"): tavily}),
                         [SearchSpec(purpose="test", phrases=["Priya Raman"])],
                         Site({"/bengaluru-summit-recap": httpx.Response(200, html=article(IN_SCOPE))}),
                         conn=connect(tmp_path / "s.db"), enabled=("tavily",))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    assert scan(tmp_path, SLUG_WORDS) == []
