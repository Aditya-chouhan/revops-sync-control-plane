select
  account_id,
  domain,
  name,
  industry,
  employee_count,
  owner_email,
  lifecycle_stage,
  marketing_opt_in,
  resolution_basis,
  updated_at
from {{ ref('stg_canonical_accounts') }}
