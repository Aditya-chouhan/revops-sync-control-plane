"""Add expiring, fenced worker claims without resetting delivery outcomes."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outbox_items", sa.Column("claim_token", sa.String(36), nullable=True))
    op.add_column(
        "outbox_items", sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    # Stop all workers first; removing fences during active dispatch is unsafe.
    op.drop_column("outbox_items", "claim_expires_at")
    op.drop_column("outbox_items", "claim_token")
