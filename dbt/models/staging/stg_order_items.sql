select
    order_item_id,
    order_id,
    product_id,
    cast(quantity as integer) as quantity,
    cast(unit_price as double) as unit_price,
    cast(discount_amount as double) as discount_amount
from {{ source('staging', 'order_items') }}

