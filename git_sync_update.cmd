@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: git_sync_update.cmd preopen^|intraday
  exit /b 2
)
if not exist ".venv-futu\Scripts\python.exe" call "%~dp0setup_venv.cmd"
".venv-futu\Scripts\python.exe" ".\git_sync_update.py" --mode "%~1"
exit /b %errorlevel%
