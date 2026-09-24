@echo off
rem AI Monitor launcher for Windows. Usage: run.bat          (real checks)
rem                                         run.bat --demo   (sample data)
rem Close the minimized "AI Monitor" window to stop the server.
setlocal
cd /d "%~dp0"

rem Position of the Xeneon Edge in Settings > Display (install.ps1 fills this in automatically).
set EDGE_X=0
set EDGE_Y=1440
set URL=http://127.0.0.1:8765

if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist .venv\Scripts\python.exe (
  echo Python 3.10+ not found. Install it with:  winget install Python.Python.3.12
  pause
  exit /b 1
)
if not exist .venv\.deps-ok (
  .venv\Scripts\python -m pip install -r requirements.txt || (pause & exit /b 1)
  echo ok> .venv\.deps-ok
)
if not exist .env copy .env.example .env >nul

start "AI Monitor" /min .venv\Scripts\python -m ai_monitor %*
timeout /t 4 /nobreak >nul

rem Separate profile so Edge honours the window position; kiosk = full screen on that display (Alt+F4 to close).
start "" msedge --kiosk %URL% --edge-kiosk-type=fullscreen --window-position=%EDGE_X%,%EDGE_Y% --user-data-dir="%~dp0.edge-profile" --no-first-run
