select
    product_id,
    product_name,
    category,
    subcategory,
    cast(unit_cost as double) as unit_cost
from {{ source('staging', 'products') }}

