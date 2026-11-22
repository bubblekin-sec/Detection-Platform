"""Where an article stands in a profile's pipeline: the one definition shared by the article list and the dashboard.

Precedence: a queued or running run shows as in progress; otherwise an article with rules from any run of the profile
shows as having rules (a later run that found none, failed or was filtered doesn't hide them, and the dashboard funnel
counts them too); otherwise the latest run's outcome.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Select, case, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.sql.elements import ColumnElement

from app.models import Article, DetectionRule, PipelineRun, ProfileVersion

PipelineStatus = Literal["not_run", "in_progress", "filtered", "failed", "no_rules", "rules"]


@dataclass(frozen=True)
class LatestRun:
    """Columns describing each article's pipeline state for a profile (any version); join with `join_to`."""

    _runs: object
    _rules: object
    run_id: ColumnElement  # the latest run
    rules: ColumnElement  # rules from every run of the profile for the article
    status: ColumnElement  # a PipelineStatus

    def join_to(self, query: Select) -> Select:
        return (
            query.outerjoin(self._runs, self._runs.c.article_id == Article.id)
            .outerjoin(self._rules, self._rules.c.article_id == Article.id)
        )


def latest_run(profile_id: int | None) -> LatestRun:
    runs = (
        select(PipelineRun.article_id, PipelineRun.id.label("run_id"), PipelineRun.status.label("run_status"))
        .join(ProfileVersion, ProfileVersion.id == PipelineRun.profile_version_id)
        .where(ProfileVersion.profile_id == profile_id)
        .order_by(PipelineRun.article_id, PipelineRun.id.desc())
        .ext(distinct_on(PipelineRun.article_id))
        .subquery()
    )
    counts = (
        select(DetectionRule.article_id, func.count().label("rules"))
        .join(PipelineRun, PipelineRun.id == DetectionRule.run_id)
        .join(ProfileVersion, ProfileVersion.id == PipelineRun.profile_version_id)
        .where(ProfileVersion.profile_id == profile_id)
        .group_by(DetectionRule.article_id)
        .subquery()
    )
    rules = func.coalesce(counts.c.rules, 0)
    status = case(
        (runs.c.run_id.is_(None), "not_run"),
        (runs.c.run_status.in_(("queued", "running")), "in_progress"),
        (rules > 0, "rules"),
        (runs.c.run_status == "filtered_out", "filtered"),
        (runs.c.run_status == "failed", "failed"),
        else_="no_rules",
    )
    return LatestRun(_runs=runs, _rules=counts, run_id=runs.c.run_id, rules=rules, status=status)
