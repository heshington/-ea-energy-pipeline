-- NOTE: Column names here will depend on the actual EA retail CSV structure.
-- After loading, run: SELECT * FROM raw.retail_prices LIMIT 5;
-- to see the real column names, then update this model.

with source as (
    select * from {{ source('raw', 'retail_prices') }}
)

select * from source
