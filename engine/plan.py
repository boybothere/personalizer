"""Research plan: deterministic objectives and query variants built from the input.

The plan is a starting point; the controller adds follow-up variants from discoveries and decides
order and stopping from measured signals. Nothing here infers anything about the person: focus
topics produce neutral discovery queries only (no preference wording), and results are evidence
candidates, not preferences (preference extraction is phase 6).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .config import PlannerSettings
from .identity_signal import org_core
from .search.spec import SearchSpec

_STOP = set("""a an the and or of to in on for with about from by at as is are be this that it its our their
discuss discussing discussion meeting meet potential possible upcoming explore exploring intro introduction call
partnership partner opportunity regarding review plan planning talk new focused focusing focus around using""".split())
PROFILE_SITES = ["github.com", "bsky.app", "linkedin.com/in", "instagram.com", "x.com"]


@dataclass
class ResearchInput:
    name: str
    meeting_purpose: str
    organization: str | None = None
    organization_domain: str | None = None
    role: str | None = None
    location: str | None = None
    known_urls: list[str] = field(default_factory=list)
    focus_topics: list[str] = field(default_factory=list)
    organization_aliases: list[str] = field(default_factory=list)  # former names, brands (identity-rules-2)

    def __post_init__(self):
        self.name = " ".join(self.name.split())
        if len(self.name) < 2:
            raise ValueError("name is required")
        if not self.meeting_purpose.strip():
            raise ValueError("meeting_purpose is required")
        if self.organization_domain:
            self.organization_domain = self.organization_domain.lower().removeprefix("https://") \
                .removeprefix("http://").removeprefix("www.").split("/")[0]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Variant:
    spec: SearchSpec
    reason: str
    origin: str = "plan"  # plan | followup:<trigger>

    @property
    def signature(self) -> str:
        s = self.spec
        return "|".join([",".join(sorted(p.lower() for p in s.phrases)), ",".join(sorted(t.lower() for t in s.terms)),
                         ",".join(sorted(a.lower() for a in s.any_of)), ",".join(sorted(s.sites)),
                         ",".join(sorted(s.exclude_sites))])

    def summary(self) -> dict:
        return {"spec": self.spec.as_dict(), "reason": self.reason, "origin": self.origin}


@dataclass
class Objective:
    key: str
    category: str
    priority: float
    target_docs: int
    target_domains: int
    variants: list[Variant]
    created_reason: str
    discovery_only: bool = False  # coverage counts profile leads, not fetched documents
    status: str = "pending"
    status_reason: str | None = None
    queries_run: int = 0
    low_yield_streak: int = 0
    relevant_docs: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    profile_leads: set[str] = field(default_factory=set)
    used: set[str] = field(default_factory=set)  # signatures already issued

    TERMINAL = ("covered", "saturated", "exhausted", "blocked")

    @property
    def terminal(self) -> bool:
        return self.status in self.TERMINAL

    def next_variant(self) -> Variant | None:
        return next((v for v in self.variants if v.signature not in self.used), None)

    def add_variant(self, v: Variant, front: bool = True) -> bool:
        if v.signature in self.used or any(x.signature == v.signature for x in self.variants):
            return False
        self.variants.insert(0, v) if front else self.variants.append(v)
        return True

    def coverage(self) -> int:
        return len(self.profile_leads) if self.discovery_only else len(self.relevant_docs)


def keywords(text: str, n: int = 3) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}", text)
    out: list[str] = []
    for w in words:
        lw = w.lower().strip(".-")
        if lw not in _STOP and len(lw) > 1 and lw not in (x.lower() for x in out):
            out.append(w.strip(".-"))
    return out[:n]


def build_plan(inp: ResearchInput, s: PlannerSettings) -> list[Objective]:
    N, O, D, n = inp.name, inp.organization, inp.organization_domain, s.results_per_query
    role_terms = keywords(inp.role or "", 3)
    org_terms = [w for w in (org_core(O) or "").split() if len(w) > 2]

    def spec(purpose: str, **kw) -> SearchSpec:
        return SearchSpec(purpose=purpose, max_results=n, **kw)

    def obj(key, category, priority, variants, reason, docs=None, domains=None, discovery=False):
        return Objective(key, category, priority, docs or s.default_target_docs, domains or s.default_target_domains,
                         [v for v in variants if v], reason, discovery_only=discovery)

    def v(purpose: str, reason: str, **kw) -> Variant:
        return Variant(spec(purpose, **kw), reason)

    identity = [
        v("identity: name with organisation", "tie the name to the stated organisation", phrases=[N, O]) if O else
        v("identity: name with role", "no organisation given; role terms disambiguate", phrases=[N], terms=role_terms)
        if role_terms else v("identity: name only", "no organisation or role given", phrases=[N]),
        v("identity: name on official domain", "official pages are the strongest identity anchor",
          phrases=[N], sites=[D]) if D else None,
        v("identity: name with role", "role terms disambiguate people sharing the name",
          phrases=[N], terms=role_terms) if (role_terms and O) else None,
        v("identity: name with location", "location disambiguates", phrases=[N, inp.location]) if inp.location else None,
    ]
    meet = keywords(inp.meeting_purpose, 3)
    objectives = [
        obj("identity", "identity", 1.0, identity, "always first: evidence cannot be attributed without identity",
            s.identity_target_docs, s.identity_target_domains),
        obj("official_profile", "official_profile", 0.9, [
            v("official profile on org domain", "organisation pages describing the person",
              phrases=[N], sites=[D], any_of=["about", "team", "leadership", "bio"]) if D else
            v("official profile", "organisation pages describing the person",
              phrases=[N] + ([O] if O else []), any_of=["about", "team", "leadership", "bio"]),
        ], "official or self-published profile pages", 1, 1),
        obj("meeting_topic", "meeting_topic", 0.85, [
            v("meeting topic", f"public work on the meeting subject ({', '.join(meet)})", phrases=[N], terms=meet)
            if meet else None,
            v("meeting topic (broad)", "alternative wording for the meeting subject", phrases=[N], any_of=meet)
            if len(meet) > 1 else None,
        ], "relevance to the stated meeting purpose"),
        obj("professional_activity", "professional_activity", 0.8, [
            v("professional activity", "announcements, roles and projects",
              phrases=[N] + ([O] if O else []), any_of=["announces", "appointed", "joins", "launches"]),
            v("professional activity (role)", "role-specific coverage", phrases=[N], terms=role_terms + org_terms[:1])
            if role_terms else None,
        ], "recent professional context"),
        obj("appearances", "appearances", 0.7, [
            v("public appearances", "talks, podcasts and interviews", phrases=[N],
              any_of=["interview", "podcast", "keynote", "panel", "talk"]),
            v("conference appearances", "event pages list speakers with affiliations",
              phrases=[N] + ([O] if O else []), any_of=["conference", "summit", "speaker"]),
        ], "public appearances are where people speak in their own words"),
        obj("writing", "writing", 0.7, [
            v("writing", "articles and posts authored by the person", phrases=[N],
              any_of=["wrote", "author", "blog", "op-ed", "article"]),
            v("writing platforms", "common self-publishing platforms", phrases=[N], sites=["medium.com", "substack.com"]),
        ], "self-authored text is the most attributable source"),
        obj("public_profiles", "public_profiles", 0.5, [
            v(f"profile discovery: {site}", f"discover a {site} profile (lead only; access mode per PLATFORMS.md)",
              phrases=[N], sites=[site]) for site in PROFILE_SITES
        ], "discover public profiles; discovery-only platforms yield leads, never fetched content", 2, 2,
            discovery=True),
    ]
    for topic in inp.focus_topics:
        objectives.append(obj(f"focus:{topic.lower()}", "focus", 0.4, [
            v(f"focus topic: {topic}", "user-requested focus; neutral discovery query (no preference wording)",
              phrases=[N], terms=[topic]),
        ], f"focus topic requested by the user: {topic}", 1, 1))
    for o in objectives:
        if not o.variants:
            o.status, o.status_reason = "exhausted", "no query variants could be built from the input"
    return objectives
