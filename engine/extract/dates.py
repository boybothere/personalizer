"""Publication-date resolution with provenance. Never invents a date, never uses 'today' as a fallback.

Candidate sources in priority order:
  1. jsonld:datePublished    2. meta:<name>         3. time_element
  4. url_pattern             5. heuristic:htmldate  (trafilatura/htmldate guess; lowest trust)
Candidates that cannot be parsed to a full date, are before 1995, or are in the future are recorded
as rejected. Disagreement (> 2 days) among non-heuristic sources sets `conflict`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from dateutil import parser as dtparser

PRIORITY = ["jsonld", "meta", "time_element", "url_pattern", "heuristic"]
_SENTINEL = datetime(1, 1, 1)
_URL_DATE = re.compile(r"/((?:19|20)\d{2})[/-](0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])(?:/|-|$)")


@dataclass
class DateCandidate:
    source: str  # e.g. "jsonld:datePublished", "meta:article:published_time", "url_pattern"
    raw: str
    value: str | None = None  # ISO date when accepted
    rejected: str | None = None


@dataclass
class DateResolution:
    value: str | None
    source: str | None
    conflict: bool
    candidates: list[DateCandidate] = field(default_factory=list)

    def as_list(self) -> list[dict]:
        return [c.__dict__ for c in self.candidates]


def parse_full_date(raw: str) -> date | None:
    """Parse only if year, month and day are all present (dateutil would otherwise fill gaps)."""
    raw = (raw or "").strip()
    if not raw or not re.search(r"\d{4}", raw):
        return None
    try:
        a = dtparser.parse(raw, default=_SENTINEL.replace(month=1, day=1))
        b = dtparser.parse(raw, default=_SENTINEL.replace(year=2, month=2, day=2))
    except (ValueError, OverflowError, TypeError):
        return None
    if a.date() != b.date():  # some component came from the default -> incomplete
        return None
    return a.date()


_URL_DATE_MONTH_NAME = re.compile(r"/((?:19|20)\d{2})/(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*/"
                                  r"(0?[1-9]|[12]\d|3[01])(?:/|-|$)", re.I)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}


def url_date(url: str) -> str | None:
    m = _URL_DATE.search(url)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        m = _URL_DATE_MONTH_NAME.search(url)
        if m:
            return date(int(m.group(1)), _MONTHS[m.group(2).lower()[:3]], int(m.group(3))).isoformat()
    except ValueError:
        return None
    return None


def resolve(raw_candidates: list[tuple[str, str]], today: date) -> DateResolution:
    accepted: list[DateCandidate] = []
    out: list[DateCandidate] = []
    for source, raw in raw_candidates:
        c = DateCandidate(source, str(raw)[:80])
        d = parse_full_date(str(raw))
        if d is None:
            c.rejected = "incomplete or unparseable"
        elif d.year < 1995:
            c.rejected = "implausibly early"
        elif d > today + timedelta(days=1):
            c.rejected = "in the future"
        else:
            c.value = d.isoformat()
            accepted.append(c)
        out.append(c)
    # A heuristic guess alone is not a reliable publication date (live run: htmldate dated RFC 9309,
    # published Sept 2022, as 2020-09-17). It is kept as a candidate and only counts as corroboration.
    strong_values = {c.value for c in accepted if not c.source.startswith("heuristic")}
    for c in accepted:
        if c.source.startswith("heuristic") and not _near_any(c.value, strong_values):
            c.value, c.rejected = None, "heuristic guess without supporting source"
    accepted = [c for c in accepted if c.value]
    if not accepted:
        return DateResolution(None, None, False, out)
    rank = {p: i for i, p in enumerate(PRIORITY)}
    accepted.sort(key=lambda c: rank.get(c.source.split(":")[0], len(PRIORITY)))
    best = accepted[0]
    strong = [date.fromisoformat(v) for v in strong_values]
    conflict = bool(strong) and (max(strong) - min(strong)).days > 2
    return DateResolution(best.value, best.source, conflict, out)


def _near_any(value: str, others: set[str], days: int = 2) -> bool:
    d = date.fromisoformat(value)
    return any(abs((d - date.fromisoformat(o)).days) <= days for o in others)
