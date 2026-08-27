from __future__ import annotations

import argparse
import json

import uvicorn

from revops_sync.config import Settings
from revops_sync.db import Base, build_engine, build_session_factory
from revops_sync.reconcile import ReconciliationService
from revops_sync.schemas import ReconcileRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="RevOps sync control plane")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Run the FastAPI service")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    reconcile = subparsers.add_parser("reconcile", help="Run the synthetic fixture")
    reconcile.add_argument("--fixture", default=None)
    args = parser.parse_args()

    if args.command == "serve":
        uvicorn.run("revops_sync.main:app", host=args.host, port=args.port)
        return

    settings = Settings(fixture_path=args.fixture) if args.fixture else Settings()
    engine = build_engine(settings.database_url)
    if settings.auto_create_schema:
        Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    with factory() as session:
        result = ReconciliationService(session, settings).run(ReconcileRequest())
    print(json.dumps(result.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
