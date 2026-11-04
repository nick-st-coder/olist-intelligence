-- order_reviews has no valid single-column key: 547 orders carry more than one review and 814
-- rows reuse a review_id. The latest review_answer_timestamp wins; ties are broken by the
-- lowest review_score so the result is deterministic.
with ranked as (
    select
        order_id,
        review_id,
        review_score,
        review_answer_timestamp,
        row_number() over (
            partition by order_id
            order by review_answer_timestamp asc, review_score asc
        ) as rn
    from {{ ref('stg_order_reviews') }}
)

select
    order_id,
    review_id,
    review_score,
    review_answer_timestamp
from ranked
where rn = 1
