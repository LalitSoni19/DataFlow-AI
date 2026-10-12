"""Interactive SQL explorer for the curated analytics warehouse."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import csv
import io

import streamlit as st

from dataflow_ai.ai.sql_guard import SafeSQLExecutor, SQLSafetyError

try:
    from streamlit_app.components import ensure_demo_data, get_repository, page_heading
except ModuleNotFoundError:
    from components import ensure_demo_data, get_repository, page_heading  # type: ignore[no-redef]

page_heading("Data Explorer", "Query the warehouse with safe, read-only SQL against analytics tables")

if ensure_demo_data():
    repository = get_repository()
    executor = SafeSQLExecutor(repository)

    is_flexible = repository.active_sales_mode() == "flexible"

    # Show approved tables and their columns for reference
    with st.expander("📋 Approved analytics tables & available columns", expanded=False):
        metadata = repository.table_metadata()
        for item in metadata:
            tbl = item['table']
            st.markdown(f"**`{tbl}`** — {item['description']}")
            # Show live columns for tables
            try:
                tbl_name = tbl.rsplit(".", 1)[-1]
                cols = [
                    r["column_name"]
                    for r in repository.fetch_all(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
                        [tbl_name],
                    )
                ]
                if cols:
                    st.caption("📌 Columns: " + ", ".join(f"`{c}`" for c in cols))
            except Exception:
                pass

    # Quick template queries
    if is_flexible:
        template_options = {
            "Canonical Sales Fact (flex_sales)": (
                "SELECT customer, product, category, region, sales_date, revenue, status\n"
                "FROM flex_sales\n"
                "LIMIT 50"
            ),
            "Raw Ingested Data (sales_upload)": (
                "SELECT *\n"
                "FROM sales_upload\n"
                "LIMIT 50"
            ),
            "Revenue by Category (flex_sales)": (
                "SELECT category, SUM(revenue) AS total_revenue\n"
                "FROM flex_sales\n"
                "GROUP BY category\n"
                "ORDER BY total_revenue DESC"
            ),
            "Top Customers by Spend (flex_sales)": (
                "SELECT customer, SUM(revenue) AS total_spent\n"
                "FROM flex_sales\n"
                "GROUP BY customer\n"
                "ORDER BY total_spent DESC\n"
                "LIMIT 20"
            ),
        }
    else:
        template_options = {
            "Sales by Region (fact_sales)": (
                "SELECT region, SUM(net_revenue) AS total_sales\n"
                "FROM fact_sales\n"
                "GROUP BY region\n"
                "ORDER BY total_sales DESC"
            ),
            "Top Products by Revenue (fact_sales)": (
                "SELECT p.product_name, p.category, SUM(f.net_revenue) AS total_revenue\n"
                "FROM fact_sales f\n"
                "JOIN dim_product p ON p.product_id = f.product_id\n"
                "GROUP BY p.product_name, p.category\n"
                "ORDER BY total_revenue DESC\n"
                "LIMIT 20"
            ),
            "Top Customers by Purchasing (fact_sales)": (
                "SELECT c.customer_name, c.region, SUM(f.net_revenue) AS total_spent\n"
                "FROM fact_sales f\n"
                "JOIN dim_customer c ON c.customer_id = f.customer_id\n"
                "GROUP BY c.customer_name, c.region\n"
                "ORDER BY total_spent DESC\n"
                "LIMIT 20"
            ),
        }

    selected_template = st.selectbox("💡 Quick query templates", list(template_options.keys()))
    default_query = template_options[selected_template]

    limit_col, run_col, _ = st.columns([2, 2, 4])
    row_limit = limit_col.selectbox("Max rows limit", [50, 100, 200, 500, 1000], index=2)
    run_clicked = run_col.button("▶ Run Query", type="primary")

    sql_input = st.text_area(
        "SQL Query",
        value=default_query,
        height=160,
        help="Write a SELECT query against the approved analytics tables. Table names without 'analytics.' prefix are automatically resolved.",
    )

    if run_clicked and sql_input.strip():
        executor = SafeSQLExecutor(repository, maximum_rows=row_limit)
        try:
            with st.spinner("Executing query..."):
                result = executor.execute(sql_input)

            st.success(f"Query returned {result.row_count} row(s) in {result.executed_at}")

            # Show the validated SQL
            with st.expander("✅ Validated SQL", expanded=False):
                st.code(result.sql, language="sql")
                if result.parameters:
                    st.caption(f"Parameters: {result.parameters}")

            # Display results
            if result.rows:
                st.subheader("Results")
                st.dataframe(result.rows, use_container_width=True, hide_index=True)

                # CSV download
                csv_buffer = io.StringIO()
                if result.rows:
                    writer = csv.DictWriter(csv_buffer, fieldnames=result.columns)
                    writer.writeheader()
                    writer.writerows(result.rows)
                st.download_button(
                    "📥 Download as CSV",
                    csv_buffer.getvalue(),
                    file_name="query_results.csv",
                    mime="text/csv",
                )
            else:
                st.info("The query returned no rows.")

        except SQLSafetyError as error:
            st.error(f"🛡️ Query blocked by safety guard: {error}")
        except Exception as error:  # noqa: BLE001
            st.error(f"Query execution failed: {error}")

    elif run_clicked:
        st.warning("Please enter a SQL query before running.")
