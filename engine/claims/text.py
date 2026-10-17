"""Sentence spans with character offsets into the original text (offsets must stay exact for provenance)."""

from __future__ import annotations

import re

_END = re.compile(r"[.!?](?:[\"'”’)\]])?(?=\s+[\"'“‘(\[]?[A-Z0-9])")
MAX_EVIDENCE_CHARS = 300


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in text.split("\n"):
        start = 0
        for m in _END.finditer(line):
            _add(spans, line, start, m.end(), offset)
            start = m.end()
        _add(spans, line, start, len(line), offset)
        offset += len(line) + 1
    return spans


def _add(spans, line, a, b, offset):
    seg = line[a:b]
    lead = len(seg) - len(seg.lstrip())
    trail = len(seg) - len(seg.rstrip())
    if seg.strip():
        spans.append((offset + a + lead, offset + b - trail))
