from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from typing import Any

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session
from test_gateways import seeded_item
from test_timeout_recovery import live_settings, remote_record

from alembic import command
from revops_sync.config import Settings
from revops_sync.db import Base, build_engine, build_session_factory
from revops_sync.gateways import GuardedDeliveryClient, OutboxOrderingBlocked, WorkerClaimLost
from revops_sync.models import OrderingResolution, OutboxItem
from revops_sync.ordering import release_ordering_hold


def next_item(session: Session, older: OutboxItem) -> OutboxItem:
    sequence = older.sequence + 1
    payload = copy.deepcopy(older.payload)
    fields = payload["properties"] if older.target_provider == "hubspot" else payload
    fields["name" if older.target_provider == "hubspot" else "Name"] = "Newer account value"
    key = hashlib.sha256(f"{older.id}:{sequence}".encode()).hexdigest()
    item = OutboxItem(
        id=f"{older.canonical_account_id}:{older.target_provider}:{sequence}",
        canonical_account_id=older.canonical_account_id,
        target_provider=older.target_provider,
        target_external_id=older.target_external_id,
        operation=older.operation,
        sequence=sequence,
        payload=payload,
        payload_checksum=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
        idempotency_key=key,
        status="preview_only",
    )
    session.add(item)
    session.commit()
    return item


@pytest.mark.parametrize(
    "status",
    ["preview_only", "retryable_failed", "dispatching", "outcome_unknown", "delivery_rejected"],
)
def test_newer_item_cannot_pass_unresolved_prefix(
    session: Session,
    settings: Settings,
    status: str,
) -> None:
    _, older = seeded_item(session, settings)
    newer = next_item(session, older)
    older.status = status
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("blocked stream issued HTTP")

    with pytest.raises(OutboxOrderingBlocked):
        GuardedDeliveryClient(
            live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
        ).deliver(newer)
    assert newer.attempts == 0 and newer.claim_token is None


@pytest.mark.parametrize("scope", ["provider", "account"])
def test_unrelated_streams_are_not_blocked(
    session: Session, settings: Settings, scope: str
) -> None:
    _, held = seeded_item(session, settings)
    if scope == "provider":
        # The first HubSpot fixture may be an isolated no-domain account,
        # so choose a matched account that actually exists in both providers.
        matched = session.scalar(
            select(OutboxItem).where(
                OutboxItem.target_provider == "hubspot",
                OutboxItem.target_external_id.is_not(None),
                OutboxItem.canonical_account_id.in_(
                    select(OutboxItem.canonical_account_id).where(
                        OutboxItem.target_provider == "salesforce",
                        OutboxItem.target_external_id.is_not(None),
                    )
                ),
            )
        )
        assert matched is not None
        held = matched
    held.ordering_hold = True
    session.commit()
    statement = select(OutboxItem).where(OutboxItem.target_external_id.is_not(None))
    if scope == "provider":
        statement = statement.where(
            OutboxItem.canonical_account_id == held.canonical_account_id,
            OutboxItem.target_provider != held.target_provider,
        )
    else:
        statement = statement.where(
            OutboxItem.canonical_account_id != held.canonical_account_id,
            OutboxItem.target_provider == held.target_provider,
        )
    independent = session.scalar(statement)
    assert independent is not None
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json={"id": independent.target_external_id}, request=request)

    GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    ).deliver(independent)
    assert calls == ["PATCH"]


def test_acknowledged_predecessor_still_blocks_until_owner_releases(
    session: Session,
    settings: Settings,
) -> None:
    _, older = seeded_item(session, settings)
    newer = next_item(session, older)
    owner = GuardedDeliveryClient(live_settings(settings))
    token = owner._claim(older)
    assert token is not None
    owner._store(older, token, "delivered", ordering_hold=False)
    with Session(session.get_bind()) as second:
        candidate = second.get(OutboxItem, newer.id)
        assert candidate is not None
        with pytest.raises(OutboxOrderingBlocked):
            owner.deliver(candidate)
    owner._release(older, token)
    new_token = owner._claim(newer)
    assert new_token is not None
    owner._release(newer, new_token)


@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
def test_racing_different_sequences_dispatch_in_order(
    tmp_path: Path,
    settings: Settings,
    provider: str,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'stream.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    with factory() as seed:
        _, older = seeded_item(seed, settings, provider)
        newer = next_item(seed, older)
        old_id, new_id = older.id, newer.id
    start, sent, finish = Barrier(2), Event(), Event()
    names: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        fields = body["properties"] if provider == "hubspot" else body
        names.append(fields["name" if provider == "hubspot" else "Name"])
        if len(names) == 1:
            sent.set()
            assert finish.wait(5)
        return httpx.Response(200, request=request)

    client = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    def worker(item_id: str) -> str:
        with factory() as active:
            candidate = active.get(OutboxItem, item_id)
            assert candidate is not None
            start.wait(timeout=5)
            try:
                client.deliver(candidate)
                return "sent"
            except OutboxOrderingBlocked:
                return "blocked"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, item_id) for item_id in (old_id, new_id)]
            try:
                assert sent.wait(5)
                done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
                assert len(done) == 1 and next(iter(done)).result() == "blocked"
                assert len(names) == 1
            finally:
                finish.set()
            assert [future.result(timeout=5) for future in futures] == ["sent", "blocked"]
        with factory() as retry:
            candidate = retry.get(OutboxItem, new_id)
            assert candidate is not None
            client.deliver(candidate)
            assert candidate.attempts == 1 and candidate.status == "delivered"
        assert len(names) == 2 and names[-1] == "Newer account value"
    finally:
        engine.dispose()


def test_matching_readback_keeps_ordering_hold_until_audited_release(
    session: Session,
    settings: Settings,
) -> None:
    _, older = seeded_item(session, settings)
    newer = next_item(session, older)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            raise httpx.ReadTimeout("write may still be in flight", request=request)
        return httpx.Response(200, json=remote_record(older), request=request)

    client = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = client.deliver(older)
    assert result["outcome"] == "desired_state_observed" and result["ordering_hold"] is True
    assert older.status == "reconciled" and older.ordering_hold is True
    with pytest.raises(OutboxOrderingBlocked):
        client.deliver(newer)
    assert calls == ["PATCH", "GET"]
    resolution = release_ordering_hold(
        session,
        older.id,
        reviewer="test-operator",
        reason="Simulated provider settled; stale workers stopped",
        provider_settled=True,
    )
    audit = session.get(OrderingResolution, resolution)
    assert audit is not None and audit.reviewer == "test-operator"
    client.deliver(newer)
    assert calls == ["PATCH", "GET", "PATCH"]


def test_every_older_item_is_checked_not_only_the_latest(
    session: Session,
    settings: Settings,
) -> None:
    _, first = seeded_item(session, settings)
    middle = next_item(session, first)
    newest = next_item(session, middle)
    first.status, middle.status = "outcome_unknown", "delivered"
    session.commit()
    with pytest.raises(OutboxOrderingBlocked):
        GuardedDeliveryClient(live_settings(settings)).deliver(newest)
    assert newest.attempts == 0


def test_hold_release_requires_attestation_and_does_not_forge_ack(
    session: Session,
    settings: Settings,
) -> None:
    _, item = seeded_item(session, settings)
    item.ordering_hold = True
    session.commit()
    with pytest.raises(ValueError):
        release_ordering_hold(session, item.id, reviewer="reviewer", reason="reason")
    with pytest.raises(RuntimeError):
        release_ordering_hold(
            session, item.id, reviewer="reviewer", reason="reason", provider_settled=True
        )
    assert item.status == "preview_only" and item.ordering_hold is True
    assert session.scalar(select(func.count()).select_from(OrderingResolution)) == 0


def test_audit_failure_rolls_back_hold_release(session: Session, settings: Settings) -> None:
    _, item = seeded_item(session, settings)
    item.status, item.ordering_hold = "reconciled", True
    session.commit()

    def fail_audit(active: Session, context: Any, instances: Any) -> None:
        raise RuntimeError("simulated audit insert failure")

    with Session(session.get_bind()) as operator:
        event.listen(operator, "before_flush", fail_audit)
        with pytest.raises(RuntimeError, match="audit insert failure"):
            release_ordering_hold(
                operator, item.id, reviewer="reviewer", reason="reason", provider_settled=True
            )
    session.refresh(item)
    assert item.ordering_hold is True
    assert session.scalar(select(func.count()).select_from(OrderingResolution)) == 0


def test_async_success_does_not_release_the_stream(session: Session, settings: Settings) -> None:
    _, older = seeded_item(session, settings)
    newer = next_item(session, older)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            202 if len(calls) == 1 else 200, json=remote_record(older), request=request
        )

    client = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert client.deliver(older)["ordering_hold"] is True
    with pytest.raises(OutboxOrderingBlocked):
        client.deliver(newer)
    assert calls == ["PATCH", "GET"]


def test_takeover_readback_cannot_advance_stream_while_old_request_is_live(
    session: Session,
    settings: Settings,
) -> None:
    _, older = seeded_item(session, settings)
    newer = next_item(session, older)
    now = [datetime(2026, 9, 13, 12, tzinfo=UTC)]
    live = live_settings(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        now[0] += timedelta(seconds=settings.outbox_claim_seconds + 1)

        def readback(read: httpx.Request) -> httpx.Response:
            assert read.method == "GET"
            return httpx.Response(200, json=remote_record(older), request=read)

        with Session(session.get_bind()) as successor:
            old_copy, new_copy = (
                successor.get(OutboxItem, older.id),
                successor.get(OutboxItem, newer.id),
            )
            assert old_copy is not None and new_copy is not None
            client = GuardedDeliveryClient(
                live,
                clock=lambda: now[0],
                client=httpx.Client(transport=httpx.MockTransport(readback)),
            )
            client.deliver(old_copy)
            with pytest.raises(OutboxOrderingBlocked):
                client.deliver(new_copy)
        return httpx.Response(200, request=request)

    with pytest.raises(WorkerClaimLost):
        GuardedDeliveryClient(
            live, clock=lambda: now[0], client=httpx.Client(transport=httpx.MockTransport(handler))
        ).deliver(older)
    session.refresh(older)
    assert older.ordering_hold is True and older.status == "reconciled"
    assert newer.attempts == 0


@pytest.mark.parametrize(
    "status,held",
    [("delivered", False), ("reconciled", True), ("dispatching", True), ("outcome_unknown", True)],
)
def test_ordering_migration_backfills_legacy_uncertainty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    held: bool,
) -> None:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{tmp_path / 'ordering_migration.sqlite3'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0003")
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
                "VALUES ('existing','canonical','hubspot','PATCH','key','{}',:status,"
                "3,CURRENT_TIMESTAMP,1,'checksum')"
            ),
            {"status": status},
        )
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT status,attempts,ordering_hold FROM outbox_items WHERE id='existing'")
        ).one()
        assert row[0] == status and row[1] == 3 and bool(row[2]) is held
    command.downgrade(config, "0003")
    command.upgrade(config, "head")
    engine.dispose()
