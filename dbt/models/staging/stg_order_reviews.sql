-- No deduplication here: 547 orders carry more than one review. Collapsing to order grain is
-- the job of int_reviews_deduplicated.
select
    review_id,
    order_id,
    cast(review_score as integer) as review_score,
    review_comment_title,
    review_comment_message,
    cast(review_creation_date    as timestamp) as review_creation_date,
    cast(review_answer_timestamp as timestamp) as review_answer_timestamp
from {{ source('raw', 'order_reviews') }}
