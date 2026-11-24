"""Monthly business summary with AI-generated insights and downloadable reports."""

from __future__ import annotations

import sys
from pathlib import Path

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

page_heading("Monthly reports", "Choose a month to review sales, compare categories and download a report.")

if ensure_demo_data():
    repository = get_repository()

    try:
        available = repository.available_months()
    except Exception:  # noqa: BLE001
        available = []

    if not available:
        st.info("No sales data available. Run the pipeline first.")
        st.stop()

    selected_month = st.selectbox("Select month", available, index=0)

    if st.button("📊 Generate Report", type="primary"):
        with st.spinner("Analyzing warehouse data and generating insights..."):
            try:
                summary = repository.monthly_summary(selected_month)
            except Exception as error:  # noqa: BLE001
                st.error(f"Failed to generate report: {error}")
                st.stop()

        from dataflow_ai.reports import monthly_report_text

        st.subheader(f"Monthly summary · {selected_month}")
        code = repository.active_currency_code()
        cards = st.columns(4)
        cards[0].metric(f"Net revenue ({code})", currency(summary["total_revenue"]),
                        delta=(f"{summary['revenue_change_pct']:+.1f}% vs prior month"
                               if summary["revenue_change_pct"] is not None else None))
        for card, label, key in zip(cards[1:3], ("Orders", "Customers"), ("total_orders", "total_customers")):
            card.metric(label, "Unavailable" if summary[key] is None else format(summary[key], ","))
        cards[3].metric("Average line value", currency(summary["avg_line_value"]))
        report = monthly_report_text(summary, code)
        st.text(report)
        st.subheader("Category performance")
        st.dataframe(summary["categories"], hide_index=True)
        st.download_button("Download monthly report", report, file_name=f"sales-{selected_month}.md")
