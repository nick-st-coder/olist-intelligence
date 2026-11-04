select
    product_category_name,
    product_category_name_english as product_category_name_en
from {{ source('raw', 'category_translation') }}
