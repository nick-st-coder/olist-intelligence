-- The seller and product of the first line item, one row per order.
--
-- 1,278 orders have more than one seller. The primary seller is used for the distance feature
-- and the seller geography; the rest are counted in int_order_items_aggregated.n_sellers.
with ranked as (
    select
        order_id,
        seller_id,
        product_id,
        row_number() over (partition by order_id order by order_item_id) as rn
    from {{ ref('stg_order_items') }}
)

select
    order_id,
    seller_id  as primary_seller_id,
    product_id as primary_product_id
from ranked
where rn = 1
