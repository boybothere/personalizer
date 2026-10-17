"""The research pipeline. Plain sequential stages, no agent loop:

input -> policy check -> query generation -> search -> source filtering -> fetch/sanitise
-> identity resolution -> extraction -> excerpt verification -> sensitivity filter
-> corroboration/conflicts -> confidence -> preference tiers -> relevance/ranking
-> suitability -> personalisation (+validation) -> report
"""

from __future__ import annotations

import json
import logging
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel

from .config import SEARCH_PRICES, Settings
from .extract import Extractor, HeuristicExtractor, LLMExtractor, prepare_text, verify_excerpt
from .fetch import Fetcher, FixtureFetcher, HttpFetcher
from .identity import Anchors, assess
from .llm import AnthropicLLM, BudgetExceeded, LLMError, StructuredLLM
from .models import (
    Category,
    ExcludedItem,
    Finding,
    IdentitySummary,
    Level,
    PreferenceTier,
    Report,
    ResearchRequest,
    SearchHit,
    SearchQuery,
    SourceDocument,
    SourceTier,
    Usage,
)
from .personalize import LLMPersonalizer, Personalizer, TemplatePersonalizer, validate
from .providers import FixtureProvider, SearchError, SearchProvider, build_providers
from .queries import finalize, template_queries
from .safety import detect_injection, request_policy, sensitive_reason
from .scoring import corroborate, detect_role_conflicts, preference_tiers, rank, relevance, score, suitability
from .sources import TIER_RELIABILITY, SourcePolicy
from .text import domain_of, norm_key

log = logging.getLogger(__name__)
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "ref"}
_LOWER = {Level.HIGH: Level.MEDIUM, Level.MEDIUM: Level.LOW, Level.LOW: Level.LOW}


def canonical_url(url: str) -> str:
    p = urlparse(url)
    q = urlencode([(k, v) for k, v in parse_qsl(p.query) if k.lower() not in _TRACKING])
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "", q, ""))


class QueryIdeas(BaseModel):
    queries: list[str]


QUERY_SYSTEM = """You suggest web search queries to prepare for a professional meeting.
Return up to 3 additional queries that would find public, professional information about the
person (talks, articles, interviews, projects) relevant to the meeting purpose. Use exact-phrase
quotes for the person's name. Do not use filetype:, inurl:, intitle: or similar operators, and never
target personal details (family, address, contact data, health, religion, politics, finances)."""


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        providers: list[SearchProvider],
        fetcher: Fetcher,
        llm: StructuredLLM | None = None,
        today: date | None = None,
    ):
        self.settings = settings
        self.providers = providers
        self.fetcher = fetcher
        self.llm = llm
        self.today = today or date.today()

    @classmethod
    def from_settings(cls, settings: Settings, today: date | None = None) -> "Pipeline":
        providers = build_providers(settings)
        if any(isinstance(p, FixtureProvider) for p in providers):
            fetcher: Fetcher = FixtureFetcher(json.loads(settings.fixture_path.read_text(encoding="utf-8"))["pages"])
        else:
            fetcher = HttpFetcher(settings.user_agent, settings.fetch_timeout_s, settings.max_page_bytes)
        return cls(settings, providers, fetcher, today=today)

    # ------------------------------------------------------------------ run

    def run(self, req: ResearchRequest) -> Report:
        request_policy(req.meeting_purpose)
        usage = Usage()
        llm = self._llm_for(usage)
        warnings: list[str] = []
        excluded: list[ExcludedItem] = []
        if not self.providers:
            warnings.append("No search provider is configured or has credentials; no sources were searched.")

        anchors = Anchors.from_request(req)
        profile_domain = domain_of(str(req.profile_url)) if req.profile_url else None
        policy = SourcePolicy(
            org_domain=req.organization_domain,
            extra_reputable=set(self.settings.extra_reputable_domains),
            extra_blocked=set(self.settings.extra_blocked_domains),
        )
        if profile_domain and not policy.block_reason(profile_domain):
            policy.personal_domains.add(profile_domain)

        # 1. queries
        queries = template_queries(req)
        if llm:
            queries += self._llm_queries(llm, req, warnings)
        queries, rejected = finalize(queries)
        warnings += rejected

        # 2. search
        hits = self._search(queries, usage, warnings)
        if req.profile_url and not policy.block_reason(profile_domain or ""):
            hits.insert(0, SearchHit(url=str(req.profile_url), provider="user", query="known profile"))

        # 3. source filtering + retrieval
        docs = self._retrieve(hits, policy, req, usage, excluded)

        # 4. identity, extraction, verification
        extractor: Extractor = LLMExtractor(llm) if llm else HeuristicExtractor()
        fallback = HeuristicExtractor()
        findings: list[Finding] = []
        for doc in docs:
            if doc.fetch_status != "ok":
                continue
            doc.injection_flags += detect_injection(doc.text)
            doc.identity = assess(doc, anchors)
            if doc.identity.level == Level.LOW:
                doc.skip_reason = "identity not matched: " + "; ".join(doc.identity.conflicts or doc.identity.signals)
                excluded.append(ExcludedItem(source_url=doc.url, reason=doc.skip_reason))
                continue
            text = prepare_text(doc)
            try:
                claims = extractor.extract(doc, text, anchors)
            except BudgetExceeded as exc:
                warnings.append(f"{exc}; remaining sources used offline heuristic extraction")
                extractor = fallback
                claims = fallback.extract(doc, text, anchors)
            except LLMError as exc:
                warnings.append(f"LLM extraction failed for {doc.domain} ({exc}); used heuristic extraction")
                claims = fallback.extract(doc, text, anchors)
            for c in claims:
                if not c.about_subject:
                    excluded.append(ExcludedItem(source_url=doc.url, category=c.category, reason="statement about another person"))
                    continue
                if not verify_excerpt(c.excerpt, text):
                    excluded.append(ExcludedItem(source_url=doc.url, category=c.category,
                                                 reason="excerpt not found verbatim in source (unsupported claim)"))
                    continue
                reason = sensitive_reason(c.claim + " " + c.excerpt) or ("flagged sensitive by extractor" if c.sensitive else None)
                if reason:
                    excluded.append(ExcludedItem(source_url=doc.url, category=c.category, reason=f"sensitive: {reason}"))
                    continue
                findings.append(self._finding(doc, c))
            doc.used = any(f.source_id == doc.id for f in findings)

        findings = self._dedupe(findings)

        # 5. verification, scoring, ranking
        corroborate(findings)
        warnings += detect_role_conflicts(findings, anchors.org)
        for f in findings:
            score(f, self.today)
        preference_tiers(findings)
        for f in findings:
            relevance(f, req)
            rank(f)
            suitability(f, req)
        findings.sort(key=lambda f: (-int(f.suitable_for_personalization), -f.rank_score))

        # 6. personalisation
        suitable = [f for f in findings if f.suitable_for_personalization]
        personalizer: Personalizer = LLMPersonalizer(llm) if llm and suitable else TemplatePersonalizer()
        try:
            draft = personalizer.generate(req, suitable)
        except LLMError as exc:
            warnings.append(f"LLM synthesis failed ({exc}); used template brief")
            draft = TemplatePersonalizer().generate(req, suitable)
        brief, dropped = validate(draft, findings)
        warnings += dropped

        usage.estimated_cost_usd = round(
            usage.estimated_cost_usd + sum(SEARCH_PRICES.get(p, 0.0) * n for p, n in usage.search_calls.items()), 4
        )
        identity = self._identity_summary(docs)
        return Report(
            mode="llm" if llm else "heuristic",
            request=req,
            identity=identity,
            professional_context=brief.professional_context,
            conversation_starters=brief.conversation_starters,
            questions=brief.questions,
            common_ground=brief.common_ground,
            prep_notes=brief.prep_notes,
            hypotheses=[f.id for f in findings if f.preference_tier == PreferenceTier.HYPOTHESIS or f.confidence == Level.LOW],
            unknowns=self._unknowns(suitable, identity) + [u for u in brief.unknowns if u],
            findings=findings,
            excluded=excluded,
            sources=docs,
            queries=queries,
            warnings=warnings,
            usage=usage,
        )

    # ------------------------------------------------------------------ stages

    def _llm_for(self, usage: Usage) -> StructuredLLM | None:
        if self.llm is not None:
            if hasattr(self.llm, "usage"):
                self.llm.usage = usage
            return self.llm
        if self.settings.llm_provider == "anthropic":
            return AnthropicLLM(self.settings.model, self.settings.llm_effort, usage, self.settings.max_cost_usd)
        return None

    def _llm_queries(self, llm: StructuredLLM, req: ResearchRequest, warnings: list[str]) -> list[SearchQuery]:
        user = (f"Person: {req.name}\nOrganisation: {req.organization or 'unknown'}\nRole: {req.role or 'unknown'}\n"
                f"Meeting purpose: {req.meeting_purpose}")
        try:
            ideas = llm.parse(QUERY_SYSTEM, user, QueryIdeas, max_tokens=2000)
        except LLMError as exc:
            warnings.append(f"LLM query generation skipped ({exc})")
            return []
        return [SearchQuery(text=q, purpose="llm-suggested", origin="llm")
                for q in ideas.queries[:3] if req.name.lower() in q.lower()]  # policy applied in finalize()

    def _search(self, queries: list[SearchQuery], usage: Usage, warnings: list[str]) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for provider in self.providers:
            for q in queries:
                try:
                    results = provider.search(q, self.settings.results_per_query)
                except SearchError as exc:
                    warnings.append(f"{provider.name} search failed: {exc}")
                    if "rate limited" in str(exc) or "PERSONALIZER_CONTACT" in str(exc):
                        break
                    continue
                finally:
                    usage.search_calls[provider.name] = usage.search_calls.get(provider.name, 0) + 1
                hits.extend(results)
        return hits

    def _retrieve(self, hits: list[SearchHit], policy: SourcePolicy, req: ResearchRequest,
                  usage: Usage, excluded: list[ExcludedItem]) -> list[SourceDocument]:
        merged: dict[str, tuple[SearchHit, int]] = {}
        for h in hits:
            key = canonical_url(h.url)
            prev = merged.get(key)
            if prev is None:
                merged[key] = (h, 1)
            else:
                best = prev[0] if prev[0].content else h
                if best.published_at is None:
                    best = best.model_copy(update={"published_at": prev[0].published_at or h.published_at})
                merged[key] = (best, prev[1] + 1)

        candidates: list[tuple[SearchHit, SourceTier, str]] = []
        for key, (h, _) in merged.items():
            d = domain_of(h.url)
            reason = policy.block_reason(d)
            if reason:
                excluded.append(ExcludedItem(source_url=h.url, reason=reason))
                continue
            candidates.append((h, policy.tier(d, h.url), d))
        order = {SourceTier.OFFICIAL: 0, SourceTier.REPUTABLE: 1, SourceTier.PROFESSIONAL: 2, SourceTier.GENERAL: 3}
        candidates.sort(key=lambda c: (c[0].provider != "user", order[c[1]], -merged[canonical_url(c[0].url)][1]))
        candidates = candidates[: req.max_sources]

        docs: list[SourceDocument] = []
        fetches = 0
        for h, tier, d in candidates:
            doc = SourceDocument(url=h.url, domain=d, title=h.title, published_at=h.published_at,
                                 provider=h.provider, tier=tier)
            if h.content and h.provider != "brave":
                doc.text = h.content
            elif fetches >= self.settings.max_fetches:
                doc.fetch_status = "skipped: fetch budget reached"
            else:
                fetches += 1
                r = self.fetcher.fetch(h.url)
                doc.fetch_status, doc.text = r.status, r.text
                doc.title = doc.title or r.title
                doc.injection_flags += [f for f in r.flags if f != "truncated"]
            if doc.fetch_status != "ok":
                excluded.append(ExcludedItem(source_url=h.url, reason=doc.fetch_status))
            docs.append(doc)
        usage.pages_fetched = fetches
        return docs

    def _finding(self, doc: SourceDocument, c) -> Finding:
        reliability, _ = TIER_RELIABILITY[doc.tier]
        f = Finding(
            claim=c.claim, category=c.category, source_id=doc.id, source_url=doc.url,
            source_name=doc.title or doc.domain, source_tier=doc.tier, excerpt=c.excerpt,
            published_at=doc.published_at, retrieved_at=doc.retrieved_at, identity_score=doc.identity.score,
            identity_confidence=doc.identity.level, source_reliability=reliability, disclosure=c.disclosure,
        )
        if doc.injection_flags:
            f.source_reliability = _LOWER[f.source_reliability]
            f.downgrade_reasons.append("source contained hidden or instruction-like content")
        if doc.identity.conflicts:
            f.downgrade_reasons.append("identity conflict in source: " + "; ".join(doc.identity.conflicts))
        return f

    @staticmethod
    def _dedupe(findings: list[Finding]) -> list[Finding]:
        seen: dict[tuple[str, str], Finding] = {}
        for f in findings:
            key = (f.source_id, norm_key(f.excerpt))
            if key not in seen:
                seen[key] = f
        return list(seen.values())

    @staticmethod
    def _identity_summary(docs: list[SourceDocument]) -> IdentitySummary:
        levels = [d.identity.level for d in docs if d.fetch_status == "ok"]
        high, medium = levels.count(Level.HIGH), levels.count(Level.MEDIUM)
        notes = []
        rejected = [d for d in docs if d.fetch_status == "ok" and d.identity.level == Level.LOW]
        others = sorted({c for d in rejected for c in d.identity.conflicts})
        if others:
            notes.append("Sources about a different or unverified person were excluded (" + "; ".join(others[:4]) + ").")
        ambiguous = [d for d in rejected if not d.identity.conflicts and "name not found" not in d.identity.signals]
        if ambiguous:
            notes.append(f"{len(ambiguous)} source(s) mentioned the name but could not be tied to this person.")
        resolved = high >= 1 or medium >= 2
        if not resolved:
            notes.append("Identity could not be confirmed from public sources; treat any findings with caution.")
        conf = Level.HIGH if high >= 2 else Level.MEDIUM if resolved else Level.LOW
        return IdentitySummary(resolved=resolved, confidence=conf, notes=notes)

    @staticmethod
    def _unknowns(suitable: list[Finding], identity: IdentitySummary) -> list[str]:
        cats = {f.category for f in suitable}
        out = []
        if not identity.resolved:
            out.append("Identity unresolved: no reliable source ties this name to the given organisation.")
        if Category.ROLE not in cats:
            out.append("Current role could not be verified from public sources.")
        if not cats & {Category.EXPERTISE, Category.INDUSTRY_TOPIC, Category.PUBLICATION, Category.SPEAKING}:
            out.append("No public statements on the meeting topics were found.")
        out.append("Their current priorities, constraints and decision-making role for this meeting are not "
                   "established by public sources; ask rather than assume.")
        return out
