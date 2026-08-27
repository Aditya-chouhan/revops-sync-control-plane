# BigQuery deployment path

## Status: prepared, not deployed

The `warehouse/` directory contains a minimal Terraform and dbt path for replicating control-plane audit tables into BigQuery. It has not been applied because this public build has no GCP project, billing account, service-account key, or permission to create cloud resources.

That boundary matters: infrastructure code is not evidence of a running warehouse.

## Intended topology

```text
PostgreSQL canonical/source/conflict/run tables
  -> authorized CDC or scheduled export
  -> BigQuery raw_revops dataset
  -> dbt staging models
  -> dim_account + fct_sync_conflict
  -> data-quality tests and BI consumers
```

The extraction/CDC mechanism is intentionally not invented. A real environment could use Datastream, an approved ELT platform, or a scheduled export depending on data volume and security constraints.

## Credentialed steps

1. Select an existing GCP project with billing enabled.
2. Authenticate through the organization's approved identity flow; do not commit a service-account key.
3. Copy `warehouse/terraform/terraform.tfvars.example` to an ignored `terraform.tfvars` and set the real project ID and region.
4. Run `terraform init`, `terraform plan`, review the exact resources, then `terraform apply`.
5. Configure the approved CDC/export to populate the declared raw tables.
6. Set `GCP_PROJECT_ID` and use the committed secret-free OAuth profile, or copy `profiles.example.yml` to the approved user-level dbt profiles directory.
7. Run `dbt debug`, `dbt build`, and capture the build receipt.
8. Only then change the portfolio wording from “deployment path” to “deployed warehouse.”

## Evidence needed before a live claim

- Terraform apply output with sensitive values removed
- BigQuery dataset/table metadata
- row counts reconciled to the source
- `dbt build` output and test results
- cost and freshness observations over a disclosed time window
- explicit identification of synthetic versus authorized real CRM data
