-- Main reporting mart: national generation mix over time.
-- Materialized as a table — this is what Power BI reads from.
--
-- Key questions this answers:
--   - How has NZ's renewable % changed year on year?
--   - What happened to generation during COVID lockdowns?
--   - How has the fuel mix shifted (coal declining, wind growing)?

{{ config(materialized='table') }}

select *,
d.season as dim_season,
d.year as dim_year,
d.month_name as dim_month_name,
d.quarter_in_year as dim_quarter_in_year,
d.month_year_label as dim_month_year_label
from {{ ref('int_generation_mix_daily') }}
left join {{ ref('dim_date') }} d
    on {{ ref('int_generation_mix_daily') }}.trading_date = d.date_day
order by trading_date, fuel_category
