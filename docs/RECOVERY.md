# Recovery from an ambiguous CRM write

## Failure being handled

The CRM may commit a company/account create or update, then the response is lost. Retrying that write blindly can create duplicates or replace a newer seller edit. An `Idempotency-Key` header alone is not a provider acceptance or deduplication guarantee.

## Persisted states

| State | Meaning | Reinvoking `deliver(item)` |
|---|---|---|
| `preview_only` | No write attempt yet | May dispatch after a fresh, unclaimed item is claimed |
| `dispatching` | Marker committed before request; outcome not acknowledged | Active claim blocks other workers; takeover is read-back only |
| `retryable_failed` | Explicit throttling or a pre-send connection/pool failure | Owner may retry while its claim is valid; expired-claim takeover is read-back only |
| `outcome_unknown` | Write may have landed and read-back has not confirmed desired state | Claim required for read-back; never automatically replay the write |
| `delivered` | Success response recorded; a create's object ID was recorded | No HTTP request |
| `reconciled` | Desired state observed on exactly one identified CRM object | No HTTP request |
| `delivery_rejected` | Non-retryable provider rejection | No automatic write |

Each dispatch increments `attempts`; read-back does not. `last_error` stores a sanitized category rather than tokens, authorization headers, or provider response bodies. The client commits transitions in the item's attached SQLAlchemy session: use a dedicated delivery session with no unrelated pending changes. A failure to commit the pre-dispatch marker prevents the write. A crash after that marker leaves a persisted state that is treated conservatively on restart.

## Worker ownership and deployment

Claims live in the shared database, not a Python lock. A conditional UPDATE permits acquisition only for an eligible item with no claim or an expired claim. Every owner receives a new `claim_token` and `claim_expires_at`. Expired-claim acquisition forces `outcome_unknown`, even if the old owner crashed before dispatch or during retry backoff. A fresh unclaimed `preview_only` or `retryable_failed` item can enter the write path; takeover cannot.

All state changes check the token and unexpired lease in the database. Cleanup checks the token so a stale holder cannot release a successor's claim. Active losing workers get `WorkerClaimUnavailable` without HTTP. An expired or replaced owner gets `WorkerClaimLost`; it must not retry the write outside this API. Claims remain held during safe retries/backoff and read-back. Normal completion releases the claim; process death leaves it until expiry. Lease updates happen at transitions, not in a background heartbeat. UTC clocks should be synchronized; premature expiry reduces availability but still does not permit a replacement write. An old HTTP call may remain in flight after expiry.

Deployment:

1. Stop all old/unfenced delivery processes. Mixed-version delivery is not safe.
2. Run `alembic upgrade head` to apply revision `0003`. Existing delivery outcomes remain unchanged; new claim columns start null. `AUTO_CREATE_SCHEMA` does not alter existing tables.
3. Restart claim-aware workers with `OUTBOX_CLAIM_SECONDS` sized above expected request/read-back latency (default 300 seconds).
4. Inspect `claim_expires_at` via the outbox API or database when diagnosing a busy item. Claim tokens are not exposed on the API; do not copy another worker's token to bypass ownership.
5. Treat busy/lost claims as stop conditions. Do not reset outcomes or clear active claims manually. Downgrading requires all workers stopped first.

## Read-back policy

- Existing object: GET the exact HubSpot company or Salesforce Account ID.
- Create with no known object ID: search for `gtm_canonical_id` in HubSpot or query `GTM_Canonical_ID__c` in Salesforce. Both custom fields must exist and be readable in the authorized environment.
- Require one result, the correct canonical stamp, the correct object ID when known, and all intended field keys and values. HubSpot string representations of booleans and integers are narrowly normalized; missing keys are not treated as null.
- A matching result records `reconciled`, the external object ID, and returns `desired_state_observed`. It does not assert causal attribution to the timed-out request.
- A search miss, a different value, duplicate results, malformed JSON, 404, 429, or read-back failure leaves `outcome_unknown`. Search indexing may lag: absence is not proof that a create never landed.

Request contracts follow [HubSpot company retrieval](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/companies/guide) and [Salesforce SOQL query](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-query.html). These paths are tested with simulated HTTP responses here, not newly executed against a live provider.

## Operator steps

1. Pause delivery for the affected account/provider. Keep the original item, sequence, checksum, payload, and idempotency key unchanged.
2. Inspect its `status`, `attempts`, `last_error`, `claim_expires_at`, and intended payload from the database. Let active ownership finish or expire; do not steal or clear its token. Check for newer staged items or seller edits before considering any write.
3. In an authorized portal/org, inspect the exact target object or search the canonical stamp. Confirm all fields and identity, not only company name or domain.
4. If indexing or availability may be delayed, retry read-back later using the same persisted uncertain item. Do not reset it to `preview_only`.
5. If one object matches every intended field, read-back acknowledges it automatically. If duplicates exist, stop for a reviewed identity resolution. Do not delete records automatically.
6. If newer seller edits differ, preserve them and reconcile current source observations through the ownership/consent policy. Do not replay the old payload over them.
7. If investigation establishes that a new write is necessary, record the investigation and obtain operator approval before deliberately staging a fresh operation. This repository does not provide an automated "force replay" or dead-letter resolution endpoint.

## Reproduce without credentials

```bash
pytest tests/test_timeout_recovery.py -v
pytest tests/test_worker_claims.py -v
pytest --junitxml=evidence/pytest_worker_claims_2026-09-13.xml \
  --cov=src/revops_sync --cov-report=xml:evidence/coverage_worker_claims_2026-09-13.xml \
  --cov-report=term-missing --cov-fail-under=80
```

The tests cover both providers, creates and updates, read/write timeouts and 503-after-success, matching/missing/different/duplicate state, read-back outages, invalid identity, malformed responses, persisted restart replay, acknowledgement replay, and disabled-write guards. Worker tests race two threads with independent sessions/connections on file-backed SQLite, fence stale acknowledgements and cleanup, block expired dispatch, and exercise migration upgrade/drift/downgrade without resetting unknown outcomes. Tokens are fake and HTTP is an in-memory transport; all data is synthetic.

## Remaining boundaries

Same-item exclusion is now database-backed and token-fenced, with read-only expired-claim takeover. It does not order different outbox items for one account/provider, cancel a provider request already in flight, or enforce a provider-side conditional update or per-field version contract. A separate newer item can still race another worker. Matching read-back does not prove the absence of unrelated duplicates outside the lookup result or establish exactly-once side effects. The new concurrency and migration evidence is SQLite-only; no new live-provider or PostgreSQL verification is claimed. No public delivery endpoint, background worker service, webhook activation, or paid/provider credential use was introduced.
