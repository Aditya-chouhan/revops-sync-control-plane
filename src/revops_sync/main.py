from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from revops_sync.api import build_router
from revops_sync.config import Settings, get_settings
from revops_sync.db import Base, build_engine, build_session_factory
from revops_sync.observability import configure_logging, request_observability


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    engine = build_engine(resolved.database_url)
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if resolved.auto_create_schema:
            Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="RevOps Sync Control Plane",
        version="0.1.0",
        description="Auditable identity resolution and guarded HubSpot-Salesforce sync previews.",
        lifespan=lifespan,
    )
    app.middleware("http")(request_observability)
    app.include_router(build_router(resolved, factory))
    return app


app = create_app()
