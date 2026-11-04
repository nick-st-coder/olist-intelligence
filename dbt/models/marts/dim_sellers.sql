-- One row per seller (3,095 rows).
select
    s.seller_id,
    s.seller_city,
    s.seller_state,
    s.seller_zip_code_prefix,
    g.lat as seller_lat,
    g.lng as seller_lng
from {{ ref('stg_sellers') }} s
left join {{ ref('int_geolocation_by_zip') }} g
    on s.seller_zip_code_prefix = g.geolocation_zip_code_prefix
