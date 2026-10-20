"""Transparent natural-language analytics UI with retrieved-document and SQL traces."""

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

import streamlit as st

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

page_heading("Ask sales", "Ask data-grounded questions about imported sales files or general sales-strategy questions")

if ensure_demo_data():
    # Show AI provider status
    settings = get_settings()
    if settings.llm_base_url and settings.llm_api_key:
        provider_name = settings.llm_model or "LLM"
        st.success(f"🤖 AI Engine: **{provider_name}** — data questions and sales guidance are available", icon="✅")
    elif settings.ollama_base_url and settings.ollama_model:
        st.info(f"🤖 AI Engine: Ollama ({settings.ollama_model}) — data questions and sales guidance are available", icon="🔗")
    else:
        st.warning(
            "⚠️ No LLM configured. Common data questions are available offline; configure a provider to unlock open-ended sales questions and dynamic queries.",
            icon="⚠️",
        )

    # Example question suggestions
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
        help="Data questions return an approved SQL trace. General sales guidance is clearly marked as non-data-grounded.",
    )

    if st.button("Ask the warehouse", type="primary"):
        if not question.strip():
            st.warning("Please enter a question.")
        else:
            with st.spinner("Analyzing your question..."):
                response = get_assistant().ask(question)

            st.subheader("Answer")
            st.write(response.answer)

            if response.data_grounded:
                st.caption("✓ Data-grounded answer from the active warehouse.")
            elif response.mode == "sales_guidance":
                st.info("General sales guidance — this answer is not a claim about your uploaded data.")
            elif response.mode == "clarify":
                st.info("A focused clarification will help produce a data-grounded result.")

            if response.warning:
                st.warning(response.warning)

            if response.sql:
                st.subheader("Approved SQL")
                st.code(response.sql, language="sql")
                if response.parameters:
                    st.caption(f"Parameters: {response.parameters}")

            if response.rows:
                st.subheader("Warehouse results")
                st.dataframe(response.rows, use_container_width=True, hide_index=True)

            if response.source_documents:
                st.caption("Retrieved metadata: " + ", ".join(response.source_documents))

            st.caption(f"Provider: `{response.provider}` | Request ID: `{response.request_id}`")
