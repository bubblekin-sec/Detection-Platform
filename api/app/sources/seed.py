"""Seed the source catalog from the vendored Huntable CTI Studio list (see THIRD_PARTY_NOTICES.md)."""

from importlib.resources import files
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Source

SEED_FILE = files("app.sources") / "seed_data" / "huntable_sources.yaml"
DEFAULT_CHECK_FREQUENCY_SECONDS = 4 * 3600


def load_seed_rows() -> list[dict[str, Any]]:
    catalog = yaml.safe_load(SEED_FILE.read_text(encoding="utf-8"))
    return [_to_row(entry) for entry in catalog["sources"]]


def _to_row(entry: dict[str, Any]) -> dict[str, Any]:
    config = dict(entry.get("config") or {})
    # Keep upstream fields that have no column yet, so scraping can be added later without re-vendoring.
    config["upstream_active"] = bool(entry.get("active"))
    if "lookback_days" in entry:
        config["lookback_days"] = entry["lookback_days"]
    return {
        "slug": entry["id"],
        "name": entry["name"],
        "url": entry["url"],
        "rss_url": entry.get("rss_url"),
        # The MVP only fetches RSS, so scrape-only sources start inactive even if upstream enables them.
        "active": bool(entry.get("active")) and bool(entry.get("rss_url")),
        "check_frequency_seconds": entry.get("check_frequency") or DEFAULT_CHECK_FREQUENCY_SECONDS,
        "description": entry.get("description"),
        "config": config,
        "origin": "huntable_seed",
    }


def seed_sources(session: Session) -> dict[str, Any]:
    """Insert seed sources whose slug is not present. Existing rows, edited or not, are never updated.

    `skipped_user_owned` lists seed slugs not added because a user-created source already uses that slug.
    """
    rows = load_seed_rows()
    inserted = set(
        session.scalars(
            insert(Source).values(rows).on_conflict_do_nothing(index_elements=[Source.slug]).returning(Source.slug)
        )
    )
    not_inserted = [row["slug"] for row in rows if row["slug"] not in inserted]
    user_owned = sorted(
        session.scalars(select(Source.slug).where(Source.slug.in_(not_inserted), Source.origin == "user"))
    )
    session.commit()
    return {
        "added": len(inserted),
        "existing": len(not_inserted) - len(user_owned),
        "skipped_user_owned": user_owned,
    }

