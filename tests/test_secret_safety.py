"""Tests for secret safety, redaction utilities, environment loading, and git protection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from dataflow_ai.ai.assistant import AnalyticsAssistant
from dataflow_ai.ai.llm_client import LLMClient, LLMError
from dataflow_ai.ingestion.api_loader import ApiIngestor
from dataflow_ai.ingestion.raw_store import RawDataLake
from dataflow_ai.settings import Settings
from dataflow_ai.utils import sanitize_secret_string, sanitize_uri


def test_sanitize_secret_string_masks_sensitive_patterns() -> None:
    assert sanitize_secret_string("Bearer my_super_secret_token_12345") == "Bearer [REDACTED]"
    assert sanitize_secret_string("bearer my-token-abc") == "Bearer [REDACTED]"
    assert sanitize_secret_string("Basic dXNlcjpwYXNzd29yZA==") == "Basic [REDACTED]"
    assert sanitize_secret_string("Failed with sk-proj-1234567890abcdef") == "Failed with sk-[REDACTED]"
    assert (
        sanitize_secret_string("Connection failed: postgresql://admin:my_secret_pass@localhost:5432/db")
        == "Connection failed: postgresql://admin:***@localhost:5432/db"
    )
    assert sanitize_secret_string("") == ""
    assert sanitize_secret_string(None) == ""


def test_sanitize_uri_masks_passwords_and_query_tokens() -> None:
    # Database URIs
    db_uri = "postgresql://analytics_user:super_secret_password_123@db.internal:5432/warehouse"
    sanitized_db = sanitize_uri(db_uri)
    assert "super_secret_password_123" not in sanitized_db
    assert sanitized_db == "postgresql://analytics_user:***@db.internal:5432/warehouse"

    # API query parameters
    api_uri = "https://api.example.com/v1/feed?api_key=secret_key_abc123&region=West&token=tok_456"
    sanitized_api = sanitize_uri(api_uri)
    assert "secret_key_abc123" not in sanitized_api
    assert "tok_456" not in sanitized_api
    assert "api_key=[REDACTED]" in sanitized_api
    assert "token=[REDACTED]" in sanitized_api
    assert "region=West" in sanitized_api

    assert sanitize_uri("") == ""
    assert sanitize_uri(None) == ""


def test_settings_has_no_hardcoded_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Ensure no environment variables are set
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DATAFLOW_DATABASE_URL", raising=False)
    monkeypatch.delenv("INGESTION_API_KEY", raising=False)

    settings = Settings.from_environment(tmp_path)
    assert settings.llm_api_key is None
    assert settings.database_url is None
    assert settings.ingestion_api_key is None

    # Verify overrides via environment variables
    monkeypatch.setenv("LLM_API_KEY", "env_provided_key")
    monkeypatch.setenv("DATAFLOW_DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.setenv("INGESTION_API_KEY", "env_ingest_token")
    monkeypatch.setenv("DATAFLOW_CORS_ORIGINS", "http://localhost:3000,http://app.example.com")

    custom_settings = Settings.from_environment(tmp_path)
    assert custom_settings.llm_api_key == "env_provided_key"
    assert custom_settings.database_url == "postgresql://user:pass@host/db"
    assert custom_settings.ingestion_api_key == "env_ingest_token"
    assert custom_settings.cors_origins == ("http://localhost:3000", "http://app.example.com")


def test_api_ingestor_receipt_sanitizes_query_secrets(tmp_path: Path) -> None:
    lake = RawDataLake(tmp_path / "raw")
    ingestor = ApiIngestor(lake)

    mock_response = MagicMock()
    mock_response.headers.get_content_type.return_value = "application/json"
    mock_response.read.return_value = b'[{"id": 1, "value": "test"}]'
    mock_response.__enter__.return_value = mock_response

    sensitive_url = "https://api.vendor.com/v1/records?api_key=super_secret_credential&limit=50"

    with patch("dataflow_ai.ingestion.api_loader.urlopen", return_value=mock_response):
        receipt = ingestor.ingest(sensitive_url, "vendor_records", "test-run-id")

    assert "super_secret_credential" not in receipt.source_uri
    assert "api_key=[REDACTED]" in receipt.source_uri
    assert "limit=50" in receipt.source_uri


def test_llm_client_sanitizes_network_errors() -> None:
    client = LLMClient("https://api.openai.com/v1?token=sk-12345678", "gpt-4o", api_key="sk-secret-token")
    with patch("dataflow_ai.ai.llm_client.urlopen", side_effect=HTTPError("https://api.openai.com", 401, "Unauthorized", {}, None)):  # type: ignore
        with pytest.raises(LLMError) as exc_info:
            client.complete("system", "user")
        assert "sk-secret-token" not in str(exc_info.value)
        assert "sk-12345678" not in str(exc_info.value)


def test_assistant_warning_sanitizes_secrets(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    assistant = AnalyticsAssistant(settings)
    assistant.repository.initialize()

    # Force an LLM error that contains sensitive text
    with patch.object(assistant, "llm", MagicMock()):
        assistant.llm.complete.side_effect = LLMError("Failed connecting with Bearer secret_token_xyz")  # type: ignore
        response = assistant.ask("Unusual question that triggers LLM fallback")
        if response.warning:
            assert "secret_token_xyz" not in response.warning
            assert "[REDACTED]" in response.warning


def test_env_example_contains_no_real_secrets() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")
    assert "sk_live_" not in env_example
    assert "pk_live_" not in env_example
    assert "AKIA" not in env_example
    assert "SG." not in env_example or "placeholder" in env_example
    assert "AC_" not in env_example or "placeholder" in env_example


def test_gitignore_contains_critical_secret_patterns() -> None:
    gitignore_text = Path(".gitignore").read_text(encoding="utf-8")
    for pattern in [".env", ".env.*", "dbt/profiles.yml", "credentials.json", "token.json"]:
        assert pattern in gitignore_text
