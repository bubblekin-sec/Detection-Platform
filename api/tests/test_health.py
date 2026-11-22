"""Liveness (/api/health, process-local) and readiness (/api/ready, needs the database)."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_ready_when_the_database_answers(db_client):
    response = db_client.get("/api/ready")

    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_not_ready_without_the_database_but_still_alive_and_no_credentials_leak():
    unreachable = Settings(_env_file=None, database_url="postgresql+psycopg://user:s3cret-pw@127.0.0.1:1/x",
                           database_connect_timeout_seconds=1)
    with TestClient(create_app(unreachable)) as client:
        ready = client.get("/api/ready")
        alive = client.get("/api/health")

    assert ready.status_code == 503
    assert ready.json() == {"detail": {"errors": ["database: unreachable"]}}
    assert "s3cret" not in ready.text and "127.0.0.1" not in ready.text
    assert alive.status_code == 200
