"""Source policy: which domains are blocked, and how reliable the rest are.

Tiers are a coarse, auditable prior. They are not a judgement of any
publisher's accuracy on a given story; corroboration does that work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Level, SourceTier
from .text import domain_matches

# Platforms whose terms prohibit automated collection, or that we deliberately
# do not use for profiling. Search snippets from them are discarded too.
RESTRICTED_PLATFORMS = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com",
    "threads.net", "snapchat.com", "pinterest.com", "quora.com",
}

# People-search engines, contact-data brokers, leak/paste sites, dating sites,
# court-record aggregators: inappropriate for meeting prep regardless of access.
DATA_BROKERS = {
    "spokeo.com", "whitepages.com", "beenverified.com", "truepeoplesearch.com", "fastpeoplesearch.com",
    "radaris.com", "intelius.com", "peoplefinders.com", "mylife.com", "zabasearch.com", "pipl.com",
    "rocketreach.co", "zoominfo.com", "apollo.io", "lusha.com", "contactout.com", "signalhire.com",
    "truecaller.com", "peekyou.com", "thatsthem.com", "instantcheckmate.com", "truthfinder.com",
    "pastebin.com", "ghostbin.com", "justpaste.it", "indiankanoon.org", "judyrecords.com",
    "tinder.com", "bumble.com", "shaadi.com", "jeevansathi.com", "bharatmatrimony.com",
}

REPUTABLE = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com", "nytimes.com", "economist.com",
    "theguardian.com", "bbc.co.uk", "bbc.com", "cnbc.com", "hbr.org", "techcrunch.com", "theverge.com",
    "wired.com", "arstechnica.com", "venturebeat.com", "theinformation.com", "axios.com", "fortune.com",
    "economictimes.indiatimes.com", "livemint.com", "thehindu.com", "thehindubusinessline.com",
    "business-standard.com", "moneycontrol.com", "financialexpress.com", "yourstory.com", "inc42.com",
    "the-ken.com", "entrackr.com", "nasscom.in", "zdnet.com", "infoworld.com", "datacenterdynamics.com",
}

PROFESSIONAL = {
    "wikipedia.org", "arxiv.org", "openalex.org", "orcid.org", "github.com", "crunchbase.com",
    "ieee.org", "acm.org", "usenix.org", "neurips.cc", "nvidia.com", "aws.amazon.com", "youtube.com",
    "substack.com", "medium.com", "podcasts.apple.com", "spotify.com", "sched.com", "sessionize.com",
}

CONFERENCE_HINTS = ("summit", "conf", "conference", "expo", "forum", "symposium", "devday", "sessionize", "sched")


@dataclass
class SourcePolicy:
    org_domain: str | None = None
    personal_domains: set[str] = field(default_factory=set)
    extra_reputable: set[str] = field(default_factory=set)
    extra_blocked: set[str] = field(default_factory=set)

    def block_reason(self, domain: str) -> str | None:
        if domain_matches(domain, RESTRICTED_PLATFORMS):
            return "platform terms restrict automated collection; not used"
        if domain_matches(domain, DATA_BROKERS | self.extra_blocked):
            return "people-search, data-broker, leak or records site; inappropriate source"
        return None

    def tier(self, domain: str, url: str = "") -> SourceTier:
        if self.block_reason(domain):
            return SourceTier.BLOCKED
        if (self.org_domain and domain_matches(domain, {self.org_domain})) or domain_matches(
            domain, self.personal_domains
        ):
            return SourceTier.OFFICIAL
        if domain_matches(domain, REPUTABLE | self.extra_reputable):
            return SourceTier.REPUTABLE
        if domain_matches(domain, PROFESSIONAL) or any(h in domain for h in CONFERENCE_HINTS):
            return SourceTier.PROFESSIONAL
        if any(h in url.lower() for h in ("/speakers", "/speaker/", "/agenda")):
            return SourceTier.PROFESSIONAL
        return SourceTier.GENERAL


TIER_RELIABILITY = {
    SourceTier.OFFICIAL: (Level.HIGH, 1.0),
    SourceTier.REPUTABLE: (Level.HIGH, 0.9),
    SourceTier.PROFESSIONAL: (Level.MEDIUM, 0.7),
    SourceTier.GENERAL: (Level.LOW, 0.4),
    SourceTier.BLOCKED: (Level.LOW, 0.0),
}
