# Architecture

A single Python process: FastAPI plus a sequential pipeline, with SQLite for storage. There are no agent loops, queues or microservices; each stage is a plain function or class that can be replaced. The LLM is used in at most three bounded places: query suggestions, per-document extraction and brief writing. Each has a deterministic fallback.

## Pipeline

| # | Stage | Module | What it does | Fails safe by |
|---|---|---|---|---|
| 1 | Input + policy | `models.ResearchRequest`, `safety.request_policy` | Validate fields; refuse purposes aimed at sensitive or private data | 422 / exit code 2 |
| 2 | Identity anchors | `identity.Anchors` | Name forms, organisation core name, org domain, role terms, profile URL, location | — |
| 3 | Query generation | `queries.py` (+ `pipeline._llm_queries`) | Operator templates: exact name, name + org, interview/podcast/keynote, conference/speaker, article/paper, meeting topics, official domain, company news. The LLM may add ≤3 queries. | Every query passes `query_policy` (no `filetype:`/`inurl:`/`intitle:`, credential or personal-data terms); rejections are logged |
| 4 | Search | `providers.py` | Each provider renders operators in its own dialect (Brave: `site:`; Tavily/Exa: domain lists) and excludes restricted platforms and brokers at the API level | Provider errors become warnings; rate limits stop that provider |
| 5 | Source policy | `sources.SourcePolicy` | Block restricted platforms and data brokers; assign tiers: official / reputable / professional / general | Blocked sources are listed in *Excluded material* |
| 6 | Retrieval + sanitising | `fetch.py` | Use provider-supplied text, or fetch: public IPs only (re-checked on every redirect), robots.txt, 2 MB / 10 s caps, HTML only, no cookies. Strip scripts, comments, hidden elements, zero-width characters. | Paywalls (401/402/403/451) and robots disallows are skipped, never bypassed |
| 7 | Identity resolution | `identity.assess` | Score each page: full name, page-title match, org mention and proximity, official domain, profile link, role, location; minus conflicts (name tied to a different employer) and hedged attributions ("some say this is…") | Pages below MEDIUM are not read further |
| 8 | Extraction | `extract.py` | Heuristic (sentence rules) or Claude (structured output). Instruction-like sentences are replaced with a marker first. | LLM errors or budget → heuristic for that page |
| 9 | Verification | `extract.verify_excerpt`, `safety.sensitive_reason` | The excerpt must occur **verbatim** (after whitespace/punctuation normalisation) in the sanitised source; claims about other people and sensitive claims are dropped | Dropped claims are logged without their text |
| 10 | Corroboration + conflicts | `scoring.corroborate`, `detect_role_conflicts` | Similar claims on independent registrable domains corroborate; near-identical excerpts count as one source (syndication); different current titles or employers are flagged and the older one is downgraded | Conflicts become warnings |
| 11 | Confidence | `scoring.score` | Rule-based score (see below) | — |
| 12 | Preference tiers | `scoring.preference_tiers` | Explicit (own words) → repeated (corroborated) → hypothesis | Hypotheses are never used for personal common ground |
| 13 | Relevance + rank | `scoring.relevance`, `rank` | Overlap with meeting-topic terms (with a small synonym table) plus a category prior; rank = relevance, confidence, freshness, reliability | — |
| 14 | Suitability | `scoring.suitability` | Excludes: user-excluded categories, personal interests when disabled, outdated or contradicted time-sensitive facts, weak identity, low confidence, hobby hypotheses | Reason recorded on each finding |
| 15 | Personalisation | `personalize.py` | Template writer, or Claude given **only verified findings** (never raw pages) | LLM error → template |
| 16 | Validation | `personalize.validate` | Every item must cite ≥1 *suitable* finding; drops items that fail the tone guard (fake familiarity, covert-research phrasing) or the sensitivity check; hobby common ground needs an explicit or repeated tier | Drops are listed under *Warnings* |
| 17 | Report + storage | `report.py`, `store.py` | Markdown/JSON with numbered citations; SQLite with retention, deletion, feedback, opt-out | — |

## Data model (key fields)

`Finding` (in `models.py`) carries everything the brief asked for:

| Requirement | Field |
|---|---|
| Claim / observation | `claim` |
| Category | `category` (11 values) |
| Source URL and name | `source_url`, `source_name`, `source_tier` |
| Supporting excerpt | `excerpt` (verbatim; verified) |
| Publication date | `published_at` (`null` = unknown) |
| Retrieval timestamp | `retrieved_at` |
| Identity-match confidence | `identity_score`, `identity_confidence` |
| Source reliability | `source_reliability` (tier-based; lowered when the page contained hidden or instruction-like text) |
| Evidence strength | `evidence_strength` (corroboration × identity × reliability) |
| Relevance | `relevance`, `relevance_reason` |
| Stated vs inferred | `disclosure`: `explicitly_stated` / `observed_activity` / `third_party_report` / `inferred` |
| Preference tier | `preference_tier`: explicit / repeated / hypothesis / n/a |
| Suitable for personalisation | `suitable_for_personalization` |
| Reason excluded or downgraded | `exclusion_reason`, `downgrade_reasons` |
| User feedback | `feedback` |

`SourceDocument.text` is excluded from serialisation, so raw page text is never stored or returned. `ExcludedItem` records dropped material by URL and reason only.

## Confidence

Confidence is a **transparent ordinal score, not a probability**, and is never taken from an LLM:

```
score = 0.35·identity + 0.25·reliability + 0.15·corroboration + 0.10·freshness + 0.15·disclosure
        − 0.12 per conflict / contradiction / outdated flag
high ≥ 0.75 · medium ≥ 0.55 · low otherwise
```

- *identity*: page-level identity score (0–1)
- *reliability*: official/reputable 1.0, professional 0.7, general 0.4
- *corroboration*: 0.5 + 0.25 per independent domain (max 1)
- *freshness*: ≤1 year 1.0, ≤3 years 0.7, ≤5 years 0.4, older 0.2, unknown 0.5
- *disclosure*: own words 1.0, observed activity 0.85, third party 0.5, inferred 0.2

**How it should be validated.** Treat each level as a claim of the form "findings marked *high* are right X% of the time". `GET /api/feedback/stats` and `eval/run_eval.py` report precision per level against labels. Adjust the weights only when labelled data shows the levels out of order (for example *medium* more precise than *high*) or poorly separated. With enough labels (a few hundred), fit a logistic model on these same features and report reliability curves. Only then could a calibrated probability be shown.

## Prompt-injection defence in depth

1. **Sanitise:** scripts, comments, hidden elements (`display:none`, `aria-hidden`, `sr-only`, …) and zero-width characters are removed before any text is used.
2. **Redact:** sentences matching instruction patterns are replaced with `[removed: instruction-like text]`, and the page's reliability is lowered.
3. **Isolate:** each page goes to the model alone, inside `<untrusted_document id=NONCE>` tags, with a system prompt saying the content is data. Pages are never mixed together.
4. **Verify:** an extracted excerpt must exist verbatim in the sanitised page and must not contain the redaction marker, so injected "facts" without real text behind them fail.
5. **Separate synthesis:** the brief writer sees only verified findings, never pages.
6. **Validate output:** citations must point to suitable findings; tone and sensitivity checks run on the final text.
7. **No tools:** the model has no tool access; it can't browse, fetch or act.

A residual risk remains: a page that states a false fact plainly (not as an instruction) will be extracted like any other fact. Corroboration and source tiers reduce this risk; they don't eliminate it.

## Design decisions

| Decision | Why | Alternative considered |
|---|---|---|
| Sequential pipeline, no autonomous agent | Predictable cost, auditable, testable; the brief asked for agents only where they demonstrably help | An LLM agent choosing queries and pages: higher recall at higher cost, with a larger injection surface. Worth trialling as a bounded "follow-up query" step once there is an evaluation set with real recall labels. |
| Heuristic mode always available | Runs with no paid APIs; deterministic tests; fallback on LLM failure | LLM-only (simpler, but every run costs money and nothing works offline) |
| Excerpt-first evidence | Makes hallucinated evidence mechanically detectable | Trusting model citations |
| Hobbies never searched for directly | Data minimisation: personal interests surface only incidentally from professional sources | A dedicated hobby query, which would be more intrusive |
| SQLite + stdlib | Zero setup for a local prototype | Postgres, once multi-user |
| `httpx` + BeautifulSoup | Small, well-known dependencies | trafilatura/readability give better main-text extraction but pull in heavier dependencies |
| Claude Opus 5.5 default, model configurable | Strongest extraction and synthesis quality | Haiku 5.5 is about 40× cheaper for extraction and should be tested against the evaluation set |
