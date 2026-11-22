"""Whether a worker is processing the queue: heartbeats written by `app.worker`, read by the API and other workers.

A worker registers at start, beats every BEAT_INTERVAL from a background thread (so a long model call doesn't make it
look gone), and removes its row on a clean stop. A worker is alive when its row was refreshed within SEEN_WINDOW.
Times come from the database clock, so the worker's and the API's clocks never need to agree. A running run whose
claiming worker is no longer alive is orphaned: the next worker fails it, and the reviewer can cancel it at once.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import PipelineRun, WorkerHeartbeat

BEAT_INTERVAL = timedelta(seconds=10)
SEEN_WINDOW = timedelta(seconds=30)
PRUNE_AFTER = timedelta(days=1)  # rows left by killed containers (Azure replica names change per revision)
ORPHANED = "The worker processing this run stopped before finishing. Run it again."


@dataclass
class WorkerStatus:
    alive: bool
    last_seen_at: datetime | None
    queued: int
    running: int
    orphaned: int  # running runs whose worker stopped


def register(session: Session, worker_id: str, provider: str) -> None:
    now = func.now()
    statement = insert(WorkerHeartbeat).values(worker_id=worker_id, provider=provider, started_at=now, last_seen_at=now)
    # host:pid can repeat (pid 1 in a restarted container): a new start replaces the old row entirely.
    session.execute(statement.on_conflict_do_update(
        index_elements=["worker_id"], set_={"provider": provider, "started_at": now, "last_seen_at": now},
    ))
    session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen_at < func.now() - PRUNE_AFTER))
    session.commit()


def beat(session: Session, worker_id: str, provider: str) -> None:
    now = func.now()
    statement = insert(WorkerHeartbeat).values(worker_id=worker_id, provider=provider, started_at=now, last_seen_at=now)
    session.execute(statement.on_conflict_do_update(index_elements=["worker_id"], set_={"last_seen_at": now}))
    session.commit()


def forget(session: Session, worker_id: str) -> None:
    session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id))
    session.commit()


def live_worker(worker_id_column):
    """SQL condition: the given worker id has a fresh heartbeat."""
    return exists().where(
        WorkerHeartbeat.worker_id == worker_id_column, WorkerHeartbeat.last_seen_at >= func.now() - SEEN_WINDOW
    )


def orphaned_runs():
    """SQL condition: a running run claimed by a worker that is no longer alive."""
    return and_(PipelineRun.status == "running", PipelineRun.claimed_by.is_not(None), ~live_worker(PipelineRun.claimed_by))


def fail_orphaned_runs(session: Session) -> int:
    failed = session.execute(
        update(PipelineRun).where(orphaned_runs())
        .values(status="failed", finished_at=func.now(), error=ORPHANED)
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    return failed


def worker_status(session: Session) -> WorkerStatus:
    last_seen = session.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
    alive = session.scalar(select(exists().where(WorkerHeartbeat.last_seen_at >= func.now() - SEEN_WINDOW)))
    counts = dict(session.execute(
        select(PipelineRun.status, func.count()).where(PipelineRun.status.in_(("queued", "running")))
        .group_by(PipelineRun.status)
    ).all())
    orphaned = session.scalar(select(func.count()).select_from(PipelineRun).where(orphaned_runs()))
    return WorkerStatus(alive=bool(alive), last_seen_at=last_seen, queued=counts.get("queued", 0),
                        running=counts.get("running", 0), orphaned=orphaned or 0)


def cancellable():
    """SQL condition: a run the reviewer may cancel (queued, or running with its worker gone)."""
    return or_(PipelineRun.status == "queued", orphaned_runs())
