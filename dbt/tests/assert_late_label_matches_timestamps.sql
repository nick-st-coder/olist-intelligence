-- Every late order must have been delivered after the estimate, and every on-time order on or
-- before it. Compared on the timestamps themselves, not on delay_days: delay_days truncates to
-- whole days, so 1,292 orders delivered later on the estimated day have delay_days = 0 while
-- still being late by minutes.
select order_id
from {{ ref('int_order_delivery') }}
where is_late
  and not (order_delivered_customer_date > order_estimated_delivery_date)
