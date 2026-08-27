from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from revops_sync.config import Settings
from revops_sync.identity import canonical_identity, source_record_id
from revops_sync.models import (
    CanonicalAccount,
    ConflictRecord,
    OutboxItem,
    SourceRecord,
    SyncRun,
)
from revops_sync.schemas import ReconcileRequest, ReconcileResult, SourceAccount

FIELD_POLICIES: dict[str, tuple[str, str]] = {
    "name": ("salesforce", "salesforce_preferred"),
    "industry": ("salesforce", "salesforce_preferred"),
    "employee_count": ("salesforce", "salesforce_preferred"),
    "owner_email": ("salesforce", "salesforce_preferred"),
    "lifecycle_stage": ("hubspot", "hubspot_preferred"),
    "marketing_opt_in": ("hubspot", "hubspot_preferred"),
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def load_fixture(path: str) -> list[SourceAccount]:
    raw = json.loads(Path(path).read_text())
    if raw.get("data_classification") != "synthetic":
        raise ValueError("Committed CRM fixture must declare data_classification=synthetic")
    return [SourceAccount.model_validate(item) for item in raw["records"]]


def _latest_by_provider(records: list[SourceRecord]) -> dict[str, SourceRecord]:
    latest: dict[str, SourceRecord] = {}
    for record in records:
        current = latest.get(record.provider)
        if current is None or record.source_updated_at > current.source_updated_at:
            latest[record.provider] = record
    return latest


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _choose_value(
    field_name: str, records: dict[str, SourceRecord]
) -> tuple[Any, str, str]:
    preferred_source, policy = FIELD_POLICIES[field_name]
    preferred = records.get(preferred_source)
    if preferred and _present(preferred.payload.get(field_name)):
        return preferred.payload[field_name], preferred_source, policy
    candidates = [
        record for record in records.values() if _present(record.payload.get(field_name))
    ]
    if not candidates:
        return None, "none", policy
    newest = max(candidates, key=lambda item: item.source_updated_at)
    return newest.payload[field_name], newest.provider, f"{policy}_fallback_latest_non_null"


def _target_payload(provider: str, account: CanonicalAccount) -> dict[str, Any]:
    if provider == "hubspot":
        return {
            "properties": {
                "domain": account.domain,
                "name": account.name,
                "industry": account.industry,
                "numberofemployees": account.employee_count,
                "gtm_owner_email": account.owner_email,
                "lifecyclestage": account.lifecycle_stage,
                "gtm_marketing_opt_in": account.marketing_opt_in,
                "gtm_canonical_id": account.id,
            }
        }
    return {
        "Name": account.name,
        "Website": f"https://{account.domain}" if account.domain else None,
        "Industry": account.industry,
        "NumberOfEmployees": account.employee_count,
        "Owner_Email__c": account.owner_email,
        "Lifecycle_Stage__c": account.lifecycle_stage,
        "Marketing_Opt_In__c": account.marketing_opt_in,
        "GTM_Canonical_ID__c": account.id,
    }


class ReconciliationService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def run(self, request: ReconcileRequest) -> ReconcileResult:
        records = (
            load_fixture(self.settings.fixture_path)
            if request.source_mode == "fixture"
            else request.records
        )
        inserted = updated = unchanged = 0
        touched_ids: set[str] = set()

        for record in records:
            account_id, basis, domain = canonical_identity(record)
            touched_ids.add(account_id)
            account = self.session.get(CanonicalAccount, account_id)
            if account is None:
                account = CanonicalAccount(
                    id=account_id,
                    domain=domain,
                    name=record.name,
                    industry=record.industry,
                    employee_count=record.employee_count,
                    owner_email=record.owner_email,
                    lifecycle_stage=record.lifecycle_stage,
                    marketing_opt_in=record.marketing_opt_in,
                    resolution_basis=basis,
                )
                self.session.add(account)
                self.session.flush()

            payload = record.model_dump(mode="json")
            checksum = _checksum(payload)
            row_id = source_record_id(record)
            existing = self.session.get(SourceRecord, row_id)
            if existing is None:
                self.session.add(
                    SourceRecord(
                        id=row_id,
                        provider=record.provider,
                        external_id=record.external_id,
                        canonical_account_id=account_id,
                        source_updated_at=record.source_updated_at,
                        checksum=checksum,
                        payload=payload,
                    )
                )
                inserted += 1
            elif existing.checksum == checksum and existing.canonical_account_id == account_id:
                unchanged += 1
            else:
                existing.canonical_account_id = account_id
                existing.source_updated_at = record.source_updated_at
                existing.checksum = checksum
                existing.payload = payload
                updated += 1

        self.session.flush()
        conflicts_created = 0
        outbox_created = 0
        for account_id in touched_ids:
            conflict_count, outbox_count = self._reconcile_account(account_id)
            conflicts_created += conflict_count
            outbox_created += outbox_count

        run_id = str(uuid.uuid4())
        result = ReconcileResult(
            run_id=run_id,
            source_mode=request.source_mode,
            data_classification=(
                "synthetic" if request.source_mode == "fixture" else "caller_supplied"
            ),
            input_records=len(records),
            inserted_records=inserted,
            updated_records=updated,
            unchanged_records=unchanged,
            accounts_reconciled=len(touched_ids),
            conflicts_recorded=conflicts_created,
            outbox_previews_created=outbox_created,
            external_writes=0,
        )
        self.session.add(
            SyncRun(
                id=run_id,
                source_mode=request.source_mode,
                input_records=len(records),
                inserted_records=inserted,
                updated_records=updated,
                unchanged_records=unchanged,
                accounts_reconciled=len(touched_ids),
                conflicts_recorded=conflicts_created,
                outbox_previews_created=outbox_created,
                receipt=result.model_dump(mode="json"),
            )
        )
        self.session.commit()
        return result

    def _reconcile_account(self, account_id: str) -> tuple[int, int]:
        account = self.session.get(CanonicalAccount, account_id)
        if account is None:
            raise RuntimeError("canonical account disappeared during reconciliation")
        records = list(
            self.session.scalars(
                select(SourceRecord).where(SourceRecord.canonical_account_id == account_id)
            )
        )
        latest = _latest_by_provider(records)
        conflicts_created = 0

        for field_name in FIELD_POLICIES:
            chosen, chosen_source, applied_policy = _choose_value(field_name, latest)
            setattr(account, field_name, chosen)
            hubspot_value = latest.get("hubspot")
            salesforce_value = latest.get("salesforce")
            hs_value = hubspot_value.payload.get(field_name) if hubspot_value else None
            sf_value = salesforce_value.payload.get(field_name) if salesforce_value else None
            if _present(hs_value) and _present(sf_value) and hs_value != sf_value:
                fingerprint = _checksum(
                    [account_id, field_name, hs_value, sf_value, chosen, applied_policy]
                )
                if self.session.get(ConflictRecord, fingerprint) is None:
                    self.session.add(
                        ConflictRecord(
                            id=fingerprint,
                            canonical_account_id=account_id,
                            field_name=field_name,
                            hubspot_value=hs_value,
                            salesforce_value=sf_value,
                            chosen_value=chosen,
                            chosen_source=chosen_source,
                            policy=applied_policy,
                        )
                    )
                    conflicts_created += 1

        account.updated_at = datetime.now(UTC)
        self.session.flush()
        outbox_created = self._create_outbox_previews(account, latest)
        return conflicts_created, outbox_created

    def _create_outbox_previews(
        self, account: CanonicalAccount, records: dict[str, SourceRecord]
    ) -> int:
        created = 0
        by_provider: dict[str, list[SourceRecord]] = defaultdict(list)
        for row in records.values():
            by_provider[row.provider].append(row)
        for provider in ("hubspot", "salesforce"):
            payload = _target_payload(provider, account)
            target = max(
                by_provider.get(provider, []),
                key=lambda item: item.source_updated_at,
                default=None,
            )
            external_id = target.external_id if target else None
            operation = "PATCH" if external_id else "POST"
            idempotency_key = _checksum([provider, account.id, operation, payload])
            item_id = idempotency_key
            if self.session.get(OutboxItem, item_id) is not None:
                continue
            self.session.add(
                OutboxItem(
                    id=item_id,
                    canonical_account_id=account.id,
                    target_provider=provider,
                    target_external_id=external_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    payload=payload,
                    status="preview_only",
                )
            )
            created += 1
        return created
