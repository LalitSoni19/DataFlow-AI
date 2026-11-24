# Data dictionary

## Core business rule

Revenue is the sum of `net_revenue` for completed order lines. `net_revenue` is `quantity * unit_price - discount_amount`. Cancelled and refunded orders are excluded when `analytics.fact_sales` is built.

## Curated warehouse tables

| Table | Grain | Purpose |
| --- | --- | --- |
| `analytics.fact_sales` | One completed order line | Revenue, cost, and profit analysis |
| `analytics.dim_customer` | One customer | Customer, segment, and geography context |
| `analytics.dim_product` | One product | Product, category, supplier, and cost context |
| `analytics.dim_date` | One order date | Time-based analysis |
| `analytics.dim_region` | One sales region | Regional reporting |
| `analytics.monthly_sales` | Month and region | Dashboard-ready aggregate |

## Source contracts

| Dataset | Primary key | Required controls |
| --- | --- | --- |
| Customers | `customer_id` | Unique ID, valid email, ISO signup date |
| Products | `product_id` | Unique ID, non-negative price and cost |
| Orders | `order_id` | Unique ID, known customer, valid status and date |
| Order items | `order_item_id` | Unique ID, known order/product, positive quantity and price |
| Website events | `event_id` | Unique ID, known customer, ISO timestamp |


## Flexible sales

Each reporting row represents one sales line or one unique invoice total. The reporting view includes completed, paid and fulfilled records in one currency. Missing currency is a separate UNKNOWN bucket. Net revenue uses an explicit line amount when supplied, or quantity times unit price less the discount. Gross sales and discounts are unavailable if the required inputs are incomplete. Average line value uses measurable lines; average order value requires known order IDs and measurable revenue on every line.

The pipeline's acceptance percentage counts rows loaded. Revenue coverage is the percentage of received rows with a usable revenue measure, by currency. It is displayed alongside recognized row counts. Dates missing from a sales export prevent a monthly breakdown even if the all-time total is measurable.
