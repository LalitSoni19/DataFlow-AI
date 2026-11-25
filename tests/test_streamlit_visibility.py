from pathlib import Path

from dataflow_ai.ai.sql_guard import SafeSQLExecutor
from dataflow_ai.ingestion.flexible_sales import load_flexible_sales_files
from dataflow_ai.settings import Settings
from dataflow_ai.warehouse.repository import WarehouseRepository
from streamlit_app.visibility import DisplayPolicy


def test_streamlit_identifiers_excluded_before_display_and_query_synthesis(tmp_path):
    export = tmp_path / "sales.csv"
    export.write_text("Invoice Number,Client ID,SKU,Amount,Category\n"
                      "111,private-client,private-stock,10,Books\n"
                      "222,private-client,private-stock,20,Books\n")
    imported = load_flexible_sales_files([export])
    repo = WarehouseRepository(Settings.from_environment(tmp_path))
    repo.load_flexible_sales(imported, "fixture")
    policy = DisplayPolicy(repo)
    executor = SafeSQLExecutor(repo, display_policy=policy)
    rows = policy.rows(imported.canonical_rows)
    assert "customer_name" not in rows[0] and "product" not in rows[0]
    assert "order_id" not in rows[0] and rows[0]["category"] == "Books"
    for table in ("analytics.flex_sales_reporting", "analytics.sales_upload"):
        result = executor.execute(f"SELECT * FROM {table}")
        assert "private-" not in str(result.rows)
        assert "111" not in str(result.rows)
    query = ("SELECT customer AS buyer, order_id AS invoice, product_name AS item "
             "FROM analytics.flex_sales_reporting")
    assert executor.execute(query).rows == [{"buyer": "Unknown", "invoice": None, "item": None}] * 2
    summary = executor.execute("SELECT COUNT(DISTINCT order_id) AS orders, SUM(revenue) AS sales "
                               "FROM analytics.flex_sales_reporting")
    assert summary.rows == [{"orders": 2, "sales": 30}]


def test_dashboard_and_assistant_keep_aggregates_visible(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from streamlit_app import components

    settings = Settings(tmp_path, tmp_path, tmp_path / "raw", tmp_path / "processed",
                        tmp_path / "quarantine", tmp_path / "sample", tmp_path / "warehouse",
                        tmp_path / "warehouse" / "local.duckdb", tmp_path / "logs")
    export = tmp_path / "sales.csv"
    export.write_text("order_id,customer_id,product,date,category,amount,currency\n"
                      "42,private-client,Desk,2026-01-01,Furniture,20,USD\n"
                      "43,private-client,Desk,2026-02-01,Furniture,30,USD\n")
    WarehouseRepository(settings).load_flexible_sales(load_flexible_sales_files([export]), "internal-run")
    monkeypatch.setattr(components, "get_settings", lambda: settings)
    monkeypatch.setattr(components, "base_settings", lambda: settings)
    for page in ("home", "warehouse", "assistant"):
        app = AppTest.from_file(Path(components.__file__).parent / "pages" / f"{page}.py", default_timeout=30).run()
        if page == "assistant":
            app.text_area[0].set_value("What is total revenue?")
            next(b for b in app.button if b.label == "Ask the warehouse").click()
            app.run()
            assert app.dataframe[0].value.iloc[0]["total_revenue"] == 50
        assert not app.exception
        assert all("private-client" not in frame.value.to_csv() for frame in app.dataframe)
        assert all("internal-run" not in item.value for item in app.caption)
