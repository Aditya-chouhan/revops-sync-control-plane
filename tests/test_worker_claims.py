from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from test_gateways import seeded_item
from test_timeout_recovery import live_settings, remote_record

from alembic import command
from revops_sync.config import Settings
from revops_sync.db import Base, build_engine, build_session_factory
from revops_sync.gateways import GuardedDeliveryClient, WorkerClaimLost, WorkerClaimUnavailable
from revops_sync.models import OutboxItem


@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
@pytest.mark.parametrize("create", [False, True])
def test_two_workers_with_stale_snapshots_send_only_one_write(
    tmp_path: Path,
    settings: Settings,
    provider: str,
    create: bool,
) -> None:
    # File-backed SQLite: independent sessions/connections, not the shared
    # in-memory test connection. HTTP blocks while the losing worker claims.
    engine = build_engine(f"sqlite:///{tmp_path / 'concurrent.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    with factory() as seed:
        _, item = seeded_item(seed, settings, provider)
        if create:
            item.operation, item.target_external_id = "POST", None
            seed.commit()
        item_id = item.id
    start, sent, finish = Barrier(2), Event(), Event()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        sent.set()
        assert finish.wait(5), "test controller did not release provider response"
        return httpx.Response(201 if create else 200, json={"id": "crm-123"}, request=request)

    client = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    def worker() -> str:
        with factory() as active:
            candidate = active.get(OutboxItem, item_id)
            assert candidate is not None and candidate.claim_token is None
            start.wait(timeout=5)
            try:
                client.deliver(candidate)
                return "sent"
            except WorkerClaimUnavailable:
                return "busy"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            try:
                assert sent.wait(5)
                done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
                assert len(done) == 1 and next(iter(done)).result() == "busy"
                assert len(calls) == 1
            finally:
                finish.set()
            assert sorted(future.result(timeout=5) for future in futures) == ["busy", "sent"]
        with factory() as reader:
            saved = reader.get(OutboxItem, item_id)
            assert saved is not None and saved.status == "delivered"
            assert saved.attempts == 1 and saved.claim_token is None
            assert saved.claim_expires_at is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("prior_status", ["preview_only", "dispatching", "retryable_failed"])
def test_expired_claim_is_read_only_and_stale_owner_is_fenced(
    session: Session,
    settings: Settings,
    prior_status: str,
) -> None:
    _, item = seeded_item(session, settings)
    start = datetime(2026, 9, 13, 12, tzinfo=UTC)
    old = GuardedDeliveryClient(live_settings(settings), clock=lambda: start)
    old_token = old._claim(item)
    assert old_token is not None
    old._store(item, old_token, prior_status)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=remote_record(item), request=request)

    successor = GuardedDeliveryClient(
        live_settings(settings),
        clock=lambda: start + timedelta(seconds=settings.outbox_claim_seconds + 1),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with Session(session.get_bind()) as newer:
        candidate = newer.get(OutboxItem, item.id)
        assert candidate is not None
        new_token = successor._claim(candidate)
        assert new_token is not None and new_token != old_token
        assert candidate.status == "outcome_unknown"
        # Even an unexpired local clock cannot bypass the token fence.
        with pytest.raises(WorkerClaimLost):
            old._store(item, old_token, "delivered", external_id="stale-owner-id")
        old._release(item, old_token)
        newer.refresh(candidate)
        assert candidate.claim_token == new_token
        assert (
            successor._deliver_claimed(candidate, new_token)["outcome"] == "desired_state_observed"
        )
        successor._release(candidate, new_token)
        assert candidate.status == "reconciled" and candidate.attempts == 0
        assert candidate.target_external_id != "stale-owner-id"
    assert calls == ["GET"]


def test_late_response_cannot_overwrite_takeover_result(
    session: Session, settings: Settings
) -> None:
    _, item = seeded_item(session, settings)
    start = datetime(2026, 9, 13, 12, tzinfo=UTC)
    now = [start]
    live = live_settings(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        now[0] += timedelta(seconds=settings.outbox_claim_seconds + 1)

        def readback(read: httpx.Request) -> httpx.Response:
            assert read.method == "GET"
            return httpx.Response(200, json=remote_record(item), request=read)

        with Session(session.get_bind()) as newer:
            candidate = newer.get(OutboxItem, item.id)
            assert candidate is not None
            result = GuardedDeliveryClient(
                live,
                clock=lambda: now[0],
                client=httpx.Client(transport=httpx.MockTransport(readback)),
            ).deliver(candidate)
            assert result["outcome"] == "desired_state_observed"
        return httpx.Response(200, request=request)

    old = GuardedDeliveryClient(
        live, clock=lambda: now[0], client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(WorkerClaimLost):
        old.deliver(item)
    session.refresh(item)
    assert item.status == "reconciled" and item.attempts == 1
    assert item.claim_token is None


def test_active_claim_blocks_recovery_http(session: Session, settings: Settings) -> None:
    _, item = seeded_item(session, settings)
    owner = GuardedDeliveryClient(live_settings(settings))
    token = owner._claim(item)
    assert token is not None
    owner._store(item, token, "outcome_unknown")

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("losing worker made an HTTP request")

    with Session(session.get_bind()) as other:
        candidate = other.get(OutboxItem, item.id)
        assert candidate is not None
        with pytest.raises(WorkerClaimUnavailable):
            GuardedDeliveryClient(
                live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
            ).deliver(candidate)
    owner._release(item, token)


def test_expired_owner_cannot_begin_a_write(session: Session, settings: Settings) -> None:
    _, item = seeded_item(session, settings)
    start = datetime(2026, 9, 13, 12, tzinfo=UTC)
    now = [start]
    owner = GuardedDeliveryClient(live_settings(settings), clock=lambda: now[0])
    token = owner._claim(item)
    assert token is not None
    now[0] += timedelta(seconds=settings.outbox_claim_seconds + 1)
    with pytest.raises(WorkerClaimLost):
        owner._store(item, token, "dispatching", increment_attempt=True)
    owner._release(item, token)
    assert item.attempts == 0


def test_dirty_session_cannot_flush_stale_state_over_a_claim(
    session: Session,
    settings: Settings,
) -> None:
    _, item = seeded_item(session, settings)
    item.status = "preview_only"
    with pytest.raises(RuntimeError, match="clean dedicated session"):
        GuardedDeliveryClient(live_settings(settings)).deliver(item)
    session.rollback()


def test_worker_claim_migration_preserves_existing_unknown_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{tmp_path / 'migration.sqlite3'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0002")
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
                "VALUES ('existing','canonical','hubspot','POST','key','{}','outcome_unknown',"
                "3,CURRENT_TIMESTAMP,1,'checksum')"
            )
        )
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status,attempts,claim_token,claim_expires_at "
                "FROM outbox_items WHERE id='existing'"
            )
        ).one()
        assert tuple(row) == ("outcome_unknown", 3, None, None)
    command.downgrade(config, "0002")
    assert "claim_token" not in {
        column["name"] for column in inspect(engine).get_columns("outbox_items")
    }
    command.upgrade(config, "head")
    engine.dispose()
