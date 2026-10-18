"""Mention-level identity evidence. Deterministic text rules; no inference about the person.

For every full-name mention we look at its sentence (plus a following pronoun sentence) and record
what ties the name to an organisation, role, time and place. Organisation appearing *somewhere* on a
page is not the same as the name being tied to it: that distinction is the point of this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..claims.text import pieces
from ..identity_signal import GENERIC_ROLE_WORDS
from .orgmatch import OrgMatcher, chunks

# Words that follow "of"/"at" in titles but are functions, not organisations ("VP of Infrastructure").
FUNCTION_WORDS = {"infrastructure", "engineering", "product", "products", "sales", "marketing", "operations", "research",
                  "technology", "design", "data", "finance", "strategy", "growth", "platform", "people", "talent",
                  "security", "the", "a", "an", "our", "their", "his", "her", "this", "that", "staff", "communications"}
# Places are not employers ("a chef from Chennai"). Small deterministic list; extended by the subject's location.
PLACES = {"india", "chennai", "bengaluru", "bangalore", "mumbai", "delhi", "new delhi", "hyderabad", "pune", "kolkata",
          "london", "new york", "san francisco", "singapore", "berlin", "paris", "tokyo", "lagos", "dubai", "toronto",
          "usa", "uk", "us", "europe", "asia", "africa", "california"}
MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"}
OCCUPATIONS = re.compile(r"\b(pastry chef|chef|baker|footballer|striker|goalkeeper|cricketer|athlete|actor|actress|"
                         r"singer|musician|novelist|poet|painter|student|teacher|professor|doctor|surgeon|nurse|lawyer|"
                         r"politician|farmer|influencer|model|dancer|photographer)\b", re.I)
PAST = re.compile(r"\b(former(ly)?|previously|ex-|until (19|20)\d\d|used to|left|departed|was (a|an|the)|had been|"
                  r"earlier (in )?(her|his|their) career)\b", re.I)
HEDGE = re.compile(r"\b(some say|unverified|unconfirmed|posting as|allegedly|claims? to be|believed to be|"
                   r"may be the same|possibly the same|rumou?red to be)\b", re.I)
PRONOUN_START = re.compile(r"^(She|He|They|Her|His|Their)\b")
_ORG = r"([A-Z][\w&.\-]*(?:\s+(?:[A-Z][\w&.\-]*|&)){0,4})"
_ORG_LAZY = r"([A-Z][\w&.\-]*(?:\s+(?:[A-Z][\w&.\-]*|&)){0,4}?)"  # stops before a trailing title word
_TITLE = (r"(?:CEO|CTO|CFO|COO|CIO|CISO|VP|SVP|EVP|President|Founder|Co-founder|Chairman|Chairwoman|Director|"
          r"Head of [A-Z]\w+|Chief [A-Z]\w+ Officer|Engineer|Scientist|Partner|Chef)")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class Subject:
    name: str
    organization: str | None = None
    organization_domain: str | None = None
    role: str | None = None
    location: str | None = None
    known_urls: list[str] = field(default_factory=list)
    organization_aliases: list[str] = field(default_factory=list)

    def matcher(self) -> OrgMatcher:
        return OrgMatcher(self.organization, self.organization_aliases, self.organization_domain,
                          {self.first, self.last})

    @property
    def role_terms(self) -> set[str]:
        return {w for w in _WORD.findall((self.role or "").lower()) if w not in GENERIC_ROLE_WORDS}

    @property
    def first(self) -> str:
        return self.name.split()[0]

    @property
    def last(self) -> str:
        return self.name.split()[-1]

    def name_regex(self) -> re.Pattern[str]:
        f, l = re.escape(self.first), re.escape(self.last)
        if f == l:
            return re.compile(rf"\b{f}\b")
        # "First Last", "First M. Last", "First Middle Last", "Last, First"
        return re.compile(rf"\b{f}(?:\s+[A-Z][\w.]*)?\s+{l}\b|\b{l},\s+{f}\b")


@dataclass
class Mention:
    window: str
    target_org: bool = False
    other_orgs: list[str] = field(default_factory=list)
    partial_orgs: list[str] = field(default_factory=list)  # e.g. "Nimbus" for "Nimbus Compute": unclear
    historical: bool = False
    role_near: bool = False
    occupation: str | None = None
    hedged: bool = False
    location_near: bool = False


@dataclass
class DocEvidence:
    doc_id: str
    url: str
    domain: str
    registrable: str
    published_at: str | None
    duplicate_of: str | None
    mentions: list[Mention] = field(default_factory=list)
    official_domain: bool = False
    known_url: bool = False
    profile_links: list[str] = field(default_factory=list)
    surname_only: bool = False
    text_available: bool = True
    origin_group: str | None = None  # phase 6: independence is counted by origin group when known
    removed_named: int = 0  # out-of-scope sentences naming the subject were removed (count only; no text)
    redacted_after_identity: bool = False  # removal happened after identity had seen the full text


def _sentences(text: str) -> list[str]:
    """Placeholders for removed text stay as sentences of their own: they never merge their neighbours and they
    stop a following pronoun sentence from being attached to an earlier mention."""
    out: list[str] = []
    for line in text.splitlines():
        for a, b, hard in pieces(line):
            part = line[a:b]
            if hard:
                out.append(part.strip())
                continue
            out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])", part) if s.strip()]
    return out


def _is_org(candidate: str, subject: Subject) -> bool:
    words = candidate.lower().split()
    if not words or words[0] in FUNCTION_WORDS or words[0] in MONTHS:
        return False
    name = candidate.lower()
    places = PLACES | ({subject.location.lower()} if subject.location else set())
    return name not in places and not name.isdigit()


_ATTACHED = re.compile(r"^\s*(,|\(|-\s|–|of\b|at\b|from\b|is\b|was\b|has been\b|had been\b|joined\b|joins\b|"
                       r"works\b|worked\b|founded\b|co-founded\b|leads\b|led\b|heads\b|runs\b)", re.I)


def _orgs_tied_to_name(window: str, m: re.Match, subject: Subject) -> list[str]:
    """Organisations grammatically attached to this mention: in the appositive or predicate right after
    the name ("Name, chef at X", "Name is VP of Y at X", "Name of X", "Name joined X", "Name (X)") or a
    possessive before it ("X's Name"). Organisations in later clauses ("...spoke with Ravi of X") are not
    attached to this name."""
    after = window[m.end(): m.end() + 200]
    found: list[str] = []
    if _ATTACHED.match(after):
        # the attached segment ends at the clause boundary (second comma for an appositive)
        body = after.lstrip()
        if body.startswith(","):
            segment = "," + body[1:].split(",")[0]
        else:
            segment = re.split(r"[,;:]|\.\s|\s(?:and|but|while|where|who)\s", body)[0]
        segment = re.split(r"\.\s|;", segment)[0]
        for g in re.finditer(rf"\b(?:at|of|from|with|joined|joins)\s+{_ORG}", segment):
            found.append(g.group(1))
        g = re.match(rf"^\s*\(\s*{_ORG}\s*\)", after)
        if g:
            found.append(g.group(1))
    before = window[max(0, m.start() - 80): m.start()]
    # "X's Name", "X' CTO Name" (plural possessive), "X CTO Name" (organisation, title, name)
    g = (re.search(rf"{_ORG}['’]s?\s+(?:[\w\-]+\s+){{0,3}}$", before)
         or re.search(rf"{_ORG_LAZY}\s+{_TITLE}\s+$", before))
    if g:
        found.append(g.group(1))
    return [f.strip(" .,") for f in found if _is_org(f.strip(" .,"), subject)]


def extract(*, doc_id: str, url: str, domain: str, registrable: str, published_at: str | None,
            duplicate_of: str | None, title: str | None, text: str | None, metadata: dict | None,
            subject: Subject, origin_group: str | None = None) -> DocEvidence:
    ev = DocEvidence(doc_id, url, domain, registrable, published_at, duplicate_of, origin_group=origin_group)
    meta = metadata or {}
    ev.profile_links = sorted(set(meta.get("same_as", []) + meta.get("rel_me", [])))
    od = subject.organization_domain
    ev.official_domain = bool(od and (domain == od or domain.endswith("." + od)))
    known = [k.rstrip("/").lower() for k in subject.known_urls]
    ev.known_url = url.rstrip("/").lower() in known or any(l.rstrip("/").lower() in known for l in ev.profile_links)
    if text is None:
        ev.text_available = False
        return ev
    rx = subject.name_regex()
    matcher = subject.matcher()
    sentences = _sentences(f"{title or ''}\n{text}")
    for i, s in enumerate(sentences):
        for m in rx.finditer(s):
            window = s
            if i + 1 < len(sentences) and PRONOUN_START.match(sentences[i + 1]):
                window = f"{s} {sentences[i + 1]}"
            low = window.lower()
            mention = Mention(window=window[:300])
            orgs = _orgs_tied_to_name(window, m, subject)
            kinds = [(o, matcher.match(o) if matcher else "different") for o in orgs]
            attached_target = any(k in ("exact", "alias") for _, k in kinds)
            mention.other_orgs = [o for o, k in kinds if k == "different"]
            mention.partial_orgs = [o for o, k in kinds if k == "partial"]
            # identity-rules-2: organisations match by tokens, never by substring ("Nimbus Compute Bank" is not
            # "Nimbus Compute"). The target counts if it is attached to this name, or merely present in the
            # sentence while nothing else is attached ("Priya Raman (Saffron Bakehouse) and Arjun (Nimbus)" is
            # not evidence for Nimbus).
            present = bool(matcher) and any(matcher.is_target(c) for c in chunks(window))
            mention.target_org = attached_target or (present and not mention.other_orgs
                                                     and not mention.partial_orgs)
            mention.historical = bool(PAST.search(window))
            terms = subject.role_terms
            mention.role_near = bool(terms) and terms <= set(_WORD.findall(low))
            occ = OCCUPATIONS.search(window)
            mention.occupation = occ.group(0).lower() if occ and not mention.role_near else None
            mention.hedged = bool(HEDGE.search(window))
            mention.location_near = bool(subject.location and subject.location.lower() in low)
            ev.mentions.append(mention)
    if not ev.mentions and re.search(rf"\b{re.escape(subject.last)}\b", text):
        ev.surname_only = True
    return ev
