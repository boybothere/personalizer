# Phase 4 plan: reliable, policy-compliant fetching layer

**Baseline:** `5aa6830` (Phase 3), pushed to `origin/main`. Phase 4 is delivered in new commits.
**Numbering:** the original roadmap had platform connectors as phase 4. Per your instructions, phase 4 is now **fetch-layer hardening**; connectors move to a later phase.

## Inspection findings (current fetcher, `engine/fetch/client.py`)

Phase 1 delivered robots.txt (RFC 9309), retries with backoff, `Retry-After`, manual redirects re-checked per hop, size, time and content-type limits, attempt-level accounting, and extraction with date provenance. Inspection found these gaps:

| # | Gap | Risk |
|---|---|---|
| G1 | **DNS rebinding / re-resolution.** `_address_check()` resolves the host and checks the IPs, then httpx **resolves it again** when connecting. robots.txt fetches have the same issue. | A hostile DNS server can return a public IP for the check and a private one for the connection (SSRF). |
| G2 | **Environment proxies.** The client uses `trust_env=True` (the default), so `HTTP(S)_PROXY` variables would route requests through a proxy that does its own DNS. | Address checks become meaningless; traffic leaves through an unexpected route. |
| G3 | **Embedded IPv4 in IPv6.** Python reports `64:ff9b::7f00:1` (NAT64 for 127.0.0.1) as `is_global=True`. | NAT64/DNS64 networks could reach internal IPv4 addresses. |
| G4 | **Legacy numeric hosts** (`2130706433`, `0x7f000001`, `0177.0.0.1`, `127.1`). Windows rejects them; glibc resolves them to 127.0.0.1. | Behaviour depends on the platform; ambiguous host identity. |
| G5 | **Connection reuse with pinned IPs.** Once connections are pinned to IPs, a pool keyed by IP could reuse a TLS session negotiated for another hostname on the same IP. | Wrong certificate or virtual host. |
| G6 | **Bot challenges served with HTTP 200** ("Just a moment…", captcha pages) are likely classified `JS_REQUIRED`, a *technical* failure that permits the Tavily fallback. | The fallback would **bypass an access control**. |
| G7 | PDFs are reported only as a generic `UNSUPPORTED_CONTENT_TYPE`. | PDF limitations aren't distinguishable in metrics. |
| G8 | No metadata-coverage metrics (title, author, language, canonical link). | Can't show that fetched pages yield *metadata*, not just text. |
| G9 | The IP actually connected to isn't recorded per attempt. | SSRF decisions can't be audited after the fact. |

## Design

1. **Pinned connections (G1, G5).**
   - Each hop resolves once.
   - **All** returned IPs must be public; one of them is chosen deterministically and attached to the request as `pinned_ip`.
   - A `PinnedTransport` rewrites the request to connect to that IP. It keeps the `Host` header and TLS **SNI/certificate verification** on the original hostname, using httpcore's `sni_hostname` extension.
   - It **fails closed**: a request without `pinned_ip` is refused.
   - Keep-alive is disabled, so TLS sessions are never reused across hostnames.
   - robots.txt fetches and every redirect hop use the same path.
   - **Every** fetcher transport, including test transports, is wrapped in `PinnedTransport`. Offline tests therefore exercise the pinned path.
2. **No environment proxies (G2):** `trust_env=False`.
3. **One address-policy function (G3, G4):** `is_public_ip()` checks Python's `is_global` **and** unwraps IPv4-mapped, NAT64 (`64:ff9b::/96`, `64:ff9b:1::/48`), 6to4 and Teredo addresses, rejecting any non-global embedded IPv4 address. Non-canonical numeric hosts are rejected outright (`URL_NONCANONICAL_HOST`) on every platform.
4. **Access challenges (G6):** a new `ACCESS_CHALLENGE` code for 200-status bot or captcha interstitials. It is checked **before** JavaScript detection, counted as blocked or inaccessible, and **never** eligible for the provider fallback.
5. **PDF honesty (G7):** a distinct `UNSUPPORTED_PDF` code ("PDF text extraction not supported"). The body is not downloaded.
6. **Metadata coverage (G8):** extraction metrics gain `with_title`, `with_author`, `with_language`, `with_canonical_link`, `with_published_date`, plus a usable-of-retrieved rate.
7. **Audit (G9):** migration 0004 adds `fetch_attempts.connected_ip` (reversible).
8. A `python -m engine fetch-trace URL…` command prints a per-URL attempt trace: hops, pinned IPs, robots decision, outcome and extraction result.

Budgets, access restrictions, retention, failure reporting and the no-LLM rule are unchanged.

## Acceptance criteria

| # | Criterion | Evidence |
|---|---|---|
| F1 | Redirects: relative, absolute, protocol-relative and cross-host are followed; each hop is re-checked against policy, robots.txt and address rules | Offline tests |
| F2 | Redirect loops and too many redirects are reported precisely | Offline tests |
| F3 | Connect, read and total timeouts are reported, with retries counted | Offline tests |
| F4 | HTTP errors are classified (401/403/451 access, 404/410 not found, 429/5xx retried, other 4xx) | Offline tests |
| F5 | Oversized responses (declared, streamed, **compressed bomb**) stop at the cap | Offline tests |
| F6 | Content types: HTML, XHTML, RSS/Atom and text are supported; **PDF reported as `UNSUPPORTED_PDF`**; others rejected without download; sniffing for missing or generic types | Offline tests |
| F7 | robots.txt: allow, disallow, agent-specific rules, 4xx allows, 5xx/unreachable disallows, redirects (address-checked), crawl-delay | Offline tests |
| F8 | SSRF: private, loopback, link-local, CGNAT, metadata, IPv4-mapped, NAT64, 6to4, unspecified and non-canonical numeric hosts are all refused; **rebinding** is defeated (the connection goes to the checked IP; DNS is resolved once per hop); unpinned requests are refused; proxies are ignored | Offline tests |
| F9 | Budget exhaustion (fetches, bytes, robots, time) mid-run gives precise codes and partial status | Offline tests |
| F10 | Bot or captcha interstitials give `ACCESS_CHALLENGE`, never the fallback | Offline tests |
| F11 | Fetched pages yield **usable text and metadata**, shown by the metrics | Offline fixture run, plus a live run on public, **non-personal** pages (keyless) |
| F12 | Pinned TLS works for real: SNI and certificate verification on the hostname while connecting to the IP | Live smoke test (`ENGINE_LIVE=1`), public non-personal sites |
| F13 | The prototype, `data/personalizer.db` and `data/engine.db` are unchanged; engine, prototype and no-LLM suites pass | Checksums and test runs |

## Non-goals

- No JavaScript rendering, no PDF text extraction, no connectors.
- No live personal research.
- No `db upgrade` on the original database; migration 0004 is tested on copies only.
