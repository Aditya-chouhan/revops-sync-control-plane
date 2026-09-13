# Evidence

## Ingestion ordering — 2026-09-13

- `pytest_ingestion_ordering_2026-09-13.xml`: machine-generated JUnit receipt, 116 passed and 1 skipped. Twenty additional tests cover duplicate snapshots, all six serial arrival permutations of three versions, stale identity/consent protection, whole-batch conflict rollback, timezone and legacy offset normalization, timezone validation, API 409/stale receipts, and mixed-batch counts.
- `coverage_ingestion_ordering_2026-09-13.xml`: machine-generated line coverage, 93.85% (840/895 lines); the 80% gate passed.
- `ruff_ingestion_ordering_2026-09-13.txt` and `mypy_ingestion_ordering_2026-09-13.txt`: captured final static-check output.

Same cached local dependency environment described below; SQLite and synthetic complete source snapshots. The optional PostgreSQL smoke remains skipped. This does not prove fresh installation, live CRM webhook signatures, concurrent ingestion exclusion, or PostgreSQL concurrency. No live/provider credentials or database deployment were used. Earlier receipts remain unchanged. GitHub Actions results require a separate check.

```bash
pytest --junitxml=evidence/pytest_ingestion_ordering_2026-09-13.xml \
  --cov=src/revops_sync --cov-report=xml:evidence/coverage_ingestion_ordering_2026-09-13.xml \
  --cov-report=term-missing --cov-fail-under=80
```

## Target binding — 2026-09-13

- `pytest_target_binding_2026-09-13.xml`: machine-generated JUnit receipt, 96 passed and 1 skipped. Thirteen additional tests cover both providers' queued POST/PATCH chains, persisted exact-object recovery after read-back outage and restart, conflicting/missing IDs, malformed intent, reconciled-create binding after audited release, stale-owner fencing, and migration preservation.
- `coverage_target_binding_2026-09-13.xml`: machine-generated line coverage, 93.63% (808/863 lines); the 80% gate passed.
- `ruff_target_binding_2026-09-13.txt` and `mypy_target_binding_2026-09-13.txt`: captured final static-check output.

Same cached local dependency environment described below; SQLite and simulated HTTP only. The optional PostgreSQL smoke remains skipped because `POSTGRES_SMOKE_URL` is unset. No new live CRM, PostgreSQL, or fresh-install evidence is claimed. Earlier receipts are retained. GitHub Actions results must be checked separately.

```bash
pytest --junitxml=evidence/pytest_target_binding_2026-09-13.xml \
  --cov=src/revops_sync --cov-report=xml:evidence/coverage_target_binding_2026-09-13.xml \
  --cov-report=term-missing --cov-fail-under=80
```

## Stream ordering — 2026-09-13

- `pytest_stream_ordering_2026-09-13.xml`: machine-generated JUnit receipt, 83 passed and 1 skipped. Twenty additional tests cover full-prefix blocking, independent streams, different-item races with stale snapshots, active-owner barriers, late requests after lease takeover, asynchronous acceptance, operator attestation, audit rollback, and conservative SQLite migration backfill.
- `coverage_stream_ordering_2026-09-13.xml`: machine-generated line coverage, 93.56% (770/823 lines); the 80% gate passed.
- `ruff_stream_ordering_2026-09-13.txt` and `mypy_stream_ordering_2026-09-13.txt`: captured final static-check output.

Same cached local dependency environment as below. Concurrent tests use independent sessions/connections on file-backed SQLite and simulated HTTP, not live CRMs. The optional PostgreSQL smoke remains skipped because `POSTGRES_SMOKE_URL` is unset. No new PostgreSQL concurrency, fresh-install, or provider-settlement evidence is claimed. Earlier receipts remain unchanged. GitHub Actions results must be checked separately.

```bash
pytest --junitxml=evidence/pytest_stream_ordering_2026-09-13.xml \
  --cov=src/revops_sync --cov-report=xml:evidence/coverage_stream_ordering_2026-09-13.xml \
  --cov-report=term-missing --cov-fail-under=80
```

## Worker claims — 2026-09-13

- `pytest_worker_claims_2026-09-13.xml`: machine-generated JUnit receipt, 63 passed and 1 skipped. Twelve new tests cover two-worker races for both CRMs and both operations, active exclusion, expired-claim read-only takeover, token-fenced stale acknowledgements/cleanup, expired dispatch, dirty-session rejection, and SQLite migration upgrade/drift/downgrade preserving existing unknown outcomes.
- `coverage_worker_claims_2026-09-13.xml`: machine-generated line coverage, 93.31% (725/777 lines); the 80% gate passed.
- `ruff_worker_claims_2026-09-13.txt` and `mypy_worker_claims_2026-09-13.txt`: captured final static-check output.

The races use separate SQLAlchemy sessions/connections on file-backed SQLite and two Python threads with stale snapshots. They do not use a process-local delivery mutex. This is not a PostgreSQL concurrency run or live CRM evidence. Runtime uses the same cached local development dependencies described below; tokens and HTTP responses are synthetic. The optional PostgreSQL smoke remains skipped. Original timeout receipts are preserved separately.

```bash
pytest --junitxml=evidence/pytest_worker_claims_2026-09-13.xml \
  --cov=src/revops_sync --cov-report=xml:evidence/coverage_worker_claims_2026-09-13.xml \
  --cov-report=term-missing --cov-fail-under=80
```

## Timeout recovery verification — 2026-09-13

- `pytest_2026-09-13.xml`: machine-generated JUnit receipt, 51 passed and 1 skipped. The skip is the optional real-PostgreSQL smoke test because `POSTGRES_SMOKE_URL` was not configured.
- `coverage_2026-09-13.xml`: machine-generated line coverage, 93.14% (679/729 lines); the 80% gate passed.
- `ruff_2026-09-13.txt` and `mypy_2026-09-13.txt`: captured command output, not handwritten summaries.

Environment: Python 3.12.14, pytest 8.4.2, pytest-cov 6.3.0, SQLite, cached development dependencies with the current repository's `src` on `PYTHONPATH`. A fresh dependency installation was blocked by DNS resolution, so this receipt is a local cached-dependency verification, not proof of a fresh install. HTTP provider responses are simulated by `httpx.MockTransport`; credentials are fake. No live CRM write or new PostgreSQL run occurred. Docker/dbt/Terraform were not revalidated locally for this Python-only change. GitHub Actions is authoritative for those checks.

Reproduce after installing the project's development dependencies:

```bash
ruff check .
mypy src
pytest --junitxml=evidence/pytest_2026-09-13.xml \
  --cov=src/revops_sync --cov-report=xml:evidence/coverage_2026-09-13.xml \
  --cov-report=term-missing --cov-fail-under=80
```

## Historical verification — 2026-08-27

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
