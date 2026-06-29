@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "reports" mkdir "reports" >nul 2>nul
set "LOG=%~dp0reports\git_sync_update_%~1.log"

if "%~1"=="" (
  echo Usage: git_sync_update.cmd preopen^|intraday
  echo.
  pause
  exit /b 2
)

echo Git-synced update started at %DATE% %TIME% > "%LOG%"
echo Mode: %~1 >> "%LOG%"

if not exist ".venv-futu\Scripts\python.exe" (
  call "%~dp0setup_venv.cmd" >> "%LOG%" 2>&1
  if errorlevel 1 (
    set "RC=1"
    goto failed
  )
)

".venv-futu\Scripts\python.exe" ".\git_sync_update.py" --mode "%~1" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto failed

type "%LOG%"
exit /b 0

:failed
if not defined RC set "RC=1"
echo.
echo Update failed. The previous dashboard has been preserved.
echo Log file:
echo %LOG%
echo.
type "%LOG%"
echo.
pause
exit /b %RC%
