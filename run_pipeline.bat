@echo off
setlocal
cd /d "%~dp0"
call scripts\project_python.bat "dataflow_ai.pipeline"
if errorlevel 1 exit /b 1
"%DATAFLOW_PYTHON%" scripts\run_pipeline.py %*
set "result=%errorlevel%"
pause
exit /b %result%
