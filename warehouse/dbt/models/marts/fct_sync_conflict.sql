select
  conflict_id,
  account_id,
  field_name,
  hubspot_value,
  salesforce_value,
  chosen_value,
  chosen_source,
  policy,
  status,
  observed_at
from {{ ref('stg_conflict_records') }}
