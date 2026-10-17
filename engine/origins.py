"""Origin groups: which documents are NOT independent sources (phase 6, PHASE6_PLAN.md §2.2).

Union-find over a run's documents. When independence is uncertain the documents are merged: over-merging can
only lower corroboration, under-merging would inflate it. Every link records why it was made.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

RULE_VERSION = "origins-1"
AGENCY = re.compile(r"\((PTI|ANI|IANS|Reuters|AP|AFP|UNI)\)|\b(?:via|source:?)\s+(PTI|ANI|IANS|Reuters|AFP)\b")
WIRE_HOSTS = {"businesswire.com", "prnewswire.com", "globenewswire.com", "newswire.com", "prweb.com",
              "einpresswire.com", "accesswire.com", "prlog.org"}
WIRE_TEXT = re.compile(r"\b(PR ?Newswire|Business ?Wire|GLOBE ?NEWSWIRE|EIN Presswire|ACCESSWIRE|PRWeb)\b", re.I)
ORIGINALLY = re.compile(r"\b(?:originally|first)\s+(?:published|appeared|posted)\s+(?:on|in|at|by)\s+([^.;\n]{2,60})",
                        re.I)
QUOTE = re.compile(r"[\"“]([^\"”]{12,600})[\"”]")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class Doc:
    id: str
    url: str
    registrable: str
    text: str | None
    canonical_link: str | None = None
    duplicate_of: str | None = None
    author: str | None = None
    site_name: str | None = None
    metadata: dict = field(default_factory=dict)


def _norm_url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip().lower().split("#")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", u)


def _grams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def group(docs: list[Doc], *, passage_words: int = 12, shingle_ratio: float = 0.15, shingle_min: int = 4,
          quote_words: int = 8, ignore_words: frozenset[str] = frozenset()) -> dict[str, tuple[str, list[dict]]]:
    """Return {doc_id: (origin_group_id, reasons)}. The group id is the earliest document (input order).

    `ignore_words` (the subject's name): 5-word shingles containing them are left out of the overlap ratio, so a
    "Name, title at Organisation" phrase that independent outlets all repeat is not taken as copying. Shared
    12-word passages and quotes still count in full."""
    ids = [d.id for d in docs]
    order = {d: i for i, d in enumerate(ids)}
    parent = {d: d for d in ids}
    reasons: dict[str, list[dict]] = {d: [] for d in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def link(a, b, why):
        if a == b:
            return
        ra, rb = find(a), find(b)
        if ra != rb:
            if order[rb] < order[ra]:
                ra, rb = rb, ra
            parent[rb] = ra
        reasons[a].append({"link": why, "with": b})
        reasons[b].append({"link": why, "with": a})

    def by_key(key_of, why):
        seen: dict = {}
        for d in docs:
            for k in key_of(d):
                if k in seen:
                    link(seen[k], d.id, why)
                else:
                    seen[k] = d.id

    urls = {_norm_url(d.url): d.id for d in docs}
    for d in docs:
        if d.duplicate_of in parent:
            link(d.duplicate_of, d.id, "near_duplicate")
        for target in [d.canonical_link, *d.metadata.get("is_based_on", [])]:
            other = urls.get(_norm_url(target))
            if other and other != d.id:
                link(other, d.id, "canonical_or_based_on")
    by_key(lambda d: [c] if (c := _norm_url(d.canonical_link)) else [], "same_canonical")
    by_key(lambda d: [d.registrable] if d.registrable else [], "same_site")
    by_key(lambda d: [a] if d.author and len((a := " ".join(_WORD.findall(d.author.lower()))).split()) >= 2
           else [], "same_byline")
    by_key(lambda d: sorted({(m.group(1) or m.group(2)).upper() for m in AGENCY.finditer(d.text or "")}),
           "news_agency")
    by_key(lambda d: ["wire"] if d.registrable in WIRE_HOSTS or WIRE_TEXT.search(d.text or "") else [],
           "press_release_wire")
    # "originally published on <site>" naming another document's site
    for d in docs:
        for m in ORIGINALLY.finditer(d.text or ""):
            named = m.group(1).lower()
            for o in docs:
                label = o.registrable.split(".")[0] if o.registrable else ""
                if o.id != d.id and ((label and len(label) > 2 and label in named.replace(" ", ""))
                                     or (o.site_name and o.site_name.lower() in named)):
                    link(o.id, d.id, "originally_published")
    # text reuse: shared passages, shingle overlap, shared quotes
    words = {d.id: _WORD.findall((d.text or "").lower()) for d in docs}
    long_grams = {i: _grams(w, passage_words) for i, w in words.items()}
    shingles = {i: {g for g in _grams(w, 5) if not ignore_words & set(g)} for i, w in words.items()}
    quotes = {d.id: {" ".join(q) for m in QUOTE.finditer(d.text or "")
                     if len(q := _WORD.findall(m.group(1).lower())) >= quote_words} for d in docs}
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            if long_grams[a] & long_grams[b]:
                link(a, b, "shared_passage")
            shared = len(shingles[a] & shingles[b])
            smaller = min(len(shingles[a]), len(shingles[b]))
            if smaller and shared >= shingle_min and shared / smaller >= shingle_ratio:
                link(a, b, "shared_shingles")
            if quotes[a] & quotes[b]:
                link(a, b, "shared_quote")
    return {d: (find(d), reasons[d]) for d in ids}


def compute(conn: sqlite3.Connection, run_id: str, settings=None, ignore_words=frozenset()) -> dict[str, str]:
    """Compute and store origin groups for a run's usable documents (replacing earlier rows)."""
    rows = conn.execute("""SELECT d.id, d.source_url, d.text, d.canonical_link, d.duplicate_of, d.author, d.site_name,
                                  d.metadata_json, u.registrable_domain
                           FROM documents d JOIN urls u ON u.id = d.url_id
                           WHERE d.run_id=? AND d.usable=1 ORDER BY d.created_at, d.id""", (run_id,)).fetchall()
    docs = [Doc(r["id"], r["source_url"], r["registrable_domain"] or "", r["text"], r["canonical_link"],
                r["duplicate_of"], r["author"], r["site_name"],
                json.loads(r["metadata_json"]) if r["metadata_json"] else {}) for r in rows]
    kw = {}
    if settings is not None:
        kw = dict(passage_words=settings.shared_passage_words, shingle_ratio=settings.shared_shingle_ratio,
                  shingle_min=settings.shared_shingle_min, quote_words=settings.shared_quote_words)
    groups = group(docs, ignore_words=frozenset(w.lower() for w in ignore_words), **kw)
    conn.execute("DELETE FROM origin_groups WHERE run_id=?", (run_id,))
    for doc_id, (gid, why) in groups.items():
        conn.execute("INSERT INTO origin_groups (document_id, run_id, origin_group, reasons_json, rule_version)"
                     " VALUES (?,?,?,?,?)", (doc_id, run_id, gid, json.dumps(why), RULE_VERSION))
    return {d: g for d, (g, _) in groups.items()}


def load(conn: sqlite3.Connection, run_id: str) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT document_id, origin_group FROM origin_groups WHERE run_id=?",
                                             (run_id,))}


def ensure(conn: sqlite3.Connection, run_id: str, settings=None) -> dict[str, str]:
    return load(conn, run_id) or compute(conn, run_id, settings)
