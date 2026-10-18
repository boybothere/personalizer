"""Identity resolution rules (phase 5). Deterministic and explainable; uncertainty is kept explicit.

Document statuses : confirmed > strong > possible > historical > ambiguous > rejected > insufficient
Profile statuses  : confirmed > strong > possible > duplicate_unresolved > rejected
Subject statuses  : confirmed | strongly_supported (= RESOLVED) | possible | conflicting | insufficient_evidence

A bare name-and-organisation co-occurrence is never more than `possible`. The subject is resolved only
by an official/known-URL anchor or by >= 2 strong documents on >= 2 independent domains, with no conflict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlsplit

from .evidence import DocEvidence, Subject

RULE_VERSION = "identity-rules-2"  # rules-2: token organisation matching; independence by origin group
SUPPORTING = {"confirmed", "strong"}
# How much a document status lets through (support for the subject, claim gating), lowest = most conservative.
# Not the same as the strength order in the module docstring: historical gates claims (claims/store.py) while
# possible does not; ambiguous and rejected block in different ways (conflict 2 vs. shared-profile conflict and
# staleness), so neither dominates and they rank equal.
PERMISSIVENESS = {"rejected": 0, "ambiguous": 0, "insufficient": 1, "possible": 2, "historical": 3, "strong": 4,
                  "confirmed": 5}
STALE_AFTER_DAYS = 730
PROFILE_HOSTS = {"github.com", "medium.com", "x.com", "twitter.com", "linkedin.com", "instagram.com", "bsky.app",
                 "facebook.com", "substack.com", "mastodon.social"}


@dataclass
class DocDecision:
    doc_id: str
    url: str
    registrable: str
    status: str
    reasons: list[str]
    signals: dict
    candidate: str  # "target", "namesake:<org>", "namesake:<occupation>", or "unassigned"
    decided_by: str = "rules"


@dataclass
class ProfileDecision:
    url: str
    platform: str
    handle: str
    status: str
    reasons: list[str]
    sources: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    status: str
    resolved: bool
    explanation: list[str]
    documents: list[DocDecision]
    profiles: list[ProfileDecision]
    candidates: dict[str, list[str]]  # candidate key -> doc ids
    conflicts: list[str]
    rule_version: str = RULE_VERSION


def profile_key(url: str) -> tuple[str, str] | None:
    p = urlsplit(url if "://" in url else "https://" + url)
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    host = "x.com" if host == "twitter.com" else host
    parts = [x for x in p.path.split("/") if x]
    if host.endswith(".substack.com"):
        return "substack.com", host.split(".")[0]
    if host not in PROFILE_HOSTS or not parts:
        return None
    if host == "linkedin.com":
        return (host, parts[1].lower()) if len(parts) > 1 and parts[0] == "in" else None
    if host == "bsky.app":
        return (host, parts[1].lower()) if len(parts) > 1 and parts[0] == "profile" else None
    if host == "github.com" and parts[0] in {"orgs", "topics", "search", "features", "about", "sponsors"}:
        return None
    return host, parts[0].lower().lstrip("@")


# ---------------------------------------------------------------- documents


def attribute(ev: DocEvidence, subject: Subject, confirmed_profiles: set[tuple[str, str]]) -> DocDecision:
    """Rules below, then the identity-safety signal: a page whose out-of-scope text named the subject was only
    partly seen, so it cannot support identity on its own (confirmed/strong/historical become ambiguous)."""
    dd = _attribute(ev, subject, confirmed_profiles)
    if ev.removed_named:
        dd.signals = {**dd.signals, "removed_named": ev.removed_named}
        if dd.status in ("confirmed", "strong", "historical"):
            dd.reasons = [f"{ev.removed_named} sentence(s) naming the subject were removed as out of scope; the"
                          f" remaining evidence is incomplete"] + dd.reasons
            dd.status = "ambiguous"
    return dd


def _attribute(ev: DocEvidence, subject: Subject, confirmed_profiles: set[tuple[str, str]]) -> DocDecision:
    sig = {"official_domain": ev.official_domain, "known_url": ev.known_url, "mentions": len(ev.mentions),
           "surname_only": ev.surname_only, "text_available": ev.text_available}

    def d(status, reasons, candidate="unassigned"):
        return DocDecision(ev.doc_id, ev.url, ev.registrable, status, reasons, sig, candidate)

    if not ev.text_available:
        return d("insufficient", ["document text no longer retained; cannot assess"])
    if not ev.mentions:
        return d("insufficient", ["surname only; full name absent" if ev.surname_only else "name not found"])
    firm = [m for m in ev.mentions if not m.hedged]
    if not firm:
        return d("rejected", ["attribution is hedged (e.g. 'some say', 'unverified')"], "unassigned")
    tgt_current = any(m.target_org and not m.historical for m in firm)
    tgt_hist = any(m.target_org and m.historical for m in firm)
    others_current = sorted({o for m in firm if not m.historical for o in m.other_orgs})
    partial = sorted({o for m in firm if not m.historical for o in m.partial_orgs})
    occupation = next((m.occupation for m in firm if m.occupation), None)
    role = any(m.role_near for m in firm)
    location = any(m.location_near for m in firm)
    linked = sorted({f"{k[0]}/{k[1]}" for l in ev.profile_links if (k := profile_key(l)) in confirmed_profiles})
    sig.update({"target_org_current": tgt_current, "target_org_historical": tgt_hist, "other_orgs": others_current,
                "partial_orgs": partial, "occupation": occupation, "role_near": role, "location_near": location,
                "links_confirmed_profile": linked})

    if ev.official_domain or ev.known_url:
        if others_current and not tgt_current:
            return d("ambiguous", [f"anchor page, but the name is tied to {', '.join(others_current)}"], "target")
        anchor = "official organisation domain" if ev.official_domain else "user-supplied profile URL"
        return d("confirmed", [f"full name on {anchor}"], "target")
    if tgt_current and others_current:
        return d("ambiguous", [f"name tied to the target organisation and to {', '.join(others_current)}"], "target")
    if tgt_current and occupation:
        return d("ambiguous", [f"target organisation present but the name is described as '{occupation}'"], "target")
    if tgt_current:
        extra = [x for x, ok in (("role terms", role), ("location", location),
                                 ("links a confirmed profile", bool(linked))) if ok]
        if extra:
            return d("strong", [f"name tied to the target organisation plus {', '.join(extra)}"], "target")
        return d("possible", ["name and organisation only: not sufficient on its own"], "target")
    if partial and not others_current:
        return d("ambiguous", [f"organisation name only partly matches the target: {', '.join(partial)}"],
                 "unassigned")
    if tgt_hist and not others_current:
        return d("historical", ["name tied to the target organisation in the past tense"], "target")
    if others_current:
        return d("rejected", [f"name tied to a different organisation: {', '.join(others_current)}"],
                 f"namesake:{others_current[0].lower()}")
    if occupation:
        return d("rejected", [f"name described with a different occupation: {occupation}"], f"namesake:{occupation}")
    if role:
        return d("possible", ["role terms near the name, no organisation"], "target")
    return d("insufficient", ["name without organisation, role, domain or profile anchors"])


# ---------------------------------------------------------------- profiles


def _profiles(subject: Subject, docs: list[DocDecision], evidence: dict[str, DocEvidence],
              leads: list[str]) -> list[ProfileDecision]:
    found: dict[tuple[str, str], ProfileDecision] = {}

    def add(url, status, reason, source):
        k = profile_key(url)
        if not k:
            return
        rank = PROFILE_RANK[status]
        cur = found.get(k)
        if cur is None or PROFILE_RANK[cur.status] < rank:
            found[k] = ProfileDecision(url, k[0], k[1], status, [reason], [source] + (cur.sources if cur else []))
        else:
            cur.sources.append(source)

    for u in subject.known_urls:
        add(u, "confirmed", "supplied by the user as a known profile", "input")
    strong_links: dict[tuple[str, str], set[str]] = {}
    for dd in docs:
        for link in evidence[dd.doc_id].profile_links:
            if dd.status == "confirmed":
                add(link, "confirmed", f"linked from confirmed page {dd.url}", dd.url)
            elif dd.status == "strong":
                k = profile_key(link)
                if k:
                    strong_links.setdefault(k, set()).add(evidence[dd.doc_id].origin_group or dd.registrable)
                add(link, "possible", f"linked from strong page {dd.url}", dd.url)
            elif dd.status in ("possible", "historical"):
                add(link, "possible", f"linked from {dd.status} page {dd.url}", dd.url)
            elif dd.status == "rejected":
                add(link, "rejected", f"linked only from a page about someone else ({dd.reasons[0]})", dd.url)
    for k, domains in strong_links.items():
        if len(domains) >= 2 and found[k].status != "confirmed":
            found[k].status = "strong"
            found[k].reasons.append(f"linked from strong pages in {len(domains)} independent origins")
    for u in leads:
        add(u, "possible", "search-result lead only (snippet; never verified)", "search")
    # duplicates: several live profiles on one platform -> only the single best can stand
    by_platform: dict[str, list[ProfileDecision]] = {}
    for p in found.values():
        if p.status != "rejected":
            by_platform.setdefault(p.platform, []).append(p)
    for platform, ps in by_platform.items():
        if len(ps) < 2:
            continue
        ps.sort(key=lambda p: -PROFILE_RANK[p.status])
        top_rank = PROFILE_RANK[ps[0].status]
        ties = [p for p in ps if PROFILE_RANK[p.status] == top_rank]
        losers = ps if len(ties) > 1 else ps[1:]  # a tie at the top leaves none of them standing
        for p in losers:
            p.reasons.append(f"duplicate: {len(ps)} profiles on {platform} for this subject; "
                             + ("tied at the top, none can be preferred" if len(ties) > 1 else
                                f"weaker than {ps[0].url}"))
            p.status = "duplicate_unresolved"
    return sorted(found.values(), key=lambda p: (p.platform, p.handle))


PROFILE_RANK = {"rejected": 0, "duplicate_unresolved": 1, "possible": 2, "strong": 3, "confirmed": 4}


# ---------------------------------------------------------------- subject


def resolve(subject: Subject, evidence: list[DocEvidence], *, leads: list[str] | None = None,
            overrides: dict[str, str] | None = None, today: date | None = None,
            carried: dict[str, tuple[str, str]] | None = None) -> Resolution:
    """`carried`: earlier rule decisions (status, candidate) for documents whose text has since expired. They are
    carried forward instead of collapsing to 'insufficient', so re-resolution after expiry keeps what was known."""
    today = today or date.today()
    overrides = overrides or {}
    carried = carried or {}
    by_id = {e.doc_id: e for e in evidence}
    known = {k for u in subject.known_urls if (k := profile_key(u))}
    # pass 1: documents with only user-anchored profiles; pass 2: with profiles confirmed by pass-1 anchors
    docs = [attribute(e, subject, known) for e in evidence]
    confirmed_links = known | {k for dd in docs if dd.status == "confirmed"
                               for l in by_id[dd.doc_id].profile_links if (k := profile_key(l))}
    docs = [attribute(e, subject, confirmed_links) for e in evidence]
    for dd in docs:
        if dd.doc_id in carried and not by_id[dd.doc_id].text_available:
            dd.status, dd.candidate = carried[dd.doc_id]
            dd.reasons = [f"text expired; earlier rule decision carried forward ({dd.status})"]
        elif dd.doc_id in carried and by_id[dd.doc_id].redacted_after_identity:
            # redacted after identity: keep whichever is more conservative, the earlier decision (full text) or the
            # one recomputed from the remaining text, so removed evidence cannot keep a stale strong status alive
            earlier, recomputed = carried[dd.doc_id], dd.status
            if PERMISSIVENESS.get(earlier[0], 0) <= PERMISSIVENESS.get(recomputed, 0):
                dd.status, dd.candidate = earlier
                dd.reasons = [f"redacted after identity; earlier decision kept ({dd.status}), it is not less"
                              f" conservative than the remaining text ({recomputed})"]
            else:
                dd.reasons = [f"redacted after identity; the remaining text supports only {recomputed}, so the"
                              f" earlier {earlier[0]} is not carried forward"] + dd.reasons
        if dd.doc_id in overrides:
            dd.reasons = [f"user override: {dd.status} -> {overrides[dd.doc_id]}"] + dd.reasons
            dd.status, dd.decided_by = overrides[dd.doc_id], "user"
            dd.candidate = "target" if dd.status in SUPPORTING | {"possible", "historical"} else dd.candidate
    profiles = _profiles(subject, docs, by_id, leads or [])

    explanation: list[str] = []
    conflicts: list[str] = []
    independent = [dd for dd in docs if not by_id[dd.doc_id].duplicate_of]
    syndicated = len(docs) - len(independent)
    if syndicated:
        explanation.append(f"{syndicated} syndicated/near-duplicate document(s) counted once")
    confirmed = [dd for dd in independent if dd.status == "confirmed"]
    # independence: origin group (phase 6: same site, copies, syndication, shared passages), else the domain
    strong_domains = {by_id[dd.doc_id].origin_group or dd.registrable for dd in independent if dd.status == "strong"}
    supportive = [dd for dd in independent if dd.status in ("confirmed", "strong", "possible", "historical")]

    # conflict 1: a profile we attribute to the subject also appears on a page tying the name elsewhere
    trusted = {(p.platform, p.handle) for p in profiles if p.status in ("confirmed", "strong")}
    for dd in docs:
        namesake = dd.status == "rejected" and dd.candidate.startswith("namesake:")
        # identity-safety signal: a page whose removed text named the subject may tie the name elsewhere
        removed = bool(by_id[dd.doc_id].removed_named) and dd.status not in SUPPORTING
        if namesake or removed:
            shared = [f"{k[0]}/{k[1]}" for l in by_id[dd.doc_id].profile_links if (k := profile_key(l)) in trusted]
            if shared:
                newer = _newer(by_id[dd.doc_id].published_at, [by_id[x.doc_id].published_at for x in supportive])
                where = dd.candidate.split(':', 1)[1] if namesake else \
                    "an affiliation described in text removed as out of scope"
                conflicts.append(f"{dd.url} ties the same profile ({', '.join(shared)}) to {where}"
                                 + (" and is newer: possible role change" if newer else ""))
    # conflict 2: ambiguous documents and nothing that outranks them
    ambiguous = [dd for dd in independent if dd.status == "ambiguous"]
    if ambiguous and not confirmed and len(strong_domains) < 2:
        conflicts.append(f"{len(ambiguous)} document(s) tie the name to conflicting affiliations: "
                         + "; ".join(dd.reasons[0] for dd in ambiguous[:3]))
    # staleness: all support old while newer documents tie the name elsewhere
    stale = False
    support_dates = [by_id[dd.doc_id].published_at for dd in supportive if by_id[dd.doc_id].published_at]
    other_dates = [by_id[dd.doc_id].published_at for dd in independent
                   if (dd.candidate.startswith("namesake:") or (by_id[dd.doc_id].removed_named
                                                                and dd.status not in SUPPORTING))
                   and by_id[dd.doc_id].published_at]
    if support_dates and len(support_dates) == len(supportive) and other_dates:
        newest_support = max(date.fromisoformat(x) for x in support_dates)
        if newest_support < today - timedelta(days=STALE_AFTER_DAYS) and \
                max(date.fromisoformat(x) for x in other_dates) > newest_support:
            stale = True
            explanation.append(f"all supporting evidence is older than {STALE_AFTER_DAYS // 365} years "
                               f"(newest {newest_support}) while newer pages tie the name elsewhere")

    if conflicts:
        status = "conflicting"
    elif confirmed and not stale:
        status = "confirmed"
        explanation.append(f"{len(confirmed)} confirmed anchor document(s)")
    elif len(strong_domains) >= 2 and not stale:
        status = "strongly_supported"
        explanation.append(f"strong documents in {len(strong_domains)} independent origins")
    elif supportive:
        status = "possible"
        explanation.append(f"support below threshold: {len(confirmed)} confirmed, "
                           f"{len(strong_domains)} strong origin(s), "
                           f"{sum(dd.status == 'possible' for dd in supportive)} possible, "
                           f"{sum(dd.status == 'historical' for dd in supportive)} historical")
    else:
        status = "insufficient_evidence"
        explanation.append("no document supports this person")

    candidates: dict[str, list[str]] = {}
    for dd in docs:
        candidates.setdefault(dd.candidate, []).append(dd.doc_id)
    namesakes = sorted(k for k in candidates if k.startswith("namesake:"))
    if namesakes:
        explanation.append(f"{len(namesakes)} namesake candidate(s) kept separate: "
                           + ", ".join(k.split(":", 1)[1] for k in namesakes))
    dups = [p for p in profiles if p.status == "duplicate_unresolved"]
    if dups:
        explanation.append(f"{len(dups)} duplicate profile(s) left unresolved: "
                           + ", ".join(f"{p.platform}/{p.handle}" for p in dups))
    removed_docs = [e for e in evidence if e.removed_named]
    if removed_docs:
        explanation.append(f"{len(removed_docs)} document(s) had sentences naming the subject removed as out of"
                           f" scope; their identity evidence is incomplete")
    explanation.extend(conflicts)
    return Resolution(status, status in ("confirmed", "strongly_supported"), explanation, docs, profiles,
                      candidates, conflicts)


def _newer(candidate: str | None, others: list[str | None]) -> bool:
    known = [o for o in others if o]
    return bool(candidate and known and candidate > max(known))
