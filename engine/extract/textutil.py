"""Normalisation, hashing and near-duplicate fingerprints (64-bit simhash over word 3-shingles)."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿]")
_WORD = re.compile(r"\w+", re.UNICODE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def has_invisible(text: str) -> bool:
    return bool(_INVISIBLE.search(text))


def sha256(text: str) -> str:
    return hashlib.sha256(" ".join(_WORD.findall(text.lower())).encode()).hexdigest()


def simhash(text: str) -> str:
    words = _WORD.findall(text.lower())
    shingles = [" ".join(words[i : i + 3]) for i in range(max(1, len(words) - 2))] if words else [""]
    acc = [0] * 64
    for s in shingles:
        h = int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            acc[bit] += 1 if h >> bit & 1 else -1
    value = sum(1 << bit for bit in range(64) if acc[bit] > 0)
    return f"{value:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")
