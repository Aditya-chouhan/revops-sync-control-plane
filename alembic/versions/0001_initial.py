"""Initial sync-control schema."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canonical_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=120), nullable=True),
        sa.Column("employee_count", sa.Integer(), nullable=True),
        sa.Column("owner_email", sa.String(length=255), nullable=True),
        sa.Column("lifecycle_stage", sa.String(length=80), nullable=True),
        sa.Column("marketing_opt_in", sa.Boolean(), nullable=True),
        sa.Column("resolution_basis", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain"),
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_mode", sa.String(length=24), nullable=False),
        sa.Column("input_records", sa.Integer(), nullable=False),
        sa.Column("inserted_records", sa.Integer(), nullable=False),
        sa.Column("updated_records", sa.Integer(), nullable=False),
        sa.Column("unchanged_records", sa.Integer(), nullable=False),
        sa.Column("accounts_reconciled", sa.Integer(), nullable=False),
        sa.Column("conflicts_recorded", sa.Integer(), nullable=False),
        sa.Column("outbox_previews_created", sa.Integer(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "source_records",
        sa.Column("id", sa.String(length=320), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_account_id", sa.String(length=36), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["canonical_account_id"], ["canonical_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_records_provider", "source_records", ["provider"])
    op.create_index(
        "ix_source_records_canonical_account_id", "source_records", ["canonical_account_id"]
    )
    op.create_index(
        "uq_source_provider_external",
        "source_records",
        ["provider", "external_id"],
        unique=True,
    )
    op.create_table(
        "conflict_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("canonical_account_id", sa.String(length=36), nullable=False),
        sa.Column("field_name", sa.String(length=80), nullable=False),
        sa.Column("hubspot_value", sa.JSON(), nullable=True),
        sa.Column("salesforce_value", sa.JSON(), nullable=True),
        sa.Column("chosen_value", sa.JSON(), nullable=True),
        sa.Column("chosen_source", sa.String(length=24), nullable=False),
        sa.Column("policy", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["canonical_account_id"], ["canonical_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conflict_records_canonical_account_id",
        "conflict_records",
        ["canonical_account_id"],
    )
    op.create_table(
        "outbox_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("canonical_account_id", sa.String(length=36), nullable=False),
        sa.Column("target_provider", sa.String(length=24), nullable=False),
        sa.Column("target_external_id", sa.String(length=255), nullable=True),
        sa.Column("operation", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["canonical_account_id"], ["canonical_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_outbox_items_target_provider", "outbox_items", ["target_provider"])
    op.create_index("ix_outbox_items_status", "outbox_items", ["status"])
    op.create_index(
        "ix_outbox_items_canonical_account_id", "outbox_items", ["canonical_account_id"]
    )


def downgrade() -> None:
    op.drop_table("outbox_items")
    op.drop_table("conflict_records")
    op.drop_table("source_records")
    op.drop_table("sync_runs")
    op.drop_table("canonical_accounts")
