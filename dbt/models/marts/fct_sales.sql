select
    *,
    net_revenue - cost_amount as profit_amount
from {{ ref('int_completed_sales') }}

