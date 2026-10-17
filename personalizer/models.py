"""Pydantic schemas shared by every pipeline stage.

The evidence model is the core of the system: every statement in a report must
point back to a Finding, and every Finding must carry a verbatim excerpt from a
retrieved source.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Category(str, Enum):
    ROLE = "role_and_career"
    EXPERTISE = "professional_expertise"
    PUBLICATION = "publication"
    SPEAKING = "speaking_appearance"
    COMPANY = "company_activity"
    AFFILIATION = "professional_affiliation"
    INDUSTRY_TOPIC = "industry_topic"
    HOBBY = "disclosed_hobby"
    COMMUNICATION = "communication_preference"
    CONNECTION = "business_connection"
    OTHER = "other"


PERSONAL_CATEGORIES = {Category.HOBBY}
PREFERENCE_CATEGORIES = {
    Category.HOBBY,
    Category.COMMUNICATION,
    Category.EXPERTISE,
    Category.INDUSTRY_TOPIC,
}


class Disclosure(str, Enum):
    SELF_STATED = "explicitly_stated"  # the person said it themselves (quote, own article, own bio)
    OBSERVED = "observed_activity"  # documented public activity (talk given, paper published)
    THIRD_PARTY = "third_party_report"  # someone else asserts it about them
    INFERRED = "inferred"  # derived, not stated anywhere


class PreferenceTier(str, Enum):
    EXPLICIT = "explicit_preference"
    REPEATED = "repeated_public_interest"
    HYPOTHESIS = "hypothesis_needs_confirmation"
    NOT_APPLICABLE = "not_applicable"


class Level(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceTier(str, Enum):
    OFFICIAL = "official"  # subject's organisation or the subject's own site
    REPUTABLE = "reputable_publication"
    PROFESSIONAL = "professional_platform"  # conference sites, academic indexes, encyclopaedias
    GENERAL = "general_web"
    BLOCKED = "blocked"


class FeedbackLabel(str, Enum):
    ACCURATE = "accurate"
    INCORRECT = "incorrect"
    IRRELEVANT = "irrelevant"
    INAPPROPRIATE = "inappropriate"


# ---------------------------------------------------------------- input


class ResearchRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    organization: str | None = Field(default=None, max_length=160)
    organization_domain: str | None = Field(
        default=None, max_length=120, description="e.g. nimbuscompute.example; improves identity resolution"
    )
    role: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=120)
    profile_url: HttpUrl | None = Field(
        default=None, description="Known public profile; used as an identity anchor, fetched only if permitted"
    )
    meeting_purpose: str = Field(min_length=5, max_length=600)
    requester_context: str | None = Field(
        default=None, max_length=500,
        description="Your own role/interests; used only to find genuine common ground, never searched",
    )
    excluded_categories: list[Category] = Field(default_factory=list)
    allow_personal_interests: bool = True
    max_sources: int = Field(default=12, ge=1, le=40)

    @field_validator("name", "organization", "role", "location", "meeting_purpose")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return " ".join(v.split()) if v else v

    @field_validator("organization_domain")
    @classmethod
    def _domain(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip().lower()
        v = v.removeprefix("https://").removeprefix("http://").split("/")[0]
        return v.removeprefix("www.")


# ---------------------------------------------------------------- retrieval


class SearchQuery(BaseModel):
    text: str  # quoted phrases + keywords; providers render operators themselves
    include_domains: list[str] = Field(default_factory=list)
    purpose: str
    origin: Literal["template", "llm"] = "template"


class SearchHit(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    content: str | None = None  # full text when the provider supplies it
    published_at: date | None = None
    provider: str
    query: str


class IdentityAssessment(BaseModel):
    score: float = 0.0
    level: Level = Level.LOW
    signals: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class SourceDocument(BaseModel):
    id: str = Field(default_factory=lambda: new_id("src"))
    url: str
    domain: str
    title: str = ""
    published_at: date | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    provider: str
    tier: SourceTier = SourceTier.GENERAL
    text: str = Field(default="", exclude=True)  # never persisted (data minimisation)
    fetch_status: str = "ok"
    injection_flags: list[str] = Field(default_factory=list)
    identity: IdentityAssessment = Field(default_factory=IdentityAssessment)
    used: bool = False
    skip_reason: str | None = None


# ---------------------------------------------------------------- evidence


class RawClaim(BaseModel):
    """Extractor output before verification."""

    claim: str
    category: Category
    excerpt: str
    disclosure: Disclosure
    about_subject: bool = True
    sensitive: bool = False


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: new_id("f"))
    claim: str
    category: Category
    source_id: str
    source_url: str
    source_name: str
    source_tier: SourceTier
    excerpt: str
    published_at: date | None = None
    retrieved_at: datetime
    identity_score: float
    identity_confidence: Level
    source_reliability: Level
    evidence_strength: Level = Level.LOW
    corroborating_urls: list[str] = Field(default_factory=list)
    disclosure: Disclosure
    preference_tier: PreferenceTier = PreferenceTier.NOT_APPLICABLE
    freshness: float = 0.5
    relevance: float = 0.0
    relevance_reason: str = ""
    confidence_score: float = 0.0  # rule-based ordinal score, NOT a probability
    confidence: Level = Level.LOW
    rank_score: float = 0.0
    suitable_for_personalization: bool = False
    exclusion_reason: str | None = None
    downgrade_reasons: list[str] = Field(default_factory=list)
    feedback: FeedbackLabel | None = None


class ExcludedItem(BaseModel):
    """Something dropped before it became a Finding. Excerpts are not kept."""

    source_url: str
    category: Category | None = None
    reason: str


# ---------------------------------------------------------------- output


class Statement(BaseModel):
    text: str
    finding_ids: list[str] = Field(min_length=1)


class Suggestion(BaseModel):
    kind: Literal["conversation_starter", "question", "common_ground", "prep_note"]
    text: str
    rationale: str
    finding_ids: list[str] = Field(min_length=1)
    optional: bool = False


class IdentitySummary(BaseModel):
    resolved: bool
    confidence: Level
    notes: list[str] = Field(default_factory=list)


class Usage(BaseModel):
    search_calls: dict[str, int] = Field(default_factory=dict)
    pages_fetched: int = 0
    llm_calls: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    estimated_cost_usd: float = 0.0


class Report(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rpt"))
    created_at: datetime = Field(default_factory=utcnow)
    mode: Literal["llm", "heuristic"]
    request: ResearchRequest
    identity: IdentitySummary
    professional_context: list[Statement] = Field(default_factory=list)
    conversation_starters: list[Suggestion] = Field(default_factory=list)
    questions: list[Suggestion] = Field(default_factory=list)
    common_ground: list[Suggestion] = Field(default_factory=list)
    prep_notes: list[Suggestion] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list, description="finding ids that need confirmation")
    unknowns: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    excluded: list[ExcludedItem] = Field(default_factory=list)
    sources: list[SourceDocument] = Field(default_factory=list)
    queries: list[SearchQuery] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)

    def finding(self, finding_id: str) -> Finding | None:
        return next((f for f in self.findings if f.id == finding_id), None)
