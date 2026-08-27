"""Split outbox idempotency into a monotonic sequence plus a payload checksum.

A checksum used directly as the primary key gives deduplication, not sequence
idempotency: a value that reverts to an earlier state produces the same key as
the earlier item and is silently dropped instead of staged. This migration adds
`sequence` (identity, monotonic per account+provider) and `payload_checksum`
(a dedup hint compared only against the immediately preceding item).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default backfills any existing rows (none, in this demo's usage
    # pattern) and is deliberately left in place rather than dropped
    # afterward: dropping a column default requires ALTER COLUMN ... DROP
    # DEFAULT, which SQLite's limited ALTER TABLE support rejects outright,
    # and the application always supplies both columns explicitly on every
    # insert, so the leftover default is inert either way.
    op.add_column(
        "outbox_items",
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "outbox_items",
        sa.Column("payload_checksum", sa.String(length=64), nullable=False, server_default=""),
    )
    op.create_index(
        "uq_outbox_account_provider_sequence",
        "outbox_items",
        ["canonical_account_id", "target_provider", "sequence"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_outbox_account_provider_sequence", table_name="outbox_items")
    op.drop_column("outbox_items", "payload_checksum")
    op.drop_column("outbox_items", "sequence")
