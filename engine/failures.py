"""Failure codes. Every rejected URL, failed fetch, unusable document and degraded run carries one."""

from __future__ import annotations

from enum import Enum


class Code(str, Enum):
    # URL policy
    URL_INVALID = "URL_INVALID"
    URL_UNSUPPORTED_SCHEME = "URL_UNSUPPORTED_SCHEME"
    URL_CREDENTIALS = "URL_CREDENTIALS"
    URL_PRIVATE_ADDRESS = "URL_PRIVATE_ADDRESS"
    URL_DNS_FAILURE = "URL_DNS_FAILURE"
    URL_EXCLUDED_DOMAIN = "URL_EXCLUDED_DOMAIN"
    URL_PLATFORM_DISCOVERY_ONLY = "URL_PLATFORM_DISCOVERY_ONLY"
    URL_PLATFORM_CONNECTOR_ONLY = "URL_PLATFORM_CONNECTOR_ONLY"
    URL_PLATFORM_UNAVAILABLE = "URL_PLATFORM_UNAVAILABLE"
    # robots
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    ROBOTS_UNREACHABLE = "ROBOTS_UNREACHABLE"
    # HTTP / network
    HTTP_ACCESS_CONTROLLED = "HTTP_ACCESS_CONTROLLED"
    HTTP_NOT_FOUND = "HTTP_NOT_FOUND"
    HTTP_RATE_LIMITED = "HTTP_RATE_LIMITED"
    HTTP_CLIENT_ERROR = "HTTP_CLIENT_ERROR"
    HTTP_SERVER_ERROR = "HTTP_SERVER_ERROR"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    REDIRECT_BLOCKED = "REDIRECT_BLOCKED"
    REDIRECT_LOOP = "REDIRECT_LOOP"
    TOO_MANY_REDIRECTS = "TOO_MANY_REDIRECTS"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    # extraction
    DECODE_ERROR = "DECODE_ERROR"
    EXTRACTION_EMPTY = "EXTRACTION_EMPTY"
    TEXT_TOO_SHORT = "TEXT_TOO_SHORT"
    JS_REQUIRED = "JS_REQUIRED"
    FEED_INVALID = "FEED_INVALID"
    FEED_EMPTY = "FEED_EMPTY"
    NEAR_DUPLICATE = "NEAR_DUPLICATE"
    # budgets / run control
    BUDGET_LEADS = "BUDGET_LEADS"
    BUDGET_SEARCHES = "BUDGET_SEARCHES"
    BUDGET_FETCHES = "BUDGET_FETCHES"
    BUDGET_ROBOTS = "BUDGET_ROBOTS"
    BUDGET_BYTES = "BUDGET_BYTES"
    BUDGET_TIME = "BUDGET_TIME"
    BUDGET_COST = "BUDGET_COST"
    CANCELLED = "CANCELLED"
    # search providers
    SEARCH_NOT_CONFIGURED = "SEARCH_NOT_CONFIGURED"
    SEARCH_AUTH_ERROR = "SEARCH_AUTH_ERROR"
    SEARCH_QUOTA_EXHAUSTED = "SEARCH_QUOTA_EXHAUSTED"
    SEARCH_FREE_TIER_GUARD = "SEARCH_FREE_TIER_GUARD"
    SEARCH_RATE_LIMITED = "SEARCH_RATE_LIMITED"
    SEARCH_BAD_REQUEST = "SEARCH_BAD_REQUEST"
    SEARCH_PROVIDER_ERROR = "SEARCH_PROVIDER_ERROR"
    SEARCH_TIMEOUT = "SEARCH_TIMEOUT"
    SEARCH_BAD_RESPONSE = "SEARCH_BAD_RESPONSE"
    SEARCH_PROVIDER_DISABLED = "SEARCH_PROVIDER_DISABLED"
    SEARCH_UNSUPPORTED_SPEC = "SEARCH_UNSUPPORTED_SPEC"
    # planner stop reasons (phase 3)
    PLAN_RESOLVED = "PLAN_RESOLVED"
    DIMINISHING_RETURNS = "DIMINISHING_RETURNS"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    NO_PROVIDER_AVAILABLE = "NO_PROVIDER_AVAILABLE"
    PROVIDER_ERRORS = "PROVIDER_ERRORS"
    # run quality gates
    NO_LEADS = "NO_LEADS"
    NO_USABLE_DOCUMENTS = "NO_USABLE_DOCUMENTS"
    ALL_FETCHES_FAILED = "ALL_FETCHES_FAILED"
    WORK_UNPROCESSED = "WORK_UNPROCESSED"


EXPLANATIONS: dict[Code, str] = {
    Code.URL_INVALID: "The URL could not be parsed.",
    Code.URL_UNSUPPORTED_SCHEME: "Only http and https URLs are retrieved.",
    Code.URL_CREDENTIALS: "URLs containing credentials are never requested.",
    Code.URL_PRIVATE_ADDRESS: "The host resolves to a private, loopback or otherwise non-public address.",
    Code.URL_DNS_FAILURE: "The host name could not be resolved.",
    Code.URL_EXCLUDED_DOMAIN: "The domain is excluded by policy (people-search, data broker, leak or records site).",
    Code.URL_PLATFORM_DISCOVERY_ONLY: "The platform permits no automated page access and has no enabled content route; "
                                      "the URL is kept as a lead only.",
    Code.URL_PLATFORM_CONNECTOR_ONLY: "Pages on this platform are not fetched; content is available only through its "
                                      "official connector, which is disabled or not yet implemented.",
    Code.URL_PLATFORM_UNAVAILABLE: "The platform is not reachable from this network (for example geo-blocked).",
    Code.ROBOTS_DISALLOWED: "robots.txt disallows this URL for our user agent.",
    Code.ROBOTS_UNREACHABLE: "robots.txt returned a server error or could not be fetched; per RFC 9309 the site is "
                             "treated as fully disallowed.",
    Code.HTTP_ACCESS_CONTROLLED: "The server requires authentication, payment or otherwise denied access (401/402/403/407/451).",
    Code.HTTP_NOT_FOUND: "The page does not exist (404/410).",
    Code.HTTP_RATE_LIMITED: "The server rate-limited the request (429) and retries were exhausted or Retry-After was too long.",
    Code.HTTP_CLIENT_ERROR: "The server rejected the request with another 4xx status.",
    Code.HTTP_SERVER_ERROR: "The server failed (5xx) and retries were exhausted.",
    Code.NETWORK_TIMEOUT: "Connecting or reading timed out and retries were exhausted.",
    Code.NETWORK_ERROR: "A network error occurred and retries were exhausted.",
    Code.REDIRECT_BLOCKED: "A redirect pointed to a URL rejected by policy.",
    Code.REDIRECT_LOOP: "Redirects formed a loop.",
    Code.TOO_MANY_REDIRECTS: "More redirects than allowed.",
    Code.RESPONSE_TOO_LARGE: "The response exceeded the per-page size limit.",
    Code.UNSUPPORTED_CONTENT_TYPE: "The content type is not supported in this phase (supported: HTML, XHTML, RSS/Atom, plain text).",
    Code.DECODE_ERROR: "The response body could not be decoded as text.",
    Code.EXTRACTION_EMPTY: "The page was retrieved but no main text could be extracted.",
    Code.TEXT_TOO_SHORT: "The extracted main text is shorter than the usable minimum.",
    Code.JS_REQUIRED: "The page appears to render its content with JavaScript; no headless browser is used.",
    Code.FEED_INVALID: "The feed could not be parsed.",
    Code.FEED_EMPTY: "The feed parsed but contained no entries.",
    Code.NEAR_DUPLICATE: "The text is a near-duplicate of another document in this run.",
    Code.BUDGET_LEADS: "The lead limit for this run was reached.",
    Code.BUDGET_SEARCHES: "The search request limit for this run was reached.",
    Code.BUDGET_FETCHES: "The fetch attempt limit for this run was reached.",
    Code.BUDGET_ROBOTS: "The robots.txt request limit for this run was reached.",
    Code.BUDGET_BYTES: "The total download size limit for this run was reached.",
    Code.BUDGET_TIME: "The wall-clock time limit for this run was reached.",
    Code.BUDGET_COST: "The cost limit for this run was reached.",
    Code.CANCELLED: "The run was cancelled.",
    Code.SEARCH_NOT_CONFIGURED: "The provider has no API key configured.",
    Code.SEARCH_AUTH_ERROR: "The provider rejected the API key (401/403).",
    Code.SEARCH_QUOTA_EXHAUSTED: "The provider reports the plan or credit allowance is used up (402/432/433); "
                                 "the provider is disabled for the rest of the run.",
    Code.SEARCH_FREE_TIER_GUARD: "Sending this request would exceed the configured monthly free-allowance cap; "
                                 "it was not sent, so no paid usage can occur.",
    Code.SEARCH_RATE_LIMITED: "The provider rate-limited the request and retries were exhausted.",
    Code.SEARCH_BAD_REQUEST: "The provider rejected the request parameters (400/422).",
    Code.SEARCH_PROVIDER_ERROR: "The provider failed (5xx) and retries were exhausted.",
    Code.SEARCH_TIMEOUT: "The provider did not respond in time and retries were exhausted.",
    Code.SEARCH_BAD_RESPONSE: "The provider returned a response that could not be parsed.",
    Code.SEARCH_PROVIDER_DISABLED: "The provider was disabled earlier in this run (quota or auth failure).",
    Code.SEARCH_UNSUPPORTED_SPEC: "The provider cannot express this query specification.",
    Code.PLAN_RESOLVED: "Every objective was covered, saturated or exhausted; the planner stopped naturally. "
                        "Coverage gaps, if any, are listed per objective.",
    Code.DIMINISHING_RETURNS: "Recent queries added no new relevant evidence; further searching was judged not "
                              "worthwhile. Coverage gaps are listed per objective.",
    Code.IDENTITY_UNRESOLVED: "The identity objective finished without enough supporting evidence to attribute "
                              "documents to this person; other research was not run.",
    Code.NO_PROVIDER_AVAILABLE: "No search provider can accept further requests (quota exhausted, free-tier guard, "
                                "authentication failure or not configured).",
    Code.PROVIDER_ERRORS: "Several consecutive search requests failed; the run stopped rather than retry blindly.",
    Code.NO_LEADS: "The run had no leads to process.",
    Code.NO_USABLE_DOCUMENTS: "No document yielded usable text; the run cannot support any finding.",
    Code.ALL_FETCHES_FAILED: "Every attempted fetch failed.",
    Code.WORK_UNPROCESSED: "Some accepted URLs were never processed (budget, time or cancellation).",
}

BUDGET_CODES = {Code.BUDGET_LEADS, Code.BUDGET_SEARCHES, Code.BUDGET_FETCHES, Code.BUDGET_ROBOTS,
                Code.BUDGET_BYTES, Code.BUDGET_TIME, Code.BUDGET_COST}

# Planner stops that mean "the planner finished by design" (not cut short by a limit or a fault).
NATURAL_STOPS = {Code.PLAN_RESOLVED, Code.DIMINISHING_RETURNS}

# "Blocked or inaccessible": we deliberately did not, or were not allowed to, obtain the content.
BLOCKED_CODES = {Code.URL_PLATFORM_DISCOVERY_ONLY, Code.URL_PLATFORM_CONNECTOR_ONLY, Code.URL_PLATFORM_UNAVAILABLE,
                 Code.URL_EXCLUDED_DOMAIN, Code.ROBOTS_DISALLOWED, Code.ROBOTS_UNREACHABLE,
                 Code.HTTP_ACCESS_CONTROLLED}


def explain(code: str | Code | None) -> str | None:
    if code is None:
        return None
    try:
        return EXPLANATIONS[Code(code)]
    except ValueError:
        return None
