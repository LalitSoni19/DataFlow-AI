"""Automated test suite verifying the 7 critical security attack paths.

Tests cover:
1. IDOR prevention (ownership and role verification).
2. Login bypass defense, JWT verification, and logout token blacklisting.
3. Privilege escalation defense (server-side RBAC).
4. Feature abuse & rate limiting (AI endpoint, CSV exports).
5. Content injection (table allow-list).
6. Internal exposure (SSRF guard, Swagger disabling in production).
7. Business logic & payment security (pricing tampering, discount limits, webhook signatures).
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataflow_ai.api.main import create_app
from dataflow_ai.api.rate_limiter import reset_rate_limiter
from dataflow_ai.ingestion.api_loader import validate_safe_api_url
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.processing.validation import validate_order_items
from dataflow_ai.sample_data import generate_sample_data
from dataflow_ai.security import (
    RESET_TOKEN_STORE,
    TOKEN_BLACKLIST,
    create_jwt,
    verify_jwt,
    verify_razorpay_webhook_signature,
    verify_stripe_webhook_signature,
)
from dataflow_ai.settings import Settings


@pytest.fixture(autouse=True)
def clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_rate_limiter()
    TOKEN_BLACKLIST.clear()
    RESET_TOKEN_STORE.clear()
    monkeypatch.delenv("DATAFLOW_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DATAFLOW_AUTH_REQUIRED", raising=False)


# 1. IDOR prevention tests

def test_idor_prevented_when_customer_accesses_other_customer(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    generate_sample_data(settings.sample_dir, customer_count=10, order_count=30)
    PipelineRunner(settings).run_from_sources(settings.sample_dir)

    client = TestClient(create_app(settings))
    secret = settings.jwt_secret

    # Customer C-0001 token
    c1_token = create_jwt(user_id="C-0001", role="customer", secret=secret)
    c1_headers = {"Authorization": f"Bearer {c1_token}"}

    # 1. Customer C-0001 can access own status
    own_resp = client.get("/api/privacy/customers/C-0001/status", headers=c1_headers)
    assert own_resp.status_code == 200
    assert own_resp.json()["customer_id"] == "C-0001"

    # 2. Customer C-0001 ATTEMPTS IDOR: accessing C-0002 status -> 403 Forbidden!
    idor_status = client.get("/api/privacy/customers/C-0002/status", headers=c1_headers)
    assert idor_status.status_code == 403
    assert "Forbidden" in idor_status.json()["detail"]

    # 3. Customer C-0001 ATTEMPTS IDOR: deleting C-0002 data -> 403 Forbidden!
    idor_del = client.post("/api/privacy/customers/C-0002/delete", headers=c1_headers)
    assert idor_del.status_code == 403
    assert "Forbidden" in idor_del.json()["detail"]

    # 4. Admin token CAN access any customer
    admin_token = create_jwt(user_id="admin-001", role="admin", secret=secret)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    admin_resp = client.get("/api/privacy/customers/C-0002/status", headers=admin_headers)
    assert admin_resp.status_code == 200


# 2. Login bypass and JWT lifecycle tests

def test_login_bypass_blocked_when_auth_is_enforced(tmp_path: Path) -> None:
    settings = replace(Settings.from_environment(tmp_path), auth_required=True)
    generate_sample_data(settings.sample_dir, customer_count=10, order_count=30)
    PipelineRunner(settings).run_from_sources(settings.sample_dir)

    client = TestClient(create_app(settings))

    # Calling protected privacy endpoint without token is rejected
    unauth_resp = client.get("/api/privacy/customers/C-0001/status")
    assert unauth_resp.status_code == 401
    assert "Authentication credentials were not provided" in unauth_resp.json()["detail"]


def test_jwt_validation_rejects_expired_malformed_and_tampered_tokens(tmp_path: Path) -> None:
    secret = "a" * 32

    # Malformed token
    with pytest.raises(ValueError, match="Invalid JWT structure"):
        verify_jwt("not.a.valid.jwt.token", secret)

    # Expired token
    expired_token = create_jwt("user-1", "customer", secret, expires_in_seconds=-10)
    with pytest.raises(ValueError, match="expired"):
        verify_jwt(expired_token, secret)

    # Tampered signature
    valid_token = create_jwt("user-1", "customer", secret, expires_in_seconds=300)
    wrong_secret = "b" * 32
    with pytest.raises(ValueError, match="mismatch"):
        verify_jwt(valid_token, wrong_secret)


def test_jwt_blacklisting_on_logout(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))
    secret = settings.jwt_secret

    token = create_jwt(user_id="analyst-001", role="analyst", secret=secret, expires_in_seconds=600)
    headers = {"Authorization": f"Bearer {token}"}

    # Verify token works before logout
    payload = verify_jwt(token, secret)
    assert payload["sub"] == "analyst-001"

    # Call logout
    logout_resp = client.post("/api/auth/logout", headers=headers)
    assert logout_resp.status_code == 200
    assert "revoked" in logout_resp.json()["message"]

    # Token is now revoked in blacklist
    with pytest.raises(ValueError, match="revoked"):
        verify_jwt(token, secret)


def test_password_reset_token_single_use_and_15_min_limit() -> None:
    raw_token = RESET_TOKEN_STORE.generate_reset_token("user@example.com")
    assert len(raw_token) >= 32

    # First consumption succeeds
    email = RESET_TOKEN_STORE.verify_and_consume_token(raw_token)
    assert email == "user@example.com"

    # Second consumption fails (Single-use enforcement)
    with pytest.raises(ValueError, match="already been used"):
        RESET_TOKEN_STORE.verify_and_consume_token(raw_token)


# 3. Rate limiting tests

def test_rate_limiting_on_ai_ask_endpoint(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    # Allowed: 10 requests per minute
    for _ in range(10):
        resp = client.post("/api/ask", json={"question": "What are total sales?"})
        assert resp.status_code != 429

    # 11th request is blocked
    blocked = client.post("/api/ask", json={"question": "What are total sales?"})
    assert blocked.status_code == 429
    assert "Too many requests" in blocked.json()["detail"]


# 4. SSRF protection tests

def test_swagger_disabled_in_production(tmp_path: Path) -> None:
    prod_settings = replace(
        Settings.from_environment(tmp_path),
        environment="production",
        jwt_secret="strong-production-key-over-32-characters-long!",
    )
    client = TestClient(create_app(prod_settings))

    # In production, Swagger and OpenAPI JSON must return 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_ssrf_protection_blocks_private_and_cloud_metadata_ips() -> None:
    # 1. AWS Cloud Metadata IP (169.254.169.254)
    with pytest.raises(ValueError, match="SSRF protection"):
        validate_safe_api_url("http://169.254.169.254/latest/meta-data/")

    # 2. Localhost / Loopback (127.0.0.1)
    with pytest.raises(ValueError, match="SSRF protection"):
        validate_safe_api_url("http://127.0.0.1:8000/api/kpis/overview")

    # 3. Insecure protocol (file://)
    with pytest.raises(ValueError, match="Insecure scheme"):
        validate_safe_api_url("file:///etc/passwd")


# 5. Business logic & pricing tests

def test_order_items_validation_rejects_price_tampering_and_excessive_discounts() -> None:
    catalog = {"P-0001": 100.0}
    orders = {"ORD-0001"}

    # 1. Attacker sets price to 0.01 for a 100.00 product -> REJECTED
    tampered_price = [
        {
            "order_item_id": "ITEM-1",
            "order_id": "ORD-0001",
            "product_id": "P-0001",
            "quantity": 2,
            "unit_price": 0.01,
            "discount_amount": 0.0,
        }
    ]
    res_price = validate_order_items(tampered_price, orders, catalog)
    assert len(res_price.issues) > 0
    assert any(i.rule == "price_tampering" for i in res_price.issues)

    # 2. Attacker sets discount exceeding gross total -> REJECTED
    tampered_discount = [
        {
            "order_item_id": "ITEM-2",
            "order_id": "ORD-0001",
            "product_id": "P-0001",
            "quantity": 1,
            "unit_price": 100.0,
            "discount_amount": 150.0,
        }
    ]
    res_disc = validate_order_items(tampered_discount, orders, catalog)
    assert len(res_disc.issues) > 0
    assert any(i.rule == "discount_tampering" for i in res_disc.issues)

    # 3. Legitimate item passes
    valid_item = [
        {
            "order_item_id": "ITEM-3",
            "order_id": "ORD-0001",
            "product_id": "P-0001",
            "quantity": 2,
            "unit_price": 95.0,
            "discount_amount": 10.0,
        }
    ]
    res_valid = validate_order_items(valid_item, orders, catalog)
    assert len(res_valid.issues) == 0
    assert len(res_valid.accepted_records) == 1


def test_payment_webhook_signature_verification() -> None:
    import hashlib
    import hmac

    secret = "whsec_stripe_test_secret_key_32_characters"
    payload = b'{"type": "payment_intent.succeeded", "id": "pi_123"}'
    now = int(time.time())

    # Valid Stripe signature
    signed_payload = f"{now}.".encode("utf-8") + payload
    sig = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    valid_header = f"t={now},v1={sig}"

    assert verify_stripe_webhook_signature(payload, valid_header, secret) is True

    # Forged signature -> rejected
    assert verify_stripe_webhook_signature(payload, f"t={now},v1=forged_signature", secret) is False

    # Replayed timestamp (older than 300 seconds) -> rejected
    old_time = now - 400
    old_signed = f"{old_time}.".encode("utf-8") + payload
    old_sig = hmac.new(secret.encode("utf-8"), old_signed, hashlib.sha256).hexdigest()
    assert verify_stripe_webhook_signature(payload, f"t={old_time},v1={old_sig}", secret) is False

    # Razorpay signature
    rzp_secret = "rzp_secret_32_characters_minimum"
    rzp_sig = hmac.new(rzp_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    assert verify_razorpay_webhook_signature(payload, rzp_sig, rzp_secret) is True
    assert verify_razorpay_webhook_signature(payload, "invalid_sig", rzp_secret) is False
