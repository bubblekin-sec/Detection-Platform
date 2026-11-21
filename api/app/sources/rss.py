"""Feed parsing and HTML-to-text. Pure functions: no I/O, no database."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from time import struct_time
from urllib.parse import urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup, CData, NavigableString, Tag
from soupsieve import SelectorSyntaxError

# Query parameters that only identify a campaign or click, never the document (so `ref`, `source` are kept).
TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|msclkid|mc_cid|mc_eid|_hsenc|_hsmi)$", re.IGNORECASE)
NON_CONTENT_TAGS = frozenset(
    {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg", "template"}
)
BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "main", "li", "ul", "ol", "dl", "dt", "dd", "pre", "blockquote",
    "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "hr", "br",
})  # fmt: skip


class FeedParseError(Exception):
    pass


@dataclass(frozen=True)
class FeedEntry:
    url: str
    title: str
    author: str | None
    published_at: datetime | None
    content_html: str


def parse_feed(body: bytes) -> list[FeedEntry]:
    parsed = feedparser.parse(body)
    if not parsed.entries and (parsed.bozo or not parsed.get("version")):
        raise FeedParseError("Response is not a valid RSS or Atom feed.")
    entries = []
    for item in parsed.entries:
        link = item.get("link")
        if not link:
            continue
        entries.append(
            FeedEntry(
                url=link,
                title=(item.get("title") or "").strip(),
                author=item.get("author") or None,
                published_at=_to_datetime(item.get("published_parsed") or item.get("updated_parsed")),
                content_html=_richest_content(item),
            )
        )
    return entries


def _richest_content(item) -> str:
    candidates = [content.get("value", "") for content in item.get("content", [])]
    candidates.append(item.get("summary", ""))
    return max(candidates, key=len)


def _to_datetime(value: struct_time | None) -> datetime | None:
    return datetime(*value[:6], tzinfo=UTC) if value else None


def html_to_text(html: str, body_selectors: list[str] | None = None, *, min_chars: int = 0) -> str:
    """Readable text from HTML.

    With selectors (a source's `extract.body_selectors`), use the first selector whose text has at least
    `min_chars`; if none does, the longest. Early generic selectors like `main` often match only a teaser.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = [_text(nodes) for nodes in _matches(soup, body_selectors)]
    for candidate in candidates:
        if len(candidate) >= min_chars:
            return candidate
    if candidates:
        return max(candidates, key=len)
    return _text([soup.find("article") or soup.find("main") or soup.body or soup])


def _text(nodes: list) -> str:
    """Newlines only around block elements; inline tags (code, em, a) stay inside their sentence.

    One iterative walk that never modifies the tree: bs4 tree edits rescan siblings (quadratic on large
    flat pages), and recursion could overflow on deeply nested markup.
    """
    out: list[str] = []
    stack: list[tuple[object, bool]] = [(node, False) for node in reversed(nodes)]
    while stack:
        item, closing = stack.pop()
        if closing:
            out.append("\n")
        elif isinstance(item, NavigableString):
            if type(item) in (NavigableString, CData):  # skip comments, doctypes, processing instructions
                out.append(str(item))
        elif isinstance(item, Tag) and item.name not in NON_CONTENT_TAGS:
            if item.name in BLOCK_TAGS:
                out.append("\n")
                stack.append((item, True))
            stack.extend((child, False) for child in reversed(item.contents))
    lines = (re.sub(r"[^\S\n]+", " ", line).strip() for line in "".join(out).split("\n"))
    return "\n".join(line for line in lines if line)


def _matches(soup: BeautifulSoup, selectors: list[str] | None) -> list[list]:
    matches = []
    for selector in selectors or []:
        # Huntable selectors may use scrapy's ::text / ::attr() pseudo-elements, which CSS doesn't have.
        css = re.sub(r"::(text|attr\([^)]*\))$", "", selector).strip()
        if not css:
            continue
        try:
            nodes = soup.select(css)
        except SelectorSyntaxError:
            continue
        if nodes:
            matches.append(nodes)
    return matches


def canonical_url(url: str) -> str:
    """Drop the fragment and click-tracking parameters so one article reached by two links is stored once.

    Only the scheme and host are lowercased; userinfo, path and the remaining query keep their exact text.
    Raises ValueError for URLs that cannot be parsed.
    """
    parts = urlsplit(url.strip())
    _ = parts.port  # raises ValueError for an invalid port
    userinfo, at, hostport = parts.netloc.rpartition("@")
    netloc = f"{userinfo}{at}{hostport.lower()}"
    query = "&".join(
        param for param in parts.query.split("&") if param and not TRACKING_PARAMS.match(param.split("=", 1)[0])
    )
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", query, ""))
