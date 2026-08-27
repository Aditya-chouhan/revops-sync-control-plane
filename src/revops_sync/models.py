from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revops_sync.db import Base


class CanonicalAccount(Base):
    __tablename__ = "canonical_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    domain: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lifecycle_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    marketing_opt_in: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolution_basis: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    source_records: Mapped[list[SourceRecord]] = relationship(back_populates="account")
    conflicts: Mapped[list[ConflictRecord]] = relationship(back_populates="account")
    outbox_items: Mapped[list[OutboxItem]] = relationship(back_populates="account")


class SourceRecord(Base):
    __tablename__ = "source_records"

    id: Mapped[str] = mapped_column(String(320), primary_key=True)
    provider: Mapped[str] = mapped_column(String(24), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    canonical_account_id: Mapped[str] = mapped_column(
        ForeignKey("canonical_accounts.id", ondelete="CASCADE"), index=True
    )
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    account: Mapped[CanonicalAccount] = relationship(back_populates="source_records")

    __table_args__ = (
        Index("uq_source_provider_external", "provider", "external_id", unique=True),
    )


class ConflictRecord(Base):
    __tablename__ = "conflict_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_account_id: Mapped[str] = mapped_column(
        ForeignKey("canonical_accounts.id", ondelete="CASCADE"), index=True
    )
    field_name: Mapped[str] = mapped_column(String(80))
    hubspot_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    salesforce_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    chosen_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    chosen_source: Mapped[str] = mapped_column(String(24))
    policy: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="auto_resolved")
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    account: Mapped[CanonicalAccount] = relationship(back_populates="conflicts")


class OutboxItem(Base):
    __tablename__ = "outbox_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_account_id: Mapped[str] = mapped_column(
        ForeignKey("canonical_accounts.id", ondelete="CASCADE"), index=True
    )
    target_provider: Mapped[str] = mapped_column(String(24), index=True)
    target_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operation: Mapped[str] = mapped_column(String(24))
    # Monotonic per (account, provider) — the identity half of idempotency.
    # `payload_checksum` is a dedup hint compared only against the immediately
    # preceding item for this pair, so a value that reverts to something staged
    # two items ago still gets a fresh sequence instead of being silently
    # dropped (see: `_create_outbox_previews`).
    sequence: Mapped[int] = mapped_column(Integer)
    payload_checksum: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), default="preview_only", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped[CanonicalAccount] = relationship(back_populates="outbox_items")

    __table_args__ = (
        Index(
            "uq_outbox_account_provider_sequence",
            "canonical_account_id",
            "target_provider",
            "sequence",
            unique=True,
        ),
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_mode: Mapped[str] = mapped_column(String(24))
    input_records: Mapped[int] = mapped_column(Integer)
    inserted_records: Mapped[int] = mapped_column(Integer)
    updated_records: Mapped[int] = mapped_column(Integer)
    unchanged_records: Mapped[int] = mapped_column(Integer)
    accounts_reconciled: Mapped[int] = mapped_column(Integer)
    conflicts_recorded: Mapped[int] = mapped_column(Integer)
    outbox_previews_created: Mapped[int] = mapped_column(Integer)
    receipt: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
