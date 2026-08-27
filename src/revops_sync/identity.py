from __future__ import annotations

import re
import uuid
from urllib.parse import urlsplit

from revops_sync.schemas import SourceAccount

NAMESPACE = uuid.UUID("e136699e-d2e4-4b1d-b64e-dba6c14a9f6b")


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


def canonical_identity(record: SourceAccount) -> tuple[str, str, str | None]:
    domain = normalize_domain(record.domain)
    if domain:
        return str(uuid.uuid5(NAMESPACE, f"domain:{domain}")), "exact_normalized_domain", domain
    isolated_key = f"isolated:{record.provider}:{record.external_id}"
    return str(uuid.uuid5(NAMESPACE, isolated_key)), "provider_record_no_domain", None


def source_record_id(record: SourceAccount) -> str:
    return f"{record.provider}:{record.external_id}"
