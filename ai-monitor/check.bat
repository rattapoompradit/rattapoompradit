@echo off
rem Check every provider once and show the result (for troubleshooting).
cd /d "%~dp0"
.venv\Scripts\python -m ai_monitor --check
pause
