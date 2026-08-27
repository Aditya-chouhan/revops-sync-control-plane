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
