select
    order_id,
    cast(payment_sequential   as integer)      as payment_sequential,
    payment_type,
    cast(payment_installments as integer)      as payment_installments,
    cast(payment_value        as numeric(12, 2)) as payment_value
from {{ source('raw', 'order_payments') }}
