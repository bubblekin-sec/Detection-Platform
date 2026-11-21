"""Seed built-in profiles. Insert-only: an existing profile (and its versions) is never changed here."""

from importlib.resources import files
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Profile, ProfileVersion
from app.profiles.schema import ProfileDefinition

SEED_DIR = files("app.profiles") / "seed_data"


def seed_profiles(session: Session) -> dict[str, int]:
    added = existing = 0
    for path in sorted(SEED_DIR.iterdir(), key=lambda item: item.name):
        if not path.name.endswith(".yaml"):
            continue
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        if session.scalar(select(Profile.id).where(Profile.slug == data["slug"])) is not None:
            existing += 1
            continue
        definition = ProfileDefinition.model_validate(data["definition"])
        profile = Profile(slug=data["slug"], name=data["name"], description=data.get("description"))
        session.add(profile)
        session.flush()
        session.add(ProfileVersion(profile_id=profile.id, version=1, definition=definition.model_dump(mode="json")))
        added += 1
    session.commit()
    return {"added": added, "existing": existing}


def latest_version(session: Session, slug: str) -> ProfileVersion | None:
    return session.scalars(
        select(ProfileVersion)
        .join(Profile)
        .where(Profile.slug == slug)
        .order_by(ProfileVersion.version.desc())
        .limit(1)
    ).first()
