"""Verification, confidence, preference tiers, relevance and ranking.

Confidence here is a transparent, rule-based ordinal score. It is NOT a calibrated
probability, and it is never taken from an LLM. Calibration is checked offline:
eval/run_eval.py and /api/feedback/stats report precision per confidence level
against labels, which is what should drive changes to these weights.
"""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher

from .models import (
    PREFERENCE_CATEGORIES,
    Category,
    Disclosure,
    Finding,
    Level,
    PreferenceTier,
    ResearchRequest,
)
from .queries import meeting_keywords
from .text import content_words, domain_of, expand_terms, jaccard, registrable

DISCLOSURE_WEIGHT = {
    Disclosure.SELF_STATED: 1.0,
    Disclosure.OBSERVED: 0.85,
    Disclosure.THIRD_PARTY: 0.5,
    Disclosure.INFERRED: 0.2,
}
CATEGORY_PRIOR = {
    Category.ROLE: 0.6, Category.EXPERTISE: 0.7, Category.PUBLICATION: 0.6, Category.SPEAKING: 0.6,
    Category.COMPANY: 0.55, Category.INDUSTRY_TOPIC: 0.65, Category.AFFILIATION: 0.4, Category.HOBBY: 0.25,
    Category.COMMUNICATION: 0.6, Category.CONNECTION: 0.4, Category.OTHER: 0.2,
}
TIME_SENSITIVE = {Category.ROLE, Category.COMPANY, Category.AFFILIATION}
_PAST = re.compile(r"\b(former(ly)?|previously|until|ex-|left|was (a|an|the))\b", re.I)
_AT_ORG = re.compile(r"\b(?:at|joined)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})")
_TITLE = re.compile(
    r"\b(ceo|cto|cfo|coo|chief [a-z]+ officer|(?:vp|vice president|head|director) of [a-z]+(?: [a-z]+)?)\b", re.I)


def _norm_title(t: str) -> str:
    t = re.sub(r"\bvice president\b", "vp", t.lower())
    t = {"chief technology officer": "cto", "chief executive officer": "ceo", "chief financial officer": "cfo",
         "chief operating officer": "coo"}.get(t, t)
    return re.sub(r"\s+(at|in|for)$", "", t)


def freshness(published: date | None, today: date) -> float:
    if published is None:
        return 0.5
    age_years = (today - published).days / 365.25
    if age_years <= 1:
        return 1.0
    if age_years <= 3:
        return 0.7
    if age_years <= 5:
        return 0.4
    return 0.2


def _signature(f: Finding) -> set[str]:
    return content_words(f.claim) | content_words(f.excerpt)


def corroborate(findings: list[Finding]) -> None:
    """Count independent domains that support a similar claim. Near-identical excerpts on different
    domains are treated as syndication (one source), not corroboration."""
    sigs = [_signature(f) for f in findings]
    for i, f in enumerate(findings):
        support: dict[str, str] = {registrable(domain_of(f.source_url)): f.source_url}
        for j, g in enumerate(findings):
            if i == j or g.category != f.category:
                continue
            if jaccard(sigs[i], sigs[j]) < 0.35:
                continue
            if SequenceMatcher(None, f.excerpt.lower(), g.excerpt.lower()).ratio() > 0.9:
                continue  # syndicated copy
            support.setdefault(registrable(domain_of(g.source_url)), g.source_url)
        f.corroborating_urls = [u for d, u in support.items() if u != f.source_url]


def detect_role_conflicts(findings: list[Finding], org: str | None) -> list[str]:
    """Current-role claims that name different organisations, or that are stale, are downgraded."""
    warnings = []
    current = [f for f in findings if f.category == Category.ROLE and not _PAST.search(f.excerpt)]
    orgs: dict[str, list[Finding]] = {}
    for f in current:
        for m in _AT_ORG.finditer(f.excerpt):
            orgs.setdefault(m.group(1).lower(), []).append(f)
    if org:
        others = {k: v for k, v in orgs.items() if org.lower() not in k and k not in org.lower()}
        if others and any(org.lower() in k or k in org.lower() for k in orgs):
            newest = max((f.published_at for f in current if f.published_at), default=None)
            for k, fs in others.items():
                for f in fs:
                    f.downgrade_reasons.append(f"conflicts with other sources on current employer ('{k}')")
                    if newest and f.published_at and f.published_at < newest:
                        f.downgrade_reasons.append("older than conflicting source")
            warnings.append("Sources disagree about the current organisation; older/conflicting role claims were downgraded.")
    titles = {}
    for f in current:
        t = _TITLE.search(f.excerpt)
        if t:
            titles.setdefault(_norm_title(t.group(1)), []).append(f)
    if len(titles) > 1:
        warnings.append("Sources give different job titles: " + "; ".join(sorted(titles)) + ". Confirm before the meeting.")
        newest_title = max(titles, key=lambda k: max((f.published_at or date.min) for f in titles[k]))
        for k, fs in titles.items():
            if k != newest_title:
                for f in fs:
                    f.downgrade_reasons.append(f"job title '{k}' contradicted by a newer source")
    return warnings


def score(f: Finding, today: date) -> None:
    f.freshness = freshness(f.published_at, today)
    rel = {Level.HIGH: 1.0, Level.MEDIUM: 0.7, Level.LOW: 0.4}[f.source_reliability]
    corr = min(1.0, 0.5 + 0.25 * len(f.corroborating_urls))
    s = (0.35 * f.identity_score + 0.25 * rel + 0.15 * corr + 0.1 * f.freshness
         + 0.15 * DISCLOSURE_WEIGHT[f.disclosure])
    if f.category in TIME_SENSITIVE and f.freshness <= 0.4:
        f.downgrade_reasons.append("may be outdated (time-sensitive claim from an old source)")
    s -= 0.12 * len([r for r in f.downgrade_reasons if "conflict" in r or "contradicted" in r or "outdated" in r])
    f.confidence_score = round(max(0.0, min(1.0, s)), 3)
    f.confidence = Level.HIGH if f.confidence_score >= 0.75 else Level.MEDIUM if f.confidence_score >= 0.55 else Level.LOW
    n = len(f.corroborating_urls)
    if n >= 1 and f.identity_confidence == Level.HIGH and f.source_reliability != Level.LOW:
        f.evidence_strength = Level.HIGH
    elif n >= 1 or f.source_reliability == Level.HIGH:
        f.evidence_strength = Level.MEDIUM
    else:
        f.evidence_strength = Level.LOW


def preference_tiers(findings: list[Finding]) -> None:
    for f in findings:
        if f.category not in PREFERENCE_CATEGORIES:
            f.preference_tier = PreferenceTier.NOT_APPLICABLE
        elif f.disclosure == Disclosure.SELF_STATED:
            f.preference_tier = PreferenceTier.EXPLICIT
        elif len(f.corroborating_urls) >= 1 and f.disclosure != Disclosure.INFERRED:
            f.preference_tier = PreferenceTier.REPEATED
        else:
            f.preference_tier = PreferenceTier.HYPOTHESIS


def relevance(f: Finding, req: ResearchRequest) -> None:
    topic = expand_terms(set(meeting_keywords(req.meeting_purpose, limit=8)))
    words = content_words(f.claim + " " + f.excerpt)
    matched = sorted(topic & words)
    overlap = min(1.0, len(matched) / 2)
    f.relevance = round(0.55 * overlap + 0.45 * CATEGORY_PRIOR[f.category], 3)
    f.relevance_reason = (
        f"mentions meeting topics: {', '.join(matched)}" if matched
        else f"general {f.category.value.replace('_', ' ')} context; no direct meeting-topic overlap"
    )


def rank(f: Finding) -> None:
    rel = {Level.HIGH: 1.0, Level.MEDIUM: 0.7, Level.LOW: 0.4}[f.source_reliability]
    f.rank_score = round(0.4 * f.relevance + 0.35 * f.confidence_score + 0.15 * f.freshness + 0.1 * rel, 3)


def suitability(f: Finding, req: ResearchRequest) -> None:
    if f.category in req.excluded_categories:
        f.exclusion_reason = "category excluded by user"
    elif f.category == Category.HOBBY and not req.allow_personal_interests:
        f.exclusion_reason = "personal interests disabled for this request"
    elif f.category in TIME_SENSITIVE and any(
        k in r for r in f.downgrade_reasons for k in ("outdated", "contradicted", "conflicts with")
    ):
        f.exclusion_reason = "outdated or contradicted; confirm before relying on it"
    elif f.identity_confidence == Level.LOW:
        f.exclusion_reason = "identity match too weak"
    elif f.confidence == Level.LOW:
        f.exclusion_reason = "low confidence; listed for review only"
    elif f.category == Category.HOBBY and f.preference_tier == PreferenceTier.HYPOTHESIS:
        f.exclusion_reason = "personal interest not self-disclosed or corroborated; needs confirmation"
    elif f.category == Category.OTHER:
        f.exclusion_reason = "not useful for meeting preparation"
    f.suitable_for_personalization = f.exclusion_reason is None
