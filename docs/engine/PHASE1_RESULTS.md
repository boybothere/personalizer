# Phase 1 results: observability, storage and retrieval

**Date:** 2026-10-17
**Scope:** everything from leads to documents. That covers canonicalisation, URL policy, robots.txt, fetching, extraction, date provenance, near-duplicate marking, metrics derived from stored records, quality gates, budgets, cancellation, a versioned database, retention and deletion. Search, connectors, identity, preference extraction and validation are later phases.

## Test results (actually run)

| Suite | Command | Result |
|---|---|---|
| Engine, deterministic | `pytest tests_engine` | **121 passed**, 1 skipped (the live test, skipped by design) |
| Engine, live smoke test | `ENGINE_LIVE=1 pytest tests_engine/test_live.py` | **1 passed** (free, keyless public URLs) |
| Prototype, unchanged | `pytest tests` | **68 passed** |
| Prototype integrity | `sha256sum -c` against the baseline taken before any work began | **54 of 54 files and `data/personalizer.db` unchanged** |

## Acceptance criteria covered by this phase

### B. Retrieval

| Criterion | Evidence |
|---|---|
| Search results are not treated as fetched pages | `test_search_snippets_stay_leads_with_limitations`: snippet leads are stored with their limitations; 0 documents; fetch attempts count only real requests |
| Retrieval and extraction are measured separately | `test_http_success_is_not_extraction_success_and_zero_usable_fails_the_run`: 2 × HTTP 200, 0 usable (`JS_REQUIRED`, `TEXT_TOO_SHORT`), run **failed** |
| Fetch failures carry specific reasons | `test_status_and_type_failures` (8), `test_robots_semantics` (5), `test_redirect_targets_are_rechecked` (3), `test_static_policy_codes` (12), timeout, connection, rate-limit and size tests. Every code has an explanation (`test_every_failure_code_is_explained`). |
| Real extracted content from accessible sources | Live run `run_08385c19583b4b24` below |

### E. Operational integrity

| Criterion | Evidence |
|---|---|
| No LLM calls | `test_no_llm.py`: (1) no LLM SDK imports in the engine source; (2) the whole package imports and a full run completes in a fresh interpreter where LLM SDKs **cannot be imported**, with no request to any LLM host; (3) no LLM SDK in `requirements-engine.txt`; (4) the engine does not import the prototype |
| Searches, fetches, usable documents and failures have separate metrics | `test_metrics_equal_what_the_server_actually_received`: the engine's count of fetch attempts and robots.txt requests **equals the requests the mock server received** |
| Limits and budgets enforced | Separate tests for fetch attempts, retries counting against the budget, leads, bytes, wall-clock time (including requests in flight) and cancellation |
| A run is auditable from input to evidence | `test_run_is_auditable_from_input_to_document`: query → lead → canonical URL → attempt (after a redirect) → document, plus an ordered trace and stored input and config |

### F. Maintainability (partially covered)

The URL policy, fetcher, extractors, storage and metrics are separate modules, each with its own tests. Migrations upgrade, downgrade, re-upgrade and roll back on failure (`test_migrations_are_reversible`, `test_downgrading_a_later_migration_preserves_earlier_data`, `test_failed_migration_rolls_back`).

### Regressions for the prototype failures found in the audit

| Audit item | Test |
|---|---|
| C1 "0 pages fetched" accounting / C10 no quality gates | `test_http_success_is_not_extraction_success_and_zero_usable_fails_the_run`, `test_no_leads_fails` |
| C2 every finding undated | `test_url_pattern_date_used_when_page_has_none`, plus the JSON-LD, meta and feed date tests |
| C3 the organisation's own pages crowding out other sources | `test_org_pages_do_not_crowd_out_other_sources` |
| C8 failed requests counted as completed | `test_failed_requests_are_not_counted_as_completed` |

## Live demonstration: `run_08385c19583b4b24` (stored in `data/engine.db`)

13 public URLs, chosen so that each one exercises a different outcome. No personal research was involved.

| URL | Outcome | Requested? |
|---|---|---|
| rfc-editor.org RFC 9309 | usable page, 10,315 characters, **date unknown** | yes |
| en.wikipedia.org/wiki/Robots.txt | usable page, 24,139 characters, date 2002-10-09 (JSON-LD), flagged `living_document` | yes |
| same URL with `?utm_source=demo` | duplicate lead | no |
| github.blog/feed | 10 feed entries with full content and dates | yes |
| blog.python.org feed (served as `application/octet-stream`) | sniffed as a feed; 50 dated entries, **50 flagged `feed_summary_only`** | yes |
| Wikipedia `/w/index.php?...action=history` | `ROBOTS_DISALLOWED` | no |
| example.com | `TEXT_TOO_SHORT` (HTTP 200, 156 characters) | yes |
| w3.org dummy.pdf | `UNSUPPORTED_CONTENT_TYPE` | yes |
| httpbin.org/status/404 | `HTTP_NOT_FOUND` | yes |
| instagram.com/instagram | `URL_PLATFORM_DISCOVERY_ONLY` | no |
| x.com/jack/status/20 | `URL_PLATFORM_CONNECTOR_ONLY` (connector disabled) | no |
| 127.0.0.1:8080 | `URL_PRIVATE_ADDRESS` | no |
| spokeo.com | `URL_EXCLUDED_DOMAIN` | no |

**Run totals:** 13 leads → 12 unique URLs (4 rejected by policy) → 7 fetch attempts → 5 URLs retrieved → 63 documents, 58 usable, 1 near-duplicate. Status **completed**, stop reason `all_work_processed`. Inspect it with `python -m engine summary run_08385c19583b4b24 --trace`.

## Defects found and fixed during this phase

| Found by | Defect | Fix |
|---|---|---|
| Unit test | htmldate reported a page's *modified* date as its publication date | A heuristic date equal to the modified date is ignored when no other source supports it |
| **Live run** | htmldate dated RFC 9309 (published September 2022) as 2020-09-17 | Heuristic dates are never accepted on their own, only as corroboration |
| **Live run** | Wikipedia's `datePublished` is the article's creation date | `living_document` flag when the page was modified more than a year after publication |
| **Live run** | Feeds that carry only summaries counted as full content | `feed_summary_only` flag; full posts are to be retrieved as follow-up leads (phase 3) |
| Integration test | Requests were still sent after the byte budget ran out | Requests are refused once no bytes remain |
| Code review | robots.txt redirects were not checked against the address policy, and the body had no size cap | Redirect targets re-checked; body capped at 500 KiB |
| Code review | A URL that was retried and then stopped by the budget counted as processed | Processing is judged by each URL's final attempt only |

## Known limitations of this phase

- **DNS rebinding:** the address check resolves the host before the request; httpx then resolves it again, so the IP isn't pinned. This is a partial SSRF defence.
- **No JavaScript rendering:** script-built pages are reported as `JS_REQUIRED`. Detection is heuristic, so some JavaScript-only pages may come through as `TEXT_TOO_SHORT`.
- **No PDF text yet:** PDFs are reported as `UNSUPPORTED_CONTENT_TYPE`.
- **Feed summaries count as "usable" records** but are flagged, so later validation must treat them as weaker evidence.
- **Single-process SQLite** with writes on the event loop. Fine at this scale; it would need a writer queue for heavy concurrency.
- **The time-limit check runs every 0.25 s**, so a run can overshoot `max_wall_seconds` by that much.

## Next phase (2): search layer, pending your input

This phase needs approval to spend credits for live testing: Brave, Exa or Tavily. It also needs answers on the "Default, confirm" items in [DECISIONS.md](DECISIONS.md).
