"""End-to-end coverage for schema-tolerant sales-file imports."""

from __future__ import annotations

from pathlib import Path

import pytest

from dataflow_ai.ai.assistant import AnalyticsAssistant
from dataflow_ai.ingestion.flexible_sales import SalesImportError, load_flexible_sales_files
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.settings import Settings
from dataflow_ai.warehouse.repository import WarehouseRepository


def _write_sales_csv(path: Path) -> None:
    path.write_text(
        "Invoice Date,Invoice Number,Client Name,Item Name,Product Category,Zone,Qty,Total Amount\n"
        "2026-08-01,INV-1001,Alice Singh,Widget A,Hardware,North,2,1500.50\n"
        "2026-08-02,INV-1002,Bob Shah,Widget B,Software,South,1,800\n",
        encoding="utf-8",
    )


def test_arbitrary_sales_csv_becomes_active_catalog_and_answers_offline(tmp_path: Path) -> None:
    source = tmp_path / "August_Export_From_CRM.csv"
    _write_sales_csv(source)
    settings = Settings.from_environment(tmp_path)
    # This repository represents a long-lived API/dashboard process.  It must
    # see a model activation committed by the separate pipeline worker below.
    repository = WarehouseRepository(settings)
    assert repository.active_sales_mode() == "curated"

    summary = PipelineRunner(settings).run_from_sources(tmp_path)

    assert summary.status == "completed"
    assert summary.ingested_rows == 2
    assert repository.active_sales_mode() == "flexible"
    assert repository.kpi_overview()["total_revenue"] == pytest.approx(2300.5)
    profile = repository.flexible_sales_profile()
    assert profile is not None
    assert profile["semantic_mapping"]["revenue"] == "total_amount"
    catalog = repository.ai_schema_catalog()
    assert "TABLE analytics.flex_sales" in catalog
    assert "TABLE analytics.sales_upload" in catalog
    assert "analytics.fact_sales" not in catalog

    response = AnalyticsAssistant(settings, repository).ask("What is the total revenue?")
    assert response.data_grounded is True
    assert response.sql is not None
    assert "analytics.flex_sales" in response.sql
    assert response.rows[0]["total_revenue"] == pytest.approx(2300.5)


def test_xlsx_sales_export_is_profiled(tmp_path: Path) -> None:
    from openpyxl import Workbook

    source = tmp_path / "sales.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Date", "Order Number", "Product", "Revenue"])
    sheet.append(["2026-09-01", "O-1", "Annual plan", 12000])
    workbook.save(source)

    imported = load_flexible_sales_files([source])

    assert imported.profile.semantic_mapping["sales_date"] == "date"
    assert imported.profile.semantic_mapping["revenue"] == "revenue"
    assert imported.canonical_rows[0]["revenue"] == 12000.0


def test_mixed_sales_exports_use_compatible_aliases_per_row(tmp_path: Path) -> None:
    first = tmp_path / "pos_export.csv"
    first.write_text(
        "Invoice Number,Sales Amount,Item\nINV-1,125.50,Keyboard\n",
        encoding="utf-8",
    )
    second = tmp_path / "crm_export.csv"
    second.write_text(
        "Order ID,Revenue,Product\nORD-2,275.25,Monitor\n",
        encoding="utf-8",
    )

    imported = load_flexible_sales_files([first, second])

    assert [row["revenue"] for row in imported.canonical_rows] == [125.5, 275.25]
    assert [row["order_id"] for row in imported.canonical_rows] == ["INV-1", "ORD-2"]


def test_non_sales_file_is_rejected_with_a_actionable_message(tmp_path: Path) -> None:
    source = tmp_path / "employees.csv"
    source.write_text("employee_id,full_name,department\n1,Alice,Engineering\n", encoding="utf-8")

    with pytest.raises(SalesImportError, match="does not look like a sales export"):
        load_flexible_sales_files([source])


def test_xlsx_sales_export_with_blank_leading_rows(tmp_path: Path) -> None:
    from openpyxl import Workbook

    source = tmp_path / "sales_blank_rows.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append([])
    sheet.append([None, None, None])
    sheet.append(["Date", "Order Number", "Revenue"])
    sheet.append(["2026-09-02", "O-2", 450.0])
    workbook.save(source)

    imported = load_flexible_sales_files([source])
    assert imported.canonical_rows[0]["revenue"] == 450.0

