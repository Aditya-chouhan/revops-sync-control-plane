from __future__ import annotations

from datetime import UTC, datetime

from revops_sync.identity import canonical_identity, normalize_domain
from revops_sync.schemas import SourceAccount


def make_record(provider: str, external_id: str, domain: str | None) -> SourceAccount:
    return SourceAccount(
        provider=provider,  # type: ignore[arg-type]
        external_id=external_id,
        name="Example",
        domain=domain,
        source_updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_domain_normalization_is_conservative() -> None:
    assert normalize_domain(" HTTPS://WWW.Example.com/pricing ") == "example.com"
    assert normalize_domain("example.com.") == "example.com"
    assert normalize_domain("not a domain") is None
    assert normalize_domain(None) is None


def test_exact_domain_matches_but_missing_domain_does_not() -> None:
    hubspot = make_record("hubspot", "hs-1", "www.example.com")
    salesforce = make_record("salesforce", "sf-1", "https://example.com")
    assert canonical_identity(hubspot)[0] == canonical_identity(salesforce)[0]

    hs_missing = make_record("hubspot", "hs-2", None)
    sf_missing = make_record("salesforce", "sf-2", None)
    assert canonical_identity(hs_missing)[0] != canonical_identity(sf_missing)[0]
    assert canonical_identity(hs_missing)[1] == "provider_record_no_domain"


def test_generic_domains_are_isolated_not_merged() -> None:
    hubspot = make_record("hubspot", "hs-9", "gmail.com")
    salesforce = make_record("salesforce", "sf-9", "gmail.com")
    hs_id, hs_basis, hs_domain = canonical_identity(hubspot)
    sf_id, sf_basis, sf_domain = canonical_identity(salesforce)
    assert hs_id != sf_id
    assert hs_basis == sf_basis == "generic_domain_not_mergeable"
    assert hs_domain is None
    assert sf_domain is None


def test_canonical_id_hint_rebinds_when_the_account_actually_exists() -> None:
    hint_record = make_record("salesforce", "sf-echo", None).model_copy(
        update={"canonical_id_hint": "existing-account-id"}
    )
    account_id, basis, domain = canonical_identity(
        hint_record, account_exists=lambda candidate: candidate == "existing-account-id"
    )
    assert account_id == "existing-account-id"
    assert basis == "canonical_id_echo"
    assert domain is None


def test_canonical_id_hint_is_ignored_when_the_account_does_not_exist() -> None:
    hint_record = make_record("salesforce", "sf-echo", None).model_copy(
        update={"canonical_id_hint": "ghost-account-id"}
    )
    account_id, basis, _ = canonical_identity(hint_record, account_exists=lambda candidate: False)
    assert basis == "provider_record_no_domain"
    assert account_id != "ghost-account-id"


def test_canonical_id_hint_is_ignored_without_an_exists_check() -> None:
    # No account_exists callable supplied — a caller cannot forget the check
    # and accidentally trust an unverified hint.
    hint_record = make_record("salesforce", "sf-echo", None).model_copy(
        update={"canonical_id_hint": "some-account-id"}
    )
    account_id, basis, _ = canonical_identity(hint_record)
    assert basis == "provider_record_no_domain"
    assert account_id != "some-account-id"
