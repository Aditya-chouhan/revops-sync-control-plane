# Recovery from an ambiguous CRM write

## Failure being handled

The CRM may commit a company/account create or update, then the response is lost. Retrying that write blindly can create duplicates or replace a newer seller edit. An `Idempotency-Key` header alone is not a provider acceptance or deduplication guarantee.

## Persisted states

| State | Meaning | Reinvoking `deliver(item)` |
|---|---|---|
| `preview_only` | No write attempt yet | May dispatch if all write guards permit |
| `dispatching` | Marker committed before request; outcome not acknowledged | Read-back only |
| `retryable_failed` | Explicit throttling or a pre-send connection/pool failure | Bounded retry permitted |
| `outcome_unknown` | Write may have landed and read-back has not confirmed desired state | Read-back only; never automatically replay the write |
| `delivered` | Success response recorded; a create's object ID was recorded | No HTTP request |
| `reconciled` | Desired state observed on exactly one identified CRM object | No HTTP request |
| `delivery_rejected` | Non-retryable provider rejection | No automatic write |

Each dispatch increments `attempts`; read-back does not. `last_error` stores a sanitized category rather than tokens, authorization headers, or provider response bodies. The client commits transitions in the item's attached SQLAlchemy session: use a dedicated delivery session with no unrelated pending changes. A failure to commit the pre-dispatch marker prevents the write. A crash after that marker leaves a persisted state that is treated conservatively on restart.

## Read-back policy

- Existing object: GET the exact HubSpot company or Salesforce Account ID.
- Create with no known object ID: search for `gtm_canonical_id` in HubSpot or query `GTM_Canonical_ID__c` in Salesforce. Both custom fields must exist and be readable in the authorized environment.
- Require one result, the correct canonical stamp, the correct object ID when known, and all intended field keys and values. HubSpot string representations of booleans and integers are narrowly normalized; missing keys are not treated as null.
- A matching result records `reconciled`, the external object ID, and returns `desired_state_observed`. It does not assert causal attribution to the timed-out request.
- A search miss, a different value, duplicate results, malformed JSON, 404, 429, or read-back failure leaves `outcome_unknown`. Search indexing may lag: absence is not proof that a create never landed.

Request contracts follow [HubSpot company retrieval](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/companies/guide) and [Salesforce SOQL query](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-query.html). These paths are tested with simulated HTTP responses here, not newly executed against a live provider.

## Operator steps

1. Pause delivery for the affected account/provider. Keep the original item, sequence, checksum, payload, and idempotency key unchanged.
2. Inspect its `status`, `attempts`, `last_error`, and intended payload from the database. Check for newer staged items or seller edits before considering any write.
3. In an authorized portal/org, inspect the exact target object or search the canonical stamp. Confirm all fields and identity, not only company name or domain.
4. If indexing or availability may be delayed, retry read-back later using the same persisted uncertain item. Do not reset it to `preview_only`.
5. If one object matches every intended field, read-back acknowledges it automatically. If duplicates exist, stop for a reviewed identity resolution. Do not delete records automatically.
6. If newer seller edits differ, preserve them and reconcile current source observations through the ownership/consent policy. Do not replay the old payload over them.
7. If investigation establishes that a new write is necessary, record the investigation and obtain operator approval before deliberately staging a fresh operation. This repository does not provide an automated "force replay" or dead-letter resolution endpoint.

## Reproduce without credentials

```bash
pytest tests/test_timeout_recovery.py -v
pytest --junitxml=evidence/pytest_2026-09-13.xml \
  --cov=src/revops_sync --cov-report=xml:evidence/coverage_2026-09-13.xml \
  --cov-report=term-missing --cov-fail-under=80
```

The tests cover both providers, creates and updates, read/write timeouts and 503-after-success, matching/missing/different/duplicate state, read-back outages, invalid identity, malformed responses, persisted restart replay, acknowledgement replay, and disabled-write guards. Tokens are fake and HTTP is an in-memory transport; all data is synthetic.

## Remaining boundaries

This is sequential recovery, not concurrent-worker exclusion. No leases, distributed lock, compare-and-swap worker claim, provider-side conditional update, or per-field version contract has been added. A separate newer outbox item can still race another worker. Matching read-back does not prove the absence of unrelated duplicates outside the lookup result or establish exactly-once side effects. Durable states have no new live-provider or PostgreSQL verification from this run. No public delivery endpoint, worker, webhook activation, or paid/provider credential use was introduced.
