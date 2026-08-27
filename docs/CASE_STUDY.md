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

On the committed synthetic fixture, one run reconciles six source records into four canonical accounts, records eight policy-resolved conflicts, creates eight intended write previews, and performs zero external writes. A replay creates no duplicate source, conflict, or outbox rows.

These are deterministic software-test results. They are not customer records, a live CRM sync, hours saved, pipeline, or revenue.

## Failure mode demonstrated

Two records share the name “Northstar Labs” but have no usable domain. The control plane refuses to merge them. This lowers automatic match recall while protecting against silent false positives—the safer default for CRM identity.

## What I would add with an authorized environment

1. deploy the documented custom properties/fields in sandbox portals;
2. validate provider-native webhook signatures;
3. add queue-backed outbox workers and dead-letter replay;
4. run shadow comparison against an existing integration before enabling writes;
5. measure duplicate rate, conflict rate, false-merge review outcomes, sync latency, and delivery failures;
6. replicate audit tables to BigQuery and run the included dbt models.

Until those steps happen, this repository claims a tested integration control plane and nothing more.
