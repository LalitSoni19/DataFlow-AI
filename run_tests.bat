@echo off
setlocal
cd /d "%~dp0"
call scripts\project_python.bat "pytest, dataflow_ai.pipeline, fastapi"
if errorlevel 1 exit /b 1
"%DATAFLOW_PYTHON%" -m pytest --basetemp=.pytest_temp %*
set "result=%errorlevel%"
pause
exit /b %result%
