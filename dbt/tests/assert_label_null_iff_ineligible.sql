-- The label must be null exactly when the order is not label-eligible. Setting is_late to
-- FALSE for ineligible orders would silently turn 2,971 unknown outcomes into negatives.
select order_id
from {{ ref('fact_orders') }}
where (is_late is not null) <> is_label_eligible
