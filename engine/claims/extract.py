"""Sentence-level claim candidates (PHASE6_PLAN.md §2.3). Surface grammar only: who the grammatical subject is,
first-person verbs, negators, modals, question marks, quotation marks and a fixed list of sarcasm markers.
Nothing here understands meaning; unmarked sarcasm, irony and implied preferences are read literally or missed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..identity.evidence import Subject
from . import scope
from .text import MAX_EVIDENCE_CHARS, sentence_spans

RULES = "claims-rules-1"
_ADV = r"(?:\s+(?:really|also|still|truly|absolutely|genuinely|just|now|often|regularly|recently|once|always|has|have|had))*"
_APPOS = r"(?:\s*,[^,]{1,100},)?"
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December|"
           "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
EVENT_DATE = re.compile(rf"\b(?:({_MONTHS})\.?\s+)?(19[5-9]\d|20\d\d)\b")
_MONTH_NUM = {m[:3].lower(): i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
                                                      "oct", "nov", "dec"], 1)}

SARCASM = re.compile(r"(/s\b|\byeah,? right\b|\bsaid no one ever\b|🙄|😒|\bnot!\s*$|\boh,? (great|joy|sure)\b|"
                     r"\bas if\b|[\"“‘'](love|enjoy|like|adore)[\"”’'])", re.I)
REPOST = re.compile(r"(\bRT @|\brepost(ed)?\b|\bvia @|\bshared from\b|\bretweet)", re.I)
QUOTED_OTHER = re.compile(r"\b(as|like) (my|our|a|the|one) [\w\- ]{1,30}?\b(says|said|puts it|likes to say|used to say|"
                          r"told me)\b", re.I)
HYPOTHETICAL = re.compile(r"\b(I'd|I’d|I would|I'll|I’ll|I will|I might|I may|I could|I want to|I wanted to|I hope to|"
                          r"I wish|if I|if she|if he|if they|would|wouldn't|wouldn’t|might|someday|one day|"
                          r"want(s)? to try|hope(s)? to)\b", re.I)
DOUBLE_NEG = re.compile(r"\b(I|she|he|they)\s+(don't|don’t|do not|doesn't|doesn’t|does not)\s+(hate|dislike|mind)\b|"
                        r"\bnot that (I|she|he|they)\b|\bwouldn't say\b", re.I)
VAGUE_OBJECT = {"it", "this", "that", "them", "these", "those", "everything", "something", "anything", "how", "what",
                "where", "why", "who", "one", "him", "her", "think", "say", "believe", "know", "see"}
DETERMINERS = {"the", "a", "an", "my", "our", "all", "any", "some", "to", "her", "his", "their"}
_OBJ_END = re.compile(r"[,.;:!?()\"“”/]|\s(?:and|but|because|since|when|while|which|although|though|"
                      r"so|with my|whenever|every|on weekends|these days)\b|"
                      rf"\s(?:in|on|during|back in)\s+(?:(?:{_MONTHS})\.?\s+)?(?:19|20)\d\d\b")
TRAILING_FILLER = re.compile(r"(?:\s+(?:too|as well|also|again|for years|for a long time|these days|now|lately|"
                             r"anymore|any more|a lot|so much|very much|last spring|last year|this year))+$", re.I)
RUN_WORDS = re.compile(r"\b(marathon|half[- ]marathon|10k|5k|21k|42k|ultra\w*|race|trail run|triathlon|ironman|"
                       r"duathlon)\b", re.I)

# preference verbs: (regex, polarity, predicate)
PREF_FIRST = [
    (r"(?:hate|dislike|detest|can't stand|can’t stand|cannot stand|(?:don't|don’t|do not)(?:\s+really)?\s+(?:like|enjoy|love)|"
     r"never\s+(?:liked|enjoyed))", -1, "dislikes"),
    (r"(?:used to (?:love|like|enjoy)|gave up|no longer (?:like|enjoy|love)|stopped (?:liking|enjoying))", -1,
     "no_longer_likes"),
    (r"(?:love|enjoy|adore|like|prefer)", 1, "likes"),
]
PREF_THIRD = [
    (r"(?:hates|dislikes|detests|can't stand|can’t stand|cannot stand|(?:doesn't|doesn’t|does not)(?:\s+really)?\s+"
     r"(?:like|enjoy|love)|never\s+(?:liked|enjoyed))", -1, "dislikes"),
    (r"(?:used to (?:love|like|enjoy)|no longer (?:likes|enjoys|loves)|stopped (?:liking|enjoying))", -1,
     "no_longer_likes"),
    (r"(?:loves|enjoys|adores|likes|prefers)", 1, "likes"),
]
FAN_FIRST = r"(?:I am|I'm|I’m)\s+(?P<neg>not\s+)?(?:a\s+)?(?:huge\s+|big\s+|massive\s+)?(?:fan of|passionate about|obsessed with)"
FAN_THIRD = r"is\s+(?P<neg>not\s+)?(?:a\s+)?(?:huge\s+|big\s+|massive\s+)?(?:fan of|passionate about|obsessed with)"
FAVOURITE = re.compile(r"\b[Mm]y favou?rite\s+[\w\-]+(?:\s+[\w\-]+)?\s+(?:is|are|has to be)\s+(?P<obj>.+)")

# activities: (verb regex, predicate, preposition regex or None)
ACTIVITIES = [
    (r"(?:ran|run|runs|completed|finished)", "ran", None),
    (r"(?:spoke|speak|speaks|presented|present|presents|gave (?:a |the )?(?:talk|keynote|presentation|lecture)|"
     r"delivered (?:a |the )?(?:talk|keynote|lecture)|keynoted|was (?:a )?(?:speaker|panellist|panelist))",
     "spoke_at", r"(?:at|on|during)"),
    (r"(?:wrote|write|writes|published|publish|publishes|blogged|blog|blogs)(?:\s+(?:a|an)\s+(?:book|post|article|"
     r"essay|piece|paper|thread))?", "wrote_about", r"(?:about|on)"),
    (r"(?:attended|attend|attends)", "attended", None),
    (r"(?:organi[sz]ed|organi[sz]e|organi[sz]es|hosted|host|hosts|co-organi[sz]ed|co-hosted)", "organised", None),
    (r"(?:mentored|mentor|mentors|taught|teach|teaches|judged|judge|judges)", "mentored_or_taught", r"(?:at|for)?"),
    (r"(?:plays|played|play|paints|painted|paint|cooks|cooked|cook|bakes|baked|bake|swims|swam|swim|cycles|cycled|"
     r"cycle|climbs|climbed|climb|surfs|surfed|surf)", "hobby", None),
    (r"(?:built|build|builds|launched|launch|founded|co-founded|created|shipped)", "built", None),
    (r"(?:won|received)", "won", None),
]
EVENT_PREDICATES = {"ran", "spoke_at", "attended", "organised", "mentored_or_taught"}
PATTERN_LABELS = {"ran": "taking part in running events", "spoke_at": "speaking at events",
                  "attended": "attending events", "organised": "organising events",
                  "mentored_or_taught": "mentoring or teaching"}
ASSOC_ROLE = re.compile(r"(?P<tense>is|was|has been|serves as|served as|became|joined as|am|'m|’m)?"
                        r"(?:\s+(?:also|still|now|currently|already|long|formerly))*\s*(?:a |an |the )?"
                        r"(?:[\w\-]+\s+)?(?P<role>member|fellow|trustee|volunteer|mentor|advisor|adviser|ambassador|"
                        r"patron|sponsor)\s+(?:of|at|with|for)\s+(?P<obj>.+)", re.I)
ASSOC_BOARD = re.compile(r"(?P<tense>sits on|sat on|joined|is on|was on|serves on|served on|am on|'m on|’m on)\s+"
                         r"the\s+(?:advisory\s+)?board of\s+(?P<obj>.+)", re.I)
ASSOC_VOLUNTEER = re.compile(r"(?P<tense>volunteers|volunteered|volunteer)\s+(?:with|at|for)\s+(?P<obj>.+)", re.I)
QUOTE = re.compile(r"[\"“]([^\"”]{3,600})[\"”]")
FIRST_PERSON = re.compile(r"\b(I|I'm|I’m|I've|I’ve|I'd|I’d|[Mm]y|[Mm]e)\b")
HYPOTHETICAL_FIRST = re.compile(r"(?:^|(?<=[\s,;:(\"“]))(?:I'd|I’d|I would|I'll|I’ll|I will|I might|I may|I could|"
                                r"I want to|I wanted to|I hope to|I wish I could)\s+(?:really\s+)?"
                                r"(?P<verb>love|like|enjoy|adore|prefer|try)\b")


@dataclass
class DocInput:
    id: str
    url: str
    registrable: str
    text: str
    author: str | None
    published_at: str | None
    published_source: str | None
    date_conflict: bool
    evidence_kind: str
    identity_status: str
    origin_group: str
    authored: bool  # the subject wrote this page (author metadata or a user-supplied URL)
    text_expires_at: str | None = None


@dataclass
class Candidate:
    doc_id: str
    claim_type: str
    predicate: str
    polarity: int | None
    object_text: str
    object_key: str
    mode: str  # authored | quoted | reported
    subject_link: str
    span: tuple[int, int]
    event_date: str | None
    outcome: str = "ok"  # ok | rejected | insufficient
    reason: str = ""
    rule: str = ""
    past: bool = False
    flags: list[str] = field(default_factory=list)


def event_date(sentence: str) -> str | None:
    found = EVENT_DATE.findall(sentence)
    if not found:
        return None
    month, year = found[-1]
    return f"{year}-{_MONTH_NUM[month[:3].lower()]:02d}" if month else year


def _object(rest: str) -> tuple[str, str] | None:
    rest = rest.strip()
    m = _OBJ_END.search(rest)
    phrase = (rest[: m.start()] if m else rest).strip()
    words = phrase.split()
    while words and words[0].lower() in DETERMINERS:
        words = words[1:]
    if not words:
        return None
    text = " ".join(words[:8])
    text = TRAILING_FILLER.sub("", text).strip() or text  # "the Open Compute Project too" -> same object
    return text, re.sub(r"[^\w\s\-]", "", text.lower()).strip()


def _name_alt(subject: Subject) -> str:
    return subject.name_regex().pattern


def _blank_quotes(s: str, keep: set[int]) -> str:
    """Blank out quotations (someone else's words). Single-word scare quotes ('I "love" meetings') stay, so the
    sarcasm marker can be seen."""
    out = s
    for i, m in enumerate(QUOTE.finditer(s)):
        if i not in keep and " " in m.group(1).strip():
            out = out[: m.start()] + " " * (m.end() - m.start()) + out[m.end():]
    return out


def _quote_attribution(sentence: str, subject: Subject) -> list[tuple[int, re.Match, bool]]:
    """(index, quote match, attributed to the subject?) for every quotation in the sentence."""
    name, last = _name_alt(subject), re.escape(subject.last)
    who = rf"(?:{name}|\b{last}\b|\b[Ss]he\b|\b[Hh]e\b|\b[Tt]hey\b)"
    verb = r"(?:said|says|told [\w ]{1,20}?|added|explained|wrote|noted|recalled|tweeted|posted)"
    out = []
    for i, m in enumerate(QUOTE.finditer(sentence)):
        after, before = sentence[m.end():], sentence[: m.start()]
        ok = bool(re.match(rf"\s*,?\s*{verb}\s+{who}", after) or re.match(rf"\s*,?\s*{who}\s+{verb}", after)
                  or re.search(rf"{who}\s+{verb}\s*[:,]?\s*$", before)
                  or re.search(rf"according to\s+{who}[^\"“]*$", before))
        out.append((i, m, ok))
    return out


def extract_doc(doc: DocInput, subject: Subject, today: date) -> tuple[list[Candidate], dict[str, int], int]:
    """Candidates from one gated document; out-of-scope counts; number of subject mentions without a claim."""
    cands: list[Candidate] = []
    dropped: dict[str, int] = {}
    no_claim = 0
    name = subject.name_regex()
    last = re.compile(rf"\b{re.escape(subject.last)}\b")
    seen_full = False
    prev_about = False
    for a, b in sentence_spans(doc.text):
        s = doc.text[a:b]
        has_full = bool(name.search(s))
        seen_full = seen_full or has_full
        about = has_full or (seen_full and bool(last.search(s))) or \
            (prev_about and bool(re.match(r"^(She|He|They|Her|His|Their)\b", s)))
        quotes = _quote_attribution(s, subject)
        subject_quote = [q for q in quotes if q[2]]
        first_person_authored = doc.authored and bool(FIRST_PERSON.search(_blank_quotes(s, set())))
        if not (about or subject_quote or first_person_authored):
            prev_about = False
            continue
        cat = scope.classify(s, today, subject.location)
        if cat:
            dropped[cat] = dropped.get(cat, 0) + 1
            prev_about = about
            continue
        span = (a, min(b, a + MAX_EVIDENCE_CHARS))
        when = event_date(s)
        found = None
        if subject_quote:
            for _, m, _ in subject_quote:
                found = found or _grammar(m.group(1), subject, "first", doc, span, when, "quoted", "attributed_quote", s)
        if not found and first_person_authored:
            clause = _blank_quotes(s, set())
            found = _grammar(clause, subject, "first", doc, span, when, "authored", "authored_page", s)
            if not found and doc.authored:
                # the subject quoting someone else ("As my mentor says, 'I love chaos'"): never the subject's view
                for _, m, attributed in quotes:
                    if not attributed and _grammar(m.group(1), subject, "first", doc, span, when, "authored",
                                                   "authored_page", s):
                        found = _reject(doc, span, when, "authored", "authored_page", "quotes someone else", s)
                        break
        if not found and about:
            clause = _blank_quotes(s, {i for i, _, ok in quotes if ok})
            found = _grammar(clause, subject, "third", doc, span, when, "reported", "subject_of_sentence", s)
        if found:
            cands.append(found)
        elif about:
            no_claim += 1
        prev_about = about
    return cands, dropped, no_claim


def _reject(doc, span, when, mode, link, reason, s, outcome="rejected") -> Candidate:
    return Candidate(doc.id, "stated_preference", "unclear", None, "(not attributed)", "", mode, link, span, when,
                     outcome, reason, RULES)


def _grammar(clause: str, subject: Subject, person: str, doc: DocInput, span, when, mode, link,
             sentence: str) -> Candidate | None:
    if person == "first":
        subj = r"(?:^|(?<=[\s,;:(\"“]))(?:I)"
        prefs, fan = PREF_FIRST, FAN_FIRST
    else:
        subj = rf"(?:{_name_alt(subject)}|\b{re.escape(subject.last)}\b|\b[Ss]he\b|\b[Hh]e\b|\b[Tt]hey\b)"
        prefs, fan = PREF_THIRD, FAN_THIRD

    def cand(ctype, predicate, polarity, rest, past=False, outcome="ok", reason=""):
        obj = _object(rest)
        if obj is None or obj[0].split()[0].lower() in VAGUE_OBJECT or len(obj[0].split()) > 6:
            if ctype != "stated_preference":
                return None
            if outcome == "ok":
                outcome, reason = "insufficient", "unclear object (unverified inference)"
            obj = obj or ("(unclear)", "")
        return Candidate(doc.id, ctype, predicate, polarity, obj[0], obj[1], mode, link, span, when, outcome,
                         reason, RULES, past)

    # --- preferences
    dn = re.search(rf"{subj}\s+(?:don't|don’t|do not|doesn't|doesn’t|does not)\s+(?:hate|dislike|mind)\b", clause)
    if dn or re.search(r"\bnot that (I|she|he|they)\b|\bwouldn't say\b", clause, re.I):
        rest = clause[dn.end():] if dn else ""
        return cand("stated_preference", "unclear", None, rest, outcome="rejected",
                    reason="unclear negation (double negative)")
    if person == "first" and (h := HYPOTHETICAL_FIRST.search(clause)):
        return cand("stated_preference", "likes", 1, clause[h.end():], outcome="rejected",
                    reason="hypothetical or wish, not a current preference")
    pref = None
    m = FAVOURITE.search(clause) if person == "first" else None
    if m:
        pref = (1, "likes", clause[m.start("obj"):], m.start("obj"))
    if not pref:
        m = re.search(rf"{subj}{_APPOS}{_ADV}\s+{fan}\s+(?P<obj>.+)" if person == "third"
                      else rf"(?:^|(?<=[\s,;:(\"“])){fan}\s+(?P<obj>.+)", clause)
        if m:
            neg = bool(m.group("neg"))
            pref = (-1 if neg else 1, "dislikes" if neg else "likes", m.group("obj"), m.start())
    if not pref:
        for verb, pol, predicate in prefs:
            # optional scare quotes around the verb are kept so the sarcasm check below can see them
            m = re.search(rf"{subj}{_APPOS}{_ADV}\s+[\"“‘']?(?P<verb>{verb})\b[\"”’']?\s+(?P<obj>.+)", clause)
            if m:
                pref = (pol, predicate, m.group("obj"), m.start("verb"))
                break
    if pref:
        pol, predicate, rest, at = pref
        sentence_low = sentence.strip()
        if sentence_low.endswith("?"):
            return cand("stated_preference", predicate, pol, rest, outcome="rejected", reason="question")
        if SARCASM.search(sentence):
            return cand("stated_preference", predicate, pol, rest, outcome="rejected", reason="sarcasm marker")
        if REPOST.search(sentence):
            return cand("stated_preference", predicate, pol, rest, outcome="rejected", reason="repost of someone else")
        if QUOTED_OTHER.search(sentence):
            return cand("stated_preference", predicate, pol, rest, outcome="rejected", reason="quotes someone else")
        if DOUBLE_NEG.search(clause):
            return cand("stated_preference", predicate, pol, rest, outcome="rejected",
                        reason="unclear negation (double negative)")
        if HYPOTHETICAL.search(clause[: at + 1] if at else clause) or re.match(r"\s*to (try|learn|start)\b", rest):
            return cand("stated_preference", predicate, pol, rest, outcome="rejected",
                        reason="hypothetical or wish, not a current preference")
        return cand("stated_preference", predicate, pol, rest)

    if sentence.strip().endswith("?"):
        return None
    # --- associations
    for rx in (ASSOC_BOARD, ASSOC_VOLUNTEER, ASSOC_ROLE):
        m = re.search(rf"{subj}{_APPOS}{_ADV}\s*" + rx.pattern, clause, rx.flags)
        if m:
            role = (m.groupdict().get("role") or ("board" if rx is ASSOC_BOARD else "volunteer")).lower()
            tense = (m.group("tense") or "").lower()
            past = tense in {"was", "served as", "sat on", "was on", "served on", "volunteered"}
            return cand("association", f"{role}_of", None, m.group("obj"), past=past)
    # --- activities
    for verb, predicate, prep in ACTIVITIES:
        prep_rx = rf"\s+{prep}" if prep and not prep.endswith("?") else (rf"(?:\s+{prep[:-1]})?" if prep else "")
        m = re.search(rf"{subj}{_APPOS}{_ADV}\s+(?P<verb>{verb})\b{prep_rx}\s+(?P<obj>.+)", clause)
        if not m:
            continue
        if predicate == "ran" and not RUN_WORDS.search(m.group("obj")):
            continue  # "runs the infrastructure team" is not a running event
        if HYPOTHETICAL.search(clause[: m.start("verb") + 1]):
            return None
        return cand("activity", predicate, None, m.group("obj"))
    return None
