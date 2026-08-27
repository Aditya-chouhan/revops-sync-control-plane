# Integration contract

## HubSpot

The preview maps the canonical record to a company `properties` object. Standard-like fields include `domain`, `name`, `industry`, `numberofemployees`, and `lifecyclestage`. The `gtm_*` fields are intentional custom-property placeholders and must exist in the target portal before live delivery.

The delivery boundary uses a private-app bearer token supplied at runtime. No OAuth flow, portal ID, private-app token, or successful write is included in the repository.

## Salesforce

The preview maps to Account fields. `Name`, `Website`, `Industry`, and `NumberOfEmployees` are standard-shaped. Fields ending in `__c` are explicit custom-field contracts and must be deployed in the target org before live delivery.

The REST API version is configuration, not a timeless claim. A real deployment should set `SALESFORCE_API_VERSION` to a version supported by its org.

## Loop prevention

Source precedence is field-specific. A Salesforce-owned field observed from HubSpot cannot overwrite the canonical Salesforce value merely because it arrived later. Conversely, HubSpot owns lifecycle fields in this demonstration; marketing consent is the one field ownership doesn't govern at all — see below.

Field-level ownership isn't sufficient on its own to prevent a loop, though. An account with no domain has no identity anchor another system can independently arrive at, so staging a *create* into a provider that has never seen it is unsafe on its own terms: if that write is ever delivered, the created record comes back with no domain either, gets isolated as a brand-new canonical account, and stages a create of its own — an unbounded chain, one new duplicate per sync cycle, in both directions. `ReconciliationService._create_outbox_previews` refuses to stage that create at all for a no-domain account, unless identity has been re-established via the `canonical_id_hint` echo described below. The full trace is in `docs/CASE_STUDY.md`, "Failure mode demonstrated."

Each outbox item is keyed by a monotonic sequence per (account, provider), not by a checksum of its payload alone. A payload checksum is still recorded and compared, but only against the immediately *preceding* item for that pair — enough to skip staging a duplicate of the last thing written, without treating "this state was staged once before, further back" as a reason to silently drop a new write. A value that reverts to something staged two writes ago gets its own sequence number and its own outbox row.

## Re-establishing identity across a write

Every outbox payload carries this control plane's own canonical id (`gtm_canonical_id` for HubSpot, `GTM_Canonical_ID__c` for Salesforce). If a source system later echoes a record back carrying that id — via `SourceAccount.canonical_id_hint` — and the id resolves to an account that actually exists, `identity.canonical_identity` binds the record to it directly instead of isolating a new one. The hint is only ever trusted against a live existence check the caller supplies (`account_exists`); an unverified or stale id is treated the same as no hint at all.

## Marketing consent

`marketing_opt_in` is deliberately carved out of the ownership table above. When HubSpot and Salesforce both have a value and disagree, the restrictive (opt-out) value wins outright — not the preferred source, not the newer record. Consent is the one field where the safe failure mode and the permissive failure mode are not symmetric, so it doesn't get a "preferred source" at all when the two sides actually conflict.

## Secrets and writes

`.env.example` contains empty placeholders only. Live delivery requires a global switch, provider switch, and provider credentials. The FastAPI router does not expose the delivery method.

This is a safe public integration boundary, not proof that Aditya has access to a private HubSpot portal or Salesforce production org.
