import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

os.environ.setdefault("PEACE_DATABASE_URL", "postgresql+psycopg://peace@127.0.0.1:55432/peace_test")
os.environ.setdefault("PEACE_API_TOKEN", "test-token-" + "x" * 32)
os.environ.setdefault("MOODLE_URL", "https://moodle.example.pt/moodle")
os.environ.setdefault("INFORESTUDANTE_URL", "https://inforestudante.example.pt/nonio")

from peace.api import app  # noqa: E402
from peace.config import database_url  # noqa: E402
from peace.db import get_engine  # noqa: E402
from peace.models import Base  # noqa: E402

ALEMBIC = Config(str(Path(__file__).parents[1] / "alembic.ini"))
TOKEN = os.environ["PEACE_API_TOKEN"]


@pytest.fixture(scope="session", autouse=True)
def schema():
    # The schema is dropped below; refuse anything that is not a throwaway database.
    assert (database_url().database or "").endswith("_test"), "tests must run against a *_test database"
    with get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(ALEMBIC, "head")


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session():
    with Session(get_engine()) as s:
        yield s


@pytest.fixture
def client():
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
