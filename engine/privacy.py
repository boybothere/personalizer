"""Write-boundary privacy filter (phase 6 corrective pass). Applied inside the functions that persist content, so
every entry point (research, search, retrieve) is covered and an interrupted run cannot leave unfiltered text.

Storage level (here): sensitive traits (D4.1), residence, and current or upcoming whereabouts (D4.2) are removed
from page text, titles and snippets before they are written; URLs whose path or query matches the same categories
are withheld (never requested, stored only as a hash); organisation or occupation labels in identity records are
withheld. Claim level (engine/claims/scope.py, plus end-of-run redaction): additionally undated travel and undated
place mentions, which are claim rules (D4.2: places kept only as dated statements) and are not removed earlier
because identity resolution needs them (for example "a pastry chef in Chennai" marks a namesake).

The detection is lexical (fixed word lists and patterns). It is not complete: sensitive content phrased in words
the lists do not contain is stored, and only the 7-day text expiry removes it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

from .claims import scope

URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>\\]+")
WITHHELD_PREFIX = "withheld:out_of_scope:"


def _today():
    return datetime.now(timezone.utc).date()


def category(text: str) -> str | None:
    return scope.classify(text, _today(), storage_only=True)


def filter_text(text: str | None) -> str | None:
    """Same-length placeholder over every storage-level out-of-scope sentence (offsets stay valid)."""
    return filter_with_signal(text)[0]


def filter_with_signal(text: str | None, subject_name: str | None = None) -> tuple[str | None, int, int]:
    """(filtered text, sentences removed, removed sentences that contained the subject's full name).

    The counts are the identity-safety signal: identity resolution needs to know that evidence naming the subject
    was removed, without the sentence, the trait or the place being kept."""
    if not text:
        return text, 0, 0
    out, counts, named = scope.redact_text_signal(text, _today(), None, storage_only=True,
                                                  subject_name=subject_name)
    return out, sum(counts.values()), named


REMOVED, REMOVED_NAMED, AFTER_IDENTITY = "privacy_removed:", "privacy_removed_named:", "privacy_redacted_after_identity"


def removal_signal(flags: list | None) -> tuple[int, int, bool]:
    """(sentences removed, of which named the subject, removal happened after identity resolution)."""
    removed = named = 0
    after = False
    for f in flags or []:
        if f.startswith(REMOVED):
            removed = int(f[len(REMOVED):])
        elif f.startswith(REMOVED_NAMED):
            named = int(f[len(REMOVED_NAMED):])
        elif f == AFTER_IDENTITY:
            after = True
    return removed, named, after


def with_removal(flags: list | None, removed: int, named: int, after_identity: bool = False) -> list:
    """Flags with the removal counters increased (counts only; no text)."""
    old_removed, old_named, old_after = removal_signal(flags)
    out = [f for f in (flags or []) if not f.startswith((REMOVED, REMOVED_NAMED)) and f != AFTER_IDENTITY]
    if old_removed + removed:
        out.append(f"{REMOVED}{old_removed + removed}")
    if old_named + named:
        out.append(f"{REMOVED_NAMED}{old_named + named}")
    if old_after or (after_identity and removed):
        out.append(AFTER_IDENTITY)
    return out


def url_category(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    words = re.sub(r"[^A-Za-z0-9]+", " ", unquote(f"{parts.path} {parts.query}")).strip()
    return category(words) if words else None


def safe_url(url: str | None) -> str | None:
    if not url or url.startswith("withheld:"):
        return url
    if url_category(url):
        return WITHHELD_PREFIX + hashlib.sha256(url.encode()).hexdigest()[:24]
    return url


def scrub_urls(text: str | None) -> str | None:
    """Withhold out-of-scope URLs embedded in free text (details, reasons, JSON)."""
    if not text:
        return text
    return URL_IN_TEXT.sub(lambda m: safe_url(m.group(0)) or "", text)


def safe_label(label: str | None) -> str | None:
    """An organisation or occupation name that is itself out of scope (for example a support group)."""
    if not label:
        return label
    return f"out_of_scope:{hashlib.sha256(label.lower().encode()).hexdigest()[:8]}" if category(label) else label


def safe_statement(text: str) -> str:
    """A reason or explanation string: withheld entirely if it carries out-of-scope content."""
    text = scrub_urls(text) or text
    cat = category(text)
    return f"[withheld: out of scope ({cat})]" if cat else text


def scrub_obj(obj: Any) -> Any:
    """URLs inside any JSON-like structure (trace data, metadata)."""
    if isinstance(obj, str):
        return scrub_urls(obj)
    if isinstance(obj, list):
        return [scrub_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub_obj(v) for k, v in obj.items()}
    return obj
