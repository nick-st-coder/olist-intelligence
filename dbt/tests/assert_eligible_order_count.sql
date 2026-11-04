-- The label-eligible population is fixed: 96,470 of 99,441 orders. A change here means the
-- eligibility rule moved, which invalidates every metric reported against the model.
select count(*) as n_eligible
from {{ ref('fact_orders') }}
where is_label_eligible
having count(*) <> 96470
