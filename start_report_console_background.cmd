@echo off
setlocal
cd /d "%~dp0"
set "APPDATA=%CD%\.futu-appdata"
set "appdata=%APPDATA%"
if not exist ".venv-futu\Scripts\python.exe" call "%~dp0setup_venv.cmd"
".venv-futu\Scripts\python.exe" ".\launch_report_server.py"
exit /b %errorlevel%
