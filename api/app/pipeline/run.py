"""Pipeline runs: queueing, claiming and executing stages for one article under one profile version.

Runs live in Postgres. Workers claim the oldest queued run with SELECT ... FOR UPDATE SKIP LOCKED and commit
the claim immediately, so no transaction (or row lock) is held while a model call is in flight.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.llm.client import LLMClient, LLMError
from app.models import ACTIVE_RUN_CONSTRAINT, Article, Observable, PipelineRun, ProfileVersion, Source, StageResult
from app.pipeline.junk import junk_reason
from app.profiles.compile import compile_extraction, compile_relevance, render_article, unescape_markup
from app.profiles.schema import ProfileDefinition, RelevanceVerdict, extraction_schema

log = logging.getLogger(__name__)
ACTIVE_STATUSES = ("queued", "running")
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def find_active_run(session: Session, article_id: int, profile_version_id: int) -> PipelineRun | None:
    return session.scalars(
        select(PipelineRun).where(
            PipelineRun.article_id == article_id,
            PipelineRun.profile_version_id == profile_version_id,
            PipelineRun.status.in_(ACTIVE_STATUSES),
        )
    ).first()


def queue_run(session: Session, article_id: int, version: ProfileVersion) -> tuple[PipelineRun, bool]:
    """Queue a run, or return the one already queued/running for this article and profile version.

    A partial unique index allows only one active run per article and version, so concurrent requests
    cannot queue duplicates (and pay twice); the loser of the race gets the winner's run.
    """
    existing = find_active_run(session, article_id, version.id)
    if existing:
        return existing, False
    run = PipelineRun(article_id=article_id, profile_version_id=version.id, status="queued")
    session.add(run)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if ACTIVE_RUN_CONSTRAINT not in str(error.orig):
            raise
        return find_active_run(session, article_id, version.id), False
    return run, True


def fail_stalled_runs(session: Session, now: datetime, stale_after: timedelta) -> int:
    """Fail runs whose worker stopped reporting progress (killed, OOM, lost connection) so they can be re-queued."""
    stalled = session.execute(
        update(PipelineRun)
        .where(PipelineRun.status == "running", PipelineRun.heartbeat_at < now - stale_after)
        .values(status="failed", finished_at=now, error="The worker stopped before finishing. Run it again.")
    ).rowcount
    session.commit()
    return stalled


def claim_next_run(session: Session, now: datetime) -> int | None:
    run = session.scalars(
        select(PipelineRun)
        .where(PipelineRun.status == "queued")
        .order_by(PipelineRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if run is None:
        session.rollback()
        return None
    run.status, run.started_at, run.heartbeat_at, run.current_step = "running", now, now, "junk_filter"
    session.commit()
    return run.id


def process_next_run(
    session_factory: sessionmaker[Session], llm: LLMClient, settings: Settings, *, clock: Clock = _utcnow
) -> int | None:
    """Claim and execute one run. Returns its id, or None when the queue is empty."""
    with session_factory() as session:
        fail_stalled_runs(session, clock(), timedelta(seconds=settings.run_stale_after_seconds))
        run_id = claim_next_run(session, clock())
    if run_id is None:
        return None
    try:
        _execute(session_factory, llm, settings, run_id, clock)
    except Exception as error:  # never leave a run stuck in "running" because of a bug
        log.exception("Run %s crashed", run_id)
        _finish(session_factory, run_id, "failed", None, clock(), error=f"Internal error ({type(error).__name__}).")
    except BaseException:  # Ctrl+C or SIGTERM mid-run: record it, then let the worker stop
        _finish(session_factory, run_id, "failed", None, clock(), error="Interrupted: the worker was stopped.")
        raise
    return run_id


def _execute(session_factory: sessionmaker[Session], llm: LLMClient, settings: Settings, run_id: int, clock: Clock) -> None:
    with session_factory() as session:
        run = session.get(PipelineRun, run_id)
        article = session.get(Article, run.article_id)
        source = session.get(Source, article.source_id)
        definition = ProfileDefinition.model_validate(run.profile_version.definition)
        title, url, text = article.title, article.url, article.content_text
        source_name, source_config = source.name, dict(source.config or {})

    # Stage 1: deterministic junk filter (source settings may have changed since the article was fetched).
    started = clock()
    reason = junk_reason(title, text, source_config)
    _record(session_factory, run_id, StageResult(
        stage="junk_filter", status="filtered" if reason else "passed", output={"reason": reason},
        started_at=started, finished_at=clock(),
    ))
    if reason:
        _finish(session_factory, run_id, "filtered_out", "junk_filter", clock())
        return

    # Stage 2: relevance, driven by the profile's plain-language criteria.
    _set_step(session_factory, run_id, "relevance")
    system = compile_relevance(definition)
    article_input = render_article(title=title, source=source_name, url=url, text=text)
    started = clock()
    try:
        result = llm.complete_json(
            model=settings.relevance_model,
            system=system,
            user=article_input.text,
            schema=RelevanceVerdict,
            effort=settings.relevance_effort,
            max_tokens=16000,  # thinking counts against this; the verdict itself is small
        )
    except LLMError as error:
        _record(session_factory, run_id, StageResult(
            stage="relevance", status="failed", output={"error": str(error)}, compiled_prompt=system,
            model=settings.relevance_model, started_at=started, finished_at=clock(),
        ))
        _finish(session_factory, run_id, "failed", "relevance", clock(), error=str(error))
        return

    verdict = result.output
    score = max(0, min(10, verdict.score))
    passed = score >= definition.min_score
    output: dict[str, Any] = {
        "score": score, "min_score": definition.min_score, "rationale": verdict.rationale, "signals": verdict.signals,
    }
    if article_input.truncated:
        output["article_truncated"] = True
    _record(session_factory, run_id, StageResult(
        stage="relevance", status="passed" if passed else "filtered", output=output, compiled_prompt=system,
        model=result.model, input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
        cache_read_tokens=result.usage.cache_read_tokens, started_at=started, finished_at=clock(),
    ))
    if not passed:
        _finish(session_factory, run_id, "filtered_out", "relevance", clock())
        return

    # Stage 3: extraction of the profile's fields, each value backed by a quote that is checked against the text.
    _set_step(session_factory, run_id, "extraction")
    system = compile_extraction(definition)
    started = clock()
    try:
        result = llm.complete_json(
            model=settings.extraction_model,
            system=system,
            user=article_input.text,
            schema=extraction_schema(definition),
            effort=settings.extraction_effort,
            max_tokens=16000,
            fallbacks=True,
        )
    except LLMError as error:
        _record(session_factory, run_id, StageResult(
            stage="extraction", status="failed", output={"error": str(error)}, compiled_prompt=system,
            model=settings.extraction_model, started_at=started, finished_at=clock(),
        ))
        _finish(session_factory, run_id, "failed", "extraction", clock(), error=str(error))
        return

    observables = collect_observables(result.output, definition, text)
    counts = {field.name: sum(1 for item in observables if item.field_name == field.name) for field in definition.extract_fields}
    unverified = sum(1 for item in observables if not item.evidence_verified)
    _record(
        session_factory,
        run_id,
        StageResult(
            stage="extraction", status="passed",
            output={"counts": counts, "total": len(observables), "unverified": unverified}
            | ({"fallback_from": result.fallback_from} if result.fallback_from else {}),
            compiled_prompt=system, model=result.model, input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens, cache_read_tokens=result.usage.cache_read_tokens,
            started_at=started, finished_at=clock(),
        ),
        observables=observables,
    )
    # Rule generation (increment 6) continues from here.
    _finish(session_factory, run_id, "succeeded", "extraction", clock())


def collect_observables(extraction: BaseModel, definition: ProfileDefinition, article_text: str) -> list[Observable]:
    """Flatten the model's output into rows: duplicates merged, evidence checked against the article."""
    haystack = _normalize(article_text)
    rows: list[Observable] = []
    seen: set[tuple[str, str]] = set()
    for field in definition.extract_fields:
        for item in getattr(extraction, field.name):
            value, evidence = _clean(item.value), _clean(item.evidence)
            key = (field.name, _normalize(value))
            if not value or key in seen:
                continue
            seen.add(key)
            # Verified only if the quote is really in the article AND actually contains the value.
            verified = bool(evidence) and _normalize(value) in _normalize(evidence) and _normalize(evidence) in haystack
            rows.append(Observable(
                field_name=field.name,
                value=value,
                evidence=evidence,
                evidence_verified=verified,
                tags={tag: _clean(str(getattr(item, tag))) for tag in field.tags},
            ))
    return rows


def _clean(text: str) -> str:
    """Undo the "<" escaping the model saw, and drop NUL (PostgreSQL text and JSONB reject it)."""
    return unescape_markup(text).replace("\x00", "").strip()


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _record(
    session_factory: sessionmaker[Session], run_id: int, stage: StageResult, *, observables: list[Observable] | None = None
) -> None:
    with session_factory() as session:
        stage.run_id = run_id
        session.add(stage)
        for observable in observables or []:
            observable.run_id = run_id
            session.add(observable)
        session.execute(update(PipelineRun).where(PipelineRun.id == run_id).values(heartbeat_at=stage.finished_at))
        session.commit()


def _set_step(session_factory: sessionmaker[Session], run_id: int, step: str) -> None:
    with session_factory() as session:
        session.execute(
            update(PipelineRun).where(PipelineRun.id == run_id).values(current_step=step, heartbeat_at=_utcnow())
        )
        session.commit()


def _finish(
    session_factory: sessionmaker[Session], run_id: int, status: str, step: str | None, now: datetime, *, error: str | None = None
) -> None:
    values: dict[str, Any] = {"status": status, "finished_at": now, "error": error}
    if step is not None:
        values["current_step"] = step
    with session_factory() as session:
        session.execute(update(PipelineRun).where(PipelineRun.id == run_id).values(**values))
        session.commit()
