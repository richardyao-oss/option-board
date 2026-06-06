@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
set "LOG=%PROJECT_ROOT%\reports\legacy_validate_now_case.log"
echo Legacy NOW validation started at %DATE% %TIME% > "%LOG%"
".venv-futu\Scripts\python.exe" ".\legacy\historical\historical_case_validator.py" --underlying US.NOW --dates 2026-05-14 2026-05-15 2026-05-18 2026-05-19 >> "%LOG%" 2>&1
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Legacy NOW validation failed with exit code %EXITCODE%.
  echo Log file:
  echo %LOG%
  echo.
  type "%LOG%"
  pause
)
exit /b %EXITCODE%
