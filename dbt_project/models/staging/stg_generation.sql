with source as (
    select * from {{ source('raw', 'generation_output') }}
),

staged as (
    select
        site_code,
        poc_code,
        nwk_code,
        gen_code                                        as station_name,
        fuel_code,
        tech_code,
        trading_date,
        trading_period,
        output_kw,

        -- Convert kW half-hour reading to MWh
        -- (kW × 0.5 hours) ÷ 1000 = MWh
        round(cast(output_kw * 0.5 / 1000 as numeric), 6)  as output_mwh,

        -- Classify fuel as renewable or not
        case
            when fuel_code in ('Hydro', 'Geo', 'Wind', 'Wood') then true
            else false
        end                                             as is_renewable,

        -- Broad fuel category for grouping
        case
            when fuel_code = 'Hydro'            then 'Hydro'
            when fuel_code = 'Geo'              then 'Geothermal'
            when fuel_code = 'Wind'             then 'Wind'
            when fuel_code in ('Gas', 'Gas&Oil') then 'Gas'
            when fuel_code = 'Coal'             then 'Coal'
            when fuel_code = 'Diesel'           then 'Diesel'
            when fuel_code = 'Wood'             then 'Wood / Biomass'
            else 'Other'
        end                                             as fuel_category,

        -- Derive a proper timestamp from date + trading period
        -- Each period is 30 min; period 1 starts at midnight
        trading_date + (cast((trading_period - 1) * 30 as text) || ' minutes')::interval
                                                        as period_start_at

    from source
    where output_kw is not null
      and output_kw >= 0
)

select * from staged
