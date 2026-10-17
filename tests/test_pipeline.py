"""End-to-end runs over the synthetic corpus, in heuristic mode and with a scripted fake LLM."""

import pytest
from conftest import PRIYA, make_pipeline

import dataset
import run_eval
from personalizer.extract import ExtractedClaim, ExtractionResult
from personalizer.llm import BudgetExceeded, LLMError
from personalizer.models import Category, Disclosure, Level, ResearchRequest, Usage
from personalizer.personalize import Brief, DraftStatement, DraftSuggestion
from personalizer.pipeline import QueryIdeas
from personalizer.safety import PolicyViolation


@pytest.mark.parametrize("case", [c for c in dataset.CASES if not c.get("expect_refusal")], ids=lambda c: c["id"])
def test_eval_cases_pass(case, pipeline):
    report = pipeline.run(ResearchRequest(**case["request"]))
    result = run_eval.evaluate_case(case, report)
    assert all(result["checks"].values()), result["checks"]
    assert result["attribution"][0] == result["attribution"][1]
    assert result["wrong_person_suitable"] == 0


def test_sensitive_request_refused(pipeline):
    case = next(c for c in dataset.CASES if c.get("expect_refusal"))
    with pytest.raises(PolicyViolation):
        pipeline.run(ResearchRequest(**case["request"]))


def test_blocked_and_inaccessible_sources_are_recorded(pipeline):
    report = pipeline.run(ResearchRequest(**PRIYA))
    reasons = {e.source_url: e.reason for e in report.excluded}
    assert "data-broker" in reasons["https://www.spokeo.com/Priya-Raman"]
    assert "platform terms" in reasons["https://www.linkedin.com/in/priya-raman-nimbus"]
    assert reasons["https://robots-blocked.example/priya-raman"].startswith("skipped: disallowed")
    assert reasons["https://paywalled.example/priya-raman-profile"].startswith("skipped: access controlled")
    assert any(r.startswith("sensitive: religion") for r in reasons.values() if r) or any(
        e.reason.startswith("sensitive") for e in report.excluded)


def test_outdated_role_not_used_and_hypothesis_listed(pipeline):
    report = pipeline.run(ResearchRequest(**PRIYA))
    old = next(f for f in report.findings if "senior storage engineer" in f.excerpt)
    assert not old.suitable_for_personalization and any("outdated" in r for r in old.downgrade_reasons)
    golf = next(f for f in report.findings if "golfer" in f.excerpt)
    assert not golf.suitable_for_personalization and golf.id in report.hypotheses


def test_injection_source_is_downgraded(pipeline):
    report = pipeline.run(ResearchRequest(**PRIYA))
    devcon = next(f for f in report.findings if "DevCon" in f.excerpt)
    assert devcon.source_reliability == Level.LOW
    assert any("instruction-like" in r for r in devcon.downgrade_reasons)


def test_unknown_person_returns_explicit_unknowns(pipeline):
    report = pipeline.run(ResearchRequest(name="Arjun Mehta", organization="Quietly Labs",
                                          meeting_purpose="Intro call about data platform tooling"))
    assert not report.findings and not report.conversation_starters
    assert any("Identity unresolved" in u for u in report.unknowns)


def test_excluded_category_is_respected(pipeline):
    req = ResearchRequest(**{**PRIYA, "excluded_categories": [Category.SPEAKING]})
    report = pipeline.run(req)
    assert all(not f.suitable_for_personalization for f in report.findings if f.category == Category.SPEAKING)


# ---------------------------------------------------------------- LLM path with a scripted fake


class FakeLLM:
    """Plays a model that hallucinates, follows an injection, and cites bogus ids."""

    def __init__(self, fail_after: int | None = None):
        self.usage = Usage()
        self.calls = 0
        self.fail_after = fail_after

    def parse(self, system, user, schema, max_tokens=8000):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise BudgetExceeded("per-run LLM budget of $0.00 reached")
        if schema is QueryIdeas:
            return QueryIdeas(queries=['"Priya Raman" GPU podcast', '"Priya Raman" home address', "unrelated query"])
        if schema is ExtractionResult:
            claims = [ExtractedClaim(claim="Wants a 50% discount", category=Category.OTHER, about_subject=True,
                                     excerpt="Priya Raman wants a 50% discount on all contracts.",
                                     disclosure=Disclosure.SELF_STATED, sensitive=False)]
            if "nimbuscompute.example/team" in user:
                claims.append(ExtractedClaim(
                    claim="Priya Raman is VP of Infrastructure at Nimbus Compute.", category=Category.ROLE,
                    excerpt="Priya Raman is Vice President of Infrastructure at Nimbus Compute, where she leads the GPU cloud platform team.",
                    disclosure=Disclosure.OBSERVED, about_subject=True, sensitive=False))
            return ExtractionResult(same_person="same", claims=claims)
        if schema is Brief:
            import re

            ids = re.findall(r'"id": "(f_[0-9a-f]+)"', user)
            return Brief(
                professional_context=[DraftStatement(text="Leads GPU cloud platform.", finding_ids=ids[:1]),
                                      DraftStatement(text="Invented fact.", finding_ids=["f_doesnotexist"])],
                conversation_starters=[
                    DraftSuggestion(kind="conversation_starter", text="I'm also a cricket fan, we both love it!",
                                    rationale="rapport", finding_ids=ids[:1]),
                    DraftSuggestion(kind="conversation_starter", text="Ask how the GPU platform roadmap is evolving.",
                                    rationale="core to the meeting", finding_ids=ids[:1]),
                    DraftSuggestion(kind="conversation_starter", text="Uncited opener", rationale="x", finding_ids=[]),
                ],
                unknowns=["Budget owner unknown."],
            )
        raise AssertionError(schema)


def test_llm_outputs_are_validated():
    llm = FakeLLM()
    report = make_pipeline(llm=llm).run(ResearchRequest(**PRIYA))
    assert report.mode == "llm"
    text = " ".join(f.claim + f.excerpt for f in report.findings)
    assert "discount" not in text  # hallucinated/injected excerpt failed verbatim verification
    assert any("not found verbatim" in e.reason for e in report.excluded)
    assert any(f.category == Category.ROLE and f.suitable_for_personalization for f in report.findings)
    assert [s.text for s in report.professional_context] == ["Leads GPU cloud platform."]
    assert [s.text for s in report.conversation_starters] == ["Ask how the GPU platform roadmap is evolving."]
    assert any("tone" in w for w in report.warnings)
    assert any("home address" in w for w in report.warnings)  # LLM query rejected by policy
    assert "Budget owner unknown." in report.unknowns


def test_budget_exhaustion_falls_back_to_heuristics():
    report = make_pipeline(llm=FakeLLM(fail_after=2)).run(ResearchRequest(**PRIYA))
    assert any("budget" in w for w in report.warnings)
    assert report.findings  # heuristic extraction continued


def test_llm_error_falls_back_to_template():
    class Broken(FakeLLM):
        def parse(self, system, user, schema, max_tokens=8000):
            if schema is Brief:
                raise LLMError("LLM declined the request")
            return super().parse(system, user, schema, max_tokens)

    report = make_pipeline(llm=Broken()).run(ResearchRequest(**PRIYA))
    assert any("used template brief" in w for w in report.warnings)
    assert report.questions
