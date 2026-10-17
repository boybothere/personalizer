"""HTML -> main text + metadata. Deterministic: lxml sanitising, trafilatura extraction, own JSON-LD/meta parsing.

Separates three outcomes that the prototype conflated: the HTTP request succeeded (fetch layer),
text was extracted (this module), and that text is usable (length/JS checks here).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

import lxml.html
import trafilatura
from charset_normalizer import from_bytes
from lxml import etree

from ..config import ExtractSettings
from ..failures import Code
from ..fetch import challenge
from . import dates
from .textutil import has_invisible, normalize, sha256, simhash

_HIDDEN_STYLE = ("display:none", "visibility:hidden", "font-size:0", "opacity:0")
_HIDDEN_CLASSES = {"sr-only", "visually-hidden", "hidden", "d-none"}
_JS_HINT = re.compile(r"(enable|turn on) javascript|requires javascript|javascript (is )?(required|disabled)", re.I)
_SPA_ROOT = re.compile(rb'<div[^>]+id=["\'](root|app|__next|__nuxt|svelte)["\'][^>]*>\s*</div>', re.I)
META_DATE_KEYS = ("article:published_time", "og:article:published_time", "datepublished", "date", "dc.date",
                  "dc.date.issued", "dcterms.created", "pubdate", "publishdate", "publish-date", "parsely-pub-date",
                  "sailthru.date", "citation_publication_date", "citation_date")
META_MODIFIED_KEYS = ("article:modified_time", "og:updated_time", "datemodified", "last-modified")


@dataclass
class Extracted:
    usable: bool
    failure_code: str | None
    text: str = ""
    title: str | None = None
    author: str | None = None
    site_name: str | None = None
    language: str | None = None
    canonical_link: str | None = None
    published: dates.DateResolution | None = None
    modified_at: str | None = None
    flags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    extractor: str = f"trafilatura-{trafilatura.__version__}"

    def record(self) -> dict:
        p = self.published
        return {
            "usable": self.usable, "failure_code": self.failure_code, "title": self.title, "author": self.author,
            "site_name": self.site_name, "language": self.language, "canonical_link": self.canonical_link,
            "published_at": p.value if p else None, "published_source": p.source if p else None,
            "date_candidates": p.as_list() if p else [], "date_conflict": bool(p and p.conflict),
            "modified_at": self.modified_at, "text_chars": len(self.text),
            "text_sha256": sha256(self.text) if self.text else None,
            "simhash": simhash(self.text) if self.text else None, "flags": self.flags, "metadata": self.metadata,
            "extractor": self.extractor, "text": self.text or None,
        }


def decode(body: bytes, content_type: str | None) -> str | None:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        try:
            return body.decode(m.group(1))
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        best = from_bytes(body).best()
        return str(best) if best else None


def _is_hidden(el) -> bool:
    attrs = el.attrib
    style = attrs.get("style", "").replace(" ", "").lower()
    return ("hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true"
            or any(h in style for h in _HIDDEN_STYLE)
            or bool(_HIDDEN_CLASSES & set(attrs.get("class", "").split())))


def _jsonld_objects(tree) -> list[dict]:
    out = []
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            data = json.loads(script.text or "")
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                out.append(item)
                if isinstance(item.get("@graph"), list):
                    stack.extend(item["@graph"])
            elif isinstance(item, list):
                stack.extend(item)
    return out


def _names(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [value["name"]] if isinstance(value.get("name"), str) else []
    if isinstance(value, list):
        return [n for v in value for n in _names(v)]
    return []


def _links(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def extract_html(body: bytes, url: str, content_type: str | None, cfg: ExtractSettings, today: date) -> Extracted:
    html = decode(body, content_type)
    if html is None:
        return Extracted(False, Code.DECODE_ERROR.value)
    flags: list[str] = []
    if has_invisible(html):
        flags.append("invisible_unicode_removed")
    try:
        tree = lxml.html.fromstring(html)
    except (etree.ParserError, ValueError):
        return Extracted(False, Code.EXTRACTION_EMPTY.value, flags=["unparseable_html"])

    # metadata from the untouched document
    meta: dict[str, str] = {}
    for m in tree.xpath("//meta[@content]"):
        key = (m.get("property") or m.get("name") or m.get("itemprop") or "").strip().lower()
        if key and key not in meta:
            meta[key] = m.get("content", "").strip()
    jsonld = _jsonld_objects(tree)
    title = (meta.get("og:title") or (tree.findtext(".//title") or "").strip()) or None
    canonical = next(iter(tree.xpath('//link[@rel="canonical"]/@href')), None)
    rel_me = sorted(set(tree.xpath('//a[contains(concat(" ", normalize-space(@rel), " "), " me ")]/@href'
                                   ' | //link[contains(concat(" ", normalize-space(@rel), " "), " me ")]/@href')))
    authors, same_as, raw_dates, modified = [], [], [], None
    for obj in jsonld:
        authors += _names(obj.get("author"))
        same_as += _links(obj.get("sameAs"))
        if obj.get("datePublished"):
            raw_dates.append(("jsonld:datePublished", str(obj["datePublished"])))
        modified = modified or obj.get("dateModified")
    for key in META_DATE_KEYS:
        if meta.get(key):
            raw_dates.append((f"meta:{key}", meta[key]))
    modified = modified or next((meta[k] for k in META_MODIFIED_KEYS if meta.get(k)), None)
    for t in tree.xpath("//article//time[@datetime] | //time[@pubdate][@datetime]")[:1]:
        raw_dates.append(("time_element", t.get("datetime")))
    if (u := dates.url_date(url)):
        raw_dates.append(("url_pattern", u))
    author = (authors[0] if authors else None) or meta.get("author") or meta.get("article:author") or None

    # sanitise: hidden elements and comments are removed before text extraction
    for c in tree.xpath("//comment()"):
        if c.getparent() is not None:
            c.getparent().remove(c)
    hidden = [e for e in tree.iter() if isinstance(e.tag, str) and _is_hidden(e)]
    hidden_set = set(hidden)
    hidden_chars = 0
    for el in hidden:
        if el.getparent() is None or any(a in hidden_set for a in el.iterancestors()):
            continue  # root, or already removed with a hidden ancestor
        hidden_chars += len(el.text_content().strip())
        el.getparent().remove(el)
    if hidden_chars:
        flags.append(f"hidden_text_removed:{hidden_chars}")

    doc = trafilatura.bare_extraction(tree, url=url, with_metadata=True, favor_precision=True,
                                      include_comments=False, include_tables=True,
                                      date_extraction_params={"original_date": True, "extensive_search": False})
    data = doc.as_dict() if doc is not None and hasattr(doc, "as_dict") else (doc or {})
    text = normalize(data.get("text") or "")[: cfg.max_text_chars]
    modified_iso = (d.isoformat() if (d := dates.parse_full_date(str(modified))) else None) if modified else None
    heuristic = data.get("date")
    if heuristic and not (heuristic == modified_iso and not raw_dates):
        raw_dates.append(("heuristic:htmldate", heuristic))
    elif heuristic:
        flags.append("heuristic_date_equals_modified_date_ignored")  # htmldate picked the modification date
    published = dates.resolve(raw_dates, today)
    if published.conflict:
        flags.append("date_conflict")
    if published.value and modified_iso and (
            date.fromisoformat(modified_iso) - date.fromisoformat(published.value)).days > 365:
        flags.append("living_document")  # e.g. wiki pages: creation date says little about current content

    result = Extracted(
        usable=False, failure_code=None, text=text, title=title or data.get("title"), author=author,
        site_name=meta.get("og:site_name") or data.get("sitename"), language=data.get("language") or tree.get("lang"),
        canonical_link=canonical, published=published, flags=flags,
        modified_at=modified_iso,
        metadata={"same_as": sorted(set(same_as)), "rel_me": rel_me,
                  "jsonld_types": sorted({str(o.get("@type")) for o in jsonld if o.get("@type")}),
                  "og_type": meta.get("og:type")},
    )
    reason = challenge.detect(html=html, title=title, text_chars=len(text))
    if reason:  # an access control, not a technical failure: never eligible for the provider fallback
        result.failure_code = Code.ACCESS_CHALLENGE.value
        result.flags.append(f"access_challenge:{reason[:60]}")
    elif not text:
        result.failure_code = (Code.JS_REQUIRED if _looks_js_only(body, html, cfg) else Code.EXTRACTION_EMPTY).value
    elif len(text) < cfg.min_usable_chars:
        result.failure_code = (Code.JS_REQUIRED if _looks_js_only(body, html, cfg) else Code.TEXT_TOO_SHORT).value
    else:
        result.usable = True
    return result


def _looks_js_only(body: bytes, html: str, cfg: ExtractSettings) -> bool:
    if _SPA_ROOT.search(body):
        return True
    noscript = " ".join(re.findall(r"<noscript[^>]*>(.*?)</noscript>", html, re.I | re.S))
    if _JS_HINT.search(noscript):
        return True
    return len(body) >= cfg.js_shell_min_html_bytes and html.lower().count("<script") >= 5


def extract_text(body: bytes, content_type: str | None, cfg: ExtractSettings) -> Extracted:
    text = decode(body, content_type)
    if text is None:
        return Extracted(False, Code.DECODE_ERROR.value, extractor="plain")
    text = normalize(text)[: cfg.max_text_chars]
    ok = len(text) >= cfg.min_usable_chars
    return Extracted(ok, None if ok else Code.TEXT_TOO_SHORT.value, text=text, extractor="plain",
                     published=dates.DateResolution(None, None, False, []))
