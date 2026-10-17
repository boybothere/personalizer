"""Research run (phase 3): input -> plan -> adaptive controller (search + retrieval per step) -> gates.

No preference inference happens here; documents are stored as evidence candidates for later phases.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date

import httpx

from .budget import RunBudget
from .config import EngineConfig
from .controller import AdaptiveController
from .observe import stage
from .plan import ResearchInput, build_plan
from .policy import screen
from .retrieval import Retriever
from .search.base import Provider
from .search.providers import TavilyProvider
from .search.runner import SearchRunner, build_providers
from .search_run import make_tavily_fallback, scrub_transient
from .store import repo


def preview(inp: ResearchInput, cfg: EngineConfig) -> list[dict]:
    """Plan preview without network access (--dry-run)."""
    screen(inp.meeting_purpose, inp.focus_topics)
    return [{"objective": o.key, "priority": o.priority, "targets": {"docs": o.target_docs, "domains": o.target_domains},
             "status": o.status, "variants": [v.summary() for v in o.variants]}
            for o in build_plan(inp, cfg.planner)]


async def research_run(conn: sqlite3.Connection, cfg: EngineConfig, inp: ResearchInput, *,
                       providers: dict[str, Provider] | None = None, transport: httpx.AsyncBaseTransport | None = None,
                       resolver=None, sleep=asyncio.sleep, today: date | None = None, subject_id: str | None = None,
                       label: str | None = None) -> str:
    screen(inp.meeting_purpose, inp.focus_topics)  # raises PolicyViolation before anything is recorded
    repo.purge(conn)
    objectives = build_plan(inp, cfg.planner)
    run_id = repo.create_run(conn, "research", {"label": label, "input": inp.as_dict(),
                                                "plan": [o.key for o in objectives]},
                             cfg.safe_dump(), cfg.retention.run_days, subject_id)
    repo.trace(conn, run_id, "run", "started", reason="adaptive research run requested",
               data={"objectives": [o.key for o in objectives], "budget": cfg.budget.model_dump(),
                     "planner": cfg.planner.model_dump()})
    budget = RunBudget(cfg.budget)
    own_client = providers is None
    client = httpx.AsyncClient(headers={"User-Agent": cfg.user_agent}) if own_client else None
    provs = providers if providers is not None else build_providers(cfg, client, sleep)
    runner = SearchRunner(conn, cfg, provs, run_id, budget, sleep=sleep)
    tavily = provs.get("tavily")
    retriever = Retriever(conn, cfg, transport=transport, resolver=resolver, sleep=sleep, today=today,
                          fallback=make_tavily_fallback(conn, cfg, tavily) if isinstance(tavily, TavilyProvider) else None)
    fetcher = retriever.make_fetcher(run_id, budget)
    try:
        controller = AdaptiveController(conn, cfg, run_id, budget, runner, retriever, fetcher, inp, objectives)
        with stage(conn, run_id, "research"):
            stop = await controller.run()
        with stage(conn, run_id, "storage_policy"):
            scrub_transient(conn, run_id)
    finally:
        await fetcher.aclose()
        if client is not None:
            await client.aclose()
    return retriever.finish(run_id, budget, extra_stop=stop)
