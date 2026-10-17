"""robots.txt handling per RFC 9309.

* 2xx: parse the rules.
* 4xx (incl. 404): no rules apply; everything is allowed.
* 5xx or network failure: treat as complete disallow (RFC 9309 s2.3.1.4).
Redirects are followed (up to 5 hops). Results are cached per origin for the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from ..config import BOT_TOKEN
from ..failures import Code

MAX_ROBOTS_BYTES = 500_000  # RFC 9309 requires parsing at least 500 KiB


@dataclass
class RobotsResult:
    origin: str
    result: str  # parsed | allow_all | disallow_all
    http_status: int | None
    failure_code: Code | None
    detail: str | None
    parser: RobotFileParser | None = None
    crawl_delay: float | None = None

    def allows(self, url: str) -> bool:
        if self.result == "allow_all":
            return True
        if self.result == "disallow_all" or self.parser is None:
            return False
        return self.parser.can_fetch(BOT_TOKEN, url)


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def build(origin: str, status: int | None, body: str | None, error: str | None) -> RobotsResult:
    if error is not None or status is None:
        return RobotsResult(origin, "disallow_all", status, Code.ROBOTS_UNREACHABLE, error or "no response")
    if status >= 500:
        return RobotsResult(origin, "disallow_all", status, Code.ROBOTS_UNREACHABLE, f"robots.txt HTTP {status}")
    if status >= 400:
        return RobotsResult(origin, "allow_all", status, None, f"robots.txt HTTP {status}: no restrictions")
    parser = RobotFileParser()
    parser.parse((body or "").splitlines())
    delay = parser.crawl_delay(BOT_TOKEN)
    return RobotsResult(origin, "parsed", status, None, None, parser, float(delay) if delay else None)
