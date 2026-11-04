-- One row per line item. A second fact table at a different grain, because "which categories
-- generate the most revenue" needs line-item grain: an order with three items from two
-- categories must contribute to both.
--
-- Do not join this to fact_orders — the grains differ and the join multiplies rows.
select
    oi.order_id,
    oi.order_item_id,
    oi.product_id,
    oi.seller_id,
    oi.shipping_limit_date,
    oi.price,
    oi.freight_value,

    o.order_status,
    o.order_purchase_timestamp,

    coalesce(t.product_category_name_en, pr.product_category_name, 'unknown')
        as product_category,
    pr.product_weight_g,

    s.seller_state,
    c.customer_state,

    d.is_label_eligible,
    d.is_late

from {{ ref('stg_order_items') }} oi
left join {{ ref('stg_orders') }} o          on oi.order_id = o.order_id
left join {{ ref('stg_products') }} pr       on oi.product_id = pr.product_id
left join {{ ref('stg_category_translation') }} t on pr.product_category_name = t.product_category_name
left join {{ ref('stg_sellers') }} s         on oi.seller_id = s.seller_id
left join {{ ref('stg_customers') }} c       on o.customer_id = c.customer_id
left join {{ ref('int_order_delivery') }} d  on oi.order_id = d.order_id
