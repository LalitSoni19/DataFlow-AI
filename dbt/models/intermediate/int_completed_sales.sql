select
    i.order_item_id as sale_key,
    o.order_id,
    o.customer_id,
    i.product_id,
    o.order_date as date_key,
    o.region,
    o.marketing_channel,
    i.quantity,
    i.unit_price,
    i.discount_amount,
    i.quantity * i.unit_price as gross_revenue,
    greatest((i.quantity * i.unit_price) - i.discount_amount, 0) as net_revenue,
    i.quantity * p.unit_cost as cost_amount
from {{ ref('stg_order_items') }} as i
inner join {{ ref('stg_orders') }} as o on o.order_id = i.order_id
inner join {{ ref('stg_products') }} as p on p.product_id = i.product_id
where o.status = 'completed'

