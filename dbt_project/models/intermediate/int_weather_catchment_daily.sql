{{ config(materialized='view') }}
 
-- Daily weather observations enriched with catchment metadata.
-- Stays at (location, observation_date) grain so downstream models can
-- aggregate by catchment, island, or roll up across all locations.
 
with weather as (
 
    select * from {{ ref('stg_weather_daily') }}
 
),
 
locations as (
 
    select * from {{ ref('dim_locations') }}
 
),
 
joined as (
 
    select
        w.observation_date,
        w.location,
 
        -- location metadata
        l.lake_name,
        l.catchment,
        l.island,
        l.latitude,
        l.longitude,
 
        -- weather measures
        w.precipitation_mm,
        w.rain_mm,
        w.snowfall_cm,
        w.mean_temp_c,
        w.max_temp_c,
        w.min_temp_c,
 
        -- pre-computed time parts (handy for seasonal grouping)
        w.observation_year,
        w.observation_month
 
    from weather w
    inner join locations l
        on w.location = l.location
 
)
 
select * from joined