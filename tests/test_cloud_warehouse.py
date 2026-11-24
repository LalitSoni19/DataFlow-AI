"""Tests for MotherDuck cloud warehouse configuration, token handling, and fallback safety."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dataflow_ai.settings import Settings
from dataflow_ai.warehouse.repository import WarehouseRepository


@pytest.fixture(autouse=True)
def isolate_motherduck_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    monkeypatch.delenv("motherduck_token", raising=False)
    monkeypatch.delenv("DATAFLOW_DATABASE_URL", raising=False)
    yield
    if "MOTHERDUCK_TOKEN" in os.environ:
        del os.environ["MOTHERDUCK_TOKEN"]
    if "motherduck_token" in os.environ:
        del os.environ["motherduck_token"]
    if "DATAFLOW_DATABASE_URL" in os.environ:
        del os.environ["DATAFLOW_DATABASE_URL"]


def test_motherduck_placeholder_token_detection(tmp_path: Path) -> None:
    env_content = "MOTHERDUCK_TOKEN=abcdef\nDATAFLOW_DATABASE_URL=md:dataflow_ai\n"
    (tmp_path / ".env").write_text(env_content, encoding="utf-8")
    settings = Settings.from_environment(tmp_path)

    assert settings.motherduck_token == "abcdef"
    assert settings.has_motherduck_placeholder is True
    assert settings.is_cloud_database is False
    assert settings.effective_database_dsn == str(settings.database_path)


def test_motherduck_short_token_detection(tmp_path: Path) -> None:
    env_content = "MOTHERDUCK_TOKEN=your-token-here\nDATAFLOW_DATABASE_URL=md:dataflow_ai\n"
    (tmp_path / ".env").write_text(env_content, encoding="utf-8")
    settings = Settings.from_environment(tmp_path)

    assert settings.has_motherduck_placeholder is True
    assert settings.is_cloud_database is False


def test_motherduck_valid_token_activates_cloud(tmp_path: Path) -> None:
    real_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_payload_for_testing"
    env_content = f"MOTHERDUCK_TOKEN={real_token}\nDATAFLOW_DATABASE_URL=md:dataflow_ai\n"
    (tmp_path / ".env").write_text(env_content, encoding="utf-8")
    settings = Settings.from_environment(tmp_path)

    assert settings.has_motherduck_placeholder is False
    assert settings.is_cloud_database is True
    assert settings.effective_database_dsn == "md:dataflow_ai"


def test_motherduck_env_injection(tmp_path: Path) -> None:
    real_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_payload_for_testing"
    env_content = f"MOTHERDUCK_TOKEN={real_token}\nDATAFLOW_DATABASE_URL=md:dataflow_ai\n"
    (tmp_path / ".env").write_text(env_content, encoding="utf-8")
    settings = Settings.from_environment(tmp_path)

    repo = WarehouseRepository(settings)
    repo._ensure_motherduck_env()

    assert os.environ.get("motherduck_token") == real_token
    assert os.environ.get("MOTHERDUCK_TOKEN") == real_token


def test_motherduck_connection_options_passed(tmp_path: Path) -> None:
    real_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_payload_for_testing"
    env_content = f"MOTHERDUCK_TOKEN={real_token}\nDATAFLOW_DATABASE_URL=md:dataflow_ai\n"
    (tmp_path / ".env").write_text(env_content, encoding="utf-8")
    settings = Settings.from_environment(tmp_path)

    repo = WarehouseRepository(settings)
    mock_conn = MagicMock()

    with patch("duckdb.connect", return_value=mock_conn) as mock_connect:
        with repo.connection(read_only=True) as conn:
            assert conn == mock_conn

        mock_connect.assert_called_once()
        call_args = mock_connect.call_args
        assert call_args[0][0] == "md:dataflow_ai"
        assert "config" in call_args[1]
        assert call_args[1]["config"].get("motherduck_token") == real_token
        assert "read_only" not in call_args[1]


def test_motherduck_streamlit_secrets_activates_cloud(tmp_path: Path) -> None:
    real_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_stream_secrets_token"
    mock_secrets = {
        "MOTHERDUCK_TOKEN": real_token,
        "DATAFLOW_DATABASE_URL": "md:dataflow_ai",
    }
    with patch("streamlit.secrets", mock_secrets):
        settings = Settings.from_environment(tmp_path)
        assert settings.motherduck_token == real_token
        assert settings.database_url == "md:dataflow_ai"
        assert settings.is_cloud_database is True
        assert settings.effective_database_dsn == "md:dataflow_ai"



def test_identity_scopes_keep_cloud_backend(tmp_path):
    from dataclasses import replace
    base = replace(Settings.from_environment(tmp_path), database_url="md:warehouse",
                   motherduck_token="dummy-test-credential")
    alice = base.workspace("issuer", "alice")
    bob = base.workspace("issuer", "bob")
    assert alice.is_cloud_database and alice.effective_database_dsn.startswith("md:df_")
    assert alice.effective_database_dsn != bob.effective_database_dsn
    assert alice.effective_database_dsn == base.workspace("issuer", "alice").effective_database_dsn
    assert alice.motherduck_token == base.motherduck_token
