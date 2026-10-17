"""Polite, defensive page retrieval and HTML-to-text conversion.

Retrieved content is untrusted. This module:
* only fetches public http(s) URLs on public IP addresses (SSRF guard, re-checked per redirect)
* honours robots.txt for our user agent
* caps size, time and content type; sends no cookies or credentials
* strips scripts, hidden elements and comments (common prompt-injection carriers)
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Comment

from .text import INVISIBLE_RE, normalize

MAX_TEXT_CHARS = 30_000
_STRIP_TAGS = ["script", "style", "noscript", "template", "iframe", "svg", "canvas", "form", "nav",
               "footer", "aside", "button", "input", "select", "object", "embed", "head"]
_HIDDEN_STYLE = ("display:none", "visibility:hidden", "font-size:0", "opacity:0", "height:0", "width:0")


@dataclass
class FetchResult:
    url: str
    status: str  # "ok" | reason for failure
    text: str = ""
    title: str = ""
    flags: list[str] = field(default_factory=list)


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


def is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def _is_hidden(tag) -> bool:
    attrs = tag.attrs or {}
    style = str(attrs.get("style", "")).replace(" ", "").lower()
    classes = attrs.get("class", []) or []
    return (
        "hidden" in attrs
        or str(attrs.get("aria-hidden", "")).lower() == "true"
        or any(h in style for h in _HIDDEN_STYLE)
        or any(c in {"sr-only", "visually-hidden", "hidden", "d-none"} for c in classes)
    )


def html_to_text(html: str) -> tuple[str, str, list[str]]:
    """Return (title, visible_text, flags)."""
    flags: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    title = normalize(soup.title.get_text()) if soup.title else ""
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    hidden = [tag for tag in soup.find_all(True) if _is_hidden(tag)]
    hidden_chars = 0
    for tag in hidden:
        if tag.decomposed:  # already removed with a hidden ancestor
            continue
        hidden_chars += len(tag.get_text(strip=True))
        tag.decompose()
    if hidden_chars:
        flags.append(f"hidden_text_removed:{hidden_chars}")
    for br in soup.find_all(["br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr", "blockquote"]):
        br.append("\n")
    raw = soup.get_text(" ")
    if INVISIBLE_RE.search(raw):
        flags.append("invisible_unicode_removed")
    lines = [normalize(line) for line in raw.splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) > MAX_TEXT_CHARS:
        flags.append("truncated")
        text = text[:MAX_TEXT_CHARS]
    return title, text, flags


class HttpFetcher:
    def __init__(self, user_agent: str, timeout: float = 10.0, max_bytes: int = 2_000_000):
        self.ua = user_agent
        self.max_bytes = max_bytes
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": user_agent, "Accept": "text/html,text/plain;q=0.9"},
        )
        self._robots: dict[str, RobotFileParser | None] = {}

    def _robots_allows(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                r = self.client.get(base + "/robots.txt")
                if r.status_code >= 500:
                    rp = None  # server error: treat as disallow, per RFC 9309
                elif r.status_code >= 400:
                    rp.parse([])  # no robots.txt: everything allowed
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return rp is not None and rp.can_fetch(self.ua, url)

    def fetch(self, url: str) -> FetchResult:
        for _ in range(4):  # initial request + up to 3 redirects
            p = urlparse(url)
            if p.scheme not in {"http", "https"} or not p.hostname:
                return FetchResult(url, "rejected: unsupported URL")
            if p.username or p.password:
                return FetchResult(url, "rejected: credentials in URL")
            if not is_public_host(p.hostname):
                return FetchResult(url, "rejected: non-public address")
            if not self._robots_allows(url):
                return FetchResult(url, "skipped: disallowed by robots.txt")
            try:
                with self.client.stream("GET", url) as r:
                    if r.is_redirect:
                        url = urljoin(url, r.headers.get("location", ""))
                        continue
                    if r.status_code in (401, 402, 403, 407, 451):
                        return FetchResult(url, f"skipped: access controlled ({r.status_code})")
                    if r.status_code >= 400:
                        return FetchResult(url, f"unavailable ({r.status_code})")
                    ctype = r.headers.get("content-type", "").lower()
                    if not ctype.startswith(("text/html", "text/plain", "application/xhtml")):
                        return FetchResult(url, f"skipped: unsupported content type {ctype or 'unknown'}")
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            break
                    html = body.decode(r.encoding or "utf-8", errors="replace")
            except httpx.HTTPError as exc:
                return FetchResult(url, f"unavailable ({type(exc).__name__})")
            title, text, flags = html_to_text(html) if "html" in ctype else ("", normalize(html), [])
            return FetchResult(url, "ok", text, title, flags)
        return FetchResult(url, "unavailable (too many redirects)")


class FixtureFetcher:
    """Serves pages from the offline evaluation corpus."""

    def __init__(self, pages: dict[str, dict]):
        self.pages = pages

    def fetch(self, url: str) -> FetchResult:
        page = self.pages.get(url)
        if page is None:
            return FetchResult(url, "unavailable (404)")
        if page.get("robots_disallow"):
            return FetchResult(url, "skipped: disallowed by robots.txt")
        if page.get("status"):
            return FetchResult(url, page["status"])
        title, text, flags = html_to_text(page["html"])
        return FetchResult(url, "ok", text, title or page.get("title", ""), flags)
