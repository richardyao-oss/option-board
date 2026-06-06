@echo off
setlocal
cd /d "%~dp0"
set "APPDATA=%CD%\.futu-appdata"
set "appdata=%APPDATA%"
if not exist ".venv-futu\Scripts\python.exe" call "%~dp0setup_venv.cmd"
".venv-futu\Scripts\python.exe" ".\check_environment.py" %*
exit /b %errorlevel%
