# Phase 3 results: adaptive search planner and stopping logic

**Date:** 2026-10-17
**Plan:** [PHASE3_PLAN.md](PHASE3_PLAN.md) (commit `f914a99`)
**Implementation:** commits `a4a0d60` (planner) and `1095f5a` (lead-limit fix)
**Not included:** connectors, final identity decisions, preference extraction, LLM, UI, live person research

## 1. What was built

| Module | Role |
|---|---|
| `engine/plan.py` | `ResearchInput` → deterministic **objectives**, each with priority, targets and ordered query variants: identity, official profile, meeting topic, professional activity, appearances, writing, public profiles (discovery only), optional `focus:<topic>` (neutral wording only). Previewable with `--dry-run` (no network, no database). |
| `engine/controller.py` | **Adaptive controller**: one query per step, chosen by score; contribution measured after retrieval; follow-ups added; continue or stop decided by explicit rules. Every step writes a `planner_decisions` row. |
| `engine/identity_signal.py` | **Provisional** identity signal (supported / weak / none per document), used only to steer the planner. Phase 5 replaces it. |
| `engine/policy.py` | Input screen: refuses meeting purposes or focus topics aimed at special-category or private-life data, before anything is recorded. |
| `engine/research_run.py` | Wires it together: plan → controller (search, then retrieval per step) → transient-storage scrub → gates. |
| Migration 0003 | `research_objectives`, `planner_decisions` (reversible). |

### How the next query is chosen

```
score = priority × coverage_gap × diversity × discovery_boost / (1 + low_yield_streak)
```

| Component | Meaning |
|---|---|
| `coverage_gap` | 1 − relevant documents / target. If the documents are enough but the distinct domains are too few, it is 0.5. |
| `diversity` | Rises while an objective has fewer independent domains than its target. |
| `discovery_boost` | ×1.6 when the objective's next variant comes from a **discovery** (handle, official domain, disambiguation). Specific leads are acted on promptly. |
| `low_yield_streak` | Consecutive queries that added nothing to this objective. |
| Identity gating | While the identity signal is not `supported`, **only identity work runs**. |

All candidates' score components are stored with each decision.

### What counts as progress

- **Relevant:** a usable, non-duplicate document whose provisional identity is `supported` (full name plus organisation, official domain, known URL or role terms).
- **Not progress:**
  - name-only pages (`weak`)
  - pages without the name (`irrelevant`)
  - duplicates, near-duplicates and off-target results
  - URLs blocked by policy, except as **profile leads** for the discovery-only objective
- A relevant document found earlier and returned again by another objective's query **counts toward that objective's coverage**, but **not** as new evidence for the run (diminishing returns).

### Follow-ups (all recorded with their trigger)

| Trigger | Follow-up |
|---|---|
| Weak identity (name-only pages) | Two disambiguation queries: organisation words; exact role phrase |
| Supporting document on a domain containing the organisation's name | `site:` query on that domain (official profile) |
| `sameAs` / `rel=me` link or profile lead on GitHub, Medium or Substack | Query restricted to that handle |
| Enough documents but too few domains | Diversity query excluding the domains already seen |

### Stop rules and resulting status

| Stop | Status |
|---|---|
| `PLAN_RESOLVED`, `DIMINISHING_RETURNS` | **completed**: the planner finished by design. Coverage gaps are listed per objective; "completed" never means comprehensive. |
| `IDENTITY_UNRESOLVED` | **failed**: evidence can't be attributed, so no other research is run. |
| `BUDGET_*`, `NO_PROVIDER_AVAILABLE`, `PROVIDER_ERRORS`, step ceiling | **partial**, or **failed** if nothing usable was found. Never completed. |
| `CANCELLED` | cancelled |

## 2. Tests (actually run, all offline)

| Suite | Result |
|---|---|
| Engine, deterministic (`pytest tests_engine`) | **194 passed**, 1 skipped (the live test) |
| Of which new Phase 3 planner tests (`test_planner.py`) | **34 passed** |
| No-LLM checks | **4 passed** |
| Prototype (`pytest tests`) | **68 passed** |
| Prototype files against the baseline commit `c2cf132` | **unchanged** (`git diff` empty) |
| `data/engine.db`, `data/personalizer.db` against their Phase 3 start fingerprints | **unchanged** (sha256) |

### Acceptance criteria → tests

| # | Criterion | Test(s) |
|---|---|---|
| A1 | Next query chosen from measured signals | `test_query_sequence_depends_on_measured_signals` |
| A2 | Follow-ups from discoveries | `test_official_domain_and_handle_followups` (the handle query is issued on the **next** step), `test_weak_identity_triggers_disambiguation`, `test_diversity_followup_excludes_seen_domains` (the `exclude_domains` is **actually sent**) |
| A3 | Duplicates aren't progress and aren't re-fetched | `test_duplicate_results_are_not_progress_and_not_refetched` |
| A4 | Irrelevant or name-only pages never count | `test_irrelevant_and_name_only_pages_never_count_as_coverage` |
| A5 | Poor identity: disambiguation, then failure | `test_weak_identity_triggers_disambiguation`, `test_unresolved_identity_fails_the_run_and_blocks_other_research` |
| A6 | Provider errors and exhausted credits | `test_provider_error_streak_stops_partial`, `test_provider_errors_with_nothing_found_fail`, `test_exhausted_credits_stop_partial`, `test_monthly_free_tier_guard_stops_before_any_request` |
| A7 | Every budget limit | `test_each_budget_limit_stops_with_its_code_and_never_completes` (searches, cost, fetches, bytes, robots, leads), `test_time_budget_stops_partial_or_failed`, `test_search_budget_is_checked_before_sending`, `test_lead_limit_still_fetches_already_accepted_leads` |
| A8 | Diminishing returns and early stopping | `test_diminishing_returns_stop_well_inside_budget`, `test_plan_resolves_early_when_coverage_is_met` |
| A9 | Deterministic | `test_same_world_same_decisions` |
| A10 | Trace explains every step | `test_every_step_is_explained_in_the_trace` |
| A11 | No preference inference | `test_plan_is_deterministic_and_has_no_preference_wording`, `test_planner_produces_no_preference_records`, policy tests |
| A12 | Phase 1/2 records untouched | `test_existing_phase1_2_records_survive_migration_on_a_copy`: on a **copy**, implicit migration is refused, metrics for all 4 old runs are identical across migration 3 up and down, row counts are identical, and the **original file's sha256 is unchanged** |
| A13 | Prototype and existing suites; no LLM | See the suite table above |
| A14 | Ledger caveat | `test_ledger_summary_is_flagged_as_unverified` |

## 3. Budget and safety behaviour (measured, offline fictional world)

| Limit hit | Status | Stop reason | Searches | Fetches | Usable docs |
|---|---|---|---|---|---|
| `max_search_requests=2` | partial | `BUDGET_SEARCHES` | 2 | 3 | 3 |
| `max_cost_usd=0.017` (2 × $0.008 fit) | partial | `BUDGET_COST` | 2 | 3 | 3 |
| `max_fetch_attempts=1` | partial | `BUDGET_FETCHES` | 1 | 1 | 1 |
| `max_total_bytes=900` | partial | `BUDGET_BYTES` | 1 | 2 | 1 |
| `max_robots_requests=1` | partial | `BUDGET_ROBOTS` | 1 | 1 | 1 |
| `max_leads=2` | partial (**after fix**; was wrongly failed) | `BUDGET_LEADS` | 1 | 2 | 2 |
| `max_wall_seconds=0.5` | partial | `BUDGET_TIME` | 1 | 3 | 1 |
| Provider 5xx after 1 success | partial | `PROVIDER_ERRORS` | 4 | 3 | 3 |
| Credits exhausted (432) after 1 success | partial | `NO_PROVIDER_AVAILABLE` | 2 | 3 | 3 |
| Monthly free-tier cap already reached | failed | `NO_PROVIDER_AVAILABLE` | **0** (nothing sent) | 0 | 0 |

**No limit stop is ever reported as completed.** Budgets are checked before every search (searches, cost, monthly ledger) and before every fetch (fetches, bytes, robots, time).

## 4. Sample research trace (synthetic)

From the offline world: fictional subject "Priya Raman, Nimbus Compute", `.example` domains, organisation domain **not** given. Full trace: [examples/phase3_trace_synthetic.txt](examples/phase3_trace_synthetic.txt). Excerpt:

```
#1 identity          query: "Priya Raman" "Nimbus Compute"
   why: highest score 1.5; others wait for identity (signal 'unknown')
   outcome: 3 new URLs, 2 relevant, 1 name-only (a namesake). identity -> covered
   follow-up: [official_profile] discovered likely official domain nimbuscompute.example

#2 official_profile  query: "Priya Raman" [site: nimbuscompute.example]
   why: highest score 2.16 (... x discovery 1.6)

#5 appearances       query: "Priya Raman" (interview OR podcast OR keynote OR panel OR talk)
   outcome: 0 new URLs, 2 duplicates (2 relevant, credited to this objective only). appearances -> covered

#6 writing           query: "Priya Raman" (wrote OR author OR blog OR op-ed OR article)
   outcome: 1 relevant. follow-up: [writing] profile link medium.com/@priyaraman

#7 writing           query: "Priya Raman" [site: medium.com/@priyaraman]
   why: highest score 0.7 (... x discovery 1.6)

#10 STOP  DIMINISHING_RETURNS: last 4 queries added no relevant evidence
   identity, official_profile, professional_activity, appearances = covered;
   meeting_topic = exhausted; writing, public_profiles = not reached (gaps)
   10 searches of 40 allowed, 7 fetches of 80; status completed, every gate passed
```

## 5. Defects found and fixed during Phase 3

| Found by | Defect | Fix |
|---|---|---|
| First smoke run | Coverage depended on which query first found a page (a speaker page found by the meeting-topic query never covered "appearances") | Repeat relevant results credit the current objective's coverage, but not the run's new-evidence count |
| Unit test | A failed provider query **used up** its query variant, so two outages exhausted identity and blamed `IDENTITY_UNRESOLVED` | Failed or unsent queries don't use up variants; provider faults are counted separately (`PROVIDER_ERRORS`) |
| Unit test | `metrics()` crashed on version-2 databases (it queried the new planner table) | Schema-aware metrics: older databases report the planner as unavailable |
| Sample-trace review | A discovered Medium handle was queued but never searched, because generic queries outranked it until diminishing returns stopped the run | ×1.6 discovery boost; tested to run on the very next step |
| Budget measurement | `max_leads` abandoned already-accepted leads, so the run was **failed** | Only stops that make fetching impossible skip accepted URLs |
| Baseline inspection | `connect()` auto-migrated any database, so adding migration 0003 would have silently altered the Phase 1/2 database | Implicit migration only for new, empty databases; otherwise `SchemaOutdated`, with an instruction to run `engine db upgrade` explicitly |

## 6. Ledger and costs

- **No live searches were run in Phase 3**, so no credits were spent. All testing used the offline scripted provider.
- `python -m engine ledger` now leads with an explicit caveat: it is the engine's **local record**, **not a verified account balance**. Tavily's account counter didn't reflect this engine's usage when checked on 2026-10-17 and **remains unreconciled**.

## 7. Remaining limitations

- **The identity signal is provisional:** string-level name and organisation co-occurrence. Two people with the same name at the same organisation would be merged. There is no conflict detection or candidate-profile model (phase 5).
- **Fixed scoring weights:** priorities, the ×1.6 boost and the windows (2 per objective, 4 global) are hand-set defaults and **uncalibrated**. They need evaluation on real, consented subjects.
- **Diminishing returns can stop before low-priority objectives run** (in the sample, `writing` and `public_profiles` weren't completed). This is by design and reported as gaps, but it may under-explore profile discovery.
- **Objectives open at a stop keep their last status** (`active`/`pending`). The gap list in metrics is the reliable indicator.
- **One query per step, sequentially:** simple and auditable, but slow with real latency (about 2 s per Tavily query observed in Phase 2).
- **Keyword-based query templates** (English); no synonym expansion.
- **No live validation:** the planner hasn't been run against real search results. A live run needs an approved demonstration subject (DPDP review pending).
- **Discovery-only profile leads** are matched by name tokens in the URL or snippet: weak evidence, never treated as identity.
- **Upgrading `data/engine.db` (v2) to v3** requires an explicit `python -m engine db upgrade`. It hasn't been run; the database is untouched. CLI commands other than `db` and `research --dry-run` refuse to run until it is.
