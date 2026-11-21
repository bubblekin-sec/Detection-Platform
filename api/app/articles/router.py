from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, defer, joinedload

from app.db import get_session
from app.models import Article, ArticleScore, ProfileVersion, Source
from app.pipeline.priority import BANDS, SCORER_TAG, PriorityScorer
from app.pipeline.priority import band as band_of
from app.pipeline.status import PipelineStatus, latest_run
from app.profiles.schema import PriorityConfig
from app.profiles.seed import latest_version

router = APIRouter(prefix="/api/articles", tags=["articles"])

SessionDep = Annotated[Session, Depends(get_session)]
EXCERPT_LENGTH = 280
CHIP_BUCKETS = ("perfect", "lolbas", "intelligence")  # the strong signals shown as chips in the list
BandName = Literal["Excellent", "Good", "Fair", "Poor", "Very Poor"]


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


class BucketDetail(BaseModel):
    terms: list[str]
    count: int
    points: float
    max_points: float


class PriorityDetail(PriorityBrief):
    threshold: float | None
    profile_version: int
    stale: bool  # scored by an older scorer; `score-articles --missing` refreshes it
    buckets: dict[str, BucketDetail]
    spans: list[dict]  # {start, end, bucket, term}: UTF-16 offsets into content_text (title matches have none)
    spans_truncated: bool


class ArticleDetail(ArticleSummary):
    content_text: str
    priority: PriorityDetail | None = None  # the stored score with its full breakdown; the list carries the brief


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


def _chips(breakdown: dict) -> dict[str, list[str]]:
    return {name: (breakdown.get(name) or {}).get("terms", []) for name in CHIP_BUCKETS}


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
    order: Literal["desc", "asc"] | None = None,  # default: asc for source (A to Z), desc otherwise
    band: BandName | None = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    status: PipelineStatus | None = None,
) -> list[ArticleSummary]:
    # Defaults to the detection profile; an unknown profile is only an error when the caller names it.
    version = latest_version(session, profile or "detection")
    if version is None and profile is not None:
        raise HTTPException(status_code=404, detail={"errors": [f"Profile '{profile}' not found."]})
    version_id = version.id if version else None
    latest = latest_run(version.profile_id if version else None)

    # Full-content feeds can store very long text; the list only needs an excerpt, cut in the database.
    excerpt = func.substr(Article.content_text, 1, EXCERPT_LENGTH)
    query = latest.join_to(
        select(Article, excerpt, ArticleScore.score, ArticleScore.breakdown, latest.run_id, latest.rules, latest.status)
        .options(joinedload(Article.source), defer(Article.content_text))
        .outerjoin(ArticleScore, and_(ArticleScore.article_id == Article.id, ArticleScore.profile_version_id == version_id))
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
        query = query.where(latest.status == status)

    descending = (order or ("asc" if sort == "source" else "desc")) == "desc"
    if sort == "priority":
        key = ArticleScore.score.desc() if descending else ArticleScore.score.asc()
        query = query.order_by(key.nulls_last(), Article.id.desc())
    elif sort == "source":
        query = query.join(Source, Source.id == Article.source_id)
        name = Source.name.desc() if descending else Source.name.asc()
        query = query.order_by(name, Article.published_at.desc().nulls_last(), Article.id.desc())
    else:
        key = Article.published_at.desc() if descending else Article.published_at.asc()
        query = query.order_by(key.nulls_last(), Article.id.desc())

    results = []
    for article, text, score, breakdown, run_id, rule_count, state in session.execute(query.limit(limit).offset(offset)):
        priority = None if score is None else PriorityBrief(score=score, band=band_of(score), terms=_chips(breakdown))
        results.append(ArticleSummary(
            **_summary_fields(article, text),
            priority=priority,
            pipeline=PipelineBrief(status=state, run_id=run_id, rules=rule_count),
        ))
    return results


@router.get("/{article_id}", response_model=ArticleDetail)
def get_article(article_id: int, session: SessionDep, profile: str | None = None) -> ArticleDetail:
    article = session.get(Article, article_id, options=[joinedload(Article.source)])
    if article is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Article {article_id} not found."]})
    version = latest_version(session, profile or "detection")
    if version is None and profile is not None:
        raise HTTPException(status_code=404, detail={"errors": [f"Profile '{profile}' not found."]})
    pipeline = None
    if version is not None:
        latest = latest_run(version.profile_id)
        state = session.execute(
            latest.join_to(select(latest.status, latest.run_id, latest.rules).select_from(Article)).where(Article.id == article_id)
        ).one()
        pipeline = PipelineBrief(status=state[0], run_id=state[1], rules=state[2])
    return ArticleDetail(
        **_summary_fields(article, article.content_text[:EXCERPT_LENGTH]),
        content_text=article.content_text,
        priority=_priority_detail(session, article, version),
        pipeline=pipeline,
    )


def _priority_detail(session: Session, article: Article, version: ProfileVersion | None) -> PriorityDetail | None:
    """The stored score (what gating, the list and the dashboard use), plus highlight spans computed on demand.
    None until the article is scored for this version."""
    if version is None or not version.definition.get("priority"):
        return None
    stored = session.scalar(
        select(ArticleScore).where(ArticleScore.article_id == article.id, ArticleScore.profile_version_id == version.id)
    )
    if stored is None:
        return None
    config = PriorityConfig.model_validate(version.definition["priority"])
    buckets = {}
    for name, bucket in config.buckets.items():
        terms = (stored.breakdown.get(name) or {}).get("terms", [])
        points = (stored.breakdown.get(name) or {}).get("points", 0.0)
        buckets[name] = BucketDetail(terms=terms, count=len(terms), points=points, max_points=bucket.max_points)
    spans, truncated = PriorityScorer(config).spans(article.content_text)
    return PriorityDetail(
        score=stored.score, band=band_of(stored.score), terms=_chips(stored.breakdown), threshold=config.threshold,
        profile_version=version.version, stale=stored.scorer != SCORER_TAG, buckets=buckets, spans=spans,
        spans_truncated=truncated,
    )
