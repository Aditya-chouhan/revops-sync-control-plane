# Evidence

Real committed output, not hand-typed numbers. Regenerated 2026-08-27 after the
S1–S4 fixes described in `docs/CASE_STUDY.md`.

- `pytest_2026-08-27.txt` — `pytest --cov=src/revops_sync --cov-report=term-missing
  --cov-fail-under=80 -v`, Python 3.12.14, run locally with `POSTGRES_SMOKE_URL` set
  to a real local Postgres 16 instance (so `test_postgres_smoke.py` actually ran,
  not just skipped): **27 passed, 93.05% coverage**. In CI, that one test is
  skipped by design — see below — so CI shows 26 passed against the same 93.05%.
- `ruff_2026-08-27.txt` — `ruff check .`: all checks passed.
- `mypy_2026-08-27.txt` — `mypy src` (`strict = true`): no issues found in 12 files.

## Where "transactional reconciliation in PostgreSQL" is actually exercised

CI provisions a Postgres 17 service (`.github/workflows/ci.yml`) but the app test
suite runs against in-memory SQLite for per-test isolation and speed — Postgres
was previously touched only by `alembic upgrade head` / `alembic check`, which
verifies schema, not application behavior. `tests/test_postgres_smoke.py` closes
that gap: it runs a real `ReconciliationService.run()` against a real Postgres
database, using an id namespaced to the test run so it can't collide with
anything else, and cleans up its own row afterward. It's skipped unless
`POSTGRES_SMOKE_URL` is set, so it doesn't slow down the default local/CI run;
wiring CI to set it and run it on every push is the natural next step, not yet
done here.

## GitHub Actions

Run `33085722635` (2026-08-27, commit `edcde4f`) — before the fixes in this
evidence directory — is linked from the README and covers lint, `mypy --strict`,
the pre-fix test suite (15 passed / 87.25%), `alembic upgrade` + `alembic check`
on real Postgres 17, `terraform validate`, `dbt parse`, and a real `docker build`.
The push containing these fixes reruns the same CI job; see the repository's
Actions tab for that run's own log rather than treating this file as a permanent
substitute for it.
