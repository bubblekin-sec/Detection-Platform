from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import get_session
from app.models import Article, DetectionRule, PipelineRun, ProfileVersion
from app.pipeline.run import queue_run
from app.profiles.seed import latest_version

router = APIRouter(tags=["runs"])
SessionDep = Annotated[Session, Depends(get_session)]


class RunCreate(BaseModel):
    profile: str = Field(default="detection", min_length=1, max_length=100)


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stage: str
    status: str
    output: dict[str, Any]
    compiled_prompt: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    started_at: datetime
    finished_at: datetime


class ObservableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    field_name: str
    value: str
    evidence: str
    evidence_verified: bool
    tags: dict[str, Any]


class QueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target: str
    origin: str
    query_text: str | None
    status: str
    error: str | None
    warnings: list[str]
    stale: bool = False


class RuleOut(BaseModel):
    id: str
    title: str
    level: str | None
    status: str
    group: dict[str, Any]
    observable_ids: list[int]
    sigma_yaml: str
    rationale: str | None
    queries: list[QueryOut]


def rule_out(rule: DetectionRule) -> RuleOut:
    return RuleOut(
        id=rule.id, title=rule.title, level=rule.level, status=rule.status, group=rule.group_key,
        observable_ids=rule.observable_ids, sigma_yaml=rule.sigma_yaml, rationale=rule.rationale,
        queries=[
            QueryOut.model_validate(query).model_copy(update={"stale": query.source_sigma_hash != rule.sigma_hash})
            for query in rule.queries
        ],
    )


class ProfileRef(BaseModel):
    slug: str
    version: int


class RunOut(BaseModel):
    id: int
    article_id: int
    profile: ProfileRef
    status: str
    current_step: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stages: list[StageOut]
    observables: list[ObservableOut]
    rules: list[RuleOut]


def _out(run: PipelineRun) -> RunOut:
    return RunOut(
        id=run.id,
        article_id=run.article_id,
        profile=ProfileRef(slug=run.profile_version.profile.slug, version=run.profile_version.version),
        status=run.status,
        current_step=run.current_step,
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        stages=[StageOut.model_validate(stage) for stage in run.stages],
        observables=[ObservableOut.model_validate(item) for item in run.observables],
        rules=[rule_out(rule) for rule in run.rules],
    )


def _load(session: Session, run_id: int) -> PipelineRun | None:
    return session.scalars(
        select(PipelineRun)
        .where(PipelineRun.id == run_id)
        .options(selectinload(PipelineRun.stages), selectinload(PipelineRun.observables),
            selectinload(PipelineRun.rules).selectinload(DetectionRule.queries),
            joinedload(PipelineRun.profile_version).joinedload(ProfileVersion.profile))
        .execution_options(populate_existing=True)
    ).first()


@router.post("/api/articles/{article_id}/runs", response_model=RunOut, status_code=202)
def create_run(article_id: int, body: RunCreate, session: SessionDep, response: Response) -> RunOut:
    """Queue the article for the profile's pipeline. Returns the existing run (200) if one is already active."""
    if session.get(Article, article_id) is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Article {article_id} not found."]})
    version = latest_version(session, body.profile)
    if version is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Profile '{body.profile}' not found."]})
    run, created = queue_run(session, article_id, version)
    if not created:
        response.status_code = 200
    return _out(_load(session, run.id))


@router.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, session: SessionDep) -> RunOut:
    run = _load(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Run {run_id} not found."]})
    return _out(run)


@router.get("/api/articles/{article_id}/runs", response_model=list[RunOut])
def list_article_runs(article_id: int, session: SessionDep) -> list[RunOut]:
    runs = session.scalars(
        select(PipelineRun)
        .where(PipelineRun.article_id == article_id)
        .options(selectinload(PipelineRun.stages), selectinload(PipelineRun.observables),
            selectinload(PipelineRun.rules).selectinload(DetectionRule.queries),
            joinedload(PipelineRun.profile_version).joinedload(ProfileVersion.profile))
        .order_by(PipelineRun.id.desc())
        .limit(20)
    ).all()
    return [_out(run) for run in runs]
