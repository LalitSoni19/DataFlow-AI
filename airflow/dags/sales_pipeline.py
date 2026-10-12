"""Airflow TaskFlow DAG for scheduled pipeline execution."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Ensure the repository's 'src' directory is in sys.path regardless of where the DAG is loaded from
repo_root = Path(os.getenv("DATAFLOW_PROJECT_ROOT", Path(__file__).resolve().parents[2])).resolve()
src_dir = str(repo_root / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Import Airflow TaskFlow API with fallback support for Airflow 3, Airflow 2, or missing installation
try:
    from airflow.sdk import dag, task  # type: ignore
    HAS_AIRFLOW = True
except ImportError:
    try:
        from airflow.decorators import dag, task  # type: ignore
        HAS_AIRFLOW = True
    except ImportError:
        HAS_AIRFLOW = False

        def dag(*args: Any, **kwargs: Any) -> Any:
            def decorator(func: Any) -> Any:
                return func
            return decorator

        def task(*args: Any, **kwargs: Any) -> Any:
            if args and callable(args[0]):
                return args[0]
            def decorator(func: Any) -> Any:
                return func
            return decorator

# Use pendulum if installed (standard in Airflow environments), otherwise standard datetime
try:
    from pendulum import datetime as pendulum_datetime  # type: ignore
    dag_start_date = pendulum_datetime(2026, 1, 1, tz="UTC")
except ImportError:
    dag_start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)

from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.ingestion import is_supported_sales_file
from dataflow_ai.settings import Settings
from dataflow_ai.warehouse.repository import WarehouseRepository


@dag(
    dag_id="dataflow_ai_sales_pipeline",
    schedule="@daily",
    start_date=dag_start_date,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=2),
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["dataflow-ai", "analytics"],
    doc_md="""## DataFlow AI pipeline

Checks source availability, runs the validated ETL pipeline, verifies the warehouse,
and exposes a compact outcome in the Airflow task log.
""",
)
def dataflow_ai_sales_pipeline():
    @task
    def check_sources() -> str:
        settings = Settings.from_environment()
        source_dir = Path(os.getenv("DATAFLOW_SOURCE_DIR", str(settings.sample_dir))).resolve()
        if not source_dir.is_dir():
            raise FileNotFoundError(f"Configured DATAFLOW_SOURCE_DIR does not exist: {source_dir}")
        curated = {"customers.csv", "products.csv", "orders.csv", "order_items.csv", "website_events.json"}
        names = {path.name for path in source_dir.iterdir() if path.is_file()}
        if not curated.issubset(names) and not any(is_supported_sales_file(path) for path in source_dir.iterdir()):
            raise FileNotFoundError(
                "No supported sales sources found. Provide either the full curated batch or a CSV/TSV/XLSX/JSON/JSONL sales export."
            )
        return str(source_dir)

    @task(execution_timeout=timedelta(minutes=45))
    def run_pipeline(source_dir: str) -> dict:
        settings = Settings.from_environment()
        return PipelineRunner(settings).run_from_sources(Path(source_dir)).to_dict()

    @task
    def verify_warehouse(summary: dict) -> dict:
        repository = WarehouseRepository(Settings.from_environment())
        overview = repository.kpi_overview()
        if summary["status"] != "completed" or float(overview["total_revenue"]) <= 0:
            raise ValueError("Pipeline did not produce a usable analytics warehouse.")
        return {"run_id": summary["run_id"], "total_revenue": overview["total_revenue"]}

    verify_warehouse(run_pipeline(check_sources()))


if HAS_AIRFLOW:
    dataflow_ai_sales_pipeline()

