import time

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.exc import OperationalError

from app import models  # noqa: F401
from app.db import Base
from tests.conftest import API_ROOT


def test_models_match_migrations(migrated_engine):
    with migrated_engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert diff == []


def test_migrating_an_unreachable_database_fails_fast():
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["database_url"] = "postgresql+psycopg://u:p@127.0.0.1:5999/nope"
    started = time.monotonic()

    with pytest.raises(OperationalError):
        command.upgrade(config, "head")

    assert time.monotonic() - started < 15
