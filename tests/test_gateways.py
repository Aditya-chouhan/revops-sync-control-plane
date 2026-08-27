from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from revops_sync.config import Settings
from revops_sync.gateways import GuardedDeliveryClient, integration_preview
from revops_sync.models import CanonicalAccount, OutboxItem
from revops_sync.reconcile import ReconciliationService
from revops_sync.schemas import ReconcileRequest


def seeded_item(
    session: Session, settings: Settings, provider: str = "hubspot"
) -> tuple[CanonicalAccount, OutboxItem]:
    ReconciliationService(session, settings).run(ReconcileRequest())
    item = session.scalar(
        select(OutboxItem).where(
            OutboxItem.target_provider == provider,
            OutboxItem.target_external_id.is_not(None),
        )
    )
    assert item is not None
    account = session.get(CanonicalAccount, item.canonical_account_id)
    assert account is not None
    return account, item


def test_preview_never_writes(session: Session, settings: Settings) -> None:
    account, item = seeded_item(session, settings)
    preview = integration_preview("hubspot", account, item, settings)
    assert preview.would_write is False
    assert preview.live_switches_enabled is False
    assert "No CRM request" in preview.claim_boundary


def test_delivery_fails_closed_without_switches(session: Session, settings: Settings) -> None:
    _, item = seeded_item(session, settings)
    with pytest.raises(RuntimeError, match="disabled"):
        GuardedDeliveryClient(settings).deliver(item)


def test_retry_then_success_uses_idempotency_key(session: Session, settings: Settings) -> None:
    _, item = seeded_item(session, settings)
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, request=request)
        return httpx.Response(200, json={"id": "hs-1001"}, request=request)

    live = settings.model_copy(
        update={
            "live_integrations_enabled": True,
            "hubspot_live_enabled": True,
            "hubspot_access_token": SecretStr("not-a-real-token"),
        }
    )
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = GuardedDeliveryClient(live, client=client, sleep=sleeps.append).deliver(item)
    assert result == {"status_code": 200, "provider": "hubspot"}
    assert len(attempts) == 2
    assert attempts[0].headers["Idempotency-Key"] == item.idempotency_key
    assert len(sleeps) == 1
