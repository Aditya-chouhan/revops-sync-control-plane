select
  cast(id as string) as conflict_id,
  cast(canonical_account_id as string) as account_id,
  field_name,
  hubspot_value,
  salesforce_value,
  chosen_value,
  chosen_source,
  policy,
  status,
  cast(observed_at as timestamp) as observed_at
from {{ source('raw_revops', 'conflict_records') }}
