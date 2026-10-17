"""Query generation: operator-based templates, optionally extended by the LLM.

Every query, whatever its origin, passes safety.query_policy before use.
"""

from __future__ import annotations

from .models import ResearchRequest, SearchQuery
from .safety import query_policy
from .text import content_words


def meeting_keywords(purpose: str, limit: int = 4) -> list[str]:
    words = [w for w in content_words(purpose) if len(w) > 2 or w in {"ai", "ml"}]
    # keep the order they appear in the purpose text
    order = {w: purpose.lower().find(w) for w in words}
    return sorted(words, key=lambda w: order[w])[:limit]


def template_queries(req: ResearchRequest) -> list[SearchQuery]:
    name = f'"{req.name}"'
    org = f' "{req.organization}"' if req.organization else ""
    topics = " ".join(meeting_keywords(req.meeting_purpose))
    qs = [
        SearchQuery(text=f"{name}{org}", purpose="identity and current role"),
        SearchQuery(text=f"{name}{org} (interview OR podcast OR keynote OR panel)", purpose="public appearances"),
        SearchQuery(text=f"{name}{org} (conference OR summit OR speaker)", purpose="speaking engagements"),
        SearchQuery(text=f"{name} (article OR blog OR paper OR wrote)", purpose="publications"),
    ]
    if topics:
        qs.append(SearchQuery(text=f"{name} {topics}", purpose="views on meeting topics"))
    if req.organization_domain:
        qs.append(SearchQuery(text=name, include_domains=[req.organization_domain], purpose="official biography"))
    if req.organization:
        qs.append(SearchQuery(text=f'"{req.organization}" (announces OR launches OR raises)', purpose="company news"))
    return qs


def finalize(queries: list[SearchQuery], max_queries: int = 9) -> tuple[list[SearchQuery], list[str]]:
    seen, out, rejected = set(), [], []
    for q in queries:
        key = (q.text.lower(), tuple(q.include_domains))
        if key in seen:
            continue
        seen.add(key)
        reason = query_policy(q.text)
        if reason:
            rejected.append(f"query rejected ({reason}): {q.text}")
            continue
        out.append(q)
    return out[:max_queries], rejected
