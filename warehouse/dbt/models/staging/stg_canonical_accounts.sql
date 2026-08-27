select
  cast(id as string) as account_id,
  lower(domain) as domain,
  name,
  industry,
  cast(employee_count as int64) as employee_count,
  lower(owner_email) as owner_email,
  lifecycle_stage,
  cast(marketing_opt_in as bool) as marketing_opt_in,
  resolution_basis,
  cast(created_at as timestamp) as created_at,
  cast(updated_at as timestamp) as updated_at
from {{ source('raw_revops', 'canonical_accounts') }}
