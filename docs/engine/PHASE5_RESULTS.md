# Phase 5 results: reliable identity resolution

**Date:** 2026-10-17
**Plan:** [PHASE5_PLAN.md](PHASE5_PLAN.md) (`9bf486b`)
**Commits (local; not pushed):**

| Commit | Contents |
|---|---|
| `4a636b6` | Provider-text fallback disabled by default (D9.7); [BOT_DETECTION_REVIEW.md](BOT_DETECTION_REVIEW.md) |
| `9bf486b` | Phase 5 plan and acceptance criteria I1-I11 |
| `7bcd69e` | Identity core: `engine/identity/` (evidence, resolver, store), migration 0005, run integration, metrics. **This commit left one test failing.** |
| `311d144` | Fixes that test (a copy-migration comparison that assumed v4 was the newest schema) |
| `c68564a` | 20 labelled synthetic cases plus 9 property, override, integration and migration tests; one evidence-rule fix |
| final docs commit | This document; the hash is reported at handover |

**Scope:** identity resolution only.
- No preference extraction, validation or production use.
- No new keys, no paid usage, no LLM calls, no live personal research.
- All test people, organisations and domains are fictional (`.example`).

## 1. What changed

| Area | Before (phase 3 `identity_signal`) | After (phase 5 `engine/identity/`) |
|---|---|---|
| **Unit of evidence** | Whole page: "name and organisation both appear" = `supported` | **Each mention of the name.** We record which organisations are *grammatically attached* to that mention. The patterns are an appositive ("Name, CTO at X"), a predicate ("Name is VP at X", "Name joined X"), "Name of X", "Name (X)", "X's Name" and "X CTO Name". We also record past tense, role terms, occupation, location and hedging. |
| **Page decision** | supported / weak / none | `confirmed` / `strong` / `possible` / `historical` / `ambiguous` / `rejected` / `insufficient`, each with reasons and a candidate key (`target`, `namesake:<org>`, `unassigned`) |
| **Subject decision** | None (planning hint only) | `confirmed`, `strongly_supported` (both count as **resolved**), or `possible`, `conflicting`, `insufficient_evidence` (**unresolved**). Every outcome comes with an explanation list. |
| **Profiles** | Not handled | Each profile is keyed by (platform, handle) and gets a status: `confirmed`, `strong`, `possible`, `rejected` or `duplicate_unresolved`. **Two profiles on one platform are never both confirmed.** |
| **Conflicts** | Not handled | A page that ties the name to two current employers is `ambiguous`. A trusted profile that appears on a newer page for another organisation means the subject is `conflicting` (possible role change). |
| **Time** | Not handled | Past-tense affiliation becomes `historical`. If all support is more than 2 years old and newer pages tie the name elsewhere, the subject is capped at `possible`. |
| **Independence** | Not handled | Support counts by **registrable domain**. Syndicated copies (`duplicate_of`) count once. |
| **Audit and reversal** | n/a | Migration 0005 (reversible) adds `identity_resolutions` and `identity_decisions` (`rules` or `user`, supersede chain). `override_decision()` needs a written reason, and a later override supersedes it. Rule version `identity-rules-1`. |
| **Run integration** | n/a | The `identity_resolution` stage runs after the storage-policy stage, while text is still inside retention. `metrics()["identity"]` is marked `advisory: true`. **It does not change the run status.** |

The phase 3 `identity_signal` still steers the planner only. Its docstring now says so.

### Rules (ordered; first match wins), per document

1. No text, or no full-name mention → `insufficient` (a surname-only page is flagged as such).
2. Every mention is hedged ("some say", "unverified", "claims to be") → `rejected`.
3. Page is on the organisation's official domain, or is a user-supplied URL → `confirmed`. It is `ambiguous` instead if the name is attached only to another organisation there.
4. Name attached to the target **and** to another organisation, or to the target plus an unrelated occupation → `ambiguous`.
5. Name attached to the target, plus an independent extra signal → `strong`. The extra signal can be the subject's role terms, location, or a link to an already-confirmed profile.
6. Name attached to the target **only** → `possible` (**a name-plus-organisation match is never enough on its own**).
7. Only past-tense attachment to the target → `historical`.
8. Name attached to another organisation or occupation → `rejected` and recorded as `namesake:<org>`.
9. Role terms only → `possible`. Anything else → `insufficient`.

Subject:
- `conflicting` if any conflict exists.
- Else `confirmed`, given a confirmed anchor.
- Else `strongly_supported`, given strong pages on **≥ 2 independent registrable domains**.
- Else `possible`, if any support at all.
- Else `insufficient_evidence`.

Staleness caps `confirmed` and `strongly_supported` at `possible`.

## 2. Tests (actually run)

| Suite | Result |
|---|---|
| Engine, deterministic (`pytest tests_engine`) | **297 passed**, 1 skipped (the opt-in live test, `ENGINE_LIVE=1`) |
| Of which Phase 5 (`test_identity.py`) | **29 passed**: 20 labelled cases + 9 property, override, integration and migration tests |
| Phase 4 tests (fallback default-off test added) | Pass, inside the 297 |
| No-AI-library checks (`test_no_llm.py`) | **4 passed** |
| Prototype (`pytest tests`) | **68 passed** |
| Prototype files against `c2cf132` | **unchanged** (`git diff c2cf132 -- personalizer tests eval examples requirements.txt` is empty) |
| `data/engine.db` sha256 | `a23d7684…7a8c`: **unchanged** since the start of Phase 4; still schema **v2**; `integrity_check` ok |
| `data/personalizer.db` sha256 | `f888f58e…ebf6`: **unchanged** |
| Migration 0005 | Tested **on a copy** only. Row counts are identical after up and after down. Old runs report `identity.available = false`, so nothing is invented. The original's sha256 was checked before and after. |

**Live research:** none. No API calls were made in Phase 5.

### Evaluation over the labelled set

| Measure | Value |
|---|---|
| Labelled cases | 20 (7 expected resolved, 13 expected unresolved) |
| Cases where the subject status equals the label | **20 / 20** |
| Documents whose status equals the label | **34 / 34** |
| **Forced-match rate** (unresolved-labelled cases that came out resolved) | **0 / 13** |
| Namesake documents attributed to the subject | **0** |
| Platforms with two confirmed profiles | **0** |

**These numbers measure consistency with the rules, not real-world accuracy.** The cases were written by the same author as the rules, against a fictional subject. A small set of cases written independently, using real but consenting subjects, is needed before any accuracy claim (see §5).

### Acceptance criteria → evidence

| # | Criterion | Evidence | Met |
|---|---|---|---|
| I1 | Namesakes distinguished, never attributed | `namesakes_with_target`, `namesakes_only`, `test_namesake_documents_are_never_attributed`, `test_namesake_never_merged_even_if_it_mentions_target_city` | Yes |
| I2 | Name + organisation alone never resolves | `name_plus_org_only`; `test_name_and_organisation_alone_never_resolves` (5 such pages on 5 domains → `possible`) | Yes |
| I3 | Outdated roles | `historical_only` → historical/possible; `outdated_role_linked` → conflicting; `stale_unlinked` → possible | Yes |
| I4 | Misleading company associations | `misleading_company_association`: sponsor sentence, speaker list with the target org attached to someone else, and the org in a neighbouring sentence. None of these count. | Yes |
| I5 | Duplicate profiles | `duplicate_profiles_linked`, `duplicate_profiles_unlinked`, `two_confirmed_same_platform`; `test_two_profiles_on_one_platform_are_never_both_confirmed` | Yes |
| I6 | Conflicting evidence surfaced with reasons | `conflicting_same_document`, `outdated_role_linked` | Yes |
| I7 | Forced-match rate = 0 | `test_forced_match_rate_is_zero` (13 unresolved cases) | Yes |
| I8 | Independence | `syndicated_copies` (3 copies → 1 domain → possible), `same_domain_twice` → possible | Yes |
| I9 | Auditable and reversible | `test_override_is_recorded_reversible_and_applied`: possible → user confirms → confirmed → user rejects → insufficient_evidence. The supersede chain is checked, and a reason is mandatory. | Yes |
| I10 | Integrated; migration on a copy; originals untouched | `test_research_run_records_identity_resolution`, `test_resolution_is_visible_in_run_metrics_as_advisory`, `test_migration_0005_on_a_copy`; sha256 table above | Yes |
| I11 | All suites pass; no keys, paid usage or live research | §2 table | Yes |

## 3. Example identity decisions (real output from the labelled set)

The subject throughout is a fictional "Priya Raman, VP of Infrastructure, Nimbus Compute, Bengaluru".

**Namesakes kept apart** (`namesakes_with_target` → `strongly_supported`, resolved)
```
d1 strong    target                       name tied to the target organisation plus role terms
d2 strong    target                       name tied to the target organisation plus location
d3 rejected  namesake:saffron bakehouse   name tied to a different organisation: Saffron Bakehouse
d4 rejected  namesake:chennai city fc     name tied to a different organisation: Chennai City FC
explanation: strong documents on 2 independent domains
             2 namesake candidate(s) kept separate: chennai city fc, saffron bakehouse
```

**Name plus organisation is not proof** (`name_plus_org_only` → `possible`, unresolved)
```
d1 possible  target   name and organisation only: not sufficient on its own
explanation: support below threshold: 0 confirmed, 0 strong domain(s), 1 possible, 0 historical
```

**Misleading association** (`misleading_company_association` → `insufficient_evidence`)
```
"Nimbus Compute sponsored the food festival. Priya Raman, the pastry chef at Saffron Bakehouse…"  → rejected (namesake)
"Speakers: Priya Raman (Saffron Bakehouse) and Arjun Mehta (Nimbus Compute)."                   → rejected (namesake)
"Nimbus Compute announced a GPU region. Later that day, Priya Raman gave a cooking demonstration." → insufficient
```

**Outdated role, linked through a shared profile** (`outdated_role_linked` → `conflicting`, unresolved)

An official page from 2019 links `github.com/priyaraman`, so that profile is confirmed. A 2026 page says "Priya Raman joined Orbit Labs as CTO" and links the same profile.
```
d1 confirmed (official domain, 2019)   d2 strong (2019)   d3 rejected namesake:orbit labs (2026)
conflict: https://news.example/2026/orbit ties the same profile (github.com/priyaraman) to orbit labs
          and is newer: possible role change
```
The resolver does **not** decide which affiliation is current. It surfaces the conflict for a human.

**Syndication does not inflate support** (`syndicated_copies` → `possible`)
```
3 strong pages, 2 of them near-duplicates of the first → 1 strong domain → below threshold
```

**Duplicate profiles** (`two_confirmed_same_platform` → subject `confirmed`; both GitHub handles `duplicate_unresolved`)

The user supplied one GitHub handle and the official page links a different one. Neither is promoted. A human chooses.

**Hedged** (`hedged_attribution` → `insufficient_evidence`)
```
"Some say this is Priya Raman from Nimbus Compute, but the account is unverified."  → rejected (hedged)
```

## 4. False-match risks (how a wrong person could still be accepted)

Ordered by how likely we think they are to matter.

1. **Two people with the same name at the same organisation.** Nothing in the text separates them. An official page for either one confirms "the subject". Mitigation today: none beyond the role and location terms. Phase 6+ should require the role to match on the official page before treating it as confirmation for a large organisation.
2. **The official-domain anchor is strong by design.** Any full-name mention on the organisation's domain is `confirmed` unless the name is attached to another organisation there. That includes a customer story, a guest blog post, a user-generated subdomain, or a page that names a namesake as a partner. Subdomains count as official (`*.org-domain`).
3. **Re-worded press releases.** Two outlets that rewrite the same release are separate registrable domains and are not near-duplicates, so they can reach `strongly_supported`. Origin grouping is Phase 7 (validation).
4. **Organisation-name collisions.** The target matches by substring on its core name (legal suffixes stripped). An attached "Nimbus" counts as "Nimbus Compute", and a different "Nimbus Compute Bank" would too.
5. **Middle-name tolerance.** The name pattern allows one middle name or initial ("Priya K. Raman"). A different "Priya Lakshmi Raman" is treated as the same name.
6. **Trusted inputs.** User-supplied `known_urls` are taken as true, and so are `sameAs` / `rel=me` links on a confirmed page. A wrong known URL or a site's mistaken `sameAs` propagates.
7. **Pronoun carry-over.** A following sentence that starts with "She/He/They" is attached to the name. If that pronoun refers to someone else, its organisation is misattributed. This can cause a false rejection or a false `strong`.

## 5. Limitations

- **English-only, capitalisation-based patterns.** The rules miss lowercase or stylised organisation names ("acme labs", "iFoo"), non-Latin scripts, transliteration variants and nicknames ("Priya R." or "P. Raman" are not matched). All of these fail towards **unresolved**, the safe direction, but they lower recall.
- **Small lists.** Occupations, places and function words are short deterministic lists. An unknown occupation ("sommelier") neither rejects a namesake nor counts as one.
- **Staleness needs dates.** The cap applies only when **every** supporting page has a `published_at`. One undated supporting page disables it. Undated pages are common.
- **No current-role decision.** "Possible role change" is reported, never resolved.
- **Profiles are not fetched for identity.** Profile status comes from links and leads only. LinkedIn, Instagram and Facebook stay discovery-only.
- **Advisory only.** The run's status and gates ignore the resolution, and later phases do not yet consume it.
- **Evaluation is synthetic.** We report 20/20 consistency, not accuracy. A real accuracy figure needs labelled real-world pages, which needs an approved, consenting subject and the DPDP legal review. Both are still open.
- **Rule version.** Decisions record `identity-rules-1`. Changing a rule supersedes rule decisions on re-resolution and never changes user decisions.

## 6. Safeguards unchanged

- Budgets: $0.25 per-run ceiling and monthly ledger caps. The ledger is still **not** a verified account balance.
- Access policy: robots.txt; X and Reddit disabled; Instagram, Facebook and LinkedIn discovery-only; `ACCESS_CHALLENGE` is never bypassed.
- **Provider-text fallback disabled by default** (D9.7, `test_fallback_is_disabled_by_default_for_real_research`).
- 7-day text retention. Identity runs before text expires and stores reasons and short signals, not page text.
- No LLM or AI libraries (`test_no_llm.py`).

## 7. Not done (needs approval)

- Phase 6 (preferences) was not started.
- No production use and no live personal research.
- Commits are local and unpushed until you ask.
