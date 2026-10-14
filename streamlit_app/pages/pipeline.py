"""Operational view of the most recent run and its data-quality results."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root and src/ are in sys.path for Streamlit Cloud & local execution
_current = Path(__file__).resolve().parent
for _parent in [_current, *_current.parents]:
    _src = _parent / "src"
    if _src.is_dir() and (_src / "dataflow_ai").is_dir():
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

import streamlit as st

try:
    from streamlit_app.components import ensure_demo_data, get_repository, page_heading
except ModuleNotFoundError:
    from components import ensure_demo_data, get_repository, page_heading  # type: ignore[no-redef]

page_heading("Pipeline and data quality", "Observable run status, accepted/rejected rows, and rule failures")
if ensure_demo_data():
    repository = get_repository()
    status = repository.pipeline_status()
    quality = repository.data_quality()
    if status:
        columns = st.columns(4)
        columns[0].metric("Run status", status["status"].title())
        columns[1].metric("Rows ingested", f"{status['ingested_rows']:,}")
        columns[2].metric("Rows rejected", f"{status['rejected_rows']:,}")
        columns[3].metric("Quality score", f"{status['quality_score']:.2f}%")
        st.caption(f"Run ID: {status['run_id']} · duration: {status['duration_seconds']} seconds")
    st.subheader("Dataset quality results")
    st.dataframe(quality, use_container_width=True, hide_index=True)
    st.caption("Rejected records are saved as JSON Lines under data/quarantine/<run_id>/ for inspection and remediation.")

