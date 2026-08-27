FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/app/.local/bin:$PATH

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -r /wheels
COPY alembic.ini ./
COPY alembic ./alembic
COPY data ./data

USER app
# EXPOSE documents the default only. Cloud Run, Render, Railway, and HF
# Spaces all inject $PORT and route to it at runtime, so the CMD below must
# honor it rather than hardcode 8000 — a hardcoded port silently drops all
# traffic on every one of those platforms.
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn revops_sync.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
