@echo off
setlocal
cd /d "%~dp0"
echo Syncing recovered 2026-06-03 snapshot to Google Drive...
echo Project: %CD%
echo.
".venv-futu\Scripts\python.exe" ".\sync_latest_snapshot_to_google_drive.py" --snapshot-date 2026-06-03
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
