# Account/provider stream ordering

## Dispatch rule

A stream is `(canonical_account_id, target_provider)`. Within an append-only stream, `sequence` defines order. An atomic conditional claim UPDATE includes a correlated NOT EXISTS check over **every** smaller sequence, not just the previous item. Each older item must be `delivered` or `reconciled`, have no claim token, and have `ordering_hold=false`. Otherwise `OutboxOrderingBlocked` is raised before HTTP or an attempt increment. Other accounts and providers are independent.

This assumes sequences are assigned monotonically and older operations are not inserted retroactively. It does not coalesce queued payloads, skip rejected items, or rewrite history.

## Why acknowledgement alone is insufficient

Dispatch commits an ordering hold before HTTP. A final 200/201/204 success clears it; explicit safe retry failures clear it but remain unacknowledged and therefore still block successors. Ambiguous failures, expired-claim takeover, and asynchronous 202 acceptance retain the hold. Matching read-back records `reconciled` but does **not** establish that an old remote operation cannot still apply. It therefore does not clear the hold. Token fencing also prevents a late expired worker from clearing a successor's result.

This deliberately trades availability for safety. Unknown or rejected predecessors cannot be silently bypassed. A separate explicit identity/cancellation/resolution workflow is not implemented.

## Trusted operator release

Only after establishing that old workers and remote operations cannot still apply an older payload, an authorized operator may call the internal utility in a clean dedicated session:

```python
from revops_sync.ordering import release_ordering_hold

# session is a clean, dedicated SQLAlchemy Session.
# Values must identify the actual reviewer and a defensible investigation.
resolution_id = release_ordering_hold(
    session,
    item_id,
    reviewer=reviewer_identity,
    reason=investigation_record,
    provider_settled=True,
)
```

The item must already be acknowledged, unclaimed, and held. The utility clears the hold and appends an `ordering_resolutions` record atomically; an audit failure rolls both back. It sends no HTTP and cannot force an acknowledgement. `provider_settled=True` is an operator attestation, not independently verified provider evidence. The utility is not exposed through a public route and does not supply authentication or a provider-settlement oracle.

## Deployment and proof boundaries

Stop all older workers before migrating to revision `0004` and restarting. The migration holds legacy `dispatching`, `outcome_unknown`, and `reconciled` items conservatively. Do not downgrade with workers running; downgrade removes holds and their audit table.

Tests use independent sessions/connections on file-backed SQLite, two threads, fake tokens, and in-memory HTTP transports. They cover full-prefix blocking, separate streams, different-item races, late writes after takeover, audited release/rollback, and migration backfill. No new PostgreSQL or live CRM operation was run. This does not cancel remote requests, implement provider-side ETags/conditional writes, prevent external seller edits, bind subsequent creates to earlier acknowledged object IDs, guarantee exactly-once side effects, or introduce a background delivery service.
