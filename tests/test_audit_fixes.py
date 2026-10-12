"""Comprehensive verification of all security audit checkups.

Validates:
1. Webhook endpoints fail closed when secrets are not configured (no hardcoded fallback secrets).
2. Password reset tokens are NEVER leaked in API responses.
3. Password reset confirm endpoint is rate-limited.
4. Password reset with registered email updates user password.
5. GDPR self-service user account deletion endpoint works and revokes sessions.
6. Rate limiter prevents X-Forwarded-For spoofing from untrusted proxies.
7. Server-side RBAC blocks low-privilege customer roles from executive reports and sales CSV exports.
8. PII masking with mask_names=True anonymizes customer names for external LLM transmission.
9. Free-text PII redaction redacts customer names in addition to emails and phone numbers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataflow_ai.api.main import create_app
from dataflow_ai.api.rate_limiter import get_client_ip, reset_rate_limiter
from dataflow_ai.api.routes.auth import (
    RESET_TOKEN_STORE,
    TOKEN_BLACKLIST,
    _USER_REGISTRY,
    create_jwt,
    verify_jwt,
)
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.sample_data import generate_sample_data
from dataflow_ai.security import hash_password, verify_password
from dataflow_ai.settings import Settings
from dataflow_ai.utils import mask_record_pii, redact_pii_text


@pytest.fixture(autouse=True)
def clean_security_state() -> None:
    reset_rate_limiter()
    TOKEN_BLACKLIST.clear()
    RESET_TOKEN_STORE.clear()


def test_payments_fail_closed_without_secrets(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    # Calling Stripe webhook when STRIPE_WEBHOOK_SECRET is not configured returns 500
    stripe_resp = client.post("/api/payments/webhook/stripe", content=b"{}")
    assert stripe_resp.status_code == 500
    assert "STRIPE_WEBHOOK_SECRET must be set" in stripe_resp.json()["detail"]

    # Calling Razorpay webhook when RAZORPAY_WEBHOOK_SECRET is not configured returns 500
    rzp_resp = client.post("/api/payments/webhook/razorpay", content=b"{}")
    assert rzp_resp.status_code == 500
    assert "RAZORPAY_WEBHOOK_SECRET must be set" in rzp_resp.json()["detail"]


def test_no_reset_token_in_api_response(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.post("/api/auth/password-reset", json={"email": "admin@dataflow.ai"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    # reset_token must never be returned in the client response
    assert body.get("reset_token") is None


def test_password_reset_confirm_is_rate_limited(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    payload = {"token": "a" * 24, "new_password": "NewSecurePassword123!"}
    for _ in range(5):
        client.post("/api/auth/password-reset/confirm", json=payload)

    # 6th attempt within window must be rejected with 429 Too Many Requests
    blocked = client.post("/api/auth/password-reset/confirm", json=payload)
    assert blocked.status_code == 429
    assert "Too many requests" in blocked.json()["detail"]


def test_password_reset_and_login_flow(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    raw_token = RESET_TOKEN_STORE.generate_reset_token("analyst@dataflow.ai")
    new_pass = "UpdatedAnalystPass999!"

    confirm_resp = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": new_pass},
    )
    assert confirm_resp.status_code == 200
    assert "successfully updated" in confirm_resp.json()["message"]

    # Verify login succeeds with the newly updated password
    login_resp = client.post("/api/auth/login", json={"username": "analyst", "password": new_pass})
    assert login_resp.status_code == 200
    assert login_resp.json()["status"] == "authenticated"


def test_user_account_self_deletion_flow(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    # Temporarily register a test user in registry
    _USER_REGISTRY["test_delete_me"] = {
        "user_id": "temp-user-001",
        "email": "temp@example.com",
        "role": "customer",
        "password_hash": hash_password("TempPassword123!"),
    }

    token = create_jwt(user_id="temp-user-001", role="customer", secret=settings.jwt_secret)
    headers = {"Authorization": f"Bearer {token}"}

    del_resp = client.delete("/api/auth/account", headers=headers)
    assert del_resp.status_code == 200
    assert "permanently deleted" in del_resp.json()["message"]

    # User registry record removed
    assert "test_delete_me" not in _USER_REGISTRY

    # Token is revoked and rejected on verification
    with pytest.raises(ValueError, match="revoked"):
        verify_jwt(token, settings.jwt_secret)


def test_rbac_blocks_customer_from_monthly_reports_and_csv_exports(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    generate_sample_data(settings.sample_dir, customer_count=10, order_count=30)
    PipelineRunner(settings).run_from_sources(settings.sample_dir)
    client = TestClient(create_app(settings))

    customer_token = create_jwt(user_id="C-0001", role="customer", secret=settings.jwt_secret)
    cust_headers = {"Authorization": f"Bearer {customer_token}"}

    # Customer role is blocked from executive reports (403 Forbidden)
    rep_resp = client.get("/api/reports/monthly?month=2026-08", headers=cust_headers)
    assert rep_resp.status_code == 403
    assert "Forbidden" in rep_resp.json()["detail"]

    # Customer role is blocked from full sales CSV export (403 Forbidden)
    csv_resp = client.get("/api/exports/sales.csv", headers=cust_headers)
    assert csv_resp.status_code == 403
    assert "Forbidden" in csv_resp.json()["detail"]

    # Analyst role can access both
    analyst_token = create_jwt(user_id="analyst-001", role="analyst", secret=settings.jwt_secret)
    analyst_headers = {"Authorization": f"Bearer {analyst_token}"}
    assert client.get("/api/reports/monthly?month=2026-08", headers=analyst_headers).status_code == 200
    assert client.get("/api/exports/sales.csv", headers=analyst_headers).status_code == 200


def test_rate_limiter_spoof_protection() -> None:
    from fastapi import Request

    class DummyClient:
        host = "203.0.113.195"  # Public untrusted client IP

    # Create dummy request with spoofed X-Forwarded-For
    scope = {
        "type": "http",
        "client": ("203.0.113.195", 50000),
        "headers": [(b"x-forwarded-for", b"1.1.1.1")],
    }
    request = Request(scope)

    # Untrusted client IP must be returned, not spoofed header
    extracted_ip = get_client_ip(request)
    assert extracted_ip == "203.0.113.195"


def test_pii_masking_with_name_anonymization() -> None:
    record = {
        "customer_id": "C-0001",
        "customer_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "555-444-3333",
    }
    # Without mask_names, name remains readable for UI
    standard = mask_record_pii(record, mask_names=False)
    assert standard["customer_name"] == "Jane Doe"
    assert standard["email"] == "j***@example.com"
    assert standard["phone"] == "[REDACTED_PHONE]"

    # With mask_names=True (for LLM transmission), name is pseudonymized
    masked = mask_record_pii(record, mask_names=True)
    assert masked["customer_name"] == "J*** D***"
    assert masked["email"] == "j***@example.com"
    assert masked["phone"] == "[REDACTED_PHONE]"


def test_redact_pii_text_anonymizes_customer_names() -> None:
    text = "Please check orders for customer Alice Wonderland and contact Bob Marley"
    redacted = redact_pii_text(text)
    assert "Alice Wonderland" not in redacted
    assert "[REDACTED_NAME]" in redacted
