-- A late order must have a positive delay_hours, and an on-time order a non-positive one.
--
-- This guards the sub-day resolution: computing delay_hours with a truncated hour part makes
-- an order delivered 40 minutes after the estimate report 0 while still being late. 53 late
-- orders were in that state on BigQuery before the column was derived from seconds.
select order_id
from {{ ref('int_order_delivery') }}
where is_late
  and delay_hours <= 0
