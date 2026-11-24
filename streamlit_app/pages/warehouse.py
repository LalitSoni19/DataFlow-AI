"""A constrained explorer for the curated analytics schema."""

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

import csv
import io

import streamlit as st

from dataflow_ai.utils import mask_record_pii

try:
    from streamlit_app.components import ensure_demo_data, get_repository, page_heading
except ModuleNotFoundError:
    from components import ensure_demo_data, get_repository, page_heading  # type: ignore[no-redef]

page_heading("Browse tables", "Preview warehouse records and export the rows you need.")
if ensure_demo_data():
    repository = get_repository()
    metadata = repository.table_metadata()
    allowed_tables = {item["table"] for item in metadata}
    selected = st.selectbox("Table", [item["table"] for item in metadata])
    if selected not in allowed_tables:
        st.error("Access denied: Invalid or untrusted table selected.")
        st.stop()
    description = next(item["description"] for item in metadata if item["table"] == selected)
    st.caption(description)
    # SAFETY: 'selected' is constrained to the hardcoded allowed_tables set above (line 14-16).
    # DuckDB does not support parameterized table names, so f-string is the accepted pattern here.
    raw_rows = repository.fetch_all(f"SELECT * FROM {selected} LIMIT 100")
    # Apply field-level PII masking for any table containing customer/sales personal data
    has_customer_fields = any(term in selected.lower() for term in ("customer", "upload", "flex"))
    rows = [mask_record_pii(r) for r in raw_rows] if has_customer_fields else raw_rows
    st.dataframe(rows, use_container_width=True, hide_index=True)
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    csv_text = buffer.getvalue()
    st.download_button("Download displayed rows as CSV", csv_text, file_name=f"{selected.replace('.', '_')}.csv", mime="text/csv")

    st.divider()

    # Customer privacy and data erasure controls
    from streamlit_app.components import base_settings
    subject, role = "demo", "admin"
    if base_settings().auth_required or base_settings().is_production:
        subject, role = st.user["sub"], st.user.get("dataflow_role", "analyst")
        if st.user.get("dataflow_role") not in ("admin", "dpo"):
            st.stop()
    with st.expander("🛡️ Customer Privacy & Right to Erasure (GDPR)", expanded=False):
        st.markdown(
            "Execute atomic customer personal data anonymization in accordance with GDPR Article 17. "
            "Personal names, emails, and addresses are replaced with anonymized placeholders across staging and dimensional marts, "
            "while financial reporting metrics are preserved."
        )
        col1, col2 = st.columns([3, 2])
        with col1:
            target_customer_id = st.text_input("Customer ID", placeholder="e.g. C-0001", help="Enter the ID of the customer requesting data erasure.")
            confirm_erasure = st.checkbox("I confirm authorization to permanently erase this customer's data")
        with col2:
            st.write("")
            st.write("")
            action_col1, action_col2 = st.columns(2)
            check_clicked = action_col1.button("Check Status")
            anonymize_clicked = action_col2.button("Anonymize Data", type="primary")

        if check_clicked and target_customer_id.strip():
            status = repository.authorized_customer_action(target_customer_id.strip(), subject, role)
            if status:
                st.json(status)
                if status["is_anonymized"]:
                    st.info(f"Customer {target_customer_id} has already been anonymized.")
                else:
                    st.warning(f"Customer {target_customer_id} contains active personal data.")
            else:
                st.error(f"Customer {target_customer_id} not found in warehouse.")

        if anonymize_clicked and target_customer_id.strip():
            if not confirm_erasure:
                st.warning("⚠️ Please check the authorization confirmation box before executing permanent anonymization.")
            else:
                try:
                    receipt = repository.authorized_customer_action(target_customer_id.strip(), subject, role, erase=True)
                    st.success(receipt["message"])
                    st.json(receipt)
                    st.cache_data.clear()
                except KeyError as error:
                    st.error(str(error))
                except Exception as error:
                    st.error(f"Failed to anonymize: {error}")


