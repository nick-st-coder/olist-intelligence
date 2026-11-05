-- The order-level fact table must not lose orders that have no line items, no payment or no
-- review. 775 orders have no items, 1 has no payment and 768 have no review; all of them must
-- still be present with null facts rather than dropped.
select count(*) as n_orders_without_items
from {{ ref('fact_orders') }}
having sum(case when n_items is null then 1 else 0 end) <> 775
