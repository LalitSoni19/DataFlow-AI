"""Shared helpers and UI components for the Streamlit dashboard."""

from __future__ import annotations

import os
from dataclasses import replace
from html import escape
from tempfile import TemporaryDirectory
import logging
import sys
from pathlib import Path
from typing import Any

_current = Path(__file__).resolve().parent
PROJECT_ROOT = _current.parent
for _parent in [_current, *_current.parents]:
    _src = _parent / "src"
    if _src.is_dir() and (_src / "dataflow_ai").is_dir():
        PROJECT_ROOT = _parent
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

import streamlit as st

from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.settings import Settings
from dataflow_ai.warehouse.repository import WarehouseRepository

LOGGER = logging.getLogger(__name__)

_QUERY_CACHE_TTL: int = 120


@st.cache_resource
def base_settings() -> Settings:
    return Settings.from_environment(Path(os.environ.get("DATAFLOW_PROJECT_ROOT", PROJECT_ROOT)))


def get_settings() -> Settings:
    base = base_settings()
    if base.is_production or base.auth_required:
        if not base.oidc_issuer:
            st.error("Configure the workspace identity provider before signing in.")
            st.stop()
        if not st.user.is_logged_in:
            if st.button("Sign in to your workspace", type="primary"):
                st.login()
            st.stop()
        if st.user.get("iss") != base.oidc_issuer or not st.user.get("sub"):
            st.error("This identity provider is not configured for this workspace.")
            st.stop()
        return base.workspace(base.oidc_issuer, st.user["sub"])
    if "demo_directory" not in st.session_state:
        st.session_state.demo_directory = TemporaryDirectory(prefix="dataflow-demo-")
    scratch = Path(st.session_state.demo_directory.name)
    if base.is_cloud_database:
        scoped = base.with_data_root(scratch)
        return replace(
            scoped,
            database_url=base.database_url,
            motherduck_token=base.motherduck_token,
            cloud_workspace=base.cloud_workspace,
        )
    return base.with_data_root(scratch)


def get_repository() -> WarehouseRepository:
    settings = get_settings()
    code = st.session_state.get("report_currency")
    key = (settings.effective_database_dsn, code)
    if st.session_state.get("_repo_key") != key:
        st.session_state["_repository"] = WarehouseRepository(settings, code)
        st.session_state["_repo_key"] = key
    return st.session_state["_repository"]


def get_assistant():
    from dataflow_ai.ai.assistant import AnalyticsAssistant
    return AnalyticsAssistant(get_settings(), get_repository())


def cached_kpi_overview() -> dict[str, Any]:
    return get_repository().kpi_overview()


def cached_sales(start: str | None = None, end: str | None = None, region: str | None = None) -> list[dict[str, Any]]:
    return get_repository().sales(start, end, region)


def cached_top_products(limit: int = 10) -> list[dict[str, Any]]:
    return get_repository().top_products(limit)


def cached_monthly_trend(months: int = 6) -> list[dict[str, Any]]:
    return get_repository().monthly_trend(months)


def cached_sales_by_category() -> list[dict[str, Any]]:
    return get_repository().sales_by_category()


def _warehouse_ready() -> bool:
    """Check whether the warehouse has been populated.

    For MotherDuck (cloud), a probe query confirms the analytics tables exist.
    For local DuckDB, a fast file-existence check is sufficient.
    """
    settings = get_settings()
    if not settings.is_cloud_database and not settings.database_path.exists():
        return False
    repo = get_repository()
    repo.initialize()
    return bool(repo.fetch_all("SELECT mode FROM governance.active_sales_dataset LIMIT 1"))


def _display_database_name(settings: Settings) -> str:
    raw = settings.cloud_workspace or (settings.database_url or "md:dataflow_ai").removeprefix("md:")
    if raw.startswith("_share/"):
        parts = raw.split("/")
        if len(parts) >= 2:
            return f"{parts[1]} (shared)"
    return raw


def ensure_demo_data() -> bool:
    """Build the sample warehouse only after an explicit user click."""
    _show_connection_badge()

    if _warehouse_ready():
        repo = get_repository()
        if repo.active_sales_mode() == "flexible":
            codes = [row["currency_code"] for row in repo.currency_coverage()]
            if codes:
                st.sidebar.selectbox("Reporting currency", codes, key="report_currency")
        active = repo.fetch_all("SELECT mode, activated_at FROM governance.active_sales_dataset LIMIT 1")
        if active:
            st.sidebar.caption(f"{active[0]['mode'].title()} dataset · Published {active[0]['activated_at']}")
        return True

    settings = get_settings()
    if settings.is_cloud_database:
        db_name = _display_database_name(settings)
        st.info(f"☁️ Connected to MotherDuck cloud database **{db_name}**, but analytics tables are not built yet.")
        button_label = "Build Cloud Warehouse (MotherDuck)"
    elif settings.has_motherduck_placeholder:
        st.info("No warehouse built yet. Running with local storage until your real MotherDuck token is configured.")
        button_label = "Build Sample Warehouse"
    else:
        st.info("No warehouse has been built yet. Run the sample pipeline to populate the dashboard.")
        button_label = "Build Sample Warehouse"

    if st.button(button_label, type="primary"):
        with st.spinner("Ingesting, validating, transforming, and loading sample data..."):
            summary = PipelineRunner(settings).run_sample_pipeline(regenerate_sample=True)
        st.success(f"Pipeline completed: {summary.accepted_rows:,} accepted rows; quality {summary.quality_score:.2f}%.")
        st.cache_data.clear()
        st.rerun()
    return False


def _show_connection_badge() -> None:
    """Display a sidebar badge indicating the active database backend."""
    settings = get_settings()
    st.sidebar.divider()
    if settings.is_cloud_database:
        db_name = _display_database_name(settings)
        st.sidebar.success(f"☁️ MotherDuck: **{db_name}**", icon="☁️")
        st.sidebar.caption("⚡ Cloud RAM & Cloud Storage Active")
    elif settings.has_motherduck_placeholder:
        st.sidebar.warning("☁️ MotherDuck: Token Pending (`abcdef`)", icon="🔑")
        st.sidebar.caption("👉 Replace `abcdef` in `.env` (line 25) with your real MotherDuck token to activate cloud storage & compute.")
        st.sidebar.info(f"💾 Local Storage: `{settings.database_path.name}`")
    else:
        st.sidebar.info(f"💾 Local Storage: `{settings.database_path.name}`", icon="💾")


def currency(value: float | None, symbol: str | None = None) -> str:
    """Format an amount using the active dataset's currency symbol without conversion."""
    if symbol is None:
        try:
            repo = get_repository()
            symbol = repo.active_currency_symbol()
        except Exception:
            symbol = ""
    if value is None:
        return "Unavailable"
    val = float(value)
    if symbol == "$":
        if abs(val - round(val)) > 0.005:
            return f"${val:,.2f}"
        return f"${val:,.0f}"
    if symbol == "€":
        if abs(val - round(val)) > 0.005:
            return f"€{val:,.2f}"
        return f"€{val:,.0f}"
    if symbol == "£":
        if abs(val - round(val)) > 0.005:
            return f"£{val:,.2f}"
        return f"£{val:,.0f}"
    if abs(val - round(val)) > 0.005:
        return f"{symbol}{val:,.2f}"
    return f"{symbol}{val:,.0f}"


def page_heading(title: str, subtitle: str) -> None:
    styles = Path(__file__).with_name("theme.css").read_text(encoding="utf-8")
    st.html(f"<style>{styles}</style>")
    st.html(
        '<header class="df-heading"><span class="df-brand">DataFlow AI</span>'
        f"<h1>{escape(title)}</h1><p>{escape(subtitle)}</p></header>"
    )

