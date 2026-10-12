from pathlib import Path

from dataflow_ai.ai.assistant import AnalyticsAssistant
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.sample_data import generate_sample_data
from dataflow_ai.settings import Settings


def test_assistant_returns_a_sql_trace_from_warehouse(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    generate_sample_data(settings.sample_dir, customer_count=30, order_count=120)
    PipelineRunner(settings).run_from_sources(settings.sample_dir)

    response = AnalyticsAssistant(settings).ask("Why did revenue decrease in August 2026?")

    assert response.sql is not None
    assert "analytics.fact_sales" in response.sql
    assert response.rows
    assert "Revenue" in response.answer

