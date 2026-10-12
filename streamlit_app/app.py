"""DataFlow AI multipage dashboard entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root and src/ are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
src_dir = PROJECT_ROOT / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

st.set_page_config(page_title="DataFlow AI", page_icon="📊", layout="wide")

pages = {
    "Platform": [
        st.Page("pages/home.py", title="Dashboard", icon="📊", default=True),
        st.Page("pages/ingestion.py", title="Data ingestion", icon="📥"),
        st.Page("pages/pipeline.py", title="Pipeline and quality", icon="✅"),
        st.Page("pages/warehouse.py", title="Warehouse explorer", icon="🗄️"),
        st.Page("pages/explorer.py", title="Data explorer", icon="🔍"),
    ],
    "AI analytics": [
        st.Page("pages/assistant.py", title="Ask your data", icon="✨"),
        st.Page("pages/reports.py", title="Reports & insights", icon="📈"),
    ],
}
selected_page = st.navigation(pages)
st.sidebar.caption("Local-first, trusted analytics")
selected_page.run()
