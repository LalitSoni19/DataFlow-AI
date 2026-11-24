"""Plain-text business summary shared by the dashboard and API."""

def monthly_report_text(summary: dict, currency_code: str) -> str:
    revenue = summary["total_revenue"]
    amount = "Unavailable" if revenue is None else f"{revenue:,.2f}"
    change = summary["revenue_change_pct"]
    change_text = "No comparable prior revenue" if change is None else f"{change:+.1f}%"
    lines = ["# Monthly Business Report", f"Period: {summary['year_month']}",
             f"Net revenue ({currency_code}): {amount}", f"Change from previous month: {change_text}",
             f"Completed orders: {summary['total_orders'] if summary['total_orders'] is not None else 'Unavailable'}",
             f"Top region: {summary['top_region']}", "", "Revenue by region"]
    for row in summary["regions"]:
        value = row["revenue"]
        lines.append(f"{row['region']}: " + ("Unavailable" if value is None else f"{value:,.2f}"))
    if summary.get("categories"):
        lines.extend(["", "Revenue by category"])
        for category in summary["categories"]:
            amount = category["revenue"]
            lines.append(f"{category['category']}: " + ("Unavailable" if amount is None else f"{amount:,.2f}"))
    if summary.get("highest_growth_category"):
        lines.append(f"Highest comparable category growth: {summary['highest_growth_category']} ({summary['highest_growth_pct']:+.1f}%)")
    return "\n".join(lines) + "\n"
