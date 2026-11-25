@echo off
setlocal
cd /d "%~dp0"
call scripts\project_python.bat "streamlit, duckdb"
if errorlevel 1 exit /b 1
"%DATAFLOW_PYTHON%" -m streamlit run streamlit_app\app.py %*
set "result=%errorlevel%"
pause
exit /b %result%
