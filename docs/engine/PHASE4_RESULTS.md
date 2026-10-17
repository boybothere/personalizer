# Phase 4 results: reliable, policy-compliant fetching layer

**Date:** 2026-10-17
**Plan:** [PHASE4_PLAN.md](PHASE4_PLAN.md) (`3e8ef78`)
**Commits:**

| Commit | Contents |
|---|---|
| `d9f979c` | Core: pinning, address policy, challenges, PDF, migration 0004, metrics |
| `5acfa96` | One DNS lookup per hop, bounded decompression, hardening tests |
| `d9d711a` | `fetch-trace` command |
| `84b4b55` | Live pinned-TLS checks and live trace |
| final docs commit | This document; the hash is reported at handover |

**Numbering:** phase 4 is fetch hardening, per your instructions; platform connectors move to a later phase.

## 1. What changed

| Area | Before | After |
|---|---|---|
| **DNS rebinding / re-resolution** | Host checked, then httpx **resolved it again** to connect | **One lookup per hop.** Every answer must be public. The chosen IP is **pinned**: `PinnedTransport` connects to it and keeps `Host`/TLS SNI and certificate verification on the hostname. robots.txt reuses the hop's pinned IP. It **fails closed** (no pinned IP means no request) and checks that the real socket peer equals the pinned IP. |
| **Environment proxies** | `trust_env=True`: an `HTTP(S)_PROXY` could route around the checks | `trust_env=False` |
| **Embedded IPv4** | `64:ff9b::7f00:1` (NAT64 for 127.0.0.1) passed: Python says `is_global=True` | `is_public_ip()` unwraps IPv4-mapped, NAT64, 6to4 and Teredo. All NAT64 is refused, because Python classifies the whole `::/8` range as reserved. |
| **Numeric host tricks** | `2130706433`, `0x7f000001`, `0177.0.0.1`, `127.1` behaved differently by platform | Rejected outright (`URL_NONCANONICAL_HOST`) everywhere |
| **Connection reuse** | n/a | Keep-alive disabled, so a TLS session for one hostname is never reused for another on the same IP |
| **Bot / captcha challenges** | A 200-status challenge would be classed `JS_REQUIRED` (technical), so **the Tavily fallback would have bypassed it**. A 503 challenge was retried, then also treated as technical. | `ACCESS_CHALLENGE`, detected on 200/403/429/503 (vendor scripts, `cf-mitigated` header, interstitial titles on short pages). Checked before JavaScript detection, never retried, **never sent to the fallback**, counted as blocked. |
| **Decompression bombs** | httpx decompressed a whole chunk at once: a few KB → **5 MB in memory** before the cap acted | Own incremental gzip/deflate decompression with a hard output bound. Never more than cap + 1 bytes is held. Other encodings are refused (`Accept-Encoding: gzip, deflate`). |
| **PDF** | Generic `UNSUPPORTED_CONTENT_TYPE` | `UNSUPPORTED_PDF`: by content type (not downloaded) or `%PDF-` signature under a wrong type |
| **Audit** | No record of where requests went | `fetch_attempts.connected_ip` (migration 0004, reversible) |
| **Metrics** | Text usability only | Plus `metadata_coverage_of_usable` (title, author, language, canonical, date, site name) and `usable_rate_of_retrieved_pages` |
| **Tooling** | n/a | `python -m engine fetch-trace URL…`: per-URL policy, robots, every attempt with its pinned IP, outcome and extraction. In-memory; nothing stored. |

Unchanged: budgets, access restrictions (robots, platforms, excluded domains), retention, failure reporting, no LLM.

## 2. Tests (actually run)

| Suite | Result |
|---|---|
| Engine, deterministic (`pytest tests_engine`) | **267 passed**, 1 skipped (the live test) |
| Of which new Phase 4 hardening tests (`test_fetch_hardening.py`) | **73 passed** |
| Live smoke (`ENGINE_LIVE=1 pytest tests_engine/test_live.py`; public, non-personal pages) | **1 passed**: every real request recorded a public pinned IP |
| No-LLM checks | **4 passed** |
| Prototype (`pytest tests`) | **68 passed** |
| Prototype files against `c2cf132` | **unchanged** |
| `data/engine.db`, `data/personalizer.db` against their Phase 4 start sha256 | **unchanged**; `engine.db` still schema v2, `integrity_check` ok |

**All offline fetch tests now run through `PinnedTransport`.** Test servers route on the `Host` header, as real servers do.

### Acceptance criteria → evidence

| # | Criterion | Evidence |
|---|---|---|
| F1 | Redirects followed and re-checked per hop | `test_protocol_relative_and_cross_host_redirects_are_rechecked`, `test_redirects_to_disguised_internal_addresses_are_blocked` (3), Phase 1 redirect tests; live: httpbin `/redirect/2` |
| F2 | Loops and too many redirects | `test_redirect_loop_and_limit` (Phase 1, now on the pinned path) |
| F3 | Timeouts | `test_connect_timeout_is_retried_then_reported`, read and total timeout tests |
| F4 | HTTP error classes | `test_status_and_type_failures` (8); live 404 |
| F5 | Oversized, including compression bombs | `test_compressed_bomb_stops_at_the_decompressed_cap` (holds exactly cap + 1 bytes), `test_size_limits_declared_and_streamed`, `test_unknown_content_encoding_is_not_decoded`, `test_gzip_page_is_decoded_normally` |
| F6 | Content types and PDF honesty | `test_pdfs_and_binaries_are_reported_honestly` (4: PDF by type with 0 bytes read; PDF signature under octet-stream or HTML; PNG); live: `UNSUPPORTED_PDF`, 0 B downloaded |
| F7 | robots.txt decisions | `test_robots_semantics` (5), `test_robots_redirect_is_resolved_and_pinned`, `test_robots_redirect_to_private_address_is_not_followed`; live: Wikipedia `/w/` refused without a request |
| F8 | SSRF, including rebinding | `test_is_public_ip` (23), `test_python_alone_misses_nat64`, `test_legacy_ipv4_parsing` (11), `test_noncanonical_numeric_hosts_are_rejected_everywhere`, `test_resolved_private_addresses_are_never_requested` (8, including one bad answer among good ones), `test_rebinding_cannot_reach_a_private_address`, `test_rebinding_on_a_redirect_hop_is_refused`, `test_resolver_called_once_per_hop`, `test_connection_goes_to_the_checked_ip_with_original_host_and_sni`, `test_pinned_transport_fails_closed`, `test_socket_peer_mismatch_is_refused_without_retry`, `test_environment_proxies_are_ignored` |
| F9 | Budget exhaustion | Phase 1–3 budget tests (fetches, bytes, robots, time, leads), all now on the pinned path |
| F10 | Challenges are never bypassed | `test_challenge_on_error_status_is_access_challenge_and_not_retried` (4), `test_challenge_page_with_status_200_is_never_sent_to_the_fallback`, `test_challenge_status_is_never_sent_to_the_fallback`, `test_js_only_page_still_uses_fallback_but_article_about_captchas_is_usable` (control cases); live: Medium → `ACCESS_CHALLENGE` |
| F11 | Usable text and metadata, not just HTTP success | `test_metrics_show_usable_text_and_metadata_not_just_http_success`; live summary below |
| F12 | Pinned TLS works for real | Live smoke passed; live trace shows HTTPS through pinned IPs on Cloudflare- and Wikimedia-fronted sites |
| F13 | Prototype and databases unchanged; suites pass | Section 2 table; `test_migration_0004_on_a_copy_preserves_existing_records` (every pre-existing metric identical on a copy; original sha256 unchanged) |

## 3. Sample fetch trace (live, public, non-personal pages)

Full output: [examples/phase4_fetch_trace_live.txt](examples/phase4_fetch_trace_live.txt). `via` is the pinned, policy-checked IP actually connected to.

```
https://www.rfc-editor.org/rfc/rfc9309.html
  robots.txt: HTTP 200 -> parsed
  attempt 1: success  HTTP 200 via 104.18.20.81 82367B 390ms
  document: usable, 10315 chars; title='RFC 9309: Robots Exclusion Protocol'; author='Martijn Koster'; lang=en

https://en.wikipedia.org/wiki/Robots.txt
  attempt 1: success  HTTP 200 via 103.102.166.224 326744B 547ms
  document: usable, 24139 chars; published=2002-10-09 (jsonld:datePublished);
            canonical=https://en.wikipedia.org/wiki/Robots.txt; flags=[hidden_text_removed:325, living_document]

https://en.wikipedia.org/w/index.php?title=Robots.txt&action=history
  attempt 0: skipped (no request sent) ROBOTS_DISALLOWED

https://www.w3.org/.../dummy.pdf
  attempt 1: failure  HTTP 200 via 104.18.22.19 0B  UNSUPPORTED_PDF application/pdf (not downloaded)

https://httpbin.org/redirect/2
  attempt 1: redirect HTTP 302 via 3.225.83.162  -> https://httpbin.org/relative-redirect/1
  attempt 2: redirect HTTP 302 via 3.225.83.162  -> https://httpbin.org/get
  attempt 3: failure  HTTP 200  UNSUPPORTED_CONTENT_TYPE application/json

https://medium.com/gumgum-tech/respecting-robots-exclusion-protocol-...
  attempt 1: failure  HTTP 403 via 162.159.152.4 5380B  ACCESS_CHALLENGE
             HTTP 403; bot-protection marker: /cdn-cgi/challenge-platform/

https://www.instagram.com/natgeo/   policy: rejected URL_PLATFORM_DISCOVERY_ONLY (no request)
http://127.0.0.1:8080/              policy: rejected URL_PRIVATE_ADDRESS        (no request)
http://2130706433/                  policy: rejected URL_NONCANONICAL_HOST      (no request)
http://[::ffff:127.0.0.1]/          policy: rejected URL_PRIVATE_ADDRESS        (no request)
```

**Live metrics (12 URLs):**
- 9 fetch attempts, 5 with HTTP 2xx, 3 URLs retrieved.
- **2 usable documents**; usable rate of retrieved pages 0.667. example.com was retrieved but `TEXT_TOO_SHORT`.
- **Metadata coverage of usable documents:** title 2/2, author 2/2, language 2/2, site name 2/2, canonical link 1/2, published date 1/2.
- **Blocked or inaccessible:** 1 `ACCESS_CHALLENGE`, 1 `ROBOTS_DISALLOWED`, 1 `URL_PLATFORM_DISCOVERY_ONLY`, 1 `URL_NONCANONICAL_HOST`. The 2 private-address rejections are recorded under URL rejections.

HTTP success and usable content are reported separately: 5 × 2xx → 2 usable.

## 4. Defects found and fixed during Phase 4

| Found by | Defect | Fix |
|---|---|---|
| Inspection | DNS resolved twice (check, then connect): rebinding window | One lookup per hop, pinned connection, peer verification |
| Inspection | Environment proxies honoured | `trust_env=False` |
| Probe (`ipaddress`) | NAT64 `64:ff9b::7f00:1` reported as global | Embedded-IPv4 unwrapping; all NAT64 refused |
| Inspection | A challenge page would have been sent to the Tavily fallback | `ACCESS_CHALLENGE`, excluded from the fallback in two places |
| Test | Two DNS lookups per hop (content check plus robots) | robots.txt reuses the hop's pinned IP |
| Test | A gzip bomb expanded to 5 MB in memory before the cap | Bounded incremental decompression |
| Test | An unsupported encoding was skipped for already-read responses | Encoding checked first on every path |
| Live smoke | The live test still expected the pre-Phase 4 PDF code | Updated; the live test now also asserts public pinned IPs |

## 5. Remaining limitations

- **No JavaScript rendering** (`JS_REQUIRED`) and **no PDF text** (`UNSUPPORTED_PDF`): reported honestly, not worked around.
- **Challenge detection is pattern-based:** a new vendor or an unusual interstitial may be missed (it would then likely appear as `TEXT_TOO_SHORT` or `JS_REQUIRED`). The fallback after `JS_REQUIRED` therefore depends on detection quality: a missed challenge could still reach the provider fallback. **Recommendation:** keep the fallback disabled for real research until a labelled sample of blocked pages has been reviewed.
- **NAT64 and IPv6-only networks:** NAT64 addresses are refused outright. On an IPv6-only network with DNS64, IPv4-only sites would be unreachable. Deliberately conservative.
- **No keep-alive:** every request opens a new TCP/TLS connection, which is slower.
- **The socket-peer check needs a real network stream;** in-memory test transports can't exercise it, so it was tested with a simulated stream.
- **Single IP per hop:** if the pinned IP is down, the hop fails (it is retried on the same IP); there is no failover to the host's other addresses.
- **gzip and deflate only;** Brotli and zstd responses are refused (we don't advertise them, but a misbehaving server may send them).
- **`data/engine.db` is still schema v2** and was not upgraded. Migration 0004 was tested only on copies, as instructed.
- **Platform connectors** (RSS for Medium, Bluesky, etc.) remain future work. Medium article pages are bot-protected (`ACCESS_CHALLENGE`), so its RSS route is the only content route.
