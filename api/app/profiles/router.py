from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.pipeline.scoring import score_articles
from app.profiles.seed import latest_version

router = APIRouter(prefix="/api/profiles", tags=["profiles"])
SessionDep = Annotated[Session, Depends(get_session)]


class RescoreResult(BaseModel):
    profile: str
    version: int
    scored: int


@router.post("/{slug}/rescore", response_model=RescoreResult)
def rescore(slug: str, session: SessionDep) -> RescoreResult:
    """Recompute priority scores for every article under the profile's latest version. Never queues model runs."""
    version = latest_version(session, slug)
    if version is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Profile '{slug}' not found."]})
    if not version.definition.get("priority"):
        raise HTTPException(status_code=422, detail={"errors": [f"Profile '{slug}' v{version.version} has no priority scoring."]})
    return RescoreResult(profile=slug, version=version.version, scored=score_articles(session, version))
