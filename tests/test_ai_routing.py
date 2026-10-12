"""Typed sales-AI routing and bounded repair behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from dataflow_ai.ai.assistant import AnalyticsAssistant
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.sample_data import generate_sample_data
from dataflow_ai.settings import Settings


def test_sales_guidance_is_explicitly_not_data_grounded(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    assistant = AnalyticsAssistant(settings)
    mock_llm = MagicMock()
    mock_llm.complete.return_value = (
        '{"intent":"sales_guidance","sql":null,'
        '"answer":"Use discovery questions to qualify budget, authority, need, and timeline."}'
    )

    with patch.object(assistant, "llm", mock_llm):
        response = assistant.ask("How should I qualify enterprise sales leads?")

    assert response.mode == "sales_guidance"
    assert response.data_grounded is False
    assert response.sql is None
    assert "discovery" in response.answer.lower()


def test_bad_model_sql_gets_one_safe_repair_attempt(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    generate_sample_data(settings.sample_dir, customer_count=15, order_count=60)
    PipelineRunner(settings).run_from_sources(settings.sample_dir)
    assistant = AnalyticsAssistant(settings)
    mock_llm = MagicMock()
    mock_llm.complete.side_effect = [
        '{"intent":"warehouse_query","sql":"SELECT * FROM forbidden.table","answer":null}',
        '{"sql":"SELECT SUM(net_revenue)::DOUBLE AS total_revenue FROM analytics.fact_sales"}',
    ]

    with patch.object(assistant, "llm", mock_llm):
        response = assistant.ask("What is total revenue?")

    assert response.rows
    assert response.sql is not None
    assert "analytics.fact_sales" in response.sql
    assert response.warning is not None
    assert mock_llm.complete.call_count == 2
