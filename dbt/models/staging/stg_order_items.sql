select
    order_id,
    cast(order_item_id as integer)      as order_item_id,
    product_id,
    seller_id,
    cast(shipping_limit_date as timestamp) as shipping_limit_date,
    cast(price         as numeric(12, 2)) as price,
    cast(freight_value as numeric(12, 2)) as freight_value
from {{ source('raw', 'order_items') }}
