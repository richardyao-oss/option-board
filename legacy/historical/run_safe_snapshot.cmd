@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
set "LOG=%PROJECT_ROOT%\reports\legacy_run_safe_snapshot.log"
echo Legacy safe snapshot started at %DATE% %TIME% > "%LOG%"
".venv-futu\Scripts\python.exe" ".\option_screen_monitor.py" --pages 5 --page-count 200 >> "%LOG%" 2>&1
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Legacy safe snapshot failed with exit code %EXITCODE%.
  echo Log file:
  echo %LOG%
  echo.
  type "%LOG%"
  pause
)
exit /b %EXITCODE%
