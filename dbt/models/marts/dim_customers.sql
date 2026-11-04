-- One row per order-scoped customer_id (99,441 rows).
--
-- customer_id is order-scoped, so this dimension is 1:1 with orders. customer_unique_id is the
-- person-scoped identifier — 96,096 people, 2,997 of whom placed more than one order. Repeat
-- purchase analysis must group by customer_unique_id, not customer_id.
select
    c.customer_id,
    c.customer_unique_id,
    c.customer_city,
    c.customer_state,
    c.customer_zip_code_prefix,
    g.lat as customer_lat,
    g.lng as customer_lng
from {{ ref('stg_customers') }} c
left join {{ ref('int_geolocation_by_zip') }} g
    on c.customer_zip_code_prefix = g.geolocation_zip_code_prefix
