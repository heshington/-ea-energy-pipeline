{{ config(materialized='table') }}

with dates as (

    select
        generate_series(
            '2026-01-01'::date,
            '2026-12-31'::date,
            interval '1 day'
        )::date as date_day

),

date_altered as ( 
    select
        date_day,
        extract(year from date_day) as year,
        extract(month from date_day) as month_number,
        trim(to_char(date_day, 'Month')) as month_name,
        extract(quarter from date_day)as quarter_in_year,
        extract(dow from date_day) as day_of_week
    from dates
),

date_with_season as (

select 
    date_day,
    year,
    month_number,
    month_name,
    quarter_in_year,
    day_of_week,
    case
        when month_number in (12, 01, 02) then 'Summer'
        when month_number in (3,4,5) then 'Autumn'
        when month_number in (6,7,8) then 'Winter'
        when month_number in (9,10,11) then 'Spring'
    end as season,
   case 
    when day_of_week in (0, 6) then true
    else false
end as is_weekend,
    to_char(date_day, 'Mon YYYY') as month_year_label,
    extract(week from date_day) as week_number,
    to_char(date_day, 'Dy') as day_name_short
    from date_altered

)

select * from date_with_season


