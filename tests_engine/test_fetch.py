"""Fetch layer: retries/backoff, timeouts, redirects, robots, size and type limits, concurrency."""

import asyncio

import httpx
import pytest
from conftest import RecordingSleep, Site, article, make_cfg

from engine.budget import RunBudget
from engine.failures import Code
from engine.fetch.client import Fetcher, StaticResolver, route_for

URL = "https://site.example/page"


def fetch(site, url=URL, cfg=None, resolver=None, sleep=None):
    cfg = cfg or make_cfg()
    budget = RunBudget(cfg.budget)
    robots = []
    f = Fetcher(cfg, budget, resolver=resolver or StaticResolver(), transport=httpx.MockTransport(site),
                sleep=sleep or RecordingSleep(), on_robots=robots.append)

    async def go():
        try:
            return await f.fetch(url)
        finally:
            await f.aclose()

    return asyncio.run(go()), budget, robots


def test_success_records_one_attempt():
    res, budget, robots = fetch(Site({"/page": httpx.Response(200, html=article())}))
    assert res.ok and res.route == "html" and res.http_status == 200
    assert [a.outcome for a in res.attempts] == ["success"]
    assert budget.spent["fetches"] == 1 and budget.spent["robots"] == 1 and len(robots) == 1


def test_retries_503_then_succeeds_with_backoff():
    calls = iter([httpx.Response(503), httpx.Response(503), httpx.Response(200, html=article())])
    sleep = RecordingSleep()
    res, budget, _ = fetch(Site({"/page": lambda r: next(calls)}), sleep=sleep)
    assert res.ok
    assert [a.outcome for a in res.attempts] == ["retry", "retry", "success"]
    assert budget.spent["fetches"] == 3  # retries are charged
    assert len(sleep.calls) == 2 and sleep.calls[1] >= sleep.calls[0] * 0.5  # exponential-ish with jitter


def test_retry_after_is_honoured():
    calls = iter([httpx.Response(429, headers={"retry-after": "3"}), httpx.Response(200, html=article())])
    sleep = RecordingSleep()
    res, _, _ = fetch(Site({"/page": lambda r: next(calls)}), sleep=sleep)
    assert res.ok and sleep.calls[0] >= 3


def test_retry_after_beyond_cap_is_not_waited_for():
    sleep = RecordingSleep()
    res, _, _ = fetch(Site({"/page": httpx.Response(429, headers={"retry-after": "3600"})}), sleep=sleep)
    assert not res.ok and res.failure_code == Code.HTTP_RATE_LIMITED.value
    assert sleep.calls == [] and len(res.attempts) == 1 and "exceeds cap" in res.attempts[0].detail


def test_rate_limit_exhausts_retries():
    res, _, _ = fetch(Site({"/page": httpx.Response(429)}))
    assert res.failure_code == Code.HTTP_RATE_LIMITED.value
    assert [a.outcome for a in res.attempts] == ["retry", "retry", "failure"]


def test_timeout_is_retried_then_reported():
    def slow(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    res, _, _ = fetch(Site({"/page": slow}))
    assert res.failure_code == Code.NETWORK_TIMEOUT.value and len(res.attempts) == 3
    assert all(a.http_status is None for a in res.attempts)


def test_total_timeout_enforced_on_slow_body():
    async def stall(request):
        await asyncio.sleep(5)
        return httpx.Response(200, html=article())

    cfg = make_cfg()
    cfg.fetch.total_timeout = 0.05
    cfg.fetch.max_retries = 0
    res, _, _ = fetch(Site({"/page": stall}), cfg=cfg)
    assert res.failure_code == Code.NETWORK_TIMEOUT.value


def test_connection_error():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    cfg = make_cfg()
    cfg.fetch.max_retries = 1
    res, _, _ = fetch(Site({"/page": boom}), cfg=cfg)
    assert res.failure_code == Code.NETWORK_ERROR.value and len(res.attempts) == 2


def test_redirects_are_followed_and_recorded():
    site = Site({"/page": httpx.Response(301, headers={"location": "/moved"}),
                 "/moved": httpx.Response(302, headers={"location": "https://other.example/final"}),
                 "/final": httpx.Response(200, html=article())})
    res, budget, robots = fetch(site)
    assert res.ok and res.final_url == "https://other.example/final"
    assert [a.outcome for a in res.attempts] == ["redirect", "redirect", "success"]
    assert {r.origin for r in robots} == {"https://site.example", "https://other.example"}  # robots per host


@pytest.mark.parametrize("target,code", [
    ("http://internal.example/admin", Code.URL_PRIVATE_ADDRESS),  # resolves to a private IP
    ("https://www.instagram.com/p/x/", Code.REDIRECT_BLOCKED),
    ("ftp://site.example/file", Code.REDIRECT_BLOCKED),
])
def test_redirect_targets_are_rechecked(target, code):
    site = Site({"/page": httpx.Response(302, headers={"location": target})})
    res, _, _ = fetch(site, resolver=StaticResolver({"internal.example": ["10.0.0.7"]}))
    assert not res.ok and res.failure_code == code.value
    assert res.attempts[-1].outcome == "skipped"
    assert not any("instagram" in u or "internal" in u for u in site.content_requests())


def test_redirect_loop_and_limit():
    loop = Site({"/page": httpx.Response(302, headers={"location": "/b"}),
                 "/b": httpx.Response(302, headers={"location": "/page"})})
    assert fetch(loop)[0].failure_code == Code.REDIRECT_LOOP.value
    chain = Site({f"/r{i}": httpx.Response(302, headers={"location": f"/r{i + 1}"}) for i in range(10)})
    cfg = make_cfg()
    cfg.fetch.max_redirects = 3
    assert fetch(chain, url="https://site.example/r0", cfg=cfg)[0].failure_code == Code.TOO_MANY_REDIRECTS.value


def test_dns_failure_and_private_resolution_are_not_requested():
    site = Site({"/page": httpx.Response(200, html=article())})
    res, budget, _ = fetch(site, resolver=StaticResolver({"site.example": []}))
    assert res.failure_code == Code.URL_DNS_FAILURE.value and site.requests == []
    res, _, _ = fetch(site, resolver=StaticResolver({"site.example": ["192.168.1.10"]}))
    assert res.failure_code == Code.URL_PRIVATE_ADDRESS.value and site.requests == []
    assert budget.spent["fetches"] == 0


@pytest.mark.parametrize("robots,code,requested", [
    ("User-agent: *\nDisallow: /page\n", Code.ROBOTS_DISALLOWED, False),
    ("User-agent: PersonalizerResearchBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n", Code.ROBOTS_DISALLOWED, False),
    (httpx.Response(503), Code.ROBOTS_UNREACHABLE, False),  # RFC 9309: 5xx -> disallow all
    (httpx.Response(404), None, True),  # no robots.txt -> allowed
    (httpx.Response(403), None, True),  # RFC 9309: 4xx -> allowed
])
def test_robots_semantics(robots, code, requested):
    site = Site({"/page": httpx.Response(200, html=article())}, robots=robots)
    res, budget, _ = fetch(site)
    assert (res.failure_code == (code.value if code else None)) and res.ok == (code is None)
    assert bool(site.content_requests()) == requested
    if not requested:
        assert budget.spent["fetches"] == 0 and res.attempts[0].outcome == "skipped"


def test_robots_redirect_to_private_address_is_not_followed():
    site = Site({"/page": httpx.Response(200, html=article())},
                robots=httpx.Response(302, headers={"location": "http://internal.example/robots.txt"}))
    res, _, robots = fetch(site, resolver=StaticResolver({"internal.example": ["10.1.1.1"]}))
    assert res.failure_code == Code.ROBOTS_UNREACHABLE.value and "disallowed target" in robots[0].detail
    assert not any(r.url.host == "internal.example" for r in site.requests)


def test_robots_redirect_to_same_site_is_followed():
    site = Site({"/page": httpx.Response(200, html=article()),
                 "/real-robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /page\n")},
                robots=httpx.Response(301, headers={"location": "/real-robots.txt"}))
    res, _, _ = fetch(site)
    assert res.failure_code == Code.ROBOTS_DISALLOWED.value


def test_crawl_delay_is_respected_between_requests():
    site = Site({"/page": httpx.Response(200, html=article()), "/p2": httpx.Response(200, html=article())},
                robots="User-agent: *\nCrawl-delay: 4\n")
    cfg = make_cfg()
    sleep = RecordingSleep()
    budget = RunBudget(cfg.budget)
    f = Fetcher(cfg, budget, resolver=StaticResolver(), transport=httpx.MockTransport(site), sleep=sleep)

    async def go():
        await f.fetch(URL)
        await f.fetch("https://site.example/p2")
        await f.aclose()

    asyncio.run(go())
    assert sleep.calls and sleep.calls[0] > 3.5


@pytest.mark.parametrize("response,code", [
    (httpx.Response(403), Code.HTTP_ACCESS_CONTROLLED),
    (httpx.Response(451), Code.HTTP_ACCESS_CONTROLLED),
    (httpx.Response(404), Code.HTTP_NOT_FOUND),
    (httpx.Response(410), Code.HTTP_NOT_FOUND),
    (httpx.Response(418), Code.HTTP_CLIENT_ERROR),
    (httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}), Code.UNSUPPORTED_CONTENT_TYPE),
    (httpx.Response(200, json={"a": 1}), Code.UNSUPPORTED_CONTENT_TYPE),
    (httpx.Response(200, content=b"\x00\x01binary", headers={"content-type": "application/octet-stream"}),
     Code.UNSUPPORTED_CONTENT_TYPE),
])
def test_status_and_type_failures(response, code):
    res, _, _ = fetch(Site({"/page": response}))
    assert not res.ok and res.failure_code == code.value
    assert res.attempts[-1].outcome == "failure"


def test_size_limits_declared_and_streamed():
    cfg = make_cfg()
    cfg.fetch.max_page_bytes = 1000
    declared = httpx.Response(200, content=b"x" * 10, headers={"content-type": "text/html", "content-length": "999999"})
    assert fetch(Site({"/page": declared}), cfg=cfg)[0].failure_code == Code.RESPONSE_TOO_LARGE.value
    async def chunks():  # no Content-Length: the limit must be enforced while streaming
        for _ in range(10):
            yield b"<html>" + b"y" * 500

    res, budget, _ = fetch(Site({"/page": lambda r: httpx.Response(200, content=chunks(),
                                                                  headers={"content-type": "text/html"})}), cfg=cfg)
    assert res.failure_code == Code.RESPONSE_TOO_LARGE.value and budget.spent["bytes"] > 0


@pytest.mark.parametrize("ctype,head,route", [
    ("text/html; charset=utf-8", b"<html>", "html"),
    ("application/xhtml+xml", b"<html>", "html"),
    ("application/rss+xml", b"<rss>", "feed"),
    ("application/xml", b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>", "feed"),
    ("text/xml", b"<?xml version='1.0'?><sitemap/>", None),
    ("", b"<!DOCTYPE html><html>", "html"),
    ("text/plain", b"hello", "text"),
])
def test_content_type_routing(ctype, head, route):
    assert route_for(ctype, head) == route


def test_per_host_concurrency_limit():
    active = {"now": 0, "max": 0}

    async def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        return httpx.Response(200, html=article())

    cfg = make_cfg()
    cfg.fetch.per_host_concurrency = 2
    budget = RunBudget(cfg.budget)
    f = Fetcher(cfg, budget, resolver=StaticResolver(), transport=httpx.MockTransport(handler))

    async def go():
        await asyncio.gather(*(f.fetch(f"https://site.example/p{i}") for i in range(8)))
        await f.aclose()

    asyncio.run(go())
    assert active["max"] == 2
