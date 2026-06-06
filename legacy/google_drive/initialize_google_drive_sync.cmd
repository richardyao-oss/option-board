@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
echo Initializing Google Drive sync folder...
echo WARNING: Google Drive sync is legacy. Routine sync now uses Git.
echo Project: %CD%
set "LOG=%PROJECT_ROOT%\reports\legacy_initialize_google_drive_sync.log"
echo.
echo Legacy Google Drive init started at %DATE% %TIME% > "%LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\legacy\google_drive\initialize_google_drive_sync.ps1" >> "%LOG%" 2>&1
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
