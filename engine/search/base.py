"""Provider interface: capabilities, terms-derived storage policy, pricing, and a shared HTTP executor
with retries and error classification. Adapters only render requests and parse responses."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

from ..failures import Code
from .spec import SearchSpec


@dataclass(frozen=True)
class Capabilities:
    quoted_phrases: bool
    boolean_or: bool
    site_operator: bool  # site: inside the query text
    domain_filter: bool  # include/exclude domain parameters
    path_filter: bool  # domain filter accepts path prefixes
    freshness: bool
    max_results: int
    returns_snippets: bool
    extract_fallback: bool  # can supply page text for a URL (used only after a technical fetch failure)


@dataclass(frozen=True)
class TermsPolicy:
    """Derived from each provider's terms (see docs/engine/DECISIONS.md D7)."""

    storage: str  # "persist" | "transient"
    basis: str
    attribution: str | None = None


@dataclass(frozen=True)
class Pricing:
    unit: str  # request | credit | usd
    units_per_search: float
    usd_per_unit: float  # list price, used for estimates and the per-run cost ceiling

    def estimate(self, units: float) -> float:
        return round(units * self.usd_per_unit, 6)


@dataclass
class Rendered:
    method: str
    url: str
    params: dict | None = None
    json: dict | None = None
    headers: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def public(self) -> dict:
        """Request description safe to store (no headers: they carry the API key)."""
        return {"method": self.method, "url": self.url, "params": self.params, "json": self.json}


@dataclass
class Hit:
    rank: int
    url: str
    title: str | None = None
    snippet: str | None = None
    provider_date: str | None = None


@dataclass
class Usage:
    units: float
    reported_cost_usd: float | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class SearchOutcome:
    ok: bool
    hits: list[Hit] = field(default_factory=list)
    failure_code: Code | None = None
    detail: str | None = None
    http_status: int | None = None
    attempts: int = 0
    latency_ms: int | None = None
    request_id: str | None = None
    usage: Usage | None = None
    disable_provider: bool = False


def iso_date(value) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            return None


class Provider:
    name: str
    capabilities: Capabilities
    terms: TermsPolicy
    pricing: Pricing

    def __init__(self, api_key: str | None, *, client: httpx.AsyncClient, timeout: float = 20.0,
                 max_retries: int = 2, sleep=asyncio.sleep):
        self.api_key = api_key
        self.client = client
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep

    # adapters implement these
    def render(self, spec: SearchSpec) -> Rendered:
        raise NotImplementedError

    def parse(self, data: dict, spec: SearchSpec) -> tuple[list[Hit], Usage, str | None]:
        raise NotImplementedError

    def quota_codes(self) -> set[int]:
        return {402}

    def expected_units(self, spec: SearchSpec) -> float:
        return self.pricing.units_per_search

    # shared execution
    async def search(self, spec: SearchSpec) -> SearchOutcome:
        if not self.api_key:
            return SearchOutcome(False, failure_code=Code.SEARCH_NOT_CONFIGURED, detail=f"{self.name}: no API key")
        req = self.render(spec)
        return await self._execute(req, lambda data: self.parse(data, spec))

    async def _execute(self, req: Rendered, parse) -> SearchOutcome:
        attempts, start = 0, time.monotonic()
        while True:
            attempts += 1
            try:
                async with asyncio.timeout(self.timeout):
                    r = await self.client.request(req.method, req.url, params=req.params, json=req.json,
                                                  headers=req.headers)
            except (TimeoutError, httpx.TimeoutException):
                status, r = None, None
                code, detail = Code.SEARCH_TIMEOUT, "timeout"
            except httpx.TransportError as exc:
                status, r = None, None
                code, detail = Code.SEARCH_PROVIDER_ERROR, type(exc).__name__
            else:
                status = r.status_code
                code, detail = None, None
            latency = int((time.monotonic() - start) * 1000)
            if r is not None and 200 <= r.status_code < 300:
                try:
                    hits, usage, request_id = parse(r.json())
                except (ValueError, KeyError, TypeError) as exc:
                    return SearchOutcome(False, failure_code=Code.SEARCH_BAD_RESPONSE, detail=type(exc).__name__,
                                         http_status=status, attempts=attempts, latency_ms=latency,
                                         usage=Usage(self.pricing.units_per_search))
                usage.detail.update(_quota_headers(r.headers))
                return SearchOutcome(True, hits, http_status=status, attempts=attempts, latency_ms=latency,
                                     request_id=request_id or r.headers.get("x-request-id"), usage=usage)
            if r is not None:
                code, detail = self._classify(r)
            retryable = code in (Code.SEARCH_RATE_LIMITED, Code.SEARCH_PROVIDER_ERROR, Code.SEARCH_TIMEOUT)
            if retryable and attempts <= self.max_retries:
                wait = _retry_after(r.headers.get("retry-after") if r is not None else None)
                if wait is None or wait <= 30:
                    await self.sleep(wait if wait is not None else 0.5 * 2 ** (attempts - 1))
                    continue
            return SearchOutcome(False, failure_code=code, detail=detail, http_status=status, attempts=attempts,
                                 latency_ms=latency,
                                 disable_provider=code in (Code.SEARCH_QUOTA_EXHAUSTED, Code.SEARCH_AUTH_ERROR))

    def _classify(self, r: httpx.Response) -> tuple[Code, str]:
        body = r.text[:300]
        if r.status_code in self.quota_codes():
            return Code.SEARCH_QUOTA_EXHAUSTED, f"HTTP {r.status_code}: {body}"
        if r.status_code in (401, 403):
            return Code.SEARCH_AUTH_ERROR, f"HTTP {r.status_code}"
        if r.status_code == 429:
            return Code.SEARCH_RATE_LIMITED, f"HTTP 429: {body}"
        if r.status_code in (400, 404, 405, 422):
            return Code.SEARCH_BAD_REQUEST, f"HTTP {r.status_code}: {body}"
        return Code.SEARCH_PROVIDER_ERROR, f"HTTP {r.status_code}"


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _quota_headers(headers: httpx.Headers) -> dict:
    return {k.lower(): v for k, v in headers.items() if k.lower().startswith(("x-ratelimit", "ratelimit"))}
