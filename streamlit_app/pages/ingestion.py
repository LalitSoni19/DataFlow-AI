"""Controlled multi-file upload that uses the same pipeline as scheduled runs."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import shutil
import uuid

import streamlit as st

from dataflow_ai.ingestion import ApiIngestor, RawDataLake
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.utils import sanitize_secret_string

try:
    from streamlit_app.components import get_repository, get_settings, page_heading
except ModuleNotFoundError:
    from components import get_repository, get_settings, page_heading  # type: ignore[no-redef]

page_heading("Data ingestion", "Upload any sales export or a curated source batch; every received file is preserved in the raw lake")

# File upload vs API load tabs
file_tab, api_tab = st.tabs(["📁 Upload Files", "🌐 Load from API"])

# File upload tab
with file_tab:
    st.info(
        "Upload one or more sales files in CSV, TSV, XLSX, JSON, or JSON Lines format. "
        "Names and columns can differ: the importer profiles sales/date/revenue/product/customer fields and publishes its mapping for review. "
        "A complete legacy batch (customers.csv, products.csv, orders.csv, order_items.csv, website_events.json) still uses the stricter curated contract."
    )
    uploaded = st.file_uploader(
        "Upload sales file(s)",
        type=["csv", "tsv", "xlsx", "json", "jsonl"],
        accept_multiple_files=True,
        help="The importer detects sales semantics from columns, retains the raw originals, and never executes uploaded data as code or SQL.",
    )
    if uploaded and st.button("Validate and import sales data", type="primary", key="file_upload_btn"):
        by_name = {Path(file.name).name: file for file in uploaded}
        if len(by_name) != len(uploaded):
            st.error("Each uploaded file needs a unique filename.")
        else:
            MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB per file limit
            validation_error = None

            # 1. Pre-validation: check sizes before reading buffers
            for filename, file in by_name.items():
                if file.size > MAX_FILE_SIZE:
                    validation_error = f"File {filename} ({file.size / (1024*1024):.1f} MB) exceeds the 50 MB size limit."
                    break

            if validation_error:
                st.error(validation_error)
            else:
                settings = get_settings()
                batch_dir = settings.data_dir / "uploads" / str(uuid.uuid4())
                batch_dir.mkdir(parents=True, exist_ok=False)
                try:
                    for filename, file in by_name.items():
                        content = file.getvalue()
                        suffix = Path(filename).suffix.lower()
                        # XLSX is a zip-based binary container; all text types
                        # must be valid UTF-8 before the pipeline sees them.
                        if suffix != ".xlsx":
                            try:
                                sample = content[:4096].decode("utf-8-sig")
                            except UnicodeDecodeError as err:
                                raise ValueError(f"File {filename} must be UTF-8 encoded.") from err
                            if suffix in {".json", ".jsonl"} and not sample.lstrip().startswith(("{", "[")):
                                raise ValueError(f"File {filename} is not valid JSON or JSON Lines.")

                        target_file = batch_dir / filename
                        target_file.write_bytes(content)
                        try:
                            target_file.chmod(0o600)  # Restrict permissions (non-executable)
                        except OSError:
                            pass

                    with st.spinner("Running ingestion, quality checks, transformation, and warehouse load..."):
                        summary = PipelineRunner(settings).run_from_sources(batch_dir)
                    st.success(f"Completed run {summary.run_id} with quality score {summary.quality_score:.2f}%.")
                    st.json(summary.to_dict())
                    profile = get_repository().flexible_sales_profile()
                    if profile:
                        st.subheader("Detected sales mapping")
                        st.json({
                            "semantic_mapping": profile["semantic_mapping"],
                            "column_types": profile["column_types"],
                            "source_files": profile["source_files"],
                        })
                        for import_warning in profile["warnings"]:
                            st.warning(import_warning)
                    st.cache_data.clear()
                except Exception as error:  # noqa: BLE001
                    st.error(f"Pipeline failed: {sanitize_secret_string(str(error))}")
                finally:
                    shutil.rmtree(batch_dir, ignore_errors=True)

# API load tab
with api_tab:
    st.info("Enter an API URL that returns JSON data. The response will be ingested into the raw data lake.")
    api_url = st.text_input(
        "API URL",
        placeholder="https://api.example.com/data",
        help="The endpoint must return valid JSON (application/json). Avoid passing secret tokens in URL query strings.",
    )
    dataset_name = st.text_input(
        "Dataset name",
        value="api_import",
        help="A descriptive name for this dataset (e.g., 'weather_data', 'market_prices').",
    )
    api_token = st.text_input(
        "API Key / Bearer Token (Optional)",
        type="password",
        help="Optional credentials. Transmitted securely in HTTP Authorization headers, never logged or stored in manifests.",
    )

    if st.button("🔗 Load Data", type="primary", key="api_load_btn"):
        if not api_url or not api_url.startswith(("http://", "https://")):
            st.error("Please enter a valid HTTP or HTTPS URL.")
        elif not dataset_name.strip():
            st.error("Please enter a dataset name.")
        else:
            settings = get_settings()
            settings.ensure_directories()
            lake = RawDataLake(settings.raw_dir)
            ingestor = ApiIngestor(lake)
            run_id = str(uuid.uuid4())
            effective_token = api_token.strip() or settings.ingestion_api_key
            try:
                with st.spinner(f"Fetching data from {api_url}..."):
                    receipt = ingestor.ingest(
                        api_url, dataset_name.strip(), run_id, auth_token=effective_token
                    )
                st.success(f"Successfully ingested {receipt.row_count} record(s) from API.")
                st.json(receipt.to_dict())
                st.caption(
                    f"Raw data stored at: `{receipt.storage_path}`\n\n"
                    f"SHA-256: `{receipt.sha256}`"
                )
            except ConnectionError as error:
                st.error(f"Failed to connect: {sanitize_secret_string(str(error))}")
            except ValueError as error:
                st.error(f"Invalid response: {sanitize_secret_string(str(error))}")
            except Exception as error:  # noqa: BLE001
                st.error(f"Ingestion failed: {sanitize_secret_string(str(error))}")

