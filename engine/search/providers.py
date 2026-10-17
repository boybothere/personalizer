"""Brave, Tavily and Exa adapters. Request shapes follow each provider's documentation (checked
2026-10-17); storage policies follow their terms (DECISIONS.md D7)."""

from __future__ import annotations

from .base import Capabilities, Hit, Pricing, Provider, Rendered, TermsPolicy, Usage, iso_date
from .spec import SearchSpec, site_domain, site_has_path


def _freshness_days_to_brave(days: int | None) -> str | None:
    if not days:
        return None
    return "pd" if days <= 1 else "pw" if days <= 7 else "pm" if days <= 31 else "py" if days <= 365 else None


def _start_date(days: int | None) -> str | None:
    if not days:
        return None
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()


class BraveProvider(Provider):
    name = "brave"
    capabilities = Capabilities(quoted_phrases=True, boolean_or=True, site_operator=True, domain_filter=False,
                                path_filter=False, freshness=True, max_results=20, returns_snippets=True,
                                extract_fallback=False)
    terms = TermsPolicy("transient", "Brave Search API ToS s3(b)(i): no storing/caching/databases of Search Results "
                                     "beyond transient storage required for operation", "POWERED BY BRAVE (s4(d))")
    pricing = Pricing("request", 1, 0.005)

    def render(self, spec: SearchSpec) -> Rendered:
        notes = []
        q = spec.text()
        if spec.sites:
            ops = [f"site:{s}" for s in spec.sites]
            q += " " + (ops[0] if len(ops) == 1 else "(" + " OR ".join(ops) + ")")
            if any(site_has_path(s) for s in spec.sites):
                notes.append("site: with a path is not documented by Brave; results are post-filtered")
        q += "".join(f" -site:{s}" for s in spec.exclude_sites)
        params = {"q": q.strip(), "count": min(spec.max_results, self.capabilities.max_results)}
        fresh = _freshness_days_to_brave(spec.freshness_days)
        if fresh:
            params["freshness"] = fresh
        elif spec.freshness_days:
            notes.append("freshness longer than a year not expressible; not applied")
        return Rendered("GET", "https://api.search.brave.com/res/v1/web/search", params=params,
                        headers={"X-Subscription-Token": self.api_key or "", "Accept": "application/json"},
                        notes=notes)

    def parse(self, data: dict, spec: SearchSpec):
        results = (data.get("web") or {}).get("results") or []
        hits = [Hit(i + 1, r["url"], r.get("title"), r.get("description"), iso_date(r.get("page_age")))
                for i, r in enumerate(results) if r.get("url")]
        return hits, Usage(1), None

    def quota_codes(self) -> set[int]:
        return {402}


class TavilyProvider(Provider):
    name = "tavily"
    capabilities = Capabilities(quoted_phrases=True, boolean_or=False, site_operator=False, domain_filter=True,
                                path_filter=False, freshness=True, max_results=20, returns_snippets=True,
                                extract_fallback=True)
    terms = TermsPolicy("persist", "Tavily ToS (2026-01-13): no storage restriction on Output; internal business "
                                   "use; AUP bans surveillance, stalking, doxxing and obtaining special-category data")
    pricing = Pricing("credit", 1, 0.008)

    def render(self, spec: SearchSpec) -> Rendered:
        notes = []
        if spec.any_of:
            notes.append("OR groups are not documented by Tavily; alternatives sent as plain terms")
        body = {"query": spec.text(boolean_or=False), "search_depth": "basic",
                "max_results": min(spec.max_results, self.capabilities.max_results),
                "include_raw_content": False, "include_answer": False, "include_usage": True,
                "include_published_date": True}
        if spec.sites:
            body["include_domains"] = sorted({site_domain(s) for s in spec.sites})
            if any(site_has_path(s) for s in spec.sites):
                notes.append("path restrictions approximated by domain filter; results are post-filtered")
        if spec.exclude_sites:
            body["exclude_domains"] = sorted({site_domain(s) for s in spec.exclude_sites})
        if spec.freshness_days:
            body["start_date"] = _start_date(spec.freshness_days)
        return Rendered("POST", "https://api.tavily.com/search", json=body,
                        headers={"Authorization": f"Bearer {self.api_key}"}, notes=notes)

    def parse(self, data: dict, spec: SearchSpec):
        hits = [Hit(i + 1, r["url"], r.get("title"), r.get("content"), iso_date(r.get("published_date")))
                for i, r in enumerate(data.get("results") or []) if r.get("url")]
        usage = data.get("usage") or {}
        credits = usage.get("credits")
        return hits, Usage(float(credits) if credits is not None else 1.0,
                           detail={"reported_credits": credits}), data.get("request_id")

    def quota_codes(self) -> set[int]:
        return {402, 432, 433}  # 432: plan limit, 433: pay-as-you-go limit

    def render_extract(self, urls: list[str]) -> Rendered:
        return Rendered("POST", "https://api.tavily.com/extract",
                        json={"urls": urls, "extract_depth": "basic", "format": "text", "include_usage": True},
                        headers={"Authorization": f"Bearer {self.api_key}"})


class ExaProvider(Provider):
    name = "exa"
    capabilities = Capabilities(quoted_phrases=False, boolean_or=False, site_operator=False, domain_filter=True,
                                path_filter=True, freshness=True, max_results=10, returns_snippets=False,
                                extract_fallback=False)  # contents would be copied output; terms s4.2(a)
    terms = TermsPolicy("transient", "Exa ToS s4.2(a): no copying/reproducing information obtained through the "
                                     "Services except browser cache or as expressly permitted (pending review)")
    pricing = Pricing("usd", 0.007, 1.0)

    def render(self, spec: SearchSpec) -> Rendered:
        notes = ["neural search: exact-phrase matching is not guaranteed"] if spec.phrases else []
        if spec.any_of:
            notes.append("OR groups sent as plain terms")
        body = {"query": spec.text(quotes=False, boolean_or=False), "type": "auto",
                "numResults": min(spec.max_results, self.capabilities.max_results)}
        if spec.sites:
            body["includeDomains"] = spec.sites  # path prefixes are documented as supported
        if spec.exclude_sites:
            body["excludeDomains"] = spec.exclude_sites
        if spec.freshness_days:
            body["startPublishedDate"] = _start_date(spec.freshness_days) + "T00:00:00.000Z"
        return Rendered("POST", "https://api.exa.ai/search", json=body, headers={"x-api-key": self.api_key or ""},
                        notes=notes)

    def parse(self, data: dict, spec: SearchSpec):
        hits = [Hit(i + 1, r["url"], r.get("title"), None, iso_date(r.get("publishedDate")))
                for i, r in enumerate(data.get("results") or []) if r.get("url")]
        cost = (data.get("costDollars") or {}).get("total")
        return hits, Usage(float(cost) if cost is not None else self.pricing.units_per_search,
                           reported_cost_usd=float(cost) if cost is not None else None,
                           detail={"costDollars": data.get("costDollars")}), data.get("requestId")


PROVIDERS = {"brave": BraveProvider, "tavily": TavilyProvider, "exa": ExaProvider}
