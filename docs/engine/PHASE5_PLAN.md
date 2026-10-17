# Phase 5 plan: reliable identity resolution

**Baseline:** `e1e573b` (Phase 4, pushed) plus `4a636b6` (fallback disabled by default). Phase 5 is delivered in new commits.

## Problem with today's identity handling

`engine/identity_signal.py` (phase 3) calls a document "supported" when the full name appears with **any one** anchor (organisation, domain, known URL or role terms), anywhere in the page. It is a **planning signal only**. It can't tell apart:
- namesakes on the same page;
- a company mentioned near an unrelated person;
- a former role;
- syndicated copies;
- duplicate profiles.

It also has no notion of "unresolved". Phase 5 adds a separate, auditable **identity resolver**. The planner keeps its signal for steering searches only.

## Design

### 1. Mention-level evidence (`engine/identity/evidence.py`)

For each document, find every full-name mention ("First Last", "First M. Last", "Last, First") and read the sentence around it. Signals per mention:

| Signal | Meaning |
|---|---|
| `target_org_near` | Target organisation in the same sentence or window |
| `other_org` | The name tied to a *different* organisation ("X, chef at Y", "X of Y", "Y's X", "X joined Y"). Generic function words after "of" ("VP of Infrastructure") are not organisations. |
| `historical` | "former", "previously", "until 2019", "was a…": the affiliation is past, not current |
| `role_near` | The target role's distinctive terms near the name |
| `occupation_conflict` | A clearly different occupation near the name (chef, footballer, actor, student…) without the target role |
| `hedged` | Doubtful attribution ("some say", "unverified", "posting as", "believed to be") |
| `official_domain` | Document hosted on the organisation's domain |
| `known_url` | Document is, or links to, a user-supplied profile URL |
| `profile_links` | `sameAs` / `rel=me` profile links on the page |
| `location_near` | The stated location near the name |

### 2. Document attribution (one decision per document; rules, not scores)

| Status | Rule |
|---|---|
| `confirmed` | Full name **and** (on the official domain, or is/links to a known URL), and no conflicting current affiliation |
| `strong` | Full name **and** target organisation near the name **and** at least one independent extra signal (role near, profile link to a confirmed profile, location near), with no conflict and no hedge |
| `possible` | Full name with target organisation near (and nothing else), or role near only. **A bare name-plus-organisation match is never more than `possible`.** |
| `historical` | Full name tied to the target organisation only in the past tense |
| `ambiguous` | Conflicting current affiliations for the name in the same document, or target organisation present but an occupation conflict near the name |
| `rejected` | Name tied to another organisation (and not the target), hedged attribution, or occupation conflict without any target signal |
| `insufficient` | Name without any anchor, or surname only |

Syndicated copies (simhash duplicates) and pages on the same registrable domain count **once** for independence.

### 3. Profiles

Profile candidates come from three places:
- user-supplied known URLs, which are **anchors**;
- `sameAs` / `rel=me` links on attributed pages;
- platform leads from search, which are snippet-only and therefore at most `possible`.

| Status | Rule |
|---|---|
| `confirmed` | User-supplied, or linked from a `confirmed` page |
| `strong` | Linked from two or more independent `strong` pages |
| `possible` | Snippet-only lead, or linked from one non-confirmed page |
| `duplicate` | Two or more profiles **on the same platform** for the subject that are both above `rejected`. The weaker one(s) are marked `duplicate_unresolved`; **both are never confirmed**. |

### 4. Candidates and subject resolution

Mentions are grouped into **candidate people** by affiliation: the target candidate, and one candidate per other organisation (namesakes). Namesake candidates are reported and **never merged** into the target.

| Subject status | Rule |
|---|---|
| `confirmed` | At least 1 `confirmed` document, no conflict |
| `strongly_supported` | At least 2 `strong` documents on at least 2 independent domains, no conflict |
| `possible` | Some `strong` or `possible` support, below the thresholds above, **or** all support is stale (newest supporting document over 2 years old, while newer documents tie the name elsewhere) |
| `conflicting` | Evidence ties the same person (via a shared profile or known URL) to a different current organisation (a role change), or ambiguous documents carry the best evidence |
| `insufficient_evidence` | No supporting document at all |

**Resolved** means only `confirmed` or `strongly_supported`. Everything else stays **unresolved**, with explicit reasons; nothing is forced into a match.

### 5. Storage and reversibility (migration 0005, reversible)

- `identity_resolutions`: subject status, explanation, details, rule version.
- `identity_decisions`: one row per document, profile or candidate. Holds status, signals, reasons, `decided_by` (`rules` or `user`) and `superseded_by`.
- **Reversal:** `override_decision()` writes a new user decision and supersedes the old one. Re-resolving honours active overrides. The full chain stays auditable.

### 6. Integration

- `research_run` runs the resolver at the end, while document text is still within its 7-day retention.
- Its result is recorded and shown in metrics as an `identity` section.
- **Run status is unchanged:** the resolver is advisory in this phase. Phase 6 will use only `confirmed` and `strong` documents as evidence.
- The planner's provisional signal is documented as planning-only.

## Acceptance criteria

| # | Criterion | Evidence |
|---|---|---|
| I1 | Namesakes are distinguished: same-name people at other organisations or occupations are rejected and reported as separate candidates, never attributed | Labelled cases |
| I2 | A simple name-plus-organisation match alone never resolves the subject (`possible` at most) | Labelled case and a unit test |
| I3 | Outdated roles: past-tense affiliation is `historical`; a linked newer affiliation elsewhere means `conflicting`; stale-only support caps the subject at `possible` | Labelled cases |
| I4 | Misleading company associations (organisation on the page, but tied to someone else or far from the name) don't count | Labelled cases |
| I5 | Duplicate profiles are flagged; two profiles on the same platform are never both confirmed | Labelled cases |
| I6 | Conflicting evidence is surfaced (`ambiguous` documents, `conflicting` subject) with reasons | Labelled cases |
| I7 | Insufficient evidence stays unresolved: **forced-match rate = 0** across all cases labelled unresolved | Evaluation over the labelled set |
| I8 | Independence: syndicated copies and same-domain pages don't inflate support | Labelled case |
| I9 | Decisions are auditable and reversible (user override, supersede chain, re-resolution) | Test |
| I10 | Integrated into research runs; migration 0005 tested on a **copy** of `data/engine.db`; originals untouched | Tests and checksums |
| I11 | Engine, prototype and no-AI-library suites pass; no new keys, paid usage or live personal research | Test runs |

## Test data

Labelled synthetic cases only: fictional people, organisations and `.example` domains. Each case labels the expected status of every document and the expected subject status. There is no live personal research.
