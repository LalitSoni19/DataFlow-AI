select *
from {{ ref('fct_sales') }}
where net_revenue < 0

