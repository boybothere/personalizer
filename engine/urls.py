"""URL canonicalisation and static policy (no network). DNS/SSRF checks happen per request in fetch/."""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from tld import get_fld

from .config import Platforms as PlatformSwitches
from .failures import Code
from .fetch.netpolicy import is_noncanonical_numeric, is_public_ip
from .platforms import Mode, connector_enabled, is_excluded, platform_for

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id", "utm_name",
    "gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid", "igsh", "ref_src", "ref_url",
    "_hsenc", "_hsmi", "si", "feature", "trk", "trackingid",
}
HOST_ALIASES = {"twitter.com": "x.com", "mobile.twitter.com": "x.com", "m.youtube.com": "youtube.com",
                "m.facebook.com": "facebook.com", "mobile.x.com": "x.com", "old.reddit.com": "reddit.com"}


@dataclass(frozen=True)
class CanonicalURL:
    url: str  # canonical form used for requests
    key: str  # identity used for de-duplication (scheme-insensitive, www-insensitive)
    domain: str  # host without leading www.
    registrable: str  # e.g. bbc.co.uk


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: Code | None = None
    detail: str | None = None
    platform: str | None = None


class InvalidURL(ValueError):
    pass


def _referral(key: str, value: str) -> bool:
    """`ref`/`source` carrying a host name is referral tracking; other values (e.g. git refs) are meaningful."""
    return key.lower() in {"ref", "source"} and "." in value and "/" not in value and " " not in value


def canonicalize(raw: str) -> CanonicalURL:
    raw = (raw or "").strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise InvalidURL(str(exc)) from exc
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".").lower()
    if not scheme or not host:
        raise InvalidURL(f"missing scheme or host: {raw!r}")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidURL(f"bad host: {host!r}") from exc
    alias = HOST_ALIASES.get(host.removeprefix("www."))
    netloc = alias or host  # request host: keep "www." as given unless an alias applies
    bare = alias or host.removeprefix("www.")  # identity host
    path = quote(unquote(parts.path or "/"), safe="/:@!$&'()*+,;=-._~%")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS and not _referral(k, v)]
    if bare == "youtu.be" and path.strip("/"):  # short links -> watch URLs
        query = [("v", path.strip("/"))] + [(k, v) for k, v in query if k != "v"]
        bare, netloc, path = "youtube.com", "www.youtube.com", "/watch"
    query.sort()
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    url = urlunsplit((scheme, netloc, path, urlencode(query), ""))
    key_path = path.rstrip("/") or "/"
    key_src = f"{bare}{':' + str(port) if netloc.endswith(f':{port}') else ''}{key_path}?{urlencode(query)}"
    try:
        registrable = get_fld(f"http://{bare}", fail_silently=True) or bare
    except Exception:  # noqa: BLE001 - tld raises various errors for unusual hosts
        registrable = bare
    return CanonicalURL(url=url, key=hashlib.sha256(key_src.encode()).hexdigest()[:32], domain=bare,
                        registrable=registrable)


def static_policy(raw: str, canonical: CanonicalURL | None, switches: PlatformSwitches) -> PolicyDecision:
    """Checks that need no network. Order matters: the first failing rule is reported."""
    if canonical is None:
        return PolicyDecision(False, Code.URL_INVALID, f"cannot parse {raw[:200]!r}")
    parts = urlsplit(raw.strip())
    if parts.scheme.lower() not in {"http", "https"}:
        return PolicyDecision(False, Code.URL_UNSUPPORTED_SCHEME, parts.scheme)
    if parts.username or parts.password:
        return PolicyDecision(False, Code.URL_CREDENTIALS)
    if is_noncanonical_numeric(canonical.domain):
        return PolicyDecision(False, Code.URL_NONCANONICAL_HOST, canonical.domain)
    try:
        ip = ipaddress.ip_address(canonical.domain.strip("[]"))
        if not is_public_ip(ip):
            return PolicyDecision(False, Code.URL_PRIVATE_ADDRESS, str(ip))
    except ValueError:
        if canonical.domain in {"localhost"} or canonical.domain.endswith((".localhost", ".local", ".internal")):
            return PolicyDecision(False, Code.URL_PRIVATE_ADDRESS, canonical.domain)
    if is_excluded(canonical.domain):
        return PolicyDecision(False, Code.URL_EXCLUDED_DOMAIN, canonical.registrable)
    platform = platform_for(canonical.domain)
    if platform and not platform.page_fetch:
        name = platform.name
        if Mode.UNAVAILABLE_HERE in platform.modes:
            return PolicyDecision(False, Code.URL_PLATFORM_UNAVAILABLE, platform.evidence, name)
        if platform.connector_switch:
            state = "enabled" if connector_enabled(platform, switches) else "disabled"
            return PolicyDecision(False, Code.URL_PLATFORM_CONNECTOR_ONLY,
                                  f"{name}: page fetch not permitted; official connector {state} ({platform.evidence})",
                                  name)
        return PolicyDecision(False, Code.URL_PLATFORM_DISCOVERY_ONLY, f"{name}: {platform.evidence}", name)
    return PolicyDecision(True, platform=platform.name if platform else None)


def check(raw: str, switches: PlatformSwitches) -> tuple[CanonicalURL | None, PolicyDecision]:
    try:
        canonical = canonicalize(raw)
    except InvalidURL:
        canonical = None
    return canonical, static_policy(raw, canonical, switches)
