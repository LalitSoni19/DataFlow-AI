"""Tests for personal data (PII) protection, text redaction, password security, and GDPR erasure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dataflow_ai.ai.assistant import AnalyticsAssistant
from dataflow_ai.ai.sql_guard import SafeSQLExecutor
from dataflow_ai.api.main import create_app
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.sample_data import generate_sample_data
from dataflow_ai.security import hash_password, verify_password
from dataflow_ai.settings import Settings
from dataflow_ai.utils import mask_email, mask_name, mask_record_pii, redact_pii_text
from dataflow_ai.warehouse.repository import WarehouseRepository


def test_mask_email_and_name() -> None:
    assert mask_email("alice.smith@example.com") == "a***@example.com"
    assert mask_email("bob@company.org") == "b***@company.org"
    assert mask_email("") == ""
    assert mask_email(None) == ""

    assert mask_name("Alice Smith") == "A*** S***"
    assert mask_name("Bob") == "B***"
    assert mask_name("") == ""
    assert mask_name(None) == ""

    record = {
        "customer_id": "C-0001",
        "customer_name": "John Doe",
        "email": "john.doe@example.com",
        "region": "North",
    }
    masked = mask_record_pii(record)
    assert masked["customer_id"] == "C-0001"
    assert masked["region"] == "North"
    assert masked["email"] == "j***@example.com"
    assert masked["customer_name"] == "John Doe"


def test_redact_pii_text() -> None:
    text = "User email is customer.vip@test.org and telephone is 555-123-4567, token Bearer secret_123"
    redacted = redact_pii_text(text)
    assert "customer.vip@test.org" not in redacted
    assert "555-123-4567" not in redacted
    assert "secret_123" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    assert "Bearer [REDACTED]" in redacted


def test_scrypt_password_hashing_and_verification() -> None:
    pwd = "ComplexP@ssword!2026"
    hashed = hash_password(pwd)
    assert hashed.startswith("scrypt$16384$8$1$")
    assert pwd not in hashed

    # Verify correct password matches
    assert verify_password(pwd, hashed) is True

    # Verify incorrect password fails
    assert verify_password("WrongPassword123", hashed) is False

    # Verify tampering or invalid input fails gracefully
    assert verify_password(pwd, "invalid$format") is False
    assert verify_password("", hashed) is False


def test_assistant_redacts_pii_in_llm_prompt_and_audit(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    assistant = AnalyticsAssistant(settings)
    assistant.repository.initialize()

    mock_llm = MagicMock()
    mock_llm.complete.return_value = "SELECT * FROM analytics.fact_sales LIMIT 1"

    with patch.object(assistant, "llm", mock_llm):
        question = "What did customer user.vip@domain.com buy at 555-987-6543?"
        assistant.ask(question)

        # Check prompt sent to LLM was scrubbed of PII
        call_args = mock_llm.complete.call_args[0]
        user_prompt = call_args[1]
        assert "user.vip@domain.com" not in user_prompt
        assert "[REDACTED_EMAIL]" in user_prompt

        # Check DuckDB audit log was scrubbed of PII
        audit_rows = assistant.repository.fetch_all(
            "SELECT question FROM governance.ai_audit_log ORDER BY asked_at DESC LIMIT 1"
        )
        assert audit_rows
        assert "user.vip@domain.com" not in audit_rows[0]["question"]
        assert "[REDACTED_EMAIL]" in audit_rows[0]["question"]


def test_sql_executor_applies_field_level_masking(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    generate_sample_data(settings.sample_dir, customer_count=10, order_count=30)
    PipelineRunner(settings).run_from_sources(settings.sample_dir)

    executor = SafeSQLExecutor(WarehouseRepository(settings))
    result = executor.execute("SELECT customer_id, customer_name, email FROM analytics.dim_customer LIMIT 5")
    assert result.rows
    for row in result.rows:
        assert "@" in row["email"]
        assert row["email"].startswith(("c***", "C***"))
        assert row["customer_name"] and "Customer" in row["customer_name"]


def test_gdpr_customer_erasure_and_anonymization(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    generate_sample_data(settings.sample_dir, customer_count=10, order_count=40)
    PipelineRunner(settings).run_from_sources(settings.sample_dir)

    repository = WarehouseRepository(settings)
    target_id = "C-0001"

    # Pre-check: customer exists and is not anonymized
    pre_status = repository.get_customer_privacy_status(target_id)
    assert pre_status is not None
    assert pre_status["is_anonymized"] is False
    assert "anonymized" not in pre_status["email"]

    # Pre-check: tracking events exist
    pre_events = repository.fetch_all(
        "SELECT COUNT(*) AS count FROM staging.website_events WHERE customer_id = ?",
        [target_id],
    )
    pre_event_count = pre_events[0]["count"]

    # Execute GDPR erasure
    receipt = repository.delete_or_anonymize_customer(target_id)
    assert receipt["status"] == "anonymized"
    assert receipt["customer_id"] == target_id

    # Verify staging.customers is anonymized
    post_status = repository.get_customer_privacy_status(target_id)
    assert post_status is not None
    assert post_status["is_anonymized"] is True
    assert post_status["name"] == "Anonymized User"
    assert post_status["email"] == "anonymized_c_0001@privacy.local"

    # Verify analytics.dim_customer mart immediately reflects anonymization
    dim_row = repository.fetch_all(
        "SELECT customer_name, email, city FROM analytics.dim_customer WHERE customer_id = ?",
        [target_id],
    )[0]
    assert dim_row["customer_name"] == "Anonymized User"
    assert "anonymized" in dim_row["email"]
    assert dim_row["city"] == "REDACTED"

    # Verify web tracking events were purged
    post_events = repository.fetch_all(
        "SELECT COUNT(*) AS count FROM staging.website_events WHERE customer_id = ?",
        [target_id],
    )
    assert post_events[0]["count"] == 0

    # Verify financial numbers remain intact in fact_sales
    sales_check = repository.fetch_all(
        "SELECT COUNT(*) AS count, SUM(net_revenue) AS revenue FROM analytics.fact_sales WHERE customer_id = ?",
        [target_id],
    )
    assert sales_check[0]["count"] > 0
    assert sales_check[0]["revenue"] > 0


def test_privacy_api_endpoints_and_security_headers(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    generate_sample_data(settings.sample_dir, customer_count=10, order_count=30)
    PipelineRunner(settings).run_from_sources(settings.sample_dir)

    client = TestClient(create_app(settings))

    # Test Security Headers
    health_resp = client.get("/health")
    assert health_resp.headers["x-content-type-options"] == "nosniff"
    assert health_resp.headers["x-frame-options"] == "DENY"
    assert "max-age=" in health_resp.headers["strict-transport-security"]

    # Test GET Privacy Status
    status_resp = client.get("/api/privacy/customers/C-0002/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["is_anonymized"] is False

    # Test POST Privacy Delete (Anonymize)
    del_resp = client.post("/api/privacy/customers/C-0002/delete")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "anonymized"

    # Test Verified Post Status
    verify_resp = client.get("/api/privacy/customers/C-0002/status")
    assert verify_resp.status_code == 200
    assert verify_resp.json()["is_anonymized"] is True
    assert verify_resp.json()["name"] == "Anonymized User"

    # Test Non-existent Customer returns 404
    missing_resp = client.get("/api/privacy/customers/C-9999/status")
    assert missing_resp.status_code == 404
