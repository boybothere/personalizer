# Decision log: research engine rebuild

Status key: **Decided** (approved direction, or a direct requirement) · **Approved** (confirmed by you, 2026-10-17, phase-2 instructions) · **⚖ Review** (needs a legal or terms review before production use).

Evidence for platform claims is in [PLATFORMS.md](PLATFORMS.md); every probe there is dated and reproducible.

## D1 Runtime and scope

| # | Decision | Status | Rationale |
|---|---|---|---|
| D1.1 | The engine (`engine/`) makes **no LLM calls**. It doesn't import the `anthropic`, `openai` or Gemini SDKs, and tests fail if it does or if it sends traffic to an LLM host. | Decided | Requirement 1 |
| D1.2 | The prototype (`personalizer/`, `tests/`, `eval/`, `data/personalizer.db`) is **left byte-for-byte untouched**: it's not imported, moved or migrated. Checksums of 54 files were recorded before work began. | Decided | Requirement 5 |
| D1.3 | The engine is a new package with its own tests (`tests_engine/`), its own versioned database (`data/engine.db`) and its own docs (`docs/engine/`). | Decided | Isolation; the prototype keeps working |
| D1.4 | The work is phased: **1** observability, storage and retrieval → **2** search layer → **3** planner and controller → **4** platform connectors → **5** identity → **6** preference extraction (evaluated separately) → **7** validation → **8** API. No UI work in this phase. | Decided | Requirement 6 |

## D2 Platform access

| # | Decision | Status |
|---|---|---|
| D2.1 | Each platform is given one of these **access modes**, based on tested evidence rather than assumption: `content_api` (official API returns content), `oembed_record` (official embed endpoint returns one known post), `feed` (RSS/Atom), `fetch` (robots.txt allows ordinary pages), `discovery_only` (only search-index leads are possible), `unavailable_here` (blocked from this network), or `excluded` (inappropriate source). | Decided |
| D2.2 | A platform is classed `discovery_only` **only after** its legitimate access routes have been listed and tested (see PLATFORMS.md). Today that means **Instagram, Facebook and LinkedIn**: robots.txt disallows everything for generic bots, and their official APIs need an app review plus a business account (Instagram, Facebook) or partner status (LinkedIn). | **Approved** |
| D2.3 | **X**: robots.txt disallows everything, but the official **oEmbed endpoint returned post text, author and date without a key** (tested). **Disabled for real research** until the terms are reviewed and the intended retrieval, storage and analysis use is confirmed to be permitted. A working endpoint does not by itself establish permission. The official API is pay-per-use (about $0.005 per post read) and stays off. | **Approved** (disabled) · ⚖ |
| D2.4 | **Reddit**: robots.txt disallows everything, and the Data API needs approval (commercial use needs an agreement). oEmbed returned post content without a key (tested). Same treatment as X: **disabled for real research** until the terms review confirms the use. | **Approved** (disabled) · ⚖ |
| D2.5 | **TikTok**: the oEmbed request redirects to `/in/about` from this network (geo-blocked in India), so it can't be tested here. The mode is `unavailable_here`, *not* `discovery_only`. | Decided (with the evidence) |
| D2.6 | **Bluesky** (profile, author feed, actor search; post search needs auth), **Mastodon** (public search), **Medium and Substack** (RSS; robots.txt allows), **GitHub** (REST), **YouTube** (oEmbed, plus the Data API with a free key; no official route to other people's transcripts), **podcasts** (iTunes Search plus RSS) and **Wikipedia/Wikidata** are content or metadata sources. | Decided |
| D2.7 | People-search and data-broker sites are **excluded**, whatever their access terms. | Decided |
| D2.8 | Access-control bypasses are **never** used: no fake accounts, no logged-in sessions, no paywall or robots.txt evasion, no headless browsers to defeat bot protection. | Decided |

## D3 Source provenance

| # | Decision | Status |
|---|---|---|
| D3.1 | Every piece of content has an **evidence kind**: `fetched_page` (our own fetch, robots.txt permitting), `feed_entry`, `api_record`, `oembed_record`, `provider_extract` (page text supplied by a search provider) or `search_snippet`. | Decided |
| D3.2 | **Search snippets are leads.** They're stored with provider, query, rank and retrieval time, and with explicit limitations attached: *provider-generated, truncated, possibly stale, no surrounding context, not retrieved by us*. A snippet can support a finding **at most at `tentative`**, can never be `verified`, and is always labelled as snippet-only. | Decided |
| D3.3 | **Provider extracts** (for example Tavily `raw_content`) are a separate evidence kind, never counted as our fetch. They're used **only after a technical failure** of our own retrieval: timeout, network error, 5xx, oversize, unsupported type, JavaScript-only, empty extraction. **Never** after robots.txt (including unreachable robots), 401/403/451, 404, 429 or a policy rejection. Only from providers whose terms allow persistence (Tavily). Enforced in code and tested for each case. | **Approved** |
| D3.4 | Dates come only from the document itself (JSON-LD, then meta tags, then `<time>` elements, then htmldate heuristics) or from its URL pattern. Each date records **where it came from**; disagreements are recorded; otherwise the date is `unknown`. Dates are never taken from snippets and never default to "today". | Decided |
| D3.5 | All metrics are **derived from stored records** (leads, URLs, fetch attempts, documents), not from separately incremented counters, so they can't drift from what actually happened. | Decided |

## D4 Privacy boundaries

| # | Decision | Status |
|---|---|---|
| D4.1 | Not collected: religion, politics, health, sexuality, caste or ethnicity, family members and relationships, home location, contact details, identity numbers, finances, legal history. | Decided |
| D4.2 | **Travel and places**: kept only as dated statements made by the person. **Dropped**: current or upcoming whereabouts ("in Bengaluru this week"), anything that would infer where they live, photo geolocation, movement patterns. Applied in claim extraction (phase 6), with tests. | **Approved** |
| D4.3 | Other people who appear in the subject's content (family, friends) are not recorded. | Decided |
| D4.4 | Retention: runs 30 days; **document text and search snippets 7 days**, then set to null (hashes kept); per-subject and per-run deletion. Retention runs automatically before every run. `secure_delete` plus a WAL checkpoint means deleted text is overwritten on disk (verified by scanning the database file in tests). The cross-run spend ledger survives deletion and holds no personal data. | **Approved** |
| D4.5 | Deliberately researching personal preferences increases exposure under the DPDP Act: the s. 3(c)(ii) exemption covers only what the person published *themselves*. See the draft analysis in [DPDP_ANALYSIS.md](DPDP_ANALYSIS.md). **No production use before qualified Indian legal review.** Public availability is not treated as blanket permission. | **Approved** · ⚖ |

## D5 Budgets and stopping

| # | Decision | Status |
|---|---|---|
| D5.1 | **Hard limits** are configurable per run and enforced before each action: search requests, fetch attempts (retries included), robots.txt requests, total bytes, wall-clock time, estimated cost (USD), plus per-host and global concurrency. When a limit is hit, the run stops with `stop_reason=budget_<kind>` and **status PARTIAL**. | Decided |
| D5.2 | Defaults are deliberately conservative: 40 search requests, 80 fetch attempts, 25 MB, 240 s, **$0.25 hard cost ceiling** per run. These are **ceilings, not targets**; the adaptive rules (phase 3) usually stop a run sooner. | **Approved (provisional)** |
| D5.3 | **Adaptive stopping** (controller, phase 3). An objective is *saturated* when its last *k* queries add fewer than *m* new unique URLs, or fewer than *m* new origin groups. A run stops early when every active objective is saturated, covered (it has evidence at or above the target strength), or exhausted; diversity of sources (distinct registrable domains and origin groups per category) is part of "covered". Every query records its reason, and its marginal yield afterwards. | Decided (design) |
| D5.4 | Live search tests use **existing free credits only**; no paid billing. Enforced by the cross-run **provider ledger**: monthly caps below each free allowance (Brave 900 requests, Tavily 900 credits, Exa $9). A request that would cross a cap is not sent (`SEARCH_FREE_TIER_GUARD`). | **Approved** |

## D6 Validation strategy

| # | Decision | Status |
|---|---|---|
| D6.1 | **Identity status** (confirmed / strongly supported / possible / ambiguous / rejected) is stored separately from **claim validation status** (verified / strongly supported / tentative / conflicting / rejected / insufficient evidence). Every decision stores the signals behind it and can be superseded, so a wrong decision can be reversed. | Decided |
| D6.2 | Corroboration counts **origin groups**, not URLs. Sources share an origin group on near-duplicate text (simhash), the same canonical link, syndication markers, or the same quoted passage. | Decided |
| D6.3 | **Preference extraction is a separately evaluated subsystem.** It gets its own labelled corpus covering explicit like or dislike, negation, deterministically detectable sarcasm markers, quotations versus authored text, repeated behaviour, single activities and indirect associations. Precision and recall are reported per class. A match is never promoted to a preference when the grammar is uncertain: it becomes `unverified_inference` or is dropped, with the reason recorded. | Decided |
| D6.4 | Run quality gates: **FAILED** = no usable documents or every fetch failed; **PARTIAL** = stopped by a budget, timeout or cancellation, or leads left unprocessed; **COMPLETED** = all planned work processed. "Completed" never means "comprehensive"; coverage is always reported. | Decided |

## D7 Search providers (phase 2)

| # | Decision | Status |
|---|---|---|
| D7.1 | **Storage follows each provider's terms.** Brave ToS s3(b)(i) forbids storing or caching search results beyond transient operational storage, so Brave is **transient**. Exa ToS s4.2(a) forbids copying information obtained through the Services, so Exa is **transient** pending review. Tavily's ToS has no storage restriction, so Tavily **persists** snippets, subject to the 7-day retention. For transient providers, titles and snippets are never written; after the run, URLs we did not request ourselves are reduced to a hash. Tested by scanning the database file. | Decided · ⚖ |
| D7.2 | **Attribution:** Brave s4(d) requires "POWERED BY BRAVE" in any customer application using its results. Applies when a UI shows Brave-derived results (phase 8). | Noted |
| D7.3 | **Tavily AUP:** no surveillance, stalking or doxxing, and no obtaining special-category data; no unsupervised automated decisions about people (ToS s3.2(xvi), s6.6). Consistent with D4; recorded here as a binding provider-level constraint. | Decided |
| D7.4 | **Provider-level cost and quality metrics are required before relying on a provider:** requests, failures, latency, off-target rate, exclusive URLs, downstream usable documents, cost per usable document; per run and aggregated across runs. | Decided |
| D7.5 | Only the Tavily key is configured (the Brave and Exa entries in `.env` are template comments). Brave and Exa are implemented and tested against mocks; **not live-tested**. | Fact |
| D7.6 | Medium article pages returned **403** to our bot (5 of 5, live) although robots.txt allows them. Not bypassed; the fallback is not used after a 403. The Medium content route is RSS. | Decided (evidence) |

## D8 Adaptive planner (phase 3)

| # | Decision | Status |
|---|---|---|
| D8.1 | Query selection is a **deterministic scored queue**: priority × coverage gap × diversity × discovery boost / (1 + low-yield streak). The components are stored with every decision. No randomness and no LLM. | Decided |
| D8.2 | The **provisional identity signal** (name plus organisation, domain, known URL or role) is used only for planning; it is never shown as an identity decision. While it is not `supported`, only identity queries run. If identity remains unresolved, the run **fails** (`IDENTITY_UNRESOLVED`). | Decided |
| D8.3 | **Status semantics:** `completed` only for natural planner stops (`PLAN_RESOLVED`, `DIMINISHING_RETURNS`), always with per-objective coverage gaps. Any budget, provider, time or step-ceiling stop is `partial` (or `failed` if nothing usable was found). Never `completed`. | Decided |
| D8.4 | **Failed provider queries don't use up query variants**; provider faults stop the run via `PROVIDER_ERRORS` / `NO_PROVIDER_AVAILABLE`, not via objective exhaustion. | Decided |
| D8.5 | **No implicit migration of existing databases.** `connect()` migrates only new, empty databases; others need `engine db upgrade`. This protects the Phase 1/2 records. | Decided |
| D8.6 | **No preference inference in planning:** focus topics produce neutral queries (the topic word only; no "love/favourite/enjoy" wording). Planner outputs are queries and documents, never preferences (phase 6). | Decided |
| D8.7 | Scoring weights and windows are **uncalibrated defaults**, to be evaluated on consented subjects before relying on them. | Noted |

## D9 Fetch hardening (phase 4)

| # | Decision | Status |
|---|---|---|
| D9.1 | **Pinned connections:** each hop resolves once; every answer must be public; the request connects to the checked IP, with Host/SNI and certificate verification on the hostname. Requests without a pinned IP are refused (fail closed); the socket peer must equal the pinned IP. robots.txt uses the same path. | Decided |
| D9.2 | **Address policy** in one function (`is_public_ip`): Python `is_global`, plus unwrapping of IPv4-mapped, NAT64, 6to4 and Teredo. **All NAT64 is refused** (conservative). Non-canonical numeric hosts are rejected on every platform. | Decided |
| D9.3 | **No environment proxies** (`trust_env=False`) and **no keep-alive** (no TLS session reuse across hostnames on one IP). | Decided |
| D9.4 | **Bot or captcha interstitials are access controls** (`ACCESS_CHALLENGE`): never retried, never solved, **never sent to the provider fallback**, counted as blocked. | Decided |
| D9.5 | **Bounded decompression:** we accept only gzip and deflate, decoded incrementally with a hard output cap; other encodings are refused. | Decided |
| D9.6 | **PDFs are reported, not fetched** (`UNSUPPORTED_PDF`), until PDF extraction is approved and built. | Decided |
| D9.7 | Because challenge detection is pattern-based, **keep the provider-text fallback disabled for real research** until a labelled sample of blocked pages has been reviewed. | Recommendation (needs your decision) |

## Open items needing your input

1. ⚖ items: X/Reddit terms review; Exa storage terms (D7.1); DPDP legal review (DPDP_ANALYSIS.md).
2. Optional: Brave/Exa keys, if you want those providers live-tested (free credits only).
3. A demonstration subject for phases 5+ (a consenting person is recommended).
