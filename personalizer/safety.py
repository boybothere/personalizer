"""Guards applied at the edges of the pipeline.

* request_policy  - refuses research requests aimed at sensitive or private data
* query_policy    - stops generated search queries from becoming data-exposure dorks
* detect_injection - flags instruction-like text inside retrieved web content
* sensitive_reason - excludes special-category / private-life content
* tone_problems   - rejects suggestions that fake familiarity or sound like surveillance
"""

from __future__ import annotations

import re

from .text import normalize


class PolicyViolation(ValueError):
    pass


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


# ---------------------------------------------------------------- request policy

_PURPOSE_FORBIDDEN = _rx(
    r"\b(religio\w*|caste|politic\w*|vot(e|ing|ed)|party affiliation|sexual\w*|gay|lesbian|transgender)\b",
    r"\b(health|medical|illness|diagnos\w*|disabilit\w*|pregnan\w*|mental)\b",
    r"\b(home address|where (he|she|they) lives?|phone number|personal (email|phone)|date of birth|dob)\b",
    r"\b(aadhaar|pan card|passport|ssn|social security|bank account|salary|net worth|credit score)\b",
    r"\b(his|her|their) (spouse|wife|husband|children|kids|family)\b|\b(girlfriend|boyfriend|family members?)\b",
    r"\b(blackmail|dirt on|compromising (material|information)|personal weakness(es)?|manipulat\w*)\b",
    r"\b(password|credential|leak\w*|dox\w*)\b",
)


def request_policy(purpose: str) -> None:
    m = _PURPOSE_FORBIDDEN.search(purpose)
    if m:
        raise PolicyViolation(
            f"Meeting purpose references '{m.group(0)}', which is outside scope. This engine only "
            "prepares professional meeting context and never researches sensitive or private attributes."
        )


# ---------------------------------------------------------------- query policy

_QUERY_FORBIDDEN = _rx(
    r"\b(filetype|ext|inurl|intitle|allinurl|allintitle|cache|intext):",
    r"index of",
    r"\b(password|passwd|credential|api[_ ]?key|secret|token|confidential|internal only|leak\w*)\b",
    r"\b(home address|phone|mobile number|email address|date of birth|aadhaar|pan|passport|ssn)\b",
    r"\b(religion|caste|politic\w*|sexual\w*|diagnos\w*|illness|divorce|arrest\w*|lawsuit)\b",
)
_ALLOWED_OPERATOR = re.compile(r"\b(site|OR|AND)\b")


def query_policy(text: str) -> str | None:
    """Return a rejection reason, or None if the query is acceptable."""
    if len(text) > 300:
        return "query too long"
    m = _QUERY_FORBIDDEN.search(text)
    if m:
        return f"forbidden operator or term: {m.group(0)!r}"
    return None


# ---------------------------------------------------------------- prompt injection

_INJECTION = _rx(
    r"\bignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|directions)",
    r"\bdisregard (all |the |your )?(previous|prior|above|system)",
    r"\b(you are|act as|you're) (now )?(an? )?(ai|assistant|language model|chatgpt|claude)\b",
    r"\bsystem prompt\b",
    r"\b(new|updated|additional) instructions?\s*:",
    r"\bnote to (ai|llm|assistant|the model|language models?)\b",
    r"\b(ai|llm|assistant|model)s? (must|should) (say|state|report|include|write|recommend)",
    r"</?(system|instructions?|assistant|user|document)>",
    r"\bdo not (tell|inform|reveal to) the user\b",
    r"\bin your (report|summary|response),? (say|state|include|mention)",
)


def detect_injection(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _INJECTION.finditer(normalize(text))})


def is_injection(sentence: str) -> bool:
    return bool(_INJECTION.search(sentence))


# ---------------------------------------------------------------- sensitive content

_SENSITIVE = {
    "religion or belief": _rx(
        r"\b(religio\w*|faith|church|mosque|temple|gurdwara|synagogue|prayer|pray(s|ed)?|worship\w*|pilgrimage)\b",
        r"\b(hindu|muslim|christian|sikh|jain|buddhist|jewish|atheist|catholic|evangelical)s?\b",
        r"\b(fasting for|ramadan|navratri|lent)\b",
    ),
    "political opinion": _rx(
        r"\b(bjp|congress party|indian national congress|aam aadmi|democrat(ic party)?|republican(s| party)?|labour party|tory|tories)\b",
        r"\b(voted for|political views?|campaigned for|party member|left-wing|right-wing|liberal views|conservative views)\b",
    ),
    "health": _rx(
        r"\b(diagnos\w*|cancer|diabet\w*|depression|anxiety disorder|adhd|autis\w*|disabilit\w*|chronic (illness|pain))\b",
        r"\b(surgery|chemotherapy|rehab|therapy for|mental health (struggle|battle|condition)|pregnan\w*|miscarriage)\b",
        r"\b(suffers? from|battl(e|ed|ing) with|recover(ed|ing) from) (an? )?\w+",
    ),
    "sexual orientation or gender identity": _rx(
        r"\b(gay|lesbian|bisexual|transgender|queer|lgbt\w*|sexual orientation|came out)\b"
    ),
    "caste, race or ethnicity": _rx(r"\b(caste|dalit|brahmin|ethnicity|ethnic origin|racial)\b"),
    "family and private life": _rx(
        r"\b(his|her|their) (wife|husband|spouse|children|kids|son|daughter|parents)\b",
        r"\b(married to|divorc\w*|child custody|engaged to)\b",
    ),
    "financial or legal status": _rx(
        r"\b(salary|net worth|personal debt|personally bankrupt|loan default|arrest\w*|convict\w*)\b",
        r"\bcriminal (case|charges?|record)\b",
    ),
    "location or identifiers": _rx(
        r"\b(lives (at|in|on)|home address|residence|neighbou?rhood|phone number|personal email|date of birth|born on)\b",
        r"\b(aadhaar|pan number|passport number|ssn)\b",
    ),
}


def sensitive_reason(text: str) -> str | None:
    t = normalize(text)
    for label, rx in _SENSITIVE.items():
        if rx.search(t):
            return label
    return None


# ---------------------------------------------------------------- tone

_TONE = _rx(
    r"\bi('ve| have)? (been )?(follow(ing|ed)?|look(ed|ing)) (you|into you)( up)?\b",
    r"\b(i|we) (did|have done) (some|my|our) (research|digging|homework) on you\b",
    r"\baccording to my research\b",
    r"\b(i'?m|i am) also (a|an)?\b",
    r"\bme too\b|\blike me\b|\bwe both\b|\bas (old )?friends\b|\bremember when\b",
    r"\byour (wife|husband|spouse|kids|children|family|home|house)\b",
    r"\b(instagram|facebook|tiktok|dating)\b",
)


def tone_problems(text: str) -> str | None:
    m = _TONE.search(text)
    return f"tone: {m.group(0)!r} implies familiarity or covert research" if m else None
