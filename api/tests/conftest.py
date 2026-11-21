from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SIGMA_FIXTURES = Path(__file__).parent / "fixtures" / "sigma"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def sigma_fixture():
    def load(name: str) -> str:
        return (SIGMA_FIXTURES / name).read_text(encoding="utf-8")

    return load
