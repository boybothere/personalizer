"""Input screening for research runs (privacy boundaries, DECISIONS.md D4).

The engine refuses research purposes or focus topics aimed at special-category or private-life data.
This screens *inputs*; it does not classify findings (that is phase 6/7).
"""

from __future__ import annotations

import re


class PolicyViolation(ValueError):
    pass


_SENSITIVE = re.compile(
    r"\b(religio\w*|faith|caste|ethnic\w*|racial|politic\w*|vot(e|es|ed|ing)|party affiliation|sexual\w*|gay|"
    r"lesbian|transgender|health|medical|illness|diagnos\w*|disabilit\w*|pregnan\w*|mental|"
    r"home address|where (he|she|they) lives?|lives? at|whereabouts|current location|phone number|personal email|"
    r"date of birth|dob|aadhaar|pan card|passport|salary|net worth|debts?|credit score|bank account|"
    r"spouse|wife|husband|children|kids|girlfriend|boyfriend|family members?|relatives|"
    r"arrest\w*|criminal|lawsuit|dirt on|blackmail|leverage over|weakness(es)?)\b",
    re.I,
)


def screen(purpose: str, focus_topics: list[str]) -> None:
    m = _SENSITIVE.search(purpose or "")
    if m:
        raise PolicyViolation(f"meeting purpose references '{m.group(0)}': outside scope (special-category or "
                              f"private-life data is never researched)")
    for topic in focus_topics:
        if len(topic) > 40:
            raise PolicyViolation(f"focus topic too long: {topic[:40]!r}...")
        m = _SENSITIVE.search(topic)
        if m:
            raise PolicyViolation(f"focus topic '{topic}' is outside scope ('{m.group(0)}')")
