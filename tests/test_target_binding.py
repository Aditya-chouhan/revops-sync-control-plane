from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from test_gateways import seeded_item
from test_stream_ordering import next_item
from test_timeout_recovery import live_settings, remote_record

from alembic import command
from revops_sync.config import Settings
from revops_sync.db import build_engine, build_session_factory
from revops_sync.gateways import (
    DeliveryOutcomeUnknown,
    GuardedDeliveryClient,
    TargetBindingConflict,
    WorkerClaimLost,
)
from revops_sync.models import OutboxItem
from revops_sync.ordering import release_ordering_hold


@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
def test_queued_creates_become_updates_without_rewriting_intent(
    session: Session, settings: Settings, provider: str
) -> None:
    _, first = seeded_item(session, settings, provider)
    first.operation, first.target_external_id = "POST", None
    session.commit()
    second = next_item(session, first)
    third = next_item(session, second)
    intent = (second.operation, second.payload_checksum, second.idempotency_key)
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(
            201 if request.method == "POST" else 204, json={"id": "created-123"}, request=request
        )

    client = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    client.deliver(first)
    client.deliver(second)
    client.deliver(third)
    assert [method for method, _ in calls] == ["POST", "PATCH", "PATCH"]
    assert all(path.endswith("/created-123") for _, path in calls[1:])
    assert (second.operation, second.payload_checksum, second.idempotency_key) == intent
    assert second.dispatch_operation == "PATCH" and second.dispatch_external_id == "created-123"
    assert second.binding_source_id == first.id
    assert second.target_external_id == third.target_external_id == "created-123"
    client.deliver(second)
    assert len(calls) == 3


@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
def test_bound_timeout_restart_reads_exact_created_object(
    session: Session, settings: Settings, provider: str
) -> None:
    _, first = seeded_item(session, settings, provider)
    first.status, first.target_external_id = "delivered", "created-123"
    session.commit()
    newer = next_item(session, first)
    newer.operation, newer.target_external_id = "POST", None
    session.commit()
    item_id = newer.id
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "PATCH":
            raise httpx.ReadTimeout("lost response", request=request)
        if len(calls) == 2:
            return httpx.Response(503, request=request)
        record = remote_record(newer)
        record["id" if provider == "hubspot" else "Id"] = "created-123"
        return httpx.Response(200, json=record, request=request)

    client = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(DeliveryOutcomeUnknown):
        client.deliver(newer)
    assert [method for method, _ in calls] == ["PATCH", "GET"]
    assert all(path.endswith("/created-123") for _, path in calls)
    with Session(session.get_bind()) as restarted:
        saved = restarted.get(OutboxItem, item_id)
        assert saved is not None and saved.ordering_hold
        assert saved.dispatch_operation == "PATCH" and saved.dispatch_external_id == "created-123"
        assert client.deliver(saved)["outcome"] == "desired_state_observed"
        assert client.deliver(saved)["outcome"] == "already_acknowledged"
    assert [method for method, _ in calls] == ["PATCH", "GET", "GET"]
    assert all(path.endswith("/created-123") for _, path in calls)


@pytest.mark.parametrize(
    "problem", ["conflicting", "missing", "staged_conflict", "missing_patch", "post_id", "stamp"]
)
def test_unsafe_identity_blocks_before_http(
    session: Session, settings: Settings, problem: str
) -> None:
    _, first = seeded_item(session, settings)
    newer = next_item(session, first)
    first.status = "delivered"
    if problem == "conflicting":
        first.target_external_id = "one"
        newer.status, newer.target_external_id = "delivered", "two"
        newer = next_item(session, newer)
    elif problem == "missing":
        first.target_external_id = None
    elif problem == "staged_conflict":
        newer.target_external_id = "different"
    else:
        # No older history in these malformed standalone intent cases.
        newer = first
        newer.status = "preview_only"
        if problem == "missing_patch":
            newer.operation, newer.target_external_id = "PATCH", None
        elif problem == "post_id":
            newer.operation = "POST"
        else:
            newer.payload = {"properties": {"gtm_canonical_id": "wrong"}}
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("unsafe identity made an HTTP request")

    with pytest.raises(TargetBindingConflict):
        GuardedDeliveryClient(
            live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
        ).deliver(newer)
    assert newer.attempts == 0 and newer.claim_token is None


def test_reconciled_create_can_bind_after_audited_hold_release(
    session: Session, settings: Settings
) -> None:
    _, first = seeded_item(session, settings)
    first.operation, first.target_external_id = "POST", "observed-123"
    first.status, first.ordering_hold = "reconciled", True
    session.commit()
    newer = next_item(session, first)
    newer.operation, newer.target_external_id = "POST", None
    session.commit()
    release_ordering_hold(
        session,
        first.id,
        reviewer="test operator",
        reason="synthetic settled operation",
        provider_settled=True,
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert request.url.path.endswith("/observed-123")
        return httpx.Response(204, request=request)

    GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    ).deliver(newer)
    assert calls == ["PATCH"]


def test_expired_owner_cannot_persist_binding(session: Session, settings: Settings) -> None:
    _, item = seeded_item(session, settings)
    now = [datetime(2026, 9, 13, tzinfo=UTC)]
    client = GuardedDeliveryClient(live_settings(settings), clock=lambda: now[0])
    token = client._claim(item)
    assert token is not None
    now[0] += timedelta(seconds=settings.outbox_claim_seconds + 1)
    with pytest.raises(WorkerClaimLost):
        client._bind_target(item, token)
    session.refresh(item)
    assert item.dispatch_operation is None and item.attempts == 0
    client._release(item, token)


def test_binding_migration_preserves_existing_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{tmp_path / 'binding.sqlite3'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0004")
    engine = build_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO canonical_accounts (id,name,resolution_basis,created_at,updated_at) "
                "VALUES ('canonical','Migration Test','isolated_source_record',"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO outbox_items (id,canonical_account_id,target_provider,operation,"
                "idempotency_key,payload,status,attempts,created_at,sequence,payload_checksum) "
                "VALUES ('existing','canonical','hubspot','POST','key','{}','preview_only',"
                "0,CURRENT_TIMESTAMP,1,'checksum')"
            )
        )
    command.upgrade(config, "head")
    command.check(config)
    factory = build_session_factory(engine)
    with factory() as session:
        item = session.get(OutboxItem, "existing")
        assert item is not None
        item_id, intent = item.id, (item.operation, item.payload_checksum, item.idempotency_key)
    command.downgrade(config, "0004")
    assert "dispatch_operation" not in {
        col["name"] for col in inspect(engine).get_columns("outbox_items")
    }
    command.upgrade(config, "head")
    with factory() as session:
        saved = session.get(OutboxItem, item_id)
        assert saved is not None
        assert (saved.operation, saved.payload_checksum, saved.idempotency_key) == intent
        assert (
            saved.dispatch_operation
            is saved.dispatch_external_id
            is saved.binding_source_id
            is None
        )
    engine.dispose()
