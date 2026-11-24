"""Executive KPI dashboard backed only by analytics.fact_sales."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root and src/ are in sys.path for Streamlit Cloud & local execution
_current = Path(__file__).resolve().parent
PROJECT_ROOT = _current.parent.parent
for _parent in [_current, *_current.parents]:
    _src = _parent / "src"
    if _src.is_dir() and (_src / "dataflow_ai").is_dir():
        PROJECT_ROOT = _parent
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

import streamlit as st

try:
    from streamlit_app.components import (
        cached_kpi_overview,
        cached_monthly_trend,
        cached_sales,
        cached_sales_by_category,
        cached_top_products,
        currency,
        ensure_demo_data,
        get_repository,
        page_heading,
    )
except ModuleNotFoundError:
    from components import (  # type: ignore[no-redef]
        cached_kpi_overview,
        cached_monthly_trend,
        cached_sales,
        cached_sales_by_category,
        cached_top_products,
        currency,
        ensure_demo_data,
        get_repository,
        page_heading,
    )

page_heading("Sales overview", "Track revenue, customers and orders across your sales data.")
if ensure_demo_data():
    try:
        overview = cached_kpi_overview()
        sales = cached_sales()
        top_products = cached_top_products(5)
        monthly_trend = cached_monthly_trend(6)
        category_sales = cached_sales_by_category()
    except Exception as error:  # noqa: BLE001
        st.error(f"Warehouse query failed: {error}")
        st.stop()

    # KPI cards with MoM deltas
    revenue_delta = None
    customer_delta = None
    order_delta = None
    if len(monthly_trend) >= 2:
        current_m = monthly_trend[0]
        previous_m = monthly_trend[1]
        cur_rev = float(current_m.get("revenue") or 0)
        prev_rev = float(previous_m.get("revenue") or 0)
        if prev_rev:
            revenue_delta = f"{((cur_rev - prev_rev) / prev_rev * 100):+.1f}%"

        cur_cust = float(current_m.get("customers") or 0)
        prev_cust = float(previous_m.get("customers") or 0)
        if prev_cust:
            customer_delta = f"{((cur_cust - prev_cust) / prev_cust * 100):+.1f}%"

        cur_ord = float(current_m.get("orders") or 0)
        prev_ord = float(previous_m.get("orders") or 0)
        if prev_ord:
            order_delta = f"{((cur_ord - prev_ord) / prev_ord * 100):+.1f}%"

    curr_sym = get_repository().active_currency_symbol()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net revenue", currency(overview.get("total_revenue"), curr_sym), delta=revenue_delta)
    m2.metric("Total Customers", format(overview["active_customers"], ",") if overview["active_customers"] is not None else "Unavailable", delta=customer_delta)
    m3.metric("Total Orders", format(overview["total_orders"], ",") if overview["total_orders"] is not None else "Unavailable", delta=order_delta)
    m4.metric("Avg Line Value", currency(overview.get("average_line_value")))

    if len(monthly_trend) >= 2:
        st.caption(f"Totals cover all records. Changes compare {current_m['year_month']} with {previous_m['year_month']}.")

    with st.expander("Revenue breakdown"):
        st.write(f"Gross sales: {currency(overview.get('total_sales_amount'), curr_sym)}")
        st.write(f"Recorded discounts: {currency(overview.get('discounts'), curr_sym)}")
        st.write(f"Net revenue: {currency(overview.get('total_revenue'), curr_sym)}")
    if overview.get("coverage"):
        st.subheader("Revenue coverage by currency")
        st.dataframe(overview["coverage"], hide_index=True)
        st.caption("Received includes all rows. Net revenue includes completed, paid and fulfilled sales. UNKNOWN means no currency was supplied.")
    # Monthly trend and category breakdown
    left, right = st.columns([3, 2])

    with left.container(border=True):
        st.subheader("Monthly revenue")
        if monthly_trend:
            trend_sorted = sorted(monthly_trend, key=lambda r: r.get("year_month") or "")
            chart_data = [{"Month": row["year_month"], "Revenue": float(row.get("revenue") or 0)} for row in trend_sorted if row.get("year_month")]
            st.line_chart(chart_data, x="Month", y="Revenue", color="#E8C978", x_label="Month", y_label=(f"Revenue ({curr_sym})" if curr_sym else "Revenue (currency unknown)"))
        else:
            st.info("No monthly data available yet.")

    with right.container(border=True):
        st.subheader("Sales by Category")
        if category_sales:
            cat_data = [{"Category": row.get("category") or "Unspecified", "Revenue": float(row.get("revenue") or 0)} for row in category_sales]
            st.bar_chart(cat_data, x="Category", y="Revenue", color="#F2E8CD", x_label="Category", y_label=(f"Revenue ({curr_sym})" if curr_sym else "Revenue (currency unknown)"), horizontal=True)
        else:
            st.info("No category data available yet.")

    st.divider()

    # Daily revenue and top products
    left2, right2 = st.columns([3, 2])

    with left2.container(border=True):
        st.subheader("Daily revenue")
        daily: dict[str, float] = {}
        for row in sales:
            date_k = row.get("date_key") or "Unknown"
            daily[date_k] = daily.get(date_k, 0.0) + float(row.get("revenue") or 0)
        points = [{"Date": str(day), "Revenue": amount} for day, amount in daily.items()]
        if points:
            st.line_chart(points, x="Date", y="Revenue", color="#E8C978", x_label="Date", y_label=(f"Revenue ({curr_sym})" if curr_sym else "Revenue (currency unknown)"))

        else:
            st.info("No daily revenue available yet.")

    with right2.container(border=True):
        st.subheader("Top products")
        st.dataframe(
            top_products, use_container_width=True, hide_index=True,
            column_config={"product_name": "Product", "category": "Category",
                           "revenue": (f"Revenue ({curr_sym})" if curr_sym else "Revenue (currency unknown)"), "units_sold": "Units sold"},
        )
