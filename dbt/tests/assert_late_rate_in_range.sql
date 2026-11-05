-- The late rate is 8.11% under the strict label definition. A value outside 7-9% means the
-- label logic broke: a changed comparison, a lost join, or a bad dedup rule.
--
-- The conditional count is written as sum(case ...) rather than count(*) filter (where ...),
-- which BigQuery does not support.
select
    sum(case when is_late then 1 else 0 end) * 1.0 / count(*) as late_rate
from {{ ref('fact_orders') }}
where is_label_eligible
having sum(case when is_late then 1 else 0 end) * 1.0 / count(*) not between 0.07 and 0.09
