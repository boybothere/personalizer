# Phase 6 results: evidence-gated claims

**Date:** 2026-10-18
**Plan:** [PHASE6_PLAN.md](PHASE6_PLAN.md) (`94c57a5`, revised for the approval conditions in `daf233d`)
**Status:** implemented locally, **not pushed** (awaiting your review). A **corrective pass** after the read-only audit is described in **§11**. Statements in §1, §6, §7 and §9 have been corrected where they overstated what was verified.

| Commit | Contents |
|---|---|
| `94c57a5` | Phase 6 plan and acceptance criteria (proposed) |
| `daf233d` | Plan updated for the approval conditions (P6-Q1 to Q5, S1 to S5) before any code |
| `9fbd17c` | Core: `identity-rules-2`, origin groups, gated claims, out-of-scope redaction, purge and WAL reporting, migration 0006 |
| `8a2ab31` | Labelled development corpus, frozen held-out set, privacy, retention and migration tests; rule fixes found by the corpus |
| `244975f` | Trailing filler words no longer split one claim into several (copies now collapse into one claim) |
| final docs commit | This document, decision log D11, README and architecture; the hash is reported at handover |

**Scope:** no LLM calls, no new API keys, no paid usage, no network calls of any kind, no live-person research. The Tavily fallback stays disabled. All test people, organisations and domains are fictional (`.example`).

## 1. What was built

| Area | Result |
|---|---|
| **Organisation matching** (`engine/identity/orgmatch.py`, `identity-rules-2`) | By tokens, not substrings: `exact`, `alias` (user alias, joined letters, official-domain label), `partial` (unclear, counts neither way), `different`. "Nimbus Compute Bank" and "Nimbus Computers India" are other organisations. "Nimbus" alone and "Nimbus Compute Labs Ltd" are unclear. "NimbusCompute" is the target. |
| **Origin groups** (`engine/origins.py`) | Union-find with a recorded reason for every merge. The links are: near-duplicate, canonical or `isBasedOn`, same site, same byline, news agency, press-release wire, "originally published on", a shared 12-word passage, ≥ 15 % shared 5-word shingles (at least 4), and a shared quote. **Merges whenever unsure** (S1). Shingles containing the subject's own name are ignored, so independent outlets repeating "Name, title at Organisation" are not taken as copies. Identity resolution now counts independence by origin group. |
| **Identity gate** (`engine/claims/store.py`) | Claims are made only when the subject is resolved, and only from `confirmed`, `strong` or `historical` documents. Namesake and rejected pages are never read. Withheld documents are counted by reason, with no text. |
| **Claim extraction** (`engine/claims/extract.py`) | Sentence grammar. The sentence must be linked to the subject: the subject's own page (first person), a quote attributed to the subject, or the subject as the grammatical subject (name, surname, or a pronoun directly after a subject sentence). Types: `stated_preference` (±), `activity`, `association`, plus the derived `activity_pattern`. Rejections are kept with their reason: question, hypothetical, sarcasm marker, quoting someone else, repost, double negative. A vague object gives `insufficient_evidence`. |
| **Status, flags, confidence** (`engine/claims/status.py`) | Statuses: `verified` / `strongly_supported` / `tentative` / `conflicting` / `rejected` / `insufficient_evidence`. Flags are always shown: `date_missing`, `date_conflict`, `stale`, `single_origin`, `reported_not_authored`, `superseded_by_newer`, `contradicted`, `provider_text`, `historical_identity`, `past_tense`. Confidence (`high` / `medium` / `low` / `none`) is an ordinal label derived only from status and flags. It is **not a probability**. |
| **Identity vs claim confidence** (S3) | Stored separately. Each claim stores `identity_status`, and each evidence row stores `document_identity_status`. Metrics show identity and claims in separate sections. |
| **Dependent-claims policy** (S3) | *Corrected in §11.* Only claims tied to a changed identity decision are superseded (`identity_changed`); unchanged claims keep their row; other changes are `recomputed`; the first extraction is `initial`. If the text has expired, only affected claims are superseded, the run reports `incomplete: text_expired`, and nothing is invented. |
| **Provenance** | Every evidence row stores: URL, document, evidence kind, publication date and its source, date conflict, event date, origin group, document identity status, subject link, the exact sentence, its offsets and hash, the rule id, and expiry. |
| **Out-of-scope filter and redaction** (`engine/claims/scope.py`, `engine/privacy.py`) | Categories: religion, politics, health (including support and recovery groups since §11), sexuality, caste and ethnicity, family, finance, legal, identity numbers, residence, current or upcoming whereabouts, undated travel, undated places. Such sentences never become claims. *Corrected:* the original build only redacted page text, titles and snippets **after** the run, and did not cover URLs or identity records. Since §11, the storage-level categories are filtered at the write boundary, and end-of-run redaction is defence in depth. |
| **Retention** | Claim evidence text and (since §11) page titles expire with the page text (7 days). Claims live as long as the run (30 days). `purge()` clears claim evidence and reports whether the WAL checkpoint completed. Since §11 it checkpoints on every call (retry), and the outcome is recorded on the run. `temp_store = MEMORY` on every engine connection. |
| **Run integration** | New stages `origin_groups` → `identity_resolution` → `claims` → `privacy_redaction`. A `claims` section is added to metrics (advisory; run status unchanged). New CLI: `python -m engine claims RUN_ID [--evidence]` and `research --org-alias`. |
| **Migration 0006** | `origin_groups`, `claims`, `claim_evidence`, reversible. Tested only on a copy. |

## 2. Tests (actually run)

| Suite | Result |
|---|---|
| Engine, deterministic (`pytest tests_engine`) | **398 passed**, 1 skipped (the opt-in live test). Exit code 0. |
| Of which Phase 6 (`test_claims.py`) | **101 passed**: 63 labelled development cases plus 38 property, privacy, retention, dependency, organisation-matching, origin and migration tests |
| Phase 5 identity tests under `identity-rules-2` | **29 passed** (the 20 Phase 5 labelled cases are unchanged regression tests) |
| No-AI-library checks (`test_no_llm.py`) | **4 passed** |
| Prototype (`pytest tests`) | **68 passed** |
| Prototype files against `c2cf132` | **unchanged** (empty diff) |
| `data/engine.db` | sha256 `a23d7684…7a8c` **unchanged**; schema **v2**; `integrity_check` ok |
| `data/personalizer.db` | sha256 `f888f58e…ebf6` **unchanged** |

### Existing tests that were changed (reported)

| Test | Change | Why |
|---|---|---|
| `test_planner.py::test_planner_produces_no_preference_records` | Previously asserted that no table name contains "claim". It now allows the Phase 6 `claims` and `claim_evidence` tables, and asserts that no planner decision mentions claims. The check that issued queries contain no preference words is unchanged. | The approved Phase 6 adds claim tables. The test's intent (the planner itself never produces preference records) is kept. |
| `test_urls_and_store.py::test_purge_clears_text_then_deletes_expired_runs` | Expected purge output gains `evidence_cleared` and `checkpoint_complete` | New retention outputs |
| `test_fetch_hardening.py` and `test_identity.py` copy-migration tests | Exclude the new `claims` metrics section and stop assuming that their migration is the newest | Same fix as in Phase 5 |

## 3. Acceptance criteria

| # | Criterion | Evidence | Met |
|---|---|---|---|
| C1 | No subject-attributed claim when the subject is unresolved or the document is not gated; namesake pages not processed | G1 cases; `test_no_subject_claims_without_resolved_identity_or_gated_document`; `test_namesake_and_rejected_documents_are_never_read_for_claims`. **Forced attribution: 0** | Yes |
| C2 | Claims re-gated when identity changes; nothing deleted silently | `test_document_override_supersedes_dependent_claims_and_recomputes`, `test_subject_becoming_unresolved_supersedes_all_claims`, `test_rerun_without_identity_change_is_recomputed_not_identity_changed` | Yes |
| C3 | Copies do not count as independent | 9 G2 cases (agency, shared quote, canonical, isBasedOn, wire, same site, byline, "originally published", plus an independent control); `test_copies_collapse_into_one_claim_with_one_origin` (3 sites, 1 origin, 1 claim). **Inflation: 0** | Yes |
| C4 | Token organisation matching; Phase 5 cases still pass | 15-row matcher table; 7 G3 cases; §5 change report | Yes |
| C5 | Preference vs activity vs association vs mention | G4 cases; `test_activities_associations_and_mentions_are_never_promoted_to_preferences`. **Promotions: 0** | Yes |
| C6 | Negation, hypothetical, question, sarcasm, quoting others, third-party | G4 and G6 cases (§4) | Yes, within the stated limits (§8) |
| C7 | Complete provenance | `test_every_claim_has_complete_provenance_and_recomputable_confidence` (the evidence text equals `document.text[span]`, and the hash matches) | Yes |
| C8 | Contradictory, stale, undated, date-conflict and insufficient evidence explicit | G6 cases | Yes |
| C9 | Sensitive content, whereabouts, residence and other people never stored | G7 cases; byte scans (§7). **Leakage: 0** | Yes |
| C10 | Migration 0006 reversible, tested on a copy, originals unchanged | `test_migration_0006_on_a_copy`; hashes | Yes |
| C11 | Synthetic results labelled as such; real-world requirements documented | §4; PHASE6_PLAN.md §4.4 | Yes |
| C12 | All suites pass; no LLM calls, keys, paid usage, live research or fallback | §2 | Yes |
| C13 | Retention across persistence paths | §6 | Yes, with the documented limits |
| C14 | Out-of-scope text removed from stored pages and snippets | `test_out_of_scope_sentences_are_not_left_in_any_database_file`, `test_full_research_run_redacts_pages_and_snippets` | Yes |
| C15 | Identity and claim confidence separate; dependent-claims policy tested | `test_identity_and_claim_confidence_are_stored_separately` and the C2 tests | Yes |
| C16 | Change report for `identity-rules-2` | §5 | Yes |
| C17 | `activity_pattern` only from ≥ 3 distinct dated events, in ≥ 3 origins, over ≥ 2 years | Positive case plus 4 negative cases (the same event reported 3 times, one year only, undated, same origin); `test_activity_patterns_are_labelled_and_need_distinct_dated_events` | Yes |

## 4. Agreement with authored labels (synthetic; **not** real-world accuracy)

"Precision" and "recall" below mean only **agreement between the rules and labels I wrote myself**, on fictional text. They say nothing about how the rules perform on real pages.

**Development set (63 cases, 234 sentences).** I wrote and corrected these cases while building the rules, so the 234/234 agreement is **expected and uninformative** about generalisation. The first run had 11 sentence disagreements:
- 9 traced to rule bugs, which I fixed: hypothetical contractions, first person "My", scare quotes, "us" read as a country, association wording.
- 2 were my own labelling errors, which I corrected.

Separately, 4 of my claim expectations had the wrong object key and were corrected (`8a2ab31`).

| Class | Labelled | Agreeing |
|---|---|---|
| pref+ / pref− | 25 / 5 | 25 / 5 |
| activity / association | 26 / 23 | 26 / 23 |
| rejected / insufficient | 13 / 3 | 13 / 3 |
| out_of_scope / no_claim | 21 / 118 | 21 / 118 |

**Held-out set (24 cases, 87 sentences).** Written and frozen **before** its first run and not used for tuning. First run: **83/87 agreement**. The later fix in `244975f` did not change this result.

| Class | Labelled | Predicted | Agreeing | Precision vs labels | Recall vs labels |
|---|---|---|---|---|---|
| pref+ | 13 | 13 | 13 | 1.00 | 1.00 |
| pref− | 9 | 8 | 8 | 1.00 | 0.89 |
| activity | 8 | 7 | 7 | 1.00 | 0.88 |
| association | 6 | 5 | 5 | 1.00 | 0.83 |
| rejected | 9 | 8 | 8 | 1.00 | 0.89 |
| insufficient | 3 | 3 | 3 | 1.00 | 1.00 |
| out_of_scope | 10 | 10 | 10 | 1.00 | 1.00 |
| no_claim | 29 | 33 | 29 | 0.88 | 1.00 |

The 4 held-out disagreements are all **misses**: no claim was produced, and none is a false claim or a privacy leak. They were deliberately left unfixed:

| Sentence | Label → got | Cause |
|---|---|---|
| "Who doesn't love a good spreadsheet?" | rejected → no_claim | No subject in the sentence, so nothing to reject. Arguably the label is wrong. |
| "Raman serves as an adviser **to** Open Grid Alliance." | association → no_claim | The preposition "to" is not in the association grammar |
| "I **visited** Kyoto in 2019." | activity → no_claim | "visited" is not an activity verb (travel verbs are only filtered, never extracted) |
| "**Colleagues** say she can't stand long meetings." | pref− → no_claim | The sentence starts with neither the name nor a pronoun, so it is not linked to the subject |

`test_heldout_set_misses_but_never_invents` keeps this as a regression guard: any disagreement that creates a claim, or any drop below 83/87, fails.

**Measures required to be zero**, each asserted by a test over all 63 development cases: forced attribution 0; claims from namesake or rejected pages 0; out-of-scope leakage 0; promotions of activity, association or mention to preference 0; claims without complete provenance 0; reported preferences above `tentative` 0. On the held-out set the test asserts that **no disagreement produced a claim**.

## 5. `identity-rules-2` change report (P6-Q5)

`rules_compare` ran the **exact rules-1 code from `e4199b5`** and the new rules-2 code on every labelled identity situation:
- the 20 Phase 5 cases;
- the 63 Phase 6 development cases;
- the 24 held-out cases.

That is 106 subjects and 194 documents in total.

- **Phase 5 labelled cases: 0 changed decisions.** All 20 stay as regression tests, with their original labels.
- **Subject-level decisions: 0 changed.**
- **Document-level: 7 changed**, all in the new organisation-substring cases and all in the intended direction:

| Case | Page text (abridged) | rules-1 → rules-2 |
|---|---|---|
| g3_bank_superstring | "…analyst at Nimbus Compute Bank…" | possible → **rejected** (other organisation) |
| g3_different_name | "…of Nimbus Computers India…" | possible → **rejected** |
| g3_bare_prefix | "…of Nimbus, the drone maker…" | possible → **ambiguous** (partial) |
| g3_descriptor_extension_unclear | "…of Nimbus Compute Labs Ltd…" | possible → **ambiguous** (partial) |
| g3_lowercase_not_matched | "…of nimbus compute…" | possible → **insufficient** (lowercase not matched) |
| g3_domain_alias_counts | "…at NimbusCompute…" | rejected → **strong** (domain alias) |
| g3_user_alias | "…at Stratus GPU…" (a user alias) | rejected → **strong** |

Also changed in identity (behaviour, not labelled decisions): independence is now counted by origin group. A document whose text has expired now **keeps its earlier rule decision** instead of collapsing to `insufficient`. Before this, re-resolving after 7 days would have unresolved every subject.

## 6. Retention verification (P6-Q2)

**SQLite and WAL implications.** The engine uses WAL mode. An UPDATE or DELETE writes the **new** page version to the `-wal` file. The old content stays in the main file until a checkpoint copies the new pages over it, and the WAL itself keeps earlier page images until it is reset. `secure_delete = ON` makes SQLite overwrite freed content with zeros. So expired text is gone from disk only after **NULL/DELETE, then a completed checkpoint with TRUNCATE**.

A checkpoint cannot complete while another connection holds a read snapshot. Purge therefore now reports `checkpoint_complete`, and a `false` value means the old content must be treated as still present.

| Path | Mechanism | Verified by | Result |
|---|---|---|---|
| Main `.db` | NULL at expiry plus `secure_delete` plus checkpoint | `test_evidence_text_expires_after_7_days_and_claims_after_30`: raw-byte scan after purge at +8 days (evidence and page text) and +31 days (claims and object text) | Not found |
| `-wal` | `wal_checkpoint(TRUNCATE)`, with its result checked; since §11 retried on every purge | Same byte scans; `test_blocked_checkpoint_is_reported_not_hidden`; since §11 `test_blocked_checkpoint_is_retried_even_when_nothing_new_expires` | *Corrected:* before §11 a blocked checkpoint was reported but **never retried**, and `research_run` discarded the report |
| `-shm` | Index only | Byte scans | Not found |
| SQLite temp files | `PRAGMA temp_store = MEMORY` | The pragma is asserted; since §11 an isolated OS temp folder is checked after a research run and a large sort (`test_temp_artifacts.py`) | No persisted temp files (SQLite deletes its own temp files on close, so only leftovers are observable) |
| Not yet expired | | Purge at +6 days clears nothing | Kept as intended |
| Hashes and offsets | Kept after expiry | Asserted | Kept |
| Exports (`claims` CLI, metrics, summary) | Built from the database on demand | After purge, `active_claims` shows null evidence | Expired text is not exported |
| Trace and metrics | Counts and codes only | The privacy test scans the trace and metrics JSON | No sentences |
| Logs | The engine writes none | `test_engine_writes_no_log_files` (no logging handlers, no file writers) | None |
| In-process caches | Memory only (robots.txt, HTTP client) | Code review | Gone when the process ends |
| Backups | **The engine makes none** | Not testable from the engine | **Limitation:** user copies, OS backup or sync, and pytest temp folders are outside the engine. In production, `data/` must be excluded from backup and sync, or the backups must follow the same 7/30-day retention. |
| Scheduling | Purge runs at the start of every research, search and retrieve run, and in `engine purge` | | **Limitation:** no scheduler exists, so nothing is purged while the engine is idle |
| Physical media | | | **Limitation:** SSD wear-levelling and filesystem snapshots are beyond SQLite's control |

## 7. Privacy verification (S2)

- **Seeded file database (the 6 G7 cases, 19 out-of-scope sentences)** after the pipeline and redaction:
  - raw bytes of `.db`, `-wal` and `-shm` contain none of them;
  - no extra files appear;
  - trace, metrics and claims exports contain none of them;
  - evidence offsets still match the redacted text.
- **Full `research_run` through the offline fake web (file database).** The pages and a Tavily snippet carry health, family and travel sentences:
  - redaction is recorded (family and health categories, ≥ 3 sentences, checkpoint completed);
  - raw bytes contain none of the sentences;
  - the in-scope sentence on the same page still yields its association claim.
- **Correction:** the original statement here, that out-of-scope text was in the database "briefly", was wrong. The audit showed it was written at fetch time, and that a crash left it on disk until the 7-day expiry (titles and URL slugs for 30 days). §11 fixed this for sensitive-trait, residence and whereabouts content. §12 also withholds undated place and travel URL paths, which §11 had not covered. The remaining limits are listed in §11 and §12.

## 8. Example claims (real output, fictional subject)

**Ordered contradiction:** both statements are kept, and the newer one is evaluated alone.
```
likes coffee            tentative / low    flags: superseded_by_newer, single_origin
  supports    priyaraman.example/notes/coffee-1  2025-01-15  "I love coffee."
  contradicts priyaraman.example/notes/coffee-2  2026-02-01  "I gave up coffee."
no_longer_likes coffee  verified / high    (the subject's own dated statement on a confirmed page)
```

**Two independent interviews:** `strongly_supported / high`, 2 origin groups, quoted mode.

**Three agency copies:** one claim, three sites, **one origin**, `tentative / low (single_origin)`.

**Third-party report:** `likes jazz`, `tentative / low`, "reported by profiles.example (not the subject's own statement)". It is hard-capped and never `verified` or `strongly_supported`.

**Recurring activity:**
```
recurring activity: taking part in running events, not a stated preference
strongly_supported / high — 3 distinct dated events in 3 origin groups over 3 years
(the individual 2023 race is flagged stale)
```

**Unordered contradiction (undated):** both sides are `conflicting / none`, with flags `contradicted, date_missing`.

**Hypothetical:** "I'd love to try surfing someday." is `rejected` ("hypothetical or wish, not a current preference").

**Identity shown separately:** each claim carries `identity_status: confirmed`, and each evidence row its document status (`confirmed` or `strong`).

## 9. Unresolved limitations

1. **Surface grammar only.** The rules have no semantic understanding:
   - unmarked sarcasm and irony are read literally;
   - implied preferences are missed;
   - coreference covers only a pronoun at the start of the sentence directly after a subject sentence;
   - a pronoun inside a reported sentence ("they love…") is assumed to be the subject.
2. **Recall gaps** (held-out): missing prepositions ("adviser to"), missing verbs ("visited"), and sentences that start with someone else's words. These are misses, not false claims.
3. **Object normalisation is shallow.** There is no stemming or synonyms: "running" vs "trail running", "coffee" vs "espresso". Contradictions between different wordings are not detected. An object stops at the first "and" ("rock and roll" becomes "rock").
4. **The out-of-scope filter is lexical and over-inclusive.** It drops "court" (basketball), "family" (product family), "recover" (data recovery) and undated named places, including identity sentences like "based in Bengaluru". Redacted sentences can no longer contribute identity signals if identity is re-resolved later. It can also **miss** sensitive phrasings not in its lists.
5. **Places** are recognised from a fixed list plus capitalisation cues. A travel sentence is kept only with an explicit **past year**; the current year is treated as possibly upcoming and dropped.
6. **Origin grouping** cannot detect paraphrased copies that share no 12-word passage, quote, marker or byline. Over-merging is possible, for example across sites that share boilerplate. That direction is safe: it lowers corroboration.
7. **Activity patterns** treat different event dates as different events. Two reports of one event with different publication dates and no event date in the sentence could count twice, unless they share an origin.
8. **Sentence splitting** breaks on abbreviations ("Dr. Raman").
9. **Retention outside the engine:** backups, sync, scheduling and physical media (§6).
10. **Claims are advisory.** Run status ignores them, and nothing downstream consumes them yet.
11. **No real-world accuracy.** Every number above is synthetic agreement. The requirements for real validation are in PHASE6_PLAN.md §4.4: consent, legal review, blind independent labelling, subject confirmation, and at least 300 sentences.

## 10. Not done

- Not pushed (per your instruction).
- No Phase 7 work.
- No live research.
- The original databases have not been migrated.

## 11. Corrective pass (after the read-only audit)

| Commit | Change |
|---|---|
| `792ea46` | Write-boundary privacy filter for every entry point; URL withholding (`URL_OUT_OF_SCOPE`, including redirects); identity-label withholding; run-input URLs scrubbed; support-group terms added |
| `5a63724` | Titles expire with text; WAL checkpoint retried on every purge; start-of-run maintenance recorded on the run (`metrics()["maintenance"]`) |
| `5541d2c` | Interrupted runs recovered at the next start (marked `failed` / `interrupted`, redacted, checkpointed) |
| `1df11e4` | Claim lifecycle: only affected claims superseded; first extraction `initial` |
| `74800a3` | Isolated OS temp-directory test |
| final docs commit | This section and the corrections above |

**Final test run (after the corrective pass):**
- **Engine:** 430 passed, 1 skipped (the opt-in live test), exit code 0. This includes 133 tests in the Phase 6 claim, privacy, recovery, retention, temp-artifact and lifecycle modules.
- **No-AI-library checks:** 4 passed.
- **Prototype:** 68 passed, unchanged against `c2cf132`.
- **Databases:** `data/engine.db` (`a23d7684…`) and `data/personalizer.db` (`f888f58e…`) unchanged.

### Verified by tests (temporary SQLite files, synthetic data, offline fake web)

- **Write paths:**
  - each persistence function (`add_lead`, `add_url`, `add_attempt`, `add_robots_check`, `add_document`, `trace`) stores no out-of-scope sentence, URL slug or label, and keeps in-scope text, offsets and ordinary provenance (`test_every_repo_write_function_filters_before_storing`);
  - the **search** entry point withholds titles, snippets and URLs (`test_search_entry_point…`);
  - **retrieve** filters pages and never requests or follows a sensitive URL, including through a redirect (`test_retrieve_entry_point…`);
  - **research** is checked end to end (`test_research_entry_point_end_to_end`).
- **Crash safety:** crashes injected at five stages after research (`test_interrupted_run_leaves_no_storage_level_content_on_disk`), and at six stages including during research (`test_recovery.py`). No storage-level sentence or sensitive URL slug is found in `.db`, `-wal` or `-shm` after the connection closes.
- **Recovery:** the next start marks the run `failed` / `interrupted`, removes the remaining undated place sentence, leaves a completed run's rows and status byte-identical, and leaves a run younger than the threshold alone.
- **Identity records:** "Narcotics Anonymous" no longer appears in any identity, trace or claim row. The namesake is still kept separate (`namesake:out_of_scope:<hash>`), and the subject stays `confirmed`.
- **Retention:**
  - blocked checkpoint → `checkpoint_complete: false`, text still in the main file;
  - retry with **nothing new expiring** → `true`, and the text and title are absent from every file **before** the connection closes;
  - titles cleared at +8 days, kept at +6;
  - the purge outcome (including a blocked checkpoint) is visible in the next run's metrics;
  - 30-day run and claim deletion is unchanged.
- **Claim lifecycle** (`test_claim_lifecycle.py`, 6 tests):
  - initial extraction labelled `initial`;
  - rejecting one of four pages supersedes exactly the 2 claims it supports, and the 3 unrelated claims keep their ids;
  - the corroborated claim is recomputed from the remaining page (`strongly_supported` → `tentative`);
  - reject then restore gives a full history;
  - a downgrade from `confirmed` cannot leave a claim `verified`;
  - an unresolved subject supersedes everything;
  - after text expiry only affected claims are superseded.
- **Held-out fixture:** sha256 `4764ee4f…a787` before and after (unchanged); 83/87, unchanged.

### Assumptions (not verified)

- SQLite `secure_delete` plus a TRUNCATE checkpoint removes bytes **from the database files**. Whether the filesystem or SSD still holds old blocks was not, and cannot be, verified.
- Python's `os.environ` changes reach SQLite's temp-path lookup on Windows. With `temp_store = MEMORY`, no temp file should be created at all.
- A run older than `max(1800 s, wall budget + 60 s)` cannot still be live. A process stalled beyond its own budget would be marked interrupted.

### Known limitations that remain

1. **Lexical detection.** Out-of-scope content in words the lists lack is stored until the 7-day expiry. Support and recovery groups were added as a regression; other gaps certainly exist.
2. **Two-tier categories.** Undated place and travel **sentences** are stored until end-of-run redaction, or until recovery after a crash, because identity needs them. Their **URL paths** are withheld at storage since corrective pass 2 (§12).
3. **Identity signal loss — corrected in §12.** The statement previously here, that filtering "errs towards fewer resolved subjects", **was wrong**. The final review showed the opposite: removing a sentence about a same-name person made a mixed page `strong`, attributed a following "She loves jazz." to the subject, and disabled the shared-profile conflict and the staleness cap, so subjects resolved when they should have been `conflicting` or `possible`. Fixed in §12.
4. **Hashes.** `text_sha256` and `simhash` are computed on the original text, and `claim_evidence.evidence_sha256` on short sentences. A hash can confirm a guessed text.
5. **Outside the engine:** no scheduler (nothing runs while idle); backups, sync, snapshots and SSD remnants are not controlled; logical deletion and SQLite byte removal are not forensic erasure.
6. **In memory:** page content exists in process memory during a run.

## 12. Corrective pass 2 (after the final review)

| Commit | Change |
|---|---|
| `fd93a0e` | Removed text keeps sentence boundaries: the placeholder is its own sentence, and both splitters and the pronoun linker treat placeholders as hard boundaries |
| `5abfc92` | Identity-safety signal (counts only) and its use in the resolver: downgrade, shared-profile conflict, staleness, carry-forward after redaction |
| `98d5998` | Undated place and travel URL paths withheld at storage (host kept); known-URL anchors compared in raw and stored form |
| `f0aa52b` | Documentation for this section and the corrections above |
| `c8345a2` | Release-gate fix: a page redacted after identity keeps the **more conservative** of its earlier decision and the decision recomputed from the remaining text (regression test P1) |
| `0a71087` | Note that withheld URL hashes and URL keys are unsalted |

**Re-resolution after redaction (as implemented since `c8345a2`).** When a page was redacted after identity resolution, re-resolution keeps whichever of two decisions is more conservative: the earlier decision, made on the full text, or the decision recomputed from the remaining text, including the redaction downgrade.
- The ranking is by effect: rejected = ambiguous < insufficient < possible < historical < strong < confirmed. A tie keeps the earlier decision.
- A stale `strong` or `confirmed` therefore cannot survive once the evidence behind it is removed. In P1, the page falls to `insufficient`, the subject to `possible`, and its claims are withdrawn. A reviewer override can still restore the stronger status.
- **Expired-text carry-forward is unchanged:** after the 7-day expiry nothing can be recomputed, so the earlier rule decision is kept.

**Final test run (after corrective pass 2):**
- **Engine:** 444 passed, 1 skipped (the opt-in live test), exit code 0.
- **No-AI-library checks:** 4 passed.
- **Prototype:** 68 passed, unchanged against `c2cf132`.
- **Held-out fixture:** sha256 `4764ee4f…a787`, unchanged.
- **Databases:** `data/engine.db` (`a23d7684…`, schema v2, integrity ok) and `data/personalizer.db` (`f888f58e…`) unchanged.

### Review cases (verified by `tests_engine/test_identity_safety.py`)

| Case | Observed result | Why it is safe |
|---|---|---|
| **A** One page: the subject; a same-name pastry chef in a sensitive sentence; "She loves jazz." (real write path) | Subject `confirmed` (by the official page); the mixed page **`ambiguous`**; **no claims**; flags `privacy_removed:1`, `privacy_removed_named:1` | Same outcome as with the raw text. The pronoun cannot cross the placeholder, and the page cannot support identity on its own. |
| **A2** Raw text, end-of-run redaction (place sentence), then a later identity change and re-resolution | The mixed page stays **`ambiguous`**; **no claims** (re-resolution keeps the more conservative of the earlier and recomputed decisions; here both are `ambiguous`) | Removed text can neither strengthen the page nor let a pronoun inherit the subject |
| **B** A newer page ties the subject's GitHub profile to an affiliation described only in a sensitive sentence | Subject **`conflicting`**: "…ties the same profile (github.com/priyaraman) to an affiliation described in text removed as out of scope and is newer: possible role change" | The shared-profile conflict fires again. The explanation names no clinic, and the page's URL is fully withheld. |
| **C** All support is from 2018; a newer page ties the name to a role described only in a sensitive sentence | Subject **`possible`** (staleness cap: "newer pages tie the name elsewhere") | The staleness cap applies again; no claims |
| **D** The subject's only mention is in a sensitive sentence on the official page | Subject **`insufficient_evidence`**; explanation: "1 document(s) had sentences naming the subject removed as out of scope" | **Intended:** coverage loss. The subject stays unresolved instead of being resolved from evidence that cannot be kept. |

Also verified:
- **Scoping:** the signal is per document and per run; a second run in the same database is unaffected. No removed word ("church", "choir") appears in any document, identity, trace or claim row.
- **Reviewer decisions:** a reviewer can still mark such a page `strong`, which gates its in-scope claims. Marking it `ambiguous` again supersedes exactly those claims (`identity_changed`).
- **Sentence boundaries:** for every placeholder length, offsets are unchanged; legacy placeholders are hard boundaries too.
- **URLs:** a research run fetches a travel-path page and a page reached through a redirect to a place path. Its known-URL anchor still works. No path appears in the main, WAL or SHM files across leads, search results, URLs, attempts, documents, traces, claim evidence and run input. `retrieve` and `search` behave the same.
- **Changed existing test:** `test_full_research_run_redacts_pages_and_snippets` now expects the page whose removed sentences named the subject to be `ambiguous`, so its association is no longer a claim. That is the new, more conservative behaviour.

### Assumptions (not verified)

- The subject's full-name pattern (first, optional middle, last; or "Last, First") catches the mentions that matter. Surname-only or nickname mentions in removed text are not counted.
- A count of removed sentences, without the sentence, is enough for the three resolver rules above. It cannot tell *what* the removed text said, so it is deliberately conservative.

### Remaining risks

1. **Coverage loss (by design).** A subject whose decisive evidence is in removed text stays unresolved (case D). A page with any removed sentence naming the subject never supports identity unless a reviewer decides otherwise.
2. **Search and retrieve runs** record only that text was removed, not whether it named a subject; they do not resolve identity.
3. **URL paths:**
   - the host is kept, and a host name can itself be a place (`bengaluru.example`);
   - path detection is lexical;
   - dated past-travel paths are stored by design.
   - withheld URL hashes (`withheld:out_of_scope:<hash>`, `/withheld-path:<hash>`) and URL keys (`urls.url_key`, `search_results.url_key`) are **unsalted** SHA-256 prefixes, so someone with database access can confirm a guessed URL by hashing it. Hashing behaviour is unchanged.
4. **Unchanged from §11:** lexical detection, two-tier categories for sentences, hashes of original text, no scheduler, nothing outside the engine (backups, snapshots, SSD), process memory.
