# Phase 3 plan: deterministic adaptive search planner and stopping logic

**Baseline:** commit `c2cf132` (Phase 2), pushed to `origin/main`. Phase 3 is delivered in new commits; history is never rewritten.

## Scope

**In scope**
- A research plan built from the input.
- An adaptive research queue that chooses each next search from measured signals.
- Follow-up queries driven by discoveries.
- A provisional identity signal, used for planning only.
- Stopping rules.
- A planner trace, CLI and documentation.

**Out of scope**
- Connectors (phase 4).
- Final identity decisions (phase 5).
- **Preference extraction** (phase 6).
- Validation (phase 7).
- UI, LLM calls, Brave/Exa live use, deployment.

## Design

### Input and plan

`ResearchInput` contains: name, organisation, organisation domain, role, location, known URLs, meeting purpose, and optional focus topics.

`build_plan()` turns it into **objectives**, each with a priority, a coverage target (supporting documents and distinct domains) and an ordered list of query variants:

| Objective | Purpose | Example variant |
|---|---|---|
| `identity` | Tie the name to the organisation | `"Name" "Org"` |
| `official_profile` | Organisation or own pages | `"Name"` restricted to the org domain |
| `professional_activity` | Announcements, roles, projects | `"Name" "Org"` plus announcement terms |
| `meeting_topic` | Public work on the meeting subject | `"Name"` plus purpose keywords |
| `writing` | Articles and posts by the person | `"Name"` plus author terms; Medium and Substack |
| `appearances` | Talks, podcasts, interviews | `"Name"` plus interview, podcast or keynote |
| `public_profiles` | Discovery of platform profiles (leads only) | `"Name"` restricted to GitHub, Bluesky, Instagram, X or LinkedIn |
| `focus:<topic>` | Only if the user names a focus topic | `"Name"` plus the topic word, **neutral terms only** |

The plan is deterministic and can be previewed without network access (`--dry-run`).

### Adaptive queue (deterministic)

Before every step, objectives are scored. The highest score wins; ties go to plan order.

```
score = priority × coverage_gap × diversity_factor × 1/(1 + low_yield_streak) × identity_factor
```

- `coverage_gap`: 1 − supported_docs / target (0 once covered).
- `diversity_factor`: higher while the distinct domains found are below target.
- `identity_factor`: while the **provisional identity signal** is `unknown` or `weak`, only identity work is scheduled.

**Follow-ups are added as new variants**, each recording the reason it was added:

| Trigger | Follow-up |
|---|---|
| Weak identity (the name appears without org, domain or role) | Disambiguation queries (name with role, or with the organisation's distinctive words) |
| An official domain discovered (the org's name appears in a domain's label) | A `site:` query on that domain |
| Coverage met but domains below target | **Diversity** query that excludes the domains already seen |
| A profile, `sameAs` or `rel=me` link found on Medium, Substack or GitHub | A query restricted to that handle's path |

Each step runs one query, then retrieves its new on-target leads, then measures its **contribution**:
- results, and how many were duplicates;
- off-target results;
- new unique URLs;
- results blocked by policy;
- new **relevant** documents (usable, with identity supported);
- **irrelevant** documents (no name, or a name-only match);
- new domains;
- profile leads (discovery only).

### Provisional identity signal (planning only, not a phase-5 decision)

Per document:

| Level | Meaning |
|---|---|
| `supported` | Full name, plus at least one of: organisation, organisation domain, a known URL, or a role term |
| `weak` | Name only |
| `none` | No name |

Per run:

| Level | Meaning |
|---|---|
| `supported` | At least 2 supported documents on at least 2 domains, or 1 on the official domain |
| `weak` | Only weak documents |
| `unknown` | Nothing found |

### Stopping rules

Checked before every step, in order:

| # | Condition | Stop reason | Status |
|---|---|---|---|
| 1 | Cancellation, time limit | `CANCELLED` / `BUDGET_TIME` | cancelled / partial |
| 2 | Any run budget hit (searches, cost, fetches, bytes, robots, leads) | `BUDGET_*` | partial |
| 3 | Monthly free-tier guard, or quota exhausted, with no provider left | `NO_PROVIDER_AVAILABLE` (detail names the cause) | partial |
| 4 | 3 consecutive provider failures | `PROVIDER_ERRORS` | partial |
| 5 | Identity objective finished and identity still not `supported` | `IDENTITY_UNRESOLVED` | **failed**: evidence can't be attributed |
| 6 | The last N queries (default 4) added no relevant evidence | `DIMINISHING_RETURNS` | completed, with coverage gaps listed |
| 7 | Every objective covered, saturated or exhausted | `PLAN_RESOLVED` | completed, with coverage gaps listed |

- An objective is **saturated** after 2 consecutive steps with no new relevant evidence.
- An objective is **exhausted** when its variants run out, or it reaches its per-objective query cap (default 4).
- **"completed" means the planner reached a natural stop; it never means comprehensive.** Coverage per objective is always reported. Budget, provider or time stops are never reported as completed.

### Storage (migration 0003, reversible)

- `research_objectives`: status, targets, counters, reasons.
- `planner_decisions`: one row per step, holding:
  - the objective and the scores of all candidate objectives;
  - why that one was chosen;
  - the query issued;
  - its contribution;
  - the follow-ups it added;
  - continue or stop, with the reason.

**Existing databases are no longer migrated implicitly.** `connect()` upgrades only a new, empty database; an outdated database with data needs an explicit `python -m engine db upgrade`. This protects the Phase 1/2 records.

### Budgets

Unchanged ceilings, and still checked before every request: 40 searches, 80 fetches, 25 MB, 240 s, $0.25 per run, plus the monthly ledger caps. Planner settings are configurable via `ENGINE_PLANNER_*`.

**Tavily account counter:** still unreconciled. Reports show the local ledger labelled as the engine's own record of usage, **not** a verified account balance.

## Acceptance criteria

| # | Criterion | Evidence required |
|---|---|---|
| A1 | The next query is chosen from measured signals (coverage, diversity, identity, unique relevant URLs, yield), not a fixed list | Test: different fixture worlds produce different query sequences from the same plan; decision rows show the score components |
| A2 | Follow-ups come from discoveries | Tests: official-domain, diversity, handle and disambiguation follow-ups each appear, with their trigger in the trace |
| A3 | Duplicate results don't count as progress and aren't fetched twice | Test |
| A4 | Irrelevant results and name-only pages don't count toward coverage | Test |
| A5 | A poor identity match triggers disambiguation; an unresolved identity stops the run as **failed** | Test |
| A6 | Provider errors and exhausted credits stop the run with the correct reason, as **partial** | Tests (5xx streak, 432 quota, monthly guard) |
| A7 | Each budget limit (searches, cost, fetches, bytes, robots, leads, time) stops with its code as **partial** | One test per limit |
| A8 | Diminishing returns and early stopping work | Tests: global-window stop; plan resolved well within budget |
| A9 | The planner is deterministic | Test: two identical runs produce identical decision sequences |
| A10 | The trace explains each selection, its contribution and the continue/stop decision | Test on decision rows; a sample trace in the docs |
| A11 | No preference inference: no preference claims are produced, and focus queries use neutral terms only | Test |
| A12 | Phase 1/2 records are untouched | Test on a **copy** of `data/engine.db` (metrics identical across migrate up and down; the original's sha256 unchanged); no implicit migration |
| A13 | Prototype and existing suites still pass; no LLM | Prototype tests, engine tests, no-LLM checks; prototype checksums |
| A14 | Ledger caveat | Ledger output and docs flag the Tavily account counter as unreconciled |

## Test strategy

All new tests are offline and deterministic:
- a scripted Tavily fake that routes results by query text;
- scripted web pages for a **fictional** subject on `.example` domains.

No live person research is done in this phase: no demonstration subject has been approved, and the DPDP review is pending.
