-- fact_orders must have exactly one row per order in the source.
-- A mismatch means a join multiplied rows (a one-to-many table joined before aggregating) or
-- dropped them (an INNER JOIN where a LEFT JOIN was required).
select count(*) as n_rows
from {{ ref('fact_orders') }}
having count(*) <> 99441
