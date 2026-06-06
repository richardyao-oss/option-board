@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-futu\Scripts\python.exe" call "%~dp0setup_venv.cmd"
".venv-futu\Scripts\python.exe" ".\git_sync_pull.py"
exit /b %errorlevel%
