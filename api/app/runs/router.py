from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import get_session
from app.models import Article, DetectionRule, PipelineRun, ProfileVersion
from app.pipeline.priority import FoldedText
from app.pipeline.run import queue_run
from app.pipeline.workers import ORPHANED, cancellable, worker_status
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
    evidence_span: dict[str, int] | None = None  # {start, end} UTF-16 offsets into the article text (verified only)


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


class DuplicateRef(BaseModel):
    """The earlier rule this one nearly repeats (same log source; `similarity` is the Jaccard of their values)."""

    id: str
    title: str
    status: str
    article_id: int
    similarity: float


class RuleOut(BaseModel):
    id: str
    title: str
    level: str | None
    status: str
    group: dict[str, Any]
    observable_ids: list[int]
    sigma_yaml: str
    sigma_hash: str  # send back as expected_sigma_hash so approvals only apply to what the reviewer saw
    rationale: str | None
    queries: list[QueryOut]
    duplicate_of: DuplicateRef | None = None


def rule_out(rule: DetectionRule) -> RuleOut:
    original = rule.duplicate
    return RuleOut(
        id=rule.id, title=rule.title, level=rule.level, status=rule.status, group=rule.group_key,
        observable_ids=rule.observable_ids, sigma_yaml=rule.sigma_yaml, sigma_hash=rule.sigma_hash, rationale=rule.rationale,
        queries=[
            QueryOut.model_validate(query).model_copy(update={"stale": query.source_sigma_hash != rule.sigma_hash})
            for query in rule.queries
        ],
        duplicate_of=DuplicateRef(
            id=original.id, title=original.title, status=original.status, article_id=original.article_id,
            similarity=rule.duplicate_similarity or 0.0,
        ) if original is not None else None,
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


def _out(run: PipelineRun, article: FoldedText | None = None) -> RunOut:
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
        observables=[_observable(item, article) for item in run.observables],
        rules=[rule_out(rule) for rule in run.rules],
    )


def _observable(item, article: FoldedText | None) -> ObservableOut:
    out = ObservableOut.model_validate(item)
    if article is not None and item.evidence_verified:
        found = article.find(item.evidence)
        if found:
            out.evidence_span = {"start": found[0], "end": found[1]}
    return out


def _has_evidence(run: PipelineRun) -> bool:
    return any(item.evidence_verified for item in run.observables)


def _article_text(session: Session, article_id: int) -> FoldedText | None:
    """The article text indexed for evidence lookup: built once per request, however many observables there are."""
    text = session.scalar(select(Article.content_text).where(Article.id == article_id))
    return FoldedText(text, collapse_whitespace=True) if text else None


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
    loaded = _load(session, run.id)
    return _out(loaded, _article_text(session, article_id) if _has_evidence(loaded) else None)


CANCELLED = "Cancelled before it started."


@router.post("/api/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: int, session: SessionDep) -> RunOut:
    """Cancel a queued run, or a running one whose worker stopped (it ends `failed`, so the article can be queued
    again). A run a live worker is processing can't be cancelled: it is already spending, and the worker finishes it."""
    was_queued = session.scalar(select(PipelineRun.status).where(PipelineRun.id == run_id)) == "queued"
    cancelled = session.execute(
        update(PipelineRun)
        .where(PipelineRun.id == run_id, cancellable())
        .values(status="failed", error=CANCELLED if was_queued else ORPHANED, finished_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    run = _load(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Run {run_id} not found."]})
    if not cancelled:
        raise HTTPException(status_code=409, detail={"errors": [
            f"Run {run_id} is {run.status}; only a queued run, or one whose worker stopped, can be cancelled."
        ]})
    return _out(run, None)


class WorkerStatusOut(BaseModel):
    alive: bool
    last_seen_at: datetime | None
    queued: int
    running: int
    orphaned: int  # running runs whose worker stopped: cancel them and run again


@router.get("/api/worker", response_model=WorkerStatusOut)
def get_worker_status(session: SessionDep) -> WorkerStatusOut:
    """Whether a worker is processing the queue, so a waiting run can say why."""
    return WorkerStatusOut(**vars(worker_status(session)))


@router.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, session: SessionDep) -> RunOut:
    run = _load(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Run {run_id} not found."]})
    # Polled every few seconds while a run is active: only index the article when there's evidence to place.
    return _out(run, _article_text(session, run.article_id) if _has_evidence(run) else None)


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
    text = _article_text(session, article_id) if any(_has_evidence(run) for run in runs) else None
    return [_out(run, text) for run in runs]
