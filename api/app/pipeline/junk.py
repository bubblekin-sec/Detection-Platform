"""Deterministic junk filter. Driven by source config (Huntable's fields), independent of any profile."""

from typing import Any, Literal

DEFAULT_MIN_CONTENT_LENGTH = 500

JunkReason = Literal["title_keyword", "too_short"]


def junk_reason(title: str, text: str, config: dict[str, Any], *, title_only: bool = False) -> JunkReason | None:
    lowered = title.lower()
    if any(keyword.lower() in lowered for keyword in config.get("title_filter_keywords") or []):
        return "title_keyword"
    if title_only:
        return None
    if len(text) < config.get("min_content_length", DEFAULT_MIN_CONTENT_LENGTH):
        return "too_short"
    if len(text.split()) < config.get("min_word_count", 0):
        return "too_short"
    return None
