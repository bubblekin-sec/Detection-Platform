"""Seed built-in profiles. Insert-only: stored profile versions are never changed here.

A seed file declares its `version`. A missing profile is created at that version; an existing profile whose latest
version is lower gets the seed as a new version (`upgraded`); otherwise nothing happens. Versions are immutable,
so past runs keep the definition they used.
"""

from importlib.resources import files
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Profile, ProfileVersion
from app.profiles.huntable_keywords import load_huntable_priority
from app.profiles.schema import ProfileDefinition

SEED_DIR = files("app.profiles") / "seed_data"
PRIORITY_SOURCES = {"huntable_keyword_registry": load_huntable_priority}


def seed_profiles(session: Session) -> dict[str, int]:
    added = upgraded = existing = 0
    for path in sorted(SEED_DIR.iterdir(), key=lambda item: item.name):
        if not path.name.endswith(".yaml"):
            continue
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        seed_version = int(data.get("version", 1))
        definition = _definition(data["definition"])
        profile = session.scalars(select(Profile).where(Profile.slug == data["slug"])).first()
        if profile is None:
            profile = Profile(slug=data["slug"], name=data["name"], description=data.get("description"))
            session.add(profile)
            session.flush()
            added += 1
        else:
            latest = latest_version(session, data["slug"])
            if latest is not None and latest.version >= seed_version:
                existing += 1
                continue
            upgraded += 1
        session.add(ProfileVersion(profile_id=profile.id, version=seed_version, definition=definition))
    session.commit()
    return {"added": added, "upgraded": upgraded, "existing": existing}


def _definition(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a seed definition, expanding `priority: {source: ...}` into concrete keyword lists so the stored
    version is self-contained (later changes to the vendored registry never alter it)."""
    raw = dict(raw)
    priority = raw.get("priority")
    if isinstance(priority, dict) and "source" in priority:
        loader = PRIORITY_SOURCES[priority["source"]]
        extra = {key: value for key, value in priority.items() if key not in {"source", "threshold"}}
        if extra:
            raise ValueError(f"Unsupported keys next to priority.source: {sorted(extra)}")
        raw["priority"] = loader(threshold=priority.get("threshold")).model_dump(mode="json")
    return ProfileDefinition.model_validate(raw).model_dump(mode="json")


def latest_version(session: Session, slug: str) -> ProfileVersion | None:
    return session.scalars(
        select(ProfileVersion)
        .join(Profile)
        .where(Profile.slug == slug)
        .order_by(ProfileVersion.version.desc())
        .limit(1)
    ).first()


def scoring_versions(session: Session) -> list[ProfileVersion]:
    """The latest version of every profile, when it defines priority scoring."""
    versions = []
    for slug in session.scalars(select(Profile.slug).order_by(Profile.slug)):
        version = latest_version(session, slug)
        if version is not None and version.definition.get("priority"):
            versions.append(version)
    return versions
