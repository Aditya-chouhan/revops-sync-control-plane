from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session

from revops_sync.models import OrderingResolution, OutboxItem


def release_ordering_hold(
    session: Session,
    item_id: str,
    *,
    reviewer: str,
    reason: str,
    provider_settled: bool = False,
) -> str:
    """Internal operator action, not an automatic provider-settlement oracle.

    The authorized operator must establish that old workers/remote operations
    cannot still apply an older payload. This function only records that
    attestation, clears a hold, and atomically appends its audit; it sends no HTTP.
    """
    if not provider_settled or not reviewer.strip() or not reason.strip():
        raise ValueError("Hold release requires reviewer, reason, and provider-settled attestation")
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Hold release requires a clean dedicated session")
    released = session.execute(
        update(OutboxItem)
        .where(
            OutboxItem.id == item_id,
            OutboxItem.ordering_hold.is_(True),
            OutboxItem.status.in_(["delivered", "reconciled"]),
            OutboxItem.claim_token.is_(None),
        )
        .values(ordering_hold=False)
        .returning(OutboxItem.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if released is None:
        session.rollback()
        raise RuntimeError("Item is not acknowledged, is actively claimed, or has no ordering hold")
    resolution_id = str(uuid.uuid4())
    session.add(
        OrderingResolution(
            id=resolution_id,
            outbox_item_id=item_id,
            reviewer=reviewer.strip(),
            reason=reason.strip(),
            action="release_hold_provider_settled",
        )
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.expire_all()
    return resolution_id
