-- A contiguous calendar spine covering the real data range (2016-09-04 to 2018-10-31).
--
-- Power BI time intelligence needs a real date table with no gaps. It will not work against a
-- date column alone.
with spine as (
    select generate_series(
        '2016-01-01'::date,
        '2018-12-31'::date,
        interval '1 day'
    )::date as date_day
)

select
    date_day,
    extract(year    from date_day)::integer as year,
    extract(quarter from date_day)::integer as quarter,
    extract(month   from date_day)::integer as month,
    to_char(date_day, 'Mon')                as month_name,
    to_char(date_day, 'YYYY-MM')            as year_month,
    extract(week    from date_day)::integer as week,
    extract(isodow  from date_day)::integer as day_of_week,
    to_char(date_day, 'Dy')                 as day_name,
    extract(isodow  from date_day) >= 6     as is_weekend
from spine
