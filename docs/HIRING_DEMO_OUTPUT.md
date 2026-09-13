# Five-minute CRM control-plane demo

**Synthetic accounts and simulated CRM responses. No live CRM writes or revenue results.**

Run `python -m revops_sync.demo` after installing the project dependencies.
Use `python -m revops_sync.demo --json` for the machine-readable receipt.

## 1. Conflicting CRM observations

Unclear ownership and accidental opt-in.

Verified by execution:

```json
{
  "canonical_name": "Salesforce account name",
  "conflicts": [
    {
      "chosen": false,
      "field": "marketing_opt_in",
      "hubspot": true,
      "policy": "consent_restrictive_wins",
      "salesforce": false
    },
    {
      "chosen": "Salesforce account name",
      "field": "name",
      "hubspot": "HubSpot account name",
      "policy": "salesforce_preferred",
      "salesforce": "Salesforce account name"
    }
  ],
  "marketing_opt_in": false,
  "source_rows_inserted": 2
}
```

## 2. Duplicate, stale and conflicting input

Duplicate actions and stale identity/consent.

Verified by execution:

```json
{
  "additional_previews": 0,
  "canonical_name_unchanged": "Salesforce account name",
  "duplicates_unchanged": 2,
  "equal_version_conflict_rejected": true,
  "marketing_opt_in": false,
  "stale_records_ignored": 1
}
```

## 3. Accepted create, lost response, read-back recovery

Blind retry could create a second account.

Verified by execution:

```json
{
  "ordering_hold": true,
  "persisted_status": "reconciled",
  "simulated_http": [
    {
      "method": "POST",
      "path": "/services/data/v65.0/sobjects/Account"
    },
    {
      "method": "GET",
      "path": "/services/data/v65.0/query"
    }
  ],
  "synthetic_external_id": "synthetic-crm-account-001",
  "write_attempts": 1
}
```

## 4. Newer update waits after restart

A late older write could overwrite newer values.

Verified by execution:

```json
{
  "additional_simulated_http": 0,
  "newer_blocked": true,
  "newer_write_attempts": 0,
  "persisted_older_hold": true
}
```

## 5. Audited operator recovery and bound update

Unaudited bypass or a duplicate create.

Verified by execution:

```json
{
  "audit_action": "release_hold_provider_settled",
  "binding_source_matches_older_item": true,
  "dispatch_operation": "PATCH",
  "older_hold_released": true,
  "original_intent_preserved": true,
  "persisted_status": "delivered",
  "simulated_crm_name": "New account v2",
  "simulated_reason": "SIMULATED: mock handler completed; no remote operation remains in flight.",
  "simulated_reviewer": "synthetic-demo-operator",
  "staged_operation": "POST",
  "synthetic_dispatch_id": "synthetic-crm-account-001"
}
```

## Complete simulated request trace

```json
[
  {
    "method": "POST",
    "path": "/services/data/v65.0/sobjects/Account"
  },
  {
    "method": "GET",
    "path": "/services/data/v65.0/query"
  },
  {
    "method": "PATCH",
    "path": "/services/data/v65.0/sobjects/Account/synthetic-crm-account-001"
  }
]
```

Five scenario checks passed. Real external writes: **0**.

## Sample inputs

```json
[
  {
    "provider": "hubspot",
    "external_id": "synthetic-hs-001",
    "name": "HubSpot account name",
    "domain": "conflict.example",
    "industry": null,
    "employee_count": null,
    "owner_email": null,
    "lifecycle_stage": null,
    "marketing_opt_in": true,
    "source_updated_at": "2026-09-10T12:00:00Z",
    "canonical_id_hint": null
  },
  {
    "provider": "salesforce",
    "external_id": "synthetic-sf-001",
    "name": "Salesforce account name",
    "domain": "conflict.example",
    "industry": null,
    "employee_count": null,
    "owner_email": null,
    "lifecycle_stage": null,
    "marketing_opt_in": false,
    "source_updated_at": "2026-09-10T12:00:00Z",
    "canonical_id_hint": null
  },
  {
    "provider": "hubspot",
    "external_id": "synthetic-hs-001",
    "name": "HubSpot account name",
    "domain": "conflict.example",
    "industry": null,
    "employee_count": null,
    "owner_email": null,
    "lifecycle_stage": null,
    "marketing_opt_in": true,
    "source_updated_at": "2026-09-10T12:00:00Z",
    "canonical_id_hint": null
  },
  {
    "provider": "salesforce",
    "external_id": "synthetic-sf-001",
    "name": "Salesforce account name",
    "domain": "conflict.example",
    "industry": null,
    "employee_count": null,
    "owner_email": null,
    "lifecycle_stage": null,
    "marketing_opt_in": false,
    "source_updated_at": "2026-09-10T12:00:00Z",
    "canonical_id_hint": null
  },
  {
    "provider": "salesforce",
    "external_id": "synthetic-sf-001",
    "name": "Stale account name",
    "domain": "stale-domain.example",
    "industry": null,
    "employee_count": null,
    "owner_email": null,
    "lifecycle_stage": null,
    "marketing_opt_in": true,
    "source_updated_at": "2026-09-09T12:00:00Z",
    "canonical_id_hint": null
  },
  {
    "provider": "salesforce",
    "external_id": "synthetic-sf-001",
    "name": "Equal-version conflicting name",
    "domain": "conflict.example",
    "industry": null,
    "employee_count": null,
    "owner_email": null,
    "lifecycle_stage": null,
    "marketing_opt_in": false,
    "source_updated_at": "2026-09-10T12:00:00Z",
    "canonical_id_hint": null
  },
  {
    "provider": "hubspot",
    "external_id": "synthetic-hs-new",
    "name": "New account v1",
    "domain": "new-account.example",
    "industry": null,
    "employee_count": null,
    "owner_email": null,
    "lifecycle_stage": null,
    "marketing_opt_in": false,
    "source_updated_at": "2026-09-10T12:00:00Z",
    "canonical_id_hint": null
  },
  {
    "provider": "hubspot",
    "external_id": "synthetic-hs-new",
    "name": "New account v2",
    "domain": "new-account.example",
    "industry": null,
    "employee_count": null,
    "owner_email": null,
    "lifecycle_stage": null,
    "marketing_opt_in": false,
    "source_updated_at": "2026-09-11T12:00:00Z",
    "canonical_id_hint": null
  }
]
```

## What this does not prove

- All accounts, CRM IDs, CRM responses and operator attestations are synthetic.
- No network transport, live CRM credentials, paid APIs or customer data are used.
- This is serial SQLite execution, not PostgreSQL/concurrent-ingestion evidence.
- No signed webhook receiver, external-edit conditional writes or exactly-once guarantee.
- Matching read-back is not provider-settlement proof; the operator release is simulated.
- No pipeline, revenue, reply-rate, seller adoption or time-saved claim is made.
