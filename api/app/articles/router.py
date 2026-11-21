from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, defer, joinedload

from app.db import get_session
from app.models import Article, ArticleScore, DetectionRule, PipelineRun, ProfileVersion, Source
from app.pipeline.priority import BANDS
from app.pipeline.priority import band as band_of
from app.profiles.seed import latest_version

router = APIRouter(prefix="/api/articles", tags=["articles"])

SessionDep = Annotated[Session, Depends(get_session)]
EXCERPT_LENGTH = 280
CHIP_BUCKETS = ("perfect", "lolbas", "intelligence")  # the strong signals shown as chips in the list
BandName = Literal["Excellent", "Good", "Fair", "Poor", "Very Poor"]
PipelineStatus = Literal["not_run", "in_progress", "filtered", "failed", "no_rules", "rules"]


class SourceRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str


class PriorityBrief(BaseModel):
    score: float
    band: str
    terms: dict[str, list[str]]


class PipelineBrief(BaseModel):
    status: PipelineStatus
    run_id: int | None
    rules: int


class ArticleSummary(BaseModel):
    id: int
    url: str
    title: str
    author: str | None
    published_at: datetime | None
    fetched_at: datetime
    word_count: int
    excerpt: str
    source: SourceRef
    priority: PriorityBrief | None = None
    pipeline: PipelineBrief | None = None


class ArticleDetail(ArticleSummary):
    content_text: str


def _summary_fields(article: Article, excerpt: str) -> dict:
    return {
        "id": article.id,
        "url": article.url,
        "title": article.title,
        "author": article.author,
        "published_at": article.published_at,
        "fetched_at": article.fetched_at,
        "word_count": article.word_count,
        "excerpt": excerpt,
        "source": SourceRef.model_validate(article.source),
    }


def _band_bounds(name: str) -> tuple[float, float | None]:
    floors = [floor for floor, _ in BANDS]
    index = [label for _, label in BANDS].index(name)
    return floors[index], (floors[index - 1] if index > 0 else None)


@router.get("", response_model=list[ArticleSummary])
def list_articles(
    session: SessionDep,
    source_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    profile: str | None = None,
    sort: Literal["published", "priority", "source"] = "published",
    order: Literal["desc", "asc"] = "desc",
    band: BandName | None = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    status: PipelineStatus | None = None,
) -> list[ArticleSummary]:
    # Defaults to the detection profile; an unknown profile is only an error when the caller names it.
    version = latest_version(session, profile or "detection")
    if version is None and profile is not None:
        raise HTTPException(status_code=404, detail={"errors": [f"Profile '{profile}' not found."]})
    version_id = version.id if version else None

    # Latest run of this profile (any version) per article, and how many rules that run produced.
    latest_run = (
        select(PipelineRun.article_id, PipelineRun.id.label("run_id"), PipelineRun.status.label("run_status"))
        .join(ProfileVersion, ProfileVersion.id == PipelineRun.profile_version_id)
        .where(ProfileVersion.profile_id == (version.profile_id if version else None))
        .order_by(PipelineRun.article_id, PipelineRun.id.desc())
        .distinct(PipelineRun.article_id)
        .subquery()
    )
    rule_counts = select(DetectionRule.run_id, func.count().label("rules")).group_by(DetectionRule.run_id).subquery()
    rules = func.coalesce(rule_counts.c.rules, 0)
    pipeline_status = case(
        (latest_run.c.run_id.is_(None), "not_run"),
        (latest_run.c.run_status.in_(("queued", "running")), "in_progress"),
        (latest_run.c.run_status == "filtered_out", "filtered"),
        (latest_run.c.run_status == "failed", "failed"),
        (rules > 0, "rules"),
        else_="no_rules",
    )

    # Full-content feeds can store very long text; the list only needs an excerpt, cut in the database.
    excerpt = func.substr(Article.content_text, 1, EXCERPT_LENGTH)
    query = (
        select(Article, excerpt, ArticleScore.score, ArticleScore.breakdown, latest_run.c.run_id, rules, pipeline_status)
        .options(joinedload(Article.source), defer(Article.content_text))
        .outerjoin(ArticleScore, and_(ArticleScore.article_id == Article.id, ArticleScore.profile_version_id == version_id))
        .outerjoin(latest_run, latest_run.c.article_id == Article.id)
        .outerjoin(rule_counts, rule_counts.c.run_id == latest_run.c.run_id)
    )
    if source_id is not None:
        query = query.where(Article.source_id == source_id)
    if band is not None:
        low, high = _band_bounds(band)
        query = query.where(ArticleScore.score >= low)
        if high is not None:
            query = query.where(ArticleScore.score < high)
    if min_score is not None:
        query = query.where(ArticleScore.score >= min_score)
    if status is not None:
        query = query.where(pipeline_status == status)

    descending = order == "desc"
    if sort == "priority":
        key = ArticleScore.score.desc() if descending else ArticleScore.score.asc()
        query = query.order_by(key.nulls_last(), Article.id.desc())
    elif sort == "source":
        query = query.join(Source, Source.id == Article.source_id)
        query = query.order_by(Source.name.asc() if descending else Source.name.desc(), Article.published_at.desc().nulls_last())
    else:
        key = Article.published_at.desc() if descending else Article.published_at.asc()
        query = query.order_by(key.nulls_last(), Article.id.desc())

    results = []
    for article, text, score, breakdown, run_id, rule_count, state in session.execute(query.limit(limit).offset(offset)):
        priority = None
        if score is not None:
            terms = {name: (breakdown.get(name) or {}).get("terms", []) for name in CHIP_BUCKETS}
            priority = PriorityBrief(score=score, band=band_of(score), terms=terms)
        results.append(ArticleSummary(
            **_summary_fields(article, text),
            priority=priority,
            pipeline=PipelineBrief(status=state, run_id=run_id, rules=rule_count),
        ))
    return results


@router.get("/{article_id}", response_model=ArticleDetail)
def get_article(article_id: int, session: SessionDep) -> ArticleDetail:
    article = session.get(Article, article_id, options=[joinedload(Article.source)])
    if article is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Article {article_id} not found."]})
    return ArticleDetail(**_summary_fields(article, article.content_text[:EXCERPT_LENGTH]), content_text=article.content_text)
