"""Provisional identity signal, used ONLY to steer the planner (phase 3). The identity *decision* is
made by engine/identity/ (phase 5), which never treats a name-and-organisation match as proof.

It is not an identity decision: phase 5 replaces it with candidate profiles, conflict detection and
reversible decisions. It never marks anything as confirmed.

Per document:  supported = full name + (organisation | organisation domain | known URL | role terms)
               weak      = full name only
               none      = name not present
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ORG_SUFFIX = re.compile(r"\b(inc|ltd|llc|limited|pvt|private|corp|corporation|co|plc|gmbh|technologies|labs|group)\b\.?",
                         re.I)
_WORD = re.compile(r"[a-z0-9]+")
GENERIC_ROLE_WORDS = {"of", "the", "and", "head", "senior", "lead", "vp", "vice", "president", "director", "manager",
                      "chief", "officer"}


def org_core(org: str | None) -> str | None:
    if not org:
        return None
    core = " ".join(_ORG_SUFFIX.sub(" ", org.lower()).split())
    return core or None


def org_slug(org: str | None) -> str | None:
    core = org_core(org)
    slug = re.sub(r"[^a-z0-9]", "", core or "")
    return slug if len(slug) >= 4 else None


def name_pattern(name: str) -> re.Pattern[str]:
    parts = name.split()
    if len(parts) == 1:
        return re.compile(rf"\b{re.escape(parts[0])}\b", re.I)
    # allow one middle name or initial: "Priya K. Raman"
    return re.compile(rf"\b{re.escape(parts[0])}(?:\s+[A-Z][\w.]*)?\s+{re.escape(parts[-1])}\b", re.I)


@dataclass
class Assessment:
    level: str  # supported | weak | none
    signals: list[str]


def assess(*, text: str, title: str | None, url: str, domain: str, name: str, organization: str | None,
           organization_domain: str | None, role: str | None, known_urls: list[str]) -> Assessment:
    hay = f"{title or ''}\n{text or ''}"
    if not name_pattern(name).search(hay):
        return Assessment("none", ["name not found"])
    low = hay.lower()
    signals = ["full name"]
    core = org_core(organization)
    if core and core in low:
        signals.append("organisation mentioned")
    if organization_domain and (domain == organization_domain or domain.endswith("." + organization_domain)):
        signals.append("organisation domain")
    if any(k.rstrip("/").lower() in low or url.rstrip("/") == k.rstrip("/") for k in known_urls):
        signals.append("known URL")
    role_words = {w for w in _WORD.findall((role or "").lower()) if w not in GENERIC_ROLE_WORDS}
    if role_words and role_words <= set(_WORD.findall(low)):
        signals.append("role terms")
    return Assessment("supported" if len(signals) > 1 else "weak", signals)
