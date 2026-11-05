-- Collapses 1,000,163 sampled coordinates to one representative point per ZIP prefix.
--
-- Two rules, both from the data-understanding pass:
--   1. Coordinates outside Brazil's bounding box are dropped first. The raw table contains
--      latitudes up to 45.1 and longitudes down to -101.5, which are not in Brazil.
--   2. The median is used rather than the mean, because the table is a sample of observations
--      and the median is robust to the occasional wild coordinate that survives the filter.
--
-- The median and the modal state are computed with window functions rather than Postgres'
-- ``percentile_cont(...) within group`` and ``mode() within group``, which BigQuery does not
-- support. ``row_number`` over the ordered values picks the middle row per prefix, and the
-- state is ranked by frequency with ties broken alphabetically so the result is deterministic.
with in_bounds as (
    select *
    from {{ ref('stg_geolocation') }}
    where geolocation_lat between -34.0 and 5.5
      and geolocation_lng between -74.0 and -34.0
),

ranked as (
    select
        geolocation_zip_code_prefix,
        geolocation_lat,
        geolocation_lng,
        geolocation_state,
        row_number() over (
            partition by geolocation_zip_code_prefix order by geolocation_lat
        ) as rn_lat,
        row_number() over (
            partition by geolocation_zip_code_prefix order by geolocation_lng
        ) as rn_lng,
        count(*) over (partition by geolocation_zip_code_prefix) as n_geo_rows
    from in_bounds
),

medians as (
    select
        geolocation_zip_code_prefix,
        -- The median is the average of the two middle rows when the count is even, and the
        -- single middle row when it is odd. Selecting ``between floor and ceil`` of (n+1)/2
        -- covers both cases. Comparing against a single ``(n+1)/2`` fails on even counts,
        -- because BigQuery's ``/`` returns a float that never equals an integer row number.
        avg(case
            when rn_lat between floor((n_geo_rows + 1) / 2.0)
                           and ceil((n_geo_rows + 1) / 2.0)
            then geolocation_lat
        end) as lat,
        avg(case
            when rn_lng between floor((n_geo_rows + 1) / 2.0)
                           and ceil((n_geo_rows + 1) / 2.0)
            then geolocation_lng
        end) as lng,
        max(n_geo_rows) as n_geo_rows
    from ranked
    group by geolocation_zip_code_prefix
),

state_counts as (
    select
        geolocation_zip_code_prefix,
        geolocation_state,
        count(*) as n,
        row_number() over (
            partition by geolocation_zip_code_prefix
            order by count(*) desc, geolocation_state
        ) as rn
    from in_bounds
    group by geolocation_zip_code_prefix, geolocation_state
)

select
    m.geolocation_zip_code_prefix,
    m.lat,
    m.lng,
    s.geolocation_state as state,
    m.n_geo_rows
from medians m
left join state_counts s
    on m.geolocation_zip_code_prefix = s.geolocation_zip_code_prefix
   and s.rn = 1
