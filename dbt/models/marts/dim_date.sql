-- A contiguous calendar spine covering the real data range (2016-09-04 to 2018-10-31).
--
-- Power BI time intelligence needs a real date table with no gaps. It will not work against a
-- date column alone.
--
-- The spine comes from ``dbt.date_spine`` rather than ``generate_series``, which is
-- Postgres-only. ``day_of_week`` is normalised to 0 = Monday so the column means the same thing
-- on both warehouses: Postgres' ``isodow`` is already 1 = Monday, while BigQuery's
-- ``dayofweek`` is 1 = Sunday, so the BigQuery branch subtracts one.
with spine as (
    {{ dbt.date_spine('day', "'2016-01-01'", "'2018-12-31'") }}
),

normalised as (
    select
        cast(date_day as date) as date_day,
        extract(year    from date_day) as year,
        extract(quarter from date_day) as quarter,
        extract(month   from date_day) as month,
        extract(week    from date_day) as week,
        {% if target.type == 'bigquery' %}
        extract(dayofweek from date_day) - 1 as day_of_week
        {% else %}
        extract(isodow from date_day) - 1 as day_of_week
        {% endif %}
    from spine
)

select
    date_day,
    year,
    quarter,
    month,
    week,
    day_of_week,
    day_of_week >= 5 as is_weekend
from normalised
