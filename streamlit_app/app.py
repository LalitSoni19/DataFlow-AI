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

from dataflow_ai.settings import Settings

def _inject_google_site_verification() -> None:
    """Inject Google Search Console verification meta tag and verification file into Streamlit."""
    tokens = ["a02cba74d850951b", "dWYj8KkmQ7p1HlC9H7LkaGTdsdBXBPm9-pNd5UZKaJ8"]

    try:
        static_dir = Path(st.__file__).parent / "static"
        index_file = static_dir / "index.html"
        if index_file.exists():
            content = index_file.read_text(encoding="utf-8")
            for tok in tokens:
                v_tag = f'<meta name="google-site-verification" content="{tok}" />'
                if v_tag not in content and "<head>" in content:
                    content = content.replace("<head>", f"<head>\n    {v_tag}")
            index_file.write_text(content, encoding="utf-8")

        for tok in tokens:
            v_file = static_dir / f"google{tok}.html"
            v_file.write_text(f"google-site-verification: google{tok}.html\n", encoding="utf-8")
    except Exception:
        pass


_inject_google_site_verification()

st.set_page_config(page_title="DataFlow AI", page_icon="📊", layout="wide")

# Google Search Console Verification
st.html('<meta name="google-site-verification" content="a02cba74d850951b" />')
st.html('<meta name="google-site-verification" content="dWYj8KkmQ7p1HlC9H7LkaGTdsdBXBPm9-pNd5UZKaJ8" />')

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
