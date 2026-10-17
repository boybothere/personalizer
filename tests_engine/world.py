"""Offline fictional research world for planner tests: a scripted Tavily that answers by query text, and
scripted web pages. All people, organisations and domains are fictional (.example is reserved)."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
from conftest import RecordingSleep, make_cfg

from engine.fetch.client import StaticResolver
from engine.plan import ResearchInput
from engine.research_run import research_run
from engine.search.providers import TavilyProvider
from engine.store.db import connect

SUBJECT = dict(name="Priya Raman", organization="Nimbus Compute", role="VP of Infrastructure",
               meeting_purpose="Discuss an AI infrastructure partnership focused on GPU capacity")


def page(body: str, title: str = "Page", head: str = "") -> httpx.Response:
    return httpx.Response(200, html=f"<html><head><title>{title}</title>{head}</head><body><article>"
                                    f"<p>{body}</p></article></body></html>")


def para(sentence: str) -> str:
    return " ".join([sentence] * 6)


PAGES = {
    "https://nimbuscompute.example/team/priya-raman": page(para(
        "Priya Raman is VP of Infrastructure at Nimbus Compute and leads the GPU cloud platform team."),
        "Priya Raman - Nimbus Compute"),
    "https://techpress.example/2026/09/01/gpu-capacity": page(para(
        "In an interview, Priya Raman of Nimbus Compute explained how GPU capacity planning changed this year.")),
    "https://summit.example/speakers/priya-raman": page(para(
        "Speaker: Priya Raman, VP of Infrastructure at Nimbus Compute, on AI infrastructure economics.")),
    "https://chef.example/priya-raman": page(para(
        "Priya Raman is a pastry chef in Chennai who opened a bakery last spring.")),
    "https://unrelated.example/gpu-news": page(para(
        "GPU prices fell again this quarter according to market analysts and supply chain data.")),
    "https://blog.example/priya-writes": page(para(
        "Priya Raman wrote about infrastructure cost control for AI teams at Nimbus Compute."),
        head='<script type="application/ld+json">{"@type":"Person","sameAs":["https://medium.com/@priyaraman"]}</script>'),
    "https://news.example/2026/08/02/nimbus-launch": page(para(
        "Nimbus Compute announces a new GPU region; Priya Raman said capacity will double.")),
}


class World:
    """routes: list of (predicate(query_text, include_domains) -> bool, [urls]). First match wins."""

    def __init__(self, routes=None, tavily_override=None, pages=None, robots="User-agent: *\nAllow: /\n"):
        self.routes = routes or []
        self.tavily_override = tavily_override  # callable(request, n) -> Response | None
        self.pages = dict(PAGES, **(pages or {}))
        self.robots = robots
        self.queries: list[dict] = []
        self.page_requests: list[str] = []

    def tavily(self, request: httpx.Request):
        body = json.loads(request.content)
        if request.url.path == "/extract":
            return httpx.Response(200, json={"results": [], "failed_results": [], "usage": {"credits": 0}})
        self.queries.append(body)
        if self.tavily_override:
            r = self.tavily_override(request, len(self.queries))
            if r is not None:
                return r
        q, inc = body["query"], body.get("include_domains", [])
        urls = next((u for pred, u in self.routes if pred(q, inc)), [])
        return httpx.Response(200, json={"results": [{"url": u, "title": "T", "content": f"snippet {u}"} for u in urls],
                                         "usage": {"credits": 1}})

    def web(self, request: httpx.Request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        url = str(request.url)
        self.page_requests.append(url)
        route = self.pages.get(url, httpx.Response(404))
        return route(request) if callable(route) else route  # async handlers return a coroutine; httpx awaits it


def run_world(world: World, cfg=None, conn=None, **inp_overrides):
    cfg = cfg or make_cfg()
    cfg.providers.enabled = ["tavily"]
    cfg.providers.tavily_api_key = "tvly-test"
    conn = conn or connect(":memory:")
    sleep = RecordingSleep()
    client = httpx.AsyncClient(transport=httpx.MockTransport(world.tavily))
    provs = {"tavily": TavilyProvider("tvly-test", client=client, sleep=sleep, max_retries=0)}
    inp = ResearchInput(**{**SUBJECT, **inp_overrides})
    rid = asyncio.run(research_run(conn, cfg, inp, providers=provs, transport=httpx.MockTransport(world.web),
                                   resolver=StaticResolver(), sleep=sleep, today=date(2026, 10, 17)))
    return conn, rid


def decisions(conn, rid):
    return [dict(r) for r in conn.execute("SELECT * FROM planner_decisions WHERE run_id=? ORDER BY id", (rid,))]


def has(*words):
    return lambda q, inc: all(w.lower() in q.lower() for w in words)


def site(domain):
    return lambda q, inc: domain in inc
