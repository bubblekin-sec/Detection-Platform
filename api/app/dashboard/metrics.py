"""Dashboard metrics. Each section is a separate function so it can be tested and reused on its own."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.orm import Session

from app.llm.pricing import PRICES, Price, cost
from app.models import Article, ArticleScore, DetectionRule, PipelineRun, ProfileVersion, Source, StageResult
from app.pipeline.priority import BANDS
from app.pipeline.priority import band as band_of
from app.pipeline.status import latest_run


def _profile_runs(version: ProfileVersion):
    return (
        select(PipelineRun.id, PipelineRun.article_id, PipelineRun.status)
        .join(ProfileVersion, ProfileVersion.id == PipelineRun.profile_version_id)
        .where(ProfileVersion.profile_id == version.profile_id)
        .subquery()
    )


def funnel(session: Session, version: ProfileVersion) -> dict:
    """Articles that reached each stage (an article counts once, whatever number of runs it had)."""
    threshold = (version.definition.get("priority") or {}).get("threshold")
    runs = _profile_runs(version)
    fetched = session.scalar(select(func.count()).select_from(Article))
    above = 0
    if threshold is not None:
        above = session.scalar(
            select(func.count()).select_from(ArticleScore).where(
                ArticleScore.profile_version_id == version.id, ArticleScore.score >= threshold
            )
        )
    run = session.scalar(select(func.count(distinct(runs.c.article_id))))
    relevant = session.scalar(
        select(func.count(distinct(runs.c.article_id)))
        .join(StageResult, StageResult.run_id == runs.c.id)
        .where(StageResult.stage == "relevance", StageResult.status == "passed")
    )
    with_rules = session.scalar(
        select(func.count(distinct(DetectionRule.article_id))).join(runs, runs.c.id == DetectionRule.run_id)
    )
    approved = session.scalar(
        select(func.count(distinct(DetectionRule.article_id)))
        .join(runs, runs.c.id == DetectionRule.run_id)
        .where(DetectionRule.status == "approved")
    )
    return {"fetched": fetched, "above_threshold": above, "run": run, "relevant": relevant,
            "with_rules": with_rules, "approved": approved, "threshold": threshold}


def empty_funnel(session: Session) -> dict:
    """The funnel before any profile exists: only fetching has happened."""
    fetched = session.scalar(select(func.count()).select_from(Article))
    return {"fetched": fetched, "above_threshold": 0, "run": 0, "relevant": 0, "with_rules": 0, "approved": 0,
            "threshold": None}


def empty_bands() -> dict[str, int]:
    return {label: 0 for _, label in BANDS}


def volume(session: Session, days: int, today: date) -> list[dict]:
    start = today - timedelta(days=days - 1)
    day = func.date(func.timezone("UTC", Article.fetched_at))
    counts = dict(session.execute(
        select(day, func.count()).where(day >= start).group_by(day)
    ).all())
    return [{"date": (start + timedelta(days=n)).isoformat(), "articles": counts.get(start + timedelta(days=n), 0)}
            for n in range(days)]


def bands(session: Session, version: ProfileVersion) -> dict[str, int]:
    floors = [floor for floor, _ in BANDS]
    labels = [label for _, label in BANDS]
    bucket = case(*[(ArticleScore.score >= floor, label) for floor, label in zip(floors, labels)], else_=labels[-1])
    counts = dict(session.execute(
        select(bucket, func.count()).where(ArticleScore.profile_version_id == version.id).group_by(bucket)
    ).all())
    return {label: counts.get(label, 0) for label in labels}


def top_articles(session: Session, version: ProfileVersion, limit: int = 10) -> list[dict]:
    latest = latest_run(version.profile_id)
    rows = session.execute(
        latest.join_to(
            select(Article.id, Article.title, Source.name, ArticleScore.score, latest.status)
            .join(ArticleScore, and_(ArticleScore.article_id == Article.id, ArticleScore.profile_version_id == version.id))
            .join(Source, Source.id == Article.source_id)
        )
        .order_by(ArticleScore.score.desc(), Article.id.desc())
        .limit(limit)
    ).all()
    return [{"id": row.id, "title": row.title, "source": row.name, "score": row.score, "band": band_of(row.score),
             "pipeline": row[4]} for row in rows]


def failing_sources(session: Session) -> list[dict]:
    rows = session.scalars(select(Source).where(Source.last_error.is_not(None)).order_by(Source.name))
    return [{"id": s.id, "name": s.name, "last_error": s.last_error,
             "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None} for s in rows]


def spend(session: Session, prices: dict[str, Price] = PRICES) -> dict:
    """All-time model spend across every profile (stages served only by an unpriced model are left out of by_stage)."""
    rows = session.execute(
        select(StageResult.model, StageResult.stage, func.coalesce(func.sum(StageResult.input_tokens), 0),
               func.coalesce(func.sum(StageResult.output_tokens), 0), func.coalesce(func.sum(StageResult.cache_read_tokens), 0))
        .where(StageResult.model.is_not(None), StageResult.input_tokens.is_not(None))
        .group_by(StageResult.model, StageResult.stage)
    ).all()
    by_model: dict[str, dict] = {}
    by_stage: dict[str, float] = {}
    for model, stage, input_tokens, output_tokens, cache_tokens in rows:
        entry = by_model.setdefault(model, {"model": model, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0})
        entry["input_tokens"] += input_tokens
        entry["output_tokens"] += output_tokens
        entry["cache_read_tokens"] += cache_tokens
        price = prices.get(model)
        if price is not None:
            amount = cost(price, input_tokens=input_tokens, output_tokens=output_tokens, cache_read_tokens=cache_tokens)
            by_stage[stage] = by_stage.get(stage, 0.0) + amount
    for entry in by_model.values():
        price = prices.get(entry["model"])
        entry["cost"] = None if price is None else cost(
            price, input_tokens=entry["input_tokens"], output_tokens=entry["output_tokens"],
            cache_read_tokens=entry["cache_read_tokens"],
        )
    rows_out = sorted(by_model.values(), key=lambda item: -(item["cost"] or 0))
    return {
        "total": sum(item["cost"] or 0 for item in rows_out),
        "by_model": rows_out,
        "by_stage": by_stage,
        "unpriced_models": sorted(item["model"] for item in rows_out if item["cost"] is None),
    }


def today_utc() -> date:
    return datetime.now(UTC).date()
