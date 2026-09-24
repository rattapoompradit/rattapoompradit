@echo off
rem AI Monitor launcher for Windows. Usage: run.bat          (real checks)
rem                                         run.bat --demo   (sample data)
setlocal
cd /d "%~dp0"

rem Position of the Xeneon Edge in Settings > Display (top-left corner of that screen).
set EDGE_X=0
set EDGE_Y=1440
set URL=http://127.0.0.1:8765

if not exist .venv (
  py -3 -m venv .venv 2>nul || python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist .env copy .env.example .env >nul

start "AI Monitor" /min .venv\Scripts\python -m ai_monitor %*
timeout /t 4 /nobreak >nul

rem Separate profile so Edge honours the window position; kiosk = full screen on that display (Alt+F4 to close).
start "" msedge --kiosk %URL% --edge-kiosk-type=fullscreen --window-position=%EDGE_X%,%EDGE_Y% --user-data-dir="%~dp0.edge-profile" --no-first-run
