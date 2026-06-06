@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
set "LOG=%PROJECT_ROOT%\reports\legacy_run_backtest_venv.log"
echo Legacy venv backtest started at %DATE% %TIME% > "%LOG%"
".venv-futu\Scripts\python.exe" ".\legacy\historical\option_flow_monitor.py" --mode backfill --start 2026-05-01 --end 2026-05-25 --chain-request-pause 3.3 --kline-request-pause 0.55 --max-kline-requests 20 >> "%LOG%" 2>&1
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Legacy backtest failed with exit code %EXITCODE%.
  echo Log file:
  echo %LOG%
  echo.
  type "%LOG%"
  pause
)
exit /b %EXITCODE%
