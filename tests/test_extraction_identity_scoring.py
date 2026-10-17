from datetime import date

import dataset
from personalizer.extract import REDACTED, HeuristicExtractor, prepare_text, verify_excerpt
from personalizer.fetch import html_to_text
from personalizer.identity import Anchors, assess
from personalizer.models import (
    Category,
    Disclosure,
    Finding,
    Level,
    PreferenceTier,
    ResearchRequest,
    SourceDocument,
    SourceTier,
    utcnow,
)
from personalizer.scoring import corroborate, detect_role_conflicts, freshness, preference_tiers

REQ = ResearchRequest(name="Priya Raman", organization="Nimbus Compute", organization_domain="nimbuscompute.example",
                      meeting_purpose="Discuss GPU infrastructure")
ANCHORS = Anchors.from_request(REQ)


def doc(url: str) -> SourceDocument:
    title, text, _ = html_to_text(dataset.PAGES[url]["html"])
    from personalizer.text import domain_of

    return SourceDocument(url=url, domain=domain_of(url), title=title, text=text, provider="fixture")


def test_verify_excerpt():
    src = "Priya Raman is VP at Nimbus Compute.\nShe likes GPUs."
    assert verify_excerpt("Priya Raman is VP at Nimbus Compute.", src)
    assert verify_excerpt("priya raman is vp at  nimbus compute", src)  # whitespace/case tolerant
    assert not verify_excerpt("Priya Raman is CEO at Nimbus Compute.", src)
    assert not verify_excerpt("short", src)
    assert not verify_excerpt(f"{REDACTED} something long enough", src + REDACTED)


def test_identity_official_vs_same_name_vs_hedged():
    assert assess(doc("https://nimbuscompute.example/team/priya-raman"), ANCHORS).level == Level.HIGH
    chef = assess(doc("https://chefstories.example/priya-raman-pastry"), ANCHORS)
    assert chef.level == Level.LOW and chef.conflicts
    assert assess(doc("https://forum.example/thread/cloud-vendors"), ANCHORS).level == Level.LOW


def test_injection_sentence_redacted_before_extraction():
    d = doc("https://techblog.example/devcon-recap")
    text = prepare_text(d)
    assert "approved the partnership" not in text and "discount" not in text  # hidden div removed too
    claims = HeuristicExtractor().extract(d, text, ANCHORS)
    assert [c.category for c in claims] == [Category.SPEAKING]


def test_heuristic_disclosure_types():
    d = doc("https://podcast.example/episodes/scaling-gpu-clouds")
    claims = {c.category: c for c in HeuristicExtractor().extract(d, prepare_text(d), ANCHORS)}
    assert claims[Category.HOBBY].disclosure == Disclosure.SELF_STATED
    assert claims[Category.COMMUNICATION].disclosure == Disclosure.SELF_STATED
    assert claims[Category.OTHER].sensitive  # faith/temple sentence is surfaced only to be excluded
    g = doc("https://gossip.example/priya-golf")
    golf = HeuristicExtractor().extract(g, prepare_text(g), ANCHORS)
    assert golf[0].category == Category.HOBBY and golf[0].disclosure == Disclosure.THIRD_PARTY


def _f(url, excerpt, cat=Category.EXPERTISE, disc=Disclosure.OBSERVED, published=None) -> Finding:
    return Finding(claim=excerpt, category=cat, source_id="s", source_url=url, source_name="n",
                   source_tier=SourceTier.GENERAL, excerpt=excerpt, published_at=published, retrieved_at=utcnow(),
                   identity_score=0.8, identity_confidence=Level.HIGH, source_reliability=Level.MEDIUM,
                   disclosure=disc)


def test_corroboration_ignores_syndicated_copies():
    a = _f("https://a.example/x", "Priya Raman focuses on GPU scheduling for AI workloads.")
    b = _f("https://b.example/y", "Priya Raman focuses on GPU scheduling for AI workloads.")  # syndicated
    c = _f("https://c.example/z", "Raman's work focuses on scheduling GPU clusters for AI training workloads.")
    corroborate([a, b, c])
    assert a.corroborating_urls == ["https://c.example/z"]


def test_preference_tiers():
    explicit = _f("https://a.example", "I love cricket", Category.HOBBY, Disclosure.SELF_STATED)
    rumour = _f("https://b.example", "rumoured golfer", Category.HOBBY, Disclosure.THIRD_PARTY)
    role = _f("https://c.example", "VP at Nimbus", Category.ROLE)
    preference_tiers([explicit, rumour, role])
    assert explicit.preference_tier == PreferenceTier.EXPLICIT
    assert rumour.preference_tier == PreferenceTier.HYPOTHESIS
    assert role.preference_tier == PreferenceTier.NOT_APPLICABLE


def test_title_conflict_prefers_newer_source():
    old = _f("https://a.example", "Daniel Okafor is Chief Technology Officer at Helio Grid.", Category.ROLE,
             published=date(2026, 1, 1))
    new = _f("https://b.example", "Helio Grid named Daniel Okafor as VP of Engineering at Helio Grid.", Category.ROLE,
             published=date(2026, 9, 1))
    warnings = detect_role_conflicts([old, new], "helio grid")
    assert any("different job titles" in w for w in warnings)
    assert old.downgrade_reasons and not new.downgrade_reasons


def test_freshness_bands():
    today = date(2026, 10, 17)
    assert freshness(date(2026, 1, 1), today) == 1.0
    assert freshness(date(2019, 1, 1), today) == 0.2
    assert freshness(None, today) == 0.5


# Regression tests for patterns first seen in a live Wikipedia run.

def _heuristic(title: str, text: str, name="Satya Nadella", org="Microsoft"):
    req = ResearchRequest(name=name, organization=org, meeting_purpose="Discuss AI cloud")
    d = SourceDocument(url="https://w.example/x", domain="w.example", title=title, text=text, provider="t")
    return HeuristicExtractor().extract(d, prepare_text(d), Anchors.from_request(req))


def test_passing_mention_in_list_is_not_a_claim():
    text = ("The conference drew attention due to the participation of several major players, such as "
            "OpenAI CEO Sam Altman, Microsoft CEO Satya Nadella and Google DeepMind chief Demis Hassabis.")
    assert _heuristic("2023 Conference", text) == []


def test_pronoun_on_another_persons_page_is_not_our_subject():
    text = ("John W. Thompson chaired the board of Microsoft.\n"
            "He led the search for Microsoft's next CEO; as a result, Satya Nadella was selected.")
    assert _heuristic("John W. Thompson", text) == []


def test_sentence_split_keeps_initials():
    from personalizer.text import split_sentences

    assert split_sentences("He succeeded John W. Thompson as chairman. He is CEO.") == [
        "He succeeded John W. Thompson as chairman.", "He is CEO."]
