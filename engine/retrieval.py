"""Retrieval run: leads -> canonical URLs -> policy -> robots -> fetch -> extract -> documents.

Every step writes a record (leads, urls, fetch_attempts, robots_checks, documents) and a trace event,
so a run can be audited from its inputs to its documents. Search results enter as leads whose
snippet is stored with its limitations; a snippet never becomes a document.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

from .budget import BudgetExceeded, RunBudget
from .config import EngineConfig
from .extract.feeds import parse_feed
from .extract.html import extract_html, extract_text
from .extract.textutil import hamming
from .failures import BUDGET_CODES, Code
from .fetch.client import Attempt, Fetcher, FetchResult, Resolver
from .fetch.robots import RobotsResult
from .observe import evaluate_gates, stage
from .store import repo
from .urls import check

SNIPPET_LIMITATIONS = ["provider-generated text", "may be truncated", "may be stale",
                       "no surrounding context", "not retrieved by this engine"]
STOP_CODES = BUDGET_CODES | {Code.CANCELLED}
# Stops that make further fetching impossible. Lead, search and cost limits stop *new* work but do not
# abandon already-accepted URLs, which are still fetched within the remaining fetch/byte/time budget.
FETCH_BLOCKING_STOPS = STOP_CODES - {Code.BUDGET_LEADS, Code.BUDGET_SEARCHES, Code.BUDGET_COST}


@dataclass
class Lead:
    url: str
    kind: str = "seed_url"  # seed_url | search_result | connector_link | discovered_link
    origin_ref: str | None = None
    provider: str | None = None
    query_text: str | None = None
    rank: int | None = None
    snippet: str | None = None
    storage_policy: str | None = None  # provider terms: "persist" | "transient" (None for non-search leads)
    search_result_id: int | None = None


# Fetch/extraction outcomes after which provider-supplied page text may be used (decision D3.3):
# technical failures only. Robots, access control, policy, not-found and rate limits never qualify.
TECHNICAL_FETCH_FAILURES = {Code.NETWORK_TIMEOUT, Code.NETWORK_ERROR, Code.HTTP_SERVER_ERROR,
                            Code.RESPONSE_TOO_LARGE, Code.UNSUPPORTED_CONTENT_TYPE}
TECHNICAL_EXTRACTION_FAILURES = {Code.JS_REQUIRED, Code.EXTRACTION_EMPTY, Code.DECODE_ERROR}


class Retriever:
    def __init__(self, conn: sqlite3.Connection, cfg: EngineConfig, *, transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Resolver | None = None, sleep=asyncio.sleep, today: date | None = None,
                 rng: random.Random | None = None, fallback=None):
        self.conn = conn
        self.cfg = cfg
        self.transport = transport
        self.resolver = resolver
        self.sleep = sleep
        self.today = today or datetime.now(timezone.utc).date()
        self.rng = rng
        self.fallback = fallback  # async (run_id, url, reason_code, budget) -> dict | None (provider extract)
        self.subject_name: str | None = None  # research runs: lets the write boundary count removed mentions
        self.stop: Code | None = None

    # ------------------------------------------------------------------ public

    async def run(self, leads: list[Lead], *, subject_id: str | None = None, label: str | None = None) -> str:
        from . import maintenance

        maint = maintenance.startup(self.conn, self.cfg)  # retention is enforced before every run
        run_id = repo.create_run(self.conn, "retrieval", {"label": label, "leads": [asdict(l) for l in leads]},
                                 self.cfg.safe_dump(), self.cfg.retention.run_days, subject_id)
        repo.trace(self.conn, run_id, "run", "started", reason="retrieval run requested",
                   data={"leads": len(leads), "budget": self.cfg.budget.model_dump()})
        maintenance.record(self.conn, run_id, maint)
        budget = RunBudget(self.cfg.budget)
        self.stop = None
        await self.process(run_id, leads, budget)
        return self.finish(run_id, budget)

    def make_fetcher(self, run_id: str, budget: RunBudget) -> Fetcher:
        return Fetcher(self.cfg, budget, resolver=self.resolver, transport=self.transport, sleep=self.sleep,
                       on_robots=lambda r: self._on_robots(run_id, r), rng=self.rng)

    async def process(self, run_id: str, leads: list[Lead], budget: RunBudget, fetcher: Fetcher | None = None) -> None:
        """Intake, retrieval and near-duplicate marking for `leads`, inside an existing run and budget.

        Pass a long-lived `fetcher` when calling repeatedly in one run (robots.txt cache, politeness state)."""
        own = fetcher is None
        fetcher = fetcher or self.make_fetcher(run_id, budget)
        try:
            with stage(self.conn, run_id, "intake"):
                accepted = self._intake(run_id, leads, budget)
            with stage(self.conn, run_id, "retrieval"):
                await self._retrieve_all(run_id, accepted, fetcher, budget)
            with stage(self.conn, run_id, "near_duplicates"):
                self._mark_near_duplicates(run_id)
        finally:
            if own:
                await fetcher.aclose()

    def finish(self, run_id: str, budget: RunBudget, extra_stop: Code | None = None) -> str:
        stop = self.stop or extra_stop
        status, gates = evaluate_gates(self.conn, run_id, stop)
        stop_reason = stop.value if stop else "all_work_processed"
        repo.finish_run(self.conn, run_id, status, stop_reason, gates)
        repo.trace(self.conn, run_id, "run", "finished", reason=stop_reason,
                   data={"status": status, "budget": budget.snapshot()})
        return run_id

    # ------------------------------------------------------------------ stages

    def _intake(self, run_id: str, leads: list[Lead], budget: RunBudget) -> list[tuple[int, str]]:
        accepted: list[tuple[int, str]] = []
        for i, lead in enumerate(leads):
            try:
                budget.charge("leads")
            except BudgetExceeded as exc:
                self._set_stop(exc.code)
                repo.trace(self.conn, run_id, "intake", "leads_dropped", reason=str(exc),
                           data={"dropped": len(leads) - i})
                break
            keep_snippet = lead.snippet if lead.storage_policy != "transient" else None
            limitations = (SNIPPET_LIMITATIONS if keep_snippet else
                           ["snippet not stored: provider terms allow only transient use"]
                           if lead.storage_policy == "transient" and lead.snippet else None)
            lead_id = repo.add_lead(
                self.conn, run_id, lead.url, lead.kind, origin_ref=lead.origin_ref, provider=lead.provider,
                query_text=lead.query_text, rank=lead.rank, snippet=keep_snippet,
                snippet_limitations=limitations, storage_policy=lead.storage_policy,
                snippet_expires_at=repo.iso(repo.now() + timedelta(days=self.cfg.retention.text_days))
                if keep_snippet else None)
            if lead.search_result_id:
                self.conn.execute("UPDATE search_results SET lead_id=? WHERE id=?", (lead_id, lead.search_result_id))
            canonical, decision = check(lead.url, self.cfg.platforms)
            key = canonical.key if canonical else hashlib.sha256(lead.url.encode()).hexdigest()[:32]
            existing = repo.find_url(self.conn, run_id, key)
            if existing:
                repo.link_lead(self.conn, lead_id, existing["id"], duplicate=True)
                repo.trace(self.conn, run_id, "intake", "duplicate_lead", ref_type="url", ref_id=existing["id"],
                           reason="same canonical URL as an earlier lead", data={"lead_id": lead_id})
                continue
            url_id = repo.add_url(
                self.conn, run_id, url_key=key, canonical_url=canonical.url if canonical else lead.url,
                domain=canonical.domain if canonical else "", registrable_domain=canonical.registrable if canonical else "",
                platform=decision.platform, decision="accepted" if decision.allowed else "rejected",
                reject_code=decision.code.value if decision.code else None, reject_detail=decision.detail)
            repo.link_lead(self.conn, lead_id, url_id, duplicate=False)
            repo.trace(self.conn, run_id, "intake", "url_accepted" if decision.allowed else "url_rejected",
                       ref_type="url", ref_id=url_id,
                       reason=decision.code.value if decision.code else "passed static policy",
                       data={"lead_id": lead_id, "detail": decision.detail})
            if decision.allowed:
                accepted.append((url_id, canonical.url))
        return accepted

    async def _retrieve_all(self, run_id: str, accepted: list[tuple[int, str]], fetcher: Fetcher,
                            budget: RunBudget) -> None:
        main = asyncio.ensure_future(asyncio.gather(*(self._one(run_id, uid, url, fetcher, budget)
                                                      for uid, url in accepted)))
        watcher = asyncio.ensure_future(self._watch(run_id, budget, main))
        try:
            await main
        except asyncio.CancelledError:
            self._set_stop(Code.CANCELLED if budget.cancelled else Code.BUDGET_TIME)
        finally:
            watcher.cancel()
        # anything not finalised (cancelled in flight, or never started) is recorded explicitly
        for uid, url in accepted:
            self._finalise_unprocessed(run_id, uid, url, fetcher)

    async def _watch(self, run_id: str, budget: RunBudget, main: asyncio.Future) -> None:
        while not main.done():
            if repo.cancel_requested(self.conn, run_id):
                budget.cancelled = True
                main.cancel()
                return
            if budget.remaining_seconds() <= 0:
                main.cancel()
                return
            await asyncio.sleep(min(0.25, max(0.01, budget.remaining_seconds())))

    async def _one(self, run_id: str, url_id: int, url: str, fetcher: Fetcher, budget: RunBudget) -> None:
        if self.stop in FETCH_BLOCKING_STOPS:
            return  # recorded by _finalise_unprocessed
        try:
            budget.check()
        except BudgetExceeded as exc:
            self._set_stop(exc.code)
            return
        result = await fetcher.fetch(url)
        attempt_ids = self._persist_attempts(run_id, url_id, result.attempts)
        fetcher.inflight.pop(url, None)
        codes = {a.failure_code for a in result.attempts}
        for code in STOP_CODES:
            if code.value in codes:
                self._set_stop(code)
        repo.trace(self.conn, run_id, "retrieval", "fetched" if result.ok else "fetch_failed", ref_type="url",
                   ref_id=url_id, reason=result.failure_code or "2xx response",
                   data={"final_url": result.final_url, "attempts": len(result.attempts), "detail": result.detail})
        reason: Code | None = None
        if result.ok:
            with stage(self.conn, run_id, "extraction"):
                code = self._extract(run_id, url_id, attempt_ids[-1], result)
            if code and Code(code) in TECHNICAL_EXTRACTION_FAILURES:
                reason = Code(code)
        elif result.failure_code and Code(result.failure_code) in TECHNICAL_FETCH_FAILURES:
            reason = Code(result.failure_code)
        if reason and self.fallback is not None:
            await self._use_fallback(run_id, url_id, result.final_url, reason, attempt_ids, budget)

    async def _use_fallback(self, run_id: str, url_id: int, url: str, reason: Code, attempt_ids: list[int],
                            budget: RunBudget) -> None:
        """Provider-supplied page text, only after a technical failure of our own fetch/extraction (D3.3)."""
        # Defence in depth: never after any robots/access/policy outcome on this URL.
        blocked = self.conn.execute(
            "SELECT 1 FROM fetch_attempts WHERE url_id=? AND failure_code IN (?,?,?,?,?,?) UNION "
            "SELECT 1 FROM documents WHERE url_id=? AND failure_code=?",
            (url_id, Code.ROBOTS_DISALLOWED.value, Code.ROBOTS_UNREACHABLE.value, Code.HTTP_ACCESS_CONTROLLED.value,
             Code.REDIRECT_BLOCKED.value, Code.ACCESS_CHALLENGE.value, Code.CONNECTION_POLICY_REFUSED.value,
             url_id, Code.ACCESS_CHALLENGE.value)).fetchone()
        if blocked:
            repo.trace(self.conn, run_id, "fallback", "refused", ref_type="url", ref_id=url_id,
                       reason="robots/access-control outcome on this URL")
            return
        record = await self.fallback(run_id, url, reason, budget)
        if record is None:
            repo.trace(self.conn, run_id, "fallback", "unavailable", ref_type="url", ref_id=url_id, reason=reason.value)
            return
        doc = repo.add_document(self.conn, run_id, url_id, attempt_ids[-1] if attempt_ids else None,
                                {**record, "evidence_kind": "provider_extract", "source_url": url},
                                self.cfg.retention.text_days, subject_name=self.subject_name)
        repo.trace(self.conn, run_id, "fallback", "provider_extract_used", ref_type="document", ref_id=doc,
                   reason=f"own retrieval failed technically: {reason.value}",
                   data={"provider": record.get("metadata", {}).get("provider"), "usable": record.get("usable")})

    # ------------------------------------------------------------------ persistence helpers

    def _persist_attempts(self, run_id: str, url_id: int, attempts: list[Attempt]) -> list[int]:
        return [repo.add_attempt(self.conn, run_id, url_id, asdict(a)) for a in attempts]

    def _finalise_unprocessed(self, run_id: str, url_id: int, url: str, fetcher: Fetcher) -> None:
        if self.conn.execute("SELECT 1 FROM fetch_attempts WHERE url_id=?", (url_id,)).fetchone():
            return
        code = self.stop or Code.WORK_UNPROCESSED
        partial = fetcher.inflight.pop(url, [])
        for a in partial:  # requests that were in flight when the run stopped
            if a.http_status is None and not a.failure_code:
                a.failure_code, a.detail = code.value, "in flight when the run stopped; request may have been sent"
            repo.add_attempt(self.conn, run_id, url_id, asdict(a))
        if not partial:
            repo.add_attempt(self.conn, run_id, url_id, asdict(Attempt(0, url, "skipped", failure_code=code.value,
                                                                       detail="not started before the run stopped")))
        repo.trace(self.conn, run_id, "retrieval", "not_processed", ref_type="url", ref_id=url_id, reason=code.value)

    def _on_robots(self, run_id: str, r: RobotsResult) -> None:
        repo.add_robots_check(self.conn, run_id, r.origin, r.http_status, r.result,
                              r.failure_code.value if r.failure_code else None, r.detail, r.crawl_delay)

    def _set_stop(self, code: Code) -> None:
        if self.stop is None:
            self.stop = code

    def _extract(self, run_id: str, url_id: int, attempt_id: int, result: FetchResult) -> str | None:
        cfg = self.cfg.extract
        days = self.cfg.retention.text_days
        if result.route == "feed":
            feed = parse_feed(result.body or b"", result.final_url, cfg, self.today)
            if not feed.ok:
                doc = repo.add_document(self.conn, run_id, url_id, attempt_id, {
                    "evidence_kind": "fetched_page", "source_url": result.final_url, "content_type": result.content_type,
                    "title": feed.feed_title, "usable": False, "failure_code": feed.failure_code, "text_chars": 0,
                    "extractor": "feedparser"}, days, subject_name=self.subject_name)
                repo.trace(self.conn, run_id, "extraction", "unusable", ref_type="document", ref_id=doc,
                           reason=feed.failure_code)
                return None
            repo.trace(self.conn, run_id, "extraction", "feed_parsed", ref_type="url", ref_id=url_id,
                       reason=f"{len(feed.entries)} of {feed.total_entries} entries kept",
                       data={"total": feed.total_entries, "kept": len(feed.entries),
                             "truncated": feed.total_entries - len(feed.entries)})
            for entry in feed.entries:
                doc = repo.add_document(self.conn, run_id, url_id, attempt_id,
                                        {**entry.record, "evidence_kind": "feed_entry", "source_url": entry.link,
                                         "content_type": result.content_type}, days, subject_name=self.subject_name)
                repo.trace(self.conn, run_id, "extraction", "usable" if entry.record["usable"] else "unusable",
                           ref_type="document", ref_id=doc, reason=entry.record["failure_code"] or "feed entry")
            return None
        if result.route == "html":
            ex = extract_html(result.body or b"", result.final_url, result.content_type, cfg, self.today)
        else:
            ex = extract_text(result.body or b"", result.content_type, cfg)
        doc = repo.add_document(self.conn, run_id, url_id, attempt_id,
                                {**ex.record(), "evidence_kind": "fetched_page", "source_url": result.final_url,
                                 "content_type": result.content_type}, days, subject_name=self.subject_name)
        repo.trace(self.conn, run_id, "extraction", "usable" if ex.usable else "unusable", ref_type="document",
                   ref_id=doc, reason=ex.failure_code or f"{len(ex.text)} chars",
                   data={"published_at": ex.published.value if ex.published else None,
                         "published_source": ex.published.source if ex.published else None, "flags": ex.flags})
        return ex.failure_code

    def _mark_near_duplicates(self, run_id: str) -> None:
        rows = self.conn.execute("SELECT id, text_sha256, simhash FROM documents WHERE run_id=? AND usable=1"
                                 " AND simhash IS NOT NULL AND duplicate_of IS NULL ORDER BY created_at, id",
                                 (run_id,)).fetchall()  # idempotent across repeated process() calls
        kept: list[sqlite3.Row] = []
        threshold = self.cfg.extract.near_duplicate_hamming
        for row in rows:
            original = next((k for k in kept if k["text_sha256"] == row["text_sha256"]
                             or hamming(k["simhash"], row["simhash"]) <= threshold), None)
            if original:
                repo.mark_duplicate(self.conn, row["id"], original["id"])
                repo.trace(self.conn, run_id, "near_duplicates", "near_duplicate", ref_type="document",
                           ref_id=row["id"], reason=Code.NEAR_DUPLICATE.value, data={"duplicate_of": original["id"]})
            else:
                kept.append(row)
