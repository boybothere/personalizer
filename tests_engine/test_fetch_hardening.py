"""Phase 4: fetch hardening (acceptance criteria F1-F11 in docs/engine/PHASE4_PLAN.md). Offline only."""

import asyncio
import gzip
import hashlib
import ipaddress
import shutil

import httpx
import pytest
from conftest import ROOT, TODAY, RecordingSleep, Site, article, make_cfg, orig_host, run

from engine.budget import RunBudget
from engine.config import Platforms
from engine.failures import Code
from engine.fetch.client import Fetcher, StaticResolver
from engine.fetch.netpolicy import choose_ip, is_noncanonical_numeric, is_public_ip, legacy_ipv4
from engine.fetch.pinned import PinnedTransport, PinningError
from engine.observe import metrics
from engine.retrieval import Lead, Retriever
from engine.store.db import SchemaOutdated, connect
from engine.store.migrations import MIGRATIONS, migrate
from engine.urls import check

URL = "https://site.example/page"


def fetch(site, url=URL, cfg=None, resolver=None):
    cfg = cfg or make_cfg()
    f = Fetcher(cfg, RunBudget(cfg.budget), resolver=resolver or StaticResolver(),
                transport=httpx.MockTransport(site), sleep=RecordingSleep())

    async def go():
        try:
            return await f.fetch(url)
        finally:
            await f.aclose()

    return asyncio.run(go()), f


# ---------------------------------------------------------------- F8 address policy


@pytest.mark.parametrize("addr,public", [
    ("93.184.216.34", True), ("2606:2800:220:1::", True),
    ("127.0.0.1", False), ("10.1.2.3", False), ("172.16.0.1", False), ("192.168.1.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("240.0.0.1", False),
    ("::1", False), ("::", False), ("fe80::1", False), ("fc00::1", False), ("ff02::1", False),
    ("::ffff:127.0.0.1", False), ("::ffff:10.0.0.1", False),
    ("64:ff9b::7f00:1", False),  # NAT64 -> 127.0.0.1: Python alone says is_global=True
    ("64:ff9b::a00:1", False),  # NAT64 -> 10.0.0.1
    ("64:ff9b::5db8:d822", False),  # NAT64 to a public address is refused too: Python marks ::/8 reserved
    ("2002:7f00:1::", False),  # 6to4 -> 127.0.0.1
])
def test_is_public_ip(addr, public):
    assert is_public_ip(addr) is public


def test_python_alone_misses_nat64():
    assert ipaddress.ip_address("64:ff9b::7f00:1").is_global  # the gap this phase closes
    assert not is_public_ip("64:ff9b::7f00:1")


@pytest.mark.parametrize("host,ip", [
    ("2130706433", "127.0.0.1"), ("0x7f000001", "127.0.0.1"), ("0177.0.0.1", "127.0.0.1"),
    ("127.1", "127.0.0.1"), ("0x7f.1", "127.0.0.1"), ("10.1", "10.0.0.1"), ("127.0.0.1", "127.0.0.1"),
    ("example.com", None), ("1.2.3.4.example", None), ("256.1.1.1", None), ("0x", "0.0.0.0"),
])
def test_legacy_ipv4_parsing(host, ip):
    parsed = legacy_ipv4(host)
    assert (str(parsed) if parsed else None) == ip


def test_noncanonical_numeric_hosts_are_rejected_everywhere():
    for host in ("2130706433", "0x7f000001", "0177.0.0.1", "127.1"):
        assert is_noncanonical_numeric(host)
        _, decision = check(f"http://{host}/admin", Platforms())
        assert decision.code == Code.URL_NONCANONICAL_HOST
    assert not is_noncanonical_numeric("93.184.216.34") and not is_noncanonical_numeric("example.com")


def test_choose_ip_is_deterministic_and_prefers_ipv4():
    assert choose_ip(["2606:2800:220:1::", "93.184.216.35", "93.184.216.34"]) == "93.184.216.34"


@pytest.mark.parametrize("answers", [
    ["::ffff:127.0.0.1"], ["64:ff9b::7f00:1"], ["2002:7f00:1::"], ["100.64.0.1"], ["0.0.0.0"],
    ["fe80::1"], ["169.254.169.254"], ["93.184.216.34", "10.0.0.5"],  # one bad answer refuses the host
])
def test_resolved_private_addresses_are_never_requested(answers):
    site = Site({"/page": httpx.Response(200, html=article())})
    res, f = fetch(site, resolver=StaticResolver({"site.example": answers}))
    assert res.failure_code == Code.URL_PRIVATE_ADDRESS.value and site.requests == [] and f.transport.connections == []


# ---------------------------------------------------------------- F8 DNS rebinding and pinning


class RebindingResolver:
    """Public answer first, private afterwards: the classic rebinding pattern."""

    def __init__(self, first="93.184.216.34", later="127.0.0.1"):
        self.first, self.later, self.calls = first, later, []

    async def resolve(self, host):
        self.calls.append(host)
        return [self.first] if len(self.calls) == 1 else [self.later]


def test_rebinding_cannot_reach_a_private_address():
    site = Site({"/page": httpx.Response(200, html=article())})
    resolver = RebindingResolver()
    res, f = fetch(site, resolver=resolver)
    # one resolution for the hop; robots.txt and the page both go to that checked address; the later
    # (private) answer is never asked for, let alone used
    assert resolver.calls == ["site.example"] and res.ok
    assert f.transport.connections == [("site.example", "93.184.216.34")] * 2
    assert all(r.url.host == "93.184.216.34" for r in site.requests)


def test_rebinding_on_a_redirect_hop_is_refused():
    resolver = RebindingResolver()  # site.example -> public; next lookup (other.example) -> 127.0.0.1
    site = Site({"/page": httpx.Response(302, headers={"location": "https://other.example/x"})})
    res, f = fetch(site, resolver=resolver)
    assert res.failure_code == Code.URL_PRIVATE_ADDRESS.value
    assert all(ip == "93.184.216.34" for _, ip in f.transport.connections)


def test_connection_goes_to_the_checked_ip_with_original_host_and_sni():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, html=article())

    res, f = fetch(handler, resolver=StaticResolver({"site.example": ["93.184.216.40", "2606:2800:220:1::"]}))
    assert res.ok
    req = seen[-1]
    assert req.url.host == "93.184.216.40" and req.headers["host"] == "site.example"
    assert req.extensions["sni_hostname"] == "site.example" and req.extensions["pinned_ip"] == "93.184.216.40"
    assert res.attempts[-1].connected_ip == "93.184.216.40"


def test_resolver_called_once_per_hop():
    resolver = RebindingResolver(later="93.184.216.34")  # stable answers
    site = Site({"/page": httpx.Response(301, headers={"location": "https://other.example/x"}),
                 "/x": httpx.Response(200, html=article())})
    res, _ = fetch(site, resolver=resolver)
    assert res.ok
    # exactly one resolution per hop; robots.txt reuses the hop's pinned address
    assert resolver.calls == ["site.example", "other.example"]


def test_pinned_transport_fails_closed():
    recorder = httpx.MockTransport(lambda r: httpx.Response(200))
    t = PinnedTransport(inner=recorder)

    async def go(ext):
        return await t.handle_async_request(httpx.Request("GET", "https://site.example/", extensions=ext))

    with pytest.raises(PinningError, match="no pinned address"):
        asyncio.run(go({}))
    with pytest.raises(PinningError, match="not public"):
        asyncio.run(go({"pinned_ip": "127.0.0.1"}))
    assert asyncio.run(go({"pinned_ip": "93.184.216.34"})).status_code == 200


class _FakeStream:
    def __init__(self, peer):
        self.peer = peer

    def get_extra_info(self, name):
        return (self.peer, 443) if name == "server_addr" else None


def test_socket_peer_mismatch_is_refused_without_retry():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, html=article(), extensions={"network_stream": _FakeStream("10.9.9.9")})

    res, _ = fetch(handler)
    assert res.failure_code == Code.CONNECTION_POLICY_REFUSED.value
    assert [a.outcome for a in res.attempts] == ["failure"]  # policy refusal: not retried


def test_environment_proxies_are_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    cfg = make_cfg()
    f = Fetcher(cfg, RunBudget(cfg.budget))
    assert f.client.trust_env is False and isinstance(f.transport, PinnedTransport)
    asyncio.run(f.aclose())


# ---------------------------------------------------------------- F1/F2/F3 redirects and timeouts


def test_protocol_relative_and_cross_host_redirects_are_rechecked():
    site = Site({"/page": httpx.Response(302, headers={"location": "//other.example/x"}),
                 "/x": httpx.Response(200, html=article())})
    res, f = fetch(site)
    assert res.ok and res.final_url == "https://other.example/x"
    assert ("other.example", "93.184.216.34") in f.transport.connections


@pytest.mark.parametrize("target,code", [
    ("http://2130706433/x", Code.REDIRECT_BLOCKED),  # non-canonical numeric host via redirect
    ("http://[::ffff:127.0.0.1]/x", Code.REDIRECT_BLOCKED),
    ("http://[64:ff9b::7f00:1]/x", Code.REDIRECT_BLOCKED),
])
def test_redirects_to_disguised_internal_addresses_are_blocked(target, code):
    site = Site({"/page": httpx.Response(302, headers={"location": target})})
    res, f = fetch(site)
    assert res.failure_code == code.value
    assert all(ip == "93.184.216.34" for _, ip in f.transport.connections)


def test_connect_timeout_is_retried_then_reported():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectTimeout("connect timed out", request=request)

    res, _ = fetch(handler)
    assert res.failure_code == Code.NETWORK_TIMEOUT.value and [a.outcome for a in res.attempts] == [
        "retry", "retry", "failure"]


# ---------------------------------------------------------------- F5 oversized, F6 content types


def test_compressed_bomb_stops_at_the_decompressed_cap():
    bomb = gzip.compress(b"<html>" + b"A" * 5_000_000)
    cfg = make_cfg()
    cfg.fetch.max_page_bytes = 100_000

    async def stream():
        yield bomb

    site = Site({"/page": lambda r: httpx.Response(200, content=stream(), headers={
        "content-type": "text/html", "content-encoding": "gzip"})})
    res, f = fetch(site, cfg=cfg)
    assert res.failure_code == Code.RESPONSE_TOO_LARGE.value
    assert res.attempts[-1].bytes == cfg.fetch.max_page_bytes + 1  # never holds more than cap+1 decompressed bytes


def test_unknown_content_encoding_is_not_decoded():
    site = Site({"/page": httpx.Response(200, content=b"xx", headers={"content-type": "text/html",
                                                                       "content-encoding": "br"})})
    res, _ = fetch(site)
    assert res.failure_code == Code.UNSUPPORTED_CONTENT_TYPE.value and "content-encoding br" in res.detail


def test_gzip_page_is_decoded_normally():
    html = article().encode()
    site = Site({"/page": httpx.Response(200, content=gzip.compress(html),
                                         headers={"content-type": "text/html", "content-encoding": "gzip"})})
    res, _ = fetch(site)
    assert res.ok and res.body == html


@pytest.mark.parametrize("response,code,body_read", [
    (httpx.Response(200, content=b"%PDF-1.7 ...", headers={"content-type": "application/pdf"}),
     Code.UNSUPPORTED_PDF, False),
    (httpx.Response(200, content=b"%PDF-1.4 binary", headers={"content-type": "application/octet-stream"}),
     Code.UNSUPPORTED_PDF, True),
    (httpx.Response(200, content=b"%PDF-1.4 binary", headers={"content-type": "text/html"}),
     Code.UNSUPPORTED_PDF, True),
    (httpx.Response(200, content=b"\x89PNG....", headers={"content-type": "image/png"}),
     Code.UNSUPPORTED_CONTENT_TYPE, False),
])
def test_pdfs_and_binaries_are_reported_honestly(response, code, body_read):
    res, _ = fetch(Site({"/page": response}))
    assert res.failure_code == code.value and (res.attempts[-1].bytes > 0) == body_read


# ---------------------------------------------------------------- F7 robots via pinned path


def test_robots_redirect_is_resolved_and_pinned():
    site = Site({"/page": httpx.Response(200, html=article()),
                 "/real-robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /page\n")},
                robots=httpx.Response(301, headers={"location": "https://robots-host.example/real-robots.txt"}))
    res, f = fetch(site)
    assert res.failure_code == Code.ROBOTS_DISALLOWED.value
    assert ("robots-host.example", "93.184.216.34") in f.transport.connections


# ---------------------------------------------------------------- F10 access challenges


CF_200 = ('<html><head><title>Just a moment...</title></head><body><div>Checking your browser.</div>'
          '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script></body></html>')


@pytest.mark.parametrize("response", [
    httpx.Response(503, html=CF_200),
    httpx.Response(403, html='<html><body><div id="px-captcha"></div></body></html>'),
    httpx.Response(429, html='<html><body><script src="https://geo.captcha-delivery.com/c.js"></script></body></html>'),
    httpx.Response(503, headers={"cf-mitigated": "challenge"}, text=""),
])
def test_challenge_on_error_status_is_access_challenge_and_not_retried(response):
    res, _ = fetch(Site({"/page": response}))
    assert res.failure_code == Code.ACCESS_CHALLENGE.value and len(res.attempts) == 1


def _retrieve_with_fallback_spy(site):
    calls = []

    async def fallback(run_id, url, reason, budget):
        calls.append((url, reason))
        return None

    conn = connect(":memory:")
    r = Retriever(conn, make_cfg(), transport=httpx.MockTransport(site), resolver=StaticResolver(),
                  sleep=RecordingSleep(), today=TODAY, fallback=fallback)
    rid = asyncio.run(r.run([Lead(URL)]))
    return conn, rid, calls


def test_challenge_page_with_status_200_is_never_sent_to_the_fallback():
    conn, rid, calls = _retrieve_with_fallback_spy(Site({"/page": httpx.Response(200, html=CF_200)}))
    doc = conn.execute("SELECT usable, failure_code FROM documents WHERE run_id=?", (rid,)).fetchone()
    assert tuple(doc) == (0, Code.ACCESS_CHALLENGE.value) and calls == []
    assert metrics(conn, rid)["blocked_or_inaccessible"] == {"ACCESS_CHALLENGE": 1}


def test_challenge_status_is_never_sent_to_the_fallback():
    _, _, calls = _retrieve_with_fallback_spy(Site({"/page": httpx.Response(503, html=CF_200)}))
    assert calls == []


def test_js_only_page_still_uses_fallback_but_article_about_captchas_is_usable():
    _, _, calls = _retrieve_with_fallback_spy(Site({"/page": httpx.Response(
        200, html="<html><head><title>App</title></head><body><div id='root'></div></body></html>")}))
    assert calls and calls[0][1] == Code.JS_REQUIRED  # technical failure: fallback allowed (control case)
    long_article = article("This article explains how captcha systems and security checks work on websites.", 30)
    conn, rid, calls = _retrieve_with_fallback_spy(Site({"/page": httpx.Response(200, html=long_article)}))
    assert calls == [] and metrics(conn, rid)["extraction"]["usable"] == 1


# ---------------------------------------------------------------- F11 text and metadata yield; audit


def test_metrics_show_usable_text_and_metadata_not_just_http_success():
    head = ('<link rel="canonical" href="https://news.example/a"><meta property="og:site_name" content="News">'
            '<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-05-01",'
            '"author":{"name":"A Writer"}}</script>')
    site = Site({"/a": httpx.Response(200, html=article(repeat=12, head=head).replace("<html>", '<html lang="en">')),
                 "/b": httpx.Response(200, html="<html><body><div id='root'></div></body></html>"),
                 "/c": httpx.Response(200, html=CF_200)})
    conn, rid = run(site, [f"https://news.example/{p}" for p in "abc"])
    m = metrics(conn, rid)
    assert m["fetch"]["http_2xx"] == 3 and m["extraction"]["usable"] == 1
    assert m["extraction"]["usable_rate_of_retrieved_pages"] == round(1 / 3, 3)
    assert m["extraction"]["metadata_coverage_of_usable"] == {"title": 1, "author": 1, "language": 1,
                                                               "canonical_link": 1, "published_at": 1, "site_name": 1}
    ips = {r[0] for r in conn.execute("SELECT connected_ip FROM fetch_attempts WHERE run_id=? AND outcome!='skipped'",
                                      (rid,))}
    assert ips == {"93.184.216.34"}  # every request records the address it was pinned to


# ---------------------------------------------------------------- migration 0004 on a copy (never the original)


LOCAL_DB = ROOT / "data" / "engine.db"


@pytest.mark.skipif(not LOCAL_DB.exists(), reason="local Phase 1/2 database not present")
def test_migration_0004_on_a_copy_preserves_existing_records(tmp_path):
    original_hash = hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest()
    copy = tmp_path / "copy.db"
    shutil.copy2(LOCAL_DB, copy)
    with pytest.raises(SchemaOutdated):
        connect(copy)
    conn = connect(copy, upgrade=False)
    runs = [r[0] for r in conn.execute("SELECT id FROM runs")]
    # sections introduced by later migrations report "unavailable" with a schema-dependent reason
    strip = lambda m: {k: v for k, v in m.items() if k not in ("planner", "identity")}  # noqa: E731
    before = {r: strip(metrics(conn, r)) for r in runs}
    steps = migrate(conn)
    assert "up 4 fetch_connected_ip" in steps
    assert "connected_ip" in [c[1] for c in conn.execute("PRAGMA table_info(fetch_attempts)")]
    after = {r: strip(metrics(conn, r)) for r in runs}
    for r in runs:  # new metric keys appear; every pre-existing value is identical
        for section, values in before[r].items():
            for key, value in values.items() if isinstance(values, dict) else []:
                assert after[r][section][key] == value, (r, section, key)
    migrate(conn, 2)
    conn.close()
    assert hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest() == original_hash


# ---------------------------------------------------------------- fetch-trace (in-memory)


def test_fetch_trace_reports_each_url_without_storing_anything():
    from engine.fetch_trace import render, trace

    site = Site({"/a": httpx.Response(301, headers={"location": "/b"}), "/b": httpx.Response(200, html=article()),
                 "/c": httpx.Response(200, html=CF_200)})
    before = hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest() if LOCAL_DB.exists() else None
    t = trace(["https://news.example/a", "https://news.example/c", "http://127.0.0.1/x"], make_cfg(),
              transport=httpx.MockTransport(site), resolver=StaticResolver(), sleep=RecordingSleep(), today=TODAY)
    a, c, local = t["urls"]
    assert [x["outcome"] for x in a["attempts"]] == ["redirect", "success"]
    assert a["attempts"][0]["connected_ip"] == "93.184.216.34" and a["documents"][0]["usable"]
    assert c["documents"][0]["failure_code"] == "ACCESS_CHALLENGE"
    assert local["policy_code"] == "URL_PRIVATE_ADDRESS" and "attempts" in local and local["attempts"] == []
    text = render(t)
    assert "via 93.184.216.34" in text and "ACCESS_CHALLENGE" in text
    if before:
        assert hashlib.sha256(LOCAL_DB.read_bytes()).hexdigest() == before
