-- Main reporting mart: national generation mix over time.
-- Materialized as a table — this is what Power BI reads from.
--
-- Key questions this answers:
--   - How has NZ's renewable % changed year on year?
--   - What happened to generation during COVID lockdowns?
--   - How has the fuel mix shifted (coal declining, wind growing)?

{{ config(materialized='table') }}

select * from {{ ref('int_generation_mix_daily') }}
order by trading_date, fuel_category
