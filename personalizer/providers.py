"""Search provider adapters. All provider-specific logic lives here.

Every provider takes a structured SearchQuery and renders operators in its own
dialect (Brave understands `site:`; Tavily and Exa take domain lists).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import httpx

from .config import Settings
from .models import SearchHit, SearchQuery
from .sources import DATA_BROKERS, RESTRICTED_PLATFORMS

EXCLUDE = sorted(RESTRICTED_PLATFORMS | DATA_BROKERS)


class SearchError(RuntimeError):
    pass


class SearchProvider(Protocol):
    name: str

    def search(self, query: SearchQuery, limit: int) -> list[SearchHit]: ...


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")[:25]).date()
    except ValueError:
        m = re.search(r"\d{4}-\d{2}-\d{2}", value)
        return date.fromisoformat(m.group(0)) if m else None


def _post(client: httpx.Client, url: str, **kw) -> dict:
    try:
        r = client.post(url, **kw)
    except httpx.HTTPError as exc:
        raise SearchError(f"network error: {type(exc).__name__}") from exc
    if r.status_code == 429:
        raise SearchError("rate limited by provider")
    if r.status_code >= 400:
        raise SearchError(f"provider returned HTTP {r.status_code}")
    return r.json()


class TavilyProvider:
    """https://docs.tavily.com - 1,000 free credits/month; basic search = 1 credit."""

    name = "tavily"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.key = api_key
        self.client = client or httpx.Client(timeout=30)

    def search(self, query: SearchQuery, limit: int) -> list[SearchHit]:
        body = {
            "query": query.text,
            "search_depth": "basic",
            "max_results": min(limit, 20),
            "include_raw_content": "text",
            "include_published_date": True,
            "exclude_domains": EXCLUDE[:150],
        }
        if query.include_domains:
            body["include_domains"] = query.include_domains
            body.pop("exclude_domains")
        data = _post(self.client, "https://api.tavily.com/search", json=body,
                     headers={"Authorization": f"Bearer {self.key}"})
        return [
            SearchHit(url=r["url"], title=r.get("title") or "", snippet=r.get("content") or "",
                      content=r.get("raw_content"), published_at=_date(r.get("published_date")),
                      provider=self.name, query=query.text)
            for r in data.get("results", [])
        ]


class ExaProvider:
    """https://exa.ai/docs - $10 free credit/month; search $7 per 1k requests."""

    name = "exa"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.key = api_key
        self.client = client or httpx.Client(timeout=30)

    def search(self, query: SearchQuery, limit: int) -> list[SearchHit]:
        body = {
            "query": query.text,
            "type": "auto",
            "numResults": min(limit, 10),
            "contents": {"text": {"maxCharacters": 20000}},
        }
        if query.include_domains:
            body["includeDomains"] = query.include_domains
        else:
            body["excludeDomains"] = EXCLUDE
        data = _post(self.client, "https://api.exa.ai/search", json=body, headers={"x-api-key": self.key})
        return [
            SearchHit(url=r["url"], title=r.get("title") or "", snippet=(r.get("text") or "")[:300],
                      content=r.get("text"), published_at=_date(r.get("publishedDate")),
                      provider=self.name, query=query.text)
            for r in data.get("results", [])
        ]


class BraveProvider:
    """https://brave.com/search/api - $5 monthly credit; storing results needs a plan with storage rights.
    Brave hits supply only URLs and transient snippets: the pipeline fetches each page itself and keeps
    excerpts from that page, never Brave's result text (see SearchHit.content=None)."""

    name = "brave"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.key = api_key
        self.client = client or httpx.Client(timeout=30)

    def search(self, query: SearchQuery, limit: int) -> list[SearchHit]:
        q = query.text
        if query.include_domains:
            q += " (" + " OR ".join(f"site:{d}" for d in query.include_domains) + ")"
        try:
            r = self.client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": q, "count": min(limit, 20), "extra_snippets": "true"},
                headers={"X-Subscription-Token": self.key, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise SearchError(f"network error: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            raise SearchError(f"provider returned HTTP {r.status_code}")
        results = r.json().get("web", {}).get("results", [])
        return [
            SearchHit(url=x["url"], title=x.get("title") or "",
                      snippet=" ".join([x.get("description") or ""] + (x.get("extra_snippets") or [])),
                      published_at=_date(x.get("page_age")), provider=self.name, query=q)
            for x in results
        ]


class WikipediaProvider:
    """MediaWiki Action API - free, keyless; good for identity of notable people only."""

    name = "wikipedia"
    API = "https://en.wikipedia.org/w/api.php"

    def __init__(self, user_agent: str, has_contact: bool = True, client: httpx.Client | None = None):
        self.has_contact = has_contact
        self.client = client or httpx.Client(timeout=20, headers={"User-Agent": user_agent})

    def search(self, query: SearchQuery, limit: int) -> list[SearchHit]:
        if not self.has_contact:
            raise SearchError("Wikimedia requires contact details in the User-Agent; set PERSONALIZER_CONTACT")
        if query.include_domains and not any("wikipedia.org" in d for d in query.include_domains):
            return []
        try:
            r = self.client.get(self.API, params={
                "action": "query", "list": "search", "srsearch": query.text, "srlimit": min(limit, 5),
                "format": "json", "formatversion": 2,
            })
            r.raise_for_status()
            titles = [s["title"] for s in r.json().get("query", {}).get("search", [])]
            if not titles:
                return []
            r = self.client.get(self.API, params={
                "action": "query", "prop": "extracts|info", "explaintext": 1, "exintro": 1, "inprop": "url",
                "titles": "|".join(titles), "format": "json", "formatversion": 2, "exlimit": len(titles),
            })
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchError(f"wikipedia error: {type(exc).__name__}") from exc
        return [
            SearchHit(url=p["fullurl"], title=p["title"], snippet=(p.get("extract") or "")[:300],
                      content=p.get("extract"), provider=self.name, query=query.text)
            for p in r.json().get("query", {}).get("pages", []) if p.get("extract")
        ]


class FixtureProvider:
    """Offline provider over a synthetic corpus, used for tests, evaluation and demos.

    Emulates a search engine: every quoted phrase must appear in the page, plus at
    least one unquoted keyword (if any), restricted to include_domains when given.
    """

    name = "fixture"

    def __init__(self, corpus: dict):
        self.pages: dict[str, dict] = corpus["pages"]

    @classmethod
    def from_file(cls, path: Path) -> "FixtureProvider":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def search(self, query: SearchQuery, limit: int) -> list[SearchHit]:
        from .fetch import html_to_text

        phrases = [p.lower() for p in re.findall(r'"([^"]+)"', query.text)]
        rest = re.sub(r'"[^"]+"', " ", query.text).replace("(", " ").replace(")", " ")
        keywords = [w.lower() for w in rest.split() if w not in {"OR", "AND"} and len(w) > 2]
        hits = []
        for url, page in self.pages.items():
            if query.include_domains and not any(d in url for d in query.include_domains):
                continue
            title, text, _ = html_to_text(page["html"])
            hay = f"{title} {text}".lower()
            if not all(p in hay for p in phrases):
                continue
            if keywords and not any(k in hay for k in keywords):
                continue
            hits.append(SearchHit(url=url, title=title or page.get("title", ""), snippet=text[:200],
                                  published_at=_date(page.get("published")), provider=self.name,
                                  query=query.text))
        return hits[:limit]


def build_providers(settings: Settings) -> list[SearchProvider]:
    out: list[SearchProvider] = []
    for name in settings.search_providers:
        if name == "tavily" and settings.tavily_api_key:
            out.append(TavilyProvider(settings.tavily_api_key))
        elif name == "exa" and settings.exa_api_key:
            out.append(ExaProvider(settings.exa_api_key))
        elif name == "brave" and settings.brave_api_key:
            out.append(BraveProvider(settings.brave_api_key))
        elif name == "wikipedia":
            out.append(WikipediaProvider(settings.user_agent, has_contact=bool(settings.contact)))
        elif name == "fixture":
            out.append(FixtureProvider.from_file(settings.fixture_path))
    return out
