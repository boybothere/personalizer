"""Search run: SearchSpecs -> providers -> leads -> retrieval, under one run and one budget.

Afterwards, results from providers whose terms allow only transient use are scrubbed: their URLs are
kept only where this engine itself requested the page (our own access record); everything else is
reduced to a hash. Provider page text is used only after a technical failure of our own retrieval.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date

import httpx

from .budget import BudgetExceeded, RunBudget
from .config import EngineConfig
from .extract import dates
from .extract.textutil import normalize, sha256, simhash
from .failures import Code
from .observe import stage
from .retrieval import Retriever
from .search import ledger
from .search.base import Hit, Provider, Usage
from .search.providers import TavilyProvider
from .search.runner import SearchRunner, build_providers
from .search.spec import SearchSpec
from .store import repo


def make_tavily_fallback(conn: sqlite3.Connection, cfg: EngineConfig, tavily: TavilyProvider):
    async def fallback(run_id: str, url: str, reason: Code, budget: RunBudget) -> dict | None:
        if not tavily.api_key or ledger.would_exceed(conn, "tavily", 1, cfg.providers):
            return None
        expected = tavily.pricing.estimate(1)
        try:
            budget.charge("cost", expected)
        except BudgetExceeded:
            return None

        def parse(data: dict):
            results = data.get("results") or []
            credits = (data.get("usage") or {}).get("credits")
            hits = [Hit(1, r["url"], None, r.get("raw_content")) for r in results if r.get("raw_content")]
            accrued = 0.2 * len(hits)  # 1 credit per 5 successful extractions, accrued per success
            units = max(float(credits or 0), accrued)
            return hits, Usage(units, detail={"failed": data.get("failed_results"), "reported_credits": credits,
                                              "accrued_credits": accrued}), data.get("request_id")

        outcome = await tavily._execute(tavily.render_extract([url]), parse)
        if outcome.usage is not None:
            units = outcome.usage.units
            ledger.record(conn, "tavily", "extract", "credit", units, tavily.pricing.estimate(units), None, run_id)
            repo.add_provider_usage(conn, run_id, "tavily", "extract", units, tavily.pricing.estimate(units))
        if not outcome.ok or not outcome.hits:
            return None
        text = normalize(outcome.hits[0].snippet or "")[: cfg.extract.max_text_chars]
        usable = len(text) >= cfg.extract.min_usable_chars
        u = dates.url_date(url)
        resolution = dates.resolve([("url_pattern", u)] if u else [], date.today())
        return {"usable": usable, "failure_code": None if usable else Code.TEXT_TOO_SHORT.value,
                "published_at": resolution.value, "published_source": resolution.source,
                "date_candidates": resolution.as_list(), "text_chars": len(text),
                "text_sha256": sha256(text) if text else None, "simhash": simhash(text) if text else None,
                "flags": [f"provider_extract_after:{reason.value}"], "extractor": "tavily-extract",
                "metadata": {"provider": "tavily", "reason_code": reason.value, "request_id": outcome.request_id,
                             "limitations": ["provider-retrieved copy; may differ from the live page",
                                             "no page metadata (author, dates) from the provider"]},
                "text": text or None}
    return fallback


def scrub_transient(conn: sqlite3.Connection, run_id: str) -> dict:
    requested_ids = """SELECT DISTINCT u.id FROM urls u JOIN fetch_attempts fa ON fa.url_id = u.id
                       WHERE u.run_id = :r AND fa.outcome != 'skipped'"""
    requested_keys = requested_ids.replace("DISTINCT u.id", "DISTINCT u.url_key")
    p = {"r": run_id}
    results = conn.execute(f"""UPDATE search_results SET url = NULL, title = NULL, snippet = NULL, withheld = 1
        WHERE run_id = :r AND withheld = 0
          AND query_id IN (SELECT id FROM search_queries WHERE run_id = :r AND storage_policy = 'transient')
          AND url_key NOT IN ({requested_keys})""", p).rowcount
    urls = conn.execute(f"""UPDATE urls SET canonical_url = 'withheld:' || url_key
        WHERE run_id = :r AND id NOT IN ({requested_ids})
          AND EXISTS (SELECT 1 FROM leads l WHERE l.url_id = urls.id AND l.storage_policy = 'transient')
          AND NOT EXISTS (SELECT 1 FROM leads l WHERE l.url_id = urls.id
                          AND (l.storage_policy IS NULL OR l.storage_policy != 'transient'))""", p).rowcount
    leads = conn.execute(f"""UPDATE leads SET url = 'withheld:' || (SELECT url_key FROM urls WHERE urls.id = leads.url_id),
        snippet = NULL WHERE run_id = :r AND storage_policy = 'transient'
          AND (url_id IS NULL OR url_id NOT IN ({requested_ids}))""", p).rowcount
    counts = {"search_results": results, "urls": urls, "leads": leads}
    repo.trace(conn, run_id, "storage_policy", "transient_results_scrubbed", reason="provider terms", data=counts)
    return counts


async def search_run(conn: sqlite3.Connection, cfg: EngineConfig, specs: list[SearchSpec], *,
                     providers: dict[str, Provider] | None = None, provider_client: httpx.AsyncClient | None = None,
                     transport: httpx.AsyncBaseTransport | None = None, resolver=None, sleep=asyncio.sleep,
                     today: date | None = None, retrieve: bool = True, label: str | None = None,
                     subject_id: str | None = None) -> str:
    repo.purge(conn)  # retention is enforced before every run
    run_id = repo.create_run(conn, "search", {"label": label, "retrieve": retrieve,
                                              "specs": [s.as_dict() for s in specs]},
                             cfg.safe_dump(), cfg.retention.run_days, subject_id)
    repo.trace(conn, run_id, "run", "started", reason="search run requested",
               data={"specs": len(specs), "providers": cfg.providers.enabled, "budget": cfg.budget.model_dump()})
    budget = RunBudget(cfg.budget)
    own_client = provider_client is None and providers is None
    client = provider_client or httpx.AsyncClient(headers={"User-Agent": cfg.user_agent})
    try:
        provs = providers if providers is not None else build_providers(cfg, client, sleep)
        runner = SearchRunner(conn, cfg, provs, run_id, budget, sleep=sleep)
        with stage(conn, run_id, "search"):
            leads = await runner.execute(specs)
        tavily = provs.get("tavily")
        fallback = make_tavily_fallback(conn, cfg, tavily) if isinstance(tavily, TavilyProvider) else None
        retriever = Retriever(conn, cfg, transport=transport, resolver=resolver, sleep=sleep, today=today,
                              fallback=fallback)
        if retrieve:
            await retriever.process(run_id, leads, budget)
        else:  # discovery-only run: leads are recorded, nothing is fetched
            with stage(conn, run_id, "intake"):
                retriever._intake(run_id, leads, budget)
        with stage(conn, run_id, "storage_policy"):
            scrub_transient(conn, run_id)
        return retriever.finish(run_id, budget, extra_stop=runner.stop)
    finally:
        if own_client:
            await client.aclose()
