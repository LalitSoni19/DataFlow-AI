from pathlib import Path

from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.sample_data import generate_sample_data
from dataflow_ai.settings import Settings
from dataflow_ai.warehouse.repository import WarehouseRepository


def test_pipeline_builds_warehouse_and_quarantines_invalid_rows(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    generate_sample_data(settings.sample_dir, customer_count=30, order_count=120)
    summary = PipelineRunner(settings).run_from_sources(settings.sample_dir)

    assert summary.status == "completed"
    assert summary.rejected_rows >= 4
    assert summary.accepted_rows > 100
    assert (settings.quarantine_dir / summary.run_id / "orders.jsonl").exists()

    repository = WarehouseRepository(settings)
    overview = repository.kpi_overview()
    assert overview["total_revenue"] > 0
    assert overview["total_orders"] > 0
    status = repository.pipeline_status()
    assert status is not None
    assert status["status"] == "completed"

