"""Sentence spans with character offsets into the original text (offsets must stay exact for provenance).

Removed text is replaced by a same-length placeholder that is always its own sentence: it starts with "[" and a
capital letter and ends with ".]". The splitters also cut on any placeholder explicitly, so removed text can never
merge the sentences before and after it, and a pronoun after it cannot inherit an earlier subject.
"""

from __future__ import annotations

import re

_END = re.compile(r"[.!?](?:[\"'”’)\]])?(?=\s+[\"'“‘(\[]?[A-Z0-9])")
MAX_EVIDENCE_CHARS = 300
# current placeholder forms, plus the legacy form written before the boundary fix
PLACEHOLDER_RX = re.compile(r"\[Removed *\.\]|\[R *\.\]|\[removed: out of scope\] *|·+")


def placeholder(length: int) -> str:
    """Same-length replacement for removed text that is a complete sentence of its own."""
    if length >= 10:
        return "[Removed" + " " * (length - 10) + ".]"
    if length >= 4:
        return "[R" + " " * (length - 4) + ".]"
    return "·" * length


def is_placeholder(sentence: str) -> bool:
    return bool(PLACEHOLDER_RX.fullmatch(sentence.strip()))


def pieces(line: str) -> list[tuple[int, int, bool]]:
    """(start, end, is_placeholder) runs of a line: placeholders are separated from ordinary text."""
    out, pos = [], 0
    for m in PLACEHOLDER_RX.finditer(line):
        if m.start() > pos:
            out.append((pos, m.start(), False))
        out.append((m.start(), m.end(), True))
        pos = m.end()
    if pos < len(line):
        out.append((pos, len(line), False))
    return out


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in text.split("\n"):
        for a, b, hard in pieces(line):
            if hard:
                _add(spans, line, a, b, offset)
                continue
            start = a
            for m in _END.finditer(line, a, b):
                _add(spans, line, start, m.end(), offset)
                start = m.end()
            _add(spans, line, start, b, offset)
        offset += len(line) + 1
    return spans


def _add(spans, line, a, b, offset):
    seg = line[a:b]
    lead = len(seg) - len(seg.lstrip())
    trail = len(seg) - len(seg.rstrip())
    if seg.strip():
        spans.append((offset + a + lead, offset + b - trail))
