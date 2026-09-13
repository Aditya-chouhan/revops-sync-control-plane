from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Provider = Literal["hubspot", "salesforce"]


class SourceAccount(BaseModel):
    provider: Provider
    external_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    domain: str | None = None
    industry: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    owner_email: str | None = None
    lifecycle_stage: str | None = None
    marketing_opt_in: bool | None = None
    source_updated_at: datetime
    canonical_id_hint: str | None = Field(
        default=None,
        description=(
            "Read from gtm_canonical_id / GTM_Canonical_ID__c when a source system "
            "echoes back a record this control plane previously staged. Used to "
            "re-bind identity instead of isolating a new canonical account."
        ),
    )


class ReconcileRequest(BaseModel):
    source_mode: Literal["fixture", "inline"] = "fixture"
    records: list[SourceAccount] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_source(self) -> ReconcileRequest:
        if self.source_mode == "inline" and not self.records:
            raise ValueError("inline mode requires records")
        if self.source_mode == "fixture" and self.records:
            raise ValueError("fixture mode loads committed records; do not also provide records")
        return self


class ReconcileResult(BaseModel):
    run_id: str
    source_mode: str
    data_classification: str
    input_records: int
    inserted_records: int
    updated_records: int
    unchanged_records: int
    accounts_reconciled: int
    conflicts_recorded: int
    outbox_previews_created: int
    external_writes: int = 0


class SourceRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    external_id: str
    source_updated_at: datetime
    payload: dict[str, Any]


class ConflictRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    field_name: str
    hubspot_value: Any | None
    salesforce_value: Any | None
    chosen_value: Any | None
    chosen_source: str
    policy: str
    status: str


class OutboxRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    target_provider: str
    target_external_id: str | None
    operation: str
    sequence: int
    payload_checksum: str
    idempotency_key: str
    payload: dict[str, Any]
    status: str
    attempts: int
    claim_expires_at: datetime | None = None
    ordering_hold: bool = False


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    domain: str | None
    name: str
    industry: str | None
    employee_count: int | None
    owner_email: str | None
    lifecycle_stage: str | None
    marketing_opt_in: bool | None
    resolution_basis: str


class AccountDetail(AccountRead):
    source_records: list[SourceRecordRead]
    conflicts: list[ConflictRead]
    outbox_items: list[OutboxRead]


class IntegrationPreview(BaseModel):
    provider: Provider
    account_id: str
    external_id: str | None
    method: Literal["POST", "PATCH"]
    endpoint_template: str
    payload: dict[str, Any]
    would_write: Literal[False] = False
    credential_present: bool
    live_switches_enabled: bool
    claim_boundary: str
