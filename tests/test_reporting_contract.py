"""Financial reconciliation across customer workspaces, exports, AI and the API."""

from dataclasses import replace
from types import SimpleNamespace
import time
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

import pytest
from fastapi.testclient import TestClient

from dataflow_ai.ai.assistant import AnalyticsAssistant
from dataflow_ai.api.main import create_app
from dataflow_ai.api.routes import auth
from dataflow_ai.ingestion.flexible_sales import SalesImportError, load_flexible_sales_files
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.settings import Settings
from dataflow_ai.warehouse.repository import WarehouseRepository


def test_reporting_contract_and_restore(tmp_path):
    settings = Settings.from_environment(tmp_path)
    runner = PipelineRunner(settings)
    source = tmp_path / "orders-export.csv"
    source.write_text("order_id,date,status,currency,line_amount,qty,unit_price,discount_amount\n"
                      "001,2026-03-01,completed,USD,9.75,1,10,0.25\n"
                      "1,2026-03-02,paid,USD,10,1,10,0\n"
                      "002,2026-03-03,cancelled,USD,100,1,100,0\n"
                      "003,2026-03-03,refunded,USD,40,1,40,0\n"
                      "004,2026-03-04,completed,EUR,30,1,30,0\n")
    first = runner.run_flexible_sales_files([source])
    repo = WarehouseRepository(settings, "USD")
    kpis = repo.kpi_overview()
    assert (kpis["total_revenue"], kpis["total_sales_amount"], kpis["total_orders"]) == (19.75, 20, 2)
    assert kpis["active_customers"] is None
    assert repo.monthly_summary("2026-03")["total_revenue"] == 19.75
    assert sum(row["revenue"] for row in repo.sales()) == 19.75
    assert WarehouseRepository(settings, "EUR").kpi_overview()["total_revenue"] == 30
    assert AnalyticsAssistant(settings, repo).ask("What is total revenue?").rows[0]["total_revenue"] == 19.75
    assert AnalyticsAssistant(settings, repo).ask("Average order value").rows[0]["average_order_value"] == 9.875
    client = TestClient(create_app(settings))
    assert "19.75" in client.get("/api/reports/monthly?month=2026-03&currency=USD").text
    source.write_text("product,amount\nX,75\n")
    runner.run_flexible_sales_files([source])
    runner.restore_run(first.run_id)
    assert repo.kpi_overview()["total_revenue"] == 19.75


def test_fractional_amount_and_invoice_grain(tmp_path):
    p = tmp_path / "input.csv"
    p.write_text("order_id,amount\n" + "001,1\n" * 2000 + '1,"10,50"\n')
    rows = load_flexible_sales_files([p]).canonical_rows
    assert (rows[0]["order_id"], rows[-1]["order_id"], rows[-1]["revenue"]) == ("001", "1", 10.50)
    p.write_text("order_id,order_total\n001,100\n001,100\n")
    with pytest.raises(SalesImportError, match="one row per invoice"):
        load_flexible_sales_files([p])


def test_managed_workspaces_use_disjoint_storage(tmp_path, monkeypatch):
    settings = replace(Settings.from_environment(tmp_path), auth_required=True, oidc_issuer="fixture",
                       oidc_audience="dataflow-test", oidc_jwks_url="https://identity.example/jwks")
    for subject, amount in [("alice", 12), ("bob", 32)]:
        scoped = settings.workspace("fixture", subject)
        scoped.ensure_directories()
        path = scoped.data_dir / "sales.csv"
        path.write_text(f"product,amount\nWidget,{amount}\n")
        PipelineRunner(scoped).run_flexible_sales_files([path])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    local_keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key()))
    monkeypatch.setattr(auth, "_key_client", lambda _: local_keys)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/kpis/overview").status_code == 401
        for subject, amount in [("alice", 12), ("bob", 32)]:
            token = jwt.encode({"sub": subject, "iss": "fixture", "aud": "dataflow-test",
                                "exp": int(time.time()) + 120}, key, algorithm="RS256")
            headers = {"Authorization": f"Bearer {token}"}
            assert client.get("/api/kpis/overview", headers=headers).json()["total_revenue"] == amount
        assert client.post("/api/auth/login").status_code == 404
