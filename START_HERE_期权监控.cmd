@echo off
setlocal
cd /d "%~dp0"
call "%~dp0git_sync_pull.cmd"
if errorlevel 1 (
  echo.
  echo Git pull failed. Please resolve the Git state before opening the dashboard.
  pause
  exit /b 1
)
call "%~dp0start_report_console_background.cmd"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765/"
