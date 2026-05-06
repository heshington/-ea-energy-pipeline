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

catchment_to_node as (

    -- Catchment → primary downstream hydro station's 220kV GIP.
    -- Each station is the one that actually converts that catchment's
    -- inflows into electricity, so the price reflects the local rainfall
    -- → generation chain.
    --
    --   waitaki   Benmore     — chain output station (Pukaki/Tekapo/Ohau feed in)
    --   clutha    Roxburgh    — main Clutha output (Hawea feeds in via Clyde)
    --   manapouri Manapouri   — sole station for the Manapouri/Te Anau chain
    --   waikato   Aratiatia   — first Waikato station, fed directly by Lake Taupō
    --
    -- Note: Benmore in this dataset is BEN2202 (220kV bus 2), not BEN2201.
    select 'waitaki'   as catchment, 'BEN2202' as pricing_node
    union all select 'manapouri', 'MAN2201'
    union all select 'clutha',    'ROX2201'
    union all select 'waikato',   'ARA2201'

)

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
    p.spike_periods_count

from weather_by_catchment w
inner join catchment_to_node m
    on w.catchment = m.catchment
left join prices_by_node_daily p
    on  m.pricing_node = p.node
    and w.observation_date = p.trading_date