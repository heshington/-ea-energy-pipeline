-- DRAFT — review before wiring into Power BI.
--
-- Daily electricity market stress metrics.
-- Feeds a "When does the grid fail us?" reporting page.
--
-- Depends on:
--   stg_wholesale_prices           (half-hourly nodal prices + is_price_spike)
--   int_generation_mix_daily       (renewable_pct, national_total_mwh)
--   mart_carbon_intensity_daily    (carbon_intensity, for cross-reference)
--
-- Key fields produced:
--   avg / min / max / median / p95 price
--   spike_records_count    — total node×period records above $500/MWh
--   spike_periods_distinct — number of distinct 30-min periods with ≥1 node in spike
--   is_spike_day           — boolean, true if any spike that day
--   volatility_class       — Calm / Elevated / Stressed / Extreme
--   spike_days_last_7 / last_30                 — rolling breadth
--   spike_periods_distinct_last_7 / last_30     — rolling depth
--   days_since_last_spike  — NULL before first-ever spike, 0 on a spike day
--
-- Spike threshold ($500/MWh) is inherited from stg_wholesale_prices.is_price_spike.
-- If you change the threshold, update both places.
--
-- NOTE: Wholesale price data only starts 2020-09-10, so this mart's output
-- begins there too (it's price-driven by design). If you need the generation
-- mix over the full 2018+ range, use mart_generation_mix instead.

{{ config(materialized='table') }}

with price_daily as (

    select
        trading_date,

        round(avg(price_nzd_per_mwh)::numeric, 2)                                     as avg_price_nzd_per_mwh,
        round(min(price_nzd_per_mwh)::numeric, 2)                                     as min_price_nzd_per_mwh,
        round(max(price_nzd_per_mwh)::numeric, 2)                                     as max_price_nzd_per_mwh,
        round((percentile_cont(0.50) within group (order by price_nzd_per_mwh))::numeric, 2)
                                                                                      as median_price_nzd_per_mwh,
        round((percentile_cont(0.95) within group (order by price_nzd_per_mwh))::numeric, 2)
                                                                                      as p95_price_nzd_per_mwh,

        -- Every node × period combination where price > $500/MWh.
        -- Matches the legacy spike_periods_count in mart_price_vs_generation.
        sum(case when is_price_spike then 1 else 0 end)                               as spike_records_count,

        -- Number of distinct half-hour periods where *any* node spiked.
        -- A more readable "breadth of stress" metric.
        count(distinct case when is_price_spike then trading_period end)              as spike_periods_distinct

    from {{ ref('stg_wholesale_prices') }}
    group by trading_date

),

gen_context as (

    select
        trading_date,
        max(renewable_pct)              as renewable_pct,
        max(national_total_mwh)         as national_total_mwh,
        max(nz_covid_period)            as nz_covid_period
    from {{ ref('int_generation_mix_daily') }}
    group by trading_date

),

carbon_context as (

    select
        trading_date,
        carbon_intensity_kg_co2_per_mwh
    from {{ ref('mart_carbon_intensity_daily') }}

),

joined as (

    select
        p.trading_date,

        -- Price shape
        p.avg_price_nzd_per_mwh,
        p.min_price_nzd_per_mwh,
        p.max_price_nzd_per_mwh,
        p.median_price_nzd_per_mwh,
        p.p95_price_nzd_per_mwh,
        (p.max_price_nzd_per_mwh - p.min_price_nzd_per_mwh) as price_range_nzd_per_mwh,

        -- Spike breadth / depth
        p.spike_records_count,
        p.spike_periods_distinct,
        (p.spike_periods_distinct > 0)                      as is_spike_day,

        -- Volatility classification
        -- Tuned to highlight genuinely stressed days in an NZ context.
        -- Using p95 rather than max makes the classification robust to a
        -- single outlier node without changing the headline meaning.
        case
            when p.p95_price_nzd_per_mwh > 1000 or p.spike_periods_distinct >= 24 then 'Extreme'
            when p.p95_price_nzd_per_mwh > 300  or p.spike_periods_distinct >= 6  then 'Stressed'
            when p.spike_periods_distinct >= 1                                    then 'Elevated'
            else 'Calm'
        end                                                 as volatility_class,

        -- Context from other marts
        g.renewable_pct,
        g.national_total_mwh,
        g.nz_covid_period,
        c.carbon_intensity_kg_co2_per_mwh

    from price_daily p
    left join gen_context    g using (trading_date)
    left join carbon_context c using (trading_date)

),

with_rolling as (

    select
        *,

        sum(case when is_spike_day then 1 else 0 end)
            over (order by trading_date rows between  6 preceding and current row)   as spike_days_last_7,
        sum(case when is_spike_day then 1 else 0 end)
            over (order by trading_date rows between 29 preceding and current row)   as spike_days_last_30,

        sum(spike_periods_distinct)
            over (order by trading_date rows between  6 preceding and current row)   as spike_periods_distinct_last_7,
        sum(spike_periods_distinct)
            over (order by trading_date rows between 29 preceding and current row)   as spike_periods_distinct_last_30

    from joined

),

with_days_since as (

    select
        *,

        -- NULL before the first-ever spike day; 0 on a spike day; n after n clean days.
        trading_date - max(case when is_spike_day then trading_date end)
            over (order by trading_date rows between unbounded preceding and current row)
            as days_since_last_spike

    from with_rolling

)

select * from with_days_since
order by trading_date
