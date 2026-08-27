# Architecture

## Invariants

The design is centered on five invariants:

1. One provider/external ID maps to one source row.
2. Only an exact normalized domain — one that isn't a known mail provider or website-builder host — can automatically join records across providers.
3. The canonical field value is explainable by a named ownership policy, except marketing consent, which is explainable by a fail-safe override that ignores ownership on disagreement.
4. An account with no stable cross-provider identity anchor never has a create staged into a provider it has no record in, so a delivered write can't echo back into an unbounded chain of duplicate creates (see `docs/CASE_STUDY.md`, "Failure mode demonstrated").
5. Each intended write has a fresh idempotency key per state *transition*, not per state — reverting to a payload staged two writes ago produces a new key, not a collision with the older one.
6. Previewing and delivering are different operations; the public service exposes only previewing.

## Transaction boundary

One reconciliation request performs these steps in one database transaction:

```text
validate records
  -> derive conservative identities
  -> upsert immutable-source snapshots by checksum
  -> resolve canonical fields
  -> append unseen conflict fingerprints
  -> append unseen outbox fingerprints
  -> persist a run receipt
  -> commit
```

A process failure before commit leaves none of the new state visible. Replaying the request reuses deterministic account IDs, source keys, conflict fingerprints, and outbox idempotency keys.

## Data model

- `canonical_accounts`: the resolved account state used by downstream systems
- `source_records`: latest observed payload per provider/external ID
- `conflict_records`: source disagreements plus chosen policy/value
- `outbox_items`: provider-shaped intended operations, preview-only by default
- `sync_runs`: reconciliation receipts and counts

## Identity trade-off

Exact-domain matching favors precision over recall. It will miss parent/subsidiary and rebrand relationships. That is intentional: a false CRM merge can corrupt attribution, routing, consent, and ownership. A production extension should add a human review queue for probable matches rather than silently widening the automatic rule.

## Scale path

The current service is synchronous for reviewer clarity, and single-writer: two concurrent reconciliation requests that both derive the same new canonical account id will both find it absent and both attempt to insert it, and the loser gets a database `IntegrityError` rather than a handled conflict. There is no advisory lock or `INSERT ... ON CONFLICT DO NOTHING` upsert covering that race. Higher volume would introduce webhook ingress, a durable queue, that concurrency-safe upsert, `SELECT ... FOR UPDATE SKIP LOCKED` outbox workers, provider-specific distributed rate limits, dead-letter replay, encrypted tenant secrets, and row-level tenant isolation. Those are identified extensions—not claims about the current repository.
