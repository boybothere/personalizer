"""Async page retrieval with explicit accounting.

Every HTTP request for content is one `Attempt` (retries and redirect hops included); every decision
not to send a request (robots, budget, address policy) is a `skipped` attempt with a failure code.
The fetcher never raises for per-URL problems: it returns a FetchResult whose attempt log explains
exactly what happened.
"""

from __future__ import annotations

import asyncio
import ipaddress
import random
import re
import socket
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from ..budget import BudgetExceeded, RunBudget
from ..config import EngineConfig
from ..failures import Code
from ..urls import check
from . import challenge
from . import robots as robots_mod
from .netpolicy import choose_ip, is_noncanonical_numeric, is_public_ip
from .pinned import PinnedTransport, PinningError

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
ACCESS_CONTROLLED = {401, 402, 403, 407, 451}
REJECT_TYPES = ("application/pdf", "application/json", "application/zip", "application/octet-stream+zip",
                "image/", "video/", "audio/", "font/", "application/vnd.", "application/msword")


@dataclass
class Attempt:
    attempt_no: int
    request_url: str
    outcome: str  # success | redirect | retry | failure | skipped
    http_status: int | None = None
    content_type: str | None = None
    bytes: int = 0
    elapsed_ms: int | None = None
    failure_code: str | None = None
    detail: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    connected_ip: str | None = None  # the pinned, policy-checked address this request was sent to


@dataclass
class FetchResult:
    url: str
    final_url: str
    ok: bool
    attempts: list[Attempt]
    failure_code: str | None = None
    detail: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    route: str | None = None  # html | feed | text
    body: bytes | None = None


class Resolver(Protocol):
    async def resolve(self, host: str) -> list[str]: ...


class SystemResolver:
    async def resolve(self, host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted({info[4][0] for info in infos})


class StaticResolver:
    """Test/offline resolver: every host maps to `default` unless overridden."""

    def __init__(self, mapping: dict[str, list[str]] | None = None, default: str = "93.184.216.34"):
        self.mapping = mapping or {}
        self.default = default

    async def resolve(self, host: str) -> list[str]:
        if host in self.mapping:
            ips = self.mapping[host]
            if not ips:
                raise socket.gaierror("no such host")
            return ips
        return [self.default]


def route_for(content_type: str, head: bytes) -> str | None:
    ct = content_type.split(";")[0].strip().lower()
    sniff = head[:2048].lstrip().lower()
    if ct in {"text/html", "application/xhtml+xml"}:
        return "html"
    if ct in {"application/rss+xml", "application/atom+xml", "application/rdf+xml"}:
        return "feed"
    if ct in {"application/xml", "text/xml", "", "application/octet-stream", "binary/octet-stream"}:
        if sniff.startswith(b"<?xml") or b"<rss" in sniff or b"<feed" in sniff or b"<rdf:rdf" in sniff:
            return "feed" if (b"<rss" in sniff or b"<feed" in sniff or b"<rdf:rdf" in sniff) else None
        if sniff.startswith((b"<!doctype html", b"<html")):
            return "html"
        return None
    if ct == "text/plain":
        return "text"
    return None


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


class Fetcher:
    def __init__(self, cfg: EngineConfig, budget: RunBudget, *, resolver: Resolver | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 on_robots: Callable[[robots_mod.RobotsResult], None] | None = None,
                 rng: random.Random | None = None):
        self.cfg = cfg
        self.f = cfg.fetch
        self.budget = budget
        self.resolver = resolver or SystemResolver()
        self.sleep = sleep
        self.on_robots = on_robots
        self.rng = rng or random.Random()
        # every request goes through the pinning layer (tests wrap their mock transport the same way);
        # trust_env=False: environment proxies would re-resolve names and bypass the address policy
        self.transport = PinnedTransport(inner=transport) if transport is not None else PinnedTransport()
        self.client = httpx.AsyncClient(
            transport=self.transport, follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(self.f.read_timeout, connect=self.f.connect_timeout),
            headers={"User-Agent": cfg.user_agent,
                     "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/atom+xml,"
                               "application/xml;q=0.9,text/plain;q=0.8"},
        )
        self._global = asyncio.Semaphore(self.f.global_concurrency)
        self._hosts: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(self.f.per_host_concurrency))
        self._next_slot: dict[str, float] = {}
        self._robots: dict[str, asyncio.Task] = {}
        # live attempt logs per requested URL; survives cancellation so in-flight requests are still accounted for
        self.inflight: dict[str, list[Attempt]] = {}

    async def aclose(self) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------------ network policy

    async def _resolve(self, host: str) -> tuple[str | None, tuple[Code, str] | None]:
        """Resolve once, check every address, return the address to pin (or the reason to refuse)."""
        if is_noncanonical_numeric(host):
            return None, (Code.URL_NONCANONICAL_HOST, host)
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
            return (str(ip), None) if is_public_ip(ip) else (None, (Code.URL_PRIVATE_ADDRESS, str(ip)))
        except ValueError:
            pass
        try:
            ips = await self.resolver.resolve(host)
        except (OSError, UnicodeError) as exc:
            return None, (Code.URL_DNS_FAILURE, f"{host}: {type(exc).__name__}")
        bad = [ip for ip in ips if not is_public_ip(ip)]
        if not ips or bad:  # one non-public answer refuses the host; never "pick the good one"
            return None, (Code.URL_PRIVATE_ADDRESS, f"{host} -> {', '.join(bad or ['no address'])}")
        return choose_ip(ips), None

    async def _polite(self, host: str, crawl_delay: float | None) -> None:
        interval = max(self.f.min_host_interval, min(crawl_delay or 0.0, self.f.max_crawl_delay))
        now = time.monotonic()
        slot = max(now, self._next_slot.get(host, now))
        self._next_slot[host] = slot + interval
        if slot > now:
            await self.sleep(slot - now)

    # ------------------------------------------------------------------ robots

    async def robots_for(self, url: str) -> robots_mod.RobotsResult:
        origin = robots_mod.origin_of(url)
        if origin not in self._robots:
            self._robots[origin] = asyncio.ensure_future(self._load_robots(origin))
        return await self._robots[origin]

    async def _load_robots(self, origin: str) -> robots_mod.RobotsResult:
        try:
            self.budget.charge("robots")
        except BudgetExceeded as exc:
            res = robots_mod.RobotsResult(origin, "disallow_all", None, exc.code, str(exc))
            self._emit(res)
            return res
        target, status, body, error = origin + "/robots.txt", None, None, None
        try:
            for hop in range(6):  # RFC 9309: follow at least five redirects
                # every hop (including the first) is resolved once, checked, and pinned
                ip, problem = await self._resolve(urlsplit(target).hostname or "")
                if problem or urlsplit(target).scheme not in ("http", "https"):
                    where = "redirected to" if hop else "host is"
                    error = f"robots.txt {where} a disallowed target ({problem[0].value if problem else 'scheme'})"
                    break
                async with asyncio.timeout(self.f.total_timeout):
                    async with self.client.stream("GET", target, extensions={"pinned_ip": ip}) as r:
                        location = r.headers.get("location") if r.is_redirect else None
                        chunks = bytearray()
                        if not location:
                            async for chunk in r.aiter_bytes():
                                chunks.extend(chunk)
                                if len(chunks) >= robots_mod.MAX_ROBOTS_BYTES:
                                    break
                if location:
                    target = urljoin(target, location)
                    continue
                status = r.status_code
                body = bytes(chunks[: robots_mod.MAX_ROBOTS_BYTES]).decode("utf-8", errors="replace")
                break
            else:
                error = "too many robots.txt redirects"
        except (httpx.HTTPError, TimeoutError) as exc:
            error = f"{type(exc).__name__}"
        res = robots_mod.build(origin, status, body, error)
        self._emit(res)
        return res

    def _emit(self, res: robots_mod.RobotsResult) -> None:
        if self.on_robots:
            self.on_robots(res)

    # ------------------------------------------------------------------ fetch

    async def fetch(self, url: str) -> FetchResult:
        attempts: list[Attempt] = []
        self.inflight[url] = attempts
        current, seen, hops, retries = url, {url}, 0, 0
        n = 0

        def finish(code: Code | None, detail: str | None, outcome: str = "failure", **kw) -> FetchResult:
            if outcome == "skipped":
                attempts.append(Attempt(0, current, "skipped", failure_code=code.value if code else None, detail=detail))
            return FetchResult(url, current, code is None, attempts, code.value if code else None, detail, **kw)

        while True:
            if hops:
                _, decision = check(current, self.cfg.platforms)
                if not decision.allowed:
                    return finish(Code.REDIRECT_BLOCKED, f"{decision.code.value}: {decision.detail or current}", "skipped")
            host = urlsplit(current).hostname or ""
            ip, problem = await self._resolve(host)
            if problem:
                return finish(problem[0], problem[1], "skipped")
            rules = await self.robots_for(current)
            if not rules.allows(current):
                code = rules.failure_code or Code.ROBOTS_DISALLOWED
                if code not in (Code.ROBOTS_UNREACHABLE, Code.BUDGET_ROBOTS):
                    code = Code.ROBOTS_DISALLOWED
                return finish(code, rules.detail or "disallowed for our user agent", "skipped")
            if self.budget.remaining("bytes") <= 0:
                return finish(Code.BUDGET_BYTES, "run byte budget already exhausted", "skipped")
            try:
                self.budget.charge("fetches")
            except BudgetExceeded as exc:
                return finish(exc.code, str(exc), "skipped")

            n += 1
            attempt, resp = await self._request(current, n, host, rules.crawl_delay, attempts, ip)
            status = attempt.http_status

            # transient failures -> retry with backoff (an access challenge is never transient)
            transient = attempt.failure_code in (Code.NETWORK_TIMEOUT.value, Code.NETWORK_ERROR.value) or (
                status in RETRYABLE_STATUS and not attempt.failure_code)
            if transient:
                wait = _retry_after(resp.get("retry_after"))
                if retries < self.f.max_retries and (wait is None or wait <= self.f.retry_after_cap):
                    backoff = min(self.f.backoff_max, self.f.backoff_base * 2 ** retries)
                    delay = max(wait or 0.0, backoff + self.rng.uniform(0, self.f.backoff_base))
                    attempt.outcome = "retry"
                    attempt.failure_code = attempt.failure_code or (
                        Code.HTTP_RATE_LIMITED if status == 429 else Code.HTTP_SERVER_ERROR).value
                    attempt.detail = f"{attempt.detail or 'HTTP ' + str(status)}; retry in {delay:.1f}s"
                    retries += 1
                    await self.sleep(delay)
                    continue
                code = (Code(attempt.failure_code) if attempt.failure_code else
                        Code.HTTP_RATE_LIMITED if status == 429 else Code.HTTP_SERVER_ERROR)
                attempt.outcome, attempt.failure_code = "failure", code.value
                if wait is not None and wait > self.f.retry_after_cap:
                    attempt.detail = f"Retry-After {wait:.0f}s exceeds cap {self.f.retry_after_cap:.0f}s"
                return finish(code, attempt.detail, http_status=status)

            if attempt.failure_code:  # non-retryable failure decided in _request
                attempt.outcome = "failure"
                return finish(Code(attempt.failure_code), attempt.detail, http_status=status,
                              content_type=attempt.content_type)

            if status is not None and 300 <= status < 400 and resp.get("location"):
                attempt.outcome = "redirect"
                hops += 1
                nxt = urljoin(current, resp["location"])
                attempt.detail = f"-> {nxt}"
                if nxt in seen:
                    return finish(Code.REDIRECT_LOOP, nxt, http_status=status)
                if hops > self.f.max_redirects:
                    return finish(Code.TOO_MANY_REDIRECTS, f"more than {self.f.max_redirects}", http_status=status)
                seen.add(nxt)
                current = nxt
                continue

            if status is not None and 200 <= status < 300:
                attempt.outcome = "success"
                return finish(None, None, http_status=status, content_type=attempt.content_type,
                              route=resp["route"], body=resp["body"])

            code = (Code.HTTP_ACCESS_CONTROLLED if status in ACCESS_CONTROLLED else
                    Code.HTTP_NOT_FOUND if status in (404, 410) else Code.HTTP_CLIENT_ERROR)
            attempt.outcome, attempt.failure_code = "failure", code.value
            attempt.detail = attempt.detail or f"HTTP {status}"
            return finish(code, attempt.detail, http_status=status)

    async def _request(self, url: str, n: int, host: str, crawl_delay: float | None,
                       log: list[Attempt], ip: str) -> tuple[Attempt, dict]:
        attempt = Attempt(n, url, "failure", connected_ip=ip)
        log.append(attempt)  # recorded before the request is sent
        info: dict = {}
        async with self._global, self._hosts[host]:
            await self._polite(host, crawl_delay)
            start = time.monotonic()
            try:
                async with asyncio.timeout(self.f.total_timeout):
                    async with self.client.stream("GET", url, extensions={"pinned_ip": ip}) as r:
                        attempt.http_status = r.status_code
                        ctype = r.headers.get("content-type", "")
                        attempt.content_type = ctype or None
                        info["retry_after"] = r.headers.get("retry-after")
                        info["location"] = r.headers.get("location")
                        if 200 <= r.status_code < 300:
                            await self._read_body(r, ctype, attempt, info)
                        elif r.status_code in (403, 429, 503):
                            await self._check_challenge(r, attempt)
            except PinningError as exc:  # policy refusal at connect time: final, never retried
                attempt.failure_code, attempt.detail = Code.CONNECTION_POLICY_REFUSED.value, str(exc)[:200]
            except (TimeoutError, httpx.TimeoutException) as exc:
                attempt.failure_code, attempt.detail = Code.NETWORK_TIMEOUT.value, type(exc).__name__
            except httpx.TransportError as exc:
                attempt.failure_code, attempt.detail = Code.NETWORK_ERROR.value, type(exc).__name__
            attempt.elapsed_ms = int((time.monotonic() - start) * 1000)
        return attempt, info

    async def _check_challenge(self, r: httpx.Response, attempt: Attempt) -> None:
        """403/429/503 may be a bot interstitial: read a small, budgeted sample to classify it precisely."""
        sample = bytearray()
        if r.headers.get("cf-mitigated", "").lower() != "challenge":
            async for chunk in r.aiter_bytes():
                sample.extend(chunk)
                if len(sample) >= 65_536:
                    break
            self.budget.record("bytes", len(sample))
            attempt.bytes = len(sample)
        html = bytes(sample).decode("utf-8", errors="replace")
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        reason = challenge.detect(headers=dict(r.headers), html=html,
                                  title=title.group(1).strip() if title else None, text_chars=len(html))
        if reason:
            attempt.failure_code, attempt.detail = Code.ACCESS_CHALLENGE.value, f"HTTP {r.status_code}; {reason}"

    async def _read_body(self, r: httpx.Response, ctype: str, attempt: Attempt, info: dict) -> None:
        ct = ctype.split(";")[0].strip().lower()
        if ct == "application/pdf":
            attempt.failure_code, attempt.detail = Code.UNSUPPORTED_PDF.value, "application/pdf (not downloaded)"
            return
        if any(ct.startswith(t) for t in REJECT_TYPES):
            attempt.failure_code, attempt.detail = Code.UNSUPPORTED_CONTENT_TYPE.value, ct
            return
        declared = r.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.f.max_page_bytes:
            attempt.failure_code = Code.RESPONSE_TOO_LARGE.value
            attempt.detail = f"declared {declared} bytes > {self.f.max_page_bytes}"
            return
        budget_left = int(self.budget.remaining("bytes"))
        limit = min(self.f.max_page_bytes, budget_left)
        body = bytearray()
        async for chunk in r.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                break
        self.budget.record("bytes", len(body))
        attempt.bytes = len(body)
        if len(body) > limit:
            if limit < self.f.max_page_bytes:
                attempt.failure_code, attempt.detail = Code.BUDGET_BYTES.value, f"run byte budget exhausted at {limit}"
            else:
                attempt.failure_code, attempt.detail = Code.RESPONSE_TOO_LARGE.value, f"> {limit} bytes"
            return
        if bytes(body[:5]) == b"%PDF-":  # PDF served under a generic or wrong content type
            attempt.failure_code, attempt.detail = Code.UNSUPPORTED_PDF.value, f"PDF signature under {ct or 'no type'}"
            return
        route = route_for(ctype, bytes(body[:2048]))
        if route is None:
            attempt.failure_code, attempt.detail = Code.UNSUPPORTED_CONTENT_TYPE.value, ct or "unknown"
            return
        info["route"], info["body"] = route, bytes(body)
