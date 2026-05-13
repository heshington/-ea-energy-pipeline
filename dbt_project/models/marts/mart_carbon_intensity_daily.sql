{{ config(materialized='table') }}
-- NOTE:
-- 'Other' fuel category assigned emission factor of 200 kgCO2/MWh
-- as a blended estimate due to mixed/unknown composition.

with generation as (

    select
        trading_date,
        fuel_category,
        total_mwh
    from {{ ref('mart_generation_mix') }}

),

joined as (

    select
        g.trading_date,
        g.fuel_category,
        g.total_mwh,
        ef.emission_factor_kg_co2_per_mwh,
        g.total_mwh * ef.emission_factor_kg_co2_per_mwh as emissions_kg_co2
    from generation g
    left join {{ ref('emission_factors') }} ef
        on g.fuel_category = ef.fuel_category

),

daily as (

    select
        trading_date,
        sum(total_mwh) as total_generation_mwh,
        sum(emissions_kg_co2) as total_emissions_kg_co2,
        sum(
            case 
                when emission_factor_kg_co2_per_mwh is null 
                then total_mwh 
                else 0 
            end
        ) as unmapped_generation_mwh
    from joined
    group by trading_date

),

renewables as (

    select
        trading_date,
        renewable_pct
    from {{ ref('mart_price_vs_generation') }}

)

select
    d.trading_date,
    d.total_generation_mwh,
    d.total_emissions_kg_co2,
    case
        when d.total_generation_mwh = 0 then null
        else d.total_emissions_kg_co2 / d.total_generation_mwh
    end as carbon_intensity_kg_co2_per_mwh,
    d.unmapped_generation_mwh,
    case
        when d.total_generation_mwh = 0 then null
        else (d.total_generation_mwh - d.unmapped_generation_mwh) / d.total_generation_mwh
    end as mapped_generation_pct,
    r.renewable_pct,
    --date column columns 
    dd.date_day,
    dd.year,
    dd.month_number,
    dd.month_name,
    dd.quarter_in_year,
    dd.season,
    dd.month_year_label
from daily d
left join renewables r
    on d.trading_date = r.trading_date
left join {{ ref('dim_date') }} dd
    on d.trading_date = dd.date_day
order by d.trading_date