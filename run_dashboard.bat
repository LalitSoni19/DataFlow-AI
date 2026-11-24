@echo off
cd /d "%~dp0"
setlocal
set "DATAFLOW_PYTHON="
for %%P in (".venv\Scripts\python.exe" "venv\Scripts\python.exe") do (
    if not defined DATAFLOW_PYTHON if exist "%%~P" (
        "%%~P" -c "import streamlit, duckdb" >nul 2>&1
        if not errorlevel 1 set "DATAFLOW_PYTHON=%%~P"
    )
)
if not defined DATAFLOW_PYTHON (
    echo No project Python environment has Streamlit and DuckDB installed.
    pause
    exit /b 1
)
echo Starting DataFlow AI at http://localhost:8501
"%DATAFLOW_PYTHON%" -m streamlit run streamlit_app/app.py
pause
