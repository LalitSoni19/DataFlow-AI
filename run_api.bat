@echo off
setlocal
cd /d "%~dp0"
call scripts\project_python.bat "uvicorn, dataflow_ai.api.main"
if errorlevel 1 exit /b 1
"%DATAFLOW_PYTHON%" scripts\run_api.py
set "result=%errorlevel%"
pause
exit /b %result%
