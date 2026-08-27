from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter(
    "revops_sync_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
LATENCY = Histogram(
    "revops_sync_http_request_seconds", "HTTP request latency", ["method", "path"]
)
RECONCILIATIONS = Counter(
    "revops_sync_reconciliations_total", "Reconciliation runs", ["source_mode"]
)


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")


def log_event(event: str, **fields: object) -> None:
    logging.getLogger("revops_sync").info(
        json.dumps({"event": event, **fields}, sort_keys=True, default=str)
    )


async def request_observability(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - started
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    REQUESTS.labels(request.method, path, response.status_code).inc()
    LATENCY.labels(request.method, path).observe(elapsed)
    response.headers["x-request-id"] = request_id
    log_event(
        "http_request",
        request_id=request_id,
        method=request.method,
        path=path,
        status=response.status_code,
        duration_ms=round(elapsed * 1000, 2),
    )
    return response


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
