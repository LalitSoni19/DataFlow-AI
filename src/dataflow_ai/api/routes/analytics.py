"""Analytics, KPIs, and reporting endpoints."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from dataflow_ai.api.rate_limiter import rate_limit_dependency
from dataflow_ai.api.routes.auth import UserSession, get_current_user, require_role
from dataflow_ai.warehouse.repository import WarehouseRepository

router = APIRouter(prefix="/api", tags=["analytics"])
CSV_EXPORT_LIMIT = rate_limit_dependency("csv_export", max_requests=5, window_seconds=60)


def _repository(request: Request) -> WarehouseRepository:
    return request.app.state.repository


def _or_unavailable(callback):
    try:
        return callback()
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Warehouse is unavailable. Please ensure the data pipeline has populated the warehouse.",
        ) from error


@router.get("/kpis/overview")
def kpi_overview(request: Request) -> dict:
    return _or_unavailable(_repository(request).kpi_overview)


@router.get("/sales")
def sales(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    region: str | None = Query(default=None, min_length=2, max_length=30),
) -> list[dict]:
    if start and end and start >= end:
        raise HTTPException(status_code=422, detail="end must be after start")
    return _or_unavailable(
        lambda: _repository(request).sales(
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
            region=region.title() if region else None,
        )
    )


@router.get("/products/top")
def top_products(request: Request, limit: int = Query(default=10, ge=1, le=100)) -> list[dict]:
    return _or_unavailable(lambda: _repository(request).top_products(limit))


@router.get("/pipeline/status")
def pipeline_status(request: Request, current_user: UserSession = Depends(get_current_user)) -> dict:
    status = _or_unavailable(_repository(request).pipeline_status)
    if status is None:
        raise HTTPException(status_code=404, detail="No pipeline runs have been recorded.")
    return status


@router.get("/data-quality")
def data_quality(request: Request, current_user: UserSession = Depends(get_current_user)) -> list[dict]:
    return _or_unavailable(_repository(request).data_quality)


@router.get("/metadata/tables")
def table_metadata(request: Request, current_user: UserSession = Depends(get_current_user)) -> list[dict[str, str]]:
    return _repository(request).table_metadata()


@router.get("/exports/sales.csv", dependencies=[Depends(CSV_EXPORT_LIMIT)])
def export_sales_csv(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    current_user: UserSession = Depends(require_role("admin", "analyst")),
) -> StreamingResponse:
    rows = _or_unavailable(
        lambda: _repository(request).sales(
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
        )
    )
    stream = io.StringIO()
    if rows:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return StreamingResponse(
        iter([stream.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales-export.csv"},
    )


@router.get("/reports/monthly", response_class=PlainTextResponse)
def monthly_report(
    request: Request,
    month: str = Query(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
    current_user: UserSession = Depends(require_role("admin", "analyst")),
) -> str:
    repository = _repository(request)
    if repository.active_sales_mode() == "flexible":
        report_sql = """
            SELECT COALESCE(region, 'Unspecified') AS region, SUM(revenue)::DOUBLE AS revenue,
                   COUNT(DISTINCT order_id)::BIGINT AS orders
            FROM analytics.flex_sales WHERE STRFTIME(sales_date, '%Y-%m') = ?
            GROUP BY COALESCE(region, 'Unspecified') ORDER BY revenue DESC
        """
    else:
        report_sql = """
            SELECT region, SUM(net_revenue)::DOUBLE AS revenue, COUNT(DISTINCT order_id)::BIGINT AS orders
            FROM analytics.fact_sales WHERE STRFTIME(date_key, '%Y-%m') = ?
            GROUP BY region ORDER BY revenue DESC
        """
    rows = _or_unavailable(
        lambda: repository.fetch_all(
            report_sql,
            [month],
        )
    )
    total_revenue = sum(float(row["revenue"]) for row in rows)
    total_orders = sum(int(row["orders"]) for row in rows)
    top_region = rows[0]["region"] if rows else "No sales"
    lines = [
        "# Monthly Business Report",
        "",
        f"Period: {month}",
        f"Revenue: {total_revenue:,.2f}",
        f"Completed orders: {total_orders:,}",
        f"Top region: {top_region}",
        "",
        "## Revenue by Region",
    ]
    lines.extend(f"- {row['region']}: {row['revenue']:,.2f} across {row['orders']:,} orders" for row in rows)
    return "\n".join(lines) + "\n"
