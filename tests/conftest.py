from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from revops_sync.config import Settings
from revops_sync.db import Base, build_engine, build_session_factory


@pytest.fixture
def settings() -> Settings:
    # In-memory SQLite by default — fast, and a fresh isolated database per
    # test (see the `factory` fixture below). The application's Postgres
    # transactional behavior specifically is exercised separately, against a
    # real Postgres instance, in tests/test_postgres_smoke.py — this fixture
    # is deliberately not pointed at Postgres, because that database persists
    # across test functions within a CI run and these tests are not written
    # to share state safely.
    fixture = Path(__file__).parents[1] / "data/synthetic/crm_accounts.json"
    return Settings(
        app_env="test",
        database_url="sqlite://",
        auto_create_schema=True,
        fixture_path=str(fixture),
    )


@pytest.fixture
def factory(settings: Settings) -> Generator[sessionmaker[Session], None, None]:
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    yield build_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with factory() as active:
        yield active
