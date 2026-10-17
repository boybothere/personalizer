"""Live smoke test against real public endpoints (free, keyless). Skipped unless ENGINE_LIVE=1.

Asserts behaviours, not exact content, so ordinary site changes don't break it; a provider outage
can still fail it, which is why it is kept out of the deterministic suite.
"""

import asyncio

import pytest
from conftest import TODAY

from engine.config import EngineConfig
from engine.observe import metrics
from engine.retrieval import Lead, Retriever
from engine.store import repo
from engine.store.db import connect

pytestmark = pytest.mark.live

URLS = {
    "html_article": "https://www.rfc-editor.org/rfc/rfc9309.html",
    "wikipedia": "https://en.wikipedia.org/wiki/Robots.txt",
    "wikipedia_dup": "https://en.wikipedia.org/wiki/Robots.txt?utm_source=demo",
    "rss_feed": "https://github.blog/feed/",
    "atom_octet_stream": "https://blog.python.org/feeds/posts/default",
    "robots_disallowed": "https://en.wikipedia.org/w/index.php?title=Robots.txt&action=history",
    "too_short": "https://example.com/",
    "pdf": "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
    "not_found": "https://httpbin.org/status/404",
    "discovery_only": "https://www.instagram.com/instagram/",
    "connector_only": "https://x.com/jack/status/20",
    "private": "http://127.0.0.1:8080/",
    "excluded": "https://www.spokeo.com/",
}


def test_live_retrieval_demonstrates_each_outcome():
    cfg = EngineConfig.from_env()
    assert cfg.contact, "set ENGINE_CONTACT or PERSONALIZER_CONTACT (Wikimedia requires contact details)"
    conn = connect(":memory:")
    rid = asyncio.run(Retriever(conn, cfg, today=TODAY).run([Lead(u) for u in URLS.values()], label="live smoke"))
    final = {row["canonical_url"]: row["code"] for row in conn.execute(
        """SELECT u.canonical_url, COALESCE(u.reject_code,
                  (SELECT COALESCE(failure_code, 'OK') FROM fetch_attempts WHERE url_id=u.id ORDER BY id DESC LIMIT 1)) code
           FROM urls u WHERE u.run_id=?""", (rid,))}
    by_name = {name: next((c for k, c in final.items() if k.rstrip("/") in url.rstrip("/") or url.split("?")[0] in k), None)
               for name, url in URLS.items()}
    assert by_name["discovery_only"] == "URL_PLATFORM_DISCOVERY_ONLY"
    assert by_name["connector_only"] == "URL_PLATFORM_CONNECTOR_ONLY"
    assert by_name["private"] == "URL_PRIVATE_ADDRESS"
    assert by_name["excluded"] == "URL_EXCLUDED_DOMAIN"
    assert by_name["robots_disallowed"] == "ROBOTS_DISALLOWED"
    assert by_name["pdf"] == "UNSUPPORTED_PDF"
    assert by_name["not_found"] == "HTTP_NOT_FOUND"
    m = metrics(conn, rid)
    assert m["leads"]["duplicate_leads"] == 1
    assert m["extraction"]["usable"] >= 3 and m["extraction"]["by_evidence_kind"].get("feed_entry", 0) >= 1
    assert m["fetch"]["attempts"] >= 6
    assert repo.get_run(conn, rid)["status"] == "completed"
    # phase 4: every real request went to a pinned, public address (TLS verified on the hostname)
    from engine.fetch.netpolicy import is_public_ip

    ips = [r[0] for r in conn.execute("SELECT connected_ip FROM fetch_attempts WHERE run_id=? AND outcome!='skipped'",
                                      (rid,))]
    assert ips and all(ip and is_public_ip(ip) for ip in ips)
