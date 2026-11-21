import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import make_engine
from app.main import create_app

SIGMA_FIXTURES = Path(__file__).parent / "fixtures" / "sigma"
API_ROOT = Path(__file__).parent.parent
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://detection:detection@127.0.0.1:5432/detection_test"


@pytest.fixture
def sigma_fixture():
    def load(name: str) -> str:
        return (SIGMA_FIXTURES / name).read_text(encoding="utf-8")

    return load


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(database_url=os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))


@pytest.fixture(scope="session")
def migrated_engine(test_settings):
    """Migrate the test database once per session. Fails loudly rather than skipping when Postgres is down."""
    from alembic import command
    from alembic.config import Config

    engine = make_engine(test_settings)
    try:
        with engine.connect():
            pass
    except OperationalError as error:
        pytest.exit(
            "Test database unreachable. Start it with `docker compose up -d --wait db` from the repo root, "
            "or set the TEST_DATABASE_URL environment variable. If Postgres is up but `detection_test` is missing "
            "(volume created before the init script), recreate it with `docker compose down -v`. "
            f"({error.__class__.__name__})",
            returncode=2,
        )
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["database_url"] = test_settings.database_url
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine):
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE rule_queries, detection_rules, observables, stage_results, pipeline_runs, article_scores, profile_versions, profiles, articles, sources RESTART IDENTITY CASCADE"))
    with sessionmaker(migrated_engine)() as session:
        yield session


@pytest.fixture
def client(test_settings):
    """App client for endpoints that don't touch the DB."""
    with TestClient(create_app(test_settings)) as test_client:  # context manager runs lifespan, disposing the engine
        yield test_client


@pytest.fixture
def db_client(test_settings, db_session: Session):
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client
