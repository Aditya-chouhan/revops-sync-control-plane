# Duplicate and out-of-order normalized source snapshots

## Failure fixed

The ingestion service previously replaced a stored source record whenever its payload checksum changed, even if `source_updated_at` was older. A delayed snapshot could restore an old consent value or use an old domain to move identity/create a phantom account. Equal timestamps with different snapshots silently accepted whichever arrived last.

## Version contract

`POST /v1/reconcile` in inline mode accepts complete normalized `SourceAccount` snapshots, not raw CRM webhook deltas. The key is `(provider, external_id)` and the version is the authoritative source object's `source_updated_at`, not webhook arrival time. Inputs require an explicit timezone and normalize to UTC. Stored UTC SQLite timestamps are interpreted as UTC when SQLite omits timezone metadata.

| Incoming version | Behavior |
|---|---|
| Older than stored | Ignore before identity resolution; increment `stale_records`; do not touch canonical state or stage previews |
| Equal, same normalized snapshot | Count unchanged; no duplicate source row or preview |
| Equal, different snapshot | Raise `SourceVersionConflict`; rollback the entire batch; HTTP 409 |
| Newer | Accept through existing identity, ownership, restrictive-consent and outbox policies |

This policy also applies to duplicates/out-of-order records within one batch. A conflicting first batch creates no accounts, source rows, previews, or success receipt. Following requests can reuse the rolled-back session. Reconciliation requires a clean dedicated session, because the service commits or rolls back the transaction.

`stale_records` appears in the response and persisted `SyncRun.receipt`; there is no extra run-table column or migration. Successful duplicate/stale requests still create run receipts: receipt count is not event uniqueness. Stale counts mean rejected versions, not recovered revenue or prevented production incidents.

Equivalent explicit timezone offsets are normalized before duplicate comparison, including stored legacy offset payloads. Legacy payloads without an authoritative timezone need reviewed migration; this change does not silently guess one for raw source inputs.

## Integration and remaining boundaries

There is **no provider webhook receiver** in this repository. A future adapter must validate provider signatures, obtain the latest complete authorized object snapshot, translate its source version correctly, handle deletions/tombstones and event identities, and feed normalized snapshots here. Do not post property-change deltas as complete snapshots: omitted fields currently mean null rather than unchanged. HTTP 409 requires reviewed refetch/version resolution, not changing timestamps until an event passes.

**The version comparison is application-level and tested with serial ingestion. It is not a concurrent-writer compare-and-swap or lock.** Deployment must serialize ingestion across all processes (not merely use one in-process mutex) until transaction-level coordination is implemented. Delivery worker claims do not protect ingestion transactions. The HTTP API itself does not enforce that serialization. This work does not prove protection from simultaneous reconciliation requests or ingestion racing delivery planning.

No new public endpoint, live CRM request, webhook subscription, paid lookup, credential use, background worker, or database deployment occurred. Existing optional API-key protection is unchanged; this does not make an unconfigured endpoint authenticated.

## Proof

Twenty additional synthetic tests cover duplicate handling for both providers; all six serial arrival permutations of three versions; stale domain/hint/consent protection; atomic same-version conflict rollback; equivalent timezone offsets; legacy offset normalization; timezone validation; API 409/stale receipts; and mixed-batch counts.

The newest **source/canonical state and latest staged preview** converge. Historical preview counts may differ by arrival order because accepted intermediate states are retained. This is not queue compaction or proof that every staged historical payload is current.

Run:

```bash
pytest tests/test_ingestion_ordering.py -v
pytest --junitxml=evidence/pytest_ingestion_ordering_2026-09-13.xml \
  --cov=src/revops_sync --cov-report=xml:evidence/coverage_ingestion_ordering_2026-09-13.xml \
  --cov-report=term-missing --cov-fail-under=80
```

See [machine-generated receipts](../evidence/README.md). Tests use SQLite and synthetic snapshots; the optional PostgreSQL smoke was skipped. No new live provider or PostgreSQL concurrency result is claimed.
