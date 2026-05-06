{{ config(materialized='view') }}

-- Staging layer for daily weather observations.
-- Light cleanup only: rename to friendlier column names and add a few
-- derived fields that downstream models will reach for repeatedly.

with source as (

    select * from {{ source('raw', 'weather_daily') }}

),

renamed as (

    select
        location,
        time                       as observation_date,

        precipitation_sum          as precipitation_mm,
        rain_sum                   as rain_mm,
        snowfall_sum               as snowfall_cm,

        temperature_2m_mean        as mean_temp_c,
        temperature_2m_max         as max_temp_c,
        temperature_2m_min         as min_temp_c,

        -- Useful derived fields for time-series joins and seasonal grouping.
        extract(year  from time)::int  as observation_year,
        extract(month from time)::int  as observation_month,

        inserted_at
    from source

)

select * from renamed