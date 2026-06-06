@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
echo Syncing latest local snapshot to Google Drive...
echo WARNING: Google Drive sync is legacy. Routine sync now uses Git.
echo Project: %CD%
set "LOG=%PROJECT_ROOT%\reports\legacy_sync_latest_snapshot_to_google_drive.log"
echo.
echo Legacy Google Drive snapshot sync started at %DATE% %TIME% > "%LOG%"
".venv-futu\Scripts\python.exe" ".\legacy\google_drive\sync_latest_snapshot_to_google_drive.py" >> "%LOG%" 2>&1
set "EXITCODE=%ERRORLEVEL%"
echo.
if "%EXITCODE%"=="0" (
  echo Done.
) else (
  echo Failed with exit code %EXITCODE%.
  echo Log file:
  echo %LOG%
  echo.
  type "%LOG%"
  echo.
  echo Please send the error text above to Codex.
)
echo.
pause
exit /b %EXITCODE%
