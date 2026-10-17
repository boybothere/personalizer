import asyncio
import os
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.config import EngineConfig  # noqa: E402
from engine.fetch.client import StaticResolver  # noqa: E402
from engine.retrieval import Lead, Retriever  # noqa: E402
from engine.store.db import connect  # noqa: E402

TODAY = date(2026, 10, 17)
FIXTURES = Path(__file__).parent / "fixtures"


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits real public endpoints; run with ENGINE_LIVE=1")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("ENGINE_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set ENGINE_LIVE=1 to run against real endpoints")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def make_cfg(**budget) -> EngineConfig:
    cfg = EngineConfig(contact="tests@example.org")
    cfg.fetch.min_host_interval = 0
    cfg.fetch.backoff_base = 0.01
    cfg.fetch.backoff_max = 0.05
    for k, v in budget.items():
        setattr(cfg.budget, k, v)
    return cfg


class Site:
    """Scripted web server for MockTransport. routes: path -> Response | callable(request) -> Response."""

    def __init__(self, routes: dict, robots: str | httpx.Response | None = "User-agent: *\nAllow: /\n"):
        self.routes = routes
        self.robots = robots
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        if request.url.path == "/robots.txt":
            if isinstance(self.robots, httpx.Response):
                return self.robots
            return httpx.Response(404) if self.robots is None else httpx.Response(200, text=self.robots)
        route = self.routes.get(f"{request.url.host}{key}") or self.routes.get(key) or self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    def content_requests(self) -> list[str]:
        return [str(r.url) for r in self.requests if r.url.path != "/robots.txt"]


class RecordingSleep:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float):
        self.calls.append(seconds)
        await asyncio.sleep(0)


def article(body_sentence: str = "Trail running and filter coffee are recurring topics here.", repeat: int = 10,
            head: str = "") -> str:
    return (f"<html><head><title>Article</title>{head}</head><body><nav>Menu</nav><article><h1>Heading</h1>"
            f"<p>{(body_sentence + ' ') * repeat}</p></article><footer>Footer</footer></body></html>")


def run(site_or_transport, urls_or_leads, cfg: EngineConfig | None = None, resolver=None, sleep=None, conn=None):
    conn = conn or connect(":memory:")
    transport = site_or_transport if isinstance(site_or_transport, httpx.AsyncBaseTransport) else \
        httpx.MockTransport(site_or_transport)
    leads = [x if isinstance(x, Lead) else Lead(x) for x in urls_or_leads]
    r = Retriever(conn, cfg or make_cfg(), transport=transport, resolver=resolver or StaticResolver(),
                  sleep=sleep or RecordingSleep(), today=TODAY)
    run_id = asyncio.run(r.run(leads))
    return conn, run_id
