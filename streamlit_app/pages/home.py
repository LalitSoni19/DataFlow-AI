"""Executive KPI dashboard backed only by analytics.fact_sales."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

try:
    from streamlit_app.components import (
        currency,
        ensure_demo_data,
        get_repository,
        page_heading,
    )
except ModuleNotFoundError:
    from components import (  # type: ignore[no-redef]
        currency,
        ensure_demo_data,
        get_repository,
        page_heading,
    )

page_heading("Business dashboard", "Trusted metrics from the completed-order warehouse model")
st.html('<meta name="google-site-verification" content="dWYj8KkmQ7p1HlC9H7LkaGTdsdBXBPm9-pNd5UZKaJ8" />')
if ensure_demo_data():
    repository = get_repository()
    try:
        overview = repository.kpi_overview()
        sales = repository.sales()
        top_products = repository.top_products(5)
        monthly_trend = repository.monthly_trend(6)
        category_sales = repository.sales_by_category()
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

    curr_sym = repository.active_currency_symbol()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Sales", currency(overview.get("total_sales_amount") or overview.get("total_revenue")), delta=revenue_delta)
    m2.metric("Total Customers", f"{int(overview.get('active_customers') or 0):,}", delta=customer_delta)
    m3.metric("Total Orders", f"{int(overview.get('total_orders') or 0):,}", delta=order_delta)
    m4.metric("Avg Line Value", currency(overview.get("average_line_value")))

    st.divider()

    # Monthly trend and category breakdown
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Sales Trend (Last 6 Months)")
        if monthly_trend:
            trend_sorted = sorted(monthly_trend, key=lambda r: r.get("year_month") or "")
            chart_data = {row["year_month"]: float(row.get("revenue") or 0) for row in trend_sorted if row.get("year_month")}
            st.line_chart(chart_data, x_label="Month", y_label=f"Revenue ({curr_sym})")
        else:
            st.info("No monthly data available yet.")

    with right:
        st.subheader("Sales by Category")
        if category_sales:
            cat_data = {row.get("category") or "Unspecified": float(row.get("revenue") or 0) for row in category_sales}
            st.bar_chart(cat_data, x_label="Category", y_label=f"Revenue ({curr_sym})", horizontal=True)
        else:
            st.info("No category data available yet.")

    st.divider()

    # Daily revenue and top products
    left2, right2 = st.columns([3, 2])

    with left2:
        st.subheader("Daily revenue")
        daily: dict[str, float] = {}
        for row in sales:
            date_k = row.get("date_key") or "Unknown"
            daily[date_k] = daily.get(date_k, 0.0) + float(row.get("revenue") or 0)
        st.line_chart(daily, x_label="Date", y_label=f"Revenue ({curr_sym})")

    with right2:
        st.subheader("Top products")
        st.dataframe(top_products, use_container_width=True, hide_index=True)
