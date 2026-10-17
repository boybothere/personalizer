"""Identity resolution: is this document about *our* person?

Scored from transparent signals (name form, organisation co-mention and
proximity, official domain, role, profile link) minus conflicts (the same name
tied to a different employer or field). Findings are only kept from documents
at MEDIUM identity or above.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import IdentityAssessment, Level, ResearchRequest, SourceDocument
from .text import content_words, domain_matches, normalize

_ORG_SUFFIXES = re.compile(r"\b(inc|ltd|llc|limited|pvt|private|corp|corporation|co|plc|gmbh|technologies|labs)\b\.?", re.I)
# "of" is deliberately excluded: "VP of Infrastructure" names a function, not an employer.
_AFFILIATION = re.compile(
    r"(?:,\s*(?:the\s+)?(?:[\w\-]+\s){0,5}?(?:at|from)\s+|\b(?:works at|joined|employed by|founder of|ceo of|cto of)\s+)"
    r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})"
)
_PAST = re.compile(r"\b(former(ly)?|previously|ex-|until|left|prior to|earlier (in )?(her|his|their) career|was (a|an|the))\b", re.I)
# Doubt about *who* is meant (not about what they did): "some say this is X", "account is unverified".
_HEDGED = re.compile(r"\b(some say|unverified|unconfirmed|posting as|claims? to be|believed to be the same)\b", re.I)


def org_core(org: str) -> str:
    return " ".join(_ORG_SUFFIXES.sub(" ", org.lower()).split())


@dataclass
class Anchors:
    full_name: str
    first: str
    last: str
    org: str | None
    org_domain: str | None
    role_words: set[str]
    profile_url: str | None
    location: str | None

    @classmethod
    def from_request(cls, req: ResearchRequest) -> "Anchors":
        parts = req.name.split()
        return cls(
            full_name=req.name,
            first=parts[0],
            last=parts[-1] if len(parts) > 1 else parts[0],
            org=org_core(req.organization) if req.organization else None,
            org_domain=req.organization_domain,
            role_words=content_words(req.role or ""),
            profile_url=str(req.profile_url).rstrip("/") if req.profile_url else None,
            location=req.location,
        )

    def name_regex(self) -> re.Pattern[str]:
        # allow a middle initial or name: "Priya K. Raman", "Priya Kumari Raman"
        return re.compile(rf"\b{re.escape(self.first)}(?:\s+[A-Z][\w.]*){{0,1}}\s+{re.escape(self.last)}\b", re.I)

    def mentions(self, text: str) -> list[int]:
        return [m.start() for m in self.name_regex().finditer(text)]


def assess(doc: SourceDocument, anchors: Anchors) -> IdentityAssessment:
    text = normalize(f"{doc.title}\n{doc.text}")
    low = text.lower()
    signals: list[str] = []
    conflicts: list[str] = []
    score = 0.0

    positions = anchors.mentions(text)
    if positions:
        score += 0.35
        signals.append(f"full name mentioned {len(positions)}x")
    elif re.search(rf"\b{re.escape(anchors.last)}\b", text) and anchors.org and anchors.org in low:
        score += 0.1
        signals.append("surname only (weak)")
    else:
        return IdentityAssessment(score=0.0, level=Level.LOW, signals=["name not found"], conflicts=[])

    if anchors.mentions(doc.title):
        score += 0.15
        signals.append("page is about this name (title)")
    if anchors.org and anchors.org in low:
        score += 0.2
        signals.append("organisation mentioned")
        org_pos = [m.start() for m in re.finditer(re.escape(anchors.org), low)]
        if any(abs(o - p) < 250 for o in org_pos for p in positions):
            score += 0.15
            signals.append("organisation near name")
    if anchors.org_domain and domain_matches(doc.domain, {anchors.org_domain}):
        score += 0.25
        signals.append("official organisation domain")
    if anchors.profile_url and (anchors.profile_url in doc.text or doc.url.rstrip("/") == anchors.profile_url):
        score += 0.25
        signals.append("links to known profile")
    if anchors.role_words and len(anchors.role_words & content_words(low)) >= max(1, len(anchors.role_words) // 2):
        score += 0.1
        signals.append("role terms match")
    if anchors.location and anchors.location.lower() in low:
        score += 0.05
        signals.append("location matches")

    # Conflicts: the name attached to a different current organisation.
    if anchors.org:
        for p in positions:
            window = text[p : p + 160]
            for m in _AFFILIATION.finditer(window):
                other = org_core(m.group(1))
                if other and anchors.org not in other and other not in anchors.org and not _PAST.search(window):
                    conflicts.append(f"name linked to '{m.group(1)}'")
                    break
        if conflicts and "organisation mentioned" not in signals:
            score -= 0.4
        elif conflicts:
            score -= 0.15

    # Attribution that is itself hedged ("some say this is X, but unverified") is not identity evidence.
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n", text) if anchors.name_regex().search(s)]
    if sentences and all(_HEDGED.search(s) for s in sentences):
        score -= 0.45
        conflicts.append("name only appears in hedged or unverified attribution")

    score = max(0.0, min(1.0, score))
    level = Level.HIGH if score >= 0.65 else Level.MEDIUM if score >= 0.4 else Level.LOW
    return IdentityAssessment(score=round(score, 3), level=level, signals=signals, conflicts=sorted(set(conflicts)))
