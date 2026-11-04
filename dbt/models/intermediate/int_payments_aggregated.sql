-- Collapses 103,886 payment lines to 99,441 orders. Zero-value voucher lines are kept in the
-- sum (they contribute nothing) but counted separately so revenue analysis can exclude them.
select
    order_id,
    sum(payment_value)                          as payment_value,
    count(*)                                    as n_payment_lines,
    max(payment_installments)                   as max_installments,
    count(*) filter (where payment_value <= 0)  as n_zero_value_lines
from {{ ref('stg_order_payments') }}
group by order_id
