import pytest

from dataflow_ai.ai.sql_guard import SQLSafetyError, validate_read_only_sql


def test_sql_guard_allows_approved_analytics_select() -> None:
    assert validate_read_only_sql("SELECT * FROM analytics.fact_sales LIMIT 5;").startswith("SELECT")


def test_sql_guard_allows_explicit_join_and_read_only_cte() -> None:
    sql = """
        WITH revenue AS (
            SELECT f.product_id, SUM(f.net_revenue)::DOUBLE AS revenue
            FROM analytics.fact_sales AS f
            GROUP BY f.product_id
        )
        SELECT p.product_name, revenue.revenue
        FROM revenue
        JOIN analytics.dim_product AS p ON p.product_id = revenue.product_id
        ORDER BY revenue.revenue DESC
        LIMIT 10
    """
    assert "analytics.fact_sales" in validate_read_only_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM analytics.fact_sales",
        "SELECT * FROM analytics.fact_sales; DROP TABLE analytics.fact_sales",
        "SELECT * FROM read_csv_auto('anything.csv')",
        "SELECT * FROM staging.orders",
        "SELECT * FROM analytics.fact_sales -- comment",
        "SELECT * FROM analytics.fact_sales, read_csv_auto('C:/Windows/win.ini')",
        "SELECT * FROM analytics.fact_sales, range(1000000000000)",
        "SELECT * FROM analytics.fact_sales, analytics.dim_product",
        "SELECT * FROM analytics.fact_sales CROSS JOIN analytics.dim_product",
        "SELECT * FROM read_parquet('s3://private-bucket/sales.parquet')",
        "SELECT * FROM analytics.fact_sales LIMIT 201",
    ],
)
def test_sql_guard_blocks_unsafe_or_unapproved_sql(sql: str) -> None:
    with pytest.raises(SQLSafetyError):
        validate_read_only_sql(sql)


def test_sql_guard_auto_qualifies_unadorned_tables() -> None:
    sql = "SELECT customer_id FROM sales_upload WHERE customer_id = 'C001'"
    validated = validate_read_only_sql(sql)
    assert "analytics.sales_upload" in validated


def test_sql_guard_allows_range_keyword_in_filters_and_windows() -> None:
    filter_sql = "SELECT * FROM analytics.flex_sales WHERE product = 'gas range'"
    assert "analytics.flex_sales" in validate_read_only_sql(filter_sql)

    window_sql = (
        "SELECT SUM(revenue) OVER (ORDER BY sales_date RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) "
        "FROM analytics.flex_sales"
    )
    assert "analytics.flex_sales" in validate_read_only_sql(window_sql)


def test_sql_guard_allows_json_and_string_functions() -> None:
    sql = (
        "SELECT REPLACE(customer, 'C', 'Cust-') AS c, "
        "attributes_json->>'first_name' AS fn, "
        "COUNTIF(revenue > 100) AS big_sales, "
        "CONTAINS(category, 'Tech') AS is_tech, "
        "STARTS_WITH(customer, 'C') AS starts_c "
        "FROM analytics.flex_sales"
    )
    validated = validate_read_only_sql(sql)
    assert "analytics.flex_sales" in validated


def test_sql_guard_allows_cte_comma_join_with_aliases() -> None:
    sql = """
        WITH a AS (SELECT 1 AS x FROM analytics.flex_sales),
             b AS (SELECT 2 AS y FROM analytics.flex_sales)
        SELECT * FROM a AS t1, b AS t2
    """
    validated = validate_read_only_sql(sql)
    assert "JOIN b AS t2 ON 1 = 1" in validated


