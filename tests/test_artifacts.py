from __future__ import annotations

import json
from pathlib import Path

from revops_sync.identity import normalize_domain


def test_fixture_is_explicitly_synthetic() -> None:
    data = json.loads(Path("data/synthetic/crm_accounts.json").read_text())
    assert data["data_classification"] == "synthetic"
    assert "not customer" in data["claim_boundary"].lower()
    normalized = [normalize_domain(item["domain"]) for item in data["records"] if item["domain"]]
    assert all(domain and domain.endswith(".example") for domain in normalized)
    assert all(
        not value or value.endswith("@example.invalid")
        for item in data["records"]
        for value in [item["owner_email"]]
    )


def test_public_api_has_no_delivery_route() -> None:
    source = Path("src/revops_sync/api.py").read_text()
    assert 'post("/v1/integrations' not in source
    assert ".deliver(" not in source


def test_cloud_pack_is_not_misrepresented_as_deployed() -> None:
    status = Path("docs/BIGQUERY_DEPLOYMENT.md").read_text().lower()
    assert "prepared, not deployed" in status
    assert "has not been applied" in status
