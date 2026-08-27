from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from revops_sync.config import Settings
from revops_sync.db import Base, build_engine, build_session_factory
from revops_sync.models import CanonicalAccount
from revops_sync.reconcile import ReconciliationService
from revops_sync.schemas import ReconcileRequest, SourceAccount

# S7: every other test in this suite runs against in-memory SQLite for speed
# and per-test isolation (see conftest.py). That leaves "transactional
# reconciliation in PostgreSQL" as a README claim with no executing evidence
# behind it — CI's Postgres service was only ever touched by Alembic. This
# test is the one place the claim is actually exercised, against the same
# Postgres 17 service CI already provisions. It uses an id namespaced to this
# test run so it cannot collide with another test or a real deployment, and
# it cleans up its own row (cascade deletes cover the child tables).
POSTGRES_URL = os.environ.get("POSTGRES_SMOKE_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="POSTGRES_SMOKE_URL not set — this test only runs where a real Postgres is reachable",
)


def test_reconciliation_commits_transactionally_on_real_postgres() -> None:
    assert POSTGRES_URL is not None  # narrows type for mypy; skipif already guards this
    settings = Settings(app_env="test", database_url=POSTGRES_URL, auto_create_schema=True)
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)

    marker = uuid.uuid4().hex[:12]
    domain = f"pg-smoke-{marker}.example"
    record = SourceAccount(
        provider="hubspot",
        external_id=f"pg-smoke-hs-{marker}",
        name="Postgres Smoke Test Co",
        domain=domain,
        source_updated_at=datetime(2026, 8, 27, tzinfo=UTC),
    )

    with factory() as session:
        try:
            result = ReconciliationService(session, settings).run(
                ReconcileRequest(source_mode="inline", records=[record])
            )
            assert result.inserted_records == 1
            assert result.accounts_reconciled == 1
            assert result.external_writes == 0

            account = session.scalar(
                select(CanonicalAccount).where(CanonicalAccount.domain == domain)
            )
            assert account is not None
            assert account.name == "Postgres Smoke Test Co"
            assert account.resolution_basis == "exact_normalized_domain"
        finally:
            # A Core-level bulk DELETE, not session.delete(): the ORM
            # relationships here don't set passive_deletes=True, so
            # session.delete() loads every child row into memory and tries
            # to NULL their (NOT NULL) foreign key before deleting the
            # parent, instead of trusting the DB's own ON DELETE CASCADE —
            # and fails on Postgres precisely because Postgres enforces that
            # NOT NULL constraint where SQLite's default settings do not.
            session.execute(delete(CanonicalAccount).where(CanonicalAccount.domain == domain))
            session.commit()
    engine.dispose()
