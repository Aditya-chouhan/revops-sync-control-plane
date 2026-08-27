from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from urllib.parse import urlsplit

from revops_sync.schemas import SourceAccount

NAMESPACE = uuid.UUID("e136699e-d2e4-4b1d-b64e-dba6c14a9f6b")

# Domains that identify a mail provider or a hosted website builder rather than
# a company. Reps routinely put an inbox or storefront domain in the "company
# domain" field; merging on one of these would silently collapse every account
# that shares it into a single canonical row (`CanonicalAccount.domain` is
# unique). These are isolated the same conservative way a missing domain is.
_GENERIC_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.in",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "icloud.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "mail.com",
        "yandex.com",
        "zoho.com",
        "shopify.com",
        "myshopify.com",
        "wixsite.com",
        "squarespace.com",
        "godaddysites.com",
        "weebly.com",
        "business.site",
        "mybusiness.site",
    }
)


def normalize_domain(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip().lower()
    if not candidate:
        return None
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    host = (parsed.hostname or "").rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host:
        return None
    return host


def canonical_identity(
    record: SourceAccount,
    account_exists: Callable[[str], bool] | None = None,
) -> tuple[str, str, str | None]:
    domain = normalize_domain(record.domain)
    if domain and domain in _GENERIC_DOMAINS:
        isolated_key = f"isolated:{record.provider}:{record.external_id}"
        return str(uuid.uuid5(NAMESPACE, isolated_key)), "generic_domain_not_mergeable", None
    if domain:
        return str(uuid.uuid5(NAMESPACE, f"domain:{domain}")), "exact_normalized_domain", domain
    if (
        record.canonical_id_hint
        and account_exists is not None
        and account_exists(record.canonical_id_hint)
    ):
        # The source system is echoing back a record stamped with the
        # canonical id we wrote into its payload (GTM_Canonical_ID__c /
        # gtm_canonical_id). Identity is already established — bind to it
        # instead of isolating a fresh, unlinked account for it.
        return record.canonical_id_hint, "canonical_id_echo", None
    isolated_key = f"isolated:{record.provider}:{record.external_id}"
    return str(uuid.uuid5(NAMESPACE, isolated_key)), "provider_record_no_domain", None


def source_record_id(record: SourceAccount) -> str:
    return f"{record.provider}:{record.external_id}"
