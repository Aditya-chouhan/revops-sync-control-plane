"""Persist dispatch identity without rewriting staged intent."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name, length in (
        ("dispatch_operation", 24),
        ("dispatch_external_id", 255),
        ("binding_source_id", 64),
    ):
        op.add_column("outbox_items", sa.Column(name, sa.String(length), nullable=True))


def downgrade() -> None:
    # Stop all workers first; removing dispatch identity loses recovery context.
    for name in ("binding_source_id", "dispatch_external_id", "dispatch_operation"):
        op.drop_column("outbox_items", name)
