"""Telling "queued behind a worker" from "nothing will run this", and getting a stuck run out of the way."""

import threading
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.main import create_app
from app.models import Article, DetectionRule, PipelineRun, Source, WorkerHeartbeat
from app.pipeline.run import process_next_run
from app.pipeline.workers import ORPHANED, beat, forget, register
from app.profiles.seed import seed_profiles
from app.rules.duplicates import mark_duplicates
from tests.fakes import ScriptedLLM

SIGMA = """title: t
id: {id}
logsource:
  category: process_creation
  product: windows
detection:
  selection:
    CommandLine|contains: ['a', 'b', 'c']
  condition: selection
"""


@pytest.fixture
def api(test_settings, db_session):
    with TestClient(create_app(test_settings)) as client:
        yield client


@pytest.fixture
def article(db_session) -> Article:
    source = Source(slug="ex", name="Example", url="https://r.example.com/", rss_url="https://r.example.com/feed",
                    active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(source)
    db_session.flush()
    item = Article(source_id=source.id, url="https://r.example.com/a", title="A", content_text="x " * 400,
                   content_hash="h-worker", word_count=400)
    db_session.add(item)
    db_session.commit()
    seed_profiles(db_session)
    return item


def queue(api, article) -> dict:
    response = api.post(f"/api/articles/{article.id}/runs", json={"profile": "detection"})
    assert response.status_code == 202, response.text
    return response.json()


def start(db_session, run_id: int, worker_id: str | None) -> None:
    db_session.execute(update(PipelineRun).where(PipelineRun.id == run_id).values(
        status="running", started_at=datetime.now(UTC), heartbeat_at=datetime.now(UTC), claimed_by=worker_id,
    ))
    db_session.commit()


def age(db_session, worker_id: str, minutes: int = 5) -> None:
    db_session.execute(text(f"UPDATE worker_heartbeats SET last_seen_at = now() - interval '{minutes} minutes' WHERE worker_id = :w"),
                       {"w": worker_id})
    db_session.commit()


def test_with_no_worker_a_queued_run_is_reported_as_waiting_for_nobody(api, article):
    queue(api, article)

    assert api.get("/api/worker").json() == {"alive": False, "last_seen_at": None, "queued": 1, "running": 0, "orphaned": 0}


def test_a_recent_heartbeat_means_alive_and_an_old_one_does_not(api, db_session):
    register(db_session, "host:1", "anthropic")
    beat(db_session, "host:1", "anthropic")
    assert api.get("/api/worker").json()["alive"] is True

    age(db_session, "host:1")
    assert api.get("/api/worker").json()["alive"] is False

    forget(db_session, "host:1")
    assert api.get("/api/worker").json()["last_seen_at"] is None


def test_a_run_whose_worker_died_is_orphaned_not_busy_and_can_be_cancelled(api, article, db_session):
    register(db_session, "host:1", "anthropic")
    run = queue(api, article)
    start(db_session, run["id"], "host:1")
    age(db_session, "host:1")  # killed mid-run: its run still has a fresh heartbeat_at

    assert api.get("/api/worker").json() | {"last_seen_at": None} == {
        "alive": False, "last_seen_at": None, "queued": 0, "running": 1, "orphaned": 1,
    }
    response = api.post(f"/api/runs/{run['id']}/cancel")

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["error"]) == ("failed", ORPHANED)
    assert queue(api, article)["id"] != run["id"]


def test_the_next_worker_fails_an_orphaned_run_instead_of_waiting_30_minutes(api, article, db_session, migrated_engine):
    register(db_session, "dead:1", "anthropic")
    run = queue(api, article)
    start(db_session, run["id"], "dead:1")
    age(db_session, "dead:1")

    process_next_run(sessionmaker(migrated_engine, expire_on_commit=False), ScriptedLLM(), Settings(_env_file=None),
                     worker_id="live:2")

    assert (api.get(f"/api/runs/{run['id']}").json()["error"]) == ORPHANED


def test_a_queued_run_can_be_cancelled_and_a_live_workers_run_cannot(api, article, db_session):
    run = queue(api, article)
    cancelled = api.post(f"/api/runs/{run['id']}/cancel")
    assert (cancelled.status_code, cancelled.json()["error"]) == (200, "Cancelled before it started.")

    register(db_session, "host:1", "anthropic")
    second = queue(api, article)
    start(db_session, second["id"], "host:1")
    refused = api.post(f"/api/runs/{second['id']}/cancel")

    assert refused.status_code == 409
    assert "only a queued run, or one whose worker stopped" in refused.json()["detail"]["errors"][0]
    assert api.post("/api/runs/999999/cancel").status_code == 404


def test_the_worker_registers_beats_and_removes_its_row(monkeypatch, test_settings, migrated_engine, db_session):
    from app import worker

    seen = []
    monkeypatch.setenv("DATABASE_URL", test_settings.database_url)
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.chdir("tests")  # no api/.env here

    def record(session_factory, llm, settings, *, worker_id):
        with sessionmaker(migrated_engine)() as session:
            seen.extend(session.execute(select(WorkerHeartbeat.worker_id, WorkerHeartbeat.provider)).all())
        return None  # empty queue: --once stops

    monkeypatch.setattr(worker, "process_next_run", record)

    assert worker.main(["--once"]) == 0
    assert len(seen) == 1 and seen[0][1] == "offline" and seen[0][0].endswith(f":{__import__('os').getpid()}")
    with sessionmaker(migrated_engine)() as session:
        assert session.scalars(select(WorkerHeartbeat)).all() == []


def test_two_workers_saving_rules_at_once_take_turns_so_the_second_sees_the_first(article, db_session, migrated_engine):
    run = PipelineRun(article_id=article.id, profile_version_id=1, status="succeeded")
    db_session.add(run)
    db_session.commit()

    def new_rule() -> DetectionRule:
        rule_id = str(uuid.uuid4())
        sigma = SIGMA.format(id=rule_id)
        return DetectionRule(id=rule_id, run_id=run.id, article_id=article.id, position=0, group_key={}, observable_ids=[],
                             title="t", sigma_yaml=sigma, sigma_hash="h", status="pending_review")

    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    first, second = new_rule(), new_rule()
    with factory() as session_a:
        mark_duplicates(session_a, [first])  # takes the lock
        session_a.add(first)
        session_a.flush()

        def save_second():
            with factory() as session_b:
                mark_duplicates(session_b, [second])  # waits for session_a's commit
                session_b.add(second)
                session_b.commit()

        other = threading.Thread(target=save_second)
        other.start()
        other.join(timeout=1)
        assert other.is_alive()  # blocked on the lock
        session_a.commit()
    other.join(timeout=10)

    assert second.duplicate_of == first.id
