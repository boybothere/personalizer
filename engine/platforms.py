"""Platform access registry. Modes are evidence-based; see docs/engine/PLATFORMS.md for the probes.

Changing a mode requires updating `evidence` and `probed` and the PLATFORMS.md table.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import Platforms as PlatformSwitches


class Mode(str, Enum):
    CONTENT_API = "content_api"
    OEMBED_RECORD = "oembed_record"
    FEED = "feed"
    FETCH = "fetch"
    DISCOVERY_ONLY = "discovery_only"
    UNAVAILABLE_HERE = "unavailable_here"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class Platform:
    name: str
    domains: tuple[str, ...]
    page_fetch: bool  # may ordinary pages be requested (robots.txt still checked per URL)?
    modes: tuple[Mode, ...]
    evidence: str
    probed: str  # ISO date of the probe that justifies the mode
    connector_switch: str | None = None  # Platforms config flag that enables its connector


REGISTRY: tuple[Platform, ...] = (
    Platform("instagram", ("instagram.com",), False, (Mode.DISCOVERY_ONLY,),
             "robots * Disallow: /; oEmbed needs app token (400 without); Graph API needs business account + review",
             "2026-10-17"),
    Platform("facebook", ("facebook.com", "fb.com"), False, (Mode.DISCOVERY_ONLY,),
             "robots * Disallow: /; Page Public Content Access requires app review", "2026-10-17"),
    Platform("linkedin", ("linkedin.com",), False, (Mode.DISCOVERY_ONLY,),
             "robots * Disallow: /; no public API for other members", "2026-10-17"),
    Platform("x", ("x.com", "twitter.com"), False, (Mode.OEMBED_RECORD, Mode.CONTENT_API),
             "robots * Disallow: /; oEmbed 200 with post text (no key); API pay-per-use", "2026-10-17",
             connector_switch="x_oembed_enabled"),
    Platform("reddit", ("reddit.com", "redd.it"), False, (Mode.OEMBED_RECORD,),
             "robots * Disallow: /; oEmbed 200 (no key); Data API approval-gated", "2026-10-17",
             connector_switch="reddit_oembed_enabled"),
    Platform("tiktok", ("tiktok.com",), False, (Mode.UNAVAILABLE_HERE,),
             "oEmbed and pages redirect to /in/about from India (geo-blocked); untested elsewhere", "2026-10-17"),
    Platform("youtube", ("youtube.com", "youtu.be"), True, (Mode.FETCH, Mode.OEMBED_RECORD, Mode.CONTENT_API),
             "robots allows watch pages; oEmbed 200; Data API needs a key (not tested)", "2026-10-17"),
    Platform("bluesky", ("bsky.app",), True, (Mode.CONTENT_API, Mode.FETCH),
             "getProfile/getAuthorFeed/searchActors 200 unauthenticated; searchPosts 403", "2026-10-17"),
    Platform("medium", ("medium.com",), True, (Mode.FEED, Mode.FETCH),
             "robots allows posts; RSS 200; article pages returned 403 to our bot (5/5, live search run "
             "run_500282dbae8a442b) - use the RSS feed route", "2026-10-17"),
    Platform("substack", ("substack.com",), True, (Mode.FEED, Mode.FETCH), "robots allows posts; RSS 200", "2026-10-17"),
    Platform("github", ("github.com",), True, (Mode.CONTENT_API, Mode.FETCH), "REST /users 200 (60/h unauth)", "2026-10-17"),
    Platform("wikipedia", ("wikipedia.org", "wikidata.org"), True, (Mode.CONTENT_API, Mode.FETCH),
             "MediaWiki API 200 with contact User-Agent (403 without)", "2026-10-17"),
)

EXCLUDED_DOMAINS: frozenset[str] = frozenset({
    "spokeo.com", "whitepages.com", "beenverified.com", "truepeoplesearch.com", "fastpeoplesearch.com",
    "radaris.com", "intelius.com", "peoplefinders.com", "mylife.com", "zabasearch.com", "pipl.com",
    "rocketreach.co", "zoominfo.com", "apollo.io", "lusha.com", "contactout.com", "signalhire.com",
    "truecaller.com", "peekyou.com", "thatsthem.com", "instantcheckmate.com", "truthfinder.com",
    "pastebin.com", "ghostbin.com", "justpaste.it", "indiankanoon.org", "judyrecords.com",
    "tinder.com", "bumble.com", "shaadi.com", "jeevansathi.com", "bharatmatrimony.com",
})


def _matches(domain: str, candidates: tuple[str, ...] | frozenset[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in candidates)


def platform_for(domain: str) -> Platform | None:
    return next((p for p in REGISTRY if _matches(domain, p.domains)), None)


def is_excluded(domain: str) -> bool:
    return _matches(domain, EXCLUDED_DOMAINS)


def connector_enabled(platform: Platform, switches: PlatformSwitches) -> bool:
    return bool(platform.connector_switch and getattr(switches, platform.connector_switch, False))
