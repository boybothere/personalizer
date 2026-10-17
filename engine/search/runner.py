"""Executes SearchSpecs against providers with budget, free-tier and storage-policy enforcement.

For every (spec, provider) pair exactly one search_queries row is written: completed, failed or
skipped, each with a reason. Results become leads; storage of result text follows the provider's
terms (transient providers' titles/snippets are never written).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import timedelta

import httpx

from .. import privacy
from ..budget import BudgetExceeded, RunBudget
from ..config import EngineConfig
from ..failures import BUDGET_CODES, Code
from ..retrieval import Lead
from ..store import repo
from ..urls import InvalidURL, canonicalize
from . import ledger
from .base import Provider
from .providers import PROVIDERS
from .spec import SearchSpec, off_target

MIN_INTERVAL = {"brave": 1.05}  # Brave free plan: 1 request/second
STOP_SEARCH = BUDGET_CODES | {Code.CANCELLED}


def build_providers(cfg: EngineConfig, client: httpx.AsyncClient, sleep=asyncio.sleep) -> dict[str, Provider]:
    p = cfg.providers
    return {name: PROVIDERS[name](getattr(p, f"{name}_api_key"), client=client, timeout=p.timeout,
                                  max_retries=p.max_retries, sleep=sleep)
            for name in p.enabled if name in PROVIDERS}


class SearchRunner:
    def __init__(self, conn: sqlite3.Connection, cfg: EngineConfig, providers: dict[str, Provider], run_id: str,
                 budget: RunBudget, sleep=asyncio.sleep):
        self.conn, self.cfg, self.providers, self.run_id, self.budget = conn, cfg, providers, run_id, budget
        self.sleep = sleep
        self.disabled: dict[str, tuple[Code, str]] = {}
        self.seen: set[str] = set()
        self.stop: Code | None = None
        self._last: dict[str, float] = {}
        self.last_query_ids: list[int] = []  # search_queries rows written by the most recent execute()

    def available_providers(self) -> list[str]:
        return [n for n, p in self.providers.items() if p.api_key and n not in self.disabled]

    async def execute(self, specs: list[SearchSpec]) -> list[Lead]:
        self.last_query_ids = []
        leads: list[Lead] = []
        for spec in specs:
            for name, provider in self.providers.items():
                leads += await self._one(spec, name, provider)
        return leads

    # ------------------------------------------------------------------ one query

    async def _one(self, spec: SearchSpec, name: str, provider: Provider) -> list[Lead]:
        rendered = provider.render(spec) if provider.api_key else None
        base = {"provider": name, "purpose": spec.purpose, "spec": spec.as_dict(),
                "rendered": rendered.public() if rendered else None,
                "notes": rendered.notes if rendered else [], "policy": provider.terms.storage}
        if self.stop:
            return self._skip(base, self.stop, "run stopped before this query")
        if name in self.disabled:
            code, why = self.disabled[name]
            return self._skip(base, Code.SEARCH_PROVIDER_DISABLED if code != Code.SEARCH_FREE_TIER_GUARD else code, why)
        if not provider.api_key:
            return self._skip(base, Code.SEARCH_NOT_CONFIGURED, f"{name}: no API key")
        expected = provider.expected_units(spec)
        guard = ledger.would_exceed(self.conn, name, expected, self.cfg.providers)
        if guard:
            self.disabled[name] = (Code.SEARCH_FREE_TIER_GUARD, guard)
            return self._skip(base, Code.SEARCH_FREE_TIER_GUARD, guard)
        expected_usd = provider.pricing.estimate(expected)
        try:
            self.budget.charge("searches")
            self.budget.charge("cost", expected_usd)
        except BudgetExceeded as exc:
            self.stop = exc.code
            return self._skip(base, exc.code, str(exc))
        await self._pace(name)
        outcome = await provider.search(spec)

        if outcome.usage is not None:  # the provider answered 2xx: usage is billable against the allowance
            units = outcome.usage.units
            est = provider.pricing.estimate(units)
            ledger.record(self.conn, name, "search", provider.pricing.unit, units, est,
                          outcome.usage.reported_cost_usd, self.run_id)
            if est > expected_usd:
                self.budget.record("cost", est - expected_usd)
        if outcome.disable_provider:
            self.disabled[name] = (outcome.failure_code, outcome.detail or "")
        qid = self._record_query(base, outcome)
        if outcome.usage is not None:
            self.conn.execute(
                "INSERT INTO provider_usage (run_id, provider, operation, units, cost_usd, at, query_id, unit,"
                " reported_cost_usd, detail_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, name, "search", outcome.usage.units, provider.pricing.estimate(outcome.usage.units),
                 repo.iso(), qid, provider.pricing.unit, outcome.usage.reported_cost_usd,
                 json.dumps(outcome.usage.detail, default=str)))
        if not outcome.ok:
            return []
        return self._results(qid, spec, name, provider, outcome.hits,
                             rendered.params.get("q") if rendered.params else (rendered.json or {}).get("query"))

    def _results(self, qid: int, spec: SearchSpec, name: str, provider: Provider, hits, query_text) -> list[Lead]:
        persist = provider.terms.storage == "persist"
        expires = repo.iso(repo.now() + timedelta(days=self.cfg.retention.text_days)) if persist else None
        leads, off, new = [], 0, 0
        for hit in hits:
            try:
                c = canonicalize(hit.url)
            except InvalidURL:
                continue
            is_off = off_target(hit.url, spec)
            off += is_off
            if c.key not in self.seen:
                new += 1
                self.seen.add(c.key)
            cur = self.conn.execute(
                "INSERT INTO search_results (run_id, query_id, provider, rank, url, url_key, domain, title, snippet,"
                " provider_date, off_target, text_expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, qid, name, hit.rank, privacy.safe_url(hit.url), c.key, c.domain,
                 privacy.filter_text(hit.title) if persist else None,
                 privacy.filter_text(hit.snippet) if persist else None, hit.provider_date, int(is_off), expires))
            if not is_off:
                leads.append(Lead(hit.url, kind="search_result", origin_ref=f"search_query:{qid}", provider=name,
                                  query_text=query_text, rank=hit.rank, snippet=hit.snippet,
                                  storage_policy=provider.terms.storage, search_result_id=cur.lastrowid))
        self.conn.execute("UPDATE search_queries SET results_off_target=?, new_urls=? WHERE id=?", (off, new, qid))
        repo.trace(self.conn, self.run_id, "search", "results", ref_type="search_query", ref_id=qid,
                   reason=f"{len(hits)} results, {new} new URLs, {off} off-target",
                   data={"provider": name, "storage": provider.terms.storage})
        return leads

    # ------------------------------------------------------------------ helpers

    async def _pace(self, name: str) -> None:
        interval = MIN_INTERVAL.get(name, 0.0)
        wait = self._last.get(name, -1e9) + interval - time.monotonic()
        if wait > 0:
            await self.sleep(wait)
        self._last[name] = time.monotonic()

    def _insert(self, base: dict, status: str, **f) -> int:
        qid = self._insert_row(base, status, **f)
        self.last_query_ids.append(qid)
        return qid

    def _insert_row(self, base: dict, status: str, **f) -> int:
        cur = self.conn.execute(
            "INSERT INTO search_queries (run_id, provider, purpose, spec_json, rendered_json, rendering_notes, status,"
            " failure_code, http_status, attempts, latency_ms, results_returned, request_id, storage_policy,"
            " started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, base["provider"], base["purpose"], json.dumps(base["spec"]),
             json.dumps(base["rendered"]) if base["rendered"] else None, json.dumps(base["notes"]) or None, status,
             f.get("failure_code"), f.get("http_status"), f.get("attempts", 0), f.get("latency_ms"),
             f.get("results_returned", 0), f.get("request_id"), base["policy"], repo.iso()))
        return cur.lastrowid

    def _skip(self, base: dict, code: Code, detail: str) -> list[Lead]:
        qid = self._insert(base, "skipped", failure_code=code.value)
        repo.trace(self.conn, self.run_id, "search", "query_skipped", ref_type="search_query", ref_id=qid,
                   reason=code.value, data={"provider": base["provider"], "detail": detail})
        return []

    def _record_query(self, base: dict, outcome) -> int:
        qid = self._insert(base, "completed" if outcome.ok else "failed",
                           failure_code=outcome.failure_code.value if outcome.failure_code else None,
                           http_status=outcome.http_status, attempts=outcome.attempts, latency_ms=outcome.latency_ms,
                           results_returned=len(outcome.hits), request_id=outcome.request_id)
        repo.trace(self.conn, self.run_id, "search", "query_completed" if outcome.ok else "query_failed",
                   ref_type="search_query", ref_id=qid, reason=base["purpose"] if outcome.ok else
                   f"{outcome.failure_code.value}: {(outcome.detail or '')[:200]}",
                   data={"provider": base["provider"], "attempts": outcome.attempts})
        return qid
