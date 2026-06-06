@echo off
setlocal
cd /d "%~dp0"
echo Initializing Google Drive sync folder...
echo WARNING: Google Drive sync is legacy. Routine sync now uses Git.
echo Project: %CD%
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0initialize_google_drive_sync.ps1"
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
