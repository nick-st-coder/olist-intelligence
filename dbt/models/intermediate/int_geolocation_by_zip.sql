-- Collapses 1,000,163 sampled coordinates to one representative point per ZIP prefix.
--
-- Two rules, both from the data-understanding pass:
--   1. Coordinates outside Brazil's bounding box are dropped first. The raw table contains
--      latitudes up to 45.1 and longitudes down to -101.5, which are not in Brazil.
--   2. The median is used rather than the mean, because the table is a sample of observations
--      and the median is robust to the occasional wild coordinate that survives the filter.
with in_bounds as (
    select *
    from {{ ref('stg_geolocation') }}
    where geolocation_lat between -34.0 and 5.5
      and geolocation_lng between -74.0 and -34.0
)

select
    geolocation_zip_code_prefix,
    percentile_cont(0.5) within group (order by geolocation_lat) as lat,
    percentile_cont(0.5) within group (order by geolocation_lng) as lng,
    mode() within group (order by geolocation_state)             as state,
    count(*)                                                     as n_geo_rows
from in_bounds
group by geolocation_zip_code_prefix
