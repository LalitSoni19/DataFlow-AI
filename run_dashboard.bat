@echo off
cd /d "%~dp0"
echo ===================================================
echo Starting DataFlow AI Streamlit Dashboard...
echo Local URL: http://localhost:8501
echo ===================================================
".\.venv\Scripts\python.exe" -m streamlit run streamlit_app/app.py
pause
