@echo off
cd /d "%~dp0"
echo ===================================================
echo Running DataFlow AI Pytest Suite...
echo ===================================================
".\.venv\Scripts\pytest.exe" --basetemp=.pytest_temp
pause
