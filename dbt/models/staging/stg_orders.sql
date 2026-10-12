select
    order_id,
    customer_id,
    cast(order_date as date) as order_date,
    status,
    payment_method,
    region,
    marketing_channel
from {{ source('staging', 'orders') }}

