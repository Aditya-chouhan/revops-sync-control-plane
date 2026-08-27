"""Run the real reconciliation output against a real HubSpot developer test portal.

This is deliberately a standalone script, not a change to the FastAPI service or
GuardedDeliveryClient: the app's outbox items carry fixture-relative operation/
target_external_id fields (e.g. "PATCH hs-1001") that only make sense once a
prior sync has actually run against a *specific* portal. Against a freshly
empty dev portal there is no hs-1001 to PATCH, so this script re-derives
create-vs-update from the portal's own live state instead -- by searching for
each canonical account's own gtm_canonical_id, the exact stamp the app writes
into every payload so a re-ingested record can be re-identified (see
identity.canonical_identity's canonical_id_hint mechanism). Idempotency here
uses the same idea end to end: on rerun, the search finds the object this
script itself created and updates it instead of creating a duplicate.

Never commit a real access token. Read it from HUBSPOT_ACCESS_TOKEN.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from revops_sync.config import Settings
from revops_sync.db import Base, build_engine, build_session_factory
from revops_sync.models import CanonicalAccount, OutboxItem
from revops_sync.reconcile import ReconciliationService, _target_payload
from revops_sync.schemas import ReconcileRequest

HUBSPOT_BASE = "https://api.hubapi.com"

CUSTOM_PROPERTIES: list[dict[str, Any]] = [
    {
        "name": "gtm_owner_email",
        "label": "GTM Owner Email",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Canonical account owner, resolved by revops-sync-control-plane.",
    },
    {
        "name": "gtm_marketing_opt_in",
        "label": "GTM Marketing Opt-In",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "groupName": "companyinformation",
        "description": "Fail-safe consent resolution: an opt-out from either CRM always wins.",
        # HubSpot requires a boolean property to declare its two options
        # explicitly -- there is no implicit true/false for this fieldType.
        "options": [
            {"label": "True", "value": "true", "hidden": False, "displayOrder": 0},
            {"label": "False", "value": "false", "hidden": False, "displayOrder": 1},
        ],
    },
    {
        "name": "gtm_canonical_id",
        "label": "GTM Canonical ID",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": (
            "Stable id stamped by revops-sync-control-plane for cross-run re-identification."
        ),
    },
]


def ensure_properties(client: httpx.Client) -> list[dict[str, Any]]:
    created = []
    for prop in CUSTOM_PROPERTIES:
        existing = client.get(f"/crm/v3/properties/companies/{prop['name']}")
        if existing.status_code == 200:
            created.append(
                {"property": prop["name"], "action": "already_existed", "status_code": 200}
            )
            continue
        response = client.post("/crm/v3/properties/companies", json=prop)
        created.append(
            {
                "property": prop["name"],
                "action": "created",
                "status_code": response.status_code,
                "response": response.json(),
            }
        )
        response.raise_for_status()
    return created


def find_by_canonical_id(client: httpx.Client, canonical_id: str) -> str | None:
    response = client.post(
        "/crm/v3/objects/companies/search",
        json={
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "gtm_canonical_id",
                            "operator": "EQ",
                            "value": canonical_id,
                        }
                    ]
                }
            ],
            "properties": ["gtm_canonical_id"],
            "limit": 1,
        },
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0]["id"] if results else None


# HubSpot's native `industry` company property is a closed enumeration of
# ~140 fixed internal tokens, not free text. A live sync attempt is what
# surfaced this: revops-sync-control-plane's canonical `industry` field is
# whatever string field-ownership policy chose from the source CRM records
# (e.g. "Software", "Analytics"), and HubSpot 400s the entire company create
# if that string doesn't exactly match one of its tokens. Only exact,
# unambiguous matches are mapped here -- guessing a specific token for e.g.
# "Manufacturing" (no single generic match exists in HubSpot's enum) would be
# fabricating a fact this repo has no basis for, so those are dropped from
# the HubSpot payload instead, and the drop is recorded in the evidence
# receipt rather than silently discarded.
HUBSPOT_INDUSTRY_MAP: dict[str, str] = {
    "software": "COMPUTER_SOFTWARE",
}


def sync_account(
    client: httpx.Client, account: CanonicalAccount, payload: dict[str, Any]
) -> dict[str, Any]:
    properties = {k: v for k, v in payload["properties"].items() if v is not None}
    industry_dropped = None
    raw_industry = properties.get("industry")
    if raw_industry is not None:
        mapped = HUBSPOT_INDUSTRY_MAP.get(raw_industry.lower())
        if mapped:
            properties["industry"] = mapped
        else:
            industry_dropped = raw_industry
            del properties["industry"]
    existing_id = find_by_canonical_id(client, account.id)
    if existing_id:
        response = client.patch(
            f"/crm/v3/objects/companies/{existing_id}", json={"properties": properties}
        )
        operation = "PATCH"
    else:
        response = client.post("/crm/v3/objects/companies", json={"properties": properties})
        operation = "POST"
    response.raise_for_status()
    body = response.json()
    return {
        "canonical_account_id": account.id,
        "domain": account.domain,
        "resolution_basis": account.resolution_basis,
        "operation": operation,
        "hubspot_object_id": body["id"],
        "status_code": response.status_code,
        "properties_sent": properties,
        "industry_dropped_no_hubspot_enum_match": industry_dropped,
        "hubspot_response_properties": body.get("properties", {}),
    }


def main() -> None:
    token = os.environ.get("HUBSPOT_ACCESS_TOKEN")
    if not token:
        print("HUBSPOT_ACCESS_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    db_path = "/tmp/hubspot_live_sync.sqlite3"
    Path(db_path).unlink(missing_ok=True)
    settings = Settings(
        app_env="local", database_url=f"sqlite:///{db_path}", auto_create_schema=True
    )
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)

    with factory() as session:
        reconcile_result = ReconciliationService(session, settings).run(ReconcileRequest())
        hubspot_items = list(
            session.scalars(select(OutboxItem).where(OutboxItem.target_provider == "hubspot"))
        )
        accounts: dict[str, CanonicalAccount] = {}
        for item in hubspot_items:
            account = session.get(CanonicalAccount, item.canonical_account_id)
            if account is None:
                raise RuntimeError(f"canonical account {item.canonical_account_id} disappeared")
            accounts[item.canonical_account_id] = account

    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=HUBSPOT_BASE, headers=headers, timeout=30) as client:
        property_setup = ensure_properties(client)
        synced = []
        for item in hubspot_items:
            account = accounts[item.canonical_account_id]
            payload = _target_payload("hubspot", account)
            synced.append(sync_account(client, account, payload))
            time.sleep(0.3)  # stay well under HubSpot's rate limit

    receipt = {
        "data_classification": "real_hubspot_dev_portal_sync",
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "portal": "HubSpot developer test account (free, no card)",
        "reconciliation": reconcile_result.model_dump(mode="json"),
        "property_setup": property_setup,
        "companies_synced": synced,
        "claim_boundary": (
            "Every object id and response body below is real, returned by HubSpot's live API "
            "against a free developer test portal. The source data is the same synthetic, "
            "declared-fictional CRM fixture used throughout this repo -- these are not real "
            "companies, and no production HubSpot portal was touched."
        ),
    }
    out_path = Path("evidence") / f"hubspot_live_sync_{time.strftime('%Y-%m-%d')}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
