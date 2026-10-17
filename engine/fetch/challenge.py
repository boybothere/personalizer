"""Detect bot-protection / captcha interstitials. These are ACCESS CONTROLS: they are reported as
ACCESS_CHALLENGE, never retried, never solved, and never eligible for the provider-text fallback.

Markers are deliberately specific (vendor challenge scripts, headers, interstitial titles) and the
text-based ones only apply to short pages, so an article that merely mentions "captcha" is not
misclassified.
"""

from __future__ import annotations

import re

_SCRIPT_MARKERS = re.compile(
    r"/cdn-cgi/challenge-platform/|cf-browser-verification|cf_chl_opt|__cf_chl_|"  # Cloudflare
    r"captcha-delivery\.com|geo\.captcha-delivery|"  # DataDome
    r"/_Incapsula_Resource|incapsula incident id|"  # Imperva
    r"px-captcha|_pxhd|perimeterx|"  # PerimeterX / HUMAN
    r"/akam/1\d/|ak_bmsc|"  # Akamai bot manager
    r"hcaptcha\.com/1/api\.js|google\.com/recaptcha/api\.js\?.*render=explicit",
    re.I)
_TITLE_MARKERS = re.compile(
    r"just a moment\.\.\.|attention required!? \| cloudflare|checking your browser|"
    r"verify you are (a )?human|are you a robot|security check|access denied|request unsuccessful|"
    r"pardon our interruption|ddos protection by|one more step",
    re.I)
SHORT_PAGE_CHARS = 1500


def detect(*, headers: dict | None = None, html: str = "", title: str | None = None,
           text_chars: int | None = None) -> str | None:
    """Return a reason string if the response is a bot/captcha interstitial, else None."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    if h.get("cf-mitigated", "").lower() == "challenge":
        return "cf-mitigated: challenge header"
    m = _SCRIPT_MARKERS.search(html[:200_000])
    if m:
        return f"bot-protection marker: {m.group(0)[:40]}"
    if title and _TITLE_MARKERS.search(title) and (text_chars is None or text_chars < SHORT_PAGE_CHARS):
        return f"interstitial title: {title[:60]}"
    return None
