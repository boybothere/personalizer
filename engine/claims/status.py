"""Claim grouping, contradictions, flags, status and confidence (PHASE6_PLAN.md §2.5, §2.3.1).

Confidence is an ordinal label derived only from status and flags. It is not a probability, and it is kept
separate from identity confidence (the subject and document identity statuses stored next to it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .extract import EVENT_PREDICATES, PATTERN_LABELS, Candidate, DocInput

CAPS = {"date_missing", "date_conflict", "stale", "reported_not_authored", "superseded_by_newer", "provider_text"}
INFORMATIONAL = {"historical_identity", "past_tense", "single_origin"}
MODE_RANK = {"authored": 3, "quoted": 2, "reported": 1}


@dataclass
class Ev:
    cand: Candidate
    doc: DocInput
    role: str = "supports"


@dataclass
class Claim:
    claim_type: str
    predicate: str
    object_text: str
    object_key: str
    polarity: int | None
    mode: str
    attributed_to: str | None
    status: str
    confidence: str
    flags: list[str]
    reasons: list[str]
    evidence: list[Ev]
    origin_groups: int
    newest_date: str | None = None
    extra: dict = field(default_factory=dict)


def ev_date(c: Candidate, d: DocInput) -> date | None:
    """Event date written in the sentence (year or month), else the document's publication date."""
    if c.event_date:
        parts = c.event_date.split("-")
        return date(int(parts[0]), int(parts[1]), 28) if len(parts) == 2 else date(int(parts[0]), 12, 31)
    if d.published_at:
        try:
            return date.fromisoformat(d.published_at[:10])
        except ValueError:
            return None
    return None


def _evaluate(claim_type: str, supports: list[Ev], extra_flags: list[str], today: date,
              stale_days: int) -> tuple[str, str, list[str], list[str], int, str | None]:
    flags = set(extra_flags)
    reasons: list[str] = []
    groups = {e.doc.origin_group for e in supports}
    dates = [d for e in supports if (d := ev_date(e.cand, e.doc))]
    if len(groups) == 1:
        flags.add("single_origin")
    if not dates:
        flags.add("date_missing")
    elif max(dates) < today - timedelta(days=stale_days):
        flags.add("stale")
    if any(e.doc.date_conflict for e in supports):
        flags.add("date_conflict")
    if any(e.doc.identity_status == "historical" for e in supports):
        flags.add("historical_identity")
    if any(e.doc.evidence_kind == "provider_extract" for e in supports):
        flags.add("provider_text")
    if any(e.cand.past for e in supports):
        flags.add("past_tense")
    modes = {e.cand.mode for e in supports}
    if claim_type == "stated_preference" and modes == {"reported"}:
        flags.add("reported_not_authored")  # P6-Q3: a third-party report is never more than tentative
    capped = bool(flags & CAPS)
    if "contradicted" in flags:
        status = "conflicting"
        reasons.append("contradicted by another source and the dates do not order the statements")
    elif not capped and any(e.cand.mode == "authored" and e.doc.identity_status == "confirmed"
                            and ev_date(e.cand, e.doc) for e in supports):
        status = "verified"
        reasons.append("the subject's own dated statement on a confirmed page")
    elif not capped and len(groups) >= 2 and (claim_type != "stated_preference" or modes & {"authored", "quoted"}):
        status = "strongly_supported"
        reasons.append(f"{len(groups)} independent origin groups agree")
    else:
        status = "tentative"
        why = sorted(flags & (CAPS | {"single_origin"}))
        reasons.append("capped at tentative: " + (", ".join(why) if why else "insufficient corroboration"))
    if status == "verified":
        confidence = "high"
    elif status == "strongly_supported":
        confidence = "high" if not (flags - {"single_origin"}) else "medium"
    elif status == "tentative":
        confidence = "low"
    else:
        confidence = "none"
    newest = max(dates).isoformat() if dates else None
    return status, confidence, sorted(flags), reasons, len(groups), newest


def _mode_and_attribution(supports: list[Ev]) -> tuple[str, str | None]:
    mode = max((e.cand.mode for e in supports), key=lambda m: MODE_RANK[m])
    if mode == "reported":
        sites = sorted({e.doc.registrable for e in supports})
        return mode, "reported by " + ", ".join(sites) + " (not the subject's own statement)"
    return mode, None


def build(cands: list[Candidate], docs: dict[str, DocInput], today: date, stale_days: int = 730,
          pattern_min_events: int = 3, pattern_min_years: int = 2) -> list[Claim]:
    claims: list[Claim] = []
    ok = [c for c in cands if c.outcome == "ok"]
    for c in cands:
        if c.outcome != "ok":
            status = "rejected" if c.outcome == "rejected" else "insufficient_evidence"
            claims.append(Claim(c.claim_type, c.predicate, c.object_text, c.object_key, c.polarity, c.mode, None, status,
                                "none", [], [c.reason], [Ev(c, docs[c.doc_id])], 1,
                                d.isoformat() if (d := ev_date(c, docs[c.doc_id])) else None))

    # preferences: one claim per (object, polarity); opposite polarities contradict each other
    prefs: dict[str, dict[int, list[Ev]]] = {}
    for c in ok:
        if c.claim_type == "stated_preference":
            prefs.setdefault(c.object_key, {}).setdefault(c.polarity, []).append(Ev(c, docs[c.doc_id]))
    for key, sides in prefs.items():
        pos, neg = sides.get(1, []), sides.get(-1, [])
        extra = {1: [], -1: []}
        if pos and neg:
            pd = [ev_date(e.cand, e.doc) for e in pos]
            nd = [ev_date(e.cand, e.doc) for e in neg]
            if all(pd) and all(nd) and max(pd) < min(nd):
                extra[1].append("superseded_by_newer")
            elif all(pd) and all(nd) and max(nd) < min(pd):
                extra[-1].append("superseded_by_newer")
            else:
                extra[1].append("contradicted")
                extra[-1].append("contradicted")
        for pol, supports in ((1, pos), (-1, neg)):
            if not supports:
                continue
            other = neg if pol == 1 else pos
            status, conf, flags, reasons, n, newest = _evaluate("stated_preference", supports, extra[pol], today,
                                                                stale_days)
            if "superseded_by_newer" in flags:
                reasons.insert(0, "a newer statement says the opposite; kept for history")
            mode, attributed = _mode_and_attribution(supports)
            predicate = "likes" if pol == 1 else ("no_longer_likes" if all(
                e.cand.predicate == "no_longer_likes" for e in supports) else "dislikes")
            claims.append(Claim("stated_preference", predicate, supports[0].cand.object_text, key, pol, mode,
                                attributed, status, conf, flags, reasons,
                                supports + [Ev(e.cand, e.doc, "contradicts") for e in other], n, newest))

    # activities and associations: one claim per (type, predicate, object)
    grouped: dict[tuple[str, str, str], list[Ev]] = {}
    for c in ok:
        if c.claim_type in ("activity", "association"):
            grouped.setdefault((c.claim_type, c.predicate, c.object_key), []).append(Ev(c, docs[c.doc_id]))
    for (ctype, predicate, key), supports in grouped.items():
        status, conf, flags, reasons, n, newest = _evaluate(ctype, supports, [], today, stale_days)
        mode, attributed = _mode_and_attribution(supports)
        claims.append(Claim(ctype, predicate, supports[0].cand.object_text, key, None, mode, attributed, status, conf,
                            flags, reasons, supports, n, newest))

    claims.extend(_patterns(ok, docs, today, stale_days, pattern_min_events, pattern_min_years))
    return claims


def _patterns(ok: list[Candidate], docs: dict[str, DocInput], today: date, stale_days: int, min_events: int,
              min_years: int) -> list[Claim]:
    """P6-Q4: recurring activity only from >= min_events distinct, dated events in distinct origin groups."""
    classes: dict[str, list[Ev]] = {}
    for c in ok:
        if c.claim_type != "activity":
            continue
        key = c.predicate if c.predicate in EVENT_PREDICATES else f"{c.predicate}:{c.object_key}"
        classes.setdefault(key, []).append(Ev(c, docs[c.doc_id]))
    out = []
    for key, evs in classes.items():
        dated = sorted(((d, e) for e in evs if (d := ev_date(e.cand, e.doc)) and not e.doc.date_conflict),
                       key=lambda x: x[0])
        used_dates, used_groups, picked = set(), set(), []
        for d, e in dated:
            if d in used_dates or e.doc.origin_group in used_groups:
                continue  # the same event reported again, or a second report from one origin
            used_dates.add(d)
            used_groups.add(e.doc.origin_group)
            picked.append(e)
        years = {ev_date(e.cand, e.doc).year for e in picked}
        if len(picked) < min_events or len(years) < min_years:
            continue
        status, conf, flags, reasons, n, newest = _evaluate("activity_pattern", picked, [], today, stale_days)
        label = PATTERN_LABELS.get(key, key.split(":", 1)[-1] if ":" not in key else
                                   f"{key.split(':')[0]} {key.split(':', 1)[1]}")
        reasons.insert(0, f"{len(picked)} distinct dated events in {n} origin groups over {len(years)} years")
        mode, attributed = _mode_and_attribution(picked)
        out.append(Claim("activity_pattern", f"recurring:{key}",
                         f"recurring activity: {label}, not a stated preference", f"recurring:{key}", None, mode,
                         attributed, status, conf, flags, reasons, picked, n, newest))
    return out
