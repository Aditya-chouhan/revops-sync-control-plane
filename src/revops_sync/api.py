from __future__ import annotations

import hmac
from collections.abc import Generator

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from revops_sync.config import Settings
from revops_sync.gateways import integration_preview
from revops_sync.models import CanonicalAccount, OutboxItem
from revops_sync.observability import RECONCILIATIONS, metrics_response
from revops_sync.reconcile import ReconciliationService
from revops_sync.schemas import (
    AccountDetail,
    AccountRead,
    IntegrationPreview,
    OutboxRead,
    ReconcileRequest,
    ReconcileResult,
)


def build_router(settings: Settings, factory: sessionmaker[Session]) -> APIRouter:
    router = APIRouter()

    def get_db() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def require_api_key(
        x_api_key: str | None = Header(default=None, alias="x-api-key"),  # noqa: B008
    ) -> None:
        configured = settings.workflow_api_key
        if configured is None:
            return
        if not hmac.compare_digest(x_api_key or "", configured.get_secret_value()):
            raise HTTPException(status_code=401, detail="Invalid or missing workflow API key")

    protected = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

    @router.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready", tags=["health"])
    def ready(session: Session = Depends(get_db)) -> dict[str, str]:  # noqa: B008
        session.execute(text("SELECT 1"))
        return {"status": "ready"}

    @router.get("/metrics", include_in_schema=False)
    def metrics():  # type: ignore[no-untyped-def]
        return metrics_response()

    @protected.post("/reconcile", response_model=ReconcileResult, tags=["reconciliation"])
    def reconcile(
        request: ReconcileRequest,
        session: Session = Depends(get_db),  # noqa: B008
    ) -> ReconcileResult:
        try:
            result = ReconciliationService(session, settings).run(request)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        RECONCILIATIONS.labels(request.source_mode).inc()
        return result

    @protected.get("/accounts", response_model=list[AccountRead], tags=["accounts"])
    def list_accounts(session: Session = Depends(get_db)) -> list[CanonicalAccount]:  # noqa: B008
        return list(session.scalars(select(CanonicalAccount).order_by(CanonicalAccount.name)))

    @protected.get("/accounts/{account_id}", response_model=AccountDetail, tags=["accounts"])
    def get_account(
        account_id: str,
        session: Session = Depends(get_db),  # noqa: B008
    ) -> CanonicalAccount:
        account = session.get(CanonicalAccount, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        _ = account.source_records, account.conflicts, account.outbox_items
        return account

    @protected.get("/outbox", response_model=list[OutboxRead], tags=["outbox"])
    def list_outbox(
        provider: str | None = None,
        session: Session = Depends(get_db),  # noqa: B008
    ) -> list[OutboxItem]:
        query = select(OutboxItem).order_by(OutboxItem.created_at, OutboxItem.id)
        if provider:
            if provider not in {"hubspot", "salesforce"}:
                raise HTTPException(status_code=422, detail="Unsupported provider")
            query = query.where(OutboxItem.target_provider == provider)
        return list(session.scalars(query))

    @protected.get(
        "/integrations/{provider}/accounts/{account_id}/preview",
        response_model=IntegrationPreview,
        tags=["integrations"],
    )
    def preview(
        provider: str,
        account_id: str,
        session: Session = Depends(get_db),  # noqa: B008
    ) -> IntegrationPreview:
        account = session.get(CanonicalAccount, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        item = session.scalar(
            select(OutboxItem)
            .where(
                OutboxItem.canonical_account_id == account_id,
                OutboxItem.target_provider == provider,
            )
            .order_by(OutboxItem.created_at.desc())
        )
        if item is None:
            raise HTTPException(status_code=404, detail="No outbox preview for provider")
        try:
            return integration_preview(provider, account, item, settings)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    router.include_router(protected)
    return router
