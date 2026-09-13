from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session
from test_gateways import seeded_item

from revops_sync.config import Settings
from revops_sync.gateways import DeliveryOutcomeUnknown, GuardedDeliveryClient, _same_value
from revops_sync.models import OutboxItem


def live_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "live_integrations_enabled": True,
            "hubspot_live_enabled": True,
            "salesforce_live_enabled": True,
            "hubspot_access_token": SecretStr("fake-token"),
            "salesforce_access_token": SecretStr("fake-token"),
            "salesforce_instance_url": "https://salesforce.invalid",
        }
    )


def remote_record(item: OutboxItem) -> dict[str, object]:
    if item.target_provider == "hubspot":
        properties = {
            key: str(value).lower()
            if isinstance(value, bool)
            else str(value)
            if isinstance(value, int)
            else value
            for key, value in item.payload["properties"].items()
        }
        return {"id": item.target_external_id or "created-123", "properties": properties}
    return {"Id": item.target_external_id or "created-123", **item.payload}


@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "write_timeout", "server_error"])
def test_success_response_lost_is_reconciled_without_another_write(
    session: Session,
    settings: Settings,
    provider: str,
    create: bool,
    failure: str,
) -> None:
    _, item = seeded_item(session, settings, provider)
    if create:
        item.operation, item.target_external_id = "POST", None
        session.commit()
    record = remote_record(item)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            with Session(session.get_bind()) as reader:
                marker = reader.get(OutboxItem, item.id)
                assert marker is not None and marker.status == "dispatching"
                assert marker.attempts == 1
            if failure == "server_error":
                return httpx.Response(503, request=request)
            error = httpx.ReadTimeout if failure == "timeout" else httpx.WriteTimeout
            raise error("provider applied the write, response lost", request=request)
        if create and provider == "hubspot":
            assert request.url.path.endswith("/search")
            assert json.loads(request.content)["filterGroups"][0]["filters"][0]["value"] == (
                item.canonical_account_id
            )
            body = {"total": 1, "results": [record]}
        elif create:
            assert request.url.path.endswith("/query")
            assert item.canonical_account_id in request.url.params["q"]
            body = {"totalSize": 1, "records": [record]}
        else:
            assert request.method == "GET"
            body = record
        return httpx.Response(200, json=body, request=request)

    client = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = client.deliver(item)
    assert result["outcome"] == "desired_state_observed"
    assert len(requests) == 2
    assert item.attempts == 1
    with Session(session.get_bind()) as restarted:
        saved = restarted.get(OutboxItem, item.id)
        assert saved is not None and saved.status == "reconciled"
        assert saved.target_external_id == record.get("id", record.get("Id"))
        assert client.deliver(saved)["outcome"] == "already_acknowledged"
    assert len(requests) == 2


@pytest.mark.parametrize("readback", ["missing", "different", "duplicate", "unavailable"])
def test_unconfirmed_create_stays_unknown_across_restart_and_never_replays(
    session: Session,
    settings: Settings,
    readback: str,
) -> None:
    _, item = seeded_item(session, settings)
    item.operation, item.target_external_id = "POST", None
    session.commit()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if not request.url.path.endswith("/search"):
            assert len(requests) == 1
            raise httpx.ReadTimeout("ambiguous create", request=request)
        record = remote_record(item)
        if readback == "unavailable":
            return httpx.Response(503, request=request)
        if readback == "different":
            record["properties"] = {**item.payload["properties"], "name": "newer seller edit"}
        body = (
            {"total": 0, "results": []}
            if readback == "missing"
            else {
                "total": 2 if readback == "duplicate" else 1,
                "results": [record, record] if readback == "duplicate" else [record],
            }
        )
        return httpx.Response(200, json=body, request=request)

    client = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(DeliveryOutcomeUnknown):
        client.deliver(item)
    with Session(session.get_bind()) as restarted:
        saved = restarted.get(OutboxItem, item.id)
        assert saved is not None and saved.status == "outcome_unknown"
        assert saved.attempts == 1
        with pytest.raises(DeliveryOutcomeUnknown):
            client.deliver(saved)
    assert len(requests) == 3  # one write, two read-only searches


def test_crash_after_dispatch_marker_recovers_by_read_only(
    session: Session,
    settings: Settings,
) -> None:
    _, item = seeded_item(session, settings)
    item.status, item.attempts = "dispatching", 1
    session.commit()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=remote_record(item), request=request)

    result = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    ).deliver(item)
    assert result["outcome"] == "desired_state_observed"
    assert calls == ["GET"]


def test_disabled_delivery_cannot_even_reconcile(session: Session, settings: Settings) -> None:
    _, item = seeded_item(session, settings)
    item.status = "outcome_unknown"
    with pytest.raises(RuntimeError, match="disabled"):
        GuardedDeliveryClient(settings).deliver(item)


@pytest.mark.parametrize(
    "corruption", ["missing_field", "wrong_id", "wrong_canonical", "malformed"]
)
def test_invalid_readback_cannot_acknowledge_a_timed_out_update(
    session: Session,
    settings: Settings,
    corruption: str,
) -> None:
    _, item = seeded_item(session, settings)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "PATCH":
            raise httpx.ReadTimeout("lost response", request=request)
        record = remote_record(item)
        if corruption == "malformed":
            return httpx.Response(200, text="not JSON", request=request)
        if corruption == "wrong_id":
            record["id"] = "another-record"
        else:
            props = dict(item.payload["properties"])
            if corruption == "missing_field":
                props.pop("name")
            else:
                props["gtm_canonical_id"] = "another-canonical-account"
            record["properties"] = props
        return httpx.Response(200, json=record, request=request)

    with pytest.raises(DeliveryOutcomeUnknown):
        GuardedDeliveryClient(
            live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
        ).deliver(item)
    assert calls == ["PATCH", "GET"]
    assert item.status == "outcome_unknown"


def test_normal_create_ack_is_durable_and_cannot_repeat(
    session: Session,
    settings: Settings,
) -> None:
    _, item = seeded_item(session, settings)
    item.operation, item.target_external_id = "POST", None
    session.commit()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(201, json={"id": "created-456"}, request=request)

    client = GuardedDeliveryClient(
        live_settings(settings), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert client.deliver(item)["status_code"] == 201
    with Session(session.get_bind()) as restarted:
        saved = restarted.get(OutboxItem, item.id)
        assert saved is not None and saved.status == "delivered"
        assert saved.target_external_id == "created-456"
        assert client.deliver(saved)["outcome"] == "already_acknowledged"
    assert calls == ["POST"]


def test_detached_outbox_is_rejected_before_network(session: Session, settings: Settings) -> None:
    _, item = seeded_item(session, settings)
    session.expunge(item)
    with pytest.raises(RuntimeError, match="dedicated session"):
        GuardedDeliveryClient(live_settings(settings)).deliver(item)


def test_readback_normalization_is_narrow() -> None:
    assert _same_value("true", True)
    assert _same_value("12", 12)
    assert _same_value(None, None)
    assert not _same_value(True, 1)
    assert not _same_value("1", True)
    assert not _same_value("", None)
