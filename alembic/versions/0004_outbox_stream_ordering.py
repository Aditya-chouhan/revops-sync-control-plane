"""Gate account/provider streams and audit reviewed ordering-hold releases."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_items",
        sa.Column("ordering_hold", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # Legacy read-back acknowledgements do not prove an old remote request
    # cannot still land. Preserve outcomes while holding their successors.
    op.execute(
        sa.text(
            "UPDATE outbox_items SET ordering_hold=true "
            "WHERE status IN ('dispatching','outcome_unknown','reconciled')"
        )
    )
    op.create_table(
        "ordering_resolutions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "outbox_item_id", sa.String(64), sa.ForeignKey("outbox_items.id"), nullable=False
        ),
        sa.Column("reviewer", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_ordering_resolutions_outbox_item_id", "ordering_resolutions", ["outbox_item_id"]
    )


def downgrade() -> None:
    # Stop all workers first. This removes ordering safeguards and their audit.
    op.drop_table("ordering_resolutions")
    op.drop_column("outbox_items", "ordering_hold")
