-- The late rate is 8.11% under the strict label definition. A value outside 7-9% means the
-- label logic broke: a changed comparison, a lost join, or a bad dedup rule.
select
    count(*) filter (where is_late) * 1.0 / count(*) as late_rate
from {{ ref('fact_orders') }}
where is_label_eligible
having count(*) filter (where is_late) * 1.0 / count(*) not between 0.07 and 0.09
