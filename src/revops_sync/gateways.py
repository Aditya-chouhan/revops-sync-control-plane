from __future__ import annotations

import random
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import object_session
from sqlalchemy.sql.selectable import Exists

from revops_sync.config import Settings
from revops_sync.models import CanonicalAccount, OutboxItem
from revops_sync.schemas import IntegrationPreview


class DeliveryOutcomeUnknown(RuntimeError):
    """A write may have landed; replay is blocked until read-back proves its state."""


class WorkerClaimUnavailable(RuntimeError):
    """Another worker owns this item; the caller must not make any HTTP request."""


class WorkerClaimLost(RuntimeError):
    """A stale worker cannot dispatch, acknowledge, or release another owner's claim."""


class OutboxOrderingBlocked(WorkerClaimUnavailable):
    """An older item in the same account/provider stream is not settled."""


def _blocking_predecessor() -> Exists:
    prior = OutboxItem.__table__.alias("prior_outbox")
    return (
        select(prior.c.id)
        .where(
            prior.c.canonical_account_id == OutboxItem.canonical_account_id,
            prior.c.target_provider == OutboxItem.target_provider,
            prior.c.sequence < OutboxItem.sequence,
            or_(
                prior.c.status.not_in(["delivered", "reconciled"]),
                prior.c.claim_token.is_not(None),
                prior.c.ordering_hold.is_(True),
            ),
        )
        .correlate(OutboxItem.__table__)
        .exists()
    )


def _same_value(actual: Any, expected: Any) -> bool:
    # HubSpot returns typed property values as strings. Do not coerce arbitrary
    # text, null, or missing keys into a match.
    if isinstance(expected, bool):
        return actual == expected if isinstance(actual, bool) else actual == str(expected).lower()
    if isinstance(expected, int) and not isinstance(expected, bool):
        if isinstance(actual, bool):
            return False
        return actual == expected if isinstance(actual, int) else actual == str(expected)
    return bool(actual == expected)


def _credentials_present(provider: str, settings: Settings) -> bool:
    if provider == "hubspot":
        return settings.hubspot_access_token is not None
    return bool(settings.salesforce_access_token and settings.salesforce_instance_url)


def integration_preview(
    provider: str, account: CanonicalAccount, item: OutboxItem, settings: Settings
) -> IntegrationPreview:
    if provider == "hubspot":
        endpoint = (
            "/crm/v3/objects/companies/{external_id}"
            if item.target_external_id
            else "/crm/v3/objects/companies"
        )
        enabled = settings.live_integrations_enabled and settings.hubspot_live_enabled
    elif provider == "salesforce":
        endpoint = f"/services/data/{settings.salesforce_api_version}/sobjects/Account/" + (
            "{external_id}" if item.target_external_id else ""
        )
        enabled = settings.live_integrations_enabled and settings.salesforce_live_enabled
    else:
        raise ValueError(f"Unsupported provider: {provider}")
    return IntegrationPreview(
        provider=provider,
        account_id=account.id,
        external_id=item.target_external_id,
        method=item.operation,
        endpoint_template=endpoint,
        payload=item.payload,
        credential_present=_credentials_present(provider, settings),
        live_switches_enabled=enabled,
        claim_boundary="Preview only. No CRM request was sent and no sync result is claimed.",
    )


class GuardedDeliveryClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.connector_timeout_seconds)
        self.sleep = sleep
        self.clock = clock

    def deliver(self, item: OutboxItem) -> dict[str, Any]:
        provider = item.target_provider
        self._assert_enabled(provider)
        if object_session(item) is None:
            raise RuntimeError("Delivery requires a persisted outbox item in a dedicated session")
        token = self._claim(item)
        if token is None:
            return {
                "provider": provider,
                "outcome": "already_acknowledged",
                "external_id": item.target_external_id,
            }
        try:
            return self._deliver_claimed(item, token)
        finally:
            self._release(item, token)

    def _claim(self, item: OutboxItem) -> str | None:
        session = object_session(item)
        if session is None:
            raise RuntimeError("Outbox item is detached")
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("Delivery requires a clean dedicated session; commit staging first")
        now = self.clock()
        token = str(uuid.uuid4())
        statement = (
            update(OutboxItem)
            .where(
                OutboxItem.id == item.id,
                OutboxItem.status.in_(
                    ["preview_only", "retryable_failed", "dispatching", "outcome_unknown"]
                ),
                or_(OutboxItem.claim_token.is_(None), OutboxItem.claim_expires_at <= now),
                ~_blocking_predecessor(),
            )
            .values(
                # A reclaimed lease NEVER opens a write path, even if the prior
                # worker died before its dispatch marker or during retry sleep.
                status=case(
                    (OutboxItem.claim_token.is_not(None), "outcome_unknown"),
                    else_=OutboxItem.status,
                ),
                claim_token=token,
                claim_expires_at=now + timedelta(seconds=self.settings.outbox_claim_seconds),
                ordering_hold=case(
                    (OutboxItem.claim_token.is_not(None), True), else_=OutboxItem.ordering_hold
                ),
            )
            .returning(OutboxItem.id)
            .execution_options(synchronize_session=False)
        )
        claimed = session.execute(statement).scalar_one_or_none()
        session.commit()
        session.refresh(item)  # discard a worker's stale pre-claim ORM snapshot
        if claimed is not None:
            return token
        if item.status in {"delivered", "reconciled"}:
            return None
        blocked = session.scalar(
            select(OutboxItem.id).where(
                OutboxItem.id == item.id,
                _blocking_predecessor(),
            )
        )
        if blocked is not None:
            raise OutboxOrderingBlocked(
                f"Outbox {item.id}: an older stream item is unresolved or held"
            )
        raise WorkerClaimUnavailable(f"Outbox {item.id}: active claim or non-deliverable state")

    def _deliver_claimed(self, item: OutboxItem, token: str) -> dict[str, Any]:
        provider = item.target_provider
        if item.status in {"dispatching", "outcome_unknown"}:
            return self._recover(item, token, "previous write has an unconfirmed outcome")
        if item.status not in {"preview_only", "retryable_failed"}:
            raise RuntimeError(f"Outbox status {item.status} does not permit delivery")
        url, headers = self._request_parts(provider, item)
        last_error: Exception | None = None
        for attempt in range(self.settings.connector_max_retries + 1):
            self._store(item, token, "dispatching", increment_attempt=True)
            try:
                response = self.client.request(
                    item.operation,
                    url,
                    json=item.payload,
                    headers={**headers, "Idempotency-Key": item.idempotency_key},
                )
                if response.status_code >= 500:
                    return self._recover(item, token, f"ambiguous HTTP {response.status_code}")
                if response.status_code != 429:
                    if response.is_error:
                        self._store(
                            item, token, "delivery_rejected", f"HTTP {response.status_code}"
                        )
                    response.raise_for_status()
                    if response.status_code not in {200, 201, 204}:
                        return self._recover(
                            item, token, "provider returned a non-final success status"
                        )
                    if item.operation == "POST":
                        try:
                            external_id = response.json().get("id")
                        except (ValueError, AttributeError):
                            external_id = None
                        if not isinstance(external_id, str) or not external_id:
                            return self._recover(
                                item, token, "successful create omitted its object ID"
                            )
                        self._store(
                            item, token, "delivered", external_id=external_id, ordering_hold=False
                        )
                    else:
                        self._store(item, token, "delivered", ordering_hold=False)
                    return {"status_code": response.status_code, "provider": provider}
                last_error = httpx.HTTPStatusError(
                    f"retryable provider response {response.status_code}",
                    request=response.request,
                    response=response,
                )
                retry_after = response.headers.get("retry-after")
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                last_error = exc
                retry_after = None
            except httpx.TransportError:
                return self._recover(item, token, "ambiguous transport failure after dispatch")
            self._store(
                item,
                token,
                "retryable_failed",
                "request not accepted or connection not established",
                ordering_hold=False,
            )
            if attempt >= self.settings.connector_max_retries:
                break
            delay = (
                float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 16)
            )
            self.sleep(delay + random.uniform(0, 0.25))
        if last_error is None:
            raise RuntimeError("delivery failed without an error")
        raise last_error

    def _store(
        self,
        item: OutboxItem,
        token: str,
        status: str,
        error: str | None = None,
        *,
        increment_attempt: bool = False,
        external_id: str | None = None,
        ordering_hold: bool | None = None,
    ) -> None:
        session = object_session(item)
        if session is None:
            raise RuntimeError("Outbox item is detached")
        now = self.clock()
        values: dict[str, Any] = {
            "status": status,
            "last_error": error,
            "claim_expires_at": now + timedelta(seconds=self.settings.outbox_claim_seconds),
        }
        if increment_attempt:
            values["attempts"] = OutboxItem.attempts + 1
            values["ordering_hold"] = True
        if ordering_hold is not None:
            values["ordering_hold"] = ordering_hold
        if external_id is not None:
            values["target_external_id"] = external_id
        saved = session.execute(
            update(OutboxItem)
            .where(
                OutboxItem.id == item.id,
                OutboxItem.claim_token == token,
                OutboxItem.claim_expires_at > now,
            )
            .values(**values)
            .returning(OutboxItem.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        session.commit()
        if saved is None:
            raise WorkerClaimLost(f"Outbox {item.id}: claim expired or ownership changed")
        session.refresh(item)

    def _release(self, item: OutboxItem, token: str) -> None:
        session = object_session(item)
        if session is None:
            return
        # Roll back any failed DB transaction before the fenced cleanup. Never
        # flush a stale object's attributes over a successor's outcome.
        session.rollback()
        session.execute(
            update(OutboxItem)
            .where(
                OutboxItem.id == item.id,
                OutboxItem.claim_token == token,
            )
            .values(claim_token=None, claim_expires_at=None)
            .execution_options(synchronize_session=False)
        )
        session.commit()
        session.refresh(item)

    def _recover(self, item: OutboxItem, token: str, reason: str) -> dict[str, Any]:
        self._store(item, token, "outcome_unknown", reason, ordering_hold=True)
        try:
            external_id = self._observe_desired_state(item)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            external_id = None
        if external_id is None:
            raise DeliveryOutcomeUnknown(
                f"Outbox {item.id}: outcome unknown; no further write was sent. "
                "Inspect provider state and follow docs/RECOVERY.md."
            )
        self._store(item, token, "reconciled", external_id=external_id)
        return {
            "provider": item.target_provider,
            "outcome": "desired_state_observed",
            "external_id": external_id,
            "ordering_hold": item.ordering_hold,
        }

    def _observe_desired_state(self, item: OutboxItem) -> str | None:
        """Read the exact object, or require a unique canonical-ID search match.

        Matching proves desired state exists, not which request caused it. A
        missing search result is NOT evidence that a timed-out create failed.
        """
        provider = item.target_provider
        url, headers = self._request_parts(provider, item)
        fields = item.payload["properties"] if provider == "hubspot" else item.payload
        canonical_field = "gtm_canonical_id" if provider == "hubspot" else "GTM_Canonical_ID__c"
        if fields.get(canonical_field) != item.canonical_account_id:
            return None
        if item.target_external_id:
            response = self.client.get(
                url,
                headers=headers,
                params={"properties": ",".join(fields)} if provider == "hubspot" else None,
            )
            response.raise_for_status()
            record = response.json()
        elif provider == "hubspot":
            response = self.client.post(
                url + "/search",
                headers=headers,
                json={
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": canonical_field,
                                    "operator": "EQ",
                                    "value": item.canonical_account_id,
                                }
                            ]
                        }
                    ],
                    "properties": list(fields),
                    "limit": 2,
                },
            )  # search POST is a read, not a company create
            response.raise_for_status()
            body = response.json()
            if body.get("total") != 1 or len(body.get("results", [])) != 1:
                return None
            record = body["results"][0]
        else:
            if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in fields):
                return None
            canonical_id = item.canonical_account_id.replace("\\", "\\\\").replace("'", "\\'")
            base = url.rsplit("/sobjects/Account", 1)[0]
            query = (
                f"SELECT Id,{','.join(fields)} FROM Account "
                f"WHERE {canonical_field} = '{canonical_id}' LIMIT 2"
            )
            response = self.client.get(base + "/query", headers=headers, params={"q": query})
            response.raise_for_status()
            body = response.json()
            if body.get("totalSize") != 1 or len(body.get("records", [])) != 1:
                return None
            record = body["records"][0]
        if record.get("archived") is True:
            return None
        actual = record.get("properties", {}) if provider == "hubspot" else record
        if any(
            key not in actual or not _same_value(actual[key], value)
            for key, value in fields.items()
        ):
            return None
        external_id = record.get("id" if provider == "hubspot" else "Id")
        if not isinstance(external_id, str) or not external_id:
            return None
        if item.target_external_id and external_id != item.target_external_id:
            return None
        return external_id

    def _assert_enabled(self, provider: str) -> None:
        if provider not in {"hubspot", "salesforce"}:
            raise ValueError(f"Unsupported provider: {provider}")
        provider_enabled = (
            self.settings.hubspot_live_enabled
            if provider == "hubspot"
            else self.settings.salesforce_live_enabled
        )
        if not self.settings.live_integrations_enabled or not provider_enabled:
            raise RuntimeError("Live CRM delivery is disabled by configuration")
        if not _credentials_present(provider, self.settings):
            raise RuntimeError(f"Missing {provider} credentials")

    def _request_parts(self, provider: str, item: OutboxItem) -> tuple[str, dict[str, str]]:
        suffix = f"/{item.target_external_id}" if item.target_external_id else ""
        if provider == "hubspot":
            token = self.settings.hubspot_access_token
            if token is None:
                raise RuntimeError("Missing hubspot credentials")
            return (
                f"{self.settings.hubspot_base_url}/crm/v3/objects/companies{suffix}",
                {"Authorization": f"Bearer {token.get_secret_value()}"},
            )
        token = self.settings.salesforce_access_token
        instance = self.settings.salesforce_instance_url
        if token is None or instance is None:
            raise RuntimeError("Missing salesforce credentials")
        return (
            f"{instance.rstrip('/')}/services/data/{self.settings.salesforce_api_version}/sobjects/Account{suffix}",
            {"Authorization": f"Bearer {token.get_secret_value()}"},
        )
