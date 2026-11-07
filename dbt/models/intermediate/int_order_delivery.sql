-- The delivery label and the purchase-time-knowable temporal features.
--
-- The comparison is strict (``>``): a delivery on the estimated day counts as late. That is the
-- canonical definition for this project and it produces 7,826 positives out of 96,470 eligible
-- orders (8.11%). Reading the date-only estimate as end-of-day would reclassify 1,292 orders
-- and move the rate to 6.77%.
--
-- ``is_late`` is NULL when the order is not label-eligible, never FALSE.
--
-- ``delay_days`` truncates towards zero, so an order delivered 59 minutes after the estimate
-- yields delay_days = 0 while still being late. 1,292 orders are in exactly that state.
-- ``delay_hours`` keeps the sub-day resolution for diagnostics.
--
-- Day counts use ``dbt.datediff`` rather than subtracting timestamps: Postgres returns an
-- interval from ``a - b``, BigQuery does not, and the macro emits the right form for each
-- warehouse. Note the argument order is (start, end, part) — the reverse of Postgres'
-- ``date_part('day', end - start)``, which is why the raw subtraction was not portable.
select
    order_id,
    order_purchase_timestamp,
    order_delivered_customer_date,
    order_estimated_delivery_date,

    (
        order_status = 'delivered'
        and order_delivered_customer_date is not null
        and order_estimated_delivery_date is not null
    ) as is_label_eligible,

    case
        when order_status = 'delivered'
         and order_delivered_customer_date is not null
         and order_estimated_delivery_date is not null
        then order_delivered_customer_date > order_estimated_delivery_date
    end as is_late,

    {{ dbt.datediff('order_purchase_timestamp', 'order_estimated_delivery_date', 'day') }}
        as estimated_lead_days,

    {{ dbt.datediff('order_purchase_timestamp', 'order_delivered_customer_date', 'day') }}
        as delivery_days,

    {{ dbt.datediff('order_estimated_delivery_date', 'order_delivered_customer_date', 'day') }}
        as delay_days,

    -- Seconds divided by 3600, not dbt.datediff(..., 'hour'): the hour part truncates towards
    -- zero, so an order delivered 40 minutes after the estimate yields 0 while still being
    -- late. 53 late orders are in exactly that state on BigQuery. Seconds keep the sub-hour
    -- resolution on both warehouses.
    {{ dbt.datediff('order_estimated_delivery_date', 'order_delivered_customer_date', 'second') }}
        / 3600.0 as delay_hours
from {{ ref('stg_orders') }}
