with source as (
    select * from {{ source('raw', 'wholesale_prices') }}
),

staged as (
    select
        trading_date,
        trading_period,
        node,
        price                                           as price_nzd_per_mwh,
        load_mwh,
        generation_mwh,

        -- Flag price spikes (>$500/MWh is considered extreme in NZ market)
        case when price > 500 then true else false end  as is_price_spike,

        -- Derive timestamp
        trading_date + (cast((trading_period - 1) * 30 as text) || ' minutes')::interval
                                                        as period_start_at
    from source
    where price is not null
)

select * from staged
