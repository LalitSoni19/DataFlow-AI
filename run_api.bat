@echo off
cd /d "%~dp0"
echo ===================================================
echo Starting DataFlow AI FastAPI Server...
echo API Docs: http://127.0.0.1:8000/docs
echo ===================================================
".\.venv\Scripts\python.exe" scripts/run_api.py
pause
