# Phase 2 results: search layer, provider metrics and access policy

**Date:** 2026-10-17
**Scope:**
- provider-independent search specs and per-provider rendering;
- Brave, Tavily and Exa adapters with error classification and retries;
- a cross-run spend ledger with a free-tier guard;
- provider-level cost and quality metrics;
- storage driven by each provider's terms;
- the provider-extract fallback, used only after technical failures;
- automatic retention, with deletion enforced and verified on disk;
- migration 0002.

Not included: planner, adaptive controller, connectors, identity, preference extraction, LLM, UI.

## 1. Tests (actually run)

| Suite | Result |
|---|---|
| Engine, deterministic (`pytest tests_engine`) | **160 passed**, 1 skipped (the live test, skipped by design) |
| Engine, live retrieval (`ENGINE_LIVE=1`) | **1 passed** |
| No-LLM checks (static imports, blocked-SDK subprocess run, requirements) | **4 passed** |
| Prototype (`pytest tests`) | **68 passed** |
| Prototype integrity (sha256 baseline) | **54 of 54 files and `data/personalizer.db` unchanged** |

The 38 new deterministic tests in `tests_engine/test_search.py` (plus regressions in the other files) cover:

- **Rendering:** each provider's query rendering, including notes on what it can't express.
- **Site matching:** path-prefix boundaries and exclusions.
- **Accounting:** the metrics equal the requests the fake APIs received; reported costs are recorded (Exa `costDollars`, Tavily `usage.credits`).
- **Budgets:** the search-count limit and the **$0.25 cost ceiling are enforced before sending**.
- **Free-tier guard:** with the ledger at its cap, **0 requests are sent**.
- **Provider failures:** quota exhaustion (432) disables the provider and later queries are skipped, not sent. 429 is retried; 401, 5xx, bad JSON and 422 are classified.
- **Missing keys:** with no key configured, the run is FAILED with `SEARCH_NOT_CONFIGURED` and no requests.
- **Off-target results** are counted and never used.
- **Diminishing returns** are measured per query (`new_urls_by_query`).
- **Storage policy:** Brave and Exa titles and snippets never reach the database file. URLs that are transient-only and unrequested are withheld, while URLs we fetched ourselves are kept. **API keys never appear in the database file.**
- **Fallback:** used after a timeout or a JavaScript-only page; **never** after a robots.txt denial, robots.txt unreachable (5xx), 403, 404 or 429.
- **Deletion:** removes all search data, verified by scanning the DB and WAL files with `secure_delete` on, while the **spend ledger survives**. Retention runs automatically at the start of each run.
- **Live-run regressions:** month-name URL dates, `?ref=` referral tracking, extract credit accrual.

## 2. Live search results (Tavily only; free credits)

**Only the Tavily key is configured.** The Brave and Exa lines in `.env` contain only the template comment; my Phase 1 audit wrongly counted them as keys. Brave and Exa are therefore verified against mocks only.

### Run `run_500282dbae8a442b`: 6 specs from [live_specs_phase2.json](live_specs_phase2.json)

| Spec | Results | What it showed |
|---|---|---|
| Exact phrase, `site:rfc-editor.org` | 5 on target | 3 pages usable directly; 1 JavaScript-only and 1 over 3 MB, **both recovered by the Tavily fallback** (technical failures) |
| `trafilatura` restricted to the path `github.com/adbar` | 5, **1 off-target** | Tavily filters by domain only; the client-side post-filter dropped `github.com/agno-agi/...` |
| "National Geographic" restricted to `instagram.com` | 5 | All 5 became **discovery-only leads**: snippets stored with their limitations, **0 requests to Instagram** |
| "Python 3.15", last 30 days | 5 | All usable; dates from JSON-LD and the URL |
| "RFC 9309" with an OR group | 5 | Tavily has no documented OR (the rendering notes say so); 1 Medium page returned 403 |
| "robots.txt" restricted to `medium.com` | 5 | **5 of 5 returned 403** to our bot although robots.txt allows them. Not bypassed; the fallback is not used after a 403. Medium needs its RSS route. |

**Totals:** 6 queries, 30 results (28 unique) → 30 fetch attempts → 16 URLs retrieved → 18 documents, 17 usable (2 via provider extract). Status **completed**.

### Run `run_b89c5ffb1ac640f8`: verification after fixes (3 specs)

- 15 results; the `?ref=` duplicate is gone (3 unique GitHub URLs instead of 4).
- `simonwillison.net/2026/Oct/12/...` is now dated 2026-10-12 from its URL.
- 22 fetch attempts, accounted for exactly: 12 successes, 8 trailing-slash redirects, 1 retried timeout, 1 page over the size limit.

### Provider quality, aggregated across both live runs (Tavily)

| Metric | Value |
|---|---|
| Queries completed / attempted | 9 / 9 (no retries needed) |
| Latency | average 2.2 s, maximum 3.6 s |
| Results / off-target rate | 45 / 4.4% |
| Unique on-target URLs | 40 |
| Rejected by policy | 5 (discovery-only platform) |
| URLs retrieved by us | 28 |
| Usable unique documents (of which provider extracts) | 29 (4) |
| Usable documents with a reliable date | 14 of 29 (48%) |
| **Dates supplied by Tavily** | **0 of 60 results**, even with `include_published_date` |
| Usable documents per request | 3.2 |
| Estimated cost per usable document | $0.0026 (list price; actually free credits) |

## 3. Actual costs

| Source | Value |
|---|---|
| Money billed | **$0**: Researcher (free) plan; `paygo_usage: 0` before and after |
| Credits reported in Tavily's per-response `usage` fields | 9 search credits (1 per search) |
| Extract credits | 4 successful extractions, accrued at 0.2 each = 0.8 credits (Tavily reported 0, because it bills per 5 successes) |
| Engine ledger this month | **9.4 credits** recorded, conservatively; estimated list value $0.075 |
| Tavily account counter (`GET /usage`) | **2 before and 2 after**. It didn't reflect this session's usage while I was checking. **I can't reconcile it**; the per-response figures are the best available measurement. |
| Brave, Exa | Not used (no keys) |
| Per-run ceiling | Never approached; the highest single run cost $0.0512 estimated |

## 4. Access-policy decisions made in this phase

See [DECISIONS.md](DECISIONS.md), D3.3, D4.4, D5.4 and D7.

1. **Storage follows the provider's terms:**
   - Brave: transient (ToS s3(b)(i)).
   - Exa: transient, pending review (ToS s4.2(a)).
   - Tavily: persist, with 7-day snippet expiry. Its AUP bans surveillance, doxxing and obtaining special-category data.
2. **The provider-extract fallback runs only after technical failures**, and only from a provider whose terms allow storage (Tavily). It is refused after any robots.txt, access-control or policy outcome on the URL, as defence in depth.
3. **Free allowances only:** a cross-run ledger with monthly caps below each free allowance. Requests that would exceed a cap are never sent.
4. **Instagram, Facebook and LinkedIn are discovery-only;** X and Reddit stay disabled until their terms are reviewed. This is unchanged and enforced by URL policy.
5. **Medium:** the observed 403s are recorded as evidence; the content route is RSS.
6. **API keys** are excluded from stored configuration snapshots (`safe_dump`). This leak would have occurred as soon as keys entered the config; it was caught before any run stored one.

## 5. What changed: no VCS, so a file-level change list

The repository has no git, so a line-level diff against Phase 1 isn't available. Recommendation: initialise git before Phase 3.

**New:**

| File | Lines | Contents |
|---|---|---|
| `engine/search/spec.py` | 78 | Search specs, site matching |
| `engine/search/base.py` | 200 | Provider interface, executor, error classification |
| `engine/search/providers.py` | 140 | Brave, Tavily and Exa adapters |
| `engine/search/ledger.py` | 60 | Cross-run spend ledger |
| `engine/search/runner.py` | 172 | Search runner |
| `engine/search_run.py` | 128 | Orchestration, Tavily fallback, transient scrub |
| `tests_engine/test_search.py` | 397 | Search-layer tests |
| `docs/engine/DPDP_ANALYSIS.md` | | Draft for legal review |
| `docs/engine/PHASE2_RESULTS.md` | | This document |
| `docs/engine/live_specs_phase2.json` | | Live test specs |

**Modified:**

| File | Change |
|---|---|
| `engine/store/migrations.py` | Migration 0002: `search_queries`, `search_results`, `provider_ledger`, new usage and lead columns; reversible |
| `engine/store/repo.py` | Purge clears snippets; WAL checkpoint after deletes |
| `engine/store/db.py` | `secure_delete` |
| `engine/config.py` | `Providers` settings; `safe_dump` |
| `engine/failures.py` | 11 `SEARCH_*` codes |
| `engine/observe.py` | Search metrics, `provider_metrics`, search gates |
| `engine/retrieval.py` | `process`/`finish` split, storage policy on leads, fallback hook |
| `engine/urls.py` | Referral `ref`/`source` parameters treated as tracking |
| `engine/extract/dates.py` | Month-name URL dates |
| `engine/platforms.py` | Medium evidence |
| `engine/cli.py` | `search`, `ledger`, `providers`, `tavily-usage` |
| `tests_engine/test_urls_and_store.py`, `tests_engine/test_extract.py` | Version-agnostic migration tests and regressions |
| `docs/engine/DECISIONS.md`, `docs/engine/PLATFORMS.md`, `docs/engine/README.md` | Approvals, D7 and the Medium evidence |

**Database:** `data/engine.db` was upgraded additively from version 1 to 2; the Phase 1 runs are intact.

## 6. Remaining limitations

- **Single provider live-tested.** Brave's `site:` and path behaviour, its quota headers, and Exa's real `costDollars` and result quality are unverified live.
- **Tavily's own usage counter didn't move** during the session, so actual cost is measured from per-response fields and the conservative ledger, not from the account.
- **Tavily supplies no publication dates in practice** (0 of 60), so dates depend on our own extraction: 48% of usable documents were dated.
- **The ledger only sees this engine's usage.** Usage elsewhere with the same keys (for example the prototype) isn't visible, which is why the caps sit at 90% of each allowance.
- **The billing cycle may not be the calendar month.** The ledger uses the UTC calendar month; Tavily's cycle may differ.
- **Transient-provider scrubbing happens at the end of the run.** Brave and Exa URLs are briefly present in the database during a run (the transient operational storage Brave's terms allow). A crash mid-run could leave them until the next run's purge. Their titles and snippets are never stored at all.
- **Medium and other bot-protected sites** return 403. We don't bypass this; the RSS connector (phase 4) is the planned route.
- **About 36% of requests in the verification run were trailing-slash redirects** from servers; harmless but wasteful.
- **Search is sequential across providers** (simpler accounting and politeness); slower than it could be.
- **The planner and adaptive stopping** (coverage, diversity, diminishing returns) are phase 3. Phase 2 measures the signals (`new_urls_by_query`, off-target rate, yield) that the controller will use.
- **The DPDP analysis is a draft**; legal review is required before production use.
