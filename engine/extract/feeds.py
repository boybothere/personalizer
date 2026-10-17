"""RSS/Atom -> one record per entry. Entry dates come from the feed's published field; an
`updated`-only date is recorded as a modification date, never as publication."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import feedparser
import lxml.html

from ..config import ExtractSettings
from ..failures import Code
from . import dates
from .textutil import normalize, sha256, simhash


@dataclass
class FeedEntry:
    link: str
    record: dict


@dataclass
class FeedResult:
    ok: bool
    failure_code: str | None
    feed_title: str | None = None
    entries: list[FeedEntry] = field(default_factory=list)
    total_entries: int = 0  # entries in the feed before the max_feed_entries cap


def _date(struct) -> str | None:
    if not struct:
        return None
    return datetime.fromtimestamp(calendar.timegm(struct), tz=timezone.utc).date().isoformat()


def _html_text(fragment: str) -> str:
    if not fragment:
        return ""
    try:
        return normalize(lxml.html.fromstring(fragment).text_content())
    except (ValueError, lxml.etree.ParserError):
        return normalize(fragment)


def parse_feed(body: bytes, feed_url: str, cfg: ExtractSettings, today: date) -> FeedResult:
    parsed = feedparser.parse(body)
    if not parsed.entries:
        code = Code.FEED_INVALID if parsed.bozo and not parsed.feed else Code.FEED_EMPTY
        return FeedResult(False, code.value, parsed.feed.get("title") if parsed.feed else None)
    out = FeedResult(True, None, parsed.feed.get("title"), total_entries=len(parsed.entries))
    for e in parsed.entries[: cfg.max_feed_entries]:
        link = e.get("link") or feed_url
        content = e.get("content")
        raw = content[0].get("value") if content else (e.get("summary") or "")
        text = _html_text(raw)[: cfg.max_text_chars]
        published_raw = _date(e.get("published_parsed"))
        resolution = dates.resolve([("feed:published", published_raw)] if published_raw else [], today)
        usable = len(text) >= cfg.min_record_chars
        out.entries.append(FeedEntry(link, {
            "usable": usable, "failure_code": None if usable else Code.TEXT_TOO_SHORT.value,
            "title": e.get("title"), "author": e.get("author"), "site_name": parsed.feed.get("title"),
            "language": parsed.feed.get("language"), "canonical_link": link,
            "published_at": resolution.value, "published_source": resolution.source,
            "date_candidates": resolution.as_list(), "date_conflict": False,
            "modified_at": _date(e.get("updated_parsed")) if not published_raw else None,
            "text_chars": len(text), "text_sha256": sha256(text) if text else None,
            "simhash": simhash(text) if text else None,
            # no full content in the feed: the record is a summary; the entry link is the real source
            "flags": [] if content else ["feed_summary_only"],
            "extractor": f"feedparser-{feedparser.__version__}",
            "metadata": {"feed_url": feed_url, "entry_id": e.get("id")}, "text": text or None,
        }))
    return out
