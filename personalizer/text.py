"""Small text helpers used across stages (normalisation, sentences, keywords)."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
# zero-width and bidi control characters are a common carrier for hidden instructions
INVISIBLE_RE = re.compile("[​-‏‪-‮⁠-⁤﻿]")

STOPWORDS = set(
    """a an the and or of to in on for with about from by at as is are was were be been being this that these
    those it its into over under our their his her they them we you your i me my discuss discussing discussion
    focus focused focusing intro call explore about
    meeting meet potential possible upcoming explore exploring partnership partner opportunity opportunities
    around regarding re new talk call intro introduction review plan planning will would can could should
    who what when where why how not no yes also than then there here which while""".split()
)

SYNONYMS = {
    "ai": ["artificial intelligence", "machine learning", "ml", "llm", "genai"],
    "infrastructure": ["infra", "data center", "datacenter", "compute", "cloud"],
    "gpu": ["gpus", "accelerator", "accelerators", "cuda", "h100", "inference"],
    "fintech": ["payments", "banking", "lending"],
    "energy": ["grid", "renewable", "solar", "battery"],
    "security": ["cybersecurity", "infosec", "zero trust"],
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    text = INVISIBLE_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_key(text: str) -> str:
    """Aggressive normalisation for substring verification."""
    return re.sub(r"[^a-z0-9]+", " ", normalize(text).lower()).strip()


def split_sentences(text: str) -> list[str]:
    """Split on line breaks first (block elements, headings), then on sentence punctuation."""
    out = []
    for line in text.splitlines():
        line = normalize(line)
        # no split after a single-letter initial ("John W. Thompson") or common abbreviations
        parts = re.split(r"(?<!\b[A-Z]\.)(?<!\b(?:Mr|Ms|Dr|St|Jr|Sr|vs)\.)(?<=[.!?])[\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9])", line)
        out += [p.strip() for p in parts if len(p.strip()) > 2]
    return out


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9+#.\-]*[a-z0-9+#]|[a-z0-9]", text.lower())


def content_words(text: str) -> set[str]:
    return {t for t in tokens(text) if t not in STOPWORDS and len(t) > 1}


def expand_terms(words: set[str]) -> set[str]:
    out = set(words)
    for w in words:
        for syn in SYNONYMS.get(w, []):
            out.update(syn.split())
            out.add(syn)
    return out


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


def registrable(domain: str) -> str:
    """Crude registrable-domain approximation (handles co.uk / co.in style suffixes)."""
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac", "gov", "edu"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domain_matches(domain: str, patterns: set[str] | list[str]) -> bool:
    return any(domain == p or domain.endswith("." + p) for p in patterns)


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0
