"""Shared helpers and UI components for the Streamlit dashboard."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root and src/ are in sys.path for Streamlit Cloud & local execution
PROJECT_ROOT = Path(__file__).resolve().parents[1]
src_dir = PROJECT_ROOT / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.settings import Settings
from dataflow_ai.warehouse.repository import WarehouseRepository

LOGGER = logging.getLogger(__name__)


@st.cache_resource
def get_settings() -> Settings:
    return Settings.from_environment(PROJECT_ROOT)


@st.cache_resource
def get_repository() -> WarehouseRepository:
    return WarehouseRepository(get_settings())


def _warehouse_ready() -> bool:
    """Check whether the warehouse has been populated.

    For MotherDuck (cloud), a probe query confirms the analytics tables exist.
    For local DuckDB, a fast file-existence check is sufficient.
    """
    settings = get_settings()
    if settings.is_cloud_database:
        try:
            repo = get_repository()
            with repo.connection(read_only=True) as conn:
                result = conn.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'analytics' AND table_name = 'fact_sales'"
                ).fetchone()
                return bool(result and result[0] > 0)
        except Exception:
            return False
    return settings.database_path.exists()


def ensure_demo_data() -> bool:
    """Build the sample warehouse only after an explicit user click."""
    _show_connection_badge()

    if _warehouse_ready():
        return True

    settings = get_settings()
    if settings.is_cloud_database:
        db_name = (settings.database_url or "md:dataflow_ai").removeprefix("md:")
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
        db_name = (settings.database_url or "md:dataflow_ai").removeprefix("md:")
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
            symbol = "₹"
    val = float(value or 0)
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
    st.title(title)
    st.caption(subtitle)


