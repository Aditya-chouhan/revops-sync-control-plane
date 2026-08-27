from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from revops_sync.config import Settings
from revops_sync.db import Base, build_engine, build_session_factory


@pytest.fixture
def settings() -> Settings:
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
