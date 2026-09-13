"""Offline hiring walkthrough exercising the real control-plane code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from revops_sync.config import Settings
from revops_sync.db import Base, build_engine, build_session_factory
from revops_sync.gateways import GuardedDeliveryClient, OutboxOrderingBlocked
from revops_sync.models import CanonicalAccount, ConflictRecord, OrderingResolution, OutboxItem
from revops_sync.ordering import release_ordering_hold
from revops_sync.reconcile import ReconciliationService, SourceVersionConflict
from revops_sync.schemas import ReconcileRequest, SourceAccount

BOUNDARIES = [
    "All accounts, CRM IDs, CRM responses and operator attestations are synthetic.",
    "No network transport, live CRM credentials, paid APIs or customer data are used.",
    "This is serial SQLite execution, not PostgreSQL/concurrent-ingestion evidence.",
    "No signed webhook receiver, external-edit conditional writes or exactly-once guarantee.",
    "Matching read-back is not provider-settlement proof; the operator release is simulated.",
    "No pipeline, revenue, reply-rate, seller adoption or time-saved claim is made.",
]


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"Demo verification failed: {message}")


def _source(
    provider: str, external_id: str, domain: str, name: str, version: int, consent: bool
) -> SourceAccount:
    return SourceAccount.model_validate(
        {
            "provider": provider,
            "external_id": external_id,
            "domain": domain,
            "name": name,
            "marketing_opt_in": consent,
            "source_updated_at": f"2026-09-{version:02d}T12:00:00Z",
        }
    )


def run_demo() -> dict[str, Any]:
    """Use a fresh owned temporary database and an exclusively mocked transport."""
    scenarios: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    calls: list[dict[str, str]] = []
    remote: dict[str, Any] = {}
    with TemporaryDirectory(prefix="revops-hiring-demo-") as temporary:
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{Path(temporary) / 'demo.sqlite3'}",
            app_env="test",
            live_integrations_enabled=True,
            hubspot_live_enabled=False,
            salesforce_live_enabled=True,
            hubspot_access_token=None,
            salesforce_access_token=SecretStr("synthetic-demo-token-not-a-credential"),
            salesforce_instance_url="https://crm.invalid",
            salesforce_api_version="v65.0",
            workflow_api_key=None,
            connector_max_retries=0,
        )
        engine = build_engine(settings.database_url)
        Base.metadata.create_all(engine)
        factory = build_session_factory(engine)

        def transport(request: httpx.Request) -> httpx.Response:
            _check(request.url.host == "crm.invalid", "only the fake CRM host is permitted")
            calls.append({"method": request.method, "path": request.url.path})
            if request.method == "POST" and request.url.path.endswith("/sobjects/Account"):
                _check(not remote, "a second simulated create must never be issued")
                remote.update(json.loads(request.content))
                remote["Id"] = "synthetic-crm-account-001"
                raise httpx.ReadTimeout(
                    "simulated accepted create with lost response", request=request
                )
            if request.method == "GET" and request.url.path.endswith("/query"):
                return httpx.Response(200, json={"totalSize": 1, "records": [dict(remote)]})
            if request.method == "PATCH" and request.url.path.endswith(
                "/synthetic-crm-account-001"
            ):
                remote.update(json.loads(request.content))
                return httpx.Response(204)
            raise RuntimeError("Unexpected simulated CRM operation")

        try:
            with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as http:
                delivery = GuardedDeliveryClient(settings, client=http)
                with factory() as session:
                    service = ReconciliationService(session, settings)

                    def ingest(*records: SourceAccount) -> Any:
                        inputs.extend(r.model_dump(mode="json") for r in records)
                        return service.run(
                            ReconcileRequest(source_mode="inline", records=list(records))
                        )

                    hs = _source(
                        "hubspot",
                        "synthetic-hs-001",
                        "conflict.example",
                        "HubSpot account name",
                        10,
                        True,
                    )
                    sf = _source(
                        "salesforce",
                        "synthetic-sf-001",
                        "conflict.example",
                        "Salesforce account name",
                        10,
                        False,
                    )
                    initial = ingest(hs, sf)
                    account = session.scalar(
                        select(CanonicalAccount).where(
                            CanonicalAccount.domain == "conflict.example"
                        )
                    )
                    _check(account is not None, "exact domains converge on one account")
                    assert account is not None  # narrow the type; not the runtime verification
                    _check(
                        account.name == sf.name and account.marketing_opt_in is False,
                        "Salesforce owns name and restrictive consent wins",
                    )
                    conflicts = list(
                        session.scalars(
                            select(ConflictRecord).where(
                                ConflictRecord.canonical_account_id == account.id
                            )
                        )
                    )
                    _check(len(conflicts) == 2, "both disagreements have conflict evidence")
                    scenarios.append(
                        {
                            "title": "1. Conflicting CRM observations",
                            "passed": True,
                            "business_risk": "Unclear ownership and accidental opt-in.",
                            "observed": {
                                "canonical_name": account.name,
                                "marketing_opt_in": account.marketing_opt_in,
                                "source_rows_inserted": initial.inserted_records,
                                "conflicts": [
                                    {
                                        "field": c.field_name,
                                        "hubspot": c.hubspot_value,
                                        "salesforce": c.salesforce_value,
                                        "chosen": c.chosen_value,
                                        "policy": c.policy,
                                    }
                                    for c in sorted(conflicts, key=lambda c: c.field_name)
                                ],
                            },
                        }
                    )
                    before_accounts = len(list(session.scalars(select(CanonicalAccount))))
                    before_outbox = len(list(session.scalars(select(OutboxItem))))
                    duplicate = ingest(hs, sf)
                    stale = ingest(
                        _source(
                            "salesforce",
                            "synthetic-sf-001",
                            "stale-domain.example",
                            "Stale account name",
                            9,
                            True,
                        )
                    )
                    _check(
                        duplicate.unchanged_records == 2 and duplicate.outbox_previews_created == 0,
                        "duplicates stage no additional preview",
                    )
                    _check(
                        stale.stale_records == 1 and stale.accounts_reconciled == 0,
                        "stale input is stopped before identity resolution",
                    )
                    try:
                        ingest(
                            _source(
                                "salesforce",
                                "synthetic-sf-001",
                                "conflict.example",
                                "Equal-version conflicting name",
                                10,
                                False,
                            )
                        )
                    except SourceVersionConflict:
                        conflict_rejected = True
                    else:
                        conflict_rejected = False
                    session.refresh(account)
                    _check(
                        conflict_rejected
                        and account.name == sf.name
                        and account.marketing_opt_in is False,
                        "equal-version conflict rolls back without restoring consent",
                    )
                    _check(
                        len(list(session.scalars(select(CanonicalAccount)))) == before_accounts
                        and len(list(session.scalars(select(OutboxItem)))) == before_outbox,
                        "duplicate/stale/conflicting input creates no extra account or preview",
                    )
                    scenarios.append(
                        {
                            "title": "2. Duplicate, stale and conflicting input",
                            "passed": True,
                            "business_risk": "Duplicate actions and stale identity/consent.",
                            "observed": {
                                "duplicates_unchanged": duplicate.unchanged_records,
                                "stale_records_ignored": stale.stale_records,
                                "additional_previews": 0,
                                "equal_version_conflict_rejected": conflict_rejected,
                                "canonical_name_unchanged": account.name,
                                "marketing_opt_in": account.marketing_opt_in,
                            },
                        }
                    )

                    # A second synthetic account exists only in HubSpot. Two
                    # updates queue Salesforce POST intents before the first lands.
                    first_source = _source(
                        "hubspot",
                        "synthetic-hs-new",
                        "new-account.example",
                        "New account v1",
                        10,
                        False,
                    )
                    ingest(first_source)
                    new_account = session.scalar(
                        select(CanonicalAccount).where(
                            CanonicalAccount.domain == "new-account.example"
                        )
                    )
                    _check(new_account is not None, "new account was staged")
                    assert new_account is not None
                    ingest(
                        _source(
                            "hubspot",
                            "synthetic-hs-new",
                            "new-account.example",
                            "New account v2",
                            11,
                            False,
                        )
                    )
                    queue = list(
                        session.scalars(
                            select(OutboxItem)
                            .where(
                                OutboxItem.canonical_account_id == new_account.id,
                                OutboxItem.target_provider == "salesforce",
                            )
                            .order_by(OutboxItem.sequence)
                        )
                    )
                    _check(
                        len(queue) == 2 and all(q.operation == "POST" for q in queue),
                        "two undelivered creates are queued",
                    )
                    first, newer = queue
                    first_id, newer_id = first.id, newer.id
                    original_intent = (
                        newer.operation,
                        newer.payload_checksum,
                        newer.idempotency_key,
                    )
                    recovered = delivery.deliver(first)
                    _check(
                        recovered["outcome"] == "desired_state_observed" and first.ordering_hold,
                        "matching read-back acknowledges observation but retains settlement hold",
                    )
                    _check(
                        [c["method"] for c in calls] == ["POST", "GET"],
                        "lost create response is followed by read-back, not another create",
                    )
                    scenarios.append(
                        {
                            "title": "3. Accepted create, lost response, read-back recovery",
                            "passed": True,
                            "business_risk": "Blind retry could create a second account.",
                            "observed": {
                                "persisted_status": first.status,
                                "write_attempts": first.attempts,
                                "synthetic_external_id": first.target_external_id,
                                "ordering_hold": first.ordering_hold,
                                "simulated_http": list(calls),
                            },
                        }
                    )

                # Reopen a database session to inspect durable acknowledgement/hold.
                with factory() as restarted:
                    saved_first = restarted.get(OutboxItem, first_id)
                    saved_newer = restarted.get(OutboxItem, newer_id)
                    _check(
                        saved_first is not None and saved_newer is not None,
                        "outbox persisted across session restart",
                    )
                    assert saved_first is not None and saved_newer is not None
                    first, newer = saved_first, saved_newer
                    before_calls = len(calls)
                    _check(
                        delivery.deliver(first)["outcome"] == "already_acknowledged",
                        "acknowledged replay does not send another request",
                    )
                    try:
                        delivery.deliver(newer)
                    except OutboxOrderingBlocked:
                        blocked = True
                    else:
                        blocked = False
                    _check(
                        blocked and len(calls) == before_calls and newer.attempts == 0,
                        "newer item waits with zero HTTP calls",
                    )
                    scenarios.append(
                        {
                            "title": "4. Newer update waits after restart",
                            "passed": True,
                            "business_risk": "A late older write could overwrite newer values.",
                            "observed": {
                                "persisted_older_hold": first.ordering_hold,
                                "newer_blocked": blocked,
                                "additional_simulated_http": len(calls) - before_calls,
                                "newer_write_attempts": newer.attempts,
                            },
                        }
                    )
                    # Mock handler completed synchronously: the fake operation is
                    # settled. This attestation is explicitly NOT live provider proof.
                    resolution_id = release_ordering_hold(
                        restarted,
                        first.id,
                        reviewer="synthetic-demo-operator",
                        reason=(
                            "SIMULATED: mock handler completed; "
                            "no remote operation remains in flight."
                        ),
                        provider_settled=True,
                    )
                    audit = restarted.get(OrderingResolution, resolution_id)
                    _check(audit is not None, "hold release has a persisted audit")
                    assert audit is not None
                    delivery.deliver(newer)
                    _check(
                        newer.dispatch_operation == "PATCH"
                        and newer.dispatch_external_id == "synthetic-crm-account-001"
                        and newer.binding_source_id == first.id,
                        "newer intent targets the confirmed create",
                    )
                    _check(
                        (newer.operation, newer.payload_checksum, newer.idempotency_key)
                        == original_intent,
                        "staged intent and identifiers are preserved",
                    )
                    _check(
                        newer.status == "delivered" and remote.get("Name") == "New account v2",
                        "simulated CRM now contains the newer value",
                    )
                    _check(
                        [c["method"] for c in calls] == ["POST", "GET", "PATCH"],
                        "the complete walkthrough has one create, one read-back and one update",
                    )
                    scenarios.append(
                        {
                            "title": "5. Audited operator recovery and bound update",
                            "passed": True,
                            "business_risk": "Unaudited bypass or a duplicate create.",
                            "observed": {
                                "simulated_reviewer": audit.reviewer,
                                "simulated_reason": audit.reason,
                                "audit_action": audit.action,
                                "older_hold_released": not first.ordering_hold,
                                "staged_operation": newer.operation,
                                "dispatch_operation": newer.dispatch_operation,
                                "synthetic_dispatch_id": newer.dispatch_external_id,
                                "binding_source_matches_older_item": newer.binding_source_id
                                == first.id,
                                "original_intent_preserved": True,
                                "persisted_status": newer.status,
                                "simulated_crm_name": remote["Name"],
                            },
                        }
                    )
        finally:
            engine.dispose()
    return {
        "schema_version": 1,
        "data_classification": "synthetic",
        "transport": "httpx.MockTransport; no network transport",
        "database": "fresh temporary file-backed SQLite; removed after run",
        "all_checks_passed": all(s["passed"] for s in scenarios),
        "real_external_writes": 0,
        "simulated_http_calls": calls,
        "sample_inputs": inputs,
        "scenarios": scenarios,
        "boundaries": BOUNDARIES,
    }


def render_walkthrough(receipt: dict[str, Any]) -> str:
    lines = [
        "# Five-minute CRM control-plane demo",
        "",
        "**Synthetic accounts and simulated CRM responses. "
        "No live CRM writes or revenue results.**",
        "",
        "Run `python -m revops_sync.demo` after installing the project dependencies.",
        "Use `python -m revops_sync.demo --json` for the machine-readable receipt.",
        "",
    ]
    for scenario in receipt["scenarios"]:
        lines.extend(
            [
                f"## {scenario['title']}",
                "",
                scenario["business_risk"],
                "",
                "Verified by execution:",
                "",
                "```json",
                json.dumps(scenario["observed"], indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Complete simulated request trace",
            "",
            "```json",
            json.dumps(receipt["simulated_http_calls"], indent=2),
            "```",
            "",
            "Five scenario checks passed. Real external writes: **0**.",
            "",
            "## Sample inputs",
            "",
            "```json",
            json.dumps(receipt["sample_inputs"], indent=2),
            "```",
            "",
            "## What this does not prove",
            "",
        ]
    )
    lines.extend(f"- {boundary}" for boundary in receipt["boundaries"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline, verified CRM hiring walkthrough")
    parser.add_argument("--json", action="store_true", help="Emit the machine-readable receipt")
    args = parser.parse_args()
    receipt = run_demo()
    print(
        json.dumps(receipt, indent=2, sort_keys=True) if args.json else render_walkthrough(receipt),
        end="\n" if args.json else "",
    )


if __name__ == "__main__":
    main()
