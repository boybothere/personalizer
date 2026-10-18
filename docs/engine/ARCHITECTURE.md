# Research engine: revised architecture

The engine is deterministic and evidence-first, with **no LLM in the runtime**. It lives in a new package, `engine/`. The prototype in `personalizer/` is untouched and nothing imports it. Decisions are in [DECISIONS.md](DECISIONS.md) and platform evidence is in [PLATFORMS.md](PLATFORMS.md).

## Data flow

```
ResearchInput ─► Planner ─► objectives (inspectable; preview without running)
                               │
            ┌──────────────────▼──────────────────────────────────────────────┐
            │ Controller loop                                                 │
            │  choose objective (gaps, value, saturation) ─► query + reason   │
            │  ─► Search providers ─► LEADS (snippets: evidence kind          │
            │      search_snippet)                                           │
            │  ─► Connectors (APIs, feeds, oEmbed) ─► records                 │
            │  ─► URL policy (canonicalise, dedupe, platform mode, SSRF)      │
            │  ─► Retrieval (robots, async fetch, retries) ─► fetch attempts  │
            │  ─► Extraction (main text, metadata, dates + where they came   │
            │      from) ─► documents                                        │
            │  ─► Identity (per mention; status ladder) ─► decisions          │
            │  ─► Evidence (preference grammar, activities) ─► claims         │
            │  ─► Validation (origin groups, conflicts, currency) ─► status   │
            │  ─► new leads / handles / corroboration objectives ─────────────┘
            │  budgets checked before every action; every action traced
            ▼
     Stop (saturated / covered / exhausted / budget / timeout / cancelled)
     ─► Run summary: metrics derived from stored records, quality gates, coverage, trace
```

## Modules

| Module | Phase | Responsibility | Tests |
|---|---|---|---|
| `engine/config.py` | 1 | Typed configuration: budgets, timeouts, thresholds; read from `ENGINE_*` environment variables | Parsing, validation |
| `engine/failures.py` | 1 | Failure codes with human-readable explanations | Every code documented |
| `engine/store/` | 1 | SQLite, **versioned and reversible migrations** (`up`/`down`), repositories, retention, deletion | Upgrade, downgrade, re-upgrade, data preserved, cascade delete |
| `engine/observe.py` | 1 | Stage timing, trace events, **metrics derived by SQL from stored records**, quality gates | Metrics equal row counts; zero usable documents ⇒ FAILED |
| `engine/budget.py` | 1 | Enforced hard limits and cancellation | Every limit stops work and records the reason |
| `engine/urls.py` | 1 | Canonicalisation, URL key, policy (scheme, credentials, private IPs, excluded domains, platform modes) | Canonicalisation table, policy codes |
| `engine/platforms.py` | 1 | Platform mode registry (from PLATFORMS.md) | Every mode has evidence and a date |
| `engine/fetch/netpolicy.py`, `engine/fetch/pinned.py`, `engine/fetch/challenge.py`, `engine/fetch_trace.py` | 4 (**done**) | Address policy (embedded IPv4, non-canonical hosts); one resolution per hop with **pinned connections** (Host/SNI on the hostname, fail closed, peer verified, no proxies, no keep-alive); bot-challenge detection; bounded decompression; per-URL fetch trace | 73 tests, F1-F13 (see PHASE4_RESULTS.md) |
| `engine/fetch/` | 1 | robots.txt cache (RFC 9309), async fetcher: per-host and global concurrency, connect/read/total timeouts, retries with backoff honouring `Retry-After`, manual redirects re-checked on every hop, size caps, content-type routing | Redirects, timeouts, retries, 429, robots, oversized, wrong type, SSRF hop |
| `engine/extract/` | 1 | HTML sanitising, trafilatura main text and metadata, JSON-LD and OpenGraph parsing (`sameAs`, author, dates), **date resolver with source**, feed parsing, usable-text and JavaScript-only detection, content hashes and simhash | Missing, conflicting and URL-only dates; feeds; JS shells; boilerplate |
| `engine/retrieval.py` | 1 | Leads → URLs → attempts → documents, all persisted and traced | End-to-end with recorded fixtures |
| `engine/search/` | 2 | Provider interface with declared capabilities and cost; query rendering per provider; leads with snippet provenance | Rendering, accounting, snippets never become documents |
| `engine/plan.py`, `engine/controller.py`, `engine/identity_signal.py`, `engine/policy.py`, `engine/research_run.py` | 3 (**done**) | Objectives and variants; scored adaptive queue; follow-ups (weak identity, official domain, handles, diversity); provisional identity signal; stop rules; input screening | 34 tests, A1-A14 (see PHASE3_RESULTS.md) |
| `engine/connectors/` | 4 | Bluesky, Mastodon, RSS, GitHub, YouTube, iTunes, Wikidata; X and Reddit oEmbed (off by default) | Recorded responses; mode enforcement |
| `engine/identity/` | 5 (**done**) | Mention-level evidence (organisation *attached* to the name, past tense, role, occupation, hedging); per-document statuses; profiles with duplicate handling; subject statuses with conflicts and staleness; reversible user overrides. Advisory in runs. | 29 tests, 20 labelled cases, I1-I11 (see PHASE5_RESULTS.md) |
| `engine/privacy.py`, `engine/maintenance.py` | 6 (corrective pass) | Write-boundary filter used by every persistence function (text, titles, snippets, URLs, identity labels), with boundary-preserving placeholders and a counts-only identity-safety signal; start-of-run maintenance (interrupted-run recovery, purge with checkpoint retry, outcome recorded on the run) | `test_privacy_writes.py`, `test_identity_safety.py`, `test_url_withholding.py`, `test_recovery.py`, `test_retention.py`, `test_temp_artifacts.py` |
| `engine/origins.py`, `engine/identity/orgmatch.py`, `engine/claims/` | 6 (**done**) | Origin groups (conservative copy and syndication detection); token organisation matching (`identity-rules-2`); identity-gated claims (stated preference, activity, association, recurring activity) with status, flags, ordinal confidence and full provenance; out-of-scope filter and redaction of stored text | 101 tests, 63 development and 24 held-out labelled cases, C1-C17 (see PHASE6_RESULTS.md) |
| `engine/validate/` | 7 | Origin groups, corroboration, conflicts, temporal currency, status rules with explanations | Syndication, conflict and staleness cases |
| `engine/api.py` | 8 | Runs: plan preview, start, progress, cancel, filters, trace, delete | API tests |

## Storage (migration 0001, phase 1)

`schema_migrations`, `subjects`, `runs` (status, stop reason, config snapshot, quality gates), `stage_events`, `trace_events`, `leads` (with evidence kind and snippet provenance), `urls` (decision plus reject code; unique per run and URL key), `fetch_attempts` (one row per attempt, retries included), `documents` (evidence kind, final and canonical URL, metadata, `published_at` + `published_source` + candidates, hashes, usable flag or failure code, text with expiry), `provider_usage`.

Later phases add tables through new migrations: 0002 search (done), 0003 planner (done: `research_objectives`, `planner_decisions`), 0004 `fetch_attempts.connected_ip` (done), 0005 identity (done: `identity_resolutions`, `identity_decisions`), 0006 claims (done: `origin_groups`, `claims`, `claim_evidence`), then evidence and claims, validation. Each migration has a tested `down()`. **Existing databases are only migrated explicitly** (`engine db upgrade`).

## Metrics (derived; never hand-counted)

| Metric | Source |
|---|---|
| Queries planned / attempted / completed, results returned | `queries`, `search_results` (phase 2; reported as *not applicable* before then) |
| Leads; unique candidate URLs; URLs rejected, by code | `leads`, `urls` |
| Fetch attempts; HTTP successes (2xx); blocked or inaccessible, by code; network failures | `fetch_attempts` |
| Documents with usable text; extraction failures, by code; near-duplicates | `documents` |
| Documents passed to validation; evidence sources kept | `documents.usable`; phase 7 |
| Time per stage | `stage_events` |
| Provider usage and cost | `provider_usage` |
| Status, stop reason, gate results | `runs` |

## Not in this rebuild phase

- No LLM synthesis or briefing.
- No UI work.
- No headless browser: pages that need JavaScript are recorded as `JS_REQUIRED`.
- No PDF text: recorded as `UNSUPPORTED_CONTENT_TYPE` (`pypdf` is a candidate for later).
- No transcript scraping.
