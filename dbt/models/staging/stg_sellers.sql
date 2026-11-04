select
    seller_id,
    cast(seller_zip_code_prefix as integer) as seller_zip_code_prefix,
    seller_city,
    seller_state
from {{ source('raw', 'sellers') }}
