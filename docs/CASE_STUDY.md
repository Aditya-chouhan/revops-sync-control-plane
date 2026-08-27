# Case study: making cross-CRM decisions auditable

## Problem

When HubSpot and Salesforce both contain an account, a two-way sync must answer three difficult questions: are these records the same company, which system owns each field, and can a failed job be safely replayed? A connector that simply copies the newest payload can create loops, overwrite consent, or merge unrelated same-name companies.

## Build

I built a small control plane around the integration rather than treating the APIs as the system:

- conservative, deterministic identity resolution
- explicit Salesforce- and HubSpot-owned field groups
- a persistent ledger for every non-null disagreement
- canonical records separated from provider snapshots
- idempotent, provider-shaped outbox previews
- guarded HTTP delivery with bounded retry behavior
- PostgreSQL migrations, service health, metrics, tests, and container verification

## Measured result

On the committed synthetic fixture, one run reconciles six source records into four canonical accounts, records eight policy-resolved conflicts, creates six intended write previews, and performs zero external writes. A replay creates no duplicate source, conflict, or outbox rows.

These are deterministic software-test results. They are not customer records, a live CRM sync, hours saved, pipeline, or revenue.

## Failure mode demonstrated

Two records share the name “Northstar Labs” but have no usable domain. The control plane refuses to merge them. This lowers automatic match recall while protecting against silent false positives—the safer default for CRM identity.

Not-merging on its own isn't sufficient, though. The first version of this control plane refused to merge the two Northstar records into one account, then staged a write to *create* each one in the provider it wasn't already in — because an isolated, no-domain canonical account still got treated as a normal account for outbox purposes. Follow that through: if the create is ever delivered, the new record comes back from the target CRM with no domain of its own, gets isolated as a brand-new canonical account (correctly — nothing tied it back to where it came from), and *that* account stages a create of its own. Every sync cycle would double the no-domain population in both CRMs — the exact "naive two-way sync creates loops" failure this project's own opening paragraph names.

The fix is a second refusal, not just the first one: an account with no domain (and no confirmed re-identification — see below) does not stage a create into a provider it has no record in. It's the same conservative posture applied symmetrically to writing, not just merging. Two isolated Northstar accounts is still the outcome; six outbox previews instead of eight is the visible effect (`data/synthetic/offline_reconciliation_receipt_2026-08-27.json`).

That second refusal would make legitimate cross-provider propagation impossible for no-domain accounts forever, which is also wrong. So there's a narrow, explicit door back in: this control plane stamps its own canonical id into every payload it writes (`gtm_canonical_id` / `GTM_Canonical_ID__c`). If a source system ever echoes a record back carrying that id, and the id actually resolves to an existing account, the record binds to it directly instead of being isolated as a new one — identity re-established by the id the system itself wrote, not by silently trusting a caller-supplied claim it never verified.

The second named risk — "overwrite consent" — had its own version of the same problem. Field ownership here is HubSpot-preferred for `marketing_opt_in`, and the fixture has HubSpot recording an opt-in a day before Salesforce records an opt-out for the same account. Plain field-ownership policy picks HubSpot's value because it's the preferred source, full stop — which means it writes the *older opt-in* back over the *newer opt-out*. Ownership policy is the wrong governing rule for consent specifically: it's the one field where being wrong in the permissive direction is the failure that matters, regardless of which system nominally owns it or which record is newer. The fix is a fail-safe override that applies only to this field — when hubspot and salesforce disagree, the restrictive value wins outright, before ownership or recency are even consulted.

## What I would add with an authorized environment

1. ~~deploy the documented custom properties/fields in sandbox portals~~ — **done for HubSpot, 2026-08-27**: `scripts/hubspot_live_sync.py` created all three `gtm_*` properties and synced the fixture accounts against a real free dev/test portal, with committed real object IDs and a proven-by-rerun idempotency check (`evidence/hubspot_live_sync_2026-08-27.json`). It also surfaced a real gap the fixture never would have: HubSpot's `industry` property is a closed enumeration, not free text — see `docs/INTEGRATIONS.md`. Salesforce fields are deployed and verified separately in `salesforce-gtm-org`; still open here is doing the equivalent live sync against that Salesforce org instead of only generating previews for it;
2. validate provider-native webhook signatures;
3. add queue-backed outbox workers and dead-letter replay;
4. run shadow comparison against an existing integration before enabling writes;
5. measure duplicate rate, conflict rate, false-merge review outcomes, sync latency, and delivery failures;
6. replicate audit tables to BigQuery and run the included dbt models.

Until those steps happen, this repository claims a tested integration control plane and nothing more.
