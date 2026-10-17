"""Organisation matching by tokens, not substrings (phase 6, identity-rules-2).

"Nimbus Compute Bank", a bare "Nimbus" and "Nimbus Computers India" are not "Nimbus Compute". Results:

  exact     same token sequence after normalisation (case, punctuation, '&', trailing legal suffix)
  alias     equals a user-supplied alias, or the same letters without spaces ("NimbusCompute"), or the
            official-domain label ("nimbuscompute.example")
  partial   a proper subset of the target's tokens ("Nimbus"), or the target plus descriptor words only
            ("Nimbus Compute Labs", "Nimbus Compute India"): unclear, never counted for or against
  different anything else: another organisation
"""

from __future__ import annotations

import re

LEGAL = {"inc", "ltd", "llc", "llp", "limited", "pvt", "private", "corp", "corporation", "co", "plc", "gmbh", "ag",
         "sa", "bv", "pte", "pty", "company"}
# words that may extend a company name without making it a different company (unclear, not a namesake)
DESCRIPTORS = {"labs", "lab", "technologies", "technology", "tech", "group", "holdings", "systems", "solutions",
               "software", "global", "india", "international", "ventures", "studio", "studios", "digital", "cloud",
               "hq", "the"}
LEADING = {"the", "at", "in", "for", "with", "from", "of", "and", "by", "on", "to", "former", "formerly", "ex"}
TITLES = {"ceo", "cto", "cfo", "coo", "cio", "ciso", "vp", "svp", "evp", "president", "founder", "cofounder",
          "co-founder", "chairman", "chairwoman", "director", "head", "chief", "engineer", "scientist", "partner",
          "chef", "manager", "lead", "officer", "analyst", "researcher", "principal"}
_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(name: str, drop: set[str] = frozenset()) -> list[str]:
    s = re.sub(r"['’]s?\b", "", name.lower()).replace("&", " and ")
    toks = _TOKEN.findall(s)
    while toks and toks[0] in LEADING:
        toks = toks[1:]
    while toks and (toks[-1] in LEGAL or toks[-1] in TITLES or toks[-1] in drop):
        toks = toks[:-1]
    return toks


class OrgMatcher:
    def __init__(self, organization: str | None, aliases: list[str] | None = None,
                 domain: str | None = None, person_tokens: set[str] | None = None):
        self.person = {t.lower() for t in (person_tokens or set())}
        self.target = tokens(organization or "")
        self.aliases = [t for a in (aliases or []) if (t := tokens(a))]
        self.domain_label = (domain or "").split(".")[0].lower() or None

    def __bool__(self) -> bool:
        return bool(self.target)

    def match(self, candidate: str) -> str:
        c = tokens(candidate, self.person)
        t = self.target
        if not c or not t:
            return "different"
        if c == t:
            return "exact"
        joined = "".join(c)
        if any(c == a for a in self.aliases) or joined == "".join(t) or (self.domain_label and
                                                                         joined == self.domain_label):
            return "alias"
        if len(c) < len(t) and set(c) < set(t):
            return "partial"
        if c[: len(t)] == t and set(c[len(t):]) <= DESCRIPTORS:
            return "partial"
        return "different"

    def is_target(self, candidate: str) -> bool:
        return self.match(candidate) in ("exact", "alias")


_CHUNK = re.compile(r"[A-Z][\w&.\-’']*(?:\s+(?:[A-Z][\w&.\-’']*|&))*")


def chunks(text: str) -> list[str]:
    """Runs of capitalised words: candidate organisation spans for presence checks."""
    return [m.group(0) for m in _CHUNK.finditer(text)]
