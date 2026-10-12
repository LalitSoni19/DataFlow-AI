@echo off
cd /d "%~dp0"
echo ===================================================
echo Running DataFlow AI Pipeline...
echo ===================================================
".\.venv\Scripts\python.exe" scripts/run_pipeline.py
pause
