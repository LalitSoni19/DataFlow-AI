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
    settings = Settings.from_environment(PROJECT_ROOT)
    token = settings.google_site_verification or "dWYj8KkmQ7p1HlC9H7LkaGTdsdBXBPm9-pNd5UZKaJ8"
    if not token:
        return

    verification_tag = f'<meta name="google-site-verification" content="{token}" />'
    try:
        static_dir = Path(st.__file__).parent / "static"
        index_file = static_dir / "index.html"
        if index_file.exists():
            content = index_file.read_text(encoding="utf-8")
            if verification_tag not in content and "<head>" in content:
                updated = content.replace("<head>", f"<head>\n    {verification_tag}")
                index_file.write_text(updated, encoding="utf-8")

        verify_file = static_dir / f"google{token}.html"
        if not verify_file.exists():
            verify_file.write_text(f"google-site-verification: google{token}.html\n", encoding="utf-8")
    except Exception:
        pass

    try:
        st.html(f"<head>{verification_tag}</head>")
    except Exception:
        pass


_inject_google_site_verification()

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
