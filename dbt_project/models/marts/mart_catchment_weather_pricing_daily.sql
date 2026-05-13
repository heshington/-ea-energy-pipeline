{{ config(materialized='table') }}

-- Daily weather + wholesale pricing per NZ hydro catchment.
-- Designed for rainfall-vs-spot-price correlation analysis.
--
-- Grain: one row per (observation_date, catchment).
--
-- Each catchment maps to the primary downstream hydro station — the one
-- that converts its inflows into electricity. This keeps the analytical
-- chain tight (rainfall in this catchment → generation at this station →
-- price at this station's GIP) rather than diluting everything through
-- a single regional reference node.
--
-- Note: stg_wholesale_prices only starts 2020-09-10. Weather rows before
-- that date will appear with NULL price columns (LEFT JOIN), so filter
-- on price_nzd_per_mwh IS NOT NULL when doing correlation work.

with weather_by_catchment as (

    -- Roll per-lake daily weather up to per-catchment daily.
    -- Sum precipitation (total water hitting the catchment) but average
    -- temperature (a single-number summary makes more sense than a sum).
    select
        observation_date,
        catchment,
        island,
        sum(precipitation_mm)        as total_precip_mm,
        avg(precipitation_mm)        as avg_precip_mm,
        sum(snowfall_cm)             as total_snowfall_cm,
        avg(mean_temp_c)             as mean_temp_c,
        min(min_temp_c)              as min_temp_c,
        count(distinct location)     as lake_count
    from {{ ref('int_weather_catchment_daily') }}
    group by 1, 2, 3

),

prices_by_node_daily as (

    -- Half-hourly stg → daily per node.
    -- Note that load_mwh and generation_mwh are already energy (MW × 0.5h),
    -- so summing across the 48 trading periods gives daily total energy.
    select
        trading_date,
        node,
        round(avg(price_nzd_per_mwh)::numeric, 2)     as avg_price_nzd_per_mwh,
        round(min(price_nzd_per_mwh)::numeric, 2)     as min_price_nzd_per_mwh,
        round(max(price_nzd_per_mwh)::numeric, 2)     as max_price_nzd_per_mwh,
        sum(load_mwh)                                 as total_load_mwh,
        sum(generation_mwh)                           as total_generation_mwh,
        sum(case when is_price_spike then 1 else 0 end) as spike_periods_count
    from {{ ref('stg_wholesale_prices') }}
    group by 1, 2

),


joined as (

    select
        w.observation_date,
        w.catchment,
        w.island,
        m.pricing_node,

        -- weather measures (catchment-level aggregation)
        w.total_precip_mm,
        w.avg_precip_mm,
        w.total_snowfall_cm,
        w.mean_temp_c,
        w.min_temp_c,
        w.lake_count,

        -- price measures (NULL pre 2020-09-10)
        p.avg_price_nzd_per_mwh,
        p.min_price_nzd_per_mwh,
        p.max_price_nzd_per_mwh,
        p.total_load_mwh,
        p.total_generation_mwh,
        p.spike_periods_count,

        --columns from date dimension
        d.year,
        d.month_name,
        d.day_of_week,
        d.is_weekend,
        d.month_year_label

    from weather_by_catchment w
    inner join {{ ref('dim_concatchment_node') }} m
        on w.catchment = m.catchment
    left join prices_by_node_daily p
        on  m.pricing_node = p.node
        and w.observation_date = p.trading_date
    left join {{ref('dim_date')}} d
        on w.observation_date = d.date_day
        

),

enriched as (

    -- Helper columns for Power BI exploration. Window functions partition
    -- by catchment so each chain has its own independent time series.
    select
        *,

        -- NZ season tag (Dec-Feb summer, Mar-May autumn, etc.). Useful as
        -- a slicer when comparing wet-season vs dry-season behaviour.
        case
            when extract(month from observation_date) in (12, 1, 2)  then 'summer'
            when extract(month from observation_date) in (3, 4, 5)   then 'autumn'
            when extract(month from observation_date) in (6, 7, 8)   then 'winter'
            when extract(month from observation_date) in (9, 10, 11) then 'spring'
        end as season,

        -- Wet-day flags. Threshold uses avg_precip_mm (per-lake average)
        -- so small catchments aren't penalised by being summed across
        -- fewer lakes. 1mm is the WMO standard "wet day" threshold;
        -- 25mm is heavy rain.
        case when avg_precip_mm >= 1.0  then true else false end as is_wet_day,
        case when avg_precip_mm >= 25.0 then true else false end as is_heavy_rain_day,

        -- Trailing rolling rainfall sums. Boundaries (the first 6/29 days
        -- of each catchment series) return partial windows rather than NULL,
        -- which is fine for visualisation — filter them out if you need
        -- only complete windows.
        sum(total_precip_mm) over (
            partition by catchment
            order by observation_date
            rows between 6 preceding and current row
        ) as precip_7d_rolling_sum,

        sum(total_precip_mm) over (
            partition by catchment
            order by observation_date
            rows between 29 preceding and current row
        ) as precip_30d_rolling_sum,

        -- Lagged prices — each row carries the price observed N days
        -- *after* the rainfall, so you can scatter total_precip_mm
        -- against price_lead_Nd directly without DAX window functions.
        -- The last N rows of each catchment will be NULL (no future
        -- price data), as expected.
        lead(avg_price_nzd_per_mwh, 1)  over (partition by catchment order by observation_date) as price_lead_1d,
        lead(avg_price_nzd_per_mwh, 3)  over (partition by catchment order by observation_date) as price_lead_3d,
        lead(avg_price_nzd_per_mwh, 7)  over (partition by catchment order by observation_date) as price_lead_7d,
        lead(avg_price_nzd_per_mwh, 14) over (partition by catchment order by observation_date) as price_lead_14d,
        lead(avg_price_nzd_per_mwh, 30) over (partition by catchment order by observation_date) as price_lead_30d

    from joined

)

select * from enriched