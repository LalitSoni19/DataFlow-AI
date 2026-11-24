"""Tests for pre-deployment readiness: security headers, rate limiting, error shielding, and production validation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataflow_ai.api.main import create_app
from dataflow_ai.api.rate_limiter import reset_rate_limiter
from dataflow_ai.settings import Settings


@pytest.fixture(autouse=True)
def clean_rate_limits_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_rate_limiter()
    monkeypatch.delenv("DATAFLOW_DEBUG", raising=False)
    monkeypatch.delenv("DATAFLOW_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DATAFLOW_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATAFLOW_CORS_ORIGINS", raising=False)


def test_complete_security_headers_present(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    response = client.get("/health")
    assert response.status_code == 200

    headers = response.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "max-age=31536000" in headers["strict-transport-security"]
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in headers["permissions-policy"]
    assert "x-correlation-id" in headers
    assert len(headers["x-correlation-id"]) >= 32


def test_unhandled_error_shields_stack_trace_and_returns_correlation_id(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    app = create_app(settings)

    @app.get("/api/test-simulated-error")
    def fail_endpoint() -> None:
        raise RuntimeError("Sensitive internal database connection failed: postgresql://admin:secret@10.0.0.1/db")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/test-simulated-error")

    assert response.status_code == 500
    body = response.json()

    assert "Sensitive internal database" not in response.text
    assert "postgresql://" not in response.text
    assert "Traceback" not in response.text
    assert "File " not in response.text

    assert "An internal server error occurred" in body["detail"]
    assert "correlation_id" in body
    assert body["correlation_id"] == response.headers.get("x-correlation-id")


def test_rate_limiting_on_login(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    payload = {"username": "testuser", "password": "SuperSecurePassword123"}

    for _ in range(5):
        resp = client.post("/api/auth/login", json=payload)
        assert resp.status_code == 401

    blocked_resp = client.post("/api/auth/login", json=payload)
    assert blocked_resp.status_code == 429
    assert "Too many requests" in blocked_resp.json()["detail"]
    assert "retry-after" in blocked_resp.headers


def test_rate_limiting_on_password_reset(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    payload = {"email": "user@example.com"}

    for _ in range(3):
        resp = client.post("/api/auth/password-reset", json=payload)
        assert resp.status_code == 200

    blocked_resp = client.post("/api/auth/password-reset", json=payload)
    assert blocked_resp.status_code == 429
    assert "Too many requests" in blocked_resp.json()["detail"]
    assert "retry-after" in blocked_resp.headers


def test_production_validator_enforces_database_tls(tmp_path: Path) -> None:
    insecure_settings = replace(
        Settings.from_environment(tmp_path),
        environment="production",
        database_url="postgresql://app_user:valid_secret_123@db.internal:5432/warehouse",
    )
    errors = insecure_settings.validate_for_deployment()
    assert any("TLS/SSL" in err for err in errors)

    secure_settings = replace(
        insecure_settings,
        database_url="postgresql://app_user:valid_secret_123@db.internal:5432/warehouse?sslmode=require",
    )
    errors_secure = secure_settings.validate_for_deployment()
    assert not any("TLS/SSL" in err for err in errors_secure)


def test_production_validator_blocks_default_credentials(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    bad_creds_settings = replace(
        settings,
        environment="production",
        database_url="postgresql://postgres:postgres@db.internal:5432/warehouse?sslmode=require",
    )
    errors = bad_creds_settings.validate_for_deployment()
    assert any("default database credentials" in err for err in errors)


def test_production_validator_blocks_wildcard_cors(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    wildcard_settings = replace(
        settings,
        environment="production",
        cors_origins=("*",),
    )
    errors = wildcard_settings.validate_for_deployment()
    assert any("Wildcard CORS" in err for err in errors)


def test_debug_mode_defaults_to_false(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    assert settings.debug is False


def test_create_app_aborts_on_production_validation_failure(tmp_path: Path) -> None:
    invalid_prod_settings = replace(
        Settings.from_environment(tmp_path),
        environment="production",
        cors_origins=("*",),
    )
    with pytest.raises(RuntimeError, match="Production deployment validation failed"):
        create_app(invalid_prod_settings)

