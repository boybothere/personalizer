"""Out-of-scope content filter (DECISIONS D4.1-D4.3; PHASE6_PLAN.md §2.7). Lexical and deliberately over-inclusive:
a false drop costs a missed claim, a false keep would store sensitive or location data.

A sentence that matches is never turned into a claim and is redacted from stored text after the run. Only
counts per category are recorded, never the sentence or its topic.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from ..identity.evidence import PLACES
from .text import sentence_spans

_I = re.I
SENSITIVE: dict[str, list[re.Pattern]] = {
    "religion": [re.compile(
        r"\b(relig\w*|faith|god|gods|goddess|temple|church|mosque|gurudwara|gurdwara|synagogue|pray\w*|puja|pooja|"
        r"namaz|bible|quran|koran|gita|ramadan|eid|diwali|navratri|christmas|easter|holi|pongal|onam|hanukkah|"
        r"pilgrimage|spiritual\w*|atheis\w*|hindu\w*|muslim\w*|christian\w*|sikh\w*|jain\w*|buddhis\w*|jewish|"
        r"islam\w*|worship\w*|devotee|sermon)\b", _I)],
    "politics": [re.compile(
        r"\b(politic\w*|election\w*|vot(e|es|ed|ing|er|ers)|ballot|democrat\w*|republican\w*|tory|tories|"
        r"rally|rallies|campaign(ed|ing)? for|party member|left-wing|right-wing|leftist|rightist|protest\w*|"
        r"manifesto|parliament\w*|legislat\w*|activis\w*)\b", _I),
        re.compile(r"\b(BJP|INC|AAP|TMC|DMK|AIADMK|CPI|CPM|RSS)\b|\bCongress party\b|\bLabour party\b")],
    "health": [re.compile(
        r"\b(health\w*|medical|medicine|medication|diagnos\w*|illness|ill|sick\w*|disease\w*|disorder\w*|cancer|"
        r"tumou?r|diabet\w*|asthma|depress\w*|anxiety|panic attacks?|therap\w*|counsell?ing|psychiatr\w*|mental|"
        r"adhd|autis\w*|dyslexi\w*|surgery|surgeon|hospital\w*|clinic\w*|doctors?|injur\w*|recover\w*|rehab\w*|"
        r"addict\w*|sober\w*|sobriety|alcoholi\w*|pregnan\w*|ivf|miscarriage|disabilit\w*|disabled|wheelchair|"
        r"blind|deaf|hiv|covid|vaccin\w*|weight loss|dieting|insomnia|burnout|chronic|cardiac|heart attack)\b",
        _I)],
    "sexuality": [re.compile(
        r"\b(gay|lesbian|queer|bisexual|lgbt\w*|transgender|trans (woman|man)|sexual\w*|pride parade|"
        r"coming out|came out)\b", _I)],
    "caste_ethnicity": [re.compile(
        r"\b(caste\w*|brahmin\w*|dalit\w*|kshatriya|vaishya|shudra|scheduled (caste|tribe)|ethnic\w*|racial\w*|"
        r"tribal)\b", _I), re.compile(r"\bOBC\b")],
    "family": [re.compile(
        r"\b(wife|husband|spouse|sons?|daughters?|kids?|children|child|bab(y|ies)|mother|father|mom|mum|dad|"
        r"parents?|sisters?|brothers?|siblings?|grand(mother|father|ma|pa|parents?|children|kids?)|in-laws?|"
        r"aunts?|uncles?|cousins?|nieces?|nephews?|girlfriend|boyfriend|fianc[eé]e?|married|marriage|wedding|"
        r"divorce\w*|famil(y|ies))\b|\b(my|her|his|their) partner\b", _I)],
    "finance": [re.compile(
        r"\b(salary|salaries|net worth|income|loans?|debts?|mortgage|emi|savings|bankrupt\w*|credit score|"
        r"tax returns?|bank accounts?|inheritance|my portfolio|wealth)\b", _I)],
    "legal": [re.compile(
        r"\b(arrest\w*|lawsuits?|sued|court|convicted|conviction|charged with|criminal|police|bail|jail|prison|"
        r"indicted|fir)\b", _I)],
    "identity_numbers": [re.compile(r"\b(aadhaar|passport|social security|ssn|voter id|driving licen[cs]e)\b", _I),
                         re.compile(r"\bPAN\b")],
}
RESIDENCE = [re.compile(
    r"\b(i|we|she|he|they)\s+(currently\s+|now\s+)?(live|lives|lived|living|reside|resides|resided|stay|stays)\s+"
    r"(in|at|near|on)\b|\b(my|our|her|his|their)\s+(home|house|flat|apartment|neighbou?rhood|hometown|street|"
    r"address|locality|building)\b|\bback home\b|\bhome(town| town| city)\b|\b(moved|relocated|shifted)\s+"
    r"(back\s+)?to\b|\bbased (in|out of)\b|\bresident of\b|\bgrew up in\b|\bnative of\b|\bborn (in|and raised)\b",
    _I)]
WHEREABOUTS = [re.compile(
    r"\b(see you|landing|landed|arriving|arrived)\s+(in|at)\b|\b(heading|flying|driving|travell?ing|off)\s+(back\s+)?"
    r"to\b|\bon (my|her|his|their|our) way (to|back)\b|\ben route\b|\bchecked in (at|to)\b|\bcheck-in\b|"
    r"\bat the airport\b|\bcurrently (in|at|visiting|travell?ing)\b|\bright now\b|\bvacation\w*\b|"
    r"\bholiday(ing)? in\b", _I),
    re.compile(r"\b(I am|I'm|I’m|we're|we’re|we are|will be|I'll be|I’ll be)\s+(in|at|visiting)\s+[A-Z]")]
TIME_NOW = re.compile(r"\b(today|tonight|tomorrow|this (week|weekend|month|morning|evening|afternoon)|"
                      r"next (week|weekend|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
                      r"upcoming|later this (week|month)|soon)\b", _I)
# being, going or appearing somewhere: "I'll be speaking at PyCon", "flying to Pune", "visiting the Goa office"
PRESENCE = re.compile(r"\b(be|being|am|'m|’m|will be|visit\w*|go|going|fly\w*|travel\w*|head\w*|arriv\w*|"
                      r"land\w*|speak\w*|present\w*|attend\w*|join\w* us|meet\w*)\s+(?:\w+\s+){0,3}?"
                      r"(in|at|to)\s+(the\s+)?[A-Z]")
TRAVEL_PLACE = re.compile(r"\b(in|to|across|around)\s+(the\s+)?[A-Z]")
TRAVEL = re.compile(r"\b(visit(ed|ing|s)?|travell?(ed|ing|s)?|trip|tour(ed|ing)?|trek(ked|king|s)?|hik(ed|ing|es)|"
                    r"climb(ed|ing|s)?|backpack(ed|ing)?|cycled across|sail(ed|ing) to)\b", _I)
YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")
EXTRA_PLACES = {
    "goa", "kerala", "karnataka", "tamil nadu", "maharashtra", "gujarat", "rajasthan", "punjab", "kashmir", "ladakh",
    "himachal", "uttarakhand", "sikkim", "assam", "odisha", "bihar", "jaipur", "lucknow", "kochi", "cochin", "mysore",
    "mysuru", "coimbatore", "ahmedabad", "chandigarh", "indore", "bhopal", "nagpur", "surat", "vizag", "pondicherry",
    "puducherry", "shimla", "manali", "rishikesh", "varanasi", "agra", "udaipur", "ooty", "munnar", "koramangala",
    "indiranagar", "whitefield", "bandra", "andheri", "powai", "gurgaon", "gurugram", "noida", "dubai", "bali",
    "thailand", "bangkok", "sydney", "melbourne", "amsterdam", "barcelona", "madrid", "rome", "lisbon", "zurich",
    "seattle", "austin", "boston", "chicago", "los angeles", "kathmandu", "nepal", "sri lanka", "colombo",
    "maldives", "hong kong", "seoul", "beijing", "shanghai", "kilimanjaro", "everest", "himalayas", "western ghats"}


def _places(location: str | None) -> set[str]:
    return PLACES | EXTRA_PLACES | ({location.lower()} if location else set())


def _past_year(sentence: str, today: date) -> bool:
    years = [int(y) for y in YEAR.findall(sentence)]
    return bool(years) and max(years) < today.year


def classify(sentence: str, today: date, location: str | None = None) -> str | None:
    """Return the out-of-scope category of a sentence, or None if it may be processed."""
    for cat, patterns in SENSITIVE.items():
        if any(p.search(sentence) for p in patterns):
            return cat
    if any(p.search(sentence) for p in RESIDENCE):
        return "residence"
    if any(p.search(sentence) for p in WHEREABOUTS):
        return "whereabouts"
    low = sentence.lower()
    named_place = any(re.search(rf"\b{re.escape(p)}\b", low) for p in _places(location))
    if TIME_NOW.search(sentence) and (PRESENCE.search(sentence) or named_place):
        return "whereabouts"  # current or upcoming presence somewhere
    if TRAVEL.search(sentence) and (named_place or TRAVEL_PLACE.search(sentence)) and not _past_year(sentence, today):
        return "travel_undated"  # D4.2: travel kept only as a dated past statement
    if named_place and not _past_year(sentence, today):
        return "place_undated"
    return None


PLACEHOLDER = "[removed: out of scope]"


def _blank(segment: str) -> str:
    """Same-length placeholder, so stored offsets into the text stay valid."""
    if len(segment) >= len(PLACEHOLDER):
        return PLACEHOLDER + " " * (len(segment) - len(PLACEHOLDER))
    return "·" * len(segment)


def redact_text(text: str, today: date, location: str | None = None) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    out = text
    for a, b in sentence_spans(text):
        cat = classify(text[a:b], today, location)
        if cat:
            counts[cat] = counts.get(cat, 0) + 1
            out = out[:a] + _blank(out[a:b]) + out[b:]
    return out, counts


def redact_run(conn: sqlite3.Connection, run_id: str, today: date, location: str | None = None) -> dict:
    """Remove out-of-scope sentences from every stored text of a run (documents, titles, snippets). Counts only."""
    totals: dict[str, int] = {}
    docs = 0

    def add(counts):
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v

    for r in conn.execute("SELECT id, text, title FROM documents WHERE run_id=? AND (text IS NOT NULL OR title IS NOT"
                          " NULL)", (run_id,)).fetchall():
        new_text, c1 = redact_text(r["text"], today, location) if r["text"] else (None, {})
        new_title, c2 = redact_text(r["title"], today, location) if r["title"] else (None, {})
        if c1 or c2:
            docs += 1
            add(c1)
            add(c2)
            conn.execute("UPDATE documents SET text=?, title=? WHERE id=?",
                         (new_text if c1 else r["text"], new_title if c2 else r["title"], r["id"]))
    for table, cols in (("leads", ("snippet",)), ("search_results", ("snippet", "title"))):
        for r in conn.execute(f"SELECT id, {', '.join(cols)} FROM {table} WHERE run_id=?", (run_id,)).fetchall():
            updates = {}
            for col in cols:
                if r[col]:
                    new, c = redact_text(r[col], today, location)
                    if c:
                        add(c)
                        updates[col] = new
            if updates:
                conn.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
                             (*updates.values(), r["id"]))
    return {"documents_redacted": docs, "sentences_by_category": totals}
