@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "reports" mkdir "reports" >nul 2>nul
set "LOG=%~dp0reports\start_here.log"

echo START_HERE started at %DATE% %TIME% > "%LOG%"
echo Project: %CD% >> "%LOG%"

echo.
echo Checking latest Git data...
call "%~dp0git_sync_pull.cmd" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo Git pull failed. Please resolve Git before opening the dashboard.
  echo Log file:
  echo %LOG%
  echo.
  type "%LOG%"
  pause
  exit /b 1
)

echo Starting local report console...
call "%~dp0start_report_console_background.cmd" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo Report console failed to start.
  echo Log file:
  echo %LOG%
  echo.
  type "%LOG%"
  pause
  exit /b 1
)

timeout /t 2 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/api/info' -TimeoutSec 4; $r.Content; exit 0 } catch { Write-Error $_; exit 1 }" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo Report console did not respond on http://127.0.0.1:8765/.
  echo Log file:
  echo %LOG%
  echo.
  type "%LOG%"
  pause
  exit /b 1
)

echo Opening dashboard...
start "" "http://127.0.0.1:8765/"
echo Dashboard opened. Log file: %LOG%
exit /b 0
