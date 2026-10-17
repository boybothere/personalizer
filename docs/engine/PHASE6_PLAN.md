# Phase 6 plan: evidence-gated claim extraction

**Date:** 2026-10-18
**Status:** **Proposed, for review. No Phase 6 code has been written.**
**Baseline:** `e4199b5` (Phase 5, pushed)

## 0. Scope and numbering

Phase 6 turns retrieved documents into **proposed claims** about the subject: stated preferences, activities and associations. Every claim carries its evidence, and identity resolution and evidence quality gate what can be claimed.

D1.4 put preference extraction in phase 6 and validation in phase 7. Your Phase 6 requirements include work that D1.4 assigned to phase 7: independence of sources, contradictions and staleness. Claims cannot be counted or given a status safely without that work, so:

- **Phase 6 (this plan):** organisation-matching fix, origin groups, claim extraction, identity gate, claim statuses and flags, sensitive-data filter, provenance, labelled evaluation.
- **Phase 7 (later):** validation refinements. These are cross-run corroboration, currency rules beyond the 2-year default, a reviewer workflow for conflicts, and the real-world evaluation once it is approved.
- **Phase 8:** API (unchanged).

**Decision needed (P6-Q1):** approve moving origin groups and claim statuses from phase 7 into phase 6.

Unchanged constraints:
- no LLM calls or AI libraries;
- no new API keys and no paid usage;
- no live-person research; the Tavily fallback stays disabled;
- the prototype and both databases are untouched; migrations are tested on copies only;
- budget, access-policy and retention safeguards stay as they are.

## 1. Problems this phase must solve

| # | Failure mode | Current state (observed) |
|---|---|---|
| P1 | Claims attributed to someone who is not reliably the subject | No claims exist yet. Identity is resolved (Phase 5), but nothing downstream reads it. |
| P2 | Copies counted as independent corroboration | Only near-duplicate text (simhash ≤ 3) is detected. Rewritten wire stories, cross-domain `rel=canonical` copies and shared press-release quotes are counted as independent. Phase 5 counts registrable domains, not origins (PHASE5_RESULTS §4, risk 3). |
| P3 | Organisation-name substring matches | **Reproduced on `e4199b5`.** With subject organisation *Nimbus Compute*, the current rules count all three of these as the target: "analyst at **Nimbus Compute Bank**", "of **Nimbus**, the drone maker" and "of **Nimbus Computers** India". "engineer at **NimbusCompute**" is treated as a different organisation. |
| P4 | Activity, association or mention mistaken for preference | "Attended a jazz evening" is not "likes jazz". "Featured in a page about golf" is nothing at all. |
| P5 | Claims without provenance | n/a. Every claim must carry its URL, date, evidence text and confidence. |
| P6 | Weak evidence presented as fact | Contradictory, stale, undated and insufficient evidence must be visible in the claim's status and flags, not hidden. |
| P7 | Sensitive traits or whereabouts | `policy.screen` checks only research **inputs**. Findings are not yet filtered (D4.1/D4.2 deferred this to phase 6). |

## 2. Design

### 2.1 Organisation matching (`engine/identity/orgmatch.py`; fixes P3; `identity-rules-2`)

Matching is by **tokens, not substrings**. Both sides are normalised: case folded, `&` becomes "and", punctuation removed, and a trailing legal suffix (Inc, Ltd, Pvt Ltd, LLC, Labs only if the subject's name has it, …) dropped. The comparison then gives one of these results:

| Result | Rule | Example (subject *Nimbus Compute*) |
|---|---|---|
| `exact` | The normalised token sequences are equal | "Nimbus Compute Inc.", "NIMBUS COMPUTE" |
| `alias` | Equal to a user-supplied alias, or the concatenated tokens equal the official-domain label | "NimbusCompute" (`nimbuscompute.example`) |
| `partial` | A proper subset or prefix of the target's tokens | "Nimbus" |
| `different` | Any extra or different distinctive token | "Nimbus Compute Bank", "Nimbus Computers India" |

- Only `exact` and `alias` count as the target.
- `partial` is neither target nor namesake. It is recorded as an `org_partial` signal and makes the mention `ambiguous` at most.
- `different` is another organisation.
- The "organisation present in the sentence" check uses the same matcher with word boundaries.
- Phase 5 cases are re-run under `identity-rules-2`, and re-resolution supersedes `identity-rules-1` decisions (D10.6).
- Optional input: `organization_aliases` (for example a former name or a brand).

### 2.2 Origin groups (`engine/origins.py`; fixes P2, also feeds identity)

Documents in a run are joined into **origin groups** using union-find. Each link records why it was made:

1. `duplicate_of` (existing simhash or identical text).
2. Same `canonical_link`, or a cross-domain `rel=canonical` pointing at another document's URL.
3. JSON-LD `isBasedOn` / `sameAs` for the article, or syndication markers in the text ("originally published", "first appeared on", "republished with permission", "(PTI)", "(ANI)", "(Reuters)", "via IANS", …). Documents with the same agency marker and the same dateline day are grouped.
4. **Shared passage:** the two documents share at least one 12-word shingle outside boilerplate, or 30% or more of the shorter document's 5-word shingles. This catches a press release rewritten by several outlets.
5. A press-release wire host (businesswire, prnewswire, globenewswire, …) links to every document that shares a passage with it.

Corroboration, for claims and for identity's "≥ 2 independent" rule (`identity-rules-2`), then counts **origin groups, not URLs or domains**. Same-domain pages remain one source for identity, as in Phase 5.

**Limitation (documented, not solved):** two outlets that paraphrase the same release with no shared 12-word passage and no marker still count as independent.

### 2.3 Claim extraction (`engine/claims/extract.py`)

Extraction is deterministic, sentence-level grammar, run only on documents that **pass the identity gate** (§2.4). Each candidate sentence must be **linked to the subject**:

- the subject is the grammatical subject of the sentence;
- or the sentence is a first-person statement on a page **authored by the subject** (author metadata matches the full name, or it is a confirmed profile page or a user-supplied URL);
- or the sentence is a direct quote attributed to the subject ("…," said Priya Raman / Raman said: "…").

A sentence about another person on the same page is never attributed to the subject.

| Claim type | Grammar (examples) | What it means |
|---|---|---|
| `stated_preference` (+/−) | First person: "I love / enjoy / am a big fan of / my favourite … is / I prefer X to Y"; negative: "I can't stand / don't enjoy / dislike X" | The subject **said** they like or dislike X |
| `activity` | "ran the Bengaluru 10K", "spoke at PyCon India 2024", "wrote about X", "attended", "built", "published" | The subject **did** something. Says nothing about liking it. |
| `association` | "member of", "on the board of", "volunteers with", "mentor at", "sponsor of" | A **relationship** with an organisation or group. Not a preference. |
| `activity_pattern` (derived) | The same activity object in ≥ 3 origin groups across ≥ 2 distinct dates | **"Recurring activity, not a stated preference"**, always labelled that way |
| *(no claim)* | Mere mention ("an event featuring Priya Raman celebrated jazz"), lists, tags, keywords | Counted in metrics only. Nothing is stored about the topic. |

Rejected or downgraded grammar (D6.3) is kept with its reason, never promoted:

| Signal | Outcome |
|---|---|
| Negation scope ("I don't love…", "never liked") | Polarity flipped (negative preference) or rejected if the scope is unclear |
| Hypothetical, wish or modal ("I'd love to try", "might enjoy", "want to learn") | `rejected`: not a statement of current preference |
| Question ("Do I like…?") or conditional ("if I liked…") | `rejected` |
| Deterministic sarcasm markers ("/s", "yeah right", "said no one ever", "😒", or scare quotes around the object) | `rejected` |
| The subject quoting someone else ("As my mentor says, 'I love chaos'") or a retweet / repost marker | `rejected`: not the subject's statement |
| Third party reports a preference ("Priya loves jazz, her colleague said") | Kept as `attribution_mode = reported`, **capped at `tentative`** (P6-Q3) |
| Any construction outside the grammar | `insufficient_evidence` (`unverified_inference`) or dropped, with the reason |

### 2.4 Identity gate (fixes P1)

Claims are attributed to the subject **only if both** conditions hold:

1. The subject's latest resolution is **resolved** (`confirmed` or `strongly_supported`).
2. The source document's identity status is `confirmed`, `strong` or `historical`. A `historical` source sets the `historical_identity` flag on the claim.

In every other case, **no subject-attributed claim is stored**:

| Situation | What is stored |
|---|---|
| Subject `possible`, `conflicting` or `insufficient_evidence` | No claims. Trace records `claims_withheld: identity_unresolved` with counts only (no text, no topic). |
| Document `possible`, `ambiguous`, `insufficient` | Same; reason `document_identity_<status>` |
| Document `rejected` (namesake or hedged) | **Not processed at all**. Nothing about a namesake is extracted or stored. |

Each claim stores the `identity_resolution_id` it was gated on. When identity is re-resolved (new rules, a user override, a new run), the claims are **re-gated**:
- claims that no longer pass are superseded with reason `identity_changed`;
- claims that now pass are created.

Nothing is deleted silently.

### 2.5 Claim status, flags and confidence (fixes P5 and P6)

Claims about the same thing are grouped by `(claim_type, predicate, object_key)`; `object_key` is the normalised object. Evidence is counted by **origin group**.

| Status (D6.1) | Rule (first match wins) |
|---|---|
| `rejected` | The grammar rules in §2.3 rejected it (kept for audit, never shown as a finding) |
| `insufficient_evidence` | Uncertain grammar (`unverified_inference`), or the only evidence is a search snippet that could not be retrieved |
| `conflicting` | Opposite polarity, or incompatible statements, in different origin groups **and** the dates don't order them (missing, equal or conflicting dates) |
| `verified` | First-person `authored` statement on a **confirmed** document, date known, **not stale**, no contradiction |
| `strongly_supported` | ≥ 2 origin groups agree, the newest is dated and not stale, no contradiction, and at least one is `authored` or `quoted` |
| `tentative` | Everything else that survived: a single origin group, `reported`, snippet-only (D3.2), stale, or date missing |

Contradictions that the dates **do** order are not merged away. The newer statement is evaluated on its own. The older one is kept with the `superseded_by_newer` flag, so "used to love coffee, gave it up in 2026" shows both.

**Flags** are always shown and never hidden by the status. Each flag caps the status:

| Flag | Meaning | Cap |
|---|---|---|
| `date_missing` | No `published_at` on any supporting document | `tentative` |
| `date_conflict` | The document's date sources disagree (D3.4) | `tentative` |
| `stale` | Newest support is older than 730 days (`claims.stale_after_days`, configurable) | `tentative` |
| `single_origin` | One origin group | `tentative` unless the claim is `verified` |
| `reported_not_authored` | Only third-party reports | `tentative` |
| `snippet_only` | Search-snippet evidence only | `tentative` (D3.2) |
| `historical_identity` | The source ties the subject to the organisation only in the past tense | none (shown) |
| `superseded_by_newer` | A newer, contradicting statement exists | `tentative` |
| `contradicted` | Unordered contradiction | → `conflicting` |

**Confidence** is an ordinal label, **not a probability**. It is derived only from the status and flags:
- `high` = `verified`, or `strongly_supported` with no flags;
- `medium` = `strongly_supported` with flags;
- `low` = `tentative`;
- `none` = `conflicting`, `insufficient_evidence` or `rejected`.

The rules are documented so that any confidence label can be recomputed from the stored row.

### 2.6 Provenance (fixes P5)

Every evidence row stores:
- `document_id`, `source_url`, `evidence_kind`;
- `published_at`, `published_source`, `date_conflict`;
- `origin_group`;
- **`evidence_text`**: the exact sentence (at most 300 characters), with character offsets into the document text;
- `evidence_sha256`;
- `extraction_rule` (rule id and version);
- `subject_link` (`subject_of_sentence`, `authored_page`, `attributed_quote`);
- role (`supports` or `contradicts`).

A test checks that every `evidence_text` is an exact substring of its document text at the stored offsets.

**Retention (P6-Q2):** `evidence_text` is page text, so it expires with the document text after **7 days** (D4.4). After that it is set to null and the hash and offsets are kept. Claim structure (type, object, status, flags) lives as long as the run (30 days). Deleting a subject or run deletes its claims.

### 2.7 Out-of-scope content filter (`engine/claims/scope.py`; fixes P7; D4.1–D4.3)

This filter runs **before** a claim or any evidence text is stored. A sentence matching it is dropped entirely: no claim, no evidence text, no topic. Only a count by category goes to the trace (for example `dropped_out_of_scope: {health: 1, whereabouts: 2}`).

| Category | Dropped (examples) |
|---|---|
| Sensitive traits (D4.1) | religion or faith practice, political views or affiliation, health and medical, disability, sexuality, caste or ethnicity, finances, legal history, identity numbers |
| Inference from activities | Activities that would reveal a sensitive trait are dropped, not reworded: attending a temple or church event, a party rally, a support group, a clinic; "ran for a cancer charity" keeps nothing about health |
| **Current or upcoming whereabouts** (D4.2) | "I'm in Goa this week", "flying to Delhi tomorrow", "see you in Pune next month", "landing in…", "currently in…", live check-ins |
| **Residence** (D4.2) | "I live in…", "my home in…", "back home in…", "moved to <neighbourhood>", "my neighbourhood", street or locality names |
| Other people (D4.3) | Sentences about family members or relationships |

**Allowed:** a **past, dated** statement by the person about public professional travel, recorded as an `activity` ("spoke at PyCon India, Hyderabad, 2024"). If the date is missing or ambiguous (now vs past), the sentence is dropped.

The filter is lexical and deliberately over-inclusive. A false drop costs a missed claim, while a false keep would be a privacy breach.

## 3. Schema (migration 0006 `claims`, reversible; tested on a copy only)

```sql
CREATE TABLE origin_groups (
    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    origin_group TEXT NOT NULL,              -- id of the group's earliest document
    reasons_json TEXT NOT NULL,              -- [{"link": "shared_passage", "with": "d3"}, ...]
    rule_version TEXT NOT NULL
);
CREATE TABLE claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    resolution_id INTEGER NOT NULL REFERENCES identity_resolutions(id) ON DELETE CASCADE,
    claim_type TEXT NOT NULL CHECK (claim_type IN ('stated_preference', 'activity', 'association', 'activity_pattern')),
    predicate TEXT NOT NULL,                 -- likes, dislikes, spoke_at, member_of, ...
    object_text TEXT NOT NULL,
    object_key TEXT NOT NULL,
    polarity INTEGER CHECK (polarity IN (-1, 1)),
    attribution_mode TEXT NOT NULL CHECK (attribution_mode IN ('authored', 'quoted', 'reported')),
    status TEXT NOT NULL CHECK (status IN ('verified', 'strongly_supported', 'tentative', 'conflicting',
                                           'rejected', 'insufficient_evidence')),
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low', 'none')),
    flags_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    origin_groups INTEGER NOT NULL,
    newest_published_at TEXT,
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded_by INTEGER,
    supersede_reason TEXT                    -- identity_changed, rules_changed, user
);
CREATE TABLE claim_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
    role TEXT NOT NULL CHECK (role IN ('supports', 'contradicts')),
    source_url TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    published_at TEXT,
    published_source TEXT,
    date_conflict INTEGER NOT NULL DEFAULT 0,
    origin_group TEXT NOT NULL,
    subject_link TEXT NOT NULL CHECK (subject_link IN ('subject_of_sentence', 'authored_page', 'attributed_quote')),
    evidence_text TEXT,                      -- nulled at text expiry
    evidence_sha256 TEXT NOT NULL,
    span_start INTEGER NOT NULL,
    span_end INTEGER NOT NULL,
    extraction_rule TEXT NOT NULL,
    text_expires_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_claims_run ON claims(run_id, status);
CREATE INDEX idx_cev_claim ON claim_evidence(claim_id);
```

- `down()` drops these in reverse order.
- Retention: the existing `purge` also nulls `claim_evidence.evidence_text` at expiry. The `secure_delete` scan test is extended to cover it.
- No changes to existing tables.
- **`data/engine.db` (schema v2) is never upgraded.** Migration 0006 is tested on a copy, with checksums.

**Metrics:** `metrics()["claims"]` reports:
- counts by type × status × confidence, and by flag;
- withheld counts by reason (identity, document status);
- dropped counts by out-of-scope category (counts only);
- origin groups vs documents.

It is advisory and does not change the run status, the same as identity.

**Config:** `claims.stale_after_days` (730), `claims.shared_passage_words` (12), `claims.shared_shingle_ratio` (0.30), `claims.pattern_min_origins` (3). All are typed and overridable with `ENGINE_CLAIMS_*` environment variables.

## 4. Test strategy

### 4.1 Labelled synthetic corpus (`tests_engine/claim_cases.py`)

The corpus uses fictional people, organisations and `.example` domains. Each case labels:
- the expected identity outcome;
- for every sentence, its expected class (`stated_preference+`, `stated_preference−`, `activity`, `association`, `mention`, `rejected`, `out_of_scope`);
- the expected claim status, flags and confidence;
- the expected origin groups.

Target size is **≥ 60 cases and ≥ 200 labelled sentences**, covering:

| Group | Cases (examples) |
|---|---|
| G1 Identity gate | Subject `possible` with "I love sailing" → 0 claims. `conflicting` → 0. Namesake page "Priya Raman, the pastry chef, loves baking" → 0, not even processed. Resolved subject, but a page sentence about a co-panellist "Arjun loves cricket" → 0. Override flips identity → claims superseded (`identity_changed`) or created. |
| G2 Syndication and copies | One PTI story on 3 sites → 1 origin group, `tentative`. Two rewrites sharing a 15-word quote → 1 group. Cross-domain `rel=canonical` → 1 group. `isBasedOn` → 1 group. Press-release wire plus 2 rewrites → 1 group. Two genuinely independent interviews → 2 groups, `strongly_supported`. Same domain, 2 pages → still 1 identity source. |
| G3 Organisation substrings | "Nimbus Compute Bank", "Nimbus" alone, "Nimbus Computers India", "Nimbus Compute Labs Ltd", "NimbusCompute" (alias from domain), "NIMBUS COMPUTE", a lowercase mention in running text. Phase 5 labelled cases re-run under `identity-rules-2` (no regressions or documented changes). |
| G4 Preference vs activity vs association vs mention | "I love trail running" (+). "I can't stand golf" (−). "ran the Bengaluru 10K" (activity). "attended a jazz evening" (activity, **not** a jazz preference). "member of the Rotary Club" (association). "an event featuring Priya Raman celebrated jazz" (mention, nothing stored). Hypothetical "I'd love to try surfing" (rejected). Question, sarcasm "/s", the subject quoting a mentor, a repost marker. Third-party "her colleague says she loves jazz" (reported → tentative). Recurring activity across 3 origin groups → `activity_pattern`, never a preference. |
| G5 Provenance | Property over all cases: every claim has ≥ 1 evidence row with URL, document, origin group, date or the `date_missing` flag, and an `evidence_text` that equals `doc.text[span]`. Confidence can be recomputed from status and flags. |
| G6 Evidence quality | Contradiction ordered by dates (2025 "I love coffee", 2026 "I gave up coffee") → newer kept, older `superseded_by_newer`. Contradiction with no dates → `conflicting`. Only a 2019 statement → `stale`, `tentative`. No date → `date_missing`, `tentative`. Date conflict → flag. Snippet-only → `tentative` at most. Uncertain grammar → `insufficient_evidence`. |
| G7 Out of scope | Religion, politics, health ("running helped my depression"), family, finances, legal. Whereabouts: "in Goa this week", "flying to Delhi tomorrow", "see you in Pune next month". Residence: "I live in Koramangala", "back home in Chennai". Allowed: "spoke at PyCon India in Hyderabad in 2024" (past, dated) → activity. Undated travel → dropped. **Assertion:** after the run, no table column and **no byte of the database file** contains the dropped sentence (the method is reused from the retention test). |

### 4.2 Measures on the labelled set

These are **reported as synthetic rule-consistency, never as accuracy.**

| Measure | Required |
|---|---|
| Subject-attributed claims while identity is unresolved or conflicting (forced attribution) | **0** |
| Claims from namesake / rejected documents | **0** |
| Out-of-scope leakage (claims, evidence text or DB bytes) | **0** |
| Copies counted as independent (independence inflation) on the labelled set | **0** |
| Claims without complete provenance | **0** |
| Activities, associations or mentions promoted to `stated_preference` | **0** |
| Per-class precision and recall (D6.3), reported for every class | Reported. Any class below 0.9 is listed with its failures. |

### 4.3 Other tests

- Migration 0006 up, down and up again on a **copy** of `data/engine.db`. Row counts are unchanged and the original's sha256 is unchanged.
- Retention: `evidence_text` nulled at expiry; the database-file byte scan finds nothing.
- Re-gating after a user identity override and after a rule-version change.
- `research_run` integration: the `claims` metrics section is present and advisory, and the run status is unchanged.
- Existing suites: engine, prototype (68), no-AI-library checks, prototype files unchanged vs `c2cf132`, database hashes unchanged.

### 4.4 What real-world validation would require (not done in Phase 6)

Synthetic results show that the rules do what they say. They do not show that the rules are correct on real pages. A real accuracy claim requires:

1. **Approval and legal basis:**
   - DPDP legal review completed (D4.5);
   - written, informed consent from each subject;
   - a retention and deletion plan for the evaluation data.
2. **Subjects:**
   - ≥ 5 consenting people, some with **real namesakes**, some with job changes, and at least one with a common name;
   - no public figures picked without consent.
3. **Independent labelling:**
   - pages and sentences are labelled **blind** by someone who did not write the rules;
   - a second annotator labels a sample, and agreement is reported (Cohen's κ);
   - the label set is frozen **before** the rules are run on it.
4. **Ground truth from the subject:** each subject confirms or denies the claims attributed to them, including whether a preference is current.
5. **Size:**
   - ≥ 300 labelled sentences and ≥ 100 proposed claims across subjects;
   - per-class precision and recall with Wilson 95% intervals;
   - small classes are reported as such, not averaged away.
6. **Cost:**
   - free credits only, within the existing ledger caps;
   - no fallback;
   - an estimate is approved before running.
7. **Reporting:**
   - synthetic and real results are reported in separate tables and never combined;
   - every false attribution is listed with its cause.

## 5. Acceptance criteria

| # | Criterion | Evidence |
|---|---|---|
| C1 | No subject-attributed claim when the subject is `possible`, `conflicting` or `insufficient_evidence`, or when the document is not `confirmed`/`strong`/`historical`. Namesake documents are not processed. | G1; forced-attribution = 0 |
| C2 | Claims are re-gated when identity changes; nothing is deleted silently | G1 override test; supersede chain |
| C3 | Copies and syndication do not count as independent; corroboration counts origin groups (claims and identity) | G2; inflation = 0 |
| C4 | Organisation matching is by tokens: `partial` and `different` never count as the target. Phase 5 cases re-run under `identity-rules-2`. | G3; Phase 5 regression report |
| C5 | Stated preference, activity, association and mention are distinguished; activities never become preferences | G4; promotions = 0; per-class precision and recall |
| C6 | Negation, hypotheticals, questions, sarcasm markers, quoting others and third-party reports are handled as specified | G4 |
| C7 | Every claim has URL, document, origin group, date or `date_missing`, exact evidence text with offsets, rule id, status, flags and confidence | G5 property test |
| C8 | Contradictory, stale, missing-date, date-conflict and insufficient evidence are explicit in status and flags | G6 |
| C9 | Sensitive traits, whereabouts, residence and other people are never stored (no claim, no evidence text, no DB bytes). Past dated professional travel is allowed. | G7; leakage = 0; DB byte scan |
| C10 | Migration 0006 is reversible and tested on a copy only; originals unchanged; retention covers evidence text | Migration and retention tests; checksums |
| C11 | Synthetic results are labelled as synthetic; the real-world validation requirements are documented, not claimed | PHASE6_RESULTS.md |
| C12 | All suites pass; no LLM calls, keys, paid usage, live-person research or fallback | Test runs |

## 6. Decisions needed before implementation

| # | Question | Proposal |
|---|---|---|
| P6-Q1 | Move origin groups and claim statuses from phase 7 into phase 6 | **Yes** (claims cannot be counted honestly without them) |
| P6-Q2 | Retention of claim evidence text | **7 days**, like document text; claim structure 30 days |
| P6-Q3 | Third-party reported preferences | Keep, **capped at `tentative`**, labelled `reported`. The alternative is to drop them. |
| P6-Q4 | Derived `activity_pattern` claims | Allow, always labelled "recurring activity, not a stated preference" |
| P6-Q5 | `identity-rules-2` changes Phase 5 decisions (organisation matching, origin groups) | Accept. Re-resolution supersedes, and the changes are reported case by case. |

## 7. Out of scope for Phase 6

- LLM synthesis, briefings or summaries; UI; API.
- Embeddings or ML classifiers.
- Image or EXIF analysis.
- Inferring personality, traits or sensitive categories.
- Cross-run claim merging.
- Live research of any person.
