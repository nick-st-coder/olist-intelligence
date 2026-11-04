-- Every on-time order must have been delivered on or before the estimate.
select order_id
from {{ ref('int_order_delivery') }}
where is_late = false
  and order_delivered_customer_date > order_estimated_delivery_date
