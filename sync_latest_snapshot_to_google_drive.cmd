@echo off
setlocal
cd /d "%~dp0"
echo Syncing latest local snapshot to Google Drive...
echo WARNING: Google Drive sync is legacy. Routine sync now uses Git.
echo Project: %CD%
echo.
".venv-futu\Scripts\python.exe" ".\sync_latest_snapshot_to_google_drive.py"
set "EXITCODE=%ERRORLEVEL%"
echo.
if "%EXITCODE%"=="0" (
  echo Done.
) else (
  echo Failed with exit code %EXITCODE%.
  echo Please send the error text above to Codex.
)
echo.
pause
exit /b %EXITCODE%
