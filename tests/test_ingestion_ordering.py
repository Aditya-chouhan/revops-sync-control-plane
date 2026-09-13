from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from itertools import permutations
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from revops_sync.config import Settings
from revops_sync.main import create_app
from revops_sync.models import CanonicalAccount, OutboxItem, SourceRecord, SyncRun
from revops_sync.reconcile import ReconciliationService, SourceVersionConflict
from revops_sync.schemas import ReconcileRequest, SourceAccount


def snapshot(version: int, provider: str = "hubspot", **changes: Any) -> SourceAccount:
    return SourceAccount.model_validate(
        {
            "provider": provider,
            "external_id": "test-123",
            "name": f"Account v{version}",
            "domain": "ordering.example",
            "employee_count": version * 10,
            "marketing_opt_in": version == 1,
            "source_updated_at": datetime(2026, 9, 10, tzinfo=UTC) + timedelta(hours=version),
            **changes,
        }
    )


def ingest(session: Session, settings: Settings, *records: SourceAccount):
    return ReconciliationService(session, settings).run(
        ReconcileRequest(source_mode="inline", records=list(records))
    )


def state(session: Session) -> tuple[Any, ...]:
    session.expire_all()
    accounts = tuple(
        (a.id, a.domain, a.name, a.employee_count, a.marketing_opt_in)
        for a in session.scalars(select(CanonicalAccount).order_by(CanonicalAccount.id))
    )
    sources = tuple(
        (r.id, r.canonical_account_id, r.checksum, r.payload)
        for r in session.scalars(select(SourceRecord).order_by(SourceRecord.id))
    )
    outbox = tuple(
        (o.id, o.sequence, o.payload_checksum, o.idempotency_key, o.payload)
        for o in session.scalars(select(OutboxItem).order_by(OutboxItem.id))
    )
    return accounts, sources, outbox


@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
def test_duplicate_snapshot_creates_no_duplicate_source_or_preview(
    session: Session, settings: Settings, provider: str
) -> None:
    original = snapshot(2, provider)
    ingest(session, settings, original)
    before = state(session)
    result = ingest(session, settings, original, original)
    assert result.unchanged_records == 2 and result.stale_records == 0
    assert result.inserted_records == result.updated_records == result.outbox_previews_created == 0
    assert state(session) == before


@pytest.mark.parametrize("order", list(permutations([1, 2, 3])))
def test_all_serial_arrival_orders_converge_on_newest_snapshot(
    session: Session, settings: Settings, order: tuple[int, ...]
) -> None:
    for version in order:
        ingest(session, settings, snapshot(version))
    source = session.scalar(select(SourceRecord))
    account = session.scalar(select(CanonicalAccount))
    assert source is not None and source.payload["name"] == "Account v3"
    assert account is not None and account.name == "Account v3"
    assert account.employee_count == 30 and account.marketing_opt_in is False
    for provider in ("hubspot", "salesforce"):
        latest = session.scalar(
            select(OutboxItem)
            .where(OutboxItem.target_provider == provider)
            .order_by(OutboxItem.sequence.desc())
        )
        assert latest is not None
        fields = latest.payload["properties"] if provider == "hubspot" else latest.payload
        assert fields["name" if provider == "hubspot" else "Name"] == "Account v3"


@pytest.mark.parametrize(
    "change",
    [
        {"domain": "stale-domain.example"},
        {"canonical_id_hint": "untrusted-old-hint"},
        {"marketing_opt_in": True},
    ],
)
def test_stale_snapshot_cannot_move_identity_or_restore_consent(
    session: Session, settings: Settings, change: dict[str, Any]
) -> None:
    ingest(session, settings, snapshot(3))
    before = state(session)
    result = ingest(session, settings, snapshot(1, **change))
    assert result.stale_records == 1 and result.accounts_reconciled == 0
    assert result.outbox_previews_created == result.updated_records == 0
    assert state(session) == before
    run = session.get(SyncRun, result.run_id)
    assert run is not None and run.receipt["stale_records"] == 1


@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
def test_equal_version_conflict_rolls_back_whole_batch_and_session_is_reusable(
    session: Session, settings: Settings, provider: str
) -> None:
    ingest(session, settings, snapshot(2, provider))
    before = state(session)
    with pytest.raises(SourceVersionConflict):
        ingest(
            session,
            settings,
            snapshot(3, provider, external_id="new-record", domain="new-account.example"),
            snapshot(2, provider, name="Conflicting account"),
        )
    assert state(session) == before
    assert ingest(session, settings, snapshot(3, provider)).updated_records == 1


def test_conflicting_same_version_within_first_batch_is_atomic(
    session: Session, settings: Settings
) -> None:
    with pytest.raises(SourceVersionConflict):
        ingest(session, settings, snapshot(1), snapshot(1, name="Conflicting first delivery"))
    assert state(session) == ((), (), ())
    assert list(session.scalars(select(SyncRun))) == []


def test_equivalent_timezone_offsets_are_duplicates(session: Session, settings: Settings) -> None:
    original = snapshot(2)
    ingest(session, settings, original)
    before = state(session)
    offset = original.source_updated_at.astimezone(timezone(timedelta(hours=5, minutes=30)))
    duplicate = SourceAccount.model_validate({**original.model_dump(), "source_updated_at": offset})
    assert ingest(session, settings, duplicate).unchanged_records == 1
    assert state(session) == before


def test_legacy_offset_payload_normalizes_before_duplicate_comparison(
    session: Session, settings: Settings
) -> None:
    original = snapshot(2)
    ingest(session, settings, original)
    source = session.scalar(select(SourceRecord))
    assert source is not None
    source.payload = {**source.payload, "source_updated_at": "2026-09-10T07:30:00+05:30"}
    session.commit()
    assert ingest(session, settings, original).unchanged_records == 1


@pytest.mark.parametrize("value", [datetime(2026, 9, 10), "2026-09-10T02:00:00"])
def test_source_version_requires_timezone(value: Any) -> None:
    with pytest.raises(ValidationError, match="explicit timezone"):
        snapshot(2, source_updated_at=value)


def test_api_exposes_stale_receipt_and_returns_conflict_without_mutation(
    settings: Settings,
) -> None:
    def body(record: SourceAccount) -> dict[str, Any]:
        return {"source_mode": "inline", "records": [record.model_dump(mode="json")]}

    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/reconcile", json=body(snapshot(3))).status_code == 200
        before = client.get("/v1/outbox").json()
        stale = client.post("/v1/reconcile", json=body(snapshot(1)))
        assert stale.status_code == 200 and stale.json()["stale_records"] == 1
        conflict = client.post("/v1/reconcile", json=body(snapshot(3, name="Conflicting")))
        assert conflict.status_code == 409
        assert client.get("/v1/outbox").json() == before
        assert client.get("/v1/accounts").json()[0]["name"] == "Account v3"


def test_duplicate_and_stale_records_in_one_batch_have_separate_counts(
    session: Session, settings: Settings
) -> None:
    result = ingest(session, settings, snapshot(3), snapshot(1), snapshot(3), snapshot(2))
    assert result.inserted_records == 1 and result.stale_records == 2
    assert result.unchanged_records == 1 and result.updated_records == 0
    assert result.input_records == 4
