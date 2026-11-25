"""Tests for MotherDuck cloud warehouse configuration, token handling, and fallback safety."""

from __future__ import annotations

import os
from dataclasses import replace
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


def test_motherduck_streamlit_secrets_nested_connections(tmp_path: Path) -> None:
    real_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_nested_conn_token"
    mock_secrets = {
        "connections": {
            "motherduck": {
                "token": real_token,
                "database": "dataflow_ai",
            }
        }
    }
    with patch("streamlit.secrets", mock_secrets):
        settings = Settings.from_environment(tmp_path)
        assert settings.motherduck_token == real_token
        assert settings.database_url == "md:dataflow_ai"
        assert settings.is_cloud_database is True
        assert settings.effective_database_dsn == "md:dataflow_ai"


def test_motherduck_streamlit_secrets_nested_duckdb(tmp_path: Path) -> None:
    real_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_nested_duckdb_token"
    mock_secrets = {
        "connections": {
            "duckdb": {
                "token": real_token,
                "url": "md:custom_analytics_db",
            }
        }
    }
    with patch("streamlit.secrets", mock_secrets):
        settings = Settings.from_environment(tmp_path)
        assert settings.motherduck_token == real_token
        assert settings.database_url == "md:custom_analytics_db"
        assert settings.is_cloud_database is True
        assert settings.effective_database_dsn == "md:custom_analytics_db"


def test_motherduck_streamlit_secrets_toml_direct_file(tmp_path: Path) -> None:
    real_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_file_secrets_token"
    secrets_dir = tmp_path / ".streamlit"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    toml_content = f'MOTHERDUCK_TOKEN = "{real_token}"\nDATAFLOW_DATABASE_URL = "md:dataflow_ai"\n'
    (secrets_dir / "secrets.toml").write_text(toml_content, encoding="utf-8")

    with patch("streamlit.secrets", side_effect=Exception("Secrets not in memory")):
        settings = Settings.from_environment(tmp_path)
        assert settings.motherduck_token == real_token
        assert settings.database_url == "md:dataflow_ai"
        assert settings.is_cloud_database is True
        assert settings.effective_database_dsn == "md:dataflow_ai"


def test_streamlit_components_get_settings_preserves_cloud(tmp_path: Path) -> None:
    from streamlit_app.components import get_settings
    real_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_components_cloud_token"
    mock_secrets = {
        "MOTHERDUCK_TOKEN": real_token,
        "DATAFLOW_DATABASE_URL": "md:dataflow_ai",
    }
    with patch("streamlit.secrets", mock_secrets):
        with patch.dict(os.environ, {"DATAFLOW_PROJECT_ROOT": str(tmp_path)}):
            from streamlit_app.components import base_settings
            base_settings.clear()
            active_settings = get_settings()
            assert active_settings.is_cloud_database is True
            assert active_settings.database_url == "md:dataflow_ai"
            assert active_settings.motherduck_token == real_token
            assert active_settings.cloud_workspace
            assert active_settings.effective_database_dsn == f"md:{active_settings.cloud_workspace}?attach_mode=single"


def test_motherduck_attach_snippet_sanitization(tmp_path: Path) -> None:
    from streamlit_app.components import _display_database_name
    real_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy_attach_token"
    mock_secrets = {
        "MOTHERDUCK_TOKEN": real_token,
        "DATAFLOW_DATABASE_URL": "ATTACH 'md:_share/dataflow_ai/45031232-cf98-4c26-862a-5d100286fc1z';",
    }
    with patch("streamlit.secrets", mock_secrets):
        settings = Settings.from_environment(tmp_path)
        assert settings.database_url == "md:_share/dataflow_ai/45031232-cf98-4c26-862a-5d100286fc1z"
        assert settings.is_cloud_database is True
        assert _display_database_name(settings) == "dataflow_ai (shared)"
        with patch("duckdb.connect") as connect:
            with pytest.raises(ValueError, match="points to a MotherDuck share"):
                WarehouseRepository(settings).initialize()
            connect.assert_not_called()


def test_anonymous_sessions_isolate_import_history_and_dashboard(tmp_path, monkeypatch):
    import duckdb
    from streamlit.testing.v1 import AppTest
    from streamlit_app import components

    base = replace(Settings.from_environment(tmp_path), database_url="md:base",
                   motherduck_token="dummy-test-credential")
    monkeypatch.setattr(components, "base_settings", lambda: base)
    # Exercise the cloud routing and SQL locally, with a DuckDB file per cloud database.
    local_connect = duckdb.connect
    admin = MagicMock()

    def connect(dsn, *, config):
        if dsn == base.database_url:
            return admin
        assert dsn.startswith("md:df_") and dsn.endswith("?attach_mode=single")
        database = dsn.removeprefix("md:").partition("?")[0]
        return local_connect(str(tmp_path / f"{database}.duckdb"))

    monkeypatch.setattr(duckdb, "connect", connect)
    app = Path(components.__file__).with_name("app.py")
    first = AppTest.from_file(app, default_timeout=30).run()
    first.switch_page("pages/ingestion.py").run()
    first.file_uploader[0].set_value(("sales.csv", b"product,amount,currency\nDesk,123,USD\n", "text/csv")).run()
    next(b for b in first.button if b.label == "Preview import").click().run()
    next(b for b in first.button if b.label == "Publish reviewed files").click().run()
    assert any(s.label == "Completed import" for s in first.selectbox)

    second = AppTest.from_file(app, default_timeout=30).run()
    assert not second.exception and not second.metric
    assert any(b.label == "Build Cloud Warehouse (MotherDuck)" for b in second.button)
    second.switch_page("pages/ingestion.py").run()
    assert any(i.value == "Published imports will appear here." for i in second.info)
    assert not any(s.label == "Completed import" for s in second.selectbox)

    first.switch_page("pages/home.py").run()
    assert not first.exception
    assert next(m.value for m in first.metric if m.label == "Net revenue") == "$123"
    provisioned = admin.__enter__.return_value.execute.call_args_list
    assert len({call.args[0] for call in provisioned}) == 2
    assert all(call.args[0].startswith('CREATE DATABASE IF NOT EXISTS "df_') for call in provisioned)


def test_identity_scopes_keep_cloud_backend(tmp_path):
    base = replace(Settings.from_environment(tmp_path), database_url="md:warehouse",
                   motherduck_token="dummy-test-credential")
    alice = base.workspace("issuer", "alice")
    bob = base.workspace("issuer", "bob")
    assert alice.is_cloud_database and alice.effective_database_dsn.startswith("md:df_")
    assert alice.effective_database_dsn != bob.effective_database_dsn
    assert alice.effective_database_dsn == base.workspace("issuer", "alice").effective_database_dsn
    assert alice.motherduck_token == base.motherduck_token
