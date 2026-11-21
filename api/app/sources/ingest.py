"""Fetch a source's feed and store new articles. Used by the API's "Fetch now" and the CLI.

The work runs in phases so no database transaction (or row lock) is held during network I/O:
  1. read the source settings, then end the transaction
  2. fetch and parse the feed (network)
  3. one short read: which candidate URLs are already stored
  4. fetch article pages for teaser-only items (network)
  5. one short write: insert articles and update the source
"""

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app.models import Article, Source
from app.pipeline.junk import DEFAULT_MIN_CONTENT_LENGTH, junk_reason
from app.sources.fetch import FetchError, Fetcher
from app.sources.rss import FeedEntry, FeedParseError, canonical_url, html_to_text, parse_feed

MAX_URL_LENGTH = 2048
DEFAULT_MAX_ITEMS_PER_FETCH = 50
# Article pages only need their text; the larger fetcher default exists for full-content feeds.
PAGE_MAX_BYTES = 5_000_000


class SourceFetchFailed(Exception):
    """The feed itself could not be read. Message is user-facing and is stored as source.last_error."""


@dataclass
class IngestResult:
    new: int = 0
    duplicates: int = 0
    too_short: int = 0
    filtered: int = 0  # title keywords, lookback window, unusable URL, beyond max_items_per_fetch, unstorable

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class _Candidate:
    entry: FeedEntry
    url: str


def ingest_source(session: Session, source: Source, fetcher: Fetcher, *, now: datetime | None = None) -> IngestResult:
    """Fetch the feed and insert new articles. On a feed-level failure, records last_error and raises.

    Ends any transaction already open on `session` before network I/O (unflushed changes are discarded).
    """
    now = now or datetime.now(UTC)
    source_id, rss_url, config = source.id, source.rss_url, dict(source.config or {})
    if not rss_url:
        raise ValueError("Source has no RSS feed URL.")
    session.rollback()

    try:
        entries = _read_feed(fetcher, rss_url)
    except SourceFetchFailed as error:
        _record_check(session, source_id, now, error=str(error))
        raise

    result = IngestResult()
    candidates = _candidates(entries, config, now, result)
    known = set(session.scalars(select(Article.url).where(Article.url.in_([c.url for c in candidates]))))
    session.rollback()

    rows = []
    for candidate in candidates:
        if candidate.url in known:
            result.duplicates += 1  # known URLs never trigger a page fetch
            continue
        text = _article_text(fetcher, candidate.entry, config)
        if junk_reason(candidate.entry.title, text, config):
            result.too_short += 1
            continue
        rows.append(_row(source_id, candidate, text, now))

    for row in rows:
        try:
            with session.begin_nested():  # one bad row cannot poison the batch
                inserted = session.scalar(insert(Article).values(**row).on_conflict_do_nothing().returning(Article.id))
        except DataError:
            result.filtered += 1
            continue
        if inserted is None:
            result.duplicates += 1  # same content under another URL, or a concurrent fetch got there first
        else:
            result.new += 1
    _record_check(session, source_id, now, error=None)
    return result


def _read_feed(fetcher: Fetcher, url: str) -> list[FeedEntry]:
    try:
        response = fetcher.fetch(url)
    except FetchError as error:
        raise SourceFetchFailed(str(error)) from error
    if response.status != 200:
        raise SourceFetchFailed(f"Feed returned HTTP {response.status}.")
    try:
        return parse_feed(response.body)
    except FeedParseError as error:
        raise SourceFetchFailed(str(error)) from error


def _candidates(entries: list[FeedEntry], config: dict[str, Any], now: datetime, result: IngestResult) -> list[_Candidate]:
    # Some feeds return their whole archive. Bound the work (and page requests to the site) per fetch.
    limit = config.get("max_items_per_fetch", DEFAULT_MAX_ITEMS_PER_FETCH)
    newest_first = sorted(entries, key=lambda entry: entry.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    result.filtered += max(0, len(newest_first) - limit)
    candidates: dict[str, _Candidate] = {}
    for entry in newest_first[:limit]:
        try:
            url = canonical_url(entry.url)
        except ValueError:
            url = ""
        unusable = len(url) > MAX_URL_LENGTH or not url.startswith(("http://", "https://")) or url in candidates
        if unusable or _too_old(entry, config, now) or junk_reason(entry.title, "", config, title_only=True):
            result.filtered += 1
            continue
        candidates[url] = _Candidate(entry, url)
    return list(candidates.values())


def _article_text(fetcher: Fetcher, entry: FeedEntry, config: dict[str, Any]) -> str:
    text = html_to_text(entry.content_html)
    if junk_reason(entry.title, text, config) == "too_short" and not config.get("rss_only"):
        # Feed gave a teaser: read the article page itself, through the same guarded fetcher.
        selectors = (config.get("extract") or {}).get("body_selectors")
        min_chars = config.get("min_content_length", DEFAULT_MIN_CONTENT_LENGTH)
        text = _page_text(fetcher, entry.url, selectors, min_chars) or text
    return text


def _page_text(fetcher: Fetcher, url: str, selectors: list[str] | None, min_chars: int) -> str | None:
    try:
        page = fetcher.fetch(url, max_bytes=PAGE_MAX_BYTES)
    except FetchError:
        return None
    if page.status != 200 or "html" not in page.content_type:
        return None
    return html_to_text(page.body.decode("utf-8", errors="replace"), selectors, min_chars=min_chars)


def _row(source_id: int, candidate: _Candidate, text: str, now: datetime) -> dict[str, Any]:
    entry = candidate.entry
    text = _clean(text)
    published = entry.published_at
    return {
        "source_id": source_id,
        "url": candidate.url,
        "title": _clean(entry.title) or candidate.url,
        "author": _clean(entry.author) or None,
        # A future date would pin the item to the top of every newest-first list.
        "published_at": min(published, now) if published else None,
        "content_text": text,
        "content_hash": content_hash(text),
        "word_count": len(text.split()),
    }


def _clean(value: str | None) -> str:
    """PostgreSQL text cannot contain NUL; mis-decoded pages often do."""
    return (value or "").replace("\x00", "")


def _record_check(session: Session, source_id: int, now: datetime, *, error: str | None) -> None:
    session.execute(update(Source).where(Source.id == source_id).values(last_checked_at=now, last_error=error))
    session.commit()


def _too_old(entry: FeedEntry, config: dict[str, Any], now: datetime) -> bool:
    lookback = config.get("lookback_days")
    return bool(lookback and entry.published_at and entry.published_at < now - timedelta(days=lookback))


def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().lower().encode()).hexdigest()
