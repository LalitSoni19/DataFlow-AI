"""Controlled multi-file upload that uses the same pipeline as scheduled runs."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root and src/ are in sys.path for Streamlit Cloud & local execution
_current = Path(__file__).resolve().parent
PROJECT_ROOT = _current.parent.parent
for _parent in [_current, *_current.parents]:
    _src = _parent / "src"
    if _src.is_dir() and (_src / "dataflow_ai").is_dir():
        PROJECT_ROOT = _parent
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

import uuid

import streamlit as st

from dataflow_ai.ingestion import ApiIngestor, RawDataLake
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.utils import sanitize_secret_string

try:
    from streamlit_app.components import get_repository, get_settings, page_heading
except ModuleNotFoundError:
    from components import get_repository, get_settings, page_heading  # type: ignore[no-redef]

page_heading("Import data", "Upload sales files or connect a JSON API to bring data into your workspace.")

from collections import Counter
import tempfile
from dataflow_ai.ingestion.flexible_sales import load_flexible_sales_files

settings = get_settings()
runner = PipelineRunner(settings)
file_tab, api_tab, history_tab = st.tabs(["Upload files", "Connect API", "Import history"])


def preview(paths):
    curated = {"customers.csv", "products.csv", "orders.csv", "order_items.csv", "website_events.json"}
    if curated <= {p.name for p in paths}:
        return {"Model": "Curated completed order lines", "Sources": sorted(p.name for p in paths)}
    imported = load_flexible_sales_files(paths)
    rows = imported.canonical_rows
    return {"Rows": len(rows), "Rows with revenue": sum(row["revenue"] is not None for row in rows),
            "Currencies": dict(Counter(row["currency"] or "Unknown" for row in rows)),
            "Mapping": imported.profile.semantic_mapping, "Warnings": imported.profile.warnings}


with file_tab:
    uploaded = st.file_uploader("Select sales exports", type=["csv", "tsv", "xlsx", "json", "jsonl"],
                                accept_multiple_files=True)
    if uploaded:
        signature = tuple((file.name, file.file_id) for file in uploaded)
        review = st.button("Preview import")
        previous = st.session_state.get("file_review", {})
        if previous.get("signature") == signature:
            st.json(previous["preview"])
            publish = st.button("Publish reviewed files", type="primary")
        else:
            publish = False
        if review or publish:
            try:
                with tempfile.TemporaryDirectory() as temp:
                    paths = []
                    for file in uploaded:
                        path = Path(temp) / Path(file.name).name
                        if path.exists() or file.size > 50 * 1024 * 1024:
                            raise ValueError("Use unique filenames with a maximum of 50 MB per file.")
                        path.write_bytes(file.getbuffer())
                        paths.append(path)
                    if review:
                        st.session_state.file_review = {"signature": signature, "preview": preview(paths)}
                        st.rerun()
                    result = runner.run_from_sources(Path(temp))
                    st.session_state.pop("file_review", None)
                    st.success(f"Published {result.accepted_rows:,} accepted rows.")
            except Exception as exc:
                st.error(sanitize_secret_string(str(exc)))

with api_tab:
    api_url = st.text_input("HTTPS JSON endpoint")
    api_token = st.text_input("Token for this endpoint (optional)", type="password")
    if st.button("Fetch and preview"):
        st.session_state.pop("api_review", None)
        try:
            if not api_url.startswith("https://"):
                raise ValueError("Use an HTTPS endpoint.")
            receipt = ApiIngestor(RawDataLake(settings.raw_dir)).ingest(
                api_url, "api_import", str(uuid.uuid4()), auth_token=api_token or None)
            path = Path(receipt.storage_path)
            st.session_state.api_review = (str(path), preview([path]), api_url)
        except Exception as exc:
            st.error(sanitize_secret_string(str(exc)))
    reviewed = st.session_state.get("api_review")
    if reviewed and reviewed[2] == api_url:
        st.json(reviewed[1])
        if st.button("Publish reviewed API snapshot", type="primary"):
            try:
                result = runner.run_flexible_sales_files([Path(reviewed[0])])
                st.session_state.pop("api_review", None)
                st.success(f"Published {result.accepted_rows:,} accepted rows.")
            except Exception as exc:
                st.error(sanitize_secret_string(str(exc)))

with history_tab:
    manifests = sorted(settings.processed_dir.glob("run-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if manifests:
        selected = st.selectbox("Completed import", [p.stem.removeprefix("run-") for p in manifests])
        if st.button("Restore this import as a new version"):
            try:
                result = runner.restore_run(selected)
                st.success(f"Restored as run {result.run_id}.")
            except Exception as exc:
                st.error(sanitize_secret_string(str(exc)))
    else:
        st.info("Published imports will appear here.")
