{{ config(materialized='table') }}

with energy_demand as (

   SELECT

    trading_date,
    SUM(load_mwh) AS total_demand_mwh,
    AVG(load_mwh) AS avg_trading_period_demand_mwh,
    MAX(load_mwh) AS peak_trading_period_demand_mwh,
    --pulling in date dim columns
    d.date_day,
    d.year,
    d.month_number,
    d.month_name,
    d.quarter_in_year,
    d.season,
    d.month_year_label
    FROM {{ ref('stg_wholesale_prices') }}
    left join {{ref('dim_date')}} d
        on trading_date = d.date_day
    GROUP BY trading_date, d.date_day, d.year, d.month_number, d.month_name, d.quarter_in_year, d.season, d.month_year_label

)

select * from energy_demand
