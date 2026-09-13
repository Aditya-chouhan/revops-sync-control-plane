# Five-minute walkthrough: safe CRM operations

**Synthetic accounts. Simulated CRM responses. Real control-plane code.** This demonstrates correctness and operator recovery, not commercial results or a production deployment.

## Run one command

After installing the project with Python 3.12+ (`python -m pip install -e ".[dev]"`):

```bash
python -m revops_sync.demo
```

No Docker, CRM account, API key, fixture download, `.env` change, or server is required. Each run creates an isolated temporary SQLite database and removes it afterward. The demo supplies fake credentials to exercise delivery guards, and every CRM request is handled exclusively by `httpx.MockTransport` at `crm.invalid`. It does not connect to your configured database or CRM. It exits nonzero if any outcome check fails.

For machine-readable output:

```bash
python -m revops_sync.demo --json
```

Read the [captured execution output](HIRING_DEMO_OUTPUT.md) without installing anything, or inspect the [JSON receipt](../evidence/hiring_demo_2026-09-13.json). Both include exact sample inputs, observed states, simulated request trace and limitations; tests check these committed artifacts against a fresh run.

## What to watch

| Scene | Business risk | Expected evidence |
|---|---|---|
| 1. Same domain, conflicting CRM observations | Unclear ownership; accidental opt-in | One canonical account; Salesforce name wins; consent stays false; both disagreements recorded |
| 2. Replay, stale input, equal-version conflict | Duplicate actions; identity/consent regression | Two duplicates unchanged; one stale snapshot ignored before identity resolution; conflicting version rejected; no additional previews |
| 3. A second account's create is accepted but its response is lost | Blind replay creates duplicates | Simulated POST followed by read-back GET; persisted `reconciled`; one write attempt; uncertainty hold remains |
| 4. Reopen a database session; try the newer queued operation | A late old operation overwrites newer values | Acknowledged replay makes no request; newer operation blocked with zero requests and attempts |
| 5. Simulated operator settlement review | Unaudited bypass; another queued create | Audit persisted; original POST intent/key/checksum preserved; effective PATCH targets the confirmed ID; simulated CRM ends at v2 |

The full simulated trace is **POST → GET → PATCH**: one create, one read-back, one update. Real external writes: **0**. This is a scenario result, not a production incident or business KPI.

Two accounts are intentional: the first demonstrates conflicting observations from both providers; the second initially exists only in HubSpot, so Salesforce create-to-update propagation can be demonstrated. HubSpot delivery is not executed in this walkthrough; the regression suite separately covers both providers' recovery/binding paths.

## Source and inspection path

- [Demo and synthetic inputs](../src/revops_sync/demo.py)
- [Identity and reconciliation](../src/revops_sync/reconcile.py)
- [Delivery, claims and target binding](../src/revops_sync/gateways.py)
- [Audited hold release](../src/revops_sync/ordering.py)
- [Walkthrough regression tests](../tests/test_hiring_demo.py)
- [All test/static-check receipts](../evidence/README.md)

## Honest boundaries

The operator settlement attestation is synthetic: matching read-back alone is not live-provider settlement proof. The demonstration is serial SQLite execution; it does not validate PostgreSQL concurrency, simultaneous ingestion requests, signed webhooks, external seller-edit conditional updates, exactly-once effects, or source deletion/merge handling. Ingestion must be serialized across processes for operational use; the API does not enforce that coordination. No new live CRM behavior was tested. Pipeline, revenue, adoption and time saved are unmeasured.

“Five-minute” describes the intended walkthrough length, not a measured productivity saving. Automated runtime varies by machine. No video recording is implied by these runnable and captured artifacts.
