from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.dashboard import metrics
from app.db import get_session
from app.profiles.seed import latest_version

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("")
def dashboard(
    session: SessionDep, profile: str | None = None, days: Annotated[int, Query(ge=1, le=90)] = 10
) -> dict:
    """Defaults to the detection profile. Before any profile is seeded, the profile sections are empty and
    `profile` is null; naming a profile that doesn't exist is a 404."""
    version = latest_version(session, profile or "detection")
    if version is None and profile is not None:
        raise HTTPException(status_code=404, detail={"errors": [f"Profile '{profile}' not found."]})
    common = {
        "volume": metrics.volume(session, days, metrics.today_utc()),
        "failing_sources": metrics.failing_sources(session),
        "spend": metrics.spend(session),
    }
    if version is None:
        return {"profile": None, "funnel": metrics.empty_funnel(session), "bands": metrics.empty_bands(), "top": [],
                **common}
    return {
        "profile": {"slug": version.profile.slug, "version": version.version},
        "funnel": metrics.funnel(session, version),
        "bands": metrics.bands(session, version),
        "top": metrics.top_articles(session, version),
        **common,
    }
