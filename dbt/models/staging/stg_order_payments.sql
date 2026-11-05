select
    order_id,
    cast(payment_sequential   as integer)      as payment_sequential,
    payment_type,
    cast(payment_installments as integer)      as payment_installments,
    cast(payment_value        as {{ dbt.type_numeric() }}) as payment_value
from {{ source('raw', 'order_payments') }}
