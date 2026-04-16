-- Joins daily generation mix with daily wholesale prices.
-- Lets you explore: when renewable % drops, do prices spike?
-- Aggregates wholesale prices to daily averages for joining.
--
-- NOTE: Wholesale prices are nodal (per grid node).
-- This model uses a simple national average — for regional
-- analysis you'd want to filter to specific nodes.

{{ config(materialized='table') }}

with gen_mix as (
    select
        trading_date,
        max(renewable_pct)          as renewable_pct,
        max(national_total_mwh)     as national_total_mwh,
        max(nz_covid_period)        as nz_covid_period,
        max(year)                   as year,
        max(month)                  as month,
        max(quarter)                as quarter,
        max(year_month)             as year_month
    from {{ ref('int_generation_mix_daily') }}
    group by trading_date
),

prices as (
    select
        trading_date,
        round(avg(price_nzd_per_mwh)::numeric, 2)   as avg_price_nzd_per_mwh,
        round(min(price_nzd_per_mwh)::numeric, 2)   as min_price_nzd_per_mwh,
        round(max(price_nzd_per_mwh)::numeric, 2)   as max_price_nzd_per_mwh,
        count(case when is_price_spike then 1 end)   as spike_periods_count
    from {{ ref('stg_wholesale_prices') }}
    group by trading_date
)

select
    g.trading_date,
    g.year,
    g.month,
    g.quarter,
    g.year_month,
    g.renewable_pct,
    g.national_total_mwh,
    g.nz_covid_period,
    p.avg_price_nzd_per_mwh,
    p.min_price_nzd_per_mwh,
    p.max_price_nzd_per_mwh,
    p.spike_periods_count
from gen_mix g
left join prices p using (trading_date)
order by g.trading_date
