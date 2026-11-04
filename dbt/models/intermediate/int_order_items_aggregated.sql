-- Collapses 112,650 line items to 99,441 orders. Orders with no line items are absent from the
-- result, so every consumer must LEFT JOIN.
select
    order_id,
    sum(price)                 as item_revenue,
    sum(freight_value)         as freight_value,
    count(*)                   as n_items,
    count(distinct seller_id)  as n_sellers,
    count(distinct product_id) as n_products,
    max(price)                 as max_item_price,
    min(price)                 as min_item_price
from {{ ref('stg_order_items') }}
group by order_id
