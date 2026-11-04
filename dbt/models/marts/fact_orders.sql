-- One row per order. The spine of the star schema.
--
-- Every join is LEFT. An INNER JOIN would silently drop 775 orders with no line items, 1 with
-- no payment, 768 with no review and 278 customers whose ZIP prefix is absent from
-- geolocation. The row count must stay at 99,441 — assert_fact_orders_row_count enforces it.
with orders as (
    select * from {{ ref('stg_orders') }}
),

items as (
    select * from {{ ref('int_order_items_aggregated') }}
),

payments as (
    select * from {{ ref('int_payments_aggregated') }}
),

reviews as (
    select * from {{ ref('int_reviews_deduplicated') }}
),

delivery as (
    select * from {{ ref('int_order_delivery') }}
),

customers as (
    select * from {{ ref('stg_customers') }}
),

primary_item as (
    select * from {{ ref('int_primary_item') }}
),

sellers as (
    select * from {{ ref('stg_sellers') }}
),

products as (
    select * from {{ ref('stg_products') }}
),

translation as (
    select * from {{ ref('stg_category_translation') }}
),

geo as (
    select * from {{ ref('int_geolocation_by_zip') }}
)

select
    o.order_id,
    o.customer_id,
    o.order_status,
    o.order_purchase_timestamp,
    o.order_estimated_delivery_date,

    -- aggregated facts
    i.item_revenue,
    i.freight_value,
    i.n_items,
    i.n_sellers,
    i.n_products,
    i.max_item_price,
    i.min_item_price,
    p.payment_value,
    p.n_payment_lines,
    p.max_installments,
    p.n_zero_value_lines,
    r.review_score,

    -- customer geography
    c.customer_unique_id,
    c.customer_city,
    c.customer_state,
    c.customer_zip_code_prefix,
    cg.lat as customer_lat,
    cg.lng as customer_lng,

    -- seller geography (primary seller only)
    pi.primary_seller_id,
    s.seller_city,
    s.seller_state,
    sg.lat as seller_lat,
    sg.lng as seller_lng,

    -- product (primary item only)
    pi.primary_product_id,
    coalesce(t.product_category_name_en, pr.product_category_name, 'unknown')
        as product_category,
    pr.product_weight_g,

    -- label and temporal features
    d.is_label_eligible,
    d.is_late,
    d.estimated_lead_days,
    d.delivery_days,
    d.delay_days,
    d.delay_hours

from orders o
left join items        i  on o.order_id = i.order_id
left join payments     p  on o.order_id = p.order_id
left join reviews      r  on o.order_id = r.order_id
left join delivery     d  on o.order_id = d.order_id
left join customers    c  on o.customer_id = c.customer_id
left join primary_item pi on o.order_id = pi.order_id
left join sellers      s  on pi.primary_seller_id = s.seller_id
left join products     pr on pi.primary_product_id = pr.product_id
left join translation  t  on pr.product_category_name = t.product_category_name
left join geo          cg on c.customer_zip_code_prefix = cg.geolocation_zip_code_prefix
left join geo          sg on s.seller_zip_code_prefix = sg.geolocation_zip_code_prefix
