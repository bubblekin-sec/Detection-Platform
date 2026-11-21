"""Store priority scores: one row per (article, profile version), upserted so rescoring is idempotent."""

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Article, ArticleScore, ProfileVersion
from app.pipeline.priority import SCORER_TAG, PriorityScorer
from app.profiles.schema import PriorityConfig

BATCH = 200


def score_articles(
    session: Session,
    version: ProfileVersion,
    *,
    article_ids: Iterable[int] | None = None,
    missing_only: bool = False,
) -> int:
    """Score articles for `version` (all, the given ids, or those without a score yet). Commits; returns the count."""
    config = PriorityConfig.model_validate(version.definition["priority"])
    scorer = PriorityScorer(config)
    query = select(Article.id, Article.title, Article.content_text).order_by(Article.id)
    if article_ids is not None:
        ids = list(article_ids)
        if not ids:
            return 0
        query = query.where(Article.id.in_(ids))
    if missing_only:
        scored = select(ArticleScore.article_id).where(ArticleScore.profile_version_id == version.id)
        query = query.where(Article.id.not_in(scored))

    count = 0
    # Stream the reads too: full-content articles can be large, so never hold every row in memory at once.
    for rows in session.execute(query.execution_options(yield_per=BATCH)).partitions(BATCH):
        values = []
        for row in rows:
            result = scorer.score(row.title, row.content_text)
            values.append({
                "article_id": row.id, "profile_version_id": version.id, "score": result.score,
                "breakdown": result.buckets, "scorer": SCORER_TAG,
            })
        statement = insert(ArticleScore).values(values)
        session.execute(statement.on_conflict_do_update(
            constraint="uq_article_scores_article_version",
            set_={"score": statement.excluded.score, "breakdown": statement.excluded.breakdown,
                  "scorer": statement.excluded.scorer, "scored_at": func.now()},
        ))
        count += len(values)
    session.commit()
    return count
