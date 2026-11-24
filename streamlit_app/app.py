"""DataFlow AI multipage dashboard entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root and src/ are in sys.path
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

st.set_page_config(page_title="DataFlow AI", page_icon="📊", layout="wide")

pages = {
    "Workspace": [
        st.Page("pages/home.py", title="Sales overview", icon=":material/space_dashboard:", default=True),
        st.Page("pages/ingestion.py", title="Import data", icon=":material/upload_file:"),
        st.Page("pages/pipeline.py", title="Pipeline quality", icon=":material/monitor_heart:"),
        st.Page("pages/warehouse.py", title="Browse tables", icon=":material/table_view:"),
        st.Page("pages/explorer.py", title="SQL explorer", icon=":material/code:"),
    ],
    "Insights": [
        st.Page("pages/assistant.py", title="Ask your data", icon=":material/forum:"),
        st.Page("pages/reports.py", title="Monthly reports", icon=":material/summarize:"),
    ],
}
selected_page = st.navigation(pages)

from streamlit_app.components import base_settings, get_settings

get_settings()
if base_settings().auth_required or base_settings().is_production:
    if st.sidebar.button("Sign out"):
        st.logout()
else:
    st.sidebar.caption("Private demo session · temporary storage")
selected_page.run()
