"""Transparent natural-language analytics UI with retrieved-document and SQL traces."""

from __future__ import annotations

import sys
from pathlib import Path

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

import streamlit as st
from streamlit_app.visibility import public_text

try:
    from streamlit_app.components import (
        ensure_demo_data,
        get_assistant,
        get_settings,
        page_heading,
    )
except ModuleNotFoundError:
    from components import (  # type: ignore[no-redef]
        ensure_demo_data,
        get_assistant,
        get_settings,
        page_heading,
    )

page_heading("Ask your data", "Ask a question about your sales or get guidance on sales strategy.")

if ensure_demo_data():
    settings = get_settings()
    if settings.llm_base_url and settings.llm_api_key:
        st.success("Data questions and sales guidance are available.", icon="✅")
    elif settings.ollama_base_url and settings.ollama_model:
        st.info("Data questions and sales guidance are available.", icon="🔗")
    else:
        st.warning(
            "⚠️ No LLM configured. Common data questions are available offline; configure a provider to unlock open-ended sales questions and dynamic queries.",
            icon="⚠️",
        )

    st.markdown("**💡 Try asking:**")
    example_cols = st.columns(3)
    examples = [
        "Who are the top 5 customers by total purchasing?",
        "Revenue breakdown by product category",
        "Which region has the highest profit margin?",
    ]
    selected_example = None
    for col, example in zip(example_cols, examples):
        if col.button(example, use_container_width=True):
            selected_example = example

    question = st.text_area(
        "Question",
        value=selected_example or "",
        max_chars=1000,
        placeholder="e.g. 'Show monthly revenue by region' or 'How should I qualify enterprise sales leads?'",
        help="Ask for totals, trends, or a breakdown of your sales.",
    )

    if st.button("Ask the warehouse", type="primary"):
        if not question.strip():
            st.warning("Please enter a question.")
        else:
            with st.spinner("Analyzing your question..."):
                response = get_assistant().ask(question)

            st.subheader("Answer")
            st.write(public_text(response.answer))

            if response.data_grounded:
                st.caption("✓ Data-grounded answer from the active warehouse.")
            elif response.mode == "sales_guidance":
                st.info("General sales guidance — this answer is not a claim about your uploaded data.")
            elif response.mode == "clarify":
                st.info("A focused clarification will help produce a data-grounded result.")

            if response.warning:
                st.info("Some query details are unavailable.")

            if response.rows and any(response.rows):
                st.subheader("Warehouse results")
                st.dataframe(response.rows, use_container_width=True, hide_index=True)
