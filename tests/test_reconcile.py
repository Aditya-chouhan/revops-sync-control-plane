from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revops_sync.config import Settings
from revops_sync.models import CanonicalAccount, ConflictRecord, OutboxItem, SourceRecord
from revops_sync.reconcile import ReconciliationService
from revops_sync.schemas import ReconcileRequest


def test_fixture_reconciles_with_explicit_field_ownership(
    session: Session, settings: Settings
) -> None:
    result = ReconciliationService(session, settings).run(ReconcileRequest())
    assert result.input_records == 6
    assert result.inserted_records == 6
    assert result.accounts_reconciled == 4
    assert result.conflicts_recorded == 8
    assert result.outbox_previews_created == 8
    assert result.external_writes == 0

    acme = session.scalar(
        select(CanonicalAccount).where(CanonicalAccount.domain == "acme-labs.example")
    )
    assert acme is not None
    assert acme.name == "Acme Labs, Inc."
    assert acme.industry == "Software"
    assert acme.employee_count == 135
    assert acme.owner_email == "account-executive@example.invalid"
    assert acme.lifecycle_stage == "marketingqualifiedlead"
    assert acme.marketing_opt_in is True


def test_missing_domains_are_not_fuzzy_merged(session: Session, settings: Settings) -> None:
    ReconciliationService(session, settings).run(ReconcileRequest())
    northstars = list(
        session.scalars(
            select(CanonicalAccount).where(CanonicalAccount.name == "Northstar Labs")
        )
    )
    assert len(northstars) == 2
    assert all(item.domain is None for item in northstars)
    assert all(item.resolution_basis == "provider_record_no_domain" for item in northstars)


def test_reconciliation_is_idempotent(session: Session, settings: Settings) -> None:
    service = ReconciliationService(session, settings)
    first = service.run(ReconcileRequest())
    second = service.run(ReconcileRequest())
    assert first.inserted_records == 6
    assert second.unchanged_records == 6
    assert second.conflicts_recorded == 0
    assert second.outbox_previews_created == 0
    assert session.scalar(select(func.count()).select_from(SourceRecord)) == 6
    assert session.scalar(select(func.count()).select_from(ConflictRecord)) == 8
    assert session.scalar(select(func.count()).select_from(OutboxItem)) == 8


def test_conflict_ledger_records_both_values_and_policy(
    session: Session, settings: Settings
) -> None:
    ReconciliationService(session, settings).run(ReconcileRequest())
    conflict = next(
        (
            item
            for item in session.scalars(
                select(ConflictRecord).where(ConflictRecord.field_name == "lifecycle_stage")
            )
            if item.hubspot_value == "marketingqualifiedlead"
            and item.salesforce_value == "prospect"
        ),
        None,
    )
    assert conflict is not None
    assert conflict.chosen_source == "hubspot"
    assert conflict.chosen_value == "marketingqualifiedlead"
    assert conflict.policy == "hubspot_preferred"
