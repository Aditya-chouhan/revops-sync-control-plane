from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from revops_sync.config import Settings
from revops_sync.models import CanonicalAccount, OutboxItem
from revops_sync.schemas import IntegrationPreview


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
        endpoint = (
            f"/services/data/{settings.salesforce_api_version}/sobjects/Account/"
            + ("{external_id}" if item.target_external_id else "")
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
    ):
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.connector_timeout_seconds)
        self.sleep = sleep

    def deliver(self, item: OutboxItem) -> dict[str, Any]:
        provider = item.target_provider
        self._assert_enabled(provider)
        url, headers = self._request_parts(provider, item)
        last_error: Exception | None = None
        for attempt in range(self.settings.connector_max_retries + 1):
            try:
                response = self.client.request(
                    item.operation,
                    url,
                    json=item.payload,
                    headers={**headers, "Idempotency-Key": item.idempotency_key},
                )
                if response.status_code not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    return {"status_code": response.status_code, "provider": provider}
                last_error = httpx.HTTPStatusError(
                    f"retryable provider response {response.status_code}",
                    request=response.request,
                    response=response,
                )
                retry_after = response.headers.get("retry-after")
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                retry_after = None
            if attempt >= self.settings.connector_max_retries:
                break
            delay = (
                float(retry_after)
                if retry_after and retry_after.isdigit()
                else min(2**attempt, 16)
            )
            self.sleep(delay + random.uniform(0, 0.25))
        if last_error is None:
            raise RuntimeError("delivery failed without an error")
        raise last_error

    def _assert_enabled(self, provider: str) -> None:
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
