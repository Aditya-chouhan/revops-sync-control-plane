from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revops_sync.config import Settings
from revops_sync.models import CanonicalAccount, ConflictRecord, OutboxItem, SourceRecord
from revops_sync.reconcile import ReconciliationService
from revops_sync.schemas import ReconcileRequest, SourceAccount


def test_fixture_reconciles_with_explicit_field_ownership(
    session: Session, settings: Settings
) -> None:
    result = ReconciliationService(session, settings).run(ReconcileRequest())
    assert result.input_records == 6
    assert result.inserted_records == 6
    assert result.accounts_reconciled == 4
    assert result.conflicts_recorded == 8
    # Not 8: the two no-domain Northstar accounts no longer stage a phantom
    # cross-provider create (see test_no_domain_accounts_do_not_stage_...).
    assert result.outbox_previews_created == 6
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
    # HubSpot opted in (true, 2026-08-20); Salesforce opted out (false,
    # 2026-08-21, the newer record). Consent fails safe: the restrictive
    # value wins over both field-ownership policy and recency.
    assert acme.marketing_opt_in is False


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
    assert session.scalar(select(func.count()).select_from(OutboxItem)) == 6


def test_no_domain_accounts_do_not_stage_cross_provider_creates(
    session: Session, settings: Settings
) -> None:
    """S1: without a domain (or a confirmed canonical_id echo) there is no
    stable cross-provider identity anchor, so a POST into a provider that has
    never seen the account must not be staged — that POST is exactly what
    turns into an unbounded duplicate-creation loop once it is delivered and
    echoed back with no domain of its own."""
    ReconciliationService(session, settings).run(ReconcileRequest())

    isolated = list(
        session.scalars(
            select(CanonicalAccount).where(
                CanonicalAccount.resolution_basis == "provider_record_no_domain"
            )
        )
    )
    assert len(isolated) == 2  # the two "Northstar Labs" records, one per provider

    for account in isolated:
        source_providers = {
            row.provider
            for row in session.scalars(
                select(SourceRecord).where(SourceRecord.canonical_account_id == account.id)
            )
        }
        outbox_providers = {
            item.target_provider
            for item in session.scalars(
                select(OutboxItem).where(OutboxItem.canonical_account_id == account.id)
            )
        }
        assert len(source_providers) == 1  # each Northstar record is isolated on its own
        assert outbox_providers == source_providers  # never staged into the missing side


def test_canonical_id_echo_rebinds_instead_of_isolating_a_new_account(
    session: Session, settings: Settings
) -> None:
    """The mirror case of the loop test above: once identity CAN be
    re-established — the source system echoes back a record carrying the
    canonical id this control plane stamped into its outbox payload — the
    record binds to the existing account instead of spawning a new one."""
    service = ReconciliationService(session, settings)
    service.run(ReconcileRequest())

    northstar_hubspot = session.scalar(
        select(CanonicalAccount)
        .join(SourceRecord, SourceRecord.canonical_account_id == CanonicalAccount.id)
        .where(SourceRecord.provider == "hubspot", SourceRecord.external_id == "hs-3001")
    )
    assert northstar_hubspot is not None
    accounts_before = session.scalar(select(func.count()).select_from(CanonicalAccount))

    echoed = SourceAccount(
        provider="salesforce",
        external_id="001SF-ECHO-9001",
        name="Northstar Labs",
        domain=None,
        source_updated_at=datetime(2026, 8, 25, tzinfo=UTC),
        canonical_id_hint=northstar_hubspot.id,
    )
    result = service.run(ReconcileRequest(source_mode="inline", records=[echoed]))

    accounts_after = session.scalar(select(func.count()).select_from(CanonicalAccount))
    assert accounts_after == accounts_before
    assert result.accounts_reconciled == 1
    assert result.inserted_records == 1

    linked = session.get(CanonicalAccount, northstar_hubspot.id)
    assert linked is not None
    linked_external_ids = {row.external_id for row in linked.source_records}
    assert "001SF-ECHO-9001" in linked_external_ids


def test_outbox_dedup_only_against_the_immediately_preceding_item(
    session: Session, settings: Settings
) -> None:
    """S3: a payload-content-addressed id gives deduplication, not sequence
    idempotency. 135 -> 200 -> back to 135 must stage a third outbox item,
    not silently collide with the first one and leave the CRM stuck on 200."""
    service = ReconciliationService(session, settings)
    service.run(ReconcileRequest())

    acme = session.scalar(
        select(CanonicalAccount).where(CanonicalAccount.domain == "acme-labs.example")
    )
    assert acme is not None

    def acme_salesforce(employee_count: int, updated_at: datetime) -> SourceAccount:
        return SourceAccount(
            provider="salesforce",
            external_id="001SF1001",
            name="Acme Labs, Inc.",
            domain="acme-labs.example",
            industry="Software",
            employee_count=employee_count,
            owner_email="account-executive@example.invalid",
            lifecycle_stage="prospect",
            marketing_opt_in=False,
            source_updated_at=updated_at,
        )

    service.run(
        ReconcileRequest(
            source_mode="inline",
            records=[acme_salesforce(200, datetime(2026, 8, 26, tzinfo=UTC))],
        )
    )
    service.run(
        ReconcileRequest(
            source_mode="inline",
            records=[acme_salesforce(135, datetime(2026, 8, 27, tzinfo=UTC))],
        )
    )

    items = list(
        session.scalars(
            select(OutboxItem)
            .where(
                OutboxItem.canonical_account_id == acme.id,
                OutboxItem.target_provider == "salesforce",
            )
            .order_by(OutboxItem.sequence)
        )
    )
    assert [item.sequence for item in items] == [1, 2, 3]
    assert items[0].payload["NumberOfEmployees"] == 135
    assert items[1].payload["NumberOfEmployees"] == 200
    assert items[2].payload["NumberOfEmployees"] == 135
    assert items[0].payload_checksum == items[2].payload_checksum
    assert items[0].id != items[2].id
    assert items[0].idempotency_key != items[2].idempotency_key


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
