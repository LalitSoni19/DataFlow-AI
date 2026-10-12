CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS governance;

CREATE TABLE IF NOT EXISTS staging.customers (
    customer_id VARCHAR PRIMARY KEY,
    customer_name VARCHAR DEFAULT 'Unknown',
    email VARCHAR NOT NULL,
    city VARCHAR,
    state VARCHAR,
    country VARCHAR,
    region VARCHAR NOT NULL,
    signup_date DATE NOT NULL,
    segment VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.products (
    product_id VARCHAR PRIMARY KEY,
    product_name VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    subcategory VARCHAR,
    list_price DOUBLE NOT NULL,
    unit_cost DOUBLE NOT NULL,
    supplier VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.orders (
    order_id VARCHAR PRIMARY KEY,
    customer_id VARCHAR NOT NULL,
    order_date DATE NOT NULL,
    status VARCHAR NOT NULL,
    payment_method VARCHAR,
    region VARCHAR NOT NULL,
    marketing_channel VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.order_items (
    order_item_id VARCHAR PRIMARY KEY,
    order_id VARCHAR NOT NULL,
    product_id VARCHAR NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price DOUBLE NOT NULL,
    discount_amount DOUBLE NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS staging.website_events (
    event_id VARCHAR PRIMARY KEY,
    customer_id VARCHAR NOT NULL,
    event_timestamp TIMESTAMP NOT NULL,
    event_type VARCHAR NOT NULL,
    page VARCHAR,
    device VARCHAR
);

CREATE TABLE IF NOT EXISTS governance.pipeline_runs (
    run_id VARCHAR PRIMARY KEY,
    status VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    duration_seconds DOUBLE,
    ingested_rows BIGINT,
    accepted_rows BIGINT,
    rejected_rows BIGINT,
    quality_score DOUBLE,
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS governance.data_quality_results (
    run_id VARCHAR NOT NULL,
    dataset VARCHAR NOT NULL,
    rows_read BIGINT NOT NULL,
    accepted_rows BIGINT NOT NULL,
    rejected_rows BIGINT NOT NULL,
    quality_score DOUBLE NOT NULL,
    issue_count BIGINT NOT NULL,
    recorded_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS governance.ai_audit_log (
    request_id VARCHAR NOT NULL,
    asked_at TIMESTAMP NOT NULL,
    question VARCHAR NOT NULL,
    generated_sql VARCHAR,
    execution_status VARCHAR NOT NULL,
    row_count BIGINT,
    provider VARCHAR NOT NULL,
    error_message VARCHAR
);

-- Canonical layer for one-or-more user supplied sales exports.  Unlike the
-- curated star schema above, this table is intentionally tolerant of missing
-- product/customer dimensions and is populated only by the flexible importer.
CREATE TABLE IF NOT EXISTS analytics.flex_sales (
    source_file VARCHAR NOT NULL,
    source_row BIGINT NOT NULL,
    sales_date DATE,
    order_id VARCHAR,
    customer VARCHAR,
    customer_id VARCHAR,
    customer_name VARCHAR,
    product VARCHAR,
    product_name VARCHAR,
    category VARCHAR,
    region VARCHAR,
    quantity DOUBLE,
    unit_price DOUBLE,
    discount DOUBLE,
    revenue DOUBLE,
    net_revenue DOUBLE,
    cost DOUBLE,
    profit DOUBLE,
    status VARCHAR,
    currency VARCHAR,
    attributes_json VARCHAR
);

CREATE TABLE IF NOT EXISTS governance.sales_imports (
    run_id VARCHAR NOT NULL,
    imported_at TIMESTAMP NOT NULL,
    source_files_json VARCHAR NOT NULL,
    semantic_mapping_json VARCHAR NOT NULL,
    column_types_json VARCHAR NOT NULL,
    warnings_json VARCHAR NOT NULL,
    row_count BIGINT NOT NULL,
    PRIMARY KEY (run_id)
);

-- The active logical dataset is metadata only. Historical raw files and
-- import profiles remain available by run ID; switching a dashboard/AI query
-- between a curated batch and a flexible import never deletes either source.
CREATE TABLE IF NOT EXISTS governance.active_sales_dataset (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    mode VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    activated_at TIMESTAMP NOT NULL
);

