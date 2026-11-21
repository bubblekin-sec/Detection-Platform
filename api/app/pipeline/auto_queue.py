"""The priority gate: articles at or above a profile's threshold are queued for the model pipeline automatically.

Guarantees:
- An article below the threshold is never queued here, so no model ever sees it unless someone asks.
- An article with any run (of any status) for that profile version is never queued again, so re-fetching, rescoring
  or a retry storm can't multiply spend.
- At most AUTO_QUEUE_MAX_PER_DAY automatic runs per UTC day across all profiles; the highest scores win and the rest
  are reported as `deferred`. A transaction-scoped advisory lock keeps concurrent fetches from overshooting the cap.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ArticleScore, PipelineRun
from app.profiles.seed import scoring_versions

ADVISORY_LOCK_KEY = 7_311_204  # arbitrary, stable: serialises auto-queue decisions


@dataclass
class AutoQueueResult:
    queued: int = 0
    deferred: int = 0


def auto_queue(session: Session, article_ids: list[int], settings: Settings, *, now: datetime | None = None) -> AutoQueueResult:
    result = AutoQueueResult()
    if not article_ids:
        return result
    now = now or datetime.now(UTC)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY})
    today = datetime.combine(now.date(), time.min, tzinfo=UTC)
    used = session.scalar(
        select(func.count()).select_from(PipelineRun).where(PipelineRun.trigger == "auto", PipelineRun.created_at >= today)
    )
    remaining = max(0, settings.auto_queue_max_per_day - used)

    eligible: list[tuple[float, int, int]] = []  # (score, article_id, profile_version_id)
    for version in scoring_versions(session):
        threshold = version.definition["priority"].get("threshold")
        if threshold is None:
            continue
        has_run = select(PipelineRun.id).where(
            PipelineRun.article_id == ArticleScore.article_id, PipelineRun.profile_version_id == version.id
        )
        rows = session.execute(
            select(ArticleScore.score, ArticleScore.article_id).where(
                ArticleScore.profile_version_id == version.id,
                ArticleScore.article_id.in_(article_ids),
                ArticleScore.score >= threshold,
                ~has_run.exists(),
            )
        ).all()
        eligible += [(row.score, row.article_id, version.id) for row in rows]

    eligible.sort(key=lambda item: (-item[0], item[1]))
    chosen = eligible[:remaining]
    for score, article_id, version_id in chosen:
        # A reviewer may queue the same article manually in this instant: skip it rather than fail the whole batch.
        inserted = session.scalar(
            insert(PipelineRun)
            .values(article_id=article_id, profile_version_id=version_id, status="queued", trigger="auto")
            .on_conflict_do_nothing(
                index_elements=["article_id", "profile_version_id"], index_where=PipelineRun.status.in_(("queued", "running"))
            )
            .returning(PipelineRun.id)
        )
        result.queued += inserted is not None
    result.deferred = len(eligible) - len(chosen)
    session.commit()  # also releases the advisory lock
    return result
