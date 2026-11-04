-- Left unaggregated on purpose: the raw 1,000,163 rows stay queryable so the median
-- aggregation in int_geolocation_by_zip can be audited against them.
select
    cast(geolocation_zip_code_prefix as integer) as geolocation_zip_code_prefix,
    cast(geolocation_lat as numeric(10, 6))      as geolocation_lat,
    cast(geolocation_lng as numeric(10, 6))      as geolocation_lng,
    geolocation_city,
    geolocation_state
from {{ source('raw', 'geolocation') }}
