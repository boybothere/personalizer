"""Provider-independent query specification.

A spec says *what* to find; each provider renders it in its own dialect and reports which parts it
could not express (rendering notes). Site restrictions are verified client-side on every result
(`off_target`), because no provider guarantees operator compliance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from ..urls import InvalidURL, canonicalize


@dataclass
class SearchSpec:
    purpose: str  # why this query exists (required; every query must have a reason)
    phrases: list[str] = field(default_factory=list)  # exact phrases
    terms: list[str] = field(default_factory=list)  # plain keywords
    any_of: list[str] = field(default_factory=list)  # at least one of these
    sites: list[str] = field(default_factory=list)  # "example.com" or "example.com/path"
    exclude_sites: list[str] = field(default_factory=list)
    freshness_days: int | None = None
    max_results: int = 10

    def __post_init__(self):
        if not self.purpose.strip():
            raise ValueError("SearchSpec.purpose is required")
        if not (self.phrases or self.terms or self.any_of):
            raise ValueError("SearchSpec needs at least one phrase, term or any_of entry")
        self.sites = [normalize_site(s) for s in self.sites]
        self.exclude_sites = [normalize_site(s) for s in self.exclude_sites]

    def as_dict(self) -> dict:
        return asdict(self)

    def text(self, *, quotes: bool = True, boolean_or: bool = True) -> str:
        parts = [f'"{p}"' if quotes else p for p in self.phrases] + list(self.terms)
        if self.any_of:
            items = [f'"{a}"' if quotes and " " in a else a for a in self.any_of]
            parts.append("(" + " OR ".join(items) + ")" if boolean_or else " ".join(items))
        return " ".join(parts)


def normalize_site(site: str) -> str:
    site = site.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return site.rstrip("/")


def site_domain(site: str) -> str:
    return site.split("/", 1)[0]


def site_has_path(site: str) -> bool:
    return "/" in site


def matches_site(url: str, site: str) -> bool:
    """True when url is on `site` (domain or subdomain) and under its path prefix, if any."""
    try:
        c = canonicalize(url)
    except InvalidURL:
        return False
    domain, _, path = site.partition("/")
    if not (c.domain == domain or c.domain.endswith("." + domain)):
        return False
    if not path:
        return True
    url_path = urlsplit(c.url).path.lower().strip("/")
    path = path.strip("/")
    return url_path == path or url_path.startswith(path + "/")  # "/adbar" must not match "/adbarxyz"


def off_target(url: str, spec: SearchSpec) -> bool:
    if spec.sites and not any(matches_site(url, s) for s in spec.sites):
        return True
    return any(matches_site(url, s) for s in spec.exclude_sites)
