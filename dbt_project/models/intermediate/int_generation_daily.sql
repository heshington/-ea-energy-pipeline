-- Rolls half-hourly generation up to daily totals per station and fuel type.
-- This is the bridge between the raw half-hourly grain and the reporting marts.

with generation as (
    select * from {{ ref('stg_generation') }}
),

daily as (
    select
        trading_date,
        station_name,
        fuel_code,
        fuel_category,
        is_renewable,
        nwk_code                                as network,

        sum(output_mwh)                         as total_mwh,
        count(*)                                as trading_periods_with_data,

        -- Expected 48 periods per day (49/50 on DST transition days)
        round(count(*) / 48.0 * 100, 1)         as data_completeness_pct

    from generation
    group by
        trading_date,
        station_name,
        fuel_code,
        fuel_category,
        is_renewable,
        nwk_code
)

select * from daily
