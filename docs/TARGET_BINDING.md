# Binding queued operations to acknowledged CRM records

## Problem and rule

Two changes can be staged before the first create is delivered. Both previews may say POST because no source observation contains the eventual CRM ID. Dispatching both as creates would duplicate the account.

After acquiring the ordering-aware worker claim, fresh and safe-retry dispatch examines **all acknowledged older items** for the same canonical account and provider. If they confirm one nonempty CRM ID, the effective operation is PATCH to that ID. A conflicting staged target, multiple acknowledged IDs, missing acknowledged IDs, invalid method/target combination, or mismatched payload canonical stamp raises `TargetBindingConflict` before HTTP or attempt increment. The claim is released without changing the item's outcome. Do not resolve this by blindly replacing IDs or replaying POST.

An earlier reconciled create can supply the ID only after the stream gate permits progression: its uncertainty hold needs an audited operator settlement release. Binding itself does not establish provider settlement.

## Persisted intent versus dispatch

The original `operation`, payload, checksum, sequence, and idempotency key remain unchanged. The token/lease-fenced database transition commits `dispatch_operation`, `dispatch_external_id`, and `binding_source_id` before the dispatch marker and HTTP. These fields are exposed in outbox reads. A bound operation retains this plan for safe retries and exact-object read-back after restart. A successful PATCH records the confirmed target ID on its acknowledged item so subsequent operations can use the same stream identity. Acknowledged replay makes no HTTP request.

The existing `target_external_id` field can be populated on acknowledgement, as before. The dispatch fields make the actual method/target explicit without making a staged POST checksum falsely describe a rewritten PATCH intent. The original idempotency key remains a local operation identifier, not a claim of provider-side deduplication.

## Migration and limits

Stop all older workers, run `alembic upgrade head` through revision `0005`, then restart current code. Existing rows receive null binding fields; no historical dispatch identity is invented. Do not downgrade active workers; removing bindings discards recovery context.

This is local stream identity propagation, not proof of global uniqueness. It does not validate a successful create's returned ID against live CRM identity, prevent external edits/deletions/merges, recover missing legacy acknowledgements automatically, update source-record observations, add a force-resolution route, or guarantee exactly-once side effects. A legacy uncertain item is read back conservatively rather than rebound into a new write. Administrative mutation of settled history is outside the append-only stream contract.

Tests use fake credentials and simulated HTTP for both providers, with SQLite persistence and migration checks. No new live CRM or PostgreSQL run occurred; see [receipts](../evidence/README.md).
