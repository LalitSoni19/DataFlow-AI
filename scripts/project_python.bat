@echo off
rem Call from the project root. Choose an environment that can import the requested entry point.
set "DATAFLOW_PYTHON="
set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
for %%P in (".venv\Scripts\python.exe" "venv\Scripts\python.exe") do (
    if exist "%%~P" (
        "%%~P" -c "import %~1" >nul 2>&1
        if not errorlevel 1 (
            set "DATAFLOW_PYTHON=%%~P"
            exit /b 0
        )
    )
)
echo No project Python environment can import %~1. Install the project dependencies first.
exit /b 1
