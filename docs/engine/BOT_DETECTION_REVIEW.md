# Bot-page detection: limitations, and why the provider-text fallback is OFF

**Status (2026-10-17):** the Tavily page-text fallback is **disabled for real research** (`ENGINE_PROVIDERS_FALLBACK_ENABLED=false`, the default). Turning it on requires your explicit approval after the review below. Access controls are never bypassed.

## Why this matters

The fallback replaces our own fetch with a provider's copy of a page, but only after a *technical* failure (timeout, 5xx, JavaScript-only, oversize). If a bot-protection page is **misclassified** as a technical failure, especially as `JS_REQUIRED`, the fallback would hand us content the site deliberately withheld. That would be an access-control bypass.

## What detection covers (`engine/fetch/challenge.py`)

| Signal | Applies to |
|---|---|
| `cf-mitigated: challenge` header (Cloudflare) | 403 / 429 / 503 |
| Vendor script or markup markers: Cloudflare challenge platform, DataDome (`captcha-delivery.com`), Imperva/Incapsula, PerimeterX/HUMAN (`px-captcha`), Akamai bot manager, hCaptcha/reCAPTCHA explicit render | Any status (first 64 KB of an error body; the full page for 200s) |
| Interstitial titles ("Just a moment…", "Attention Required! \| Cloudflare", "Verify you are human", "Access denied", "Pardon our interruption", …) | **Short pages only** (< 1,500 characters of text), to avoid flagging articles *about* captchas |

Verified live: Medium article pages → `ACCESS_CHALLENGE` (`/cdn-cgi/challenge-platform/` marker on a 403).

## Known limitations

1. **Pattern-based.** New vendors, renamed scripts, or custom in-house bot walls aren't recognised. They would most likely appear as `JS_REQUIRED`, `TEXT_TOO_SHORT` or `HTTP_ACCESS_CONTROLLED`. The first of these is a fallback trigger, which is the residual risk.
2. **Soft blocks served as normal pages:** a site that returns a plausible but degraded page to bots (a teaser or truncated article) can't be detected.
3. **Long interstitials:** an interstitial with more than 1,500 characters of text and no vendor marker would be missed by the title rule.
4. **Localised titles:** non-English challenge titles aren't in the title list.
5. **No labelled evaluation yet:** precision and recall of the detector are **unmeasured**.

## Review steps before the fallback could be re-enabled (proposed)

1. Build a labelled sample of 50–100 pages that our fetch reported as `JS_REQUIRED` / `TEXT_TOO_SHORT`, from public, non-personal sites. Label each one: genuine JavaScript app, bot wall, or other.
2. Measure how many bot walls were misclassified as technical failures. **The target is zero** in the sample; any miss becomes a new marker plus a regression test.
3. Optionally restrict the fallback to `NETWORK_TIMEOUT`, `NETWORK_ERROR`, `HTTP_SERVER_ERROR` and `RESPONSE_TOO_LARGE` (no extraction-stage triggers). That removes the misclassification route entirely.
4. Record the decision and evidence in DECISIONS.md, then turn the switch on only with your approval.
