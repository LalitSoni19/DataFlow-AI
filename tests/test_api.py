from pathlib import Path

from fastapi.testclient import TestClient

from dataflow_ai.api.main import create_app
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.sample_data import generate_sample_data
from dataflow_ai.settings import Settings


def test_api_serves_trusted_metrics_and_ai_trace(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    generate_sample_data(settings.sample_dir, customer_count=30, order_count=120)
    PipelineRunner(settings).run_from_sources(settings.sample_dir)
    client = TestClient(create_app(settings))

    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/kpis/overview").json()["total_revenue"] > 0
    response = client.post("/api/ask", json={"question": "What are the top 3 products by revenue?"})
    assert response.status_code == 200
    assert "analytics.fact_sales" in response.json()["sql"]

