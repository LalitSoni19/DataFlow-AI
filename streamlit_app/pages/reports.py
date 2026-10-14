"""Monthly business summary with AI-generated insights and downloadable reports."""

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

page_heading("Reports & Insights", "AI-generated monthly business summaries with downloadable reports")

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

        # Header metrics
        st.subheader(f"Monthly Business Summary — {selected_month}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Total Sales",
            currency(summary["total_revenue"]),
            delta=f"{summary['revenue_change_pct']:+.1f}% from {summary['previous_month']}",
        )
        c2.metric(
            "Total Orders",
            f"{summary['total_orders']:,}",
        )
        c3.metric(
            "Total Customers",
            f"{summary['total_customers']:,}",
            delta=f"{summary['customer_change_pct']:+.1f}%",
        )
        c4.metric(
            "Avg Line Value",
            currency(summary["avg_line_value"]),
        )

        st.divider()

        # Monthly insights
        direction = "increased" if summary["revenue_change_pct"] >= 0 else "decreased"
        abs_change = abs(summary["revenue_change_pct"])

        insights = [
            (f"**Total sales: {currency(summary['total_revenue'])}** "
             f"({'↑' if summary['revenue_change_pct'] >= 0 else '↓'} {abs_change:.1f}% from last month)"),
            (f"**Highest growth in {summary['highest_growth_category']}** "
             f"({'↑' if summary['highest_growth_pct'] >= 0 else '↓'} {abs(summary['highest_growth_pct']):.1f}%)"),
            (f"**Customer base {'increased' if summary['customer_change_pct'] >= 0 else 'decreased'}** "
             f"by {abs(summary['customer_change_pct']):.1f}%"),
            f"**Top performing region: {summary['top_region']}** ({currency(summary['top_region_revenue'])})",
        ]

        # Generate recommendation based on data
        recommendations = []
        if summary["revenue_change_pct"] < 0:
            recommendations.append(
                "Revenue declined this month. Review marketing campaigns and investigate "
                "underperforming regions for potential recovery opportunities."
            )
        else:
            recommendations.append(
                "Revenue is trending positively. Consider increasing inventory for high-demand products "
                "and continue the current marketing campaign."
            )

        # Find weakest region
        if summary["regions"]:
            weakest = summary["regions"][-1]
            recommendations.append(
                f"Focus growth efforts on **{weakest['region']}** region "
                f"(currently {currency(weakest['revenue'])}) to balance regional performance."
            )

        st.subheader("📈 Key Insights")
        for insight in insights:
            st.markdown(f"• {insight}")

        st.subheader("💡 Recommendations")
        for rec in recommendations:
            st.markdown(f"• {rec}")

        st.divider()

        # Category performance table
        st.subheader("Category Performance")
        if summary["categories"]:
            st.dataframe(
                [
                    {
                        "Category": cat["category"],
                        "Revenue": currency(cat["revenue"]),
                        "Units Sold": cat["units_sold"],
                        "Growth %": f"{cat['growth_pct']:+.1f}%",
                    }
                    for cat in summary["categories"]
                ],
                use_container_width=True,
                hide_index=True,
            )

        # Regional breakdown
        st.subheader("Revenue by Region")
        if summary["regions"]:
            st.dataframe(
                [
                    {
                        "Region": r["region"],
                        "Revenue": currency(r["revenue"]),
                        "Orders": r["orders"],
                    }
                    for r in summary["regions"]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.divider()

        # Downloadable markdown report
        report_lines = [
            "# Monthly Business Report",
            "",
            f"## Business Summary — {selected_month}",
            "",
            (f"- Total sales: {currency(summary['total_revenue'])} "
             f"({'↑' if summary['revenue_change_pct'] >= 0 else '↓'} {abs_change:.1f}% from last month)"),
            (f"- Highest growth in {summary['highest_growth_category']} "
             f"({'↑' if summary['highest_growth_pct'] >= 0 else '↓'} {abs(summary['highest_growth_pct']):.1f}%)"),
            (f"- Customer base {'increased' if summary['customer_change_pct'] >= 0 else 'decreased'} "
             f"by {abs(summary['customer_change_pct']):.1f}%"),
            f"- Top performing region: {summary['top_region']} ({currency(summary['top_region_revenue'])})",
            "",
            "## Recommendations",
        ]
        for rec in recommendations:
            report_lines.append(f"- {rec.replace('**', '')}")
        report_lines.extend([
            "",
            "## Category Performance",
        ])
        for cat in summary["categories"]:
            report_lines.append(
                f"- {cat['category']}: {currency(cat['revenue'])} ({cat['growth_pct']:+.1f}% growth)"
            )
        report_lines.extend([
            "",
            "## Revenue by Region",
        ])
        for r in summary["regions"]:
            report_lines.append(f"- {r['region']}: {currency(r['revenue'])} across {r['orders']:,} orders")

        report_text = "\n".join(report_lines) + "\n"

        st.download_button(
            "📥 Download Report",
            report_text,
            file_name=f"business_report_{selected_month}.md",
            mime="text/markdown",
            type="primary",
        )
