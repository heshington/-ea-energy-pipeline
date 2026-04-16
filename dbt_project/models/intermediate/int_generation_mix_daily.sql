-- National daily generation mix: total by fuel category,
-- renewable %, and a COVID period flag.
-- This feeds directly into the generation mix mart.

with daily_by_station as (
    select * from {{ ref('int_generation_daily') }}
),

national_daily as (
    select
        trading_date,
        fuel_category,
        is_renewable,
        sum(total_mwh)                              as total_mwh
    from daily_by_station
    group by trading_date, fuel_category, is_renewable
),

totals as (
    select
        trading_date,
        sum(total_mwh)                              as national_total_mwh,
        sum(case when is_renewable then total_mwh else 0 end)
                                                    as renewable_mwh,
        sum(case when not is_renewable then total_mwh else 0 end)
                                                    as fossil_mwh
    from national_daily
    group by trading_date
),

joined as (
    select
        nd.trading_date,
        nd.fuel_category,
        nd.is_renewable,
        nd.total_mwh,
        t.national_total_mwh,
        round(nd.total_mwh / nullif(t.national_total_mwh, 0) * 100, 2)
                                                    as pct_of_national,
        round(t.renewable_mwh / nullif(t.national_total_mwh, 0) * 100, 2)
                                                    as renewable_pct,

        -- COVID lockdown periods (NZ)
        case
            when trading_date between '2020-03-26' and '2020-04-27' then 'Level 4 Lockdown'
            when trading_date between '2020-04-28' and '2020-05-13' then 'Level 3'
            when trading_date between '2021-08-17' and '2021-09-21' then 'Delta Level 4'
            when trading_date between '2021-09-22' and '2021-10-04' then 'Delta Level 3'
            else null
        end                                         as nz_covid_period,

        extract(year from nd.trading_date)          as year,
        extract(month from nd.trading_date)         as month,
        extract(quarter from nd.trading_date)       as quarter,
        to_char(nd.trading_date, 'YYYY-MM')         as year_month

    from national_daily nd
    join totals t using (trading_date)
)

select * from joined
