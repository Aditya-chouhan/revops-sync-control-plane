from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from revops_sync.config import Settings
from revops_sync.main import create_app


def test_api_reconcile_read_and_preview(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").status_code == 200
        run = client.post("/v1/reconcile", json={"source_mode": "fixture"})
        assert run.status_code == 200, run.text
        assert run.json()["external_writes"] == 0

        accounts = client.get("/v1/accounts").json()
        assert len(accounts) == 4
        acme = next(item for item in accounts if item["domain"] == "acme-labs.example")
        detail = client.get(f"/v1/accounts/{acme['id']}")
        assert detail.status_code == 200
        assert len(detail.json()["source_records"]) == 2

        preview = client.get(f"/v1/integrations/hubspot/accounts/{acme['id']}/preview")
        assert preview.status_code == 200
        assert preview.json()["would_write"] is False
        assert preview.json()["credential_present"] is False
        assert len(client.get("/v1/outbox").json()) == 8
        assert client.get("/metrics").status_code == 200


def test_reconcile_endpoint_can_require_api_key(settings: Settings) -> None:
    protected = settings.model_copy(update={"workflow_api_key": SecretStr("sync-secret")})
    with TestClient(create_app(protected)) as client:
        assert client.post("/v1/reconcile", json={"source_mode": "fixture"}).status_code == 401
        assert client.get("/v1/accounts").status_code == 401
        response = client.post(
            "/v1/reconcile",
            json={"source_mode": "fixture"},
            headers={"x-api-key": "sync-secret"},
        )
        assert response.status_code == 200
        assert client.get("/v1/accounts", headers={"x-api-key": "sync-secret"}).status_code == 200


def test_inline_mode_requires_records(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/reconcile", json={"source_mode": "inline", "records": []})
        assert response.status_code == 422
